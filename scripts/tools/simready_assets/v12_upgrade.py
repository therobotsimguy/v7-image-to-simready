#!/usr/bin/env python3
"""
v12_upgrade.py — V12 SimReady Post-Processor

Takes an existing V11 _physics.usd and adds:
  1. CoACD pre-decomposition (zero runtime cost, collision-aware, preserves handles/holes)
  2. Dual export: _physics.usd (kinematic, shift+drag) + _articulation.usd (drive targets)
  3. Sidecar physics JSON (full spec — mass, inertia, joints, documented)

V11 joint positions, mass, damping, limits stay untouched.
Only collision shapes are upgraded: runtime convexDecomp → pre-decomposed convexHull.

Usage:
  python3 v12_upgrade.py --input /path/to/asset_physics.usd
  python3 v12_upgrade.py --input /path/to/asset_physics.usd --coacd  # enable CoACD
"""

import argparse
import json
import os
import shutil
import sys

from pxr import Usd, UsdGeom, UsdPhysics, UsdShade, Gf, Sdf


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def _get_all_descendant_meshes(prim):
    meshes = []
    for child in prim.GetChildren():
        if child.GetTypeName() == "Mesh":
            meshes.append(child)
        if child.GetTypeName() in ("Xform", "Scope"):
            meshes.extend(_get_all_descendant_meshes(child))
    return meshes


def mesh_world_bbox(prim):
    bmin = [1e30, 1e30, 1e30]
    bmax = [-1e30, -1e30, -1e30]
    found = False
    for mesh in _get_all_descendant_meshes(prim):
        pts = mesh.GetAttribute("points")
        if not pts or not pts.HasValue():
            continue
        l2w = UsdGeom.Xformable(mesh).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        for pt in pts.Get():
            wp = l2w.TransformAffine(Gf.Vec3d(float(pt[0]), float(pt[1]), float(pt[2])))
            for i in range(3):
                bmin[i] = min(bmin[i], wp[i])
                bmax[i] = max(bmax[i], wp[i])
            found = True
    if not found:
        return None
    return bmin, bmax


# ═══════════════════════════════════════════════════════════════════
# #0: CoACD PRE-DECOMPOSITION
# ═══════════════════════════════════════════════════════════════════

def _extract_mesh_data(mesh_prim):
    """Extract vertices and triangulated faces from a USD Mesh prim in world space."""
    pts_attr = mesh_prim.GetAttribute("points")
    idx_attr = mesh_prim.GetAttribute("faceVertexIndices")
    cnt_attr = mesh_prim.GetAttribute("faceVertexCounts")
    if not all(a and a.HasValue() for a in [pts_attr, idx_attr, cnt_attr]):
        return None, None

    pts = pts_attr.Get()
    indices = idx_attr.Get()
    counts = cnt_attr.Get()
    if not pts or not indices or not counts:
        return None, None

    # Transform to world space
    l2w = UsdGeom.Xformable(mesh_prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    import numpy as np
    verts = np.array([[float(p[0]), float(p[1]), float(p[2])] for p in pts], dtype=np.float64)
    for i in range(len(verts)):
        wp = l2w.TransformAffine(Gf.Vec3d(verts[i][0], verts[i][1], verts[i][2]))
        verts[i] = [wp[0], wp[1], wp[2]]

    # Triangulate faces
    tris = []
    idx_offset = 0
    for fc in counts:
        if fc < 3:
            idx_offset += fc
            continue
        i0 = int(indices[idx_offset])
        for t in range(1, fc - 1):
            i1 = int(indices[idx_offset + t])
            i2 = int(indices[idx_offset + t + 1])
            tris.append([i0, i1, i2])
        idx_offset += fc

    faces = np.array(tris, dtype=np.int32)
    return verts, faces


def _merge_meshes(mesh_prims):
    """Merge multiple USD mesh prims into one vertex/face array."""
    import numpy as np
    all_verts = []
    all_faces = []
    vert_offset = 0

    for mp in mesh_prims:
        verts, faces = _extract_mesh_data(mp)
        if verts is None:
            continue
        all_verts.append(verts)
        all_faces.append(faces + vert_offset)
        vert_offset += len(verts)

    if not all_verts:
        return None, None
    return np.vstack(all_verts), np.vstack(all_faces)


def run_coacd_on_rigid_body(stage, rb_prim, threshold=0.05):
    """Run CoACD on a rigid body's meshes. Returns list of (verts, faces) convex pieces."""
    import coacd
    import numpy as np

    meshes = _get_all_descendant_meshes(rb_prim)
    if not meshes:
        return []

    verts, faces = _merge_meshes(meshes)
    if verts is None or len(verts) < 4 or len(faces) < 1:
        return []

    # Create CoACD mesh
    mesh = coacd.Mesh(verts, faces)

    # Run decomposition
    parts = coacd.run_coacd(
        mesh,
        threshold=threshold,      # concavity threshold (lower = more pieces, tighter fit)
        max_convex_hull=-1,        # no limit
        preprocess_mode="auto",
        resolution=2000,
        mcts_nodes=20,
        mcts_iterations=150,
        mcts_max_depth=3,
        merge=True,
    )

    return parts  # list of (verts_array, faces_array)


def apply_coacd_colliders(stage, rb_prim, parts):
    """Replace existing collision shapes with CoACD pre-decomposed convex hulls.

    1. Strip existing CollisionAPI from all descendant meshes
    2. Create Collisions/ scope with pre-decomposed convexHull meshes
    """
    rb_path = rb_prim.GetPath()

    # Strip existing CollisionAPI
    for desc in Usd.PrimRange(rb_prim):
        if desc.HasAPI(UsdPhysics.CollisionAPI):
            # Remove collision properties
            for prop_name in [p.GetName() for p in desc.GetAuthoredProperties()]:
                if any(kw in prop_name for kw in [
                    "physics:approximation", "physxConvex", "physxCollision",
                    "PhysicsCollisionAPI", "PhysicsMeshCollisionAPI"
                ]):
                    desc.RemoveProperty(prop_name)
            # Remove CollisionAPI schema
            prim_spec = stage.GetRootLayer().GetPrimAtPath(desc.GetPath())
            if prim_spec:
                schemas = prim_spec.GetInfo("apiSchemas")
                if schemas and hasattr(schemas, "prependedItems"):
                    filtered = [s for s in schemas.prependedItems
                                if "Collision" not in s]
                    if filtered:
                        new_list = Sdf.TokenListOp()
                        new_list.prependedItems = filtered
                        prim_spec.SetInfo("apiSchemas", new_list)
                    else:
                        prim_spec.ClearInfo("apiSchemas")

    # Create Collisions/ scope
    col_scope = rb_path.AppendChild("Collisions")
    if not stage.GetPrimAtPath(col_scope).IsValid():
        UsdGeom.Scope.Define(stage, col_scope)

    # Write each CoACD piece as a convexHull mesh
    import numpy as np
    for i, (verts, faces) in enumerate(parts):
        col_name = f"{rb_prim.GetName()}_C{i:03d}"
        col_path = col_scope.AppendChild(col_name)
        col_mesh = UsdGeom.Mesh.Define(stage, col_path)

        # Write vertices
        points = [Gf.Vec3f(float(v[0]), float(v[1]), float(v[2])) for v in verts]
        col_mesh.CreatePointsAttr(points)

        # Write faces (triangulated)
        face_counts = [3] * len(faces)
        face_indices = faces.flatten().tolist()
        col_mesh.CreateFaceVertexCountsAttr(face_counts)
        col_mesh.CreateFaceVertexIndicesAttr(face_indices)

        # Apply CollisionAPI with convexHull (already convex from CoACD)
        col_prim = col_mesh.GetPrim()
        UsdPhysics.CollisionAPI.Apply(col_prim)
        mc = UsdPhysics.MeshCollisionAPI.Apply(col_prim)
        mc.CreateApproximationAttr("convexHull")

        # Hide from rendering
        UsdGeom.Imageable(col_prim).CreatePurposeAttr("guide")

    return len(parts)


# ═══════════════════════════════════════════════════════════════════
# #1: DUAL EXPORT — articulation variant
# ═══════════════════════════════════════════════════════════════════

def create_articulation_variant(physics_usd, output_path):
    """Create ArticulationRootAPI variant from kinematic physics USD.

    Changes:
    - Remove kinematicEnabled from body
    - Add ArticulationRootAPI to default prim
    - Add FixedJoint from body to world
    """
    shutil.copy2(physics_usd, output_path)
    stage = Usd.Stage.Open(output_path)
    dp = stage.GetDefaultPrim()

    # Add ArticulationRootAPI
    dp_spec = stage.GetRootLayer().GetPrimAtPath(dp.GetPath())
    schemas = dp_spec.GetInfo("apiSchemas")
    items = list(schemas.prependedItems) if schemas and hasattr(schemas, "prependedItems") else []
    if "PhysicsArticulationRootAPI" not in items:
        items.append("PhysicsArticulationRootAPI")
        new_list = Sdf.TokenListOp()
        new_list.prependedItems = items
        dp_spec.SetInfo("apiSchemas", new_list)

    # Find kinematic body → switch to dynamic + FixedJoint to world
    for prim in stage.Traverse():
        if not prim.HasAPI(UsdPhysics.RigidBodyAPI):
            continue
        kin_attr = prim.GetAttribute("physics:kinematicEnabled")
        if kin_attr and kin_attr.Get():
            prim.RemoveProperty("physics:kinematicEnabled")
            joint_path = prim.GetPath().AppendChild("FixedJoint")
            joint = UsdPhysics.FixedJoint.Define(stage, joint_path)
            joint.CreateBody1Rel().SetTargets([prim.GetPath()])
            joint.CreateLocalPos0Attr(Gf.Vec3f(0, 0, 0))
            joint.CreateLocalPos1Attr(Gf.Vec3f(0, 0, 0))
            break

    stage.GetRootLayer().Save()


# ═══════════════════════════════════════════════════════════════════
# #2: SIDECAR PHYSICS JSON
# ═══════════════════════════════════════════════════════════════════

def generate_physics_json(stage, output_path):
    """Generate sidecar physics JSON documenting all physics properties."""
    dp = stage.GetDefaultPrim()

    # Collect rigid body info
    parts = []
    for prim in stage.Traverse():
        if not prim.HasAPI(UsdPhysics.RigidBodyAPI):
            continue

        mass_attr = prim.GetAttribute("physics:mass")
        mass = mass_attr.Get() if mass_attr and mass_attr.HasValue() else None

        kin_attr = prim.GetAttribute("physics:kinematicEnabled")
        is_kinematic = kin_attr.Get() if kin_attr and kin_attr.HasValue() else False

        bbox = mesh_world_bbox(prim)
        bounds = None
        if bbox:
            bmin, bmax = bbox
            bounds = {
                "min": [round(bmin[i], 6) for i in range(3)],
                "max": [round(bmax[i], 6) for i in range(3)],
                "center": [round((bmin[i] + bmax[i]) / 2, 6) for i in range(3)],
                "size": [round(abs(bmax[i] - bmin[i]), 6) for i in range(3)],
            }

        # Count collision shapes
        n_colliders = 0
        approx_types = {}
        for desc in Usd.PrimRange(prim):
            if desc.HasAPI(UsdPhysics.CollisionAPI):
                n_colliders += 1
                ap = desc.GetAttribute("physics:approximation")
                atype = ap.Get() if ap and ap.HasValue() else "none"
                approx_types[atype] = approx_types.get(atype, 0) + 1

        parts.append({
            "name": prim.GetName(),
            "path": str(prim.GetPath()),
            "is_kinematic": is_kinematic,
            "mass_kg": round(mass, 4) if mass else None,
            "bounds": bounds,
            "colliders": n_colliders,
            "collision_types": approx_types,
        })

    # Collect joint info
    joints = []
    for prim in stage.Traverse():
        if not prim.IsA(UsdPhysics.Joint):
            continue

        joint_info = {
            "name": prim.GetName(),
            "type": prim.GetTypeName(),
        }

        # Basic joint properties
        for attr_name in ["physics:axis", "physics:lowerLimit", "physics:upperLimit"]:
            attr = prim.GetAttribute(attr_name)
            if attr and attr.HasValue():
                val = attr.Get()
                joint_info[attr_name.split(":")[-1]] = round(val, 4) if isinstance(val, float) else val

        # Connected bodies
        body0 = prim.GetRelationship("physics:body0").GetTargets()
        body1 = prim.GetRelationship("physics:body1").GetTargets()
        joint_info["body0"] = str(body0[0]) if body0 else "world"
        joint_info["body1"] = str(body1[0]) if body1 else None

        # Drive params
        drive_info = {}
        for attr in prim.GetAttributes():
            name = attr.GetName()
            if "drive" in name and attr.HasValue():
                val = attr.Get()
                key = name.split(":")[-1]
                drive_info[key] = round(val, 4) if isinstance(val, float) else val
        if drive_info:
            joint_info["drive"] = drive_info

        joints.append(joint_info)

    # Summary
    total_mass = sum(p["mass_kg"] for p in parts if p["mass_kg"])
    n_movable = sum(1 for p in parts if not p["is_kinematic"])
    n_kinematic = sum(1 for p in parts if p["is_kinematic"])

    spec = {
        "version": "V12",
        "asset_name": dp.GetName() if dp else "unknown",
        "summary": {
            "total_mass_kg": round(total_mass, 2),
            "rigid_bodies": len(parts),
            "kinematic": n_kinematic,
            "dynamic": n_movable,
            "joints": len(joints),
        },
        "parts": parts,
        "joints": joints,
    }

    with open(output_path, "w") as f:
        json.dump(spec, f, indent=2, default=str)
    return spec


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def upgrade_to_v12(input_usd, output_dir=None, use_coacd=False, coacd_threshold=0.05,
                   use_sdf=False):
    """Apply V12 upgrades: SDF/CoACD collision + dual export + sidecar JSON."""
    input_path = os.path.abspath(input_usd)
    basename = os.path.splitext(os.path.basename(input_path))[0]
    asset_name = basename.replace("_physics", "")
    input_dir = os.path.dirname(input_path)

    if output_dir is None:
        output_dir = os.path.join(input_dir, "v12")
    os.makedirs(output_dir, exist_ok=True)

    collision_mode = "SDF (Lightwheel quality)" if use_sdf else \
                     "CoACD (threshold={})".format(coacd_threshold) if use_coacd else \
                     "V11 preserved"

    print(f"\n{'=' * 60}")
    print(f"  V12 SimReady Upgrade")
    print(f"{'=' * 60}")
    print(f"  Input:     {input_path}")
    print(f"  Output:    {output_dir}/")
    print(f"  Collision: {collision_mode}")

    # Copy physics USD
    physics_usd = os.path.join(output_dir, f"{basename}.usd")
    shutil.copy2(input_path, physics_usd)

    # SDF collision upgrade
    if use_sdf:
        print(f"\n  [0/3] Upgrading collision to SDF (exact mesh surface)...")
        stage = Usd.Stage.Open(physics_usd)
        n_switched = 0
        for prim in stage.Traverse():
            if prim.HasAPI(UsdPhysics.CollisionAPI):
                mc = UsdPhysics.MeshCollisionAPI.Apply(prim)
                old_approx = prim.GetAttribute("physics:approximation")
                old_val = old_approx.Get() if old_approx and old_approx.HasValue() else "none"
                mc.CreateApproximationAttr("sdf")
                # Remove convexDecomposition params if present
                for prop_name in [p.GetName() for p in prim.GetAuthoredProperties()]:
                    if "physxConvex" in prop_name:
                        prim.RemoveProperty(prop_name)
                n_switched += 1
        stage.GetRootLayer().Save()
        print(f"    Switched {n_switched} colliders to SDF")

    # CoACD pre-decomposition
    elif use_coacd:
        print(f"\n  [0/3] CoACD pre-decomposition (collision-aware)...")
        stage = Usd.Stage.Open(physics_usd)
        total_pieces = 0
        for prim in stage.Traverse():
            if not prim.HasAPI(UsdPhysics.RigidBodyAPI):
                continue
            name = prim.GetName()
            try:
                parts = run_coacd_on_rigid_body(stage, prim, threshold=coacd_threshold)
                if parts:
                    n = apply_coacd_colliders(stage, prim, parts)
                    total_pieces += n
                    print(f"    {name}: {n} convex pieces")
                else:
                    print(f"    {name}: skipped (no mesh data)")
            except Exception as e:
                print(f"    {name}: CoACD failed ({e}), keeping V11 collision")
        stage.GetRootLayer().Save()
        print(f"    Total: {total_pieces} pre-decomposed convex hulls (zero runtime cost)")
    else:
        print(f"\n  [0/3] V11 collision preserved")

    # Copy textures if present
    src_tex = os.path.join(input_dir, "Textures")
    dst_tex = os.path.join(output_dir, "Textures")
    if os.path.isdir(src_tex) and not os.path.isdir(dst_tex):
        shutil.copytree(src_tex, dst_tex)
        print(f"    Textures bundled")

    # Dual export — articulation variant
    print(f"\n  [2/3] Creating articulation variant...")
    artic_usd = os.path.join(output_dir, f"{asset_name}_articulation.usd")
    create_articulation_variant(physics_usd, artic_usd)
    print(f"    Saved: {artic_usd}")

    # Sidecar JSON
    print(f"\n  [3/3] Generating sidecar physics JSON...")
    stage = Usd.Stage.Open(physics_usd)
    json_path = os.path.join(output_dir, f"{asset_name}_physics.json")
    spec = generate_physics_json(stage, json_path)
    s = spec["summary"]
    print(f"    {s['rigid_bodies']} bodies ({s['kinematic']} kinematic, {s['dynamic']} dynamic)")
    print(f"    {s['joints']} joints, total mass={s['total_mass_kg']}kg")
    print(f"    Saved: {json_path}")

    # Summary
    print(f"\n{'=' * 60}")
    print(f"  V12 OUTPUT:")
    print(f"    {physics_usd}")
    print(f"      → V11 physics, shift+drag works")
    print(f"    {artic_usd}")
    print(f"      → ArticulationRootAPI, drive targets work")
    print(f"    {json_path}")
    print(f"      → Full physics specification")
    print(f"\n  Test (shift+drag):")
    print(f"    ISAACLAB_PATH=/home/msi/IsaacLab ./isaaclab.sh -p scripts/environments/teleoperation/teleop_se3_agent_cinematic.py \\")
    print(f"      --asset {os.path.abspath(physics_usd)} --device cpu")
    print(f"\n  Test (drive targets):")
    print(f"    ISAACLAB_PATH=/home/msi/IsaacLab ./isaaclab.sh -p scripts/environments/teleoperation/teleop_se3_agent_cinematic.py \\")
    print(f"      --asset {os.path.abspath(artic_usd)} --device cpu")
    print(f"{'=' * 60}")

    return physics_usd, artic_usd, json_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="V12 SimReady Upgrade")
    ap.add_argument("--input", required=True, help="Input _physics.usd from V11")
    ap.add_argument("--output-dir", default=None, help="Output directory")
    ap.add_argument("--sdf", action="store_true",
                    help="Switch all colliders to SDF (exact mesh surface, Lightwheel quality)")
    ap.add_argument("--coacd", action="store_true",
                    help="Enable CoACD pre-decomposition (replaces runtime convexDecomp)")
    ap.add_argument("--coacd-threshold", type=float, default=0.05,
                    help="CoACD concavity threshold (lower=tighter fit, more pieces. Default: 0.05)")
    args = ap.parse_args()

    if not os.path.isfile(args.input):
        print(f"ERROR: File not found: {args.input}")
        sys.exit(1)

    upgrade_to_v12(args.input, output_dir=args.output_dir,
                   use_coacd=args.coacd, coacd_threshold=args.coacd_threshold,
                   use_sdf=args.sdf)

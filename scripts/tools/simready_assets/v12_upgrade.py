#!/usr/bin/env python3
"""
v12_upgrade.py — V12 SimReady Post-Processor

Takes an existing V11 _physics.usd and adds:
  1. SDF collision (exact mesh surface — Lightwheel quality)
  2. Dual export: _physics.usd (kinematic, shift+drag) + _articulation.usd (drive targets)
  3. Sidecar physics JSON (full spec — mass, joints, colliders documented)

V11 joint positions, mass, damping, limits stay untouched.

Usage:
  python3 v12_upgrade.py --input /path/to/asset_physics.usd --sdf
  python3 v12_upgrade.py --input /path/to/asset_physics.usd          # no SDF, just dual export + JSON
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
# #1: SDF COLLISION UPGRADE
# ═══════════════════════════════════════════════════════════════════

def upgrade_collision_to_sdf(stage):
    """Switch all colliders from convexHull/convexDecomposition to SDF."""
    n_switched = 0
    for prim in stage.Traverse():
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            mc = UsdPhysics.MeshCollisionAPI.Apply(prim)
            mc.CreateApproximationAttr("sdf")
            # Remove convexDecomposition params if present
            for prop_name in [p.GetName() for p in prim.GetAuthoredProperties()]:
                if "physxConvex" in prop_name:
                    prim.RemoveProperty(prop_name)
            n_switched += 1
    return n_switched


# ═══════════════════════════════════════════════════════════════════
# #2: DUAL EXPORT — articulation variant
# ═══════════════════════════════════════════════════════════════════

def create_articulation_variant(physics_usd, output_path):
    """Create ArticulationRootAPI variant from kinematic physics USD."""
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
# #3: SIDECAR PHYSICS JSON
# ═══════════════════════════════════════════════════════════════════

def generate_physics_json(stage, output_path):
    """Generate sidecar physics JSON documenting all physics properties."""
    dp = stage.GetDefaultPrim()

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

    joints = []
    for prim in stage.Traverse():
        if not prim.IsA(UsdPhysics.Joint):
            continue

        joint_info = {
            "name": prim.GetName(),
            "type": prim.GetTypeName(),
        }

        for attr_name in ["physics:axis", "physics:lowerLimit", "physics:upperLimit"]:
            attr = prim.GetAttribute(attr_name)
            if attr and attr.HasValue():
                val = attr.Get()
                joint_info[attr_name.split(":")[-1]] = round(val, 4) if isinstance(val, float) else val

        body0 = prim.GetRelationship("physics:body0").GetTargets()
        body1 = prim.GetRelationship("physics:body1").GetTargets()
        joint_info["body0"] = str(body0[0]) if body0 else "world"
        joint_info["body1"] = str(body1[0]) if body1 else None

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

def upgrade_to_v12(input_usd, output_dir=None):
    """Apply V12 upgrades: SDF collision + dual export + sidecar JSON."""
    input_path = os.path.abspath(input_usd)
    basename = os.path.splitext(os.path.basename(input_path))[0]
    asset_name = basename.replace("_physics", "")
    input_dir = os.path.dirname(input_path)

    if output_dir is None:
        output_dir = os.path.join(input_dir, "v12")
    os.makedirs(output_dir, exist_ok=True)

    print(f"\n{'=' * 60}")
    print(f"  V12 SimReady Upgrade")
    print(f"{'=' * 60}")
    print(f"  Input:     {input_path}")
    print(f"  Output:    {output_dir}/")
    # Copy physics USD
    physics_usd = os.path.join(output_dir, f"{basename}.usd")
    shutil.copy2(input_path, physics_usd)

    # SDF collision — always applied
    print(f"\n  [1/3] Upgrading collision to SDF (exact mesh surface)...")
    stage = Usd.Stage.Open(physics_usd)
    n = upgrade_collision_to_sdf(stage)
    stage.GetRootLayer().Save()
    print(f"    Switched {n} colliders to SDF")

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
    print(f"      → SDF collision, shift+drag works")
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
    args = ap.parse_args()

    if not os.path.isfile(args.input):
        print(f"ERROR: File not found: {args.input}")
        sys.exit(1)

    upgrade_to_v12(args.input, output_dir=args.output_dir)

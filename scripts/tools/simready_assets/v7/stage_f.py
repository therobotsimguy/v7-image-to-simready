#!/usr/bin/env python3
"""
V7 Stage F — USD Physics Layer

Reads the validated USD from Stage E and adds:
  1. ArticulationRootAPI     → root body (ARTICULATED only)
  2. RigidBodyAPI            → every part Xform
  3. CollisionAPI            → every Mesh prim
  4. MeshCollisionAPI        → every Mesh prim
  5. MassAPI                 → every part Xform (estimated mass)
  6. Joints                  → per behavior_type
       ROTATIONAL             → RevoluteJoint  (doors)
       LINEAR_TRANSLATIONAL   → PrismaticJoint (drawers)
       GRASPING / CONTACT_*  → FixedJoint

No AI calls. Pure geometry + physics API writes.
Output: <name>_physics.usd  (copy of input with physics overlaid)

Uses only pxr.UsdPhysics — no PhysxSchema dependency.
"""

import json
import os
import sys
import shutil
import time

from pxr import Usd, UsdGeom, UsdPhysics, Gf, Sdf


# ═══════════════════════════════════════════════════════════════════
# MASS TABLE  (kg)
# ═══════════════════════════════════════════════════════════════════

def _estimate_mass(part_name: str, dims: dict) -> float:
    """Rough mass from volume × density. Furniture wood ≈ 600 kg/m³."""
    w = dims["width_mm"]  / 1000
    d = dims["depth_mm"]  / 1000
    h = dims["height_mm"] / 1000
    volume = w * d * h
    if any(k in part_name for k in ["handle", "knob"]):
        density = 2700  # aluminum
    elif any(k in part_name for k in ["door", "drawer"]):
        density = 500   # light plywood
    else:
        density = 600   # solid wood
    mass = max(0.05, round(volume * density, 2))
    return mass


# ═══════════════════════════════════════════════════════════════════
# PATH HELPERS
# ═══════════════════════════════════════════════════════════════════

def _find_part_xform(stage: Usd.Stage, part_name: str) -> Sdf.Path:
    """Return Sdf.Path of the Xform for a named part."""
    for prim in stage.Traverse():
        if prim.GetName() == part_name and prim.GetTypeName() == "Xform":
            return prim.GetPath()
    return None


def _find_mesh_children(stage: Usd.Stage, xform_path: Sdf.Path) -> list:
    """Return list of Mesh prim paths that are direct children of xform_path."""
    parent = stage.GetPrimAtPath(xform_path)
    if not parent:
        return []
    meshes = []
    for child in parent.GetChildren():
        if child.GetTypeName() == "Mesh":
            meshes.append(child.GetPath())
    return meshes


def _get_xform_translate(stage: Usd.Stage, path: Sdf.Path) -> Gf.Vec3f:
    """Return translate op of an Xform, or (0,0,0) if none."""
    prim = stage.GetPrimAtPath(path)
    if not prim:
        return Gf.Vec3f(0, 0, 0)
    xformable = UsdGeom.Xformable(prim)
    for op in xformable.GetOrderedXformOps():
        if "translate" in op.GetOpName():
            v = op.Get()
            return Gf.Vec3f(float(v[0]), float(v[1]), float(v[2]))
    return Gf.Vec3f(0, 0, 0)


# ═══════════════════════════════════════════════════════════════════
# API APPLICATORS
# ═══════════════════════════════════════════════════════════════════

def _apply_rigid_body(stage: Usd.Stage, path: Sdf.Path) -> None:
    """Apply RigidBodyAPI to an Xform prim."""
    prim = stage.GetPrimAtPath(path)
    if prim:
        UsdPhysics.RigidBodyAPI.Apply(prim)


def _apply_collision(stage: Usd.Stage, mesh_path: Sdf.Path) -> None:
    """Apply CollisionAPI + MeshCollisionAPI to a Mesh prim."""
    prim = stage.GetPrimAtPath(mesh_path)
    if not prim:
        return
    UsdPhysics.CollisionAPI.Apply(prim)
    mesh_col = UsdPhysics.MeshCollisionAPI.Apply(prim)
    mesh_col.CreateApproximationAttr("convexHull")


def _skip_collision_for_part(part_name: str) -> bool:
    """True for meshes that should NOT get colliders.

    Knobs/handles are children of doors/drawers but have no RigidBodyAPI. Isaac Sim
    shift+drag picking hits colliders — if the user clicks the knob, PhysX targets a
    shape with no body and nothing moves. Skip collision so the ray hits the parent
    door/drawer mesh instead.

    Inferred dividers are visual/structural only; their thin colliders steal picks
    near door edges and are not meant to be dragged.
    """
    n = part_name.lower()
    if "knob" in n or "handle" in n:
        return True
    if "divider" in n:
        return True
    return False


def _apply_mass(stage: Usd.Stage, path: Sdf.Path, mass_kg: float) -> None:
    """Apply MassAPI to an Xform prim."""
    prim = stage.GetPrimAtPath(path)
    if prim:
        mass_api = UsdPhysics.MassAPI.Apply(prim)
        mass_api.CreateMassAttr(mass_kg)


# ═══════════════════════════════════════════════════════════════════
# JOINT BUILDERS
# ═══════════════════════════════════════════════════════════════════

def _make_revolute_joint(
    stage: Usd.Stage,
    joint_path: Sdf.Path,
    body0_path: Sdf.Path,
    body1_path: Sdf.Path,
    local_pos0: Gf.Vec3f,
    local_pos1: Gf.Vec3f,
    lower_deg: float,
    upper_deg: float,
    pivot_side: str,
) -> None:
    """Create a RevoluteJoint — door hinge around Z axis."""
    joint = UsdPhysics.RevoluteJoint.Define(stage, joint_path)
    joint.CreateAxisAttr("Z")
    joint.CreateLowerLimitAttr(lower_deg)
    joint.CreateUpperLimitAttr(upper_deg)

    body0_rel = joint.CreateBody0Rel()
    body0_rel.SetTargets([body0_path])
    body1_rel = joint.CreateBody1Rel()
    body1_rel.SetTargets([body1_path])

    joint.CreateLocalPos0Attr(local_pos0)
    joint.CreateLocalPos1Attr(local_pos1)

    # Damping drive to resist free swing
    drive = UsdPhysics.DriveAPI.Apply(stage.GetPrimAtPath(joint_path), "angular")
    drive.CreateDampingAttr(10.0)
    drive.CreateStiffnessAttr(0.0)


def _make_prismatic_joint(
    stage: Usd.Stage,
    joint_path: Sdf.Path,
    body0_path: Sdf.Path,
    body1_path: Sdf.Path,
    local_pos0: Gf.Vec3f,
    local_pos1: Gf.Vec3f,
    lower_m: float,
    upper_m: float,
) -> None:
    """Create a PrismaticJoint — drawer slides along Y axis."""
    joint = UsdPhysics.PrismaticJoint.Define(stage, joint_path)
    joint.CreateAxisAttr("Y")
    joint.CreateLowerLimitAttr(lower_m)
    joint.CreateUpperLimitAttr(upper_m)

    body0_rel = joint.CreateBody0Rel()
    body0_rel.SetTargets([body0_path])
    body1_rel = joint.CreateBody1Rel()
    body1_rel.SetTargets([body1_path])

    joint.CreateLocalPos0Attr(local_pos0)
    joint.CreateLocalPos1Attr(local_pos1)

    # Damping drive to resist free slide
    drive = UsdPhysics.DriveAPI.Apply(stage.GetPrimAtPath(joint_path), "linear")
    drive.CreateDampingAttr(50.0)
    drive.CreateStiffnessAttr(0.0)


def _make_fixed_joint(
    stage: Usd.Stage,
    joint_path: Sdf.Path,
    body0_path: Sdf.Path,
    body1_path: Sdf.Path,
    local_pos0: Gf.Vec3f,
    local_pos1: Gf.Vec3f,
) -> None:
    """Create a FixedJoint — no relative motion (handles, knobs, top panel)."""
    joint = UsdPhysics.FixedJoint.Define(stage, joint_path)

    body0_rel = joint.CreateBody0Rel()
    body0_rel.SetTargets([body0_path])
    body1_rel = joint.CreateBody1Rel()
    body1_rel.SetTargets([body1_path])

    joint.CreateLocalPos0Attr(local_pos0)
    joint.CreateLocalPos1Attr(local_pos1)


# ═══════════════════════════════════════════════════════════════════
# JOINT DISPATCHER — per part behavior_type
# ═══════════════════════════════════════════════════════════════════

def _dispatch_joint(
    stage: Usd.Stage,
    p: dict,
    joints_scope: str,
    root_body_path: Sdf.Path,
) -> None:
    """Create the right joint for one part.

    LocalPos computation:
    - Most parts have NO Xform translate in USD (positions baked into mesh vertices
      by Blender's USD exporter after transform_apply). Their body frame origin is
      at world (0,0,0). So localPos = world position of anchor (from spec position_xyz).
    - Parts with an Xform translate (only cylinder/knob parts) store their translate
      in the parent's local frame. For those, use xform_translate as localPos0 and
      (0,0,0) as localPos1 (center of body in its own frame).
    """
    name   = p["part"]
    btype  = p.get("behavior", {}).get("behavior_type", "CONTACT_BASED")
    parent = p.get("parent", "")
    dims   = p["dims_reconciled"]
    pivot  = p.get("pivot", "")
    cx, cy, cz = p["position_xyz"]  # world center from spec

    part_path = _find_part_xform(stage, name)
    if not part_path:
        print(f"    [F] ✗ Part Xform not found: {name}")
        return

    if parent in ("none", None, ""):
        return  # root body — no joint

    parent_path = _find_part_xform(stage, parent)
    if not parent_path:
        print(f"    [F] ✗ Parent Xform not found: {parent} for {name}")
        return

    joint_path = Sdf.Path(f"{joints_scope}/{name}_joint")

    if btype == "ROTATIONAL":
        # Revolute joint — door hinge along Z axis.
        # Stage D created door with origin AT the hinge (world position).
        # localPos1 = (0,0,0) since door origin IS the hinge.
        # localPos0 = hinge position in parent body's local frame = hinge_world - body_world.
        pivot_lower = pivot.lower()
        if "right" in pivot_lower:
            lower_deg, upper_deg = 0.0, 120.0
            hinge_wx = cx + dims["width_mm"] / 1000 / 2
        else:
            lower_deg, upper_deg = -120.0, 0.0
            hinge_wx = cx - dims["width_mm"] / 1000 / 2

        # Parent body world position (root body has no parent, its Xform = world pos)
        parent_t = _get_xform_translate(stage, parent_path)
        local_pos0 = Gf.Vec3f(hinge_wx - float(parent_t[0]),
                               cy - float(parent_t[1]),
                               cz - float(parent_t[2]))
        local_pos1 = Gf.Vec3f(0, 0, 0)  # door origin IS the hinge

        _make_revolute_joint(
            stage, joint_path,
            parent_path, part_path,
            local_pos0, local_pos1,
            lower_deg, upper_deg,
            pivot_lower,
        )
        print(f"    [F] RevoluteJoint  {name:<30s}  localPos0={tuple(round(v,3) for v in local_pos0)}  limits=[{lower_deg:.0f}°, {upper_deg:.0f}°]")

    elif btype == "LINEAR_TRANSLATIONAL":
        # Prismatic joint — drawer slides in +Y (out toward user).
        # Stage D created drawer with origin at back face (world position).
        # localPos1 = (0,0,0) since drawer origin IS the slide start.
        # localPos0 = back-face position in parent body's local frame.
        max_travel = round(dims["depth_mm"] / 1000 * 0.85, 3)

        slide_wy = cy - dims["depth_mm"] / 1000 / 2  # back face world Y
        parent_t = _get_xform_translate(stage, parent_path)
        local_pos0 = Gf.Vec3f(cx - float(parent_t[0]),
                               slide_wy - float(parent_t[1]),
                               cz - float(parent_t[2]))
        local_pos1 = Gf.Vec3f(0, 0, 0)  # drawer origin IS slide start

        _make_prismatic_joint(
            stage, joint_path,
            parent_path, part_path,
            local_pos0, local_pos1,
            lower_m=0.0,
            upper_m=max_travel,
        )
        print(f"    [F] PrismaticJoint {name:<30s}  axis=Y  limits=[0m, {max_travel}m]")

    else:
        # Fixed joint — handles, knobs, dividers, top panel
        child_xform_t = _get_xform_translate(stage, part_path)
        has_xform = any(v != 0 for v in child_xform_t)

        if has_xform:
            # Part has an Xform translate (e.g. knobs created at origin then located).
            # The translate value is LOCAL to the parent's frame.
            local_pos0 = Gf.Vec3f(child_xform_t[0], child_xform_t[1], child_xform_t[2])
            local_pos1 = Gf.Vec3f(0, 0, 0)
        else:
            # Baked position — both frames at world origin, use spec center.
            local_pos0 = Gf.Vec3f(cx, cy, cz)
            local_pos1 = Gf.Vec3f(cx, cy, cz)

        _make_fixed_joint(
            stage, joint_path,
            parent_path, part_path,
            local_pos0, local_pos1,
        )
        print(f"    [F] FixedJoint     {name:<30s}")


# ═══════════════════════════════════════════════════════════════════
# PHYSICS SCENE
# ═══════════════════════════════════════════════════════════════════

def _add_physics_scene(stage: Usd.Stage) -> Sdf.Path:
    """Create UsdPhysics.Scene at /physicsScene (Isaac Sim default path).

    Rigid bodies and colliders must set physics:simulationOwner → this prim or
    Kit may not register them for simulation / viewport shift+drag picking.
    """
    scene_path = Sdf.Path("/physicsScene")
    if not stage.GetPrimAtPath(scene_path).IsValid():
        UsdPhysics.Scene.Define(stage, scene_path)
    scene = UsdPhysics.Scene.Get(stage, scene_path)
    if scene:
        scene.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
        scene.CreateGravityMagnitudeAttr(9.81)
    return scene_path


def _bind_physics_prims_to_scene(stage: Usd.Stage, scene_path: Sdf.Path) -> int:
    """Set physics:simulationOwner on every RigidBodyAPI and CollisionAPI prim."""
    n = 0
    for prim in stage.Traverse():
        if UsdPhysics.RigidBodyAPI(prim):
            api = UsdPhysics.RigidBodyAPI(prim)
            rel = api.GetSimulationOwnerRel()
            if not rel:
                rel = api.CreateSimulationOwnerRel()
            rel.SetTargets([scene_path])
            n += 1
        if UsdPhysics.CollisionAPI(prim):
            api = UsdPhysics.CollisionAPI(prim)
            rel = api.GetSimulationOwnerRel()
            if not rel:
                rel = api.CreateSimulationOwnerRel()
            rel.SetTargets([scene_path])
            n += 1
    return n


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def run_stage_f(spec: dict, input_usd: str, output_dir: str) -> dict:
    t0 = time.time()
    print(f"\n{'='*60}")
    print(f"  STAGE F — USD Physics Layer")
    print(f"{'='*60}")

    articulation = spec["object"]["articulation"]
    obj_name     = spec["object"]["type"].replace(" ", "_")
    parts        = spec["parts"]

    # Output path
    output_usd = os.path.join(output_dir, f"{obj_name}_physics.usd")
    shutil.copy2(input_usd, output_usd)
    print(f"  Copied {os.path.basename(input_usd)} → {os.path.basename(output_usd)}")

    stage = Usd.Stage.Open(output_usd)
    if not stage:
        return {"status": "error", "message": f"Cannot open {output_usd}"}

    # ── Step 1: Physics Scene ────────────────────────────────────────
    print(f"\n  [F1] Physics scene...")
    scene_path = _add_physics_scene(stage)
    print(f"       {scene_path}")

    # ── Step 2: Find root body ───────────────────────────────────────
    root_part  = next((p for p in parts if p.get("parent") in ("none", None, "")), parts[0])
    root_name  = root_part["part"]
    root_path  = _find_part_xform(stage, root_name)
    if not root_path:
        return {"status": "error", "message": f"Root body Xform not found: {root_name}"}
    print(f"  [F2] Root body: {root_path}")

    # ── Step 3: ArticulationRootAPI (ARTICULATED only) ───────────────
    if articulation == "ARTICULATED":
        root_prim = stage.GetPrimAtPath(root_path)
        UsdPhysics.ArticulationRootAPI.Apply(root_prim)
        print(f"  [F3] ArticulationRootAPI → {root_path}")
    else:
        print(f"  [F3] RIGID — skipping ArticulationRootAPI")

    # Build parent lookup for depth detection
    part_map = {p["part"]: p for p in parts}

    def _is_direct_child_of_root(p):
        """True if p's parent is the root (depth=1 in articulation tree)."""
        parent_name = p.get("parent", "")
        if parent_name in ("none", None, ""):
            return False  # this IS root
        parent = part_map.get(parent_name, {})
        return parent.get("parent") in ("none", None, "")

    def _is_root(p):
        return p.get("parent") in ("none", None, "")

    # ── Step 4: RigidBodyAPI + CollisionAPI + MassAPI per part ───────
    # PhysX rule: NO nested RigidBodyAPIs in hierarchy.
    # Only root (ArticulationRootAPI, no RigidBody) and direct children of root
    # (RigidBodyAPI) are valid articulation links.
    # Grandchildren (handles, knobs) — skip RigidBodyAPI, pure visual geometry.
    print(f"\n  [F4] Rigid bodies + collision + mass:")
    for p in parts:
        name      = p["part"]
        dims      = p["dims_reconciled"]
        xform_path = _find_part_xform(stage, name)
        if not xform_path:
            print(f"    ✗ Xform not found: {name}")
            continue

        mass = _estimate_mass(name, dims)

        if _is_root(p):
            # Root: ArticulationRootAPI only — no RigidBodyAPI (fixed base)
            pass
        elif _is_direct_child_of_root(p):
            # Direct articulation link — gets RigidBodyAPI
            _apply_rigid_body(stage, xform_path)
        else:
            # Grandchild (handle, knob) — skip RigidBodyAPI to avoid nested rigid body error
            print(f"    (skip RigidBody — grandchild) {name}")
            pass

        # CollisionAPI on each Mesh child (skip decoy geometry — see _skip_collision_for_part)
        mesh_paths = _find_mesh_children(stage, xform_path)
        n_col_meshes = 0
        if _skip_collision_for_part(name):
            pass
        else:
            for mp in mesh_paths:
                _apply_collision(stage, mp)
                n_col_meshes += 1

        # MassAPI on Xform
        _apply_mass(stage, xform_path, mass)

        col_note = f"  colliders={n_col_meshes}" if n_col_meshes else "  (no mesh colliders — visual only)"
        print(f"    {name:<30s}  mass={mass:.2f}kg  meshes={len(mesh_paths)}{col_note}")

    # ── Step 5: Joints ───────────────────────────────────────────────
    if articulation == "ARTICULATED":
        print(f"\n  [F5] Joints:")
        joints_scope_path = f"{root_path}/joints"
        joints_scope = stage.GetPrimAtPath(Sdf.Path(joints_scope_path))
        if not joints_scope or not joints_scope.IsValid():
            UsdGeom.Scope.Define(stage, Sdf.Path(joints_scope_path))

        for p in parts:
            if p.get("parent") in ("none", None, ""):
                continue
            if not _is_direct_child_of_root(p):
                # Grandchildren have no RigidBodyAPI — skip joint entirely
                continue
            _dispatch_joint(stage, p, joints_scope_path, root_path)
    else:
        print(f"\n  [F5] RIGID — no joints")

    # ── Step 6: Bind every body/collider to PhysicsScene (Isaac / PhysX) ─
    print(f"\n  [F6] simulationOwner → {scene_path}...")
    n_bound = _bind_physics_prims_to_scene(stage, scene_path)
    print(f"       bound {n_bound} rigid body / collision prims")

    # ── Step 7: Save ─────────────────────────────────────────────────
    stage.GetRootLayer().Save()
    elapsed = time.time() - t0
    print(f"\n  ✓ Stage F complete — {elapsed:.1f}s")
    print(f"  Physics USD: {output_usd}")
    print(f"{'='*60}")

    # ── Step 8: Quick verify ─────────────────────────────────────────
    stage2   = Usd.Stage.Open(output_usd)
    def _has_api(prim, api_name):
        return any(api_name in str(s) for s in prim.GetAppliedSchemas())
    n_joints = sum(1 for p in stage2.Traverse() if "Joint" in p.GetTypeName())
    n_rigid  = sum(1 for p in stage2.Traverse() if _has_api(p, "PhysicsRigidBodyAPI"))
    n_col    = sum(1 for p in stage2.Traverse() if _has_api(p, "PhysicsCollisionAPI"))
    print(f"\n  Verify: {n_rigid} rigid bodies, {n_col} collision shapes, {n_joints} joints")

    return {
        "status": "success",
        "output_usd": output_usd,
        "n_rigid": n_rigid,
        "n_collision": n_col,
        "n_joints": n_joints,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage_c",  required=True, help="Path to stage_c.json")
    parser.add_argument("--input_usd", required=True, help="Path to validated .usd from Stage E")
    parser.add_argument("--output_dir", default=None)
    args = parser.parse_args()

    with open(args.stage_c) as f:
        spec = json.load(f)

    out_dir = args.output_dir or os.path.dirname(args.input_usd)
    os.makedirs(out_dir, exist_ok=True)

    run_stage_f(spec, args.input_usd, out_dir)

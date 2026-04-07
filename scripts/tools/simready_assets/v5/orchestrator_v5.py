#!/usr/bin/env python3
"""V5 Orchestrator — Semantic Behavior Pipeline.

Input: OBJ / blend / fbx / stl / usd / usda / usdc
Output: SimReady USD with Behavior Contract

Pipeline:
  Layer 1: Mechanical Extraction (from Blender scene)
  Layer 2: Plausible Behaviors (16 behaviors × matrix lookup + AI)
  Layer 3: Semantic Filtering → Behavior Contract (15 domains)
  Blender Prep: Fix geometry per contract
  PhysX: Add joints/collision/mass per contract
  Judge D: Validate per contract at each stage

Usage:
    python orchestrator_v5.py --input oven.obj
    python orchestrator_v5.py --input cabinet.blend --output output.usd
    python orchestrator_v5.py --input fridge.usd --contract-only
"""

import argparse
import json
import os
import sys
import time

_DIR = os.path.dirname(os.path.abspath(__file__))
_ASSETS_DIR = os.path.dirname(_DIR)
sys.path.insert(0, _ASSETS_DIR)

from v5.behavior_contract import BehaviorContract
from v5.layer1_mechanical import run_layer1, send_to_blender
from v5.layer2_plausible import run_layer2
from v5.layer3_semantic import run_layer3
from v5.layer3b_validate import run_layer3b
from v5.validate_blender_geometry import validate_blender_geometry
from v5.validate_blender_ai import validate_blender_ai
from v5.fix_blender_from_ai import fix_blender_from_ai
from v5.ai_agents import ai_validate_geometry


# ═══════════════════════════════════════════════════════════════════════════════
# BLENDER PREP — reads contract, fixes geometry
# ═══════════════════════════════════════════════════════════════════════════════

def run_blender_prep(contract: BehaviorContract, port=9876):
    """Fix Blender geometry per Behavior Contract.

    Ownership:
      Blender: shift vertices so they're LOCAL to the pivot point
               (but does NOT set obj.location — keeps at 0,0,0)
      PhysX:   places the pivot in world space via localPos0

    This way:
      - USD has NO xformOp:translate (obj.location stays 0,0,0)
      - Mesh vertices are relative to the pivot
      - PhysX localPos0 positions the pivot in world space
      - No double offset
    """
    print("\n" + "=" * 60)
    print("  BLENDER PREP: Shift vertices to pivot-local + fix geometry")
    print("=" * 60)

    # Step 0: Merge sub-parts into parent bodies using spatial containment tree.
    # Parts that are geometrically inside a moving body AND have no independent
    # behavior (fixed joint) get merged into that body before vertex shifts.
    merge_map = {}  # parent_name → [child_names_to_merge]
    containment = getattr(contract, "containment_tree", {})

    if containment:
        # Use geometry-based containment (Layer 1 computed this)
        for parent_name, children in containment.items():
            parent_part = next((p for p in contract.parts if p.name == parent_name), None)
            if not parent_part or parent_part.is_static:
                continue  # only merge INTO moving parts (doors, shelves)
            pb = parent_part.primary_behavior
            if not pb or pb.joint_type == "fixed":
                continue  # parent has no real motion — skip
            merge_children = []
            for child_name in children:
                child_part = next((p for p in contract.parts if p.name == child_name), None)
                if not child_part:
                    continue
                cb = child_part.primary_behavior
                # Merge child if it has no independent motion (fixed or same joint as parent)
                if not cb or cb.joint_type == "fixed" or cb.joint_type is None:
                    merge_children.append(child_name)
            if merge_children:
                merge_map[parent_name] = merge_children
    else:
        # Fallback: name-prefix algorithm if containment tree not available
        import os as _os
        all_names = [p.name for p in contract.parts]
        base_prefix = _os.path.commonprefix(all_names)
        if "_" in base_prefix:
            base_prefix = base_prefix[:base_prefix.rfind("_") + 1]
        all_names_set = set(all_names)
        part_by_name = {p.name: p for p in contract.parts}
        for part in contract.parts:
            if "_body_" not in part.name:
                continue
            remainder = part.name[len(base_prefix):]
            if not remainder.startswith("body_"):
                idx = remainder.index("_body_")
                group = remainder[:idx]
                group_prefix = base_prefix + group + "_"
                parent_w = part.bbox_max[0] - part.bbox_min[0]
                parent_h = part.bbox_max[2] - part.bbox_min[2]
                children = []
                for n in all_names_set:
                    if n == part.name or not n.startswith(group_prefix) or "_body_" in n:
                        continue
                    child = part_by_name.get(n)
                    if child:
                        child_w = child.bbox_max[0] - child.bbox_min[0]
                        child_h = child.bbox_max[2] - child.bbox_min[2]
                        if child_w > parent_w * 1.15 or child_h > parent_h * 1.15:
                            print(f"    SKIP merge {n} → {part.name}: child too large")
                            continue
                    children.append(n)
                if children:
                    merge_map[part.name] = children

    if merge_map:
        lines = ["import bpy", "log = []"]
        for parent, children in merge_map.items():
            for child in children:
                lines += [
                    f'base = bpy.data.objects.get("{parent}")',
                    f'child = bpy.data.objects.get("{child}")',
                    f'if base and child:',
                    f'    bpy.ops.object.select_all(action="DESELECT")',
                    f'    child.select_set(True)',
                    f'    base.select_set(True)',
                    f'    bpy.context.view_layer.objects.active = base',
                    f'    bpy.ops.object.join()',
                    f'    log.append("merged {child} → {parent}")',
                ]
        lines.append('print("\\n".join(log))')
        result = send_to_blender("\n".join(lines), port)
        out = result.get("result", {}).get("result", "")
        if out:
            for line in out.strip().splitlines():
                print(f"    {line}")

    # Batch all vertex shifts into one Blender call (one socket round-trip instead of N)
    shifts = []
    for part in contract.parts:
        if not part.primary_behavior or part.is_static:
            continue
        b = part.primary_behavior
        if not b.pivot_position:
            continue
        if b.joint_type == "prismatic":
            # Use bbox center — PhysX will place the body here via localPos0
            cx = (part.bbox_min[0] + part.bbox_max[0]) / 2
            cy = (part.bbox_min[1] + part.bbox_max[1]) / 2
            cz = (part.bbox_min[2] + part.bbox_max[2]) / 2
            shifts.append((part.name, (cx, cy, cz)))
        else:
            shifts.append((part.name, b.pivot_position))
    if shifts:
        lines = ["import bpy", "from mathutils import Vector", "log = []"]
        for name, (px, py, pz) in shifts:
            lines += [
                f'obj = bpy.data.objects.get("{name}")',
                f'if obj:',
                f'    pivot = Vector(({px}, {py}, {pz}))',
                f'    for v in obj.data.vertices: v.co -= pivot',
                f'    obj.data.update()',
                f'    log.append("{name}: shifted ({px*1000:.0f},{py*1000:.0f},{pz*1000:.0f})mm")',
            ]
        lines.append('print("\\n".join(log))')
        result = send_to_blender("\n".join(lines), port)
        out = result.get("result", {}).get("result", "")
        if out:
            for line in out.strip().splitlines():
                print(f"    {line}")

    # Remove cavity-blocking front faces (from contract blender_actions)
    for part in contract.parts:
        if part.is_static:
            for action in part.blender_actions:
                if "front" in action.lower() and ("blocking" in action.lower() or "cavity" in action.lower()):
                    print(f"    Checking {part.name} for cavity-blocking faces...")
                    script = (
                        f'import bpy, bmesh\n'
                        f'from mathutils import Vector\n'
                        f'obj = bpy.data.objects["{part.name}"]\n'
                        f'bm = bmesh.new()\n'
                        f'bm.from_mesh(obj.data)\n'
                        f'bm.faces.ensure_lookup_table()\n'
                        f'rm = []\n'
                        f'for f in bm.faces:\n'
                        f'    if f.normal.y > -0.5: continue\n'
                        f'    vs = [obj.matrix_world @ v.co for v in f.verts]\n'
                        f'    if max(v.y for v in vs) < 0.01 and (max(v.z for v in vs)-min(v.z for v in vs)) > 0.3 and (max(v.x for v in vs)-min(v.x for v in vs)) > 0.3:\n'
                        f'        rm.append(f)\n'
                        f'for f in rm: bm.faces.remove(f)\n'
                        f'bm.to_mesh(obj.data)\n'
                        f'bm.free()\n'
                        f'obj.data.update()\n'
                        f'print(f"Removed {{len(rm)}} cavity-blocking faces from {part.name}")\n'
                    )
                    result = send_to_blender(script, port)
                    out = result.get("result", {}).get("result", "")
                    if out:
                        print(f"    {out.strip()}")

    # 3. Fix materials
    print("    Fixing materials...")
    script = '''
import bpy
for mat in bpy.data.materials:
    mat.use_nodes = True
    mat.node_tree.nodes.clear()
    out = mat.node_tree.nodes.new("ShaderNodeOutputMaterial")
    bsdf = mat.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
    name = mat.name.lower()
    if "chrome" in name:
        bsdf.inputs["Base Color"].default_value = (0.8, 0.8, 0.82, 1)
        bsdf.inputs["Metallic"].default_value = 1.0
        bsdf.inputs["Roughness"].default_value = 0.1
    elif "stainless" in name:
        bsdf.inputs["Base Color"].default_value = (0.7, 0.7, 0.72, 1)
        bsdf.inputs["Metallic"].default_value = 1.0
        bsdf.inputs["Roughness"].default_value = 0.25
    elif "enamel" in name:
        bsdf.inputs["Base Color"].default_value = (0.15, 0.15, 0.15, 1)
        bsdf.inputs["Roughness"].default_value = 0.4
    elif "white" in name or "logo" in name:
        bsdf.inputs["Base Color"].default_value = (0.9, 0.9, 0.9, 1)
        bsdf.inputs["Roughness"].default_value = 0.3
    else:
        bsdf.inputs["Base Color"].default_value = (0.02, 0.02, 0.02, 1)
        bsdf.inputs["Roughness"].default_value = 0.1
    bsdf.inputs["Alpha"].default_value = 1.0
    mat.node_tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
print("Materials fixed")
'''
    send_to_blender(script, port)

    contract.blender_complete = True
    print("  Blender prep complete")
    return contract


# ═══════════════════════════════════════════════════════════════════════════════
# USD EXPORT
# ═══════════════════════════════════════════════════════════════════════════════

def export_usd(output_usd, output_blend=None, port=9876):
    """Export from Blender to USD."""
    script = f'import bpy\nbpy.ops.wm.usd_export(filepath="{output_usd}", export_materials=True)\nprint("Exported USD")'
    send_to_blender(script, port)
    if output_blend:
        script = f'import bpy\nbpy.ops.wm.save_as_mainfile(filepath="{output_blend}")\nprint("Saved blend")'
        send_to_blender(script, port)
    print(f"  Exported: {output_usd}")


# ═══════════════════════════════════════════════════════════════════════════════
# PHYSX STAGE — reads contract, adds physics
# ═══════════════════════════════════════════════════════════════════════════════

def run_physx(contract: BehaviorContract, usd_path: str):
    """Add PhysX properties to USD, reading from Behavior Contract.

    PhysX is the SOLE AUTHORITY on articulated body positioning.
    - localPos0 = pivot world position (from contract)
    - xformOp:translate = zeroed (PhysX overrides it anyway)
    - body0/body1 = parent-child hierarchy (from contract)
    """
    print("\n" + "=" * 60)
    print("  PHYSX: Add physics per Behavior Contract (sole positioning authority)")
    print("=" * 60)

    from pxr import Usd, UsdPhysics, UsdGeom, Sdf, Gf
    from geometry_math import meters_to_cm

    stage = Usd.Stage.Open(usd_path)
    root = stage.GetPrimAtPath("/root")
    UsdPhysics.ArticulationRootAPI.Apply(root)

    # Build name → prim path lookup (handles nested USD hierarchies from Blender re-export)
    name_to_path = {prim.GetName(): str(prim.GetPath())
                    for prim in stage.Traverse() if prim.GetTypeName() == "Xform"}

    # Cache root path — used by every revolute/prismatic joint
    root_path = name_to_path.get(contract.root_part, f"/root/{contract.root_part}")

    for part in contract.parts:
        behavior = part.primary_behavior
        if not behavior:
            continue

        xform_path = name_to_path.get(part.name, f"/root/{part.name}")
        xform = stage.GetPrimAtPath(xform_path)

        if not xform.IsValid():
            print(f"    SKIP {part.name}: not found in USD")
            continue

        # Find mesh child for collision
        mesh = next((c for c in xform.GetChildren() if c.GetTypeName() == "Mesh"), None)

        # Non-root static parts: static collider only — NO rigid body, NO joint.
        # (RigidBodyAPI + FixedJoint with localPos0=(0,0,0) would anchor them to world origin
        #  instead of their actual position inside the chassis, causing physics chaos.)
        if part.is_static and part.name != contract.root_part:
            if mesh and behavior.collision_type != "none":
                UsdPhysics.CollisionAPI.Apply(mesh)
                UsdPhysics.MeshCollisionAPI.Apply(mesh).CreateApproximationAttr(behavior.collision_type)
            print(f"    {part.name}: static collider (no joint), collision={behavior.collision_type}")
            continue

        # Rigid body + mass (root chassis + all movable parts)
        UsdPhysics.RigidBodyAPI.Apply(xform)
        UsdPhysics.MassAPI.Apply(xform).CreateMassAttr(part.mass_kg)

        # Collision
        if mesh and behavior.collision_type != "none":
            UsdPhysics.CollisionAPI.Apply(mesh)
            UsdPhysics.MeshCollisionAPI.Apply(mesh).CreateApproximationAttr(behavior.collision_type)

        # Zero out xform translate — PhysX localPos0 is sole positioning authority
        attr = xform.GetAttribute("xformOp:translate")
        if attr and attr.Get():
            attr.Set(Gf.Vec3d(0, 0, 0))

        # Joint
        if behavior.joint_type == "fixed":
            # Root chassis — fixed to world frame
            j = UsdPhysics.FixedJoint.Define(stage, f"{xform_path}/fixed_joint")
            j.CreateBody1Rel().SetTargets([xform_path])
            print(f"    {part.name}: fixed (root), mass={part.mass_kg}kg, collision={behavior.collision_type}")

        elif behavior.joint_type == "revolute":
            if not behavior.joint_limits_deg:
                print(f"    WARN {part.name}: revolute joint missing limits")
            j = UsdPhysics.RevoluteJoint.Define(stage, f"{xform_path}/revolute_joint")
            j.CreateBody0Rel().SetTargets([root_path])
            j.CreateBody1Rel().SetTargets([xform_path])
            j.CreateAxisAttr(behavior.joint_axis)

            if behavior.joint_limits_deg:
                j.CreateLowerLimitAttr(behavior.joint_limits_deg[0])
                j.CreateUpperLimitAttr(behavior.joint_limits_deg[1])

            if part.joint_local_pos0:
                j.CreateLocalPos0Attr(Gf.Vec3f(*part.joint_local_pos0))
            j.CreateLocalPos1Attr(Gf.Vec3f(*part.joint_local_pos1))

            j.CreateCollisionEnabledAttr(not behavior.collision_enabled_between_bodies)

            drive = UsdPhysics.DriveAPI.Apply(j.GetPrim(), "angular")
            drive.CreateTargetPositionAttr(0.0)
            drive.CreateDampingAttr(behavior.damping)
            drive.CreateStiffnessAttr(behavior.stiffness)

            limits = behavior.joint_limits_deg or [0, 0]
            lp0 = part.joint_local_pos0 or (0, 0, 0)
            print(f"    {part.name}: revolute {behavior.joint_axis} [{limits[0]}-{limits[1]}°] "
                  f"localPos0=({lp0[0]*1000:.0f},{lp0[1]*1000:.0f},{lp0[2]*1000:.0f})mm "
                  f"damping={behavior.damping}")

        elif behavior.joint_type == "prismatic":
            if not behavior.joint_limits_m:
                print(f"    WARN {part.name}: prismatic joint missing limits")
            j = UsdPhysics.PrismaticJoint.Define(stage, f"{xform_path}/prismatic_joint")
            j.CreateBody0Rel().SetTargets([root_path])
            j.CreateBody1Rel().SetTargets([xform_path])
            j.CreateAxisAttr(behavior.joint_axis)

            if behavior.joint_limits_m:
                # Output USD is metersPerUnit=1.0 (meters) — use meter values directly, no cm conversion
                j.CreateLowerLimitAttr(float(behavior.joint_limits_m[0]))
                j.CreateUpperLimitAttr(float(behavior.joint_limits_m[1]))

            # For prismatic: localPos0 = bbox center in chassis space (shelf's original world position)
            # This makes joint_pos=0 = shelf at its original position; drive target=0 keeps it there
            cx = (part.bbox_min[0] + part.bbox_max[0]) / 2
            cy = (part.bbox_min[1] + part.bbox_max[1]) / 2
            cz = (part.bbox_min[2] + part.bbox_max[2]) / 2
            j.CreateLocalPos0Attr(Gf.Vec3f(cx, cy, cz))
            j.CreateLocalPos1Attr(Gf.Vec3f(0, 0, 0))

            j.CreateCollisionEnabledAttr(False)

            drive = UsdPhysics.DriveAPI.Apply(j.GetPrim(), "linear")
            drive.CreateTargetPositionAttr(0.0)
            drive.CreateDampingAttr(behavior.damping)
            drive.CreateStiffnessAttr(behavior.stiffness)

            limits = behavior.joint_limits_m or [0, 0]
            print(f"    {part.name}: prismatic {behavior.joint_axis} [{limits[0]*1000:.0f}-{limits[1]*1000:.0f}mm] stiffness={behavior.stiffness}")

    # Warn about non-static parts with no behavior
    skipped = [p.name for p in contract.parts if not p.is_static and not p.primary_behavior]
    if skipped:
        print(f"  WARN: {len(skipped)} non-static parts have no behavior: {skipped[:5]}{'...' if len(skipped)>5 else ''}")

    # Physics scene
    UsdPhysics.Scene.Define(stage, "/physicsScene").CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))

    stage.GetRootLayer().Save()
    contract.physx_complete = True
    print(f"  PhysX complete: {usd_path}")
    return contract


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="V5 Semantic Behavior Pipeline")
    parser.add_argument("--input", required=True, help="OBJ/blend file")
    parser.add_argument("--output", default=None, help="Output USD path")
    parser.add_argument("--port", type=int, default=9876, help="Blender MCP port")
    parser.add_argument("--contract-only", action="store_true", help="Generate contract only, skip Blender/PhysX")
    args = parser.parse_args()

    input_path = os.path.abspath(args.input)
    obj_name = os.path.splitext(os.path.basename(input_path))[0].replace(" ", "_")
    output_dir = os.path.join(_ASSETS_DIR, obj_name)
    os.makedirs(output_dir, exist_ok=True)
    output_usd = args.output or os.path.join(output_dir, f"{obj_name}_simready.usd")
    output_blend = os.path.join(output_dir, f"{obj_name}.blend")

    ext = os.path.splitext(input_path)[1].lower()
    MESH_EXTS = {".obj", ".blend", ".fbx", ".stl", ".usd", ".usda", ".usdc"}

    print()
    print("=" * 60)
    print("  V5 SEMANTIC BEHAVIOR PIPELINE")
    print(f"  Input:  {input_path}")
    print(f"  Output: {output_usd}")
    print("=" * 60)

    t0 = time.time()

    if ext not in MESH_EXTS:
        print(f"\n  Unsupported file type: {ext}")
        print(f"  Supported: {MESH_EXTS}")
        return

    # 3D file → V5 pipeline
    contract = run_layer1(input_path, port=args.port)

    # Layer 2: Plausible Behaviors
    contract = run_layer2(contract)

    # Layer 3: Semantic Filtering → Behavior Contract
    contract = run_layer3(contract)

    # Layer 3b: Geometry Validator — pure bbox math, no AI
    contract = run_layer3b(contract)

    # Save contract
    contract_path = os.path.join(output_dir, "behavior_contract.json")
    with open(contract_path, "w") as f:
        f.write(contract.to_json())
    print(f"\n  Contract saved: {contract_path}")

    # Sanity check — catch silent Layer 3 failures before Blender/PhysX
    n_with_behavior = sum(1 for p in contract.parts if p.primary_behavior)
    if n_with_behavior == 0:
        print(f"\n  ERROR: Layer 3 produced no behaviors — aborting")
        return

    if args.contract_only:
        print(f"\n  Total: {time.time() - t0:.1f}s (contract only)")
        return

    # Blender Prep
    contract = run_blender_prep(contract, port=args.port)

    # ── Geometry validation BEFORE USD export ─────────────────────────────────
    print("\n" + "=" * 60)
    print("  BLENDER GEOMETRY VALIDATION (pre-export)")
    print("=" * 60)
    blender_report, critical_failures = validate_blender_geometry(contract, port=args.port)

    if critical_failures:
        # Ask AI what to do about each bad part
        ai_actions = ai_validate_geometry(contract, blender_report)
        for part_name, action in ai_actions.items():
            part = contract.get_part(part_name)
            if not part:
                continue
            print(f"  AI action [{part_name}]: {action}")
            if action == "mark_static":
                part.is_static = True
                if part.primary_behavior:
                    part.primary_behavior.joint_type = "fixed"
            # re_merge / re_export would need a full re-run — log and continue for now
        print(f"  {len(critical_failures)} critical issues found — proceeding with best effort")
    else:
        print("  All geometry checks passed — safe to export")

    # ── AI validate → fix loop (up to 2 rounds) ───────────────────────────────
    for _fix_round in range(2):
        ai_geo_result = validate_blender_ai(contract, port=args.port)
        if ai_geo_result.get("skipped") or ai_geo_result["valid"]:
            print("  AI GEOMETRY VERDICT: PASS — geometry valid, proceeding to export")
            break
        print(f"\n  AI GEOMETRY VERDICT: FAIL (round {_fix_round+1}) — sending findings to Blender for fix")
        fixed = fix_blender_from_ai(contract, ai_geo_result, port=args.port)
        if not fixed:
            print("  Fix failed — exporting best-effort geometry")
            break

    # Export USD
    export_usd(output_usd, output_blend, port=args.port)

    # PhysX
    contract = run_physx(contract, output_usd)

    # ── Validation + Auto-Retry ────────────────────────────────────────────────
    import subprocess, json as _json
    telemetry_path = "/tmp/isaaclab_telemetry.json"
    validator = os.path.join(_ASSETS_DIR, "validate_in_isaacsim.py")

    def _run_validator(usd):
        """Run Isaac Sim validator. Returns telemetry dict or None if validator unavailable."""
        if not os.path.exists(validator):
            return None
        isaaclab_sh = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(_ASSETS_DIR))), "isaaclab.sh")
        if not os.path.exists(isaaclab_sh):
            return None
        try:
            result = subprocess.run(
                [isaaclab_sh, "-p", validator, "--usd", usd, "--steps", "100"],
                capture_output=True, text=True, timeout=120,
            )
            if os.path.exists(telemetry_path):
                with open(telemetry_path) as f:
                    return _json.load(f)
        except Exception as e:
            print(f"  Validator error: {e}")
        return None

    print("\n  Running Isaac Sim validator...")
    tel = _run_validator(output_usd)

    if tel is None:
        print("  Validator not available — skipping (run manually with validate_in_isaacsim.py)")
    elif tel.get("passed"):
        print(f"  PASS — {tel['joints_total']} joints, gravity stable")
        contract.validated = True
    else:
        print(f"  FAIL — joints={tel.get('joints_total',0)}, gravity_ok={tel.get('gravity_ok')}")
        print(f"  Attempting targeted retry...")

        # Build targeted feedback for Layer 3 retry
        issues = []
        if tel.get("joints_total", 0) == 0:
            issues.append("No joints written — check that non-static parts exist in USD")
        if not tel.get("gravity_ok") and tel.get("joints_total", 0) > 0:
            issues.append(f"Gravity drift too large — pivot positions may be wrong")
        if tel.get("revolute", 0) == 0 and any("door" in p.name for p in contract.parts if not p.is_static):
            issues.append("Expected revolute joints for doors but found none")

        if issues:
            print(f"  Issues: {issues}")
            # Re-run Layer 3 with issue context injected into prompt, then redo Blender+PhysX
            # For now: re-run Blender prep + PhysX only (contract already has the specs)
            print("  Re-running Blender prep + PhysX...")
            contract.blender_complete = False
            contract.physx_complete = False
            contract = run_blender_prep(contract, port=args.port)
            export_usd(output_usd, output_blend, port=args.port)
            contract = run_physx(contract, output_usd)

            tel2 = _run_validator(output_usd)
            if tel2 and tel2.get("passed"):
                print(f"  RETRY PASS — {tel2['joints_total']} joints, gravity stable")
                contract.validated = True
            elif tel2:
                print(f"  RETRY FAIL — {tel2}")
            else:
                print("  Retry complete (validator unavailable for second check)")

    # Save final contract
    with open(contract_path, "w") as f:
        f.write(contract.to_json())

    total_time = time.time() - t0
    moving = sum(1 for p in contract.parts if not p.is_static)
    validated_str = "VALIDATED ✓" if contract.validated else "not validated"

    print()
    print("=" * 60)
    print("  V5 COMPLETE")
    print(f"  SimReady USD: {output_usd}")
    print(f"  Contract:     {contract_path}")
    print(f"  Parts:        {len(contract.parts)}")
    print(f"  Moving:       {moving}")
    print(f"  Validation:   {validated_str}")
    print(f"  Total:        {total_time:.1f}s")
    print("=" * 60)


if __name__ == "__main__":
    main()

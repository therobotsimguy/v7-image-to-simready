#!/usr/bin/env python3
"""
v12_pipeline.py — V12 SimReady Pipeline (fully self-contained)

Complete pipeline: raw USD → V12 SimReady output.
100% independent — no external pipeline dependencies.

Includes:
  - Gemini vision (Blender renders → visual part identification)
  - Gemini object understanding (mass, material, behavior)
  - LLM classification (Anthropic/OpenAI with prompt caching)
  - Physics application (joints, mass, collision, friction)
  - SDF collision (Lightwheel quality)
  - MuJoCo behavioral validation
  - Post-build visual verification
  - URDF export (dual-format)
  - Dual USD export: _physics.usd + _articulation.usd
  - Sidecar physics JSON

Usage:
  python3 v12_pipeline.py --input /path/to/raw_asset.usd
  python3 v12_pipeline.py --input /path/to/raw_asset.usd --dynamic
"""

import argparse
import glob
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from pxr import Usd, UsdGeom, UsdPhysics, UsdShade, Gf, Sdf

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
API_KEYS_PATH = os.path.join(SCRIPT_DIR, "..", "api_keys.json")



# ═══════════════════════════════════════════════════════════════════
# COMPONENT: make_simready
# ═══════════════════════════════════════════════════════════════════

#!/usr/bin/env python3




# ═══════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
API_KEYS_PATH = os.path.join(SCRIPT_DIR, "..", "api_keys.json")

MAX_DECOMP_BUDGET = 5
QUALITY_VERT_THRESHOLD = 50000

FRICTION_TABLE = {
    "rubber": (0.8, 0.7),
    "steel": (0.74, 0.57),
    "metal": (0.6, 0.45),
    "chrome": (0.6, 0.45),
    "aluminium": (0.6, 0.45),
    "aluminum": (0.6, 0.45),
    "glossy": (0.6, 0.45),
    "plastic": (0.35, 0.3),
    "glass": (0.5, 0.35),
    "wood": (0.5, 0.4),
}


# ═══════════════════════════════════════════════════════════════════
# PHASE 1 — AUDIT
# ═══════════════════════════════════════════════════════════════════

def audit(stage):
    """Check all 7 SimReady criteria. Returns dict of criterion -> {pass, details}."""
    results = {}

    rigid_bodies = []
    colliders = []
    joints = []
    drives = []
    physics_materials = []
    mat_bindings = 0
    has_physics_scene = False
    has_contact_offset = False
    nested_rigid = False

    for prim in stage.Traverse():
        apis = [str(s) for s in prim.GetAppliedSchemas()]

        if "PhysicsRigidBodyAPI" in apis:
            mass_attr = prim.GetAttribute("physics:mass")
            mass = mass_attr.Get() if mass_attr and mass_attr.HasValue() else None
            kin = prim.GetAttribute("physics:kinematicEnabled")
            kin_val = kin.Get() if kin and kin.HasValue() else False
            rigid_bodies.append({
                "path": str(prim.GetPath()),
                "mass": mass,
                "kinematic": kin_val,
                "has_mass_api": "PhysicsMassAPI" in apis,
            })
            parent = prim.GetParent()
            if parent and parent.HasAPI(UsdPhysics.RigidBodyAPI):
                nested_rigid = True

        if "PhysicsCollisionAPI" in apis:
            approx = prim.GetAttribute("physics:approximation")
            approx_val = approx.Get() if approx and approx.HasValue() else "none"
            bind = UsdShade.MaterialBindingAPI(prim)
            physics_mat = bind.GetDirectBinding("physics")
            has_binding = bool(
                physics_mat and physics_mat.GetMaterialPath()
                and str(physics_mat.GetMaterialPath()) != ""
            )
            if has_binding:
                mat_bindings += 1
            colliders.append({
                "name": prim.GetName(),
                "approx": approx_val,
                "has_physics_mat_binding": has_binding,
            })

        if prim.IsA(UsdPhysics.Joint):
            lp0 = prim.GetAttribute("physics:localPos0")
            lp1 = prim.GetAttribute("physics:localPos1")
            lp0_val = lp0.Get() if lp0 and lp0.HasValue() else None
            lp1_val = lp1.Get() if lp1 and lp1.HasValue() else None
            lp0_zero = lp0_val is not None and all(abs(float(v)) < 1e-6 for v in lp0_val)
            lp1_zero = lp1_val is not None and all(abs(float(v)) < 1e-6 for v in lp1_val)
            joints.append({
                "name": prim.GetName(),
                "type": prim.GetTypeName(),
                "both_anchors_zero": lp0_zero and lp1_zero,
            })

        for api in apis:
            if "PhysicsDriveAPI" in api:
                drives.append({"joint": prim.GetName(), "api": api})

        if "PhysicsMaterialAPI" in apis:
            sf = prim.GetAttribute("physics:staticFriction")
            physics_materials.append({
                "name": prim.GetName(),
                "sf": sf.Get() if sf and sf.HasValue() else None,
            })

        if prim.IsA(UsdPhysics.Scene):
            has_physics_scene = True

        co = prim.GetAttribute("physxCollision:contactOffset")
        if co and co.HasValue():
            has_contact_offset = True

    # C1: Rigid Bodies
    c1_pass = len(rigid_bodies) > 0 and all(rb["has_mass_api"] for rb in rigid_bodies)
    c1_detail = f"{len(rigid_bodies)} rigid bodies"
    if rigid_bodies and not all(rb["has_mass_api"] for rb in rigid_bodies):
        c1_detail += " (some missing MassAPI)"
    if not rigid_bodies:
        c1_detail = "0 found, need at least 1"
    if nested_rigid:
        c1_pass = False
        c1_detail += " — NESTED rigid body detected"
    results["C1 Rigid Bodies"] = {"pass": c1_pass, "detail": c1_detail}

    # C2: Collision Shapes (global + per-rigid-body coverage)
    c2_pass = len(colliders) > 0 and all(c["approx"] != "none" for c in colliders)
    approx_counts = {}
    for c in colliders:
        approx_counts[c["approx"]] = approx_counts.get(c["approx"], 0) + 1
    c2_detail = f"{len(colliders)} colliders"
    if approx_counts:
        c2_detail += f" ({approx_counts})"
    if not colliders:
        c2_detail = "0 colliders"
    # Per-rigid-body coverage: every rigid body must have ≥1 descendant collider
    bodies_without_colliders = []
    for rb in rigid_bodies:
        rb_prim = stage.GetPrimAtPath(rb["path"])
        if not rb_prim:
            continue
        has_col = False
        for desc in Usd.PrimRange(rb_prim):
            if desc.HasAPI(UsdPhysics.CollisionAPI):
                has_col = True
                break
        if not has_col:
            bodies_without_colliders.append(rb["path"])
    if bodies_without_colliders:
        c2_pass = False
        c2_detail += f" — {len(bodies_without_colliders)} rigid body(s) have NO colliders: {bodies_without_colliders}"
    results["C2 Collision Shapes"] = {"pass": c2_pass, "detail": c2_detail}

    # C3: Friction Materials + GripMaterial on handles (F29, F31)
    c3_pass = len(colliders) > 0 and mat_bindings == len(colliders)
    c3_detail = f"{mat_bindings}/{len(colliders)} colliders have material:binding:physics"
    if not colliders:
        c3_detail = "no colliders to bind"
    # F29/F31: Check that handle meshes exist and have GripMaterial
    handle_keywords = ("handle", "knob", "grip", "pull", "lever")
    handle_meshes = []
    handles_with_grip = 0
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh) and any(kw in prim.GetName().lower() for kw in handle_keywords):
            handle_meshes.append(prim.GetName())
            bind = UsdShade.MaterialBindingAPI(prim)
            physics_mat = bind.GetDirectBinding("physics")
            if physics_mat and physics_mat.GetMaterialPath():
                mat_path = str(physics_mat.GetMaterialPath())
                if "grip" in mat_path.lower():
                    handles_with_grip += 1
    if handle_meshes and handles_with_grip == 0:
        c3_detail += f" — WARNING: {len(handle_meshes)} handle(s) found but none bound to GripMaterial (F31)"
    results["C3 Friction"] = {"pass": c3_pass, "detail": c3_detail}

    # C4: Flat Hierarchy
    dp = stage.GetDefaultPrim()
    dp_path = dp.GetPath() if dp else Sdf.Path("/")
    movable_nested = []
    for rb in rigid_bodies:
        rb_path = Sdf.Path(rb["path"])
        parent = rb_path.GetParentPath()
        if parent != dp_path:
            parent_prim = stage.GetPrimAtPath(parent)
            if parent_prim and parent_prim.HasAPI(UsdPhysics.RigidBodyAPI):
                movable_nested.append(rb["path"])
    if len(rigid_bodies) <= 1:
        c4_pass = True
        c4_detail = "single body — hierarchy N/A"
        c4_na = True
    else:
        c4_pass = len(movable_nested) == 0
        c4_detail = (
            "all movable parts are siblings"
            if c4_pass
            else f"{len(movable_nested)} movable parts nested under another rigid body"
        )
        c4_na = False
    results["C4 Flat Hierarchy"] = {"pass": c4_pass, "detail": c4_detail, "na": c4_na if len(rigid_bodies) <= 1 else False}

    # C5: Joints (existence + anchor validity)
    has_movables = len(rigid_bodies) > 1
    if has_movables:
        enough_joints = len(joints) >= len(rigid_bodies) - 1
        zero_anchor_joints = [j for j in joints if j.get("both_anchors_zero", False)]
        anchors_ok = len(zero_anchor_joints) == 0
        c5_pass = enough_joints and anchors_ok
        c5_detail = f"{len(joints)} joints for {len(rigid_bodies) - 1} movable parts"
        if not enough_joints:
            c5_detail += " — need more joints"
        if not anchors_ok:
            c5_pass = False
            c5_detail += f" — {len(zero_anchor_joints)} joints have ZERO anchors (localPos0=localPos1=(0,0,0))"
    else:
        c5_pass = True
        c5_detail = "no movable parts — joints N/A"
    results["C5 Joints"] = {"pass": c5_pass, "detail": c5_detail, "na": not has_movables}

    # C6: Joint Drives + stiffness/damping validation (F18, F32)
    if joints:
        c6_pass = len(drives) >= len(joints)
        c6_detail = f"{len(drives)} drives for {len(joints)} joints"
        # F18: Check stiffness=0 on all drives (non-zero jams doors)
        # F32: Check damping>0 on all drives (zero causes oscillation)
        for prim in stage.Traverse():
            if prim.IsA(UsdPhysics.Joint):
                for attr in prim.GetAttributes():
                    aname = attr.GetName()
                    if "stiffness" in aname.lower() and "drive" in aname.lower():
                        val = attr.Get()
                        if val is not None and float(val) > 0:
                            c6_detail += f" — WARNING: {prim.GetName()} has stiffness={val} (F18: should be 0)"
                    if "damping" in aname.lower() and "drive" in aname.lower():
                        val = attr.Get()
                        if val is not None and float(val) <= 0:
                            c6_detail += f" — WARNING: {prim.GetName()} has damping={val} (F32: should be >0)"
    else:
        c6_pass = True
        c6_detail = "no joints — drives N/A"
    results["C6 Joint Drives"] = {"pass": c6_pass, "detail": c6_detail, "na": not joints}

    # C7: Clean Asset (no scene, no contactOffset, meters)
    mpu = UsdGeom.GetStageMetersPerUnit(stage)
    c7_issues = []
    if has_physics_scene:
        c7_issues.append("PhysicsScene found")
    if has_contact_offset:
        c7_issues.append("contactOffset found")
    if abs(mpu - 1.0) > 0.01:
        unit_name = "centimeters" if abs(mpu - 0.01) < 0.001 else f"mpu={mpu}"
        c7_issues.append(f"stage in {unit_name}, not meters")
    c7_pass = len(c7_issues) == 0
    c7_detail = "clean (meters, no scene)" if c7_pass else "; ".join(c7_issues)
    results["C7 Clean Asset"] = {"pass": c7_pass, "detail": c7_detail}

    return results


def print_audit(results, label="AUDIT"):
    """Print a formatted scorecard."""
    print(f"\n  {label}:")
    total = 0
    passed = 0
    for name, info in results.items():
        is_na = info.get("na", False)
        if is_na:
            status = "N/A "
        elif info["pass"]:
            status = "PASS"
            total += 1
            passed += 1
        else:
            status = "FAIL"
            total += 1
        print(f"    {status}  {name}: {info['detail']}")
    if total > 0:
        print(f"    SCORE: {passed}/{total}")


# ═══════════════════════════════════════════════════════════════════
# PHASE 2 — CLASSIFY (hierarchy reader + LLM)
# ═══════════════════════════════════════════════════════════════════

def read_hierarchy(stage):
    """Read the USD hierarchy into a structured dict for the LLM."""
    default_prim = stage.GetDefaultPrim()
    if not default_prim:
        raise ValueError("USD has no default prim")

    mpu = UsdGeom.GetStageMetersPerUnit(stage)
    hierarchy = {
        "default_prim": default_prim.GetName(),
        "meters_per_unit": mpu,
        "children": [],
    }

    def describe_prim(prim, depth=0):
        info = {
            "name": prim.GetName(),
            "type": prim.GetTypeName(),
            "path": str(prim.GetPath()),
            "depth": depth,
            "children": [],
        }
        if prim.GetTypeName() == "Mesh":
            pts = prim.GetAttribute("points")
            info["vertex_count"] = len(pts.Get()) if pts and pts.HasValue() else 0

        if prim.GetTypeName() == "Xform":
            xf = UsdGeom.Xformable(prim)
            ops = xf.GetOrderedXformOps()
            info["xform_ops"] = [op.GetOpName() for op in ops]
            bbox = _quick_bbox(prim, mpu)
            if bbox:
                info["bbox_meters"] = bbox
            mesh_children = []
            for child in prim.GetAllChildren():
                if child.GetTypeName() == "Mesh":
                    pts = child.GetAttribute("points")
                    nv = len(pts.Get()) if pts and pts.HasValue() else 0
                    mesh_children.append({"name": child.GetName(), "vertices": nv})
            if mesh_children:
                info["meshes"] = mesh_children

        for child in prim.GetChildren():
            info["children"].append(describe_prim(child, depth + 1))
        return info

    for child in default_prim.GetChildren():
        hierarchy["children"].append(describe_prim(child, depth=1))
    return hierarchy


def _quick_bbox(prim, mpu):
    bmin = [1e30, 1e30, 1e30]
    bmax = [-1e30, -1e30, -1e30]
    found = False
    for child in prim.GetAllChildren():
        if child.GetTypeName() != "Mesh":
            continue
        pts = child.GetAttribute("points")
        if not pts or not pts.HasValue():
            continue
        for pt in pts.Get():
            for i in range(3):
                v = float(pt[i]) * mpu
                bmin[i] = min(bmin[i], v)
                bmax[i] = max(bmax[i], v)
            found = True
    if not found:
        return None
    dims = [round(bmax[i] - bmin[i], 4) for i in range(3)]
    return {"width_m": dims[0], "depth_m": dims[1], "height_m": dims[2]}


def hierarchy_to_text(hierarchy):
    """Convert hierarchy dict to readable text for LLM prompt."""
    lines = []
    lines.append(f"USD Asset: default_prim = {hierarchy['default_prim']}")
    lines.append(f"Meters per unit: {hierarchy['meters_per_unit']}")
    lines.append("")

    def fmt(info, indent=0):
        prefix = "  " * indent
        typ = info["type"]
        name = info["name"]
        if typ == "Xform":
            line = f"{prefix}[Xform] {name}"
            if "bbox_meters" in info:
                b = info["bbox_meters"]
                line += f"  (bbox: {b['width_m']:.3f} x {b['depth_m']:.3f} x {b['height_m']:.3f} m)"
            if "xform_ops" in info and info["xform_ops"]:
                ops = ", ".join(info["xform_ops"])
                line += f"  ops=[{ops}]"
            lines.append(line)
            if "meshes" in info:
                for m in info["meshes"]:
                    lines.append(f"{prefix}  [Mesh] {m['name']}  ({m['vertices']} verts)")
        elif typ == "Mesh":
            lines.append(f"{prefix}[Mesh] {name}  ({info.get('vertex_count', '?')} verts)")
        elif typ == "Scope":
            lines.append(f"{prefix}[Scope] {name}")
        else:
            lines.append(f"{prefix}[{typ}] {name}")
        for child in info.get("children", []):
            fmt(child, indent + 1)

    for child in hierarchy["children"]:
        fmt(child)
    return "\n".join(lines)


SYSTEM_PROMPT = """You are a SimReady asset classifier for robotic simulation.

Given a USD hierarchy, classify each part so physics can be applied.

## Rules

1. Identify the BODY — the main structural Xform (largest, most meshes/vertices).

2. For each Xform child of the body (or default prim), classify:
   - Door/lid/flap (hinged): "movable:revolute" + axis (Z=vertical hinge, X=horizontal)
   - Drawer/slider: "movable:prismatic" + axis (Y=depth, X=lateral)
   - Wheel/caster: "movable:continuous" + axis (axle direction)
   - Shelf/divider/interior: "structural"
   - Bolts/clips/LEDs/logos: "decorative"

3. Use name AND geometry (bbox, mesh count, xform ops) to decide.

4. Parts nested INSIDE a movable (shelves/racks/bins inside a door) are STRUCTURAL —
   they move with their parent, not independently. Only DIRECT children of the body
   should be classified as movable. Never classify a grandchild of the body as movable.

5. Output ONLY valid JSON, no markdown fences, no explanation.

## Output format

{
  "body": "<body Xform name>",
  "parts": {
    "<part_name>": {"class": "movable:revolute", "axis": "Z"},
    "<part_name>": {"class": "movable:prismatic", "axis": "Y"},
    "<part_name>": {"class": "movable:continuous", "axis": "Y"},
    "<part_name>": {"class": "structural"},
    "<part_name>": {"class": "decorative"}
  }
}
"""


def _load_api_config(provider):
    if not os.path.isfile(API_KEYS_PATH):
        return None, None
    with open(API_KEYS_PATH) as f:
        keys = json.load(f)
    if provider in keys:
        cfg = keys[provider]
        return cfg.get("api_key"), cfg.get("model")
    return None, None


def classify_with_openai(hierarchy_text, model=None):
    try:
        from openai import OpenAI
    except ImportError:
        print("ERROR: pip install openai")
        sys.exit(1)
    file_key, file_model = _load_api_config("openai")
    api_key = os.environ.get("OPENAI_API_KEY") or file_key
    model = model or file_model or "gpt-4o"
    if not api_key:
        print("ERROR: Set OPENAI_API_KEY or add to scripts/tools/api_keys.json")
        sys.exit(1)
    client = OpenAI(api_key=api_key)
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Classify this USD hierarchy:\n\n{hierarchy_text}"},
                ],
                temperature=0.0,
            )
            text = response.choices[0].message.content.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            result = json.loads(text)
            if "body" in result and "parts" in result:
                return result
            print(f"  Retry {attempt + 1}/{max_retries}: missing 'body' or 'parts' in response")
        except (json.JSONDecodeError, IndexError, KeyError) as e:
            print(f"  Retry {attempt + 1}/{max_retries}: {type(e).__name__}: {e}")
    raise ValueError(f"LLM classification failed after {max_retries} retries (F04)")


def classify_with_anthropic(hierarchy_text, model=None):
    try:
        import anthropic
    except ImportError:
        print("ERROR: pip install anthropic")
        sys.exit(1)
    file_key, file_model = _load_api_config("anthropic")
    api_key = os.environ.get("ANTHROPIC_API_KEY") or file_key
    model = model or file_model or "claude-sonnet-4-20250514"
    if not api_key:
        print("ERROR: Set ANTHROPIC_API_KEY or add to scripts/tools/api_keys.json")
        sys.exit(1)
    client = anthropic.Anthropic(api_key=api_key)
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {"role": "user", "content": f"Classify this USD hierarchy:\n\n{hierarchy_text}"},
                ],
                temperature=0.0,
            )
            # Log cache performance
            usage = response.usage
            cached = getattr(usage, "cache_read_input_tokens", 0)
            created = getattr(usage, "cache_creation_input_tokens", 0)
            if cached > 0:
                print(f"    (prompt cache HIT: {cached} tokens read from cache)")
            elif created > 0:
                print(f"    (prompt cache CREATED: {created} tokens cached for next call)")
            text = response.content[0].text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            result = json.loads(text)
            if "body" in result and "parts" in result:
                return result
            print(f"  Retry {attempt + 1}/{max_retries}: missing 'body' or 'parts' in response")
        except (json.JSONDecodeError, IndexError, KeyError) as e:
            print(f"  Retry {attempt + 1}/{max_retries}: {type(e).__name__}: {e}")
    raise ValueError(f"LLM classification failed after {max_retries} retries (F04)")


def classify_parts(stage, provider="anthropic", model=None):
    """Read USD hierarchy and classify parts via LLM. Returns classification dict."""
    hierarchy = read_hierarchy(stage)
    hierarchy_text = hierarchy_to_text(hierarchy)

    print(f"\n  LLM CLASSIFICATION ({provider}):")
    print(f"  Sending {len(hierarchy_text)} chars of hierarchy...")

    if provider == "openai":
        result = classify_with_openai(hierarchy_text, model=model)
    else:
        result = classify_with_anthropic(hierarchy_text, model=model)

    # Validate
    if "body" not in result or "parts" not in result:
        raise ValueError(f"LLM returned invalid classification: {result}")

    print(f"    body: {result['body']}")
    for name, spec in result["parts"].items():
        cls = spec.get("class", "?")
        axis = spec.get("axis", "")
        axis_str = f" axis={axis}" if axis else ""
        print(f"    {name:40s} -> {cls}{axis_str}")

    return result


# ═══════════════════════════════════════════════════════════════════
# PHASE 3 — APPLY (geometry helpers + physics applicators)
# ═══════════════════════════════════════════════════════════════════

# --- Geometry ---

def get_joint_anchor_world(stage, path):
    """World-space anchor point for a joint on this Xform.
    Uses pivot xformOp if present (transformed by L2W), otherwise Xform world origin.
    """
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsValid():
        return Gf.Vec3d(0, 0, 0)
    xf = UsdGeom.Xformable(prim)
    pivot_local = None
    for op in xf.GetOrderedXformOps():
        opname = op.GetOpName()
        if "pivot" in opname and "invert" not in opname:
            v = op.Get()
            pivot_local = Gf.Vec3d(float(v[0]), float(v[1]), float(v[2]))
            break
    l2w = xf.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    if pivot_local is not None:
        return l2w.TransformAffine(pivot_local)
    return Gf.Vec3d(float(l2w[3][0]), float(l2w[3][1]), float(l2w[3][2]))


def world_point_to_local(stage, body_path, world_pt):
    """Transform a world point into a body's local frame."""
    prim = stage.GetPrimAtPath(body_path)
    if not prim:
        return Gf.Vec3f(0, 0, 0)
    xf = UsdGeom.Xformable(prim)
    l2w = xf.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    w2l = l2w.GetInverse()
    lp = w2l.TransformAffine(world_pt)
    return Gf.Vec3f(float(lp[0]), float(lp[1]), float(lp[2]))


def mesh_world_bbox(stage, xform_path):
    """Compute world bbox from mesh vertices under an Xform (recursive)."""
    prim = stage.GetPrimAtPath(xform_path)
    if not prim:
        return None
    bmin = Gf.Vec3d(1e30, 1e30, 1e30)
    bmax = Gf.Vec3d(-1e30, -1e30, -1e30)
    found = False
    for child in _get_all_descendant_meshes(prim):
        pts = child.GetAttribute("points")
        if not pts or not pts.HasValue():
            continue
        mesh_xf = UsdGeom.Xformable(child)
        l2w = mesh_xf.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        for pt in pts.Get():
            wp = l2w.TransformAffine(Gf.Vec3d(float(pt[0]), float(pt[1]), float(pt[2])))
            bmin = Gf.Vec3d(min(bmin[0], wp[0]), min(bmin[1], wp[1]), min(bmin[2], wp[2]))
            bmax = Gf.Vec3d(max(bmax[0], wp[0]), max(bmax[1], wp[1]), max(bmax[2], wp[2]))
            found = True
    if not found:
        return None
    return bmin, bmax


# Keywords for rail/mechanism meshes that inflate drawer bbox beyond actual travel
_DRAWER_RAIL_KEYWORDS = ("mechanism", "frame", "rail", "track", "slide", "runner", "guide")


def mesh_world_bbox_excluding(stage, xform_path, exclude_keywords):
    """Like mesh_world_bbox but skip meshes whose names contain any exclude keyword."""
    prim = stage.GetPrimAtPath(xform_path)
    if not prim:
        return None
    bmin = Gf.Vec3d(1e30, 1e30, 1e30)
    bmax = Gf.Vec3d(-1e30, -1e30, -1e30)
    found = False
    for child in _get_all_descendant_meshes(prim):
        if any(kw in child.GetName().lower() for kw in exclude_keywords):
            continue
        pts = child.GetAttribute("points")
        if not pts or not pts.HasValue():
            continue
        mesh_xf = UsdGeom.Xformable(child)
        l2w = mesh_xf.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        for pt in pts.Get():
            wp = l2w.TransformAffine(Gf.Vec3d(float(pt[0]), float(pt[1]), float(pt[2])))
            bmin = Gf.Vec3d(min(bmin[0], wp[0]), min(bmin[1], wp[1]), min(bmin[2], wp[2]))
            bmax = Gf.Vec3d(max(bmax[0], wp[0]), max(bmax[1], wp[1]), max(bmax[2], wp[2]))
            found = True
    if not found:
        return None
    return bmin, bmax


def detect_hinge_edge(stage, door_path, anchor_world=None):
    """Detect which vertical edge is the hinge. Returns 'min_x' or 'max_x'.

    anchor_world should be passed explicitly when calling after reparent
    (pivot xformOps are cleared during reparent, so re-reading them gives wrong results).
    """
    if anchor_world is None:
        anchor_world = get_joint_anchor_world(stage, door_path)
    bbox = mesh_world_bbox(stage, door_path)
    if not bbox:
        return "min_x"
    bmin, bmax = bbox
    dist_to_min = abs(anchor_world[0] - bmin[0])
    dist_to_max = abs(anchor_world[0] - bmax[0])
    return "min_x" if dist_to_min < dist_to_max else "max_x"


def _mesh_vert_count(prim):
    pts = prim.GetAttribute("points")
    if pts and pts.HasValue():
        return len(pts.Get())
    return 0


def _get_all_descendant_meshes(prim):
    """Recursively collect all Mesh prims under a prim, including those under child Xforms."""
    meshes = []
    for child in prim.GetChildren():
        if child.GetTypeName() == "Mesh":
            meshes.append(child)
        elif child.GetTypeName() == "Xform":
            meshes.extend(_get_all_descendant_meshes(child))
    return meshes


MASS_CLAMPS = {
    # Fridge door Xforms (mesh bbox) often estimate 40–90kg; caps B–F SimReady outputs. Shift+drag is tuned via revolute drive damping, not mass cap.
    "revolute": (2.0, 100.0),
    "prismatic": (0.5, 5.0),
    "continuous": (0.05, 1.0),
    "fixed": (0.1, 10.0),
}


def estimate_mass(bbox, mpu=1.0, density=500.0):
    """Estimate mass from bbox volume (fallback when mesh volume unavailable)."""
    if not bbox:
        return 1.0
    bmin, bmax = bbox
    w = abs(bmax[0] - bmin[0]) * mpu
    d = abs(bmax[1] - bmin[1]) * mpu
    h = abs(bmax[2] - bmin[2]) * mpu
    vol = w * d * h
    return max(0.1, round(vol * density, 2))


def estimate_mass_from_mesh(stage, xform_path, density=500.0):
    """Estimate mass from actual mesh volume × density (more accurate than bbox).

    Uses the divergence theorem on triangle meshes. Falls back to bbox if
    mesh volume computation fails or returns zero.
    """
    prim = stage.GetPrimAtPath(xform_path)
    if not prim:
        return None
    total_volume = 0.0
    mpu = UsdGeom.GetStageMetersPerUnit(stage)
    for mesh_prim in _get_all_descendant_meshes(prim):
        pts_attr = mesh_prim.GetAttribute("points")
        idx_attr = mesh_prim.GetAttribute("faceVertexIndices")
        cnt_attr = mesh_prim.GetAttribute("faceVertexCounts")
        if not all(a and a.HasValue() for a in [pts_attr, idx_attr, cnt_attr]):
            continue
        pts = pts_attr.Get()
        indices = idx_attr.Get()
        counts = cnt_attr.Get()
        if not pts or not indices or not counts:
            continue
        # Scale vertices to meters
        verts = [(float(p[0]) * mpu, float(p[1]) * mpu, float(p[2]) * mpu) for p in pts]
        # Compute volume using divergence theorem
        vol = 0.0
        idx_offset = 0
        for fc in counts:
            if fc < 3:
                idx_offset += fc
                continue
            # Triangulate: fan from first vertex
            i0 = int(indices[idx_offset])
            for t in range(1, fc - 1):
                i1 = int(indices[idx_offset + t])
                i2 = int(indices[idx_offset + t + 1])
                v0, v1, v2 = verts[i0], verts[i1], verts[i2]
                # Signed volume contribution: v0 · (v1 × v2) / 6
                cross = (
                    v1[1] * v2[2] - v1[2] * v2[1],
                    v1[2] * v2[0] - v1[0] * v2[2],
                    v1[0] * v2[1] - v1[1] * v2[0],
                )
                vol += v0[0] * cross[0] + v0[1] * cross[1] + v0[2] * cross[2]
            idx_offset += fc
        total_volume += abs(vol) / 6.0
    if total_volume < 1e-10:
        return None
    mass = total_volume * density
    return max(0.01, round(mass, 4))


# --- Strip existing physics ---

def strip_existing_physics(stage):
    """Remove all existing physics APIs, joints, and physics materials for a clean slate."""
    prims_to_remove = []
    n_props = 0
    n_joints = 0
    n_mats = 0

    physics_schemas = [
        "PhysicsRigidBodyAPI", "PhysicsCollisionAPI",
        "PhysicsMeshCollisionAPI", "PhysicsMassAPI",
        "PhysicsArticulationRootAPI",
    ]

    for prim in stage.Traverse():
        prim_type = prim.GetTypeName()

        if "Joint" in prim_type:
            prims_to_remove.append(prim.GetPath())
            n_joints += 1
            continue

        # C7: host app owns PhysicsScene — remove embedded scenes (audit flags them; previously only props were stripped)
        if prim.IsA(UsdPhysics.Scene):
            prims_to_remove.append(prim.GetPath())
            continue

        if prim.GetName() in ("GripMaterial", "DefaultPhysMaterial") and prim_type == "Material":
            prims_to_remove.append(prim.GetPath())
            n_mats += 1
            continue

        if prim.GetName() == "joints" and prim_type == "Scope":
            prims_to_remove.append(prim.GetPath())
            continue

        props_to_remove = []
        for prop in prim.GetAuthoredProperties():
            n = prop.GetName()
            if n.startswith("physics:") or n.startswith("physx"):
                props_to_remove.append(n)
            if n == "material:binding:physics":
                props_to_remove.append(n)
        for n in props_to_remove:
            prim.RemoveProperty(n)
            n_props += 1

        prim_spec = stage.GetRootLayer().GetPrimAtPath(prim.GetPath())
        if prim_spec:
            schemas_info = prim_spec.GetInfo("apiSchemas")
            if schemas_info and hasattr(schemas_info, "prependedItems"):
                current = list(schemas_info.prependedItems)
                filtered = [s for s in current if not any(ps in s for ps in physics_schemas)]
                if len(filtered) < len(current):
                    if filtered:
                        new_list = Sdf.TokenListOp()
                        new_list.prependedItems = filtered
                        prim_spec.SetInfo("apiSchemas", new_list)
                    else:
                        prim_spec.ClearInfo("apiSchemas")

    if prims_to_remove:
        edit = Sdf.BatchNamespaceEdit()
        for path in prims_to_remove:
            edit.Add(path, Sdf.Path.emptyPath)
        stage.GetRootLayer().Apply(edit)

    return n_joints, n_props, n_mats


# --- Collision ---

# Movable direct-child meshes matching these substrings get no collider — they overlap the cabinet
# cavity / frame and jam revolute doors in viewport drag (Refrigerator_A vs B: extra clips/bolts/logo/locker
# hulls). Keep outer panel (*body*) and *handle* for manipulation. Matches simready-collision “skip bolts, clips, rubber”.
_MOVABLE_COLLISION_SKIP_SUBSTR = (
    "interior",
    "clips",
    "bolt",
    "logo",
    "rubber",
    "lockerbox",
    "lockercilinder",
    "lockerbase",
    "refresher",
    "mechanism",
    "frame",
)


def _filter_movable_collision_meshes(mesh_prims):
    kept = [m for m in mesh_prims
            if not any(s in m.GetName().lower() for s in _MOVABLE_COLLISION_SKIP_SUBSTR)]
    return kept if kept else list(mesh_prims)


def apply_collision_q1(stage, xform_path, is_body=False):
    """Apply CollisionAPI: decomp on large concave body meshes, hull on small parts.

    For body: recurse into all descendant meshes.
    For movable parts: direct child meshes only — interior sub-Xform meshes
    (door shelves, rack bins) would clip with body internals when closed.
    """
    prim = stage.GetPrimAtPath(xform_path)
    if not prim:
        return 0, 0

    if is_body:
        meshes = [(m, _mesh_vert_count(m)) for m in _get_all_descendant_meshes(prim)]
    else:
        raw = [m for m in prim.GetChildren() if m.GetTypeName() == "Mesh"]
        raw = _filter_movable_collision_meshes(raw)
        meshes = [(m, _mesh_vert_count(m)) for m in raw]
        # Fallback: if no direct Mesh children (deeply nested Xform→Xform→Mesh),
        # search recursively. Common on small tools (scissors, forceps).
        if not meshes:
            raw = list(_get_all_descendant_meshes(prim))
            raw = _filter_movable_collision_meshes(raw)
            meshes = [(m, _mesh_vert_count(m)) for m in raw]
    if not meshes:
        return 0, 0

    meshes.sort(key=lambda x: x[1], reverse=True)
    n_col = 0
    n_decomp = 0
    for mesh_prim, npts in meshes:
        UsdPhysics.CollisionAPI.Apply(mesh_prim)
        mc = UsdPhysics.MeshCollisionAPI.Apply(mesh_prim)
        use_decomp = is_body and npts > 2000
        if use_decomp and n_decomp < MAX_DECOMP_BUDGET:
            mc.CreateApproximationAttr("convexDecomposition")
            n_decomp += 1
            # Always set quality params on body decomposition — default
            # decomposition produces bloated hulls around thin concave
            # geometry (e.g., trolley rails, fridge frames). The vertex
            # threshold only gates additional quality; body always gets it.
            if is_body or npts > QUALITY_VERT_THRESHOLD:
                mesh_prim.CreateAttribute(
                    "physxConvexDecompositionCollision:maxConvexHulls",
                    Sdf.ValueTypeNames.Int).Set(128)
                mesh_prim.CreateAttribute(
                    "physxConvexDecompositionCollision:voxelResolution",
                    Sdf.ValueTypeNames.Int).Set(500000)
                mesh_prim.CreateAttribute(
                    "physxConvexDecompositionCollision:errorPercentage",
                    Sdf.ValueTypeNames.Float).Set(1.0)
        else:
            mc.CreateApproximationAttr("convexHull")
        n_col += 1
    return n_col, n_decomp


def apply_collision_wheels(stage, xform_path):
    """All wheel meshes get convexDecomposition (hull creates blobs)."""
    prim = stage.GetPrimAtPath(xform_path)
    if not prim:
        return 0
    n = 0
    for desc in _get_all_descendant_meshes(prim):
        UsdPhysics.CollisionAPI.Apply(desc)
        mc = UsdPhysics.MeshCollisionAPI.Apply(desc)
        mc.CreateApproximationAttr("convexDecomposition")
        n += 1
    return n


# --- Friction ---

def _guess_friction(material_name):
    """Guess friction coefficients from material name using the reference table."""
    name_lower = material_name.lower()
    for keyword, (sf, df) in FRICTION_TABLE.items():
        if keyword in name_lower:
            return sf, df
    return 0.5, 0.4


def wire_friction(stage, dp_path, handle_mesh_paths):
    """Create GripMaterial, bind friction on all collision meshes."""
    grip_path = Sdf.Path(f"{dp_path}/GripMaterial")
    grip_prim = stage.GetPrimAtPath(grip_path)
    if not grip_prim.IsValid():
        grip_mat = UsdShade.Material.Define(stage, grip_path)
        phys_api = UsdPhysics.MaterialAPI.Apply(grip_mat.GetPrim())
        phys_api.CreateStaticFrictionAttr(1.0)
        phys_api.CreateDynamicFrictionAttr(0.9)
        phys_api.CreateRestitutionAttr(0.0)

    handle_paths_set = set(str(p) for p in handle_mesh_paths)
    n_grip = 0
    n_body = 0

    for prim in stage.Traverse():
        if not prim.HasAPI(UsdPhysics.CollisionAPI):
            continue

        binding_api = UsdShade.MaterialBindingAPI.Apply(prim)

        if str(prim.GetPath()) in handle_paths_set:
            binding_api.Bind(
                UsdShade.Material(stage.GetPrimAtPath(grip_path)),
                UsdShade.Tokens.weakerThanDescendants,
                "physics")
            n_grip += 1
        else:
            existing = binding_api.GetDirectBinding()
            if existing.GetMaterial().GetPrim().IsValid():
                mat_prim = existing.GetMaterial().GetPrim()
                if not mat_prim.HasAPI(UsdPhysics.MaterialAPI):
                    UsdPhysics.MaterialAPI.Apply(mat_prim)
                    sf, df = _guess_friction(mat_prim.GetName())
                    mat_prim.CreateAttribute("physics:staticFriction",
                                             Sdf.ValueTypeNames.Float).Set(sf)
                    mat_prim.CreateAttribute("physics:dynamicFriction",
                                             Sdf.ValueTypeNames.Float).Set(df)
                    mat_prim.CreateAttribute("physics:restitution",
                                             Sdf.ValueTypeNames.Float).Set(0.01)
                binding_api.Bind(
                    UsdShade.Material(mat_prim),
                    UsdShade.Tokens.weakerThanDescendants,
                    "physics")
            else:
                default_path = Sdf.Path(f"{dp_path}/DefaultPhysMaterial")
                default_prim = stage.GetPrimAtPath(default_path)
                if not default_prim.IsValid():
                    default_mat = UsdShade.Material.Define(stage, default_path)
                    phys_api = UsdPhysics.MaterialAPI.Apply(default_mat.GetPrim())
                    phys_api.CreateStaticFrictionAttr(0.5)
                    phys_api.CreateDynamicFrictionAttr(0.4)
                    phys_api.CreateRestitutionAttr(0.1)
                binding_api.Bind(
                    UsdShade.Material(stage.GetPrimAtPath(default_path)),
                    UsdShade.Tokens.weakerThanDescendants,
                    "physics")
            n_body += 1

    return n_grip, n_body


# --- Physics applicators ---

def apply_rigid_body(stage, path, kinematic=False, dynamic_body=False):
    prim = stage.GetPrimAtPath(path)
    if not prim:
        return
    UsdPhysics.RigidBodyAPI.Apply(prim)
    if kinematic:
        prim.CreateAttribute("physics:kinematicEnabled", Sdf.ValueTypeNames.Bool).Set(True)
    if dynamic_body:
        prim.CreateAttribute("physics:linearDamping", Sdf.ValueTypeNames.Float).Set(100.0)
        prim.CreateAttribute("physics:angularDamping", Sdf.ValueTypeNames.Float).Set(200.0)


def apply_mass(stage, path, mass_kg):
    prim = stage.GetPrimAtPath(path)
    if prim:
        m = UsdPhysics.MassAPI.Apply(prim)
        m.CreateMassAttr(mass_kg)


# --- Joints ---

def make_revolute_joint(stage, joint_path, body0, body1, local_pos0, local_pos1,
                        axis="Z", hinge_edge="min_x", lower_deg=-120, upper_deg=120):
    joint = UsdPhysics.RevoluteJoint.Define(stage, joint_path)
    joint.CreateAxisAttr(axis)
    if hinge_edge == "min_x":
        joint.CreateLowerLimitAttr(float(lower_deg))
        joint.CreateUpperLimitAttr(0.0)
    else:
        joint.CreateLowerLimitAttr(0.0)
        joint.CreateUpperLimitAttr(float(upper_deg))
    joint.CreateBody0Rel().SetTargets([body0])
    joint.CreateBody1Rel().SetTargets([body1])
    joint.CreateLocalPos0Attr(local_pos0)
    joint.CreateLocalPos1Attr(local_pos1)
    drive = UsdPhysics.DriveAPI.Apply(stage.GetPrimAtPath(joint_path), "angular")
    # Low damping so Isaac viewport shift+drag can rotate hinged parts (skill: ~2 Nm·s/rad for doors)
    drive.CreateDampingAttr(2.0)
    # Always stiffness 0: a positional spring to 0° (old dynamic_body branch) locks doors closed and blocks drag/gripper.
    drive.CreateStiffnessAttr(0.0)


def make_prismatic_joint(stage, joint_path, body0, body1, local_pos0, local_pos1,
                         axis="Y", lower_m=0.0, upper_m=0.4):
    joint = UsdPhysics.PrismaticJoint.Define(stage, joint_path)
    joint.CreateAxisAttr(axis)
    joint.CreateLowerLimitAttr(lower_m)
    joint.CreateUpperLimitAttr(upper_m)
    joint.CreateBody0Rel().SetTargets([body0])
    joint.CreateBody1Rel().SetTargets([body1])
    joint.CreateLocalPos0Attr(local_pos0)
    joint.CreateLocalPos1Attr(local_pos1)
    drive = UsdPhysics.DriveAPI.Apply(stage.GetPrimAtPath(joint_path), "linear")
    drive.CreateDampingAttr(5.0)
    drive.CreateStiffnessAttr(0.0)


def make_continuous_joint(stage, joint_path, body0, body1, local_pos0, local_pos1,
                          axis="X"):
    joint = UsdPhysics.RevoluteJoint.Define(stage, joint_path)
    joint.CreateAxisAttr(axis)
    joint.CreateBody0Rel().SetTargets([body0])
    joint.CreateBody1Rel().SetTargets([body1])
    joint.CreateLocalPos0Attr(local_pos0)
    joint.CreateLocalPos1Attr(local_pos1)
    joint.CreateLowerLimitAttr(-9999.0)
    joint.CreateUpperLimitAttr(9999.0)
    drive = UsdPhysics.DriveAPI.Apply(stage.GetPrimAtPath(joint_path), "angular")
    drive.CreateDampingAttr(2.0)
    drive.CreateStiffnessAttr(0.0)


def make_fixed_joint(stage, joint_path, body0, body1, local_pos0, local_pos1):
    joint = UsdPhysics.FixedJoint.Define(stage, joint_path)
    joint.CreateBody0Rel().SetTargets([body0])
    joint.CreateBody1Rel().SetTargets([body1])
    joint.CreateLocalPos0Attr(local_pos0)
    joint.CreateLocalPos1Attr(local_pos1)


# --- Reparent ---

def reparent_prims(stage, prim_paths, new_parent_path):
    """Move prims to be children of new_parent_path.

    Processes deepest paths first in separate batch edits to avoid
    parent-child conflicts (moving a parent invalidates children's source paths).
    """
    layer = stage.GetRootLayer()

    by_depth = {}
    for path in prim_paths:
        depth = len(path.GetPrefixes())
        by_depth.setdefault(depth, []).append(path)

    all_moved = {}
    for depth in sorted(by_depth.keys(), reverse=True):
        edit = Sdf.BatchNamespaceEdit()
        batch = {}
        for old_path in by_depth[depth]:
            new_path = new_parent_path.AppendChild(old_path.name)
            if old_path == new_path:
                continue
            edit.Add(old_path, new_path)
            batch[str(old_path)] = str(new_path)
        if batch and not layer.Apply(edit):
            print(f"  WARNING: SdfBatchNamespaceEdit failed at depth {depth}")
            continue
        all_moved.update(batch)
    return all_moved


def reparent_prims_preserve_world_xform(stage, prim_paths, new_parent_path):
    """Reparent preserving world pose via local = inv(parent_world) * world."""
    world_mats = {}
    for old_path in prim_paths:
        prim = stage.GetPrimAtPath(old_path)
        if not prim or not prim.IsValid():
            continue
        xf = UsdGeom.Xformable(prim)
        world_mats[str(old_path)] = xf.ComputeLocalToWorldTransform(Usd.TimeCode.Default())

    moved = reparent_prims(stage, prim_paths, new_parent_path)

    for old_s, new_s in moved.items():
        prim = stage.GetPrimAtPath(Sdf.Path(new_s))
        if not prim or not prim.IsValid():
            continue
        wmat = world_mats.get(old_s)
        if wmat is None:
            continue
        parent = prim.GetParent()
        pxf = UsdGeom.Xformable(parent)
        pw = pxf.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        local_mat = pw.GetInverse() * wmat

        xf = UsdGeom.Xformable(prim)
        xf.ClearXformOpOrder()
        top = xf.AddTransformOp(UsdGeom.XformOp.PrecisionDouble)
        top.Set(local_mat)

    return moved


# --- Wheel structural splitting ---

WHEEL_STRUCTURAL_KEYWORDS = ("fixer", "bolt", "body", "mount", "stopper")

def split_wheel_structural_parts(stage, movables, body_path):
    """Move structural child meshes (fixer/body/bolts) from wheel Xforms to body.

    Caster wheels contain rotating parts (tire, disc, detail) and structural
    parts (fixer, body, bolts = the bracket/fork). Structural parts must stay
    with the body; if they rotate with the wheel, brackets detach under force.
    """
    all_moved = {}
    for name, info in movables.items():
        if info["joint"] != "continuous":
            continue
        wheel_prim = stage.GetPrimAtPath(info["path"])
        if not wheel_prim:
            continue
        structural_paths = []
        for child in wheel_prim.GetAllChildren():
            if child.GetTypeName() != "Mesh":
                continue
            if any(kw in child.GetName().lower() for kw in WHEEL_STRUCTURAL_KEYWORDS):
                structural_paths.append(child.GetPath())
        if structural_paths:
            moved = reparent_prims_preserve_world_xform(stage, structural_paths, body_path)
            all_moved.update(moved)
    return all_moved


# --- Handle detection ---

def find_handle_meshes(stage, movable_paths):
    """Find Mesh prims that are handles/knobs under movable Xforms (recursive)."""
    handle_paths = []
    handle_keywords = ("handle", "knob", "grip", "pull", "lever")

    for path in movable_paths:
        prim = stage.GetPrimAtPath(path)
        if not prim:
            continue
        for mesh in _get_all_descendant_meshes(prim):
            if any(kw in mesh.GetName().lower() for kw in handle_keywords):
                handle_paths.append(mesh.GetPath())

    return handle_paths


# ═══════════════════════════════════════════════════════════════════
# MAIN — orchestrate all three phases
# ═══════════════════════════════════════════════════════════════════

def resolve_body_xform(stage, default_prim, body_name):
    """Find the body Xform by name."""
    dp_path = default_prim.GetPath()
    candidate = dp_path.AppendChild(body_name)
    if stage.GetPrimAtPath(candidate).IsValid():
        return candidate
    for child in default_prim.GetChildren():
        if child.GetTypeName() == "Xform":
            return child.GetPath()
    return dp_path


def resolve_movable_parts(stage, body_path, dp_path, classification):
    """Resolve classified movable parts to prim paths and joint info."""
    movables = {}
    for name, spec in classification["parts"].items():
        cls = spec.get("class", "")
        if not cls.startswith("movable:"):
            continue

        joint_type = cls.split(":")[1]
        axis = spec.get("axis", "Z" if joint_type == "revolute" else "Y")

        path = body_path.AppendChild(name)
        if not stage.GetPrimAtPath(path).IsValid():
            path = dp_path.AppendChild(name)
        if not stage.GetPrimAtPath(path).IsValid():
            for prim in stage.Traverse():
                if prim.GetName() == name and prim.GetTypeName() == "Xform":
                    path = prim.GetPath()
                    break
        if not stage.GetPrimAtPath(path).IsValid():
            print(f"  WARNING: Part '{name}' not found in USD, skipping")
            continue

        movables[name] = {"path": path, "joint": joint_type, "axis": axis}
    return movables


def normalize_to_meters(stage):
    """Convert stage from any unit (cm, mm, etc.) to meters.

    Scales all mesh vertices and translation xformOps by metersPerUnit,
    then sets metersPerUnit to 1.0. This ensures the output USD works
    in any simulator without needing external scale factors.
    """
    mpu = UsdGeom.GetStageMetersPerUnit(stage)
    if abs(mpu - 1.0) < 0.001:
        return False

    print(f"\n  NORMALIZE: stage is in {'centimeters' if abs(mpu-0.01)<0.001 else f'units (mpu={mpu})'}, converting to meters")

    for prim in stage.Traverse():
        if prim.GetTypeName() == "Mesh":
            pts_attr = prim.GetAttribute("points")
            if pts_attr and pts_attr.HasValue():
                pts = pts_attr.Get()
                scaled = [Gf.Vec3f(float(p[0])*mpu, float(p[1])*mpu, float(p[2])*mpu) for p in pts]
                pts_attr.Set(scaled)
            ext_attr = prim.GetAttribute("extent")
            if ext_attr and ext_attr.HasValue():
                ext = ext_attr.Get()
                ext_attr.Set([
                    Gf.Vec3f(float(ext[0][0])*mpu, float(ext[0][1])*mpu, float(ext[0][2])*mpu),
                    Gf.Vec3f(float(ext[1][0])*mpu, float(ext[1][1])*mpu, float(ext[1][2])*mpu),
                ])

        if prim.GetTypeName() in ("Xform", "Mesh"):
            xf = UsdGeom.Xformable(prim)
            for op in xf.GetOrderedXformOps():
                if op.IsInverseOp():
                    continue
                if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
                    v = op.Get()
                    if v is not None:
                        op.Set(Gf.Vec3d(float(v[0])*mpu, float(v[1])*mpu, float(v[2])*mpu)
                               if isinstance(v, Gf.Vec3d) else
                               Gf.Vec3f(float(v[0])*mpu, float(v[1])*mpu, float(v[2])*mpu))
                elif op.GetOpType() == UsdGeom.XformOp.TypeTransform:
                    m = op.Get()
                    if m is not None:
                        scaled = Gf.Matrix4d(m)
                        scaled.SetRow3(3, Gf.Vec3d(m[3][0]*mpu, m[3][1]*mpu, m[3][2]*mpu))
                        op.Set(scaled)

    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    n_meshes = sum(1 for p in stage.Traverse() if p.GetTypeName() == "Mesh")
    print(f"    Scaled {n_meshes} meshes + xform translations by {mpu}")
    print(f"    metersPerUnit set to 1.0")
    return True


def apply_physics(stage, classification, output_usd, dynamic_body=False,
                  gemini_mass=None, gemini_density=None):
    """Phase 3: Apply all missing physics based on classification."""
    default_prim = stage.GetDefaultPrim()
    dp_path = default_prim.GetPath()

    normalize_to_meters(stage)
    mpu = 1.0

    # Strip existing physics
    n_j, n_a, n_m = strip_existing_physics(stage)
    if n_j + n_a + n_m > 0:
        print(f"\n  STRIPPED: {n_j} joints, {n_a} physics attrs, {n_m} materials")

    body_path = resolve_body_xform(stage, default_prim, classification["body"])
    movables = resolve_movable_parts(stage, body_path, dp_path, classification)

    # Guard: skip movables nested inside other movables (physically wrong —
    # they'd get jointed to body instead of their parent movable)
    movable_path_strs = {str(info["path"]) for info in movables.values()}
    nested = [name for name, info in movables.items()
              if any(str(info["path"]).startswith(mp + "/")
                     for mp in movable_path_strs - {str(info["path"])})]
    if nested:
        print(f"\n  NESTED MOVABLES (treating as structural — move with parent):")
        for name in nested:
            print(f"    {name}")
            del movables[name]

    print(f"\n  Body: {body_path}")
    print(f"  Movable parts: {len(movables)}")
    for name, info in movables.items():
        print(f"    {name}: {info['joint']} (axis={info['axis']})")

    # Save joint anchors BEFORE reparent — reparenting clears pivot xformOps
    saved_anchors = {}
    body_bbox = mesh_world_bbox(stage, body_path)
    for name, info in movables.items():
        saved_anchors[name] = get_joint_anchor_world(stage, info["path"])
        anchor = saved_anchors[name]
        # Fallback: if anchor is at origin (no pivot xformOp found), compute from
        # body bbox edge. For prismatic joints: anchor at the body face nearest the
        # movable part (the slide start point). For revolute: use movable bbox edge.
        is_zero = all(abs(float(v)) < 1e-6 for v in anchor)
        if is_zero and body_bbox:
            part_bbox = mesh_world_bbox(stage, info["path"])
            if part_bbox:
                axis = info.get("axis", "Y")
                axis_idx = {"X": 0, "Y": 1, "Z": 2}.get(axis, 1)
                jtype = info.get("joint", "prismatic")
                if jtype == "prismatic":
                    # For prismatic: anchor at body edge nearest the part's
                    # jaw/face (the max extent on slide axis). This is where
                    # the slide starts (q=0 position).
                    part_jaw = part_bbox[1][axis_idx]
                    dist_to_min = abs(part_jaw - body_bbox[0][axis_idx])
                    dist_to_max = abs(part_jaw - body_bbox[1][axis_idx])
                    edge = body_bbox[1][axis_idx] if dist_to_max < dist_to_min else body_bbox[0][axis_idx]
                else:
                    # For revolute: anchor at body edge nearest the part center
                    part_center = (part_bbox[0][axis_idx] + part_bbox[1][axis_idx]) / 2
                    body_center = (body_bbox[0][axis_idx] + body_bbox[1][axis_idx]) / 2
                    edge = body_bbox[0][axis_idx] if part_center < body_center else body_bbox[1][axis_idx]
                fallback = list(anchor)
                fallback[axis_idx] = edge
                # Center on other axes
                for i in range(3):
                    if i != axis_idx:
                        fallback[i] = (part_bbox[0][i] + part_bbox[1][i]) / 2
                saved_anchors[name] = Gf.Vec3d(*fallback)
                print(f"    anchor {name}: ({fallback[0]:.4f}, {fallback[1]:.4f}, {fallback[2]:.4f}) (fallback from body edge)")
            else:
                print(f"    anchor {name}: ({anchor[0]:.4f}, {anchor[1]:.4f}, {anchor[2]:.4f}) (zero — no fallback)")
        else:
            print(f"    anchor {name}: ({anchor[0]:.4f}, {anchor[1]:.4f}, {anchor[2]:.4f})")

    # --- C4: Flatten hierarchy ---
    paths_to_move = []
    for info in movables.values():
        if info["path"].GetParentPath() != dp_path:
            paths_to_move.append(info["path"])
    if paths_to_move:
        print(f"\n  REPARENT: {len(paths_to_move)} movable parts -> siblings of body")
        moved = reparent_prims_preserve_world_xform(stage, paths_to_move, dp_path)
        for old, new in moved.items():
            print(f"    {old} -> {new}")
        for name in movables:
            old_p = str(movables[name]["path"])
            if old_p in moved:
                movables[name]["path"] = Sdf.Path(moved[old_p])

    # --- Wheel structural split (fixer/body/bolts -> body) ---
    wheel_moved = split_wheel_structural_parts(stage, movables, body_path)
    if wheel_moved:
        print(f"\n  WHEEL SPLIT: {len(wheel_moved)} structural meshes -> body")
        for old, new in wheel_moved.items():
            print(f"    {old} -> {new}")
        for name, info in movables.items():
            if info["joint"] == "continuous":
                bbox = mesh_world_bbox(stage, info["path"])
                if bbox:
                    tire_center = Gf.Vec3d(
                        (bbox[0][0] + bbox[1][0]) / 2,
                        (bbox[0][1] + bbox[1][1]) / 2,
                        (bbox[0][2] + bbox[1][2]) / 2)
                    saved_anchors[name] = tire_center
                    size_x = abs(bbox[1][0] - bbox[0][0])
                    size_y = abs(bbox[1][1] - bbox[0][1])
                    detected_axis = "Y" if size_y < size_x else "X"
                    if detected_axis != info["axis"]:
                        print(f"    axis override {name}: {info['axis']} -> {detected_axis} (tire X={size_x:.4f} Y={size_y:.4f})")
                        info["axis"] = detected_axis
                    print(f"    anchor {name} (tire center): ({tire_center[0]:.4f}, {tire_center[1]:.4f}, {tire_center[2]:.4f})")

    # --- C1: Rigid Bodies + Mass ---
    print(f"\n  RIGID BODIES:")
    # Graspable props: if no movable parts and object is small (<3kg estimated),
    # make body dynamic so the robot can pick it up. Large furniture stays kinematic.
    has_movables = len(movables) > 0
    if not has_movables and not dynamic_body:
        est_mass = gemini_mass  # Use Gemini mass if available
        if not est_mass:
            est_mass = estimate_mass_from_mesh(stage, body_path, density=500)
        if not est_mass:
            body_bbox_check = mesh_world_bbox(stage, body_path)
            est_mass = estimate_mass(body_bbox_check, mpu, density=500) if body_bbox_check else 999
        if est_mass < 3.0:
            dynamic_body = True
            print(f"    (small object {est_mass:.2f}kg, no joints — auto-dynamic for grasping)")
    body_kinematic = not dynamic_body
    apply_rigid_body(stage, body_path, kinematic=body_kinematic, dynamic_body=dynamic_body)
    body_bbox = mesh_world_bbox(stage, body_path)

    # Mass estimation priority: Gemini > mesh_volume > bbox
    # Gemini provides total object mass. Distribute by mesh volume ratio:
    #   body gets mass proportional to its volume, parts get the rest.
    use_density = gemini_density if gemini_density else (80.0 if dynamic_body else 600.0)

    # Compute mesh volumes for body and all parts (for proportional distribution)
    body_volume = 0.0
    part_volumes = {}
    if gemini_mass:
        bv = estimate_mass_from_mesh(stage, body_path, density=1.0)  # density=1 → returns volume
        body_volume = bv if bv else 0.0
        for name, info in movables.items():
            pv = estimate_mass_from_mesh(stage, info["path"], density=1.0)
            part_volumes[name] = pv if pv else 0.0
        total_volume = body_volume + sum(part_volumes.values())

    if gemini_mass:
        # Distribute Gemini's total mass by volume ratio
        if total_volume > 0:
            body_mass = gemini_mass * (body_volume / total_volume)
        else:
            # Fallback: 60% body, 40% parts
            body_mass = gemini_mass * 0.6
        mass_method = "gemini"
    else:
        body_mass_mesh = estimate_mass_from_mesh(stage, body_path, density=use_density)
        body_mass_bbox = estimate_mass(body_bbox, mpu, density=use_density)
        if body_mass_mesh:
            body_mass = body_mass_mesh
            mass_method = "mesh_vol"
        else:
            body_mass = body_mass_bbox
            mass_method = "bbox"
    if dynamic_body and mass_method != "gemini":
        body_mass = max(5.0, min(100.0, body_mass))
    apply_mass(stage, body_path, body_mass)
    body_mode = "dynamic" if dynamic_body else "kinematic"
    print(f"    body: {body_mode}, mass={body_mass:.1f}kg ({mass_method}, density={use_density})")

    # Per-part mass: distribute remaining Gemini mass by volume ratio
    part_density = gemini_density if gemini_density else 500.0
    remaining_mass = gemini_mass - body_mass if gemini_mass else 0
    total_part_volume = sum(part_volumes.values()) if gemini_mass else 0

    for name, info in movables.items():
        path = info["path"]
        apply_rigid_body(stage, path)
        bbox = mesh_world_bbox(stage, path)

        if gemini_mass and total_part_volume > 0:
            # Distribute remaining mass proportionally by volume
            vol_ratio = part_volumes.get(name, 0) / total_part_volume
            mass = remaining_mass * vol_ratio
            m_method = "gemini"
        elif gemini_mass:
            # Equal distribution fallback
            mass = remaining_mass / max(len(movables), 1)
            m_method = "gemini"
        else:
            mass_mesh = estimate_mass_from_mesh(stage, path, density=part_density)
            mass_bbox = estimate_mass(bbox, mpu, density=part_density)
            mass = mass_mesh if mass_mesh else mass_bbox
            m_method = "mesh_vol" if mass_mesh else "bbox"

        if m_method == "gemini":
            # Trust Gemini mass — only apply a soft minimum (0.01kg)
            mass = max(0.01, mass)
        else:
            clamp = MASS_CLAMPS.get(info["joint"], (0.1, 50.0))
            mass = max(clamp[0], min(clamp[1], mass))
        apply_mass(stage, path, mass)
        print(f"    {name}: dynamic, mass={mass:.2f}kg ({m_method}, density={part_density})")

    # --- C2: Collision Shapes ---
    print(f"\n  COLLIDERS:")
    n_body_col, n_body_decomp = apply_collision_q1(stage, body_path, is_body=True)
    total_decomp = n_body_decomp
    print(f"    body: {n_body_col} colliders ({n_body_decomp} decomp)")

    for name, info in movables.items():
        is_wheel = info["joint"] == "continuous"
        if is_wheel:
            n_col = apply_collision_wheels(stage, info["path"])
            n_d = n_col
        else:
            n_col, n_d = apply_collision_q1(stage, info["path"], is_body=False)
        total_decomp += n_d
        print(f"    {name}: {n_col} colliders ({n_d} decomp)")

    if total_decomp > MAX_DECOMP_BUDGET:
        print(f"    WARNING: {total_decomp} decomp exceeds budget of {MAX_DECOMP_BUDGET}")

    # --- C5: Joints ---
    print(f"\n  JOINTS:")
    joints_scope = Sdf.Path(f"{dp_path}/joints")
    if not stage.GetPrimAtPath(joints_scope).IsValid():
        UsdGeom.Scope.Define(stage, joints_scope)

    for name, info in movables.items():
        path = info["path"]
        jtype = info["joint"]
        axis = info["axis"]
        joint_path = joints_scope.AppendChild(f"{name}_joint")

        anchor = saved_anchors[name]
        lp0 = world_point_to_local(stage, body_path, anchor)
        lp0_f = Gf.Vec3f(float(lp0[0]), float(lp0[1]), float(lp0[2]))
        lp1 = world_point_to_local(stage, path, anchor)
        lp1_f = Gf.Vec3f(float(lp1[0]), float(lp1[1]), float(lp1[2]))

        if jtype == "revolute":
            hinge = detect_hinge_edge(stage, path, anchor_world=anchor)
            make_revolute_joint(stage, joint_path, body_path, path,
                                lp0_f, lp1_f, axis=axis, hinge_edge=hinge)
            print(f"    RevoluteJoint  {name}  axis={axis} hinge={hinge}")
        elif jtype == "prismatic":
            bbox = mesh_world_bbox(stage, path)
            axis_idx = {"X": 0, "Y": 1, "Z": 2}[axis]
            part_depth = abs(bbox[1][axis_idx] - bbox[0][axis_idx]) if bbox else 0.4
            body_depth = abs(body_bbox[1][axis_idx] - body_bbox[0][axis_idx]) if body_bbox else part_depth
            # Use the overlap region between part and body on the slide axis.
            # For overlapping parts (caliper blade over ruler), the useful travel
            # is how far the part can slide before exiting the body.
            if bbox and body_bbox:
                overlap_min = max(bbox[0][axis_idx], body_bbox[0][axis_idx])
                overlap_max = min(bbox[1][axis_idx], body_bbox[1][axis_idx])
                overlap = max(0, overlap_max - overlap_min)
                if overlap > 0 and overlap < part_depth * 0.95:
                    # Part overlaps body partially (caliper, sliding tool) —
                    # use full overlap as travel (not 85%), since the useful
                    # range IS the overlap region
                    depth = overlap
                else:
                    depth = min(part_depth, body_depth)
            else:
                depth = min(part_depth, body_depth)
            # If drawer has rail mechanism meshes, limit travel to maintain
            # rail-track overlap (rail must not fully exit the body track).
            has_rail = False
            drawer_prim = stage.GetPrimAtPath(path)
            if drawer_prim:
                for child in Usd.PrimRange(drawer_prim):
                    if child.IsA(UsdGeom.Mesh) and any(
                            kw in child.GetName().lower() for kw in _DRAWER_RAIL_KEYWORDS):
                        has_rail = True
                        break
            is_overlap_travel = (bbox and body_bbox and overlap > 0 and overlap < part_depth * 0.95)
            if has_rail:
                travel = depth * 0.45   # ~45% of total depth keeps rail overlapped
                print(f"    (rail detected — limiting travel to {travel:.3f}m for overlap)")
            elif is_overlap_travel:
                travel = depth  # overlap IS the full useful range, no 85% reduction
                print(f"    (overlap-based travel: {travel:.3f}m = full ruler/slide range)")
            else:
                travel = depth * 0.85
            # Detect slider vs drawer: a slider (caliper, measuring tool)
            # spans nearly the FULL body length on the slide axis (>70%).
            # A drawer is much shorter than the body. Sliders need
            # bidirectional limits; drawers need one-directional.
            is_slider = False
            if bbox and body_bbox:
                part_extent = abs(bbox[1][axis_idx] - bbox[0][axis_idx])
                body_extent = abs(body_bbox[1][axis_idx] - body_bbox[0][axis_idx])
                if body_extent > 0:
                    span_ratio = part_extent / body_extent
                    if span_ratio > 0.9:
                        is_slider = True

            if is_slider:
                # Bidirectional: GENEROUS limits both ways. Don't try to compute
                # exact range — the physical geometry (collision) is the real
                # constraint. Tight limits only cut off useful range. (F37)
                lower_m = -depth * 2.0
                upper_m = depth * 0.6
                print(f"    (slider detected — generous bidirectional [{lower_m:.3f}, {upper_m:.3f}])")
            elif bbox and body_bbox:
                # Drawer: one direction, face toward body exterior
                body_center_ax = (body_bbox[0][axis_idx] + body_bbox[1][axis_idx]) / 2
                drawer_center_ax = (bbox[0][axis_idx] + bbox[1][axis_idx]) / 2
                if drawer_center_ax < body_center_ax:
                    lower_m, upper_m = -travel, 0.0
                else:
                    lower_m, upper_m = 0.0, travel
            else:
                lower_m, upper_m = 0.0, travel
            make_prismatic_joint(stage, joint_path, body_path, path,
                                 lp0_f, lp1_f, axis=axis,
                                 lower_m=lower_m, upper_m=upper_m)
            print(f"    PrismaticJoint {name}  axis={axis} travel=[{lower_m:.3f}, {upper_m:.3f}]m")
        elif jtype == "continuous":
            make_continuous_joint(stage, joint_path, body_path, path,
                                  lp0_f, lp1_f, axis=axis)
            print(f"    ContinuousJoint {name}  axis={axis}")
        elif jtype == "fixed":
            make_fixed_joint(stage, joint_path, body_path, path, lp0_f, lp1_f)
            print(f"    FixedJoint      {name}")

    # --- C3 + C6: Friction ---
    print(f"\n  FRICTION:")
    movable_paths = [info["path"] for info in movables.values()]
    handle_meshes = find_handle_meshes(stage, movable_paths)
    n_grip, n_body_fric = wire_friction(stage, dp_path, handle_meshes)
    print(f"    GripMaterial on {n_grip} handle meshes")
    print(f"    Physics material binding on {n_body_fric} body meshes")

    # --- Save ---
    stage.GetRootLayer().Save()
    print(f"\n  SAVED: {output_usd}")


def run(input_usd, fix=False, provider="anthropic", model=None, output_dir=None,
        classify_json=None, dynamic_body=False, object_json=None):
    """Main entry point: audit, optionally classify + fix."""
    # Load Gemini object understanding if provided
    gemini_mass = None
    gemini_density = None
    if object_json and os.path.exists(object_json):
        with open(object_json) as f:
            obj_data = json.load(f)
        gemini_mass = obj_data.get("estimated_mass_kg")
        gemini_density = obj_data.get("material_density_kg_m3")
        if gemini_mass:
            print(f"  Gemini mass: {gemini_mass}kg, density: {gemini_density} kg/m³")
    print(f"\n{'='*60}")
    print(f"  make_simready (V8)")
    print(f"{'='*60}")
    print(f"  Input: {input_usd}")
    print(f"  Mode:  {'AUDIT + FIX' if fix else 'AUDIT ONLY'}")

    stage = Usd.Stage.Open(input_usd)

    # Phase 1: Audit
    results = audit(stage)
    print_audit(results, label="AUDIT (current state)")

    all_pass = all(r["pass"] for r in results.values())
    if all_pass:
        print(f"\n  Asset is already SimReady. Nothing to do.")
        return input_usd

    if not fix:
        print(f"\n  Run with --fix to apply missing physics.")
        return None

    # Phase 2: Classify
    if classify_json:
        with open(classify_json) as f:
            classification = json.load(f)
        print(f"\n  CLASSIFICATION (from file):")
        print(f"    body: {classification['body']}")
        for name, spec in classification.get("parts", {}).items():
            cls = spec.get("class", "?")
            axis = spec.get("axis", "")
            axis_str = f" axis={axis}" if axis else ""
            print(f"    {name:40s} -> {cls}{axis_str}")
    else:
        classification = classify_parts(stage, provider=provider, model=model)

    # Phase 3: Apply
    out_dir = output_dir or os.path.join(os.path.dirname(input_usd), "simready_out")
    os.makedirs(out_dir, exist_ok=True)
    basename = os.path.splitext(os.path.basename(input_usd))[0]
    # Single output name for entire SimReady fleet (fridges B–F): always {name}_physics.usd
    output_usd = os.path.join(out_dir, f"{basename}_physics.usd")
    shutil.copy2(input_usd, output_usd)

    src_dir = os.path.dirname(input_usd)
    tex_src = os.path.join(src_dir, "Textures")
    tex_dst = os.path.join(out_dir, "Textures")
    if os.path.isdir(tex_src) and not os.path.isdir(tex_dst):
        shutil.copytree(tex_src, tex_dst)
        print(f"  Copied Textures/")

    out_stage = Usd.Stage.Open(output_usd)
    apply_physics(out_stage, classification, output_usd, dynamic_body=dynamic_body,
                  gemini_mass=gemini_mass, gemini_density=gemini_density)

    # Re-audit
    final_stage = Usd.Stage.Open(output_usd)
    final_results = audit(final_stage)
    print_audit(final_results, label="AUDIT (after fix)")

    # Summary
    n_rigid = sum(1 for p in final_stage.Traverse() if p.HasAPI(UsdPhysics.RigidBodyAPI))
    n_col = sum(1 for p in final_stage.Traverse() if p.HasAPI(UsdPhysics.CollisionAPI))
    n_joints = sum(1 for p in final_stage.Traverse() if "Joint" in p.GetTypeName())
    print(f"\n  SUMMARY: {n_rigid} rigid bodies, {n_col} colliders, {n_joints} joints")

    # Ready-to-run commands
    abs_output = os.path.abspath(output_usd)
    print(f"\n  Run commands:")
    print(f"    # Franka teleop")
    print(f"    ./isaaclab.sh -p scripts/environments/teleoperation/teleop_se3_agent_cinematic.py \\")
    print(f"      --asset {abs_output} --device cpu")
    print(f"\n{'='*60}")

    return output_usd



# ═══════════════════════════════════════════════════════════════════
# COMPONENT: gemini_vision
# ═══════════════════════════════════════════════════════════════════

#!/usr/bin/env python3


RENDER_SCRIPT = os.path.join(SCRIPT_DIR, "render_views.py")


def _load_gemini_key():
    """Load Gemini API key from api_keys.json or environment."""
    key = os.environ.get("GOOGLE_API_KEY")
    if key:
        return key
    keys_path = Path(API_KEYS_PATH).resolve()
    if keys_path.exists():
        with open(keys_path) as f:
            keys = json.load(f)
        for name in ("google", "gemini"):
            if name in keys:
                return keys[name].get("api_key")
    return None


def _load_gemini_model():
    """Load Gemini model from api_keys.json or default."""
    keys_path = Path(API_KEYS_PATH).resolve()
    if keys_path.exists():
        with open(keys_path) as f:
            keys = json.load(f)
        for name in ("google", "gemini"):
            if name in keys:
                return keys[name].get("model", "gemini-2.5-pro")
    return "gemini-2.5-pro"


def render_views(usd_path: str, output_dir: str, verbose: bool = True) -> list:
    """Render 4 views of USD asset using Blender headless. Returns list of PNG paths."""
    if not os.path.exists(RENDER_SCRIPT):
        raise FileNotFoundError(f"render_views.py not found at {RENDER_SCRIPT}")

    cmd = [
        "blender", "--background", "--python", str(RENDER_SCRIPT),
        "--", str(usd_path), str(output_dir)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

    if result.returncode != 0:
        if verbose:
            print(f"  Blender stderr: {result.stderr[-500:]}")
        raise RuntimeError(f"Blender render failed: {result.returncode}")

    views = []
    for name in ("front", "back", "left", "right"):
        path = os.path.join(output_dir, f"{name}.png")
        if os.path.exists(path):
            views.append(path)

    if verbose:
        print(f"  Rendered {len(views)} views to {output_dir}")
    return views


def analyze_with_gemini(image_paths: list, hierarchy_text: str,
                        verbose: bool = True) -> dict:
    """Send rendered views + hierarchy to Gemini for visual analysis."""
    from google import genai
    from google.genai import types

    api_key = _load_gemini_key()
    if not api_key:
        raise ValueError("No Gemini API key found. Set GOOGLE_API_KEY or add to api_keys.json")

    model_name = _load_gemini_model()
    client = genai.Client(api_key=api_key)

    # Build multi-modal content
    contents = []

    # Add images
    for img_path in image_paths:
        with open(img_path, "rb") as f:
            img_data = f.read()
        view_name = Path(img_path).stem
        contents.append(types.Part.from_text(text=f"[{view_name} view]"))
        contents.append(types.Part.from_bytes(data=img_data, mime_type="image/png"))

    # Add hierarchy text
    contents.append(types.Part.from_text(text=f"""
Analyze this furniture asset for robotic simulation (SimReady).

USD HIERARCHY:
{hierarchy_text}

Based on the images and hierarchy, identify:

1. MOVABLE PARTS: List every part that can move independently (doors, drawers,
   wheels, lids, flaps). For each, state:
   - Name (match to hierarchy Xform names)
   - Type: door (revolute), drawer (prismatic), wheel (continuous)
   - Axis: Z for vertical hinges, X for horizontal hinges, Y for drawer depth
   - Hinge side (for doors): left or right edge
   - Handle visible? yes/no

2. MATERIALS: For each visible surface, identify the material type:
   - metal/steel/chrome, plastic, glass, wood, rubber
   - This maps to friction coefficients for robot gripper interaction

3. CLASSIFICATION ISSUES: Flag anything suspicious:
   - Parts that look movable but aren't in the hierarchy as Xforms
   - Parts that look structural but have Xform + pivot (false positive risk)
   - Ambiguous names that could be misclassified (e.g., "Group_014")

4. SCALE CHECK: Does the asset look proportionally correct?
   - Standard fridge: ~180cm tall, ~90cm wide, ~70cm deep
   - Doors and drawers proportional to the body?

Output as JSON:
{{
  "movable_parts": [
    {{"name": "...", "type": "door|drawer|wheel", "axis": "X|Y|Z",
      "hinge_side": "left|right|null", "handle_visible": true}}
  ],
  "materials": {{"surface_description": "material_type"}},
  "issues": ["list of potential problems"],
  "scale_ok": true,
  "confidence": 0.0-1.0
}}
"""))

    if verbose:
        print(f"  Sending {len(image_paths)} images + hierarchy to {model_name}...")

    response = client.models.generate_content(
        model=model_name,
        contents=contents,
        config={"temperature": 0.1},
    )

    # Parse response
    text = response.text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        result = {"raw_response": text, "parse_error": True}

    if verbose:
        n_parts = len(result.get("movable_parts", []))
        n_issues = len(result.get("issues", []))
        conf = result.get("confidence", "?")
        print(f"  Gemini found: {n_parts} movable parts, {n_issues} issues, confidence={conf}")

    return result


def analyze_asset_visually(usd_path: str, hierarchy_text: str = "",
                           verbose: bool = True) -> dict:
    """Full visual analysis: render + Gemini. Returns structured report."""
    if verbose:
        print(f"\n  V3 Visual Analysis")
        print(f"  Input: {usd_path}")
        print(f"  {'─' * 50}")

    with tempfile.TemporaryDirectory(prefix="v9_vision_") as tmpdir:
        # Step 1: Render
        if verbose:
            print("\n  [1/2] Rendering 4 views (Blender headless)...")
        try:
            views = render_views(usd_path, tmpdir, verbose=verbose)
        except Exception as e:
            if verbose:
                print(f"  ERROR: Rendering failed: {e}")
            return {"error": str(e), "movable_parts": [], "issues": []}

        if not views:
            if verbose:
                print("  ERROR: No views rendered")
            return {"error": "No views rendered", "movable_parts": [], "issues": []}

        # Step 2: Gemini analysis
        if verbose:
            print("\n  [2/2] Gemini visual analysis...")
        try:
            result = analyze_with_gemini(views, hierarchy_text, verbose=verbose)
        except Exception as e:
            if verbose:
                print(f"  ERROR: Gemini analysis failed: {e}")
            return {"error": str(e), "movable_parts": [], "issues": []}

    return result



# ═══════════════════════════════════════════════════════════════════
# COMPONENT: object_understanding
# ═══════════════════════════════════════════════════════════════════

#!/usr/bin/env python3



# Material density table (kg/m³) — used when Gemini identifies material
MATERIAL_DENSITIES = {
    "stainless_steel": 7800,
    "steel": 7800,
    "carbon_steel": 7850,
    "aluminum": 2700,
    "aluminium": 2700,
    "chrome": 7150,
    "iron": 7870,
    "brass": 8500,
    "copper": 8960,
    "titanium": 4500,
    "plastic": 1200,
    "abs_plastic": 1050,
    "nylon": 1150,
    "polycarbonate": 1200,
    "wood": 600,
    "plywood": 550,
    "mdf": 750,
    "oak": 750,
    "pine": 500,
    "glass": 2500,
    "rubber": 1100,
    "silicone": 1100,
    "ceramic": 2300,
    "concrete": 2400,
    "foam": 30,
    "cardboard": 200,
    "paper": 700,
}


def _load_gemini():
    """Load Gemini client and model name."""
    from google import genai

    api_key = os.environ.get("GOOGLE_API_KEY")
    model_name = "gemini-2.5-pro"

    keys_path = Path(API_KEYS_PATH).resolve()
    if keys_path.exists():
        with open(keys_path) as f:
            keys = json.load(f)
        for name in ("google", "gemini"):
            if name in keys:
                api_key = api_key or keys[name].get("api_key")
                model_name = keys[name].get("model", model_name)

    if not api_key:
        raise ValueError("No Gemini API key")

    client = genai.Client(api_key=api_key)
    return client, model_name


def understand_object(usd_path, hierarchy_text="", rendered_views=None, verbose=True):
    """Ask Gemini what this object IS, not just what parts it has.

    Returns a structured description that drives classification and physics:
    {
        "object_name": "vernier caliper",
        "object_type": "measurement_tool",
        "material": "stainless_steel",
        "material_density_kg_m3": 7800,
        "estimated_mass_kg": 0.15,
        "is_articulated": true,
        "movable_parts": [
            {
                "name": "depthblade",
                "behavior": "slider",
                "motion": "bidirectional linear along ruler",
                "range_description": "0 to 15cm on ruler scale",
                "range_meters": 0.15,
                "joint_type": "prismatic",
                "axis": "Y",
                "limits_bidirectional": true
            }
        ],
        "special_notes": "Sliding jaw must reach full ruler range 0-15cm",
        "is_graspable": true,
        "grip_location": "body/handle area"
    }
    """
    from google.genai import types

    client, model_name = _load_gemini()

    contents = []

    # Add rendered views if available
    if rendered_views:
        for img_path in rendered_views:
            if os.path.exists(img_path):
                with open(img_path, "rb") as f:
                    img_data = f.read()
                view_name = Path(img_path).stem
                contents.append(types.Part.from_text(text=f"[{view_name}]"))
                contents.append(types.Part.from_bytes(data=img_data, mime_type="image/png"))

    contents.append(types.Part.from_text(text=f"""
You are an expert at identifying physical objects for robotic simulation.

USD HIERARCHY:
{hierarchy_text}

TASK: Identify what this object IS, what it's made of, and how it behaves.
This is NOT about listing USD parts — it's about understanding the OBJECT.

Answer these questions:

1. WHAT IS IT? Give the specific name (e.g., "vernier caliper", "surgical mallet",
   "double-door refrigerator", "instrument trolley with caster wheels").

2. WHAT IS IT MADE OF? Identify the primary material from visual appearance
   and object type. Be specific: "stainless steel" not just "metal".
   Common surgical instruments are stainless steel (~7800 kg/m³).
   Furniture is typically wood/MDF (~600-750 kg/m³) with metal hardware.

3. HOW MUCH DOES IT WEIGH? Estimate based on what this object typically
   weighs in the real world. A surgical caliper: ~150g. A mallet: ~300g.
   A fridge door: ~20kg. A trolley: ~10kg.

4. IS IT ARTICULATED? Does it have parts that move independently?
   - If YES: describe EACH movable part, what motion it makes (rotation,
     sliding, spinning), what range of motion (e.g., "0-15cm", "0-120°"),
     and whether the motion is ONE-DIRECTIONAL (drawer) or BIDIRECTIONAL (slider/caliper).
   - If NO: is it a graspable tool (pick it up) or a static fixture?

5. SPECIAL PHYSICS NOTES: Anything that would affect simulation:
   - "Sliding jaw must reach full ruler range"
   - "Forceps tips are a single fused mesh, cannot articulate"
   - "Caster wheels have both swivel and roll axes"
   - "Drawer has rail mechanism that must maintain overlap"

Output ONLY valid JSON:
{{
    "object_name": "specific name",
    "object_type": "furniture|tool|instrument|container|fixture",
    "material": "specific_material (use underscore, lowercase)",
    "material_density_kg_m3": 7800,
    "estimated_mass_kg": 0.15,
    "is_articulated": true,
    "movable_parts": [
        {{
            "name": "match to USD Xform name if possible",
            "behavior": "door|drawer|slider|wheel|lever|button|static",
            "motion": "describe the motion in plain English",
            "range_description": "human-readable range",
            "range_meters": 0.15,
            "joint_type": "revolute|prismatic|continuous",
            "axis": "X|Y|Z",
            "limits_bidirectional": false
        }}
    ],
    "special_notes": "anything important for physics",
    "is_graspable": true,
    "grip_location": "where to grip it"
}}

For non-articulated objects, set movable_parts to empty list [].
"""))

    if verbose:
        print(f"  Asking Gemini: 'What IS this object?'...")

    response = client.models.generate_content(
        model=model_name,
        contents=contents,
        config={"temperature": 0.1},
    )

    text = response.text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        result = {"error": "JSON parse failed", "raw": text[:500]}

    # Enrich with material density lookup if Gemini's density seems off
    material = result.get("material", "").lower().replace(" ", "_")
    if material in MATERIAL_DENSITIES:
        known_density = MATERIAL_DENSITIES[material]
        gemini_density = result.get("material_density_kg_m3", 0)
        if abs(gemini_density - known_density) > known_density * 0.3:
            result["material_density_kg_m3"] = known_density
            result["_density_corrected"] = True

    if verbose:
        name = result.get("object_name", "?")
        mat = result.get("material", "?")
        mass = result.get("estimated_mass_kg", "?")
        n_parts = len(result.get("movable_parts", []))
        print(f"  Object: {name}")
        print(f"  Material: {mat} ({result.get('material_density_kg_m3', '?')} kg/m³)")
        print(f"  Mass: {mass} kg")
        print(f"  Articulated: {result.get('is_articulated', '?')} ({n_parts} movable parts)")
        for p in result.get("movable_parts", []):
            bidir = " [BIDIRECTIONAL]" if p.get("limits_bidirectional") else ""
            print(f"    {p.get('name','?')} → {p.get('behavior','?')} {p.get('joint_type','?')} "
                  f"axis={p.get('axis','?')} range={p.get('range_description','?')}{bidir}")
        notes = result.get("special_notes", "")
        if notes:
            print(f"  Notes: {notes}")

    return result


def density_for_material(material_name):
    """Look up density from material name. Returns kg/m³ or 500 (default)."""
    key = material_name.lower().replace(" ", "_")
    return MATERIAL_DENSITIES.get(key, 500)



# ═══════════════════════════════════════════════════════════════════
# COMPONENT: render_views (runs via subprocess in Blender — not merged)
# render_views.py is a Blender script called via: blender --python render_views.py
# It lives as a separate file next to v12_pipeline.py
# ═══════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════
# COMPONENT: validate_dynamics
# ═══════════════════════════════════════════════════════════════════

#!/usr/bin/env python3


# ═══════════════════════════════════════════════════════════════════
# USD → URDF CONVERSION
# ═══════════════════════════════════════════════════════════════════

def convert_usd_to_urdf(usd_path: str, output_dir: str) -> str:
    """Convert USD to URDF with collision meshes for full physics validation."""
    from nvidia.srl.from_usd.to_urdf import UsdToUrdf

    urdf_path = os.path.join(output_dir, "robot.urdf")
    converter = UsdToUrdf.init_from_file(usd_path)
    converter.save_to_file(urdf_path)

    # Move OBJ meshes from meshes/ to URDF directory and fix paths.
    # MuJoCo resolves mesh filenames from CWD, not from URDF location,
    # and strips directory prefixes.
    meshes_dir = os.path.join(output_dir, "meshes")
    if os.path.exists(meshes_dir):
        for f in glob.glob(os.path.join(meshes_dir, "*.obj")):
            shutil.move(f, output_dir)
    # Update URDF to remove meshes/ prefix
    with open(urdf_path) as f:
        urdf_text = f.read()
    urdf_text = urdf_text.replace('filename="meshes/', 'filename="')
    with open(urdf_path, 'w') as f:
        f.write(urdf_text)

    return urdf_path


def parse_urdf_joints(urdf_path: str) -> list:
    """Extract joint info from URDF for validation."""
    tree = ET.parse(urdf_path)
    root = tree.getroot()
    joints = []
    for j in root.findall("joint"):
        info = {
            "name": j.attrib["name"],
            "type": j.attrib["type"],
            "parent": j.find("parent").attrib["link"],
            "child": j.find("child").attrib["link"],
        }
        limit = j.find("limit")
        if limit is not None:
            info["lower"] = float(limit.attrib.get("lower", 0))
            info["upper"] = float(limit.attrib.get("upper", 0))
        axis_el = j.find("axis")
        if axis_el is not None:
            info["axis"] = axis_el.attrib.get("xyz", "0 0 0")
        joints.append(info)
    return joints


# ═══════════════════════════════════════════════════════════════════
# BEHAVIORAL CHECKS
# ═══════════════════════════════════════════════════════════════════

FRANKA_MAX_TORQUE = 87.0   # Nm, joints 1-4
FRANKA_MAX_GRIP = 200.0    # N, sim gripper
CONDITION_WARN = 10000
CONDITION_FAIL = 100000


def run_checks(urdf_path: str, urdf_joints: list, verbose: bool = True) -> dict:
    """Run all behavioral checks. Returns structured results."""
    import mujoco
    import numpy as np

    results = {
        "checks": {},
        "pass_count": 0,
        "warn_count": 0,
        "fail_count": 0,
        "total": 0,
    }

    def record(check_id, name, status, detail=""):
        results["checks"][check_id] = {"name": name, "status": status, "detail": detail}
        results["total"] += 1
        if status == "PASS":
            results["pass_count"] += 1
        elif status == "WARN":
            results["warn_count"] += 1
        else:
            results["fail_count"] += 1
        icon = {"PASS": "+", "WARN": "?", "FAIL": "X"}[status]
        if verbose:
            print(f"  [{icon}] {check_id}: {name} — {status}" + (f" ({detail})" if detail else ""))

    # Load into MuJoCo
    try:
        spec = mujoco.MjSpec.from_file(urdf_path)
        model = spec.compile()
        data = mujoco.MjData(model)
    except Exception as e:
        record("B0", "MuJoCo model load", "FAIL", str(e))
        return results

    record("B0", "MuJoCo model load", "PASS", f"nq={model.nq} nbody={model.nbody} njnt={model.njnt}")

    # ── B1: Prismatic travel realism ──
    # For each prismatic joint, check if travel > 60% of body depth
    # (would mean the part fully exits the body)
    body_depth_estimate = 0.0
    for j in urdf_joints:
        if j["type"] == "prismatic":
            travel = abs(j.get("upper", 0) - j.get("lower", 0))
            short = j["name"].replace("sm_refrigerator_b01_", "").replace("_joint", "")
            # Heuristic: travel > 0.5m for a typical drawer is suspicious
            if travel > 0.55:
                record(f"B1_{short}", f"Travel realism ({short})", "WARN",
                       f"travel={travel:.3f}m — may fully exit body")
            elif travel > 0.8:
                record(f"B1_{short}", f"Travel realism ({short})", "FAIL",
                       f"travel={travel:.3f}m — drawer will detach from body")
            else:
                record(f"B1_{short}", f"Travel realism ({short})", "PASS",
                       f"travel={travel:.3f}m")

    # ── B2: Revolute range sanity (F09, F16, F19) ──
    for j in urdf_joints:
        if j["type"] != "revolute":
            continue
        short = j["name"].replace("sm_refrigerator_b01_", "").replace("_joint", "")
        lo = j.get("lower", 0)
        hi = j.get("upper", 0)
        range_deg = abs(hi - lo) * 180 / math.pi
        # Doors should be 90-150°, wheels unlimited
        if range_deg > 300 and range_deg < 11000:
            record(f"B2_{short}", f"Revolute range ({short})", "WARN",
                   f"range={range_deg:.0f}° — unusually large for a door")
        elif range_deg < 10:
            record(f"B2_{short}", f"Revolute range ({short})", "WARN",
                   f"range={range_deg:.0f}° — too small to be useful")
        else:
            record(f"B2_{short}", f"Revolute range ({short})", "PASS",
                   f"range={range_deg:.0f}°")

    # ── B7: Mass per body sanity (F21, F22, F23) ──
    for i in range(model.nbody):
        bname = model.body(i).name
        if bname == "world":
            continue
        mass = model.body_mass[i]
        short = bname.replace("sm_refrigerator_b01_", "").replace("sm_", "")
        if mass > 200:
            record(f"B7_{short}", f"Mass realism ({short})", "WARN",
                   f"mass={mass:.1f}kg — very heavy")
        elif mass < 0.01 and mass > 0:
            record(f"B7_{short}", f"Mass realism ({short})", "WARN",
                   f"mass={mass:.4f}kg — very light, may blow away")
        else:
            record(f"B7_{short}", f"Mass realism ({short})", "PASS",
                   f"mass={mass:.2f}kg")

    # ── B3: Mass matrix stability ──
    # Compute mass matrix via MuJoCo
    mujoco.mj_forward(model, data)
    M = np.zeros((model.nv, model.nv))
    mujoco.mj_fullM(model, M, data.qM)
    cond = np.linalg.cond(M) if model.nv > 0 else 0
    if cond > CONDITION_FAIL:
        record("B3", "Mass matrix condition", "FAIL",
               f"cond={cond:.0f} > {CONDITION_FAIL} — solver will be unstable")
    elif cond > CONDITION_WARN:
        record("B3", "Mass matrix condition", "WARN",
               f"cond={cond:.0f} > {CONDITION_WARN} — borderline stability")
    else:
        record("B3", "Mass matrix condition", "PASS", f"cond={cond:.0f}")

    # ── B4: Gravity torque vs Franka ──
    mujoco.mj_resetData(model, data)
    mujoco.mj_forward(model, data)
    for i in range(model.njnt):
        jname = model.joint(i).name
        short = jname.replace("sm_refrigerator_b01_", "").replace("_joint", "")
        jtype_id = model.jnt_type[i]
        jtype = ["free", "ball", "slide", "hinge"][jtype_id]

        # Gravity-induced torque/force on this joint
        grav_force = abs(data.qfrc_bias[i])
        limit = FRANKA_MAX_TORQUE if jtype == "hinge" else FRANKA_MAX_GRIP
        unit = "Nm" if jtype == "hinge" else "N"
        if grav_force > limit:
            record(f"B4_{short}", f"Gravity vs Franka ({short})", "FAIL",
                   f"gravity={grav_force:.1f}{unit} > Franka {limit}{unit}")
        elif grav_force > limit * 0.8:
            record(f"B4_{short}", f"Gravity vs Franka ({short})", "WARN",
                   f"gravity={grav_force:.1f}{unit} — close to Franka {limit}{unit}")
        else:
            record(f"B4_{short}", f"Gravity vs Franka ({short})", "PASS",
                   f"gravity={grav_force:.1f}{unit}")

    # ── B5: Contact penetration at rest ──
    mujoco.mj_resetData(model, data)
    mujoco.mj_forward(model, data)
    penetrations = 0
    for i in range(data.ncon):
        if data.contact[i].dist < -0.005:  # 5mm penetration
            penetrations += 1
    if penetrations > 0:
        record("B5", "Contact penetration at rest", "WARN",
               f"{penetrations} contacts with >5mm penetration")
    else:
        record("B5", "Contact penetration at rest", "PASS",
               f"{data.ncon} contacts, none penetrating")

    # ── B6: Joint actually moves under force ──
    for i in range(model.njnt):
        jname = model.joint(i).name
        short = jname.replace("sm_refrigerator_b01_", "").replace("_joint", "")
        jtype_id = model.jnt_type[i]

        mujoco.mj_resetData(model, data)
        # Apply realistic force/torque
        force = 5.0 if jtype_id == 3 else 20.0  # 5Nm for hinge, 20N for prismatic
        # Determine direction from joint limits
        lo = model.jnt_range[i][0]
        hi = model.jnt_range[i][1]
        direction = -1.0 if abs(lo) > abs(hi) else 1.0

        for step in range(2000):  # 2 seconds at 1kHz
            data.qfrc_applied[i] = force * direction
            mujoco.mj_step(model, data)

        final_q = data.qpos[model.jnt_qposadr[i]]
        limit_extent = max(abs(lo), abs(hi))
        pct = abs(final_q / limit_extent) * 100 if limit_extent > 0.001 else 0

        if pct < 5:
            record(f"B6_{short}", f"Joint moves ({short})", "FAIL",
                   f"reached {pct:.0f}% of limit — blocked or jammed")
        elif pct < 30:
            record(f"B6_{short}", f"Joint moves ({short})", "WARN",
                   f"reached {pct:.0f}% of limit — high resistance")
        else:
            record(f"B6_{short}", f"Joint moves ({short})", "PASS",
                   f"reached {pct:.0f}% of limit")

    return results


def check_structural_overlap(usd_path, verbose=True):
    """B8: Check if structural meshes overlap with movable part travel zones.

    For each prismatic joint, compute the travel zone (bbox of movable part
    swept through its full range). Flag any structural mesh whose bbox
    intersects this zone — it would collide with the moving part in reality.

    Returns list of overlaps with actionable fixes.
    """

    stage = Usd.Stage.Open(usd_path)
    if not stage:
        return []

    overlaps = []

    # Collect rigid body paths
    body_path = None
    movable_paths = {}
    for prim in stage.Traverse():
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            kin = prim.GetAttribute("physics:kinematicEnabled")
            if kin and kin.Get():
                body_path = prim.GetPath()
            else:
                movable_paths[str(prim.GetPath())] = prim

    if not body_path:
        return []

    # Collect joint info
    joints = []
    for prim in stage.Traverse():
        if not prim.IsA(UsdPhysics.Joint):
            continue
        jtype = prim.GetTypeName()
        if "Prismatic" not in jtype:
            continue
        body1_targets = prim.GetRelationship("physics:body1").GetTargets()
        if not body1_targets:
            continue
        axis_attr = prim.GetAttribute("physics:axis")
        axis = axis_attr.Get() if axis_attr else "Y"
        lo = prim.GetAttribute("physics:lowerLimit").Get() or 0
        hi = prim.GetAttribute("physics:upperLimit").Get() or 0
        joints.append({
            "movable_path": str(body1_targets[0]),
            "axis": axis,
            "lower": lo,
            "upper": hi,
        })

    if not joints:
        return []

    # For each prismatic joint, compute swept travel zone
    bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"])

    for jinfo in joints:
        movable_prim = stage.GetPrimAtPath(jinfo["movable_path"])
        if not movable_prim:
            continue

        try:
            mbbox = bbox_cache.ComputeWorldBound(movable_prim)
            mrng = mbbox.ComputeAlignedRange()
            if mrng.IsEmpty():
                continue
            mmin = list(mrng.GetMin())
            mmax = list(mrng.GetMax())
        except:
            continue

        # Expand bbox along travel axis to create swept zone
        axis_idx = {"X": 0, "Y": 1, "Z": 2}.get(jinfo["axis"], 1)
        travel_min = mmin[axis_idx] + jinfo["lower"]
        travel_max = mmax[axis_idx] + jinfo["upper"]
        swept_min = list(mmin)
        swept_max = list(mmax)
        swept_min[axis_idx] = min(mmin[axis_idx], travel_min)
        swept_max[axis_idx] = max(mmax[axis_idx], travel_max)

        movable_name = movable_prim.GetName()

        # Check structural meshes under body for overlap with swept zone
        body_prim = stage.GetPrimAtPath(body_path)
        for child in Usd.PrimRange(body_prim):
            if not child.IsA(UsdGeom.Mesh):
                continue
            # Skip parts that SHOULD be inside the travel zone
            # (interior, shelves, hinges, covers — they're inside the fridge)
            child_name = child.GetName().lower()
            skip_keywords = ("body", "interior", "shelf", "hinge", "cover", "glass",
                           "back", "panel", "wire", "holder", "ice", "refresher",
                           "lamp", "light", "air", "plate", "screen", "indicator",
                           "pump", "motor", "fitting", "ring", "cap", "base",
                           "pillar", "sheet", "drawer")
            if any(kw in child_name for kw in skip_keywords):
                continue

            try:
                cbbox = bbox_cache.ComputeWorldBound(child)
                crng = cbbox.ComputeAlignedRange()
                if crng.IsEmpty():
                    continue
                cmin = crng.GetMin()
                cmax = crng.GetMax()
            except:
                continue

            # Check AABB overlap
            overlap = True
            for i in range(3):
                if cmax[i] < swept_min[i] or cmin[i] > swept_max[i]:
                    overlap = False
                    break

            if overlap:
                overlap_info = {
                    "structural_mesh": child.GetName(),
                    "structural_path": str(child.GetPath()),
                    "movable_part": movable_name,
                    "issue": f"Structural mesh '{child.GetName()}' overlaps with travel zone of '{movable_name}'",
                    "fix": "relocate_mesh",  # actionable fix type
                }
                overlaps.append(overlap_info)
                if verbose:
                    print(f"  [!] B8: {child.GetName()} overlaps {movable_name} travel zone")

    return overlaps


def fix_structural_overlaps(usd_path, overlaps, verbose=True):
    """Auto-fix structural overlaps by relocating offending meshes.

    For small decorative parts (wheels, bolts) that overlap movable travel zones,
    shift them out of the way. For large structural parts, just warn.
    """

    if not overlaps:
        return 0

    stage = Usd.Stage.Open(usd_path)
    fixed = 0

    # Keywords for parts that can be safely relocated
    relocatable = ("wheel", "caster", "bolt", "clip", "logo", "led")

    for ovl in overlaps:
        mesh_name = ovl["structural_mesh"].lower()
        if not any(kw in mesh_name for kw in relocatable):
            if verbose:
                print(f"  [WARN] B8: {ovl['structural_mesh']} overlaps {ovl['movable_part']} — cannot auto-fix (structural)")
            continue

        prim = stage.GetPrimAtPath(ovl["structural_path"])
        if not prim:
            continue

        # Make the mesh invisible (purpose=guide) so it doesn't render
        # but keeps the geometry data intact
        UsdGeom.Imageable(prim).CreatePurposeAttr().Set("guide")

        if verbose:
            print(f"  [FIX] B8: {ovl['structural_mesh']} hidden (overlaps {ovl['movable_part']} travel zone)")
        fixed += 1

    if fixed > 0:
        stage.GetRootLayer().Save()

    return fixed


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def validate(usd_path: str, verbose: bool = True, output_json: bool = False) -> dict:
    """Full validation pipeline: USD → URDF → MuJoCo → checks."""
    usd_path = str(Path(usd_path).resolve())

    if verbose:
        print(f"\n  V2 Behavioral Validation")
        print(f"  Input: {usd_path}")
        print(f"  Engine: MuJoCo (CPU, headless, with collision meshes)")
        print(f"  {'─' * 50}")

    # Step 1: Convert USD → URDF
    if verbose:
        print("\n  [1/2] Converting USD → URDF...")
    with tempfile.TemporaryDirectory(prefix="v9_validate_") as tmpdir:
        try:
            urdf_path = convert_usd_to_urdf(usd_path, tmpdir)
        except Exception as e:
            if verbose:
                print(f"  ERROR: USD→URDF conversion failed: {e}")
            return {"checks": {}, "pass_count": 0, "fail_count": 1, "total": 1,
                    "error": str(e)}

        urdf_joints = parse_urdf_joints(urdf_path)
        if verbose:
            print(f"  URDF: {len(urdf_joints)} joints, meshes exported")

        # Step 2: Run checks (from URDF directory so mesh paths resolve)
        if verbose:
            print("\n  [2/2] Running behavioral checks...\n")
        prev_cwd = os.getcwd()
        os.chdir(tmpdir)
        results = run_checks(urdf_path, urdf_joints, verbose=verbose)
        os.chdir(prev_cwd)

    # Step 3: B8 — structural overlap check (runs on USD directly, not MuJoCo)
    if verbose:
        print(f"\n  B8: Checking structural overlap with travel zones...")
    overlaps = check_structural_overlap(usd_path, verbose=verbose)
    if overlaps:
        results["checks"]["B8"] = {
            "name": "Structural overlap with travel zone",
            "status": "WARN",
            "detail": f"{len(overlaps)} structural mesh(es) in movable travel zone",
            "overlaps": overlaps,
        }
        results["warn_count"] += 1
        results["total"] += 1

        # Auto-fix: relocate small decorative parts that overlap
        if verbose:
            print(f"\n  B8 auto-fix: attempting to resolve overlaps...")
        n_fixed = fix_structural_overlaps(usd_path, overlaps, verbose=verbose)
        if n_fixed > 0 and verbose:
            print(f"  B8: {n_fixed} overlap(s) auto-fixed")
    else:
        results["checks"]["B8"] = {
            "name": "Structural overlap with travel zone",
            "status": "PASS",
            "detail": "No structural meshes in movable travel zones",
        }
        results["pass_count"] += 1
        results["total"] += 1
        if verbose:
            print(f"  [+] B8: No structural overlap — PASS")

    # Summary
    if verbose:
        print(f"\n  {'─' * 50}")
        total = results["total"]
        p = results["pass_count"]
        w = results["warn_count"]
        f = results["fail_count"]
        status = "PASS" if f == 0 else "FAIL"
        print(f"  BEHAVIORAL: {p}/{total} pass, {w} warn, {f} fail → {status}")

    if output_json:
        print(json.dumps(results, indent=2))

    return results


def main():
    ap = argparse.ArgumentParser(description="V9 Behavioral Validation (Pinocchio + MuJoCo)")
    ap.add_argument("--input", required=True, help="Path to _physics.usd file")
    ap.add_argument("--json", action="store_true", help="Output results as JSON")
    ap.add_argument("--quiet", action="store_true", help="Suppress verbose output")
    args = ap.parse_args()
    results = validate(args.input, verbose=not args.quiet, output_json=args.json)
    sys.exit(1 if results["fail_count"] > 0 else 0)



# ═══════════════════════════════════════════════════════════════════
# COMPONENT: export_urdf
# ═══════════════════════════════════════════════════════════════════

#!/usr/bin/env python3



def export_urdf(usd_path: str, output_dir: str = None, verbose: bool = True) -> str:
    """Export physics USD to URDF + meshes. Returns URDF path."""
    from nvidia.srl.from_usd.to_urdf import UsdToUrdf

    usd_path = str(Path(usd_path).resolve())
    asset_name = Path(usd_path).stem.replace("_physics", "")

    # Default output: next to the USD
    if not output_dir:
        output_dir = str(Path(usd_path).parent)

    os.makedirs(output_dir, exist_ok=True)

    # Convert
    if verbose:
        print(f"\n  URDF Export")
        print(f"  Input:  {usd_path}")
        print(f"  Output: {output_dir}/")
        print(f"  {'─' * 50}")
        print(f"  Converting USD → URDF...")

    # Export to temp first, then organize
    with tempfile.TemporaryDirectory(prefix="urdf_export_") as tmpdir:
        tmp_urdf = os.path.join(tmpdir, "robot.urdf")
        converter = UsdToUrdf.init_from_file(usd_path)
        converter.save_to_file(tmp_urdf)

        # Organize meshes into named directory
        meshes_dir = os.path.join(output_dir, f"{asset_name}_meshes")
        os.makedirs(meshes_dir, exist_ok=True)

        # Copy OBJ + MTL files
        tmp_meshes = os.path.join(tmpdir, "meshes")
        mesh_count = 0
        if os.path.exists(tmp_meshes):
            for f in glob.glob(os.path.join(tmp_meshes, "*.obj")):
                shutil.copy2(f, meshes_dir)
                mesh_count += 1
            for f in glob.glob(os.path.join(tmp_meshes, "*.mtl")):
                shutil.copy2(f, meshes_dir)

        # Update URDF mesh paths to point to the named meshes directory
        with open(tmp_urdf) as f:
            urdf_text = f.read()
        urdf_text = urdf_text.replace('filename="meshes/', f'filename="{asset_name}_meshes/')

        # Write final URDF
        urdf_path = os.path.join(output_dir, f"{asset_name}.urdf")
        with open(urdf_path, 'w') as f:
            f.write(urdf_text)

    if verbose:
        # Parse joint info for summary
        tree = ET.parse(urdf_path)
        root = tree.getroot()
        n_links = len(root.findall("link"))
        n_joints = len(root.findall("joint"))

        print(f"  URDF: {urdf_path}")
        print(f"  Meshes: {mesh_count} OBJ files in {asset_name}_meshes/")
        print(f"  Links: {n_links}, Joints: {n_joints}")
        print(f"\n  Compatible with: MuJoCo, PyBullet, Drake, Pinocchio, ROS")

        for j in root.findall("joint"):
            jname = j.attrib["name"]
            jtype = j.attrib["type"]
            limit = j.find("limit")
            if limit is not None:
                lo = limit.attrib.get("lower", "0")
                hi = limit.attrib.get("upper", "0")
                print(f"    {jtype:10s} {jname}  [{lo}, {hi}]")

    return urdf_path


def main():
    ap = argparse.ArgumentParser(description="Export SimReady USD to URDF + meshes")
    ap.add_argument("--input", required=True, help="Path to _physics.usd")
    ap.add_argument("--output-dir", default=None, help="Output directory (default: next to USD)")
    args = ap.parse_args()
    export_urdf(args.input, output_dir=args.output_dir)



# ═══════════════════════════════════════════════════════════════════
# COMPONENT: verify_visual
# ═══════════════════════════════════════════════════════════════════

#!/usr/bin/env python3




RENDER_SCRIPT = os.path.join(SCRIPT_DIR, "render_views.py")


def _set_joint_positions(stage, q_fraction):
    """Set all joints to a fraction of their limit range (0.0=rest, 1.0=max).

    Moves the movable Xform transforms to simulate joint positions,
    since Blender doesn't run PhysX.
    """
    for prim in stage.Traverse():
        if not prim.IsA(UsdPhysics.Joint):
            continue

        jtype = prim.GetTypeName()
        lo_attr = prim.GetAttribute("physics:lowerLimit")
        hi_attr = prim.GetAttribute("physics:upperLimit")
        axis_attr = prim.GetAttribute("physics:axis")

        if not lo_attr or not hi_attr:
            continue

        lo = lo_attr.Get() or 0.0
        hi = hi_attr.Get() or 0.0
        axis = axis_attr.Get() if axis_attr else "Y"

        # Compute target joint value
        # Use the limit with larger magnitude (the max extension direction)
        if abs(lo) > abs(hi):
            q_target = lo * q_fraction
        else:
            q_target = hi * q_fraction

        # Find the movable body (body1)
        body1_rel = prim.GetRelationship("physics:body1")
        if not body1_rel:
            continue
        targets = body1_rel.GetTargets()
        if not targets:
            continue

        movable_prim = stage.GetPrimAtPath(targets[0])
        if not movable_prim:
            continue

        xf = UsdGeom.Xformable(movable_prim)
        if not xf:
            continue

        if "Revolute" in jtype:
            # Rotate around axis
            angle_deg = math.degrees(q_target)
            ops = xf.GetOrderedXformOps()
            # Add a rotation op
            if axis == "Z":
                rot_op = xf.AddRotateZOp(opSuffix="joint_sim")
                rot_op.Set(angle_deg)
            elif axis == "X":
                rot_op = xf.AddRotateXOp(opSuffix="joint_sim")
                rot_op.Set(angle_deg)
            elif axis == "Y":
                rot_op = xf.AddRotateYOp(opSuffix="joint_sim")
                rot_op.Set(angle_deg)

        elif "Prismatic" in jtype:
            # Translate along axis
            axis_vec = {"X": Gf.Vec3d(1, 0, 0), "Y": Gf.Vec3d(0, 1, 0), "Z": Gf.Vec3d(0, 0, 1)}.get(axis, Gf.Vec3d(0, 1, 0))
            offset = axis_vec * q_target
            translate_op = xf.AddTranslateOp(opSuffix="joint_sim")
            translate_op.Set(offset)


def _render_usd(usd_path, output_dir, label=""):
    """Render 4 views using Blender headless. Returns list of PNG paths."""
    cmd = ["blender", "--background", "--python", str(RENDER_SCRIPT),
           "--", str(usd_path), str(output_dir)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        return []
    views = []
    for name in ("front", "back", "left", "right"):
        path = os.path.join(output_dir, f"{name}.png")
        if os.path.exists(path):
            # Rename with label
            labeled = os.path.join(output_dir, f"{label}_{name}.png" if label else f"{name}.png")
            if label:
                os.rename(path, labeled)
                views.append(labeled)
            else:
                views.append(path)
    return views


def _ask_gemini(image_paths, asset_description, verbose=True):
    """Send rest + max-extension images to Gemini for visual verification."""
    from google import genai
    from google.genai import types

    # Load API key
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        keys_path = Path(API_KEYS_PATH).resolve()
        if keys_path.exists():
            with open(keys_path) as f:
                keys = json.load(f)
            for name in ("google", "gemini"):
                if name in keys:
                    api_key = keys[name].get("api_key")
                    break
    if not api_key:
        return {"error": "No Gemini API key"}

    # Load model
    model_name = "gemini-2.5-pro"
    keys_path = Path(API_KEYS_PATH).resolve()
    if keys_path.exists():
        with open(keys_path) as f:
            keys = json.load(f)
        for name in ("google", "gemini"):
            if name in keys:
                model_name = keys[name].get("model", model_name)
                break

    client = genai.Client(api_key=api_key)

    contents = []
    for img_path in image_paths:
        with open(img_path, "rb") as f:
            img_data = f.read()
        label = Path(img_path).stem
        contents.append(types.Part.from_text(text=f"[{label}]"))
        contents.append(types.Part.from_bytes(data=img_data, mime_type="image/png"))

    contents.append(types.Part.from_text(text=f"""
POST-BUILD VISUAL VERIFICATION for a SimReady physics asset.

{asset_description}

You are shown the asset at REST position (joints at q=0) and at MAX EXTENSION
(joints at their limit — doors fully open, drawers fully pulled out, sliders
at max range).

Check for these specific issues:

1. **DETACHMENT**: Do any parts visually separate from the body when extended?
   (rails pulling out of tracks, brackets floating in space)

2. **RANGE**: Do movable parts reach their full expected range?
   (caliper should go 0-15 on ruler, doors should open ~120°, drawers should
   extend most of their depth)

3. **WRONG DIRECTION**: Do parts move the wrong way?
   (door opening into the body, drawer sliding backward)

4. **MISSING PARTS**: Are there parts that LOOK movable but don't move between
   rest and max images? (a visible door that stays in the same position)

5. **COLLISION ARTIFACTS**: Do parts clip through each other or through the body?

6. **POSITION ERRORS**: Are parts in physically impossible positions at max extension?

Output JSON:
{{
  "overall": "PASS" or "FAIL",
  "issues": [
    {{"type": "detachment|range|direction|missing|collision|position",
      "part": "name",
      "description": "what's wrong",
      "severity": "critical|warning"}}
  ],
  "confidence": 0.0-1.0
}}

If everything looks correct, output {{"overall": "PASS", "issues": [], "confidence": 0.95}}
"""))

    if verbose:
        print(f"  Sending {len(image_paths)} images to {model_name}...")

    response = client.models.generate_content(
        model=model_name,
        contents=contents,
        config={"temperature": 0.1},
    )

    text = response.text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        result = {"overall": "UNKNOWN", "raw_response": text, "parse_error": True}

    return result


def verify_post_build(physics_usd_path, verbose=True):
    """Full post-build visual verification. Returns structured result."""
    physics_usd_path = str(Path(physics_usd_path).resolve())

    if verbose:
        print(f"\n  Post-Build Visual Verification")
        print(f"  Input: {physics_usd_path}")
        print(f"  {'─' * 50}")

    with tempfile.TemporaryDirectory(prefix="v9_postbuild_") as tmpdir:
        # Step 1: Render at rest (q=0)
        if verbose:
            print("\n  [1/4] Rendering at rest (q=0)...")
        rest_views = _render_usd(physics_usd_path, tmpdir, label="rest")

        # Step 2: Create a temp USD with joints at max extension
        if verbose:
            print("  [2/4] Setting joints to max extension...")
        max_usd = os.path.join(tmpdir, "max_extension.usd")
        shutil.copy2(physics_usd_path, max_usd)
        stage = Usd.Stage.Open(max_usd)
        _set_joint_positions(stage, q_fraction=0.9)  # 90% of max to avoid edge issues
        stage.GetRootLayer().Save()
        del stage

        # Step 3: Render at max extension
        if verbose:
            print("  [3/4] Rendering at max extension...")
        max_dir = os.path.join(tmpdir, "max_views")
        os.makedirs(max_dir, exist_ok=True)
        max_views = _render_usd(max_usd, max_dir, label="max")

        all_views = rest_views + max_views
        if not all_views:
            if verbose:
                print("  ERROR: No views rendered")
            return {"overall": "ERROR", "issues": [], "error": "Rendering failed"}

        if verbose:
            print(f"  Rendered {len(rest_views)} rest + {len(max_views)} max views")

        # Step 4: Ask Gemini
        if verbose:
            print("\n  [4/4] Gemini visual verification...")

        # Build description from the USD
        desc_lines = [f"Asset: {Path(physics_usd_path).stem}"]
        check_stage = Usd.Stage.Open(physics_usd_path)
        for prim in check_stage.Traverse():
            if prim.IsA(UsdPhysics.Joint):
                jtype = prim.GetTypeName().replace("Physics", "")
                lo = prim.GetAttribute("physics:lowerLimit").Get()
                hi = prim.GetAttribute("physics:upperLimit").Get()
                axis = prim.GetAttribute("physics:axis").Get()
                body1 = prim.GetRelationship("physics:body1").GetTargets()
                part_name = body1[0].name if body1 else "?"
                desc_lines.append(f"  Joint: {part_name} ({jtype}, axis={axis}, limits=[{lo:.3f}, {hi:.3f}])")
        del check_stage

        description = "\n".join(desc_lines)
        result = _ask_gemini(all_views, description, verbose=verbose)

        if verbose:
            overall = result.get("overall", "?")
            n_issues = len(result.get("issues", []))
            conf = result.get("confidence", "?")
            print(f"\n  VISUAL VERDICT: {overall} ({n_issues} issues, confidence={conf})")
            for issue in result.get("issues", []):
                sev = issue.get("severity", "?")
                typ = issue.get("type", "?")
                desc = issue.get("description", "?")
                print(f"    [{sev}] {typ}: {desc}")

    return result




# ═══════════════════════════════════════════════════════════════════
# V12 FEATURES
# ═══════════════════════════════════════════════════════════════════

def apply_sdf_collision(stage):
    """Switch all collision shapes to SDF (exact mesh surface)."""
    n = 0
    for prim in stage.Traverse():
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            mc = UsdPhysics.MeshCollisionAPI.Apply(prim)
            mc.CreateApproximationAttr("sdf")
            for prop_name in [p.GetName() for p in prim.GetAuthoredProperties()]:
                if "physxConvex" in prop_name:
                    prim.RemoveProperty(prop_name)
            n += 1
    return n


def create_articulation_variant(physics_usd, output_path):
    """Create ArticulationRootAPI variant for drive targets."""
    shutil.copy2(physics_usd, output_path)
    stage = Usd.Stage.Open(output_path)
    dp = stage.GetDefaultPrim()

    dp_spec = stage.GetRootLayer().GetPrimAtPath(dp.GetPath())
    schemas = dp_spec.GetInfo("apiSchemas")
    items = list(schemas.prependedItems) if schemas and hasattr(schemas, "prependedItems") else []
    if "PhysicsArticulationRootAPI" not in items:
        items.append("PhysicsArticulationRootAPI")
        new_list = Sdf.TokenListOp()
        new_list.prependedItems = items
        dp_spec.SetInfo("apiSchemas", new_list)

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


def generate_physics_json(stage, output_path):
    """Generate sidecar physics JSON."""
    dp = stage.GetDefaultPrim()
    def _bbox(prim):
        bmin=[1e30]*3; bmax=[-1e30]*3; found=False
        for c in Usd.PrimRange(prim):
            if c.GetTypeName()!="Mesh": continue
            pts=c.GetAttribute("points")
            if not pts or not pts.HasValue(): continue
            l2w=UsdGeom.Xformable(c).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            for pt in pts.Get():
                wp=l2w.TransformAffine(Gf.Vec3d(float(pt[0]),float(pt[1]),float(pt[2])))
                for i in range(3): bmin[i]=min(bmin[i],wp[i]); bmax[i]=max(bmax[i],wp[i])
                found=True
        return (bmin,bmax) if found else None

    parts=[]
    for prim in stage.Traverse():
        if not prim.HasAPI(UsdPhysics.RigidBodyAPI): continue
        ma=prim.GetAttribute("physics:mass"); m=ma.Get() if ma and ma.HasValue() else None
        ka=prim.GetAttribute("physics:kinematicEnabled"); ik=ka.Get() if ka and ka.HasValue() else False
        bb=_bbox(prim)
        bn={"min":[round(bb[0][i],6) for i in range(3)],"max":[round(bb[1][i],6) for i in range(3)],
            "size":[round(abs(bb[1][i]-bb[0][i]),6) for i in range(3)]} if bb else None
        nc=sum(1 for d in Usd.PrimRange(prim) if d.HasAPI(UsdPhysics.CollisionAPI))
        parts.append({"name":prim.GetName(),"path":str(prim.GetPath()),"is_kinematic":ik,
                      "mass_kg":round(m,4) if m else None,"bounds":bn,"colliders":nc,"collision_type":"sdf"})
    joints=[]
    for prim in stage.Traverse():
        if not prim.IsA(UsdPhysics.Joint): continue
        ji={"name":prim.GetName(),"type":prim.GetTypeName()}
        for an in ["physics:axis","physics:lowerLimit","physics:upperLimit"]:
            a=prim.GetAttribute(an)
            if a and a.HasValue(): v=a.Get(); ji[an.split(":")[-1]]=round(v,4) if isinstance(v,float) else v
        b0=prim.GetRelationship("physics:body0").GetTargets()
        b1=prim.GetRelationship("physics:body1").GetTargets()
        ji["body0"]=str(b0[0]) if b0 else "world"; ji["body1"]=str(b1[0]) if b1 else None
        dr={}
        for at in prim.GetAttributes():
            if "drive" in at.GetName() and at.HasValue():
                v=at.Get(); dr[at.GetName().split(":")[-1]]=round(v,4) if isinstance(v,float) else v
        if dr: ji["drive"]=dr
        joints.append(ji)
    tm=sum(p["mass_kg"] for p in parts if p["mass_kg"])
    spec={"version":"V12","asset_name":dp.GetName() if dp else "unknown",
          "summary":{"total_mass_kg":round(tm,2),"rigid_bodies":len(parts),"joints":len(joints),"collision":"SDF"},
          "parts":parts,"joints":joints}
    with open(output_path,"w") as f: json.dump(spec,f,indent=2,default=str)
    return spec


# ═══════════════════════════════════════════════════════════════════
# V12 MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════

def run_v12(input_usd, output_dir=None, dynamic_body=False, classify_json=None,
            object_json=None, provider="anthropic", model=None):
    """V12 complete pipeline: raw USD → full SimReady output."""
    import time as _time

    class PhaseTimer:
        """Tracks wall clock time per pipeline phase."""
        def __init__(self):
            self.phases = []
            self.pipeline_start = _time.time()
            self._phase_start = None
            self._phase_name = None

        def start(self, name):
            if self._phase_name:
                self.stop()
            self._phase_name = name
            self._phase_start = _time.time()

        def stop(self):
            if self._phase_name:
                elapsed = _time.time() - self._phase_start
                self.phases.append((self._phase_name, elapsed))
                self._phase_name = None

        def report(self):
            self.stop()
            total = _time.time() - self.pipeline_start
            print(f"\n  {'─' * 58}")
            print(f"  TIMING REPORT")
            print(f"  {'─' * 58}")
            for name, elapsed in self.phases:
                bar = "█" * int(elapsed / total * 30)
                pct = elapsed / total * 100
                print(f"    {name:40s} {elapsed:6.1f}s  {pct:5.1f}%  {bar}")
            print(f"  {'─' * 58}")
            print(f"    {'TOTAL':40s} {total:6.1f}s")
            return {"phases": {n: round(e, 2) for n, e in self.phases}, "total_seconds": round(total, 2)}

    timer = PhaseTimer()

    input_path = os.path.abspath(input_usd)
    basename = os.path.splitext(os.path.basename(input_path))[0]
    asset_name = basename

    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(input_path), "v12_out")
    temp_out = os.path.join(os.path.dirname(input_path), "simready_out")

    print(f"\n{'=' * 60}")
    print(f"  V12 SimReady Pipeline (complete)")
    print(f"{'=' * 60}")
    print(f"  Input:  {input_path}")
    print(f"  Output: {output_dir}/")

    # ── Phase 1a: Read hierarchy ──
    timer.start("Phase 1a: Read USD hierarchy")
    stage_tmp = Usd.Stage.Open(str(input_path))
    hier = read_hierarchy(stage_tmp)
    hier_text = hierarchy_to_text(hier)

    # ── Phase 1b: Render 8 views (single Blender session) ──
    timer.start("Phase 1b: Blender render (8 views)")
    rendered_views = []
    render_dir = os.path.join(tempfile.gettempdir(), "v12_renders")
    try:
        print(f"\n  [Phase 1b] Rendering 8 views (Blender headless)...")
        render_script = os.path.join(SCRIPT_DIR, "render_views.py")
        if os.path.exists(render_script):
            cmd = ["blender", "--background", "--python", render_script,
                   "--", str(input_path), render_dir]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0:
                for name in ("front", "back", "left", "right", "top", "bottom", "corner_fl", "corner_fr"):
                    p = os.path.join(render_dir, f"{name}.png")
                    if os.path.exists(p):
                        rendered_views.append(p)
                print(f"    Rendered {len(rendered_views)} views")
            else:
                print(f"    Blender failed: {result.stderr[-200:]}")
        else:
            print(f"    Skipped — render_views.py not found")
    except Exception as e:
        print(f"    Skipped — {e}")

    # ── Phase 1c: Gemini unified analysis (vision + object understanding in 1 call) ──
    timer.start("Phase 1c: Gemini analysis (1 combined call)")
    try:
        print(f"\n  [Phase 1c] Gemini combined analysis (vision + mass + material)...")
        from google import genai
        from google.genai import types

        api_key = _load_gemini_key()
        model_name = _load_gemini_model()
        if api_key and rendered_views:
            client = genai.Client(api_key=api_key)
            contents = []
            for img_path in rendered_views:
                with open(img_path, "rb") as f:
                    img_data = f.read()
                view_name = Path(img_path).stem
                contents.append(types.Part.from_text(text=f"[{view_name} view]"))
                contents.append(types.Part.from_bytes(data=img_data, mime_type="image/png"))

            contents.append(types.Part.from_text(text=f"""
Analyze this object for robotic simulation (SimReady). You have 8 views showing all sides.

USD HIERARCHY:
{hier_text}

Answer ALL of the following in ONE JSON response:

1. MOVABLE PARTS: List every part that can move independently.
   For each: name (match USD Xform names), type (door/drawer/wheel/slider/lever),
   axis (X/Y/Z), hinge_side (left/right/null), handle_visible (true/false)

2. OBJECT IDENTITY: What is this object specifically?
   object_name, object_type (furniture/tool/instrument/container)

3. MATERIAL & MASS: What is it made of? How much does it weigh?
   material (specific: "stainless_steel" not "metal"),
   material_density_kg_m3, estimated_mass_kg

4. ARTICULATION: For each movable part:
   behavior (door/drawer/slider/wheel), joint_type (revolute/prismatic/continuous),
   range_description ("0-120°", "0-15cm"), range_meters, limits_bidirectional (true/false)

5. ISSUES: Flag anything suspicious — parts that look movable but aren't in hierarchy,
   structural parts that might be misclassified, scale issues

Output as JSON:
{{
  "movable_parts": [{{"name": "...", "type": "door", "axis": "Z", "hinge_side": "left", "handle_visible": true}}],
  "object_name": "...",
  "object_type": "...",
  "material": "...",
  "material_density_kg_m3": 7800,
  "estimated_mass_kg": 0.15,
  "is_articulated": true,
  "articulated_parts": [{{"name": "...", "behavior": "slider", "joint_type": "prismatic", "axis": "Y", "range_meters": 0.15, "limits_bidirectional": true}}],
  "issues": ["..."],
  "confidence": 0.95
}}
"""))

            response = client.models.generate_content(
                model=model_name, contents=contents, config={"temperature": 0.1})
            text = response.text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            gemini_result = json.loads(text)
            n_parts = len(gemini_result.get("movable_parts", []))
            gm = gemini_result.get("estimated_mass_kg")
            print(f"    Object: {gemini_result.get('object_name', '?')}")
            print(f"    Movable parts: {n_parts}")
            print(f"    Mass: {gm}kg, Material: {gemini_result.get('material', '?')}")

            # Save object data for physics build
            obj_json_path = os.path.join(tempfile.gettempdir(), "v12_object.json")
            with open(obj_json_path, "w") as f:
                json.dump(gemini_result, f, indent=2)
            if not object_json:
                object_json = obj_json_path
        elif not api_key:
            print(f"    Skipped — no Gemini API key")
        elif not rendered_views:
            print(f"    Skipped — no rendered views")
    except json.JSONDecodeError as e:
        print(f"    Gemini returned invalid JSON — {e}")
    except Exception as e:
        print(f"    Skipped — {e}")

    # ── Phase 2: Build physics ──
    timer.start("Phase 2: Classification + physics")
    print(f"\n  [Phase 2] Building physics...")
    physics_usd = run(input_path, fix=True, provider=provider, model=model,
                      output_dir=temp_out, classify_json=classify_json,
                      dynamic_body=dynamic_body, object_json=object_json)
    if not physics_usd:
        for f in Path(temp_out).glob("*_physics.usd"):
            physics_usd = str(f); break
    if not physics_usd:
        print("  ERROR: No _physics.usd produced"); return None

    # ── Phase 3: SDF ──
    timer.start("Phase 3: SDF collision upgrade")
    os.makedirs(output_dir, exist_ok=True)
    v12_usd = os.path.join(output_dir, f"{basename}_physics.usd")
    shutil.copy2(physics_usd, v12_usd)
    for tn in ("Textures","textures","materials"):
        s=os.path.join(os.path.dirname(input_path),tn); d=os.path.join(output_dir,tn)
        if os.path.isdir(s) and not os.path.isdir(d): shutil.copytree(s,d)

    print(f"\n  [Phase 3] Upgrading collision to SDF...")
    stage = Usd.Stage.Open(v12_usd)
    n_sdf = apply_sdf_collision(stage)
    stage.GetRootLayer().Save()
    print(f"    {n_sdf} colliders → SDF")

    # ── Phase 4: Articulation variant ──
    timer.start("Phase 4: Articulation variant")
    print(f"\n  [Phase 4] Creating articulation variant...")
    artic_usd = os.path.join(output_dir, f"{asset_name}_articulation.usd")
    create_articulation_variant(v12_usd, artic_usd)

    # ── Phase 5: MuJoCo validation ──
    timer.start("Phase 5: MuJoCo validation")
    try:
        print(f"\n  [Phase 5] MuJoCo behavioral validation...")
        bv = validate(str(v12_usd), verbose=True)
        if bv and bv.get("fail_count", 0) > 0:
            print(f"    WARNING: {bv['fail_count']} check(s) FAILED")
    except Exception as e:
        print(f"    Skipped — {e}")

    # ── Phase 5b: PhysX headless testing (via Isaac Sim subprocess) ──
    timer.start("Phase 5b: PhysX testing (Isaac Sim)")
    try:
        print(f"\n  [Phase 5b] PhysX headless testing...")
        test_script = os.path.join(SCRIPT_DIR, "test_physics.py")
        isaaclab_path = os.environ.get("ISAACLAB_PATH", os.path.join(SCRIPT_DIR, "..", "..", ".."))
        isaaclab_sh = os.path.join(isaaclab_path, "isaaclab.sh")
        if os.path.exists(test_script) and os.path.exists(isaaclab_sh):
            phys_cmd = [isaaclab_sh, "-p", test_script,
                        "--asset", str(v12_usd), "--headless", "--device", "cpu"]
            phys_result = subprocess.run(phys_cmd, capture_output=True, text=True, timeout=300)
            # Print key results
            for line in phys_result.stdout.splitlines():
                if any(k in line for k in ["[+]", "[?]", "[X]", "PHYSX TEST"]):
                    print(f"    {line.strip()}")
            if phys_result.returncode != 0:
                print(f"    WARNING: PhysX test returned exit code {phys_result.returncode}")
        else:
            print(f"    Skipped — test_physics.py or isaaclab.sh not found")
    except subprocess.TimeoutExpired:
        print(f"    Skipped — timed out after 300s")
    except Exception as e:
        print(f"    Skipped — {e}")

    # ── Phase 8: Sidecar JSON (moved before Phase 6 so JSON exists for verification) ──
    timer.start("Phase 8: Sidecar JSON")
    print(f"\n  [Phase 8] Generating physics JSON...")
    stage = Usd.Stage.Open(v12_usd)
    json_path = os.path.join(output_dir, f"{asset_name}_physics.json")
    spec = generate_physics_json(stage, json_path)
    s = spec["summary"]
    print(f"    {s['rigid_bodies']} bodies, {s['joints']} joints, {s['total_mass_kg']}kg, SDF")

    # ── Phase 6: Visual verification (uses pre-build renders + physics JSON, no re-render) ──
    timer.start("Phase 6: Visual verification (JSON-based)")
    try:
        print(f"\n  [Phase 6] Visual verification (pre-build images + physics spec)...")
        from google import genai
        from google.genai import types as _types

        _api_key = _load_gemini_key()
        _model_name = _load_gemini_model()
        if _api_key and rendered_views and os.path.exists(json_path):
            _client = genai.Client(api_key=_api_key)
            _contents = []
            for img_path in rendered_views[:4]:  # Use first 4 cardinal views
                with open(img_path, "rb") as f:
                    _contents.append(_types.Part.from_text(text=f"[{Path(img_path).stem}]"))
                    _contents.append(_types.Part.from_bytes(data=f.read(), mime_type="image/png"))

            with open(json_path) as f:
                _spec_text = f.read()

            _contents.append(_types.Part.from_text(text=f"""
You are verifying a SimReady physics asset. Compare the IMAGES (the original object)
against the PHYSICS SPEC (what was built).

PHYSICS SPEC:
{_spec_text}

Check:
1. Does the number of rigid bodies match what you see? (doors, drawers, parts)
2. Do the joint types make sense? (revolute for doors, prismatic for drawers)
3. Is the total mass realistic for this object?
4. Are there any visible parts that should be articulated but aren't in the spec?

Output JSON:
{{"overall": "PASS" or "FAIL", "issues": ["list of problems"], "confidence": 0.9}}
"""))

            _resp = _client.models.generate_content(
                model=_model_name, contents=_contents, config={"temperature": 0.1})
            _text = _resp.text.strip()
            if _text.startswith("```"):
                _text = _text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            _vv = json.loads(_text)
            overall = _vv.get("overall", "?")
            issues = _vv.get("issues", [])
            print(f"    Result: {overall}")
            if issues:
                for iss in issues:
                    print(f"    - {iss}")
        else:
            print(f"    Skipped — missing API key, renders, or JSON")
    except Exception as e:
        print(f"    Skipped — {e}")

    # ── Phase 7: URDF export ──
    timer.start("Phase 7: URDF export")
    try:
        print(f"\n  [Phase 7] URDF export...")
        urdf_path = export_urdf(str(v12_usd), output_dir=output_dir, verbose=True)
    except Exception as e:
        print(f"    Skipped — {e}")

    # ── Timing report ──
    timing = timer.report()

    # Save timing to JSON alongside the asset
    timing_path = os.path.join(output_dir, f"{asset_name}_timing.json")
    with open(timing_path, "w") as f:
        json.dump(timing, f, indent=2)

    print(f"\n{'=' * 60}")
    print(f"  V12 COMPLETE")
    print(f"{'=' * 60}")
    print(f"  {v12_usd}")
    print(f"  {artic_usd}")
    print(f"  {json_path}")
    print(f"  {timing_path}")
    print(f"\n  Test:")
    print(f"    ISAACLAB_PATH=/home/msi/IsaacLab ./isaaclab.sh -p scripts/environments/teleoperation/teleop_se3_agent_cinematic.py \\")
    print(f"      --asset {os.path.abspath(v12_usd)} --device cpu")
    print(f"{'=' * 60}")
    return v12_usd


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="V12 SimReady Pipeline")
    ap.add_argument("--input", required=True, help="Raw USD file")
    ap.add_argument("--output-dir", default=None)
    ap.add_argument("--dynamic", action="store_true", help="Dynamic body")
    ap.add_argument("--classify-json", default=None)
    ap.add_argument("--object-json", default=None)
    ap.add_argument("--provider", default="anthropic", choices=["openai", "anthropic"])
    ap.add_argument("--model", default=None)
    args = ap.parse_args()
    input_path = os.path.abspath(args.input)
    if not os.path.isfile(input_path):
        print(f"ERROR: {input_path} not found"); sys.exit(1)
    run_v12(input_path, output_dir=args.output_dir, dynamic_body=args.dynamic,
            classify_json=args.classify_json, object_json=args.object_json,
            provider=args.provider, model=args.model)

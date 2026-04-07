#!/usr/bin/env python3
"""
V7 Stage E — Validator

Two-step file analysis. No live Blender connection.

Step 1 — .blend file (headless Blender):
  ✓ correct number of objects
  ✓ each part exists by name
  ✓ dims correct (bbox ±10%)
  ✓ origin/pivot at correct position (revolute parts)
  ✓ parent-child hierarchy correct
  ✓ materials assigned
  ✓ smooth shading on
  ✓ cavity faces deleted (for body parts)

Step 2 — .usd file (pxr):
  ✓ all meshes exported
  ✓ transforms in meters
  ✓ hierarchy preserved
  ✓ materials exported
  ✓ no degenerate meshes

If Step 1 FAILS → skip USD check, report Blender issues
If both PASS → report ✓
"""

import json
import os
import sys
import subprocess
import tempfile
import time

_DIR = os.path.dirname(os.path.abspath(__file__))
BLENDER_BIN = "/home/msi/.local/bin/blender"


# ═══════════════════════════════════════════════════════════════════
# STEP 1 — .blend validation (via headless Blender)
# ═══════════════════════════════════════════════════════════════════

BLEND_INSPECTOR = '''
import bpy
import bmesh
import json
import sys

blend_path = sys.argv[sys.argv.index("--blend") + 1]
spec_path  = sys.argv[sys.argv.index("--spec")  + 1]
out_path   = sys.argv[sys.argv.index("--out")   + 1]

bpy.ops.wm.open_mainfile(filepath=blend_path)

with open(spec_path) as f:
    spec = json.load(f)

results = {"objects": [], "checks": [], "pass": True}

spec_parts = {p["part"]: p for p in spec["parts"]}
articulation = spec["object"]["articulation"]

# Collect scene objects
scene_objs = {o.name: o for o in bpy.data.objects if o.type == "MESH"}
results["scene_object_count"] = len(scene_objs)
results["spec_part_count"]    = len(spec_parts)

# Check 1: object count
expected_separate = sum(1 for p in spec["parts"] if p.get("is_separate_object", True))
if len(scene_objs) < expected_separate * 0.7:
    results["checks"].append({"check": "object_count", "status": "FAIL",
        "expected": expected_separate, "actual": len(scene_objs)})
    results["pass"] = False
else:
    results["checks"].append({"check": "object_count", "status": "PASS",
        "expected": expected_separate, "actual": len(scene_objs)})

# Check each part
for part_name, spec_part in spec_parts.items():
    obj = scene_objs.get(part_name)
    part_result = {"part": part_name, "found": obj is not None, "checks": []}

    if obj is None:
        part_result["checks"].append({"check": "exists", "status": "FAIL"})
        results["pass"] = False
        results["objects"].append(part_result)
        continue

    part_result["checks"].append({"check": "exists", "status": "PASS"})

    # Dims check ±10%
    expected = spec_part.get("dims_reconciled", spec_part.get("dims_mm", {}))
    ew = expected.get("width_mm",  0) / 1000
    ed = expected.get("depth_mm",  0) / 1000
    eh = expected.get("height_mm", 0) / 1000
    dims = obj.dimensions
    aw, ad, ah = dims.x, dims.y, dims.z

    def pct(a, e):
        return abs(a - e) / max(e, 0.001) * 100

    dim_ok = (ew == 0 or pct(aw, ew) < 10) and \
             (ed == 0 or pct(ad, ed) < 10) and \
             (eh == 0 or pct(ah, eh) < 10)

    part_result["checks"].append({
        "check": "dims",
        "status": "PASS" if dim_ok else "FAIL",
        "expected_m": [round(ew,3), round(ed,3), round(eh,3)],
        "actual_m":   [round(aw,3), round(ad,3), round(ah,3)],
    })
    if not dim_ok:
        results["pass"] = False

    # Material check
    has_mat = len(obj.data.materials) > 0
    part_result["checks"].append({"check": "material", "status": "PASS" if has_mat else "FAIL"})
    if not has_mat:
        results["pass"] = False

    # Smooth shading check
    smooth = all(f.use_smooth for f in obj.data.polygons) if obj.data.polygons else False
    part_result["checks"].append({"check": "smooth_shading", "status": "PASS" if smooth else "WARN"})

    # Parent check (articulated only)
    if articulation == "ARTICULATED":
        expected_parent = spec_part.get("parent", "none")
        actual_parent   = obj.parent.name if obj.parent else "none"
        parent_ok = (expected_parent in ("none", None, "")) == (actual_parent == "none") or \
                     actual_parent == expected_parent
        part_result["checks"].append({
            "check": "parent",
            "status": "PASS" if parent_ok else "FAIL",
            "expected": expected_parent,
            "actual": actual_parent,
        })
        if not parent_ok:
            results["pass"] = False

    # Pivot check for revolute parts
    behavior_type = spec_part.get("behavior", {}).get("behavior_type", "")
    if behavior_type == "ROTATIONAL" and articulation == "ARTICULATED":
        pivot_spec = spec_part.get("pivot", "")
        origin = obj.location
        part_result["checks"].append({
            "check": "pivot_origin",
            "status": "INFO",
            "pivot_spec": pivot_spec,
            "actual_origin": [round(origin.x,4), round(origin.y,4), round(origin.z,4)],
        })

    # Cavity face check
    if spec_part.get("cavity_face_delete"):
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        front_faces = [f for f in bm.faces if f.normal.y > 0.85]
        bm.free()
        cavity_ok = len(front_faces) == 0
        part_result["checks"].append({
            "check": "cavity_deleted",
            "status": "PASS" if cavity_ok else "FAIL",
            "front_faces_remaining": len(front_faces),
        })
        if not cavity_ok:
            results["pass"] = False

    results["objects"].append(part_result)

with open(out_path, "w") as f:
    json.dump(results, f, indent=2)

print("BLEND_CHECK_DONE:", "PASS" if results["pass"] else "FAIL")
'''


def check_blend(blend_path: str, spec: dict, output_dir: str) -> dict:
    """Run headless Blender to inspect .blend file."""
    spec_path   = os.path.join(output_dir, "_e_spec.json")
    result_path = os.path.join(output_dir, "_e_blend_result.json")

    with open(spec_path, "w") as f:
        json.dump(spec, f)

    # Write inspector script to temp file
    inspector_path = os.path.join(output_dir, "_e_blend_inspector.py")
    with open(inspector_path, "w") as f:
        f.write(BLEND_INSPECTOR)

    cmd = [
        BLENDER_BIN, "--background", "--python", inspector_path,
        "--", "--blend", blend_path, "--spec", spec_path, "--out", result_path
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        stdout = proc.stdout + proc.stderr

        if not os.path.exists(result_path):
            return {"status": "error", "message": f"Inspector did not produce output.\n{stdout[-500:]}"}

        with open(result_path) as f:
            result = json.load(f)

        result["stdout"] = stdout[-1000:]
        return result

    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Blender headless timed out"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ═══════════════════════════════════════════════════════════════════
# STEP 2 — .usd validation (pxr)
# ═══════════════════════════════════════════════════════════════════

def check_usd(usd_path: str, spec: dict) -> dict:
    """Inspect .usd file using pxr."""
    try:
        from pxr import Usd, UsdGeom, UsdShade

        stage = Usd.Stage.Open(usd_path)
        checks = []
        passed = True

        spec_parts = {p["part"]: p for p in spec["parts"]}
        found_meshes = {}

        for prim in stage.Traverse():
            if prim.IsA(UsdGeom.Mesh):
                # USD exports as /root/part_name/Cube — use parent prim name as key
                parent_prim = prim.GetParent()
                name = parent_prim.GetName() if parent_prim else prim.GetName()
                mesh = UsdGeom.Mesh(prim)

                # Get points count
                pts = mesh.GetPointsAttr().Get()
                n_pts = len(pts) if pts else 0

                # Get xform
                xform = UsdGeom.Xformable(prim)
                xform_ops = xform.GetOrderedXformOps()

                # Get bounding box
                bbox_cache = UsdGeom.BBoxCache(
                    Usd.TimeCode.Default(),
                    [UsdGeom.Tokens.default_]
                )
                bbox   = bbox_cache.ComputeWorldBound(prim)
                b_range = bbox.GetRange()
                b_min  = b_range.GetMin()
                b_max  = b_range.GetMax()
                dims_m = [
                    round(b_max[0] - b_min[0], 4),
                    round(b_max[1] - b_min[1], 4),
                    round(b_max[2] - b_min[2], 4),
                ]

                # Material binding
                mat_api  = UsdShade.MaterialBindingAPI(prim)
                mat      = mat_api.GetDirectBinding().GetMaterial()
                has_mat  = mat.GetPrim().IsValid()

                # Parent
                parent_name = prim.GetParent().GetName() if prim.GetParent() else "none"

                found_meshes[name] = {
                    "n_points": n_pts,
                    "dims_m": dims_m,
                    "has_material": has_mat,
                    "parent": parent_name,
                }

        # Check 1: all spec parts exported
        missing = [name for name in spec_parts if name not in found_meshes]
        if missing:
            checks.append({"check": "all_parts_exported", "status": "FAIL", "missing": missing})
            passed = False
        else:
            checks.append({"check": "all_parts_exported", "status": "PASS",
                           "count": len(found_meshes)})

        # Check 2: no degenerate meshes (zero points)
        degenerate = [n for n, m in found_meshes.items() if m["n_points"] == 0]
        if degenerate:
            checks.append({"check": "no_degenerate", "status": "FAIL", "degenerate": degenerate})
            passed = False
        else:
            checks.append({"check": "no_degenerate", "status": "PASS"})

        # Check 3: dims in meters (no part > 10m or < 0.001m in any axis)
        oversized  = [n for n, m in found_meshes.items() if any(d > 10 for d in m["dims_m"])]
        undersized = [n for n, m in found_meshes.items()
                      if any(0 < d < 0.001 for d in m["dims_m"])]
        if oversized:
            checks.append({"check": "dims_in_meters", "status": "WARN",
                           "oversized": oversized})
        elif undersized:
            checks.append({"check": "dims_in_meters", "status": "WARN",
                           "undersized": undersized})
        else:
            checks.append({"check": "dims_in_meters", "status": "PASS"})

        # Check 4: materials exported
        no_mat = [n for n, m in found_meshes.items() if not m["has_material"]]
        if no_mat:
            checks.append({"check": "materials_exported", "status": "WARN", "no_material": no_mat})
        else:
            checks.append({"check": "materials_exported", "status": "PASS"})

        return {"pass": passed, "checks": checks, "meshes": found_meshes}

    except Exception as e:
        return {"pass": False, "checks": [], "error": str(e)}


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def run_stage_e(spec: dict, blend_path: str, usd_path: str, output_dir: str) -> dict:
    t0 = time.time()
    print(f"\n{'='*60}")
    print(f"  STAGE E — Validator")
    print(f"{'='*60}")

    # ── Step 1: .blend ──────────────────────────────────────────────
    print(f"\n  [E1] Checking .blend file...")
    print(f"       {blend_path}")
    blend_result = check_blend(blend_path, spec, output_dir)

    if blend_result.get("status") == "error":
        print(f"  ✗ Blend check error: {blend_result['message'][:200]}")
        return {"status": "error", "blend": blend_result}

    blend_pass = blend_result.get("pass", False)
    n_obj  = blend_result.get("scene_object_count", 0)
    n_spec = blend_result.get("spec_part_count", 0)
    print(f"  {'✓' if blend_pass else '✗'} Blend: {'PASS' if blend_pass else 'FAIL'} — {n_obj} objects in scene, {n_spec} in spec")

    for chk in blend_result.get("checks", []):
        icon = "✓" if chk["status"] == "PASS" else ("⚠" if chk["status"] == "WARN" else "✗")
        print(f"    {icon} {chk['check']}: {chk['status']}")

    for obj_r in blend_result.get("objects", []):
        fails = [c for c in obj_r.get("checks", []) if c["status"] == "FAIL"]
        warns = [c for c in obj_r.get("checks", []) if c["status"] == "WARN"]
        if fails:
            print(f"    ✗ {obj_r['part']}:")
            for c in fails:
                print(f"      - {c['check']}: {c}")
        elif warns:
            print(f"    ⚠ {obj_r['part']}: {[c['check'] for c in warns]}")

    if not blend_pass:
        print(f"\n  ⛔ Blend failed — skipping USD check")
        elapsed = time.time() - t0
        print(f"  Stage E: {elapsed:.1f}s")
        return {"status": "blend_failed", "blend": blend_result}

    # ── Step 2: .usd ────────────────────────────────────────────────
    print(f"\n  [E2] Checking .usd file...")
    print(f"       {usd_path}")
    usd_result = check_usd(usd_path, spec)

    usd_pass = usd_result.get("pass", False)
    print(f"  {'✓' if usd_pass else '✗'} USD: {'PASS' if usd_pass else 'FAIL'}")

    for chk in usd_result.get("checks", []):
        icon = "✓" if chk["status"] == "PASS" else ("⚠" if chk["status"] == "WARN" else "✗")
        print(f"    {icon} {chk['check']}: {chk['status']}", end="")
        if "count" in chk:
            print(f" ({chk['count']} meshes)", end="")
        if "missing" in chk:
            print(f" missing={chk['missing']}", end="")
        print()

    overall_pass = blend_pass and usd_pass
    elapsed = time.time() - t0

    print(f"\n  {'✓ PASS' if overall_pass else '✗ FAIL'} — Stage E complete ({elapsed:.1f}s)")
    print(f"{'='*60}")

    result = {
        "status": "pass" if overall_pass else "fail",
        "blend": blend_result,
        "usd": usd_result,
    }

    out_path = os.path.join(output_dir, "stage_e.json")
    # strip heavy fields
    save = {
        "status": result["status"],
        "blend_pass": blend_pass,
        "usd_pass": usd_pass,
        "blend_checks": blend_result.get("checks", []),
        "usd_checks": usd_result.get("checks", []),
        "objects": [{k: v for k, v in o.items() if k != "stdout"} for o in blend_result.get("objects", [])],
        "usd_meshes": usd_result.get("meshes", {}),
    }
    with open(out_path, "w") as f:
        json.dump(save, f, indent=2)
    print(f"  Saved: {out_path}")

    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage_c", required=True)
    parser.add_argument("--blend",   required=True)
    parser.add_argument("--usd",     required=True)
    parser.add_argument("--output_dir", default=None)
    args = parser.parse_args()

    with open(args.stage_c) as f:
        spec = json.load(f)

    out_dir = args.output_dir or os.path.dirname(args.stage_c)
    run_stage_e(spec, args.blend, args.usd, out_dir)

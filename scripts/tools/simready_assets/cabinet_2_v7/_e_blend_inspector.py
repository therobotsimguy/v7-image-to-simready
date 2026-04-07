
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

    dim_ok = (ew == 0 or pct(aw, ew) < 10) and              (ed == 0 or pct(ad, ed) < 10) and              (eh == 0 or pct(ah, eh) < 10)

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
        parent_ok = (expected_parent in ("none", None, "")) == (actual_parent == "none") or                      actual_parent == expected_parent
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

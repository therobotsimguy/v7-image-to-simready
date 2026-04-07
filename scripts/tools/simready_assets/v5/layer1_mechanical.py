#!/usr/bin/env python3
"""V5 Layer 1: Mechanical Extraction — "What CAN this object do?"

Extracts mechanical properties from the Blender scene:
  - Objects, names, vertices, bounding boxes
  - Materials
  - Hierarchy / parent-child relationships
  - Origins

Populates the BehaviorContract with PartContract entries.
"""

import json
import os
import sys
import socket

from pxr import Usd, UsdGeom

_DIR = os.path.dirname(os.path.abspath(__file__))
_ASSETS_DIR = os.path.dirname(_DIR)
sys.path.insert(0, _ASSETS_DIR)

from v5.behavior_contract import BehaviorContract, PartContract
from geometry_math import bbox_volume_mm3, estimate_mass_kg, material_density


def send_to_blender(script, port=9876):
    """Send script to Blender MCP and get result."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(180)
    sock.connect(("localhost", port))
    sock.sendall(json.dumps({"type": "execute_code", "params": {"code": script}}).encode())
    response = b""
    while True:
        try:
            chunk = sock.recv(8192)
            if not chunk:
                break
            response += chunk
            try:
                json.loads(response.decode())
                break
            except:
                continue
        except socket.timeout:
            break
    sock.close()
    if not response:
        raise RuntimeError("No response from Blender")
    return json.loads(response.decode())


def load_obj_in_blender(obj_path, port=9876):
    """Load an OBJ file into Blender, apply transforms."""
    script = f'''
import bpy
from mathutils import Vector

# Clear scene
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
for m in list(bpy.data.meshes): bpy.data.meshes.remove(m)
for m in list(bpy.data.materials): bpy.data.materials.remove(m)

# Import OBJ
bpy.ops.wm.obj_import(filepath="{obj_path}")

# Apply transforms (OBJ Y-up → Blender Z-up)
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

print(f"Loaded {{len([o for o in bpy.data.objects if o.type=='MESH'])}} meshes")
'''
    result = send_to_blender(script, port)
    return result


def load_usd_in_blender(usd_path, port=9876):
    """Load a USD file into Blender, applying metersPerUnit scale correction."""
    stage = Usd.Stage.Open(usd_path)
    meters_per_unit = UsdGeom.GetStageMetersPerUnit(stage)
    # Blender treats imported values as meters — scale by metersPerUnit to correct
    scale = float(meters_per_unit)
    print(f"  USD metersPerUnit={meters_per_unit:.4f}, applying scale={scale}")

    script = f'''
import bpy

# Clear scene
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
for m in list(bpy.data.meshes): bpy.data.meshes.remove(m)
for m in list(bpy.data.materials): bpy.data.materials.remove(m)

# Import USD with unit scale correction
bpy.ops.wm.usd_import(filepath="{usd_path}", import_materials=True, scale={scale})

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

print(f"Loaded {{len([o for o in bpy.data.objects if o.type=='MESH'])}} meshes from USD (scale={scale})")
'''
    result = send_to_blender(script, port)
    return result


def extract_scene_data(port=9876):
    """Query Blender for complete scene data."""
    script = '''
import bpy
import json
from mathutils import Vector

scene_data = {"objects": []}

for obj in sorted(bpy.data.objects, key=lambda o: o.name):
    if obj.type != 'MESH':
        continue

    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    xs = [c.x for c in corners]
    ys = [c.y for c in corners]
    zs = [c.z for c in corners]

    mats = [m.name for m in obj.data.materials if m]

    # Count face types
    tris = sum(1 for p in obj.data.polygons if len(p.vertices) == 3)
    quads = sum(1 for p in obj.data.polygons if len(p.vertices) == 4)

    # Check for front-facing faces that might block openings
    front_faces = 0
    for p in obj.data.polygons:
        if p.normal.y < -0.9:
            vs = [obj.matrix_world @ obj.data.vertices[v].co for v in p.vertices]
            x_span = max(v.x for v in vs) - min(v.x for v in vs)
            z_span = max(v.z for v in vs) - min(v.z for v in vs)
            if x_span > 0.1 and z_span > 0.1:
                front_faces += 1

    obj_data = {
        "name": obj.name,
        "vertices": len(obj.data.vertices),
        "faces": len(obj.data.polygons),
        "tris": tris,
        "quads": quads,
        "origin": [round(obj.location.x, 4), round(obj.location.y, 4), round(obj.location.z, 4)],
        "bbox_min": [round(min(xs), 4), round(min(ys), 4), round(min(zs), 4)],
        "bbox_max": [round(max(xs), 4), round(max(ys), 4), round(max(zs), 4)],
        "dims_mm": [
            round((max(xs) - min(xs)) * 1000, 1),
            round((max(ys) - min(ys)) * 1000, 1),
            round((max(zs) - min(zs)) * 1000, 1),
        ],
        "materials": mats,
        "front_blocking_faces": front_faces,
        "parent": obj.parent.name if obj.parent else None,
    }
    scene_data["objects"].append(obj_data)

# Overall bounding box
if scene_data["objects"]:
    all_min = [min(o["bbox_min"][i] for o in scene_data["objects"]) for i in range(3)]
    all_max = [max(o["bbox_max"][i] for o in scene_data["objects"]) for i in range(3)]
    scene_data["overall_dims_mm"] = [round((all_max[i] - all_min[i]) * 1000, 1) for i in range(3)]
else:
    scene_data["overall_dims_mm"] = [0, 0, 0]

print(json.dumps(scene_data))
'''
    result = send_to_blender(script, port)
    output = result.get("result", {}).get("result", "")
    if isinstance(output, str) and output.strip().startswith("{"):
        return json.loads(output)
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 1 MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def run_layer1(source_file, contract=None, port=9876, skip_load=False):
    """Run Layer 1: Mechanical Extraction.

    Args:
        source_file: path to OBJ/blend file
        contract: existing BehaviorContract to populate (or creates new)
        port: Blender MCP port

    Returns:
        BehaviorContract with Layer 1 data populated
    """
    print("\n" + "=" * 60)
    print("  LAYER 1: Mechanical Extraction")
    print("  'What CAN this object do?'")
    print("=" * 60)

    # Create contract if not provided
    if contract is None:
        obj_name = os.path.splitext(os.path.basename(source_file))[0]
        contract = BehaviorContract(
            object_name=obj_name,
            object_type="unknown",
            source_file=source_file,
        )

    # Load file in Blender (unless scene already loaded from image path)
    if skip_load:
        print(f"  Scene already in Blender (from image → geometry stage)")
    else:
        ext = os.path.splitext(source_file)[1].lower()
        if ext == ".obj":
            print(f"  Loading OBJ: {source_file}")
            load_obj_in_blender(source_file, port)
        elif ext in (".blend",):
            print(f"  Loading blend: {source_file}")
            script = f'import bpy; bpy.ops.wm.open_mainfile(filepath="{source_file}")'
            send_to_blender(script, port)
        elif ext in (".usd", ".usda", ".usdc"):
            print(f"  Loading USD: {source_file}")
            load_usd_in_blender(source_file, port)
        else:
            raise ValueError(f"Unsupported file type: {ext}")

    # Extract scene data
    print("  Extracting scene data...")
    scene = extract_scene_data(port)
    if not scene:
        raise RuntimeError("Failed to extract scene data from Blender")

    objects = scene.get("objects", [])
    print(f"  Found {len(objects)} mesh objects")

    # Overall dims
    contract.overall_dims_mm = tuple(scene.get("overall_dims_mm", (0, 0, 0)))

    # Layer 1 does NOT decide root_part — that's Layer 2's job
    # Layer 1 only extracts data
    contract.root_part = None

    # Create PartContract for each object
    for obj in objects:
        density = material_density(obj["materials"])
        max_dim = max(obj["dims_mm"])
        fill = 0.2 if max_dim > 200 else 0.8
        mass = estimate_mass_kg(tuple(obj["dims_mm"]), density, fill)

        part = PartContract(
            name=obj["name"],
            part_type="unknown",  # Layer 2 will identify this
            is_static=False,      # Layer 2 will decide this
            vertices=obj["vertices"],
            faces=obj["faces"],
            bbox_min=tuple(obj["bbox_min"]),
            bbox_max=tuple(obj["bbox_max"]),
            dims_mm=tuple(obj["dims_mm"]),
            origin=tuple(obj["origin"]),
            materials=obj["materials"],
            mass_kg=mass,
            parent_part=None,  # Layer 2 will set parent relationships
        )

        # Note front-blocking faces for Layer 3
        if obj.get("front_blocking_faces", 0) > 0:
            part.blender_actions.append(
                f"INVESTIGATE: {obj['front_blocking_faces']} front-facing faces may block cavity openings"
            )

        # Note triangle faces
        if obj.get("tris", 0) > 0:
            part.blender_actions.append(
                f"REMOVE: {obj['tris']} triangle faces (cause phantom collision in PhysX)"
            )

        contract.parts.append(part)

        print(f"    {obj['name']}: {obj['vertices']}v, {obj['dims_mm']}mm, "
              f"mass≈{mass}kg, mats={obj['materials']}")

    # ── SPATIAL CONTAINMENT TREE ──────────────────────────────────────────────────
    # For each pair of parts: if child's centroid is inside parent's bbox AND
    # child volume < 90% of parent volume → child is spatially inside parent.
    # This replaces name-pattern merge heuristics with geometry-based grouping.
    print("  Computing spatial containment tree...")
    containment: dict = {}
    part_by_name = {p.name: p for p in contract.parts}

    def _vol(p):
        return ((p.bbox_max[0]-p.bbox_min[0]) *
                (p.bbox_max[1]-p.bbox_min[1]) *
                (p.bbox_max[2]-p.bbox_min[2]))

    for parent in contract.parts:
        pvol = _vol(parent)
        if pvol <= 0:
            continue
        children = []
        for child in contract.parts:
            if child.name == parent.name:
                continue
            cvol = _vol(child)
            if cvol <= 0 or cvol >= pvol * 0.95:
                continue  # child must be smaller than parent
            # Check: child centroid inside parent bbox (with 60mm tolerance for protruding parts)
            cx = (child.bbox_min[0] + child.bbox_max[0]) / 2
            cy = (child.bbox_min[1] + child.bbox_max[1]) / 2
            cz = (child.bbox_min[2] + child.bbox_max[2]) / 2
            tol = 0.06  # 60mm — handles handles that stick out slightly
            if (parent.bbox_min[0]-tol <= cx <= parent.bbox_max[0]+tol and
                parent.bbox_min[1]-tol <= cy <= parent.bbox_max[1]+tol and
                parent.bbox_min[2]-tol <= cz <= parent.bbox_max[2]+tol):
                children.append(child.name)
        if children:
            containment[parent.name] = children
            parent_part = part_by_name.get(parent.name)
            if parent_part:
                parent_part.spatial_children = children

    contract.containment_tree = containment
    n_relations = sum(len(v) for v in containment.values())
    print(f"  Containment tree: {len(containment)} parents, {n_relations} child relationships")

    contract.layer1_complete = True
    print(f"\n  Layer 1 complete: {len(contract.parts)} parts extracted")
    return contract


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    source = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        _ASSETS_DIR, "built_in_double_oven_final (1).obj"
    )
    contract = run_layer1(source)
    print("\n" + contract.to_json()[:2000])

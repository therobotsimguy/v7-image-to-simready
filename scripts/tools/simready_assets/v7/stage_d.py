#!/usr/bin/env python3
"""
V7 Stage D — Blender Script Writer

Pure translator: spec from Stage C → Blender Python script → execute via MCP.
Zero decisions made here. Every value comes directly from the spec.

For each part:
  1. geometry     → Blender primitive
  2. position_xyz → exact placement (from Math Engine)
  3. dims_reconciled → exact scale
  4. pivot        → set_origin_keep_visual() for revolute parts
  5. material     → Principled BSDF
  6. parent       → obj.parent
  7. cavity_face_delete → delete front faces
  8. smooth shading → shade_smooth()

Exports: .blend + .usd
"""

import json
import os
import sys
import socket
import time

_DIR = os.path.dirname(os.path.abspath(__file__))


# ═══════════════════════════════════════════════════════════════════
# BLENDER MCP
# ═══════════════════════════════════════════════════════════════════

def send_to_blender(script: str, port: int = 9876) -> dict:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(300)
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
        raise RuntimeError("No response from Blender MCP")
    return json.loads(response.decode())


# ═══════════════════════════════════════════════════════════════════
# SCRIPT GENERATORS — one per geometry type
# ═══════════════════════════════════════════════════════════════════

def _mat_block(part_name: str, mat: dict) -> str:
    """Generate material creation code."""
    r, g, b = mat.get("color_rgb", [0.6, 0.4, 0.2])
    roughness = mat.get("roughness", 0.7)
    metallic  = mat.get("metallic", 0)
    mat_name  = f"{part_name}_mat"
    return f"""
    mat = bpy.data.materials.new("{mat_name}")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = ({r}, {g}, {b}, 1.0)
        bsdf.inputs["Roughness"].default_value  = {roughness}
        bsdf.inputs["Metallic"].default_value   = {metallic}
    obj.data.materials.append(mat)"""


def _origin_block(pivot: str, dims: dict) -> str:
    """Generate set_origin_keep_visual call for revolute parts."""
    pivot_lower = pivot.lower()
    w = dims["width_mm"]  / 1000
    d = dims["depth_mm"]  / 1000
    h = dims["height_mm"] / 1000

    if "left" in pivot_lower and "edge" in pivot_lower:
        ox = f"-{w/2}"
    elif "right" in pivot_lower and "edge" in pivot_lower:
        ox = f"{w/2}"
    else:
        ox = "0.0"

    if "bottom" in pivot_lower:
        oz = f"-{h/2}"
    elif "top" in pivot_lower:
        oz = f"{h/2}"
    else:
        oz = "0.0"

    return f"""
    set_origin_keep_visual(obj, {ox}, 0.0, {oz})"""


def _box_part(p: dict) -> str:
    """Generate code for a box-shaped part."""
    name = p["part"]
    dims = p["dims_reconciled"]
    w = dims["width_mm"]  / 1000
    d = dims["depth_mm"]  / 1000
    h = dims["height_mm"] / 1000
    x, y, z = p["position_xyz"]
    mat = p["material"]
    pivot = p.get("pivot", "")
    cavity = p.get("cavity_face_delete", False)
    btype = p.get("behavior", {}).get("behavior_type", "")

    code = f"""
    # ── {name} ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=({x}, {y}, {z}))
    obj = bpy.context.active_object
    obj.name = "{name}"
    obj.scale = ({w}, {d}, {h})
    bpy.ops.object.transform_apply(scale=True)"""

    # Cavity face deletion for body/chassis
    if cavity:
        code += f"""
    # Delete front-facing faces (cavity opening)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='DESELECT')
    bpy.ops.object.mode_set(mode='OBJECT')
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    front_faces = [f for f in bm.faces if f.normal.y > 0.9]
    for f in front_faces:
        bm.faces.remove(f)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()"""

    # Material
    code += _mat_block(name, mat)

    # Pivot for revolute doors
    if btype == "ROTATIONAL" and pivot and "N/A" not in pivot:
        code += _origin_block(pivot, dims)

    # Smooth shading
    code += f"""
    bpy.ops.object.shade_smooth()"""

    return code


def _cylinder_part(p: dict) -> str:
    """Generate code for cylinder/knob shaped part.
    Knobs protrude from door along Y axis — cylinder is rotated 90° on X,
    then scaled to exact width/depth/height from spec.
    """
    name = p["part"]
    dims = p["dims_reconciled"]
    w = dims["width_mm"]  / 1000   # diameter across door face (X)
    d = dims["depth_mm"]  / 1000   # protrusion depth from door (Y)
    h = dims["height_mm"] / 1000   # height on door face (Z)
    x, y, z = p["position_xyz"]
    mat = p["material"]

    code = f"""
    # ── {name} ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cylinder_add(radius=0.5, depth=1.0, location=({x}, {y}, {z}))
    obj = bpy.context.active_object
    obj.name = "{name}"
    # Rotate so cylinder protrudes along Y axis (depth direction)
    obj.rotation_euler = (1.5708, 0, 0)
    bpy.ops.object.transform_apply(rotation=True)
    # Scale to exact spec dims: X=width, Y=depth (protrusion), Z=height
    obj.scale = ({w}, {d}, {h})
    bpy.ops.object.transform_apply(scale=True)"""

    code += _mat_block(name, mat)
    code += f"""
    bpy.ops.object.shade_smooth()"""

    return code


def _bar_handle_part(p: dict) -> str:
    """Generate code for bar handle (rectangular bar)."""
    name = p["part"]
    dims = p["dims_reconciled"]
    w = dims["width_mm"]  / 1000
    d = dims["depth_mm"]  / 1000
    h = dims["height_mm"] / 1000
    x, y, z = p["position_xyz"]
    mat = p["material"]

    code = f"""
    # ── {name} ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=({x}, {y}, {z}))
    obj = bpy.context.active_object
    obj.name = "{name}"
    obj.scale = ({w}, {d}, {h})
    bpy.ops.object.transform_apply(scale=True)"""

    code += _mat_block(name, mat)
    code += f"""
    bpy.ops.object.shade_smooth()"""

    return code


# ═══════════════════════════════════════════════════════════════════
# GEOMETRY ROUTER — picks generator based on geometry field
# ═══════════════════════════════════════════════════════════════════

def _part_code(p: dict) -> str:
    geom = p.get("geometry", "").lower()
    name = p["part"].lower()

    if "knob" in name or "cylinder" in geom or "sphere" in geom:
        return _cylinder_part(p)
    elif "handle" in name and "bar" in geom:
        return _bar_handle_part(p)
    else:
        return _box_part(p)


# ═══════════════════════════════════════════════════════════════════
# PARENT ASSIGNMENT — after all objects created
# ═══════════════════════════════════════════════════════════════════

def _parent_block(parts: list) -> str:
    code = "\n    # ── Parent assignments ──────────────────────────────────────"
    code += """
    # matrix_parent_inverse keeps child's WORLD position unchanged after parenting.
    # Without it, Blender reinterprets the child's world coords as local-to-parent,
    # compounding the parent's transform and placing the child at the wrong location."""
    for p in parts:
        parent = p.get("parent", "")
        if parent and parent not in ("none", None, ""):
            code += f"""
    if "{p['part']}" in bpy.data.objects and "{parent}" in bpy.data.objects:
        _child  = bpy.data.objects["{p['part']}"]
        _parent = bpy.data.objects["{parent}"]
        _child.parent = _parent
        _child.matrix_parent_inverse = _parent.matrix_world.inverted()"""
    return code


# ═══════════════════════════════════════════════════════════════════
# FULL SCRIPT BUILDER
# ═══════════════════════════════════════════════════════════════════

def build_script(spec: dict, output_blend: str, output_usd: str) -> str:
    parts = spec["parts"]
    obj_type = spec["object"]["type"]

    header = f'''import bpy
import bmesh
from mathutils import Vector

# ── Helpers ─────────────────────────────────────────────────────────
def set_origin_keep_visual(obj, rel_x, rel_y, rel_z):
    """Shift origin by relative offset from current location — geometry stays put visually.
    rel_x/y/z are LOCAL offsets from the object's current center, e.g. -w/2 for left edge."""
    delta = Vector((rel_x, rel_y, rel_z))
    obj.location = obj.location + delta
    for v in obj.data.vertices:
        v.co -= delta

# ── Clear scene ──────────────────────────────────────────────────────
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
for block in bpy.data.meshes:
    bpy.data.meshes.remove(block)
for block in bpy.data.materials:
    bpy.data.materials.remove(block)

print("Building: {obj_type}")
print("Parts: {len(parts)}")

def build():
'''

    body = ""
    for p in parts:
        body += _part_code(p)

    body += _parent_block(parts)

    footer = f'''
    # ── Export ──────────────────────────────────────────────────────────
    bpy.ops.wm.save_as_mainfile(filepath=r"{output_blend}")
    print("Saved blend:", r"{output_blend}")

    try:
        bpy.ops.wm.usd_export(filepath=r"{output_usd}", export_materials=True, export_normals=True)
        print("Exported USD:", r"{output_usd}")
    except Exception as e:
        print("USD export error:", e)

    # ── Report ──────────────────────────────────────────────────────────
    meshes = [o for o in bpy.data.objects if o.type == 'MESH']
    print(f"Objects in scene: {{len(meshes)}}")
    for o in meshes:
        dims = o.dimensions
        print(f"  {{o.name}}: {{dims.x:.3f}} x {{dims.y:.3f}} x {{dims.z:.3f}} m  origin={{tuple(round(v,3) for v in o.location)}}")

build()
'''

    return header + body + footer


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def run_stage_d(spec: dict, output_dir: str, blender_port: int = 9876) -> dict:
    t0 = time.time()
    print(f"\n{'='*60}")
    print(f"  STAGE D — Blender Script Writer")
    print(f"{'='*60}")

    obj_name     = spec["object"]["type"].replace(" ", "_")
    output_blend = os.path.abspath(os.path.join(output_dir, f"{obj_name}.blend"))
    output_usd   = os.path.abspath(os.path.join(output_dir, f"{obj_name}.usd"))

    # Build script
    script = build_script(spec, output_blend, output_usd)
    script_path = os.path.join(output_dir, "blender_script.py")
    with open(script_path, "w") as f:
        f.write(script)
    print(f"  Script: {len(script.splitlines())} lines → {script_path}")

    # Execute in Blender
    print(f"  Sending to Blender (port {blender_port})...")
    try:
        result = send_to_blender(script, port=blender_port)
        status = result.get("status", "?")
        output = result.get("output", "")
        error  = result.get("message", "")

        if status == "error":
            print(f"  ✗ Blender error: {error[:300]}")
            return {"status": "error", "message": error}

        print(f"  ✓ Blender OK")
        if output:
            for line in output.strip().split("\n"):
                print(f"    {line}")

    except Exception as e:
        print(f"  ✗ Connection error: {e}")
        print(f"  (Is Blender open with MCP addon running on port {blender_port}?)")
        return {"status": "error", "message": str(e)}

    elapsed = time.time() - t0
    print(f"\n  ✓ Stage D complete — {elapsed:.1f}s")
    print(f"  .blend: {output_blend}")
    print(f"  .usd:   {output_usd}")
    print(f"{'='*60}")

    return {
        "status": "success",
        "output_blend": output_blend,
        "output_usd": output_usd,
        "script_path": script_path,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage_c", required=True)
    parser.add_argument("--output_dir", default=None)
    parser.add_argument("--port", type=int, default=9876)
    parser.add_argument("--dry_run", action="store_true", help="Build script only, don't send to Blender")
    args = parser.parse_args()

    with open(args.stage_c) as f:
        spec = json.load(f)

    out_dir = args.output_dir or os.path.dirname(args.stage_c)
    os.makedirs(out_dir, exist_ok=True)

    if args.dry_run:
        obj_name    = spec["object"]["type"].replace(" ", "_")
        output_blend = os.path.join(out_dir, f"{obj_name}.blend")
        output_usd   = os.path.join(out_dir, f"{obj_name}.usd")
        script = build_script(spec, output_blend, output_usd)
        script_path = os.path.join(out_dir, "blender_script.py")
        with open(script_path, "w") as f:
            f.write(script)
        print(f"Dry run — script written to: {script_path}")
        print(f"Lines: {len(script.splitlines())}")
    else:
        run_stage_d(spec, out_dir, blender_port=args.port)

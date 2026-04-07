import bpy
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

print("Building: teak sideboard cabinet")
print("Parts: 18")

def build():

    # ── cabinet_body ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0.0, 0.0, 0.45))
    obj = bpy.context.active_object
    obj.name = "cabinet_body"
    obj.scale = (1.46, 0.48, 0.9)
    bpy.ops.object.transform_apply(scale=True)
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
    obj.data.update()
    mat = bpy.data.materials.new("cabinet_body_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.658, 0.521, 0.368, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.7
        bsdf.inputs["Metallic"].default_value   = 0
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── top_panel ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0.0, 0.0, 0.915))
    obj = bpy.context.active_object
    obj.name = "top_panel"
    obj.scale = (1.5, 0.5, 0.03)
    bpy.ops.object.transform_apply(scale=True)
    mat = bpy.data.materials.new("top_panel_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.658, 0.521, 0.368, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.7
        bsdf.inputs["Metallic"].default_value   = 0
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── left_drawer ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=(-0.4504, 0.015, 0.73))
    obj = bpy.context.active_object
    obj.name = "left_drawer"
    obj.scale = (0.69, 0.45, 0.18)
    bpy.ops.object.transform_apply(scale=True)
    mat = bpy.data.materials.new("left_drawer_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.658, 0.521, 0.368, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.7
        bsdf.inputs["Metallic"].default_value   = 0
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── middle_drawer ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=(-0.0, 0.015, 0.73))
    obj = bpy.context.active_object
    obj.name = "middle_drawer"
    obj.scale = (0.33, 0.45, 0.18)
    bpy.ops.object.transform_apply(scale=True)
    mat = bpy.data.materials.new("middle_drawer_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.658, 0.521, 0.368, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.7
        bsdf.inputs["Metallic"].default_value   = 0
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── right_drawer ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0.4504, 0.015, 0.73))
    obj = bpy.context.active_object
    obj.name = "right_drawer"
    obj.scale = (0.33, 0.45, 0.18)
    bpy.ops.object.transform_apply(scale=True)
    mat = bpy.data.materials.new("right_drawer_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.658, 0.521, 0.368, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.7
        bsdf.inputs["Metallic"].default_value   = 0
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── left_door ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=(-0.4504, 0.2275, 0.32))
    obj = bpy.context.active_object
    obj.name = "left_door"
    obj.scale = (0.69, 0.025, 0.6)
    bpy.ops.object.transform_apply(scale=True)
    mat = bpy.data.materials.new("left_door_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.658, 0.521, 0.368, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.7
        bsdf.inputs["Metallic"].default_value   = 0
    obj.data.materials.append(mat)
    set_origin_keep_visual(obj, -0.345, 0.0, 0.0)
    bpy.ops.object.shade_smooth()
    # ── middle_door ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=(-0.0, 0.2275, 0.32))
    obj = bpy.context.active_object
    obj.name = "middle_door"
    obj.scale = (0.33, 0.025, 0.6)
    bpy.ops.object.transform_apply(scale=True)
    mat = bpy.data.materials.new("middle_door_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.658, 0.521, 0.368, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.7
        bsdf.inputs["Metallic"].default_value   = 0
    obj.data.materials.append(mat)
    set_origin_keep_visual(obj, -0.165, 0.0, 0.0)
    bpy.ops.object.shade_smooth()
    # ── right_door ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0.4504, 0.2275, 0.32))
    obj = bpy.context.active_object
    obj.name = "right_door"
    obj.scale = (0.33, 0.025, 0.6)
    bpy.ops.object.transform_apply(scale=True)
    mat = bpy.data.materials.new("right_door_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.658, 0.521, 0.368, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.7
        bsdf.inputs["Metallic"].default_value   = 0
    obj.data.materials.append(mat)
    set_origin_keep_visual(obj, 0.165, 0.0, 0.0)
    bpy.ops.object.shade_smooth()
    # ── left_drawer_handle ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=(-0.4504, 0.255, 0.73))
    obj = bpy.context.active_object
    obj.name = "left_drawer_handle"
    obj.scale = (0.15, 0.03, 0.03)
    bpy.ops.object.transform_apply(scale=True)
    mat = bpy.data.materials.new("left_drawer_handle_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.75, 0.75, 0.75, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.4
        bsdf.inputs["Metallic"].default_value   = 1
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── middle_drawer_handle ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=(-0.0, 0.255, 0.73))
    obj = bpy.context.active_object
    obj.name = "middle_drawer_handle"
    obj.scale = (0.15, 0.03, 0.03)
    bpy.ops.object.transform_apply(scale=True)
    mat = bpy.data.materials.new("middle_drawer_handle_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.75, 0.75, 0.75, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.4
        bsdf.inputs["Metallic"].default_value   = 1
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── right_drawer_handle ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0.4504, 0.255, 0.73))
    obj = bpy.context.active_object
    obj.name = "right_drawer_handle"
    obj.scale = (0.15, 0.03, 0.03)
    bpy.ops.object.transform_apply(scale=True)
    mat = bpy.data.materials.new("right_drawer_handle_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.75, 0.75, 0.75, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.4
        bsdf.inputs["Metallic"].default_value   = 1
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── left_door_knob ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cylinder_add(radius=0.5, depth=1.0, location=(-0.4504, 0.2525, 0.32))
    obj = bpy.context.active_object
    obj.name = "left_door_knob"
    # Rotate so cylinder protrudes along Y axis (depth direction)
    obj.rotation_euler = (1.5708, 0, 0)
    bpy.ops.object.transform_apply(rotation=True)
    # Scale to exact spec dims: X=width, Y=depth (protrusion), Z=height
    obj.scale = (0.03, 0.025, 0.03)
    bpy.ops.object.transform_apply(scale=True)
    mat = bpy.data.materials.new("left_door_knob_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.75, 0.75, 0.75, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.4
        bsdf.inputs["Metallic"].default_value   = 1
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── middle_door_knob ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cylinder_add(radius=0.5, depth=1.0, location=(-0.0, 0.2525, 0.32))
    obj = bpy.context.active_object
    obj.name = "middle_door_knob"
    # Rotate so cylinder protrudes along Y axis (depth direction)
    obj.rotation_euler = (1.5708, 0, 0)
    bpy.ops.object.transform_apply(rotation=True)
    # Scale to exact spec dims: X=width, Y=depth (protrusion), Z=height
    obj.scale = (0.03, 0.025, 0.03)
    bpy.ops.object.transform_apply(scale=True)
    mat = bpy.data.materials.new("middle_door_knob_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.75, 0.75, 0.75, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.4
        bsdf.inputs["Metallic"].default_value   = 1
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── right_door_knob ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cylinder_add(radius=0.5, depth=1.0, location=(0.4504, 0.2525, 0.32))
    obj = bpy.context.active_object
    obj.name = "right_door_knob"
    # Rotate so cylinder protrudes along Y axis (depth direction)
    obj.rotation_euler = (1.5708, 0, 0)
    bpy.ops.object.transform_apply(rotation=True)
    # Scale to exact spec dims: X=width, Y=depth (protrusion), Z=height
    obj.scale = (0.03, 0.025, 0.03)
    bpy.ops.object.transform_apply(scale=True)
    mat = bpy.data.materials.new("right_door_knob_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.75, 0.75, 0.75, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.4
        bsdf.inputs["Metallic"].default_value   = 1
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── door_divider_1 ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=(-0.1352, 0.0, 0.32))
    obj = bpy.context.active_object
    obj.name = "door_divider_1"
    obj.scale = (0.02, 0.48, 0.6)
    bpy.ops.object.transform_apply(scale=True)
    mat = bpy.data.materials.new("door_divider_1_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.658, 0.521, 0.368, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.7
        bsdf.inputs["Metallic"].default_value   = 0
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── door_divider_2 ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0.2252, 0.0, 0.32))
    obj = bpy.context.active_object
    obj.name = "door_divider_2"
    obj.scale = (0.02, 0.48, 0.6)
    bpy.ops.object.transform_apply(scale=True)
    mat = bpy.data.materials.new("door_divider_2_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.658, 0.521, 0.368, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.7
        bsdf.inputs["Metallic"].default_value   = 0
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── drawer_divider_1 ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=(-0.1352, 0.0, 0.73))
    obj = bpy.context.active_object
    obj.name = "drawer_divider_1"
    obj.scale = (0.02, 0.48, 0.18)
    bpy.ops.object.transform_apply(scale=True)
    mat = bpy.data.materials.new("drawer_divider_1_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.658, 0.521, 0.368, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.7
        bsdf.inputs["Metallic"].default_value   = 0
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── drawer_divider_2 ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0.2252, 0.0, 0.73))
    obj = bpy.context.active_object
    obj.name = "drawer_divider_2"
    obj.scale = (0.02, 0.48, 0.18)
    bpy.ops.object.transform_apply(scale=True)
    mat = bpy.data.materials.new("drawer_divider_2_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.658, 0.521, 0.368, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.7
        bsdf.inputs["Metallic"].default_value   = 0
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── Parent assignments ──────────────────────────────────────
    if "top_panel" in bpy.data.objects and "cabinet_body" in bpy.data.objects:
        bpy.data.objects["top_panel"].parent = bpy.data.objects["cabinet_body"]
    if "left_drawer" in bpy.data.objects and "cabinet_body" in bpy.data.objects:
        bpy.data.objects["left_drawer"].parent = bpy.data.objects["cabinet_body"]
    if "middle_drawer" in bpy.data.objects and "cabinet_body" in bpy.data.objects:
        bpy.data.objects["middle_drawer"].parent = bpy.data.objects["cabinet_body"]
    if "right_drawer" in bpy.data.objects and "cabinet_body" in bpy.data.objects:
        bpy.data.objects["right_drawer"].parent = bpy.data.objects["cabinet_body"]
    if "left_door" in bpy.data.objects and "cabinet_body" in bpy.data.objects:
        bpy.data.objects["left_door"].parent = bpy.data.objects["cabinet_body"]
    if "middle_door" in bpy.data.objects and "cabinet_body" in bpy.data.objects:
        bpy.data.objects["middle_door"].parent = bpy.data.objects["cabinet_body"]
    if "right_door" in bpy.data.objects and "cabinet_body" in bpy.data.objects:
        bpy.data.objects["right_door"].parent = bpy.data.objects["cabinet_body"]
    if "left_drawer_handle" in bpy.data.objects and "left_drawer" in bpy.data.objects:
        bpy.data.objects["left_drawer_handle"].parent = bpy.data.objects["left_drawer"]
    if "middle_drawer_handle" in bpy.data.objects and "middle_drawer" in bpy.data.objects:
        bpy.data.objects["middle_drawer_handle"].parent = bpy.data.objects["middle_drawer"]
    if "right_drawer_handle" in bpy.data.objects and "right_drawer" in bpy.data.objects:
        bpy.data.objects["right_drawer_handle"].parent = bpy.data.objects["right_drawer"]
    if "left_door_knob" in bpy.data.objects and "left_door" in bpy.data.objects:
        bpy.data.objects["left_door_knob"].parent = bpy.data.objects["left_door"]
    if "middle_door_knob" in bpy.data.objects and "middle_door" in bpy.data.objects:
        bpy.data.objects["middle_door_knob"].parent = bpy.data.objects["middle_door"]
    if "right_door_knob" in bpy.data.objects and "right_door" in bpy.data.objects:
        bpy.data.objects["right_door_knob"].parent = bpy.data.objects["right_door"]
    if "door_divider_1" in bpy.data.objects and "cabinet_body" in bpy.data.objects:
        bpy.data.objects["door_divider_1"].parent = bpy.data.objects["cabinet_body"]
    if "door_divider_2" in bpy.data.objects and "cabinet_body" in bpy.data.objects:
        bpy.data.objects["door_divider_2"].parent = bpy.data.objects["cabinet_body"]
    if "drawer_divider_1" in bpy.data.objects and "cabinet_body" in bpy.data.objects:
        bpy.data.objects["drawer_divider_1"].parent = bpy.data.objects["cabinet_body"]
    if "drawer_divider_2" in bpy.data.objects and "cabinet_body" in bpy.data.objects:
        bpy.data.objects["drawer_divider_2"].parent = bpy.data.objects["cabinet_body"]
    # ── Export ──────────────────────────────────────────────────────────
    bpy.ops.wm.save_as_mainfile(filepath=r"/home/msi/IsaacLab/scripts/tools/simready_assets/cabinet_2_v7/teak_sideboard_cabinet.blend")
    print("Saved blend:", r"/home/msi/IsaacLab/scripts/tools/simready_assets/cabinet_2_v7/teak_sideboard_cabinet.blend")

    try:
        bpy.ops.wm.usd_export(filepath=r"/home/msi/IsaacLab/scripts/tools/simready_assets/cabinet_2_v7/teak_sideboard_cabinet.usd", export_materials=True, export_normals=True)
        print("Exported USD:", r"/home/msi/IsaacLab/scripts/tools/simready_assets/cabinet_2_v7/teak_sideboard_cabinet.usd")
    except Exception as e:
        print("USD export error:", e)

    # ── Report ──────────────────────────────────────────────────────────
    meshes = [o for o in bpy.data.objects if o.type == 'MESH']
    print(f"Objects in scene: {len(meshes)}")
    for o in meshes:
        dims = o.dimensions
        print(f"  {o.name}: {dims.x:.3f} x {dims.y:.3f} x {dims.z:.3f} m  origin={tuple(round(v,3) for v in o.location)}")

build()

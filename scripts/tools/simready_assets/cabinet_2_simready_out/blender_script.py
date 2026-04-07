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

print("Building: teak outdoor sideboard")
print("Parts: 18")

def build():

    # ── main_frame ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0.0, 0.0, 0.435))
    obj = bpy.context.active_object
    obj.name = "main_frame"
    obj.scale = (1.5, 0.55, 0.87)
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
    mat = bpy.data.materials.new("main_frame_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.73, 0.56, 0.38, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.7
        bsdf.inputs["Metallic"].default_value   = 0
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── top_surface ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0.0, 0.0, 0.885))
    obj = bpy.context.active_object
    obj.name = "top_surface"
    obj.scale = (1.5, 0.55, 0.03)
    bpy.ops.object.transform_apply(scale=True)
    mat = bpy.data.materials.new("top_surface_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.73, 0.56, 0.38, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.7
        bsdf.inputs["Metallic"].default_value   = 0
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── left_drawer ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=(-0.4618, -0.225, 0.09))
    obj = bpy.context.active_object
    obj.name = "left_drawer"
    obj.scale = (0.57, 0.5, 0.18)
    bpy.ops.object.transform_apply(scale=True)
    for v in obj.data.vertices:
        v.co.y += 0.25
    obj.data.update()
    mat = bpy.data.materials.new("left_drawer_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.73, 0.56, 0.38, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.7
        bsdf.inputs["Metallic"].default_value   = 0
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── middle_drawer ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0.0, -0.225, 0.09))
    obj = bpy.context.active_object
    obj.name = "middle_drawer"
    obj.scale = (0.375, 0.5, 0.18)
    bpy.ops.object.transform_apply(scale=True)
    for v in obj.data.vertices:
        v.co.y += 0.25
    obj.data.update()
    mat = bpy.data.materials.new("middle_drawer_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.73, 0.56, 0.38, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.7
        bsdf.inputs["Metallic"].default_value   = 0
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── right_drawer ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0.4618, -0.225, 0.09))
    obj = bpy.context.active_object
    obj.name = "right_drawer"
    obj.scale = (0.375, 0.5, 0.18)
    bpy.ops.object.transform_apply(scale=True)
    for v in obj.data.vertices:
        v.co.y += 0.25
    obj.data.update()
    mat = bpy.data.materials.new("right_drawer_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.73, 0.56, 0.38, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.7
        bsdf.inputs["Metallic"].default_value   = 0
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── left_door ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=(-0.7467999999999999, 0.264, 0.485))
    obj = bpy.context.active_object
    obj.name = "left_door"
    obj.scale = (0.57, 0.022, 0.57)
    bpy.ops.object.transform_apply(scale=True)
    for v in obj.data.vertices:
        v.co.x += 0.285
    obj.data.update()
    mat = bpy.data.materials.new("left_door_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.73, 0.56, 0.38, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.7
        bsdf.inputs["Metallic"].default_value   = 0
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── middle_door ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=(-0.1875, 0.264, 0.485))
    obj = bpy.context.active_object
    obj.name = "middle_door"
    obj.scale = (0.375, 0.022, 0.57)
    bpy.ops.object.transform_apply(scale=True)
    for v in obj.data.vertices:
        v.co.x += 0.1875
    obj.data.update()
    mat = bpy.data.materials.new("middle_door_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.73, 0.56, 0.38, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.7
        bsdf.inputs["Metallic"].default_value   = 0
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── right_door ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0.6493, 0.264, 0.485))
    obj = bpy.context.active_object
    obj.name = "right_door"
    obj.scale = (0.375, 0.022, 0.57)
    bpy.ops.object.transform_apply(scale=True)
    for v in obj.data.vertices:
        v.co.x += -0.1875
    obj.data.update()
    mat = bpy.data.materials.new("right_door_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.73, 0.56, 0.38, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.7
        bsdf.inputs["Metallic"].default_value   = 0
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── left_drawer_handle ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=(-0.4618, 0.2925, 0.09))
    obj = bpy.context.active_object
    obj.name = "left_drawer_handle"
    obj.scale = (0.16, 0.035, 0.03)
    bpy.ops.object.transform_apply(scale=True)
    mat = bpy.data.materials.new("left_drawer_handle_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.7, 0.7, 0.7, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.4
        bsdf.inputs["Metallic"].default_value   = 1
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── middle_drawer_handle ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0.0, 0.2925, 0.09))
    obj = bpy.context.active_object
    obj.name = "middle_drawer_handle"
    obj.scale = (0.16, 0.035, 0.03)
    bpy.ops.object.transform_apply(scale=True)
    mat = bpy.data.materials.new("middle_drawer_handle_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.7, 0.7, 0.7, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.4
        bsdf.inputs["Metallic"].default_value   = 1
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── right_drawer_handle ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0.4618, 0.2925, 0.09))
    obj = bpy.context.active_object
    obj.name = "right_drawer_handle"
    obj.scale = (0.16, 0.035, 0.03)
    bpy.ops.object.transform_apply(scale=True)
    mat = bpy.data.materials.new("right_drawer_handle_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.7, 0.7, 0.7, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.4
        bsdf.inputs["Metallic"].default_value   = 1
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── left_door_knob ──────────────────────────────────────────────────
    # Create at ORIGIN — transform_apply(rotation) resets location if created at non-zero pos
    bpy.ops.mesh.primitive_cylinder_add(radius=0.5, depth=1.0, location=(0, 0, 0))
    obj = bpy.context.active_object
    obj.name = "left_door_knob"
    # Rotate so cylinder protrudes along Y axis (depth direction)
    obj.rotation_euler = (1.5708, 0, 0)
    bpy.ops.object.transform_apply(rotation=True)
    # Scale to exact spec dims: X=width, Y=depth (protrusion), Z=height
    obj.scale = (0.032, 0.028, 0.032)
    bpy.ops.object.transform_apply(scale=True)
    # Set location AFTER all transforms — matrix_world is now reliable
    obj.location = (-0.4618, 0.289, 0.485)
    mat = bpy.data.materials.new("left_door_knob_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.7, 0.7, 0.7, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.4
        bsdf.inputs["Metallic"].default_value   = 1
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── middle_door_knob ──────────────────────────────────────────────────
    # Create at ORIGIN — transform_apply(rotation) resets location if created at non-zero pos
    bpy.ops.mesh.primitive_cylinder_add(radius=0.5, depth=1.0, location=(0, 0, 0))
    obj = bpy.context.active_object
    obj.name = "middle_door_knob"
    # Rotate so cylinder protrudes along Y axis (depth direction)
    obj.rotation_euler = (1.5708, 0, 0)
    bpy.ops.object.transform_apply(rotation=True)
    # Scale to exact spec dims: X=width, Y=depth (protrusion), Z=height
    obj.scale = (0.032, 0.028, 0.032)
    bpy.ops.object.transform_apply(scale=True)
    # Set location AFTER all transforms — matrix_world is now reliable
    obj.location = (0.0, 0.289, 0.485)
    mat = bpy.data.materials.new("middle_door_knob_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.7, 0.7, 0.7, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.4
        bsdf.inputs["Metallic"].default_value   = 1
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── right_door_knob ──────────────────────────────────────────────────
    # Create at ORIGIN — transform_apply(rotation) resets location if created at non-zero pos
    bpy.ops.mesh.primitive_cylinder_add(radius=0.5, depth=1.0, location=(0, 0, 0))
    obj = bpy.context.active_object
    obj.name = "right_door_knob"
    # Rotate so cylinder protrudes along Y axis (depth direction)
    obj.rotation_euler = (1.5708, 0, 0)
    bpy.ops.object.transform_apply(rotation=True)
    # Scale to exact spec dims: X=width, Y=depth (protrusion), Z=height
    obj.scale = (0.032, 0.028, 0.032)
    bpy.ops.object.transform_apply(scale=True)
    # Set location AFTER all transforms — matrix_world is now reliable
    obj.location = (0.4618, 0.289, 0.485)
    mat = bpy.data.materials.new("right_door_knob_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.7, 0.7, 0.7, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.4
        bsdf.inputs["Metallic"].default_value   = 1
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── door_divider_1 ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=(-0.1822, 0.0, 0.485))
    obj = bpy.context.active_object
    obj.name = "door_divider_1"
    obj.scale = (0.02, 0.55, 0.57)
    bpy.ops.object.transform_apply(scale=True)
    mat = bpy.data.materials.new("door_divider_1_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.73, 0.56, 0.38, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.7
        bsdf.inputs["Metallic"].default_value   = 0
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── door_divider_2 ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0.2309, 0.0, 0.485))
    obj = bpy.context.active_object
    obj.name = "door_divider_2"
    obj.scale = (0.02, 0.55, 0.57)
    bpy.ops.object.transform_apply(scale=True)
    mat = bpy.data.materials.new("door_divider_2_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.73, 0.56, 0.38, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.7
        bsdf.inputs["Metallic"].default_value   = 0
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── drawer_divider_1 ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=(-0.1822, 0.0, 0.09))
    obj = bpy.context.active_object
    obj.name = "drawer_divider_1"
    obj.scale = (0.02, 0.55, 0.18)
    bpy.ops.object.transform_apply(scale=True)
    mat = bpy.data.materials.new("drawer_divider_1_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.73, 0.56, 0.38, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.7
        bsdf.inputs["Metallic"].default_value   = 0
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── drawer_divider_2 ──────────────────────────────────────────────────
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0.2309, 0.0, 0.09))
    obj = bpy.context.active_object
    obj.name = "drawer_divider_2"
    obj.scale = (0.02, 0.55, 0.18)
    bpy.ops.object.transform_apply(scale=True)
    mat = bpy.data.materials.new("drawer_divider_2_mat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.73, 0.56, 0.38, 1.0)
        bsdf.inputs["Roughness"].default_value  = 0.7
        bsdf.inputs["Metallic"].default_value   = 0
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    # ── Parent assignments ──────────────────────────────────────
    bpy.context.view_layer.update()  # ensure matrix_world is current for all objects
    if "top_surface" in bpy.data.objects and "main_frame" in bpy.data.objects:
        child = bpy.data.objects["top_surface"]
        parent = bpy.data.objects["main_frame"]
        child.parent = parent
        child.matrix_parent_inverse = parent.matrix_world.inverted()
    if "left_drawer" in bpy.data.objects and "main_frame" in bpy.data.objects:
        child = bpy.data.objects["left_drawer"]
        parent = bpy.data.objects["main_frame"]
        child.parent = parent
        child.matrix_parent_inverse = parent.matrix_world.inverted()
    if "middle_drawer" in bpy.data.objects and "main_frame" in bpy.data.objects:
        child = bpy.data.objects["middle_drawer"]
        parent = bpy.data.objects["main_frame"]
        child.parent = parent
        child.matrix_parent_inverse = parent.matrix_world.inverted()
    if "right_drawer" in bpy.data.objects and "main_frame" in bpy.data.objects:
        child = bpy.data.objects["right_drawer"]
        parent = bpy.data.objects["main_frame"]
        child.parent = parent
        child.matrix_parent_inverse = parent.matrix_world.inverted()
    if "left_door" in bpy.data.objects and "main_frame" in bpy.data.objects:
        child = bpy.data.objects["left_door"]
        parent = bpy.data.objects["main_frame"]
        child.parent = parent
        child.matrix_parent_inverse = parent.matrix_world.inverted()
    if "middle_door" in bpy.data.objects and "main_frame" in bpy.data.objects:
        child = bpy.data.objects["middle_door"]
        parent = bpy.data.objects["main_frame"]
        child.parent = parent
        child.matrix_parent_inverse = parent.matrix_world.inverted()
    if "right_door" in bpy.data.objects and "main_frame" in bpy.data.objects:
        child = bpy.data.objects["right_door"]
        parent = bpy.data.objects["main_frame"]
        child.parent = parent
        child.matrix_parent_inverse = parent.matrix_world.inverted()
    if "left_drawer_handle" in bpy.data.objects and "left_drawer" in bpy.data.objects:
        child = bpy.data.objects["left_drawer_handle"]
        parent = bpy.data.objects["left_drawer"]
        child.parent = parent
        child.matrix_parent_inverse = parent.matrix_world.inverted()
    if "middle_drawer_handle" in bpy.data.objects and "middle_drawer" in bpy.data.objects:
        child = bpy.data.objects["middle_drawer_handle"]
        parent = bpy.data.objects["middle_drawer"]
        child.parent = parent
        child.matrix_parent_inverse = parent.matrix_world.inverted()
    if "right_drawer_handle" in bpy.data.objects and "right_drawer" in bpy.data.objects:
        child = bpy.data.objects["right_drawer_handle"]
        parent = bpy.data.objects["right_drawer"]
        child.parent = parent
        child.matrix_parent_inverse = parent.matrix_world.inverted()
    if "left_door_knob" in bpy.data.objects and "left_door" in bpy.data.objects:
        child = bpy.data.objects["left_door_knob"]
        parent = bpy.data.objects["left_door"]
        child.parent = parent
        child.matrix_parent_inverse = parent.matrix_world.inverted()
    if "middle_door_knob" in bpy.data.objects and "middle_door" in bpy.data.objects:
        child = bpy.data.objects["middle_door_knob"]
        parent = bpy.data.objects["middle_door"]
        child.parent = parent
        child.matrix_parent_inverse = parent.matrix_world.inverted()
    if "right_door_knob" in bpy.data.objects and "right_door" in bpy.data.objects:
        child = bpy.data.objects["right_door_knob"]
        parent = bpy.data.objects["right_door"]
        child.parent = parent
        child.matrix_parent_inverse = parent.matrix_world.inverted()
    if "door_divider_1" in bpy.data.objects and "main_frame" in bpy.data.objects:
        child = bpy.data.objects["door_divider_1"]
        parent = bpy.data.objects["main_frame"]
        child.parent = parent
        child.matrix_parent_inverse = parent.matrix_world.inverted()
    if "door_divider_2" in bpy.data.objects and "main_frame" in bpy.data.objects:
        child = bpy.data.objects["door_divider_2"]
        parent = bpy.data.objects["main_frame"]
        child.parent = parent
        child.matrix_parent_inverse = parent.matrix_world.inverted()
    if "drawer_divider_1" in bpy.data.objects and "main_frame" in bpy.data.objects:
        child = bpy.data.objects["drawer_divider_1"]
        parent = bpy.data.objects["main_frame"]
        child.parent = parent
        child.matrix_parent_inverse = parent.matrix_world.inverted()
    if "drawer_divider_2" in bpy.data.objects and "main_frame" in bpy.data.objects:
        child = bpy.data.objects["drawer_divider_2"]
        parent = bpy.data.objects["main_frame"]
        child.parent = parent
        child.matrix_parent_inverse = parent.matrix_world.inverted()
    # ── Export ──────────────────────────────────────────────────────────
    bpy.ops.wm.save_as_mainfile(filepath=r"/home/msi/IsaacLab/scripts/tools/simready_assets/cabinet_2_simready_out/teak_outdoor_sideboard.blend")
    print("Saved blend:", r"/home/msi/IsaacLab/scripts/tools/simready_assets/cabinet_2_simready_out/teak_outdoor_sideboard.blend")

    try:
        bpy.ops.wm.usd_export(filepath=r"/home/msi/IsaacLab/scripts/tools/simready_assets/cabinet_2_simready_out/teak_outdoor_sideboard.usd", export_materials=True, export_normals=True)
        print("Exported USD:", r"/home/msi/IsaacLab/scripts/tools/simready_assets/cabinet_2_simready_out/teak_outdoor_sideboard.usd")
    except Exception as e:
        print("USD export error:", e)

    # ── Report ──────────────────────────────────────────────────────────
    meshes = [o for o in bpy.data.objects if o.type == 'MESH']
    print(f"Objects in scene: {len(meshes)}")
    for o in meshes:
        dims = o.dimensions
        print(f"  {o.name}: {dims.x:.3f} x {dims.y:.3f} x {dims.z:.3f} m  origin={tuple(round(v,3) for v in o.location)}")

build()

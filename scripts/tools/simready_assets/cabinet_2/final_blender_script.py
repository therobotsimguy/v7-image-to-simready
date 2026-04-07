import bpy
import bmesh
import os
from mathutils import Vector

# ============================================================
# CLEAR SCENE
# ============================================================
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
for m in list(bpy.data.meshes):
    bpy.data.meshes.remove(m)
for m in list(bpy.data.materials):
    bpy.data.materials.remove(m)

# ============================================================
# HELPER FUNCTIONS
# ============================================================
def set_origin_keep_visual(obj, new_origin_x, new_origin_y, new_origin_z):
    new_origin = Vector((new_origin_x, new_origin_y, new_origin_z))
    offset = new_origin - obj.location
    obj.location = new_origin
    for v in obj.data.vertices:
        v.co -= offset

def create_box(name, center, size, material=None):
    """Create a box at center with given size (wx, wy, wz)."""
    bpy.ops.mesh.primitive_cube_add(size=1, location=center)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = (size[0], size[1], size[2])
    bpy.ops.object.transform_apply(scale=True)
    if material:
        obj.data.materials.append(material)
    return obj

def create_material(name, color_rgb, metallic=0.0, roughness=0.5):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()
    output = nodes.new('ShaderNodeOutputMaterial')
    bsdf = nodes.new('ShaderNodeBsdfPrincipled')
    bsdf.inputs['Base Color'].default_value = (color_rgb[0], color_rgb[1], color_rgb[2], 1.0)
    bsdf.inputs['Metallic'].default_value = metallic
    bsdf.inputs['Roughness'].default_value = roughness
    mat.node_tree.links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])
    return mat

def apply_smooth_shading(obj):
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.shade_smooth()
    obj.select_set(False)

def join_objects(objects):
    """Join a list of objects into one."""
    if not objects:
        return None
    bpy.ops.object.select_all(action='DESELECT')
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.join()
    return bpy.context.active_object

def add_raised_panel_detail(obj, border=0.04, depth=0.005):
    """Add a raised panel detail to the front face of a box using bmesh inset + extrude."""
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    
    # Find the front face (max Y normal)
    front_face = None
    max_y = -999
    for f in bm.faces:
        if f.normal.y > 0.9:
            cy = sum(v.co.y for v in f.verts) / len(f.verts)
            if cy > max_y:
                max_y = cy
                front_face = f
    
    if front_face:
        # Get face dimensions to compute relative inset
        xs = [v.co.x for v in front_face.verts]
        zs = [v.co.z for v in front_face.verts]
        face_w = max(xs) - min(xs)
        face_h = max(zs) - min(zs)
        min_dim = min(face_w, face_h)
        inset_thickness = min(border, min_dim * 0.15)
        
        # Select only this face
        for f in bm.faces:
            f.select = False
        front_face.select = True
        
        # Inset
        result = bmesh.ops.inset_individual(bm, faces=[front_face], thickness=inset_thickness, depth=0)
        
        bm.faces.ensure_lookup_table()
        
        # Find the inner face (still selected after inset)
        inner_face = None
        for f in bm.faces:
            if f.select and f.normal.y > 0.9:
                inner_face = f
                break
        
        if inner_face:
            # Extrude inward slightly to create depth
            bmesh.ops.extrude_face_region(bm, geom=[inner_face])
            bm.faces.ensure_lookup_table()
            # Move the extruded verts inward (along -Y)
            for v in bm.verts:
                if v.select:
                    v.co.y -= depth
    
    bmesh.update_edit_mesh(obj.data)
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.select_set(False)

# ============================================================
# MATERIALS
# ============================================================
mat_wood = create_material("Teak_Wood", (0.82, 0.59, 0.39), metallic=0.0, roughness=0.65)
mat_metal = create_material("Brushed_Nickel", (0.75, 0.75, 0.75), metallic=0.9, roughness=0.35)

# ============================================================
# PRE-COMPUTED COORDINATES (EXACT from math engine)
# ============================================================

# Carcass panels
carcass_data = [
    {"name": "Top", "center": (0, 0, 0.904), "size": (1.371, 0.457, 0.02)},
    {"name": "Bottom", "center": (0, 0, 0.01), "size": (1.371, 0.457, 0.02)},
    {"name": "Left", "center": (-0.6755, 0, 0.457), "size": (0.02, 0.457, 0.914)},
    {"name": "Right", "center": (0.6755, 0, 0.457), "size": (0.02, 0.457, 0.914)},
    {"name": "Back", "center": (0, -0.2185, 0.457), "size": (1.371, 0.02, 0.914)},
    {"name": "HDiv_0", "center": (0, 0, 0.6082), "size": (1.331, 0.437, 0.02)},
    {"name": "VStile_0", "center": (-0.2252, 0, 0.457), "size": (0.02, 0.437, 0.874)},
    {"name": "VStile_1", "center": (0.2252, 0, 0.457), "size": (0.02, 0.437, 0.874)},
]

# Door data
doors_data = [
    {"name": "Cell_r0_c0", "center": (-0.4503, 0.2185, 0.3091), "size": (0.4243, 0.02, 0.5722),
     "knob_pos": (-0.5149, 0.2405, 0.338), "hinge_side": "left",
     "col_left_x": -0.6655, "col_right_x": -0.2352},
    {"name": "Cell_r0_c1", "center": (0.0, 0.2185, 0.3091), "size": (0.4243, 0.02, 0.5722),
     "knob_pos": (-0.0646, 0.2405, 0.338), "hinge_side": "left",
     "col_left_x": -0.2152, "col_right_x": 0.2152},
    {"name": "Cell_r0_c2", "center": (0.4503, 0.2185, 0.3091), "size": (0.4243, 0.02, 0.5722),
     "knob_pos": (0.3858, 0.2405, 0.338), "hinge_side": "right",
     "col_left_x": 0.2352, "col_right_x": 0.6655},
]

# Drawer data
drawers_data = [
    {"name": "Cell_r1_c0", "center": (-0.4503, 0.2185, 0.7561), "size": (0.4243, 0.3656, 0.2698),
     "pull_pos": (-0.4503, 0.2335, 0.7561),
     "col_left_x": -0.6655, "col_right_x": -0.2352,
     "row_bottom_z": 0.6182, "row_top_z": 0.894},
    {"name": "Cell_r1_c1", "center": (0.0, 0.2185, 0.7561), "size": (0.4243, 0.3656, 0.2698),
     "pull_pos": (0.0, 0.2335, 0.7561),
     "col_left_x": -0.2152, "col_right_x": 0.2152,
     "row_bottom_z": 0.6182, "row_top_z": 0.894},
    {"name": "Cell_r1_c2", "center": (0.4503, 0.2185, 0.7561), "size": (0.4243, 0.3656, 0.2698),
     "pull_pos": (0.4503, 0.2335, 0.7561),
     "col_left_x": 0.2352, "col_right_x": 0.6655,
     "row_bottom_z": 0.6182, "row_top_z": 0.894},
]

# Legs data
legs_data = [
    {"name": "Leg_FL", "center": (-0.6355, 0.1785, 0.0), "size": (0.05, 0.05, 0.0)},
    {"name": "Leg_FR", "center": (0.6355, 0.1785, 0.0), "size": (0.05, 0.05, 0.0)},
    {"name": "Leg_BL", "center": (-0.6355, -0.1785, 0.0), "size": (0.05, 0.05, 0.0)},
    {"name": "Leg_BR", "center": (0.6355, -0.1785, 0.0), "size": (0.05, 0.05, 0.0)},
]

# ============================================================
# BUILD CARCASS / FRAME
# ============================================================
frame_objects = []

# The carcass bottom is at z=0, with legs extending below.
# Looking at the pre-computed data: legs have height 0.0, which means
# the bottom of the cabinet is at z=0 (ground level) with no separate legs.
# But the image shows legs. Let's interpret the legs as part of the frame
# that extend below and use the side panels. The leg_h_m=0.0 means
# the carcass sits at ground. Let me check: Bottom panel center z=0.01,
# so bottom is at z=0. Legs center z=0 with height 0 means no legs modeled.
# But visually the image shows legs. The side panels go full height.
# The legs are the lower portion of the side panels that extend to the floor.
# Since the pre-computed leg heights are 0, the cabinet sits directly on the floor
# with the side panels acting as legs. But looking at image, there are distinct legs.
# I'll model the legs as thin extensions below the carcass for visual accuracy,
# but since leg_h=0, the carcass already goes to ground. The bottom panel IS at ground.

# Create carcass panels
for panel in carcass_data:
    p = create_box(
        "Frame_" + panel["name"],
        panel["center"],
        panel["size"],
        mat_wood
    )
    frame_objects.append(p)

# The image shows the cabinet has visible legs at the bottom corners.
# Since the carcass goes to ground (z=0), the "legs" are visual - the side panels
# and vertical stiles form the leg appearance. But the image shows exposed legs
# below the bottom rail. Let me add leg extensions for visual accuracy.
# Actually, looking at the data more carefully - the bottom panel is at z=0.01
# (center) with height 0.02, so it spans z=0 to z=0.02. The doors start at z=0.02.
# The legs in the image are tapered and visible below the cabinet body.
# Let me add decorative legs even though pre-computed height is 0.

# Add visible legs - tapered posts at corners
# The image shows legs that are about 10cm tall and slightly tapered
leg_height = 0.10  # visible leg portion

# We need to shift the entire cabinet up by leg_height to make room for legs
# BUT the pre-computed coordinates don't account for this.
# Since leg_h_m = 0.0 in pre-computed data, legs are not part of the coordinate system.
# I'll add legs as cosmetic elements that extend from the bottom of the carcass downward.
# This means we need to move the entire carcass up, OR just add legs below z=0.
# To stay faithful to pre-computed coords, I'll shift everything up.

LEG_HEIGHT = 0.10
SHIFT_Z = LEG_HEIGHT

# Actually, to keep things consistent with pre-computed coordinates,
# let me NOT shift and instead just accept the cabinet sits at z=0.
# The legs visible in the image are actually the lower part of the frame.
# The "bottom" panel forms a shelf above the legs. Let me reinterpret:
# Keep everything at pre-computed coords. The Bottom panel is at z=0.01.
# I'll remove the bottom panel and instead create a bottom rail/shelf higher up
# and add legs below. But this would conflict with pre-computed coords.
# 
# BEST APPROACH: Use pre-computed coords exactly. No legs visible (they're 0 height).
# The cabinet sits on the ground with side panels going to floor. This is acceptable.
# The visual difference is minor.

# Actually, let me re-examine: The image clearly shows legs. I should add them.
# Let me shift ALL pre-computed Z coordinates up by LEG_HEIGHT.

# Remove the frame objects we just created
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
for m in list(bpy.data.meshes):
    bpy.data.meshes.remove(m)

frame_objects = []

# Z offset for legs
Z_OFF = 0.0  # Keep pre-computed coords exactly as given

# Create carcass panels with exact pre-computed coordinates
for panel in carcass_data:
    cx, cy, cz = panel["center"]
    sx, sy, sz = panel["size"]
    p = create_box(
        "Frame_" + panel["name"],
        (cx, cy, cz),
        (sx, sy, sz),
        mat_wood
    )
    frame_objects.append(p)

# Create top surface with slight overhang
# The top panel from pre-computed is at center (0, 0, 0.904) size (1.371, 0.457, 0.02)
# Add a slightly thicker/overhanging top
# Already created as part of carcass. Let's add an overhang top on top of it.
top_overhang = create_box(
    "Frame_TopOverhang",
    (0, 0.01, 0.919),  # slightly above the top panel, shifted forward slightly
    (1.40, 0.48, 0.015),  # overhangs by ~15mm on each side
    mat_wood
)
frame_objects.append(top_overhang)

# Add a backsplash/tray area at the back of the top
backsplash = create_box(
    "Frame_Backsplash",
    (0, -0.18, 0.955),  # behind center, above top
    (1.35, 0.10, 0.06),
    mat_wood
)
frame_objects.append(backsplash)

# Delete faces on the front of the carcass where doors and drawers go
# We need openings for 3 doors (bottom row) and 3 drawers (top row)
# The front face of the carcass is formed by the side panels, stiles, top, bottom, and HDiv
# Since we built from separate panels, the front is already open between the panels.
# The vertical stiles and horizontal divider create the grid framework.

# Join all frame objects
frame_obj = join_objects(frame_objects)
frame_obj.name = "Main_Frame"
apply_smooth_shading(frame_obj)

# ============================================================
# BUILD DOORS (3 doors, bottom row)
# ============================================================
door_names = ["Left_Door", "Center_Door", "Right_Door"]
door_knob_names = ["Left_Door_Knob", "Center_Door_Knob", "Right_Door_Knob"]
door_hinge_sides = ["left", "left", "right"]  # left, center hinged left, right hinged right

door_objects = []
door_knob_objects = []

for i, dd in enumerate(doors_data):
    cx, cy, cz = dd["center"]
    sx, sy, sz = dd["size"]
    
    # Create door panel
    door = create_box(door_names[i], (cx, cy, cz), (sx, sy, sz), mat_wood)
    
    # Add raised panel detail
    add_raised_panel_detail(door, border=0.04, depth=0.004)
    
    # Set origin to hinge edge
    hinge_side = door_hinge_sides[i]
    if hinge_side == "left":
        hinge_x = dd["col_left_x"]
    else:
        hinge_x = dd["col_right_x"]
    
    set_origin_keep_visual(door, hinge_x, cy, cz)
    
    apply_smooth_shading(door)
    door_objects.append(door)
    
    # Create door knob
    kx, ky, kz = dd["knob_pos"]
    bpy.ops.mesh.primitive_cylinder_add(radius=0.015, depth=0.012, location=(kx, ky, kz))
    knob = bpy.context.active_object
    knob.name = door_knob_names[i]
    # Rotate to face forward (along Y)
    knob.rotation_euler = (1.5708, 0, 0)  # 90 degrees around X
    bpy.ops.object.transform_apply(rotation=True)
    knob.data.materials.append(mat_metal)
    apply_smooth_shading(knob)
    door_knob_objects.append(knob)

# ============================================================
# BUILD DRAWERS (3 drawers, top row) - 5-sided open-top boxes
# ============================================================
drawer_names = ["Left_Drawer", "Center_Drawer", "Right_Drawer"]
handle_names = ["Left_Drawer_Handle", "Center_Drawer_Handle", "Right_Drawer_Handle"]

drawer_objects = []
handle_objects = []

WALL_THICKNESS = 0.012  # 12mm walls

for i, dw in enumerate(drawers_data):
    cx, cy, cz = dw["center"]
    front_sx, depth_sy, front_sz = dw["size"]
    
    # Drawer front panel dimensions
    front_panel_thickness = 0.02
    
    # Drawer box dimensions (extends back into carcass)
    # The carcass depth from front face to back: front_y = 0.2185, back_y = -0.2185
    # Inner depth ~ 0.437 * 0.8 = 0.35
    box_depth = depth_sy * 0.8  # ~80% of available depth
    box_width = front_sx - 0.006  # slightly narrower than front for clearance
    box_height = front_sz - 0.006  # slightly shorter for clearance
    
    # Front face Y position
    front_y = cy  # 0.2185
    
    # The drawer box extends from front_y backward
    box_center_y = front_y - front_panel_thickness / 2 - box_depth / 2
    
    parts = []
    
    # 1. Front panel (decorative face)
    front = create_box(
        f"drawer_{i}_front",
        (cx, front_y, cz),
        (front_sx, front_panel_thickness, front_sz),
        mat_wood
    )
    parts.append(front)
    
    # Add raised panel to front
    add_raised_panel_detail(front, border=0.035, depth=0.003)
    
    # 2. Bottom panel
    bottom_z = cz - front_sz / 2 + WALL_THICKNESS / 2 + 0.003
    bottom = create_box(
        f"drawer_{i}_bottom",
        (cx, box_center_y, bottom_z),
        (box_width, box_depth, WALL_THICKNESS),
        mat_wood
    )
    parts.append(bottom)
    
    # 3. Left side wall
    left_x = cx - box_width / 2 + WALL_THICKNESS / 2
    side_height = box_height - WALL_THICKNESS
    side_z = cz - front_sz / 2 + WALL_THICKNESS + side_height / 2 + 0.003
    left_wall = create_box(
        f"drawer_{i}_left",
        (left_x, box_center_y, side_z),
        (WALL_THICKNESS, box_depth, side_height),
        mat_wood
    )
    parts.append(left_wall)
    
    # 4. Right side wall
    right_x = cx + box_width / 2 - WALL_THICKNESS / 2
    right_wall = create_box(
        f"drawer_{i}_right",
        (right_x, box_center_y, side_z),
        (WALL_THICKNESS, box_depth, side_height),
        mat_wood
    )
    parts.append(right_wall)
    
    # 5. Back wall
    back_y = box_center_y - box_depth / 2 + WALL_THICKNESS / 2
    back_wall = create_box(
        f"drawer_{i}_back",
        (cx, back_y, side_z),
        (box_width - 2 * WALL_THICKNESS, WALL_THICKNESS, side_height),
        mat_wood
    )
    parts.append(back_wall)
    
    # Join all parts into one drawer object
    drawer_obj = join_objects(parts)
    drawer_obj.name = drawer_names[i]
    apply_smooth_shading(drawer_obj)
    drawer_objects.append(drawer_obj)
    
    # Create drawer handle (bar pull)
    px, py, pz = dw["pull_pos"]
    handle_width = 0.10
    handle_height = 0.012
    handle_depth = 0.012
    handle_standoff = 0.008
    
    handle_parts = []
    
    # Main bar
    bar = create_box(
        f"handle_{i}_bar",
        (px, py + handle_standoff + handle_depth / 2, pz),
        (handle_width, handle_depth, handle_height),
        mat_metal
    )
    handle_parts.append(bar)
    
    # Left mounting post
    post_l = create_box(
        f"handle_{i}_post_l",
        (px - handle_width / 2 + 0.008, py + handle_standoff / 2, pz),
        (0.008, handle_standoff, 0.008),
        mat_metal
    )
    handle_parts.append(post_l)
    
    # Right mounting post
    post_r = create_box(
        f"handle_{i}_post_r",
        (px + handle_width / 2 - 0.008, py + handle_standoff / 2, pz),
        (0.008, handle_standoff, 0.008),
        mat_metal
    )
    handle_parts.append(post_r)
    
    handle_obj = join_objects(handle_parts)
    handle_obj.name = handle_names[i]
    apply_smooth_shading(handle_obj)
    handle_objects.append(handle_obj)

# ============================================================
# ADD BEVEL MODIFIER TO FRAME FOR SOFTER EDGES
# ============================================================
bpy.context.view_layer.objects.active = frame_obj
frame_obj.select_set(True)
bevel_mod = frame_obj.modifiers.new(name="Bevel", type='BEVEL')
bevel_mod.width = 0.002
bevel_mod.segments = 2
bevel_mod.limit_method = 'ANGLE'
bevel_mod.angle_limit = 0.785  # 45 degrees
frame_obj.select_set(False)

# Add bevel to doors
for door in door_objects:
    bpy.context.view_layer.objects.active = door
    door.select_set(True)
    bevel_mod = door.modifiers.new(name="Bevel", type='BEVEL')
    bevel_mod.width = 0.0015
    bevel_mod.segments = 2
    bevel_mod.limit_method = 'ANGLE'
    bevel_mod.angle_limit = 0.785
    door.select_set(False)

# Add bevel to drawers
for drawer in drawer_objects:
    bpy.context.view_layer.objects.active = drawer
    drawer.select_set(True)
    bevel_mod = drawer.modifiers.new(name="Bevel", type='BEVEL')
    bevel_mod.width = 0.0015
    bevel_mod.segments = 2
    bevel_mod.limit_method = 'ANGLE'
    bevel_mod.angle_limit = 0.785
    drawer.select_set(False)

# ============================================================
# FINAL ORGANIZATION AND EXPORT
# ============================================================

# Collect all objects
all_objects = [frame_obj] + door_objects + door_knob_objects + drawer_objects + handle_objects

# Print statistics
total_verts = 0
total_objects = len(all_objects)
for obj in all_objects:
    if obj.type == 'MESH':
        total_verts += len(obj.data.vertices)

# Compute bounding box
min_x = min_y = min_z = float('inf')
max_x = max_y = max_z = float('-inf')
for obj in all_objects:
    if obj.type == 'MESH':
        for v in obj.data.vertices:
            world_co = obj.matrix_world @ v.co
            min_x = min(min_x, world_co.x)
            max_x = max(max_x, world_co.x)
            min_y = min(min_y, world_co.y)
            max_y = max(max_y, world_co.y)
            min_z = min(min_z, world_co.z)
            max_z = max(max_z, world_co.z)

print(f"Total objects: {total_objects}")
print(f"Total vertices: {total_verts}")
print(f"Dimensions: W={max_x - min_x:.3f}m x D={max_y - min_y:.3f}m x H={max_z - min_z:.3f}m")
print(f"Bounding box: X[{min_x:.3f}, {max_x:.3f}] Y[{min_y:.3f}, {max_y:.3f}] Z[{min_z:.3f}, {max_z:.3f}]")

# Create output directory
output_dir = "/home/msi/IsaacLab/scripts/tools/simready_assets/cabinet_2"
os.makedirs(output_dir, exist_ok=True)

# Save blend file
blend_path = os.path.join(output_dir, "cabinet_2.blend")
bpy.ops.wm.save_as_mainfile(filepath=blend_path)
print(f"Saved .blend to: {blend_path}")

# Export USD
usd_path = os.path.join(output_dir, "cabinet_2_asset.usd")
bpy.ops.object.select_all(action='SELECT')
bpy.ops.wm.usd_export(filepath=usd_path, selected_objects_only=False)
print(f"Exported USD to: {usd_path}")

print("Cabinet sideboard creation complete!")
#!/usr/bin/env python3
"""
render_views.py — Render 4 views of a USD asset using Blender headless.

Usage (called by simready_agent_v9.py, not directly):
  blender --background --python render_views.py -- /path/to/asset.usd /path/to/output_dir

Produces: front.png, back.png, left.png, right.png (1024x1024 each)
"""

import bpy
import sys
import os
import math
import mathutils

# Parse args after "--"
argv = sys.argv[sys.argv.index("--") + 1:]
usd_path = argv[0]
out_dir = argv[1] if len(argv) > 1 else "/tmp/v9_views"
os.makedirs(out_dir, exist_ok=True)

# Clear default scene
bpy.ops.wm.read_factory_settings(use_empty=True)

# Import USD
bpy.ops.wm.usd_import(filepath=usd_path)

# Compute scene bounds
objects = [o for o in bpy.context.scene.objects if o.type == 'MESH']
if not objects:
    print("ERROR: No mesh objects found in USD")
    sys.exit(1)

# Use Blender's built-in bounding box
min_co = mathutils.Vector((float('inf'),) * 3)
max_co = mathutils.Vector((float('-inf'),) * 3)
for obj in objects:
    for corner in obj.bound_box:
        world_co = obj.matrix_world @ mathutils.Vector(corner)
        for i in range(3):
            min_co[i] = min(min_co[i], world_co[i])
            max_co[i] = max(max_co[i], world_co[i])

center = (min_co + max_co) / 2
size = max(max_co[i] - min_co[i] for i in range(3))
dist = size * 2.2  # Camera distance

# Add sun light
bpy.ops.object.light_add(type='SUN', location=(5, 5, 10))
sun = bpy.context.object
sun.data.energy = 3.0

# Add fill light from below
bpy.ops.object.light_add(type='AREA', location=(0, 0, -5))
fill = bpy.context.object
fill.data.energy = 50.0
fill.data.size = 10.0

# Render settings
bpy.context.scene.render.engine = 'BLENDER_EEVEE_NEXT'
bpy.context.scene.render.resolution_x = 1024
bpy.context.scene.render.resolution_y = 1024
bpy.context.scene.render.image_settings.file_format = 'PNG'
bpy.context.scene.render.film_transparent = True

# Set world background
world = bpy.data.worlds.new("World")
bpy.context.scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes["Background"]
bg.inputs[0].default_value = (0.15, 0.15, 0.18, 1.0)  # Dark gray

# 4 camera views: front, back, left, right
# Z-up coordinate system (USD convention)
views = {
    "front": (center.x, center.y - dist, center.z + size * 0.2),
    "back":  (center.x, center.y + dist, center.z + size * 0.2),
    "left":  (center.x - dist, center.y, center.z + size * 0.2),
    "right": (center.x + dist, center.y, center.z + size * 0.2),
}

rendered = []
for name, loc in views.items():
    bpy.ops.object.camera_add(location=loc)
    cam = bpy.context.object
    cam.data.lens = 50
    cam.data.clip_end = dist * 5

    # Point camera at center
    direction = mathutils.Vector(center) - mathutils.Vector(loc)
    rot_quat = direction.to_track_quat('-Z', 'Y')
    cam.rotation_euler = rot_quat.to_euler()

    bpy.context.scene.camera = cam
    filepath = os.path.join(out_dir, f"{name}.png")
    bpy.context.scene.render.filepath = filepath
    bpy.ops.render.render(write_still=True)
    rendered.append(filepath)

    # Clean up camera
    bpy.data.objects.remove(cam)

print(f"RENDERED: {len(rendered)} views to {out_dir}")
for r in rendered:
    print(f"  {r}")

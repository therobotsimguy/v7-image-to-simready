# V7 Blender Lessons (Stage D)

## Cylinder/Knob Origin Bug
**Problem:** `primitive_cylinder_add(location=(x,y,z))` + `transform_apply(rotation=True)` resets `obj.location` to (0,0,0).
**Fix:** Create cylinder at origin (0,0,0), apply all transforms, then set `obj.location = (x,y,z)` at the end.

## Parenting Strategy
Use `matrix_parent_inverse = parent.matrix_world.inverted()` explicitly after `child.parent = parent`.
Call `bpy.context.view_layer.update()` before the parent block to ensure matrix_world is current.

## Joint Pivot = Object Origin (Critical)
**From knowledge base:** Origin = joint pivot. The physics body frame origin must be AT the joint pivot.
- For revolute doors: shift origin to hinge edge via `set_origin_keep_visual(obj, ±w/2, 0, 0)`
- For prismatic drawers: shift origin to back face via `set_origin_keep_visual(obj, 0, -d/2, 0)`
- This makes USD export `Xform translate = pivot world position`
- Stage F can then use `localPos1 = (0,0,0)` (origin IS the pivot) ✓

## set_origin_keep_visual
Shifts Blender origin by relative offset WITHOUT moving geometry visually:
```python
def set_origin_keep_visual(obj, rel_x, rel_y, rel_z):
    delta = Vector((rel_x, rel_y, rel_z))
    obj.location = obj.location + delta
    for v in obj.data.vertices:
        v.co -= delta
```

## USD Export Behavior
- Box objects (transform_apply(scale) only): position baked into mesh vertices, NO Xform translate in USD
- Cylinder objects (set location after transforms): position stored as Xform translate in USD
- After pivot-origin shift: revolute/prismatic objects export WITH correct Xform translate = pivot position

## Coordinate System (cabinet example)
- X: left(-) to right(+)
- Y: back(-) to front(+) — depth axis, drawers slide in +Y
- Z: bottom to top — height axis, revolute doors rotate around Z

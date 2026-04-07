# SimReady Asset Pipeline — V7

## Current Work
Branch: `simready-asset-generator`
Active example: `scripts/tools/simready_assets/cabinet_2_v7/`

## Pipeline Stages
`stage_a` → `stage_b` → `stage_c` (spec) → `stage_d` (Blender) → `stage_e` → `stage_f` (USD physics)

Key files:
- `scripts/tools/simready_assets/v7/stage_d.py` — Blender script generator
- `scripts/tools/simready_assets/v7/stage_f.py` — USD physics layer
- `scripts/tools/simready_assets/cabinet_2_v7/stage_c.json` — current spec

## Critical Rules (hard-won lessons)

### Blender
- **Cylinder bug**: `primitive_cylinder_add(location=X)` + `transform_apply(rotation=True)` resets obj.location to (0,0,0). Fix: create at (0,0,0), apply all transforms, THEN set obj.location.
- **Joint pivot = object origin**: USD exports Xform translate = obj.location. Physics body frame origin = pivot. So obj.location MUST be at the hinge/slide-start.
- **How to set pivot correctly**: Create cube at pivot world position, shift vertices so geometry appears at center. Do NOT use set_origin_keep_visual + parenting (loses Y/Z components in USD).
  - ROTATIONAL left hinge: create at `(cx - w/2, y, z)`, shift vertices `v.co.x += w/2`
  - ROTATIONAL right hinge: create at `(cx + w/2, y, z)`, shift vertices `v.co.x -= w/2`
  - LINEAR_TRANSLATIONAL: create at `(x, cy - d/2, z)`, shift vertices `v.co.y += d/2`
- **Parenting**: Use `matrix_parent_inverse = parent.matrix_world.inverted()` AFTER `view_layer.update()`. But avoid parenting doors/drawers — it complicates USD Xform positions.

### USD Physics (stage_f.py)
- **ArticulationRootAPI only on root** — NO RigidBodyAPI on root (that makes it float)
- **RigidBodyAPI only on direct children of root** — grandchildren (handles, knobs) get NO RigidBodyAPI (nested rigid body error)
- **Joint localPos0** = pivot position in parent body's local frame = `hinge_world - parent_body_world`
- **Joint localPos1** = `(0,0,0)` — door/drawer origin IS the pivot
- **RevoluteJoint axis** = "Z" for vertical door hinges
- **PrismaticJoint axis** = "Y" for drawers (depth direction)

### Running
```bash
# Regenerate blender script (dry run, no Blender needed):
python scripts/tools/simready_assets/v7/stage_d.py \
  --stage_c scripts/tools/simready_assets/cabinet_2_v7/stage_c.json \
  --output_dir scripts/tools/simready_assets/cabinet_2_v7 --dry_run

# Re-run physics layer:
python scripts/tools/simready_assets/v7/stage_f.py \
  --stage_c scripts/tools/simready_assets/cabinet_2_v7/stage_c.json \
  --input_usd scripts/tools/simready_assets/cabinet_2_v7/teak_outdoor_sideboard.usd \
  --output_dir scripts/tools/simready_assets/cabinet_2_v7

# Load in Isaac Sim:
./isaaclab.sh -p scripts/tools/simready_assets/cabinet_2_v7/load_in_isaacsim.py
```

### GitHub
Remote: `https://github.com/therobotsimguy/v7-image-to-simready`
Only commit files directly changed by current task.

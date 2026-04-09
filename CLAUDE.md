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
- **transform_apply defaults bug**: `transform_apply(scale=True)` silently also applies location and rotation (all default to `True`). This bakes `obj.location` into vertices and resets it to (0,0,0). **Always** use `transform_apply(location=False, rotation=False, scale=True)` when you only want to apply scale.
- **Cylinder bug**: `primitive_cylinder_add(location=X)` + `transform_apply(rotation=True)` resets obj.location to (0,0,0). Fix: create at (0,0,0), apply all transforms, THEN set obj.location.
- **Joint pivot = object origin**: USD exports Xform translate = obj.location. Physics body frame origin = pivot. So obj.location MUST be at the hinge/slide-start.
- **How to set pivot correctly**: Create cube at pivot world position, apply scale only (`location=False, rotation=False, scale=True`), shift vertices so geometry appears at center. Do NOT use set_origin_keep_visual + parenting (loses Y/Z components in USD).
  - ROTATIONAL left hinge: create at `(cx - w/2, y, z)`, shift vertices `v.co.x += w/2`
  - ROTATIONAL right hinge: create at `(cx + w/2, y, z)`, shift vertices `v.co.x -= w/2`
  - LINEAR_TRANSLATIONAL: create at `(x, cy - d/2, z)`, shift vertices `v.co.y += d/2`
- **Parenting**: After `view_layer.update()`, `child.parent = parent`, then `child.matrix_parent_inverse = parent.matrix_world.inverted()` (matches RNA / Keep Transform). Multiplying by `child.matrix_world` in that assignment is **wrong** and disjoints the cabinet (Blender 4.3). Optional: avoid parenting doors/drawers for simpler USD Xforms.

### USD Physics (stage_f.py)
- **Flat hierarchy required**: PhysX swallows nested RigidBodyAPI prims into the parent body. Movable parts (drawers, doors) MUST be **siblings** of `main_body` under `/root`, NOT children. Only structural parts (shelf, dividers) that use FixedJoints can be children.
- **ArticulationRootAPI on `/root`** (scene root Xform, no RigidBodyAPI) — not on `main_body`.
- **Root body (`main_body`) = kinematic RigidBodyAPI** (`physics:kinematicEnabled = true`). Stays at spawn position without a world anchor joint. No FixedJoint to world needed.
- **No PhysicsScene or simulationOwner in the asset USD** — when referenced into Isaac Lab, absolute paths like `/physicsScene` are outside the reference scope and USD rejects them. The host app provides the physics scene; PhysX auto-discovers bodies.
- **Collision: follow `.cursor/skills/simready-collision/SKILL.md`** — body: `convexDecomposition` on largest mesh + `convexHull` on rest. Doors/drawers: `convexHull` largest only. Handles: always `convexHull`. Wheels: `convexHull` all meshes. NEVER single-mesh `convexHull` on concave body (invisible cloak). Empirically verified on trolley + fridge.
- **Blender: do NOT parent drawers/doors to `main_body`** — Blender parenting = nested USD prims = PhysX swallows them. Leave movers unparented; only parent handles to their drawer.
- **Isaac Lab: use AssetBaseCfg, NOT ArticulationCfg** — ArticulationCfg creates an ArticulationView that writes joint targets every step, overriding shift+drag forces. AssetBaseCfg spawns during scene construction (before physics init) so PhysX picks it up. Direct post-env stage spawning causes delayed physics (only works on pause/play).
- **NO RigidBodyAPI on grandchildren** (handles, knobs) — nested rigid body error.
- **Joint localPos**: use `_world_point_to_local_body` on both parent and child. Use `_mesh_world_bbox_via_vertices` (vertex-based), NOT `BBoxCache.ComputeWorldBound`.
- **Joint drives**: low damping (5.0 prismatic, 2.0 revolute), stiffness=0.
- **RevoluteJoint axis** = "Z" for vertical door hinges
- **PrismaticJoint axis** = "Y" for drawers (depth direction)

### make_simready.py (USD → SimReady, no Blender)
- `scripts/tools/simready_assets/make_simready.py` — takes any well-named USD, adds physics
- Classifies parts by name (door → revolute, drawer → prismatic, everything else → structural)
- Reparents movable Xforms to be siblings (flat hierarchy for PhysX)
- Reads **pivot translate** (`xformOp:translate:pivot`) as joint anchor — common in DCC exports where pivot+invert cancel out but mark the rotation center
- **localPos1 must use `world_point_to_local`** — NOT (0,0,0) — because pivot-translate patterns leave the body frame at world origin, not at the pivot
- **Colliders: follow simready-collision skill** — every interaction point must have a collider. Body gets per-mesh convexHull. Doors get largest-only. Handles always. Wheels get all meshes.
- **L2W flattening: clear xform ops on BOTH Xform AND Mesh prims** — DCC exports (Omniverse, Blender) often put translate/rotate/scale/pivot on Mesh prims, not just parent Xforms. After baking L2W into vertices, clearing only `Xform` type prims leaves mesh-level transforms intact → double transformation → pieces scatter across the scene. Fix: `if prim.GetTypeName() in ("Xform", "Mesh"): ClearXformOpOrder()`.
- **Recompute `extent` after L2W vertex transform** — the `extent` attribute is the renderer's bounding box hint. After L2W-transforming vertices from cm to meters, stale extents are 85-110× too large. This causes PhysX broad-phase and RTX BVH to allocate oversized spatial structures. Fix: recompute extent from actual vertex min/max immediately after `pts_attr.Set(world_pts)`.
- **Wheel structural parts (fixer, body, bolts) must be under body Xform** — only rotating parts (tire, disc, detail) stay under the wheel Xform. Structural bracket/fork parts are children of body so they stay attached to the frame. Step 1c in `make_simready.py` does this via keyword matching: `rotating_keywords = ["tire", "disc", "detail"]`.
- **Wheel joint limits must be unlimited** — use `[-9999, 9999]` not `[-180, 180]`. Limited range stops the wheel after half a turn.
- **Wheel joint anchor = tire bounding box center** — NOT the Xform translate or pivot. Compute as `((xmin+xmax)/2, (ymin+ymax)/2, (zmin+zmax)/2)` from tire mesh vertices. Off-center anchors cause the wheel to orbit and clip through the bracket.
- **Wheel collision: `convexDecomposition` on ALL meshes** (tire + disc + detail) — `convexHull` wraps them into blobs with poor ground contact. Working trolley uses `convexDecomposition` on all 12 wheel meshes.
- **Body collision: `convexDecomposition` on ALL body meshes** with high quality params — `convexHull` on a concave body (trolley frame with rails) creates an invisible cloak. Set `maxConvexHulls=128`, `voxelResolution=500000`, `errorPercentage=1.0` for tight rail collision.
- **Friction via `material:binding:physics`** — per-mesh `PhysicsMaterialAPI` attributes alone are NOT enough. PhysX resolves friction through `material:binding:physics` relationship pointing to a `PhysicsMaterial` prim (sf=1.0, df=0.9 for rubber). Without this binding, tires slide on ground instead of rolling.
- **contactOffset = 0.0005** (0.5mm) in teleop script — Isaac Sim default is ~20mm which creates visible grip gap. Set on ALL collision shapes at runtime via teleop script. Do NOT set in the asset USD (teleop overwrites it).

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

### Skills Rule (ALWAYS follow — use ALL THREE skills on every asset)
When processing any SimReady asset, apply all three skills:

1. **simready-behaviors** — Read `.cursor/skills/simready-behaviors/SKILL.md`. Use the 16 behaviors to classify each part (not just "door"/"drawer"). Match joint type, axis, limits, damping from the behavior table. Don't force everything through one template.

2. **robot-model** — Read `.cursor/skills/robot-model/SKILL.md`. Validate every parameter against Franka specs: can the robot exert enough force? Is the handle graspable (< 80mm)? Is friction within limits? Is the interaction point within reach?

3. **simready-math** — Read `.cursor/skills/simready-math/SKILL.md`. Use `geometry.py`, `units.py`, `transforms.py` for ALL numerical computations. Divergence theorem for mass. Deterministic answers only — no mental arithmetic.

**The skills are not documentation — they are checklists. Run through them.**

### Delivery Rule (ALWAYS follow)
After any SimReady asset is built/converted, ALWAYS provide TWO ready-to-run commands:
1. **Standalone** — load the asset by itself with ground plane, verify shift+drag works
2. **With robot** — load next to Franka, 80cm away, verify both robot teleop and asset drag work

```bash
# Standalone
./isaaclab.sh -p load_in_isaacsim.py --usd /path/to/asset_physics.usd

# With robot (80cm away)
./isaaclab.sh -p load_with_robot.py --usd /path/to/asset_physics.usd --device cpu
```

### GitHub
- V7: `https://github.com/therobotsimguy/v7-image-to-simready`
- V8: `https://github.com/therobotsimguy/v8-usd-to-simready`
Only commit files directly changed by current task.

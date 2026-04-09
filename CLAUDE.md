# SimReady Asset Pipeline — V8

## Architecture
```
[Raw USD] → [generate_manifest.py --llm] → manifest.json → [make_simready.py] → asset_physics.usd
```

- **generate_manifest.py** reads USD hierarchy, sends to LLM API, outputs manifest.json
- **manifest.json** declares body, movable parts, joint types — no name-guessing in the script
- **make_simready.py** is a pure physics applicator: collision, rigid body, friction, joints; **L2W flatten is opt-in** (`--flatten-l2w`) — default keeps the DCC xform stack and reparents nested movables with world-origin preserved

Key files:
- `scripts/tools/simready_assets/generate_manifest.py` — USD hierarchy → manifest.json (via LLM or dump)
- `scripts/tools/simready_assets/make_simready.py` — manifest-driven physics applicator
- `scripts/tools/simready_assets/open_usd_in_isaacsim.py` — standalone USD viewer
- `scripts/environments/teleoperation/teleop_se3_agent_cinematic.py` — robot teleop + testing

## Critical Rules (hard-won lessons)

### USD Physics
- **Flat hierarchy required**: PhysX swallows nested RigidBodyAPI prims into the parent body. Movable parts (drawers, doors) MUST be **siblings** of `main_body` under `/root`, NOT children. Only structural parts (shelf, dividers) that use FixedJoints can be children.
- **NO ArticulationRootAPI** — PhysX does not support articulations with kinematic bodies. Joints work as standalone USD physics joints. The kinematic body anchors everything; no articulation tree needed.
- **Root body (`main_body`) = kinematic RigidBodyAPI** (`physics:kinematicEnabled = true`). Stays at spawn position without a world anchor joint. No FixedJoint to world needed.
- **No PhysicsScene or simulationOwner in the asset USD** — when referenced into Isaac Lab, absolute paths like `/physicsScene` are outside the reference scope and USD rejects them. The host app provides the physics scene; PhysX auto-discovers bodies.
- **Collision: follow `.cursor/skills/simready-collision/SKILL.md`** — Two questions per mesh: (1) Concave and matters? → `convexDecomposition`; otherwise `convexHull`. Budget: ~5 decomp max per asset (42× decomp = hang). Quality attrs on decomp meshes >50K verts. (2) Robot touches it? → `GripMaterial` (sf=1.0, df=0.9) via `material:binding:physics`. contactOffset=0.0005 at runtime. NEVER `convexHull` on a single large concave mesh. Wheels: all decomp.
- **Isaac Lab: use AssetBaseCfg, NOT ArticulationCfg** — ArticulationCfg creates an ArticulationView that writes joint targets every step, overriding shift+drag forces. AssetBaseCfg spawns during scene construction (before physics init) so PhysX picks it up. Direct post-env stage spawning causes delayed physics (only works on pause/play).
- **NO RigidBodyAPI on grandchildren** (handles, knobs) — nested rigid body error.
- **Joint localPos**: use `world_point_to_local` on both parent and child. Use vertex-based bbox, NOT `BBoxCache.ComputeWorldBound`.
- **Joint drives**: low damping (5.0 prismatic, 2.0 revolute), stiffness=0.
- **RevoluteJoint axis** = "Z" for vertical door hinges
- **PrismaticJoint axis** = "Y" for drawers (depth direction)

### make_simready.py (USD → SimReady)
- `scripts/tools/simready_assets/make_simready.py` — reads manifest.json, applies physics (7 steps)
- Reads **manifest.json** for part-to-behavior mapping — no name-based classification
- Reparents movable Xforms to be siblings (flat hierarchy for PhysX). Without `--flatten-l2w`, reparent stores full L2W then applies `inv(parent_world)*world` as a single `transform` op so rotation + translation match the pre-reparent pose.
- **Joint anchors**: pivot xform ops are **local**; anchors must be `L2W * pivot_local` (not raw pivot components) or constraints explode.
- Reads **pivot translate** (`xformOp:translate:pivot`) as joint anchor — common in DCC exports where pivot+invert cancel out but mark the rotation center
- **localPos1 must use `world_point_to_local`** — NOT (0,0,0) — because pivot-translate patterns leave the body frame at world origin, not at the pivot
- **Colliders: follow simready-collision skill** — body gets colliders (Q1 logic). Handles always get colliders. Wheels get all meshes with convexDecomposition.
- **With `--flatten-l2w` only**: **L2W flattening** — clear xform ops on BOTH Xform AND Mesh prims (DCC exports often put ops on Mesh prims; partial clear → double transform → scatter). **Recompute `extent`** after vertex bake (stale extents blow bounds).
- **Friction via `material:binding:physics`** — per-mesh `PhysicsMaterialAPI` attributes alone are NOT enough. PhysX resolves friction through `material:binding:physics` relationship pointing to a `PhysicsMaterial` prim. GripMaterial (sf=1.0, df=0.9) on handles/knobs. Without this binding, gripper slides off.
- **contactOffset = 0.0005** (0.5mm) in teleop script — Isaac Sim default is ~20mm which creates visible grip gap. Set on ALL collision shapes at runtime via teleop script. Do NOT set in the asset USD (teleop overwrites it).
- **Wheel structural parts (fixer, body, bolts) must be under body Xform** — only rotating parts (tire, disc, detail) stay under the wheel Xform.
- **Wheel joint limits must be unlimited** — use `[-9999, 9999]` not `[-180, 180]`.
- **Wheel joint anchor = tire bounding box center** — NOT the Xform translate or pivot.
- **Wheel collision: `convexDecomposition` on ALL meshes** (tire + disc + detail) — `convexHull` creates blobs.

### Running
```bash
# Step 1: Generate manifest (LLM auto-classifies):
python scripts/tools/simready_assets/generate_manifest.py \
  --input /path/to/asset.usd --llm

# Or dump hierarchy for manual review:
python scripts/tools/simready_assets/generate_manifest.py \
  --input /path/to/asset.usd --dump

# Step 2: Apply physics (default: stable, no L2W bake):
python scripts/tools/simready_assets/make_simready.py \
  --input /path/to/asset.usd \
  --manifest /path/to/manifest.json
# Optional: bake meshes to world + rebase bodies (only if default mode misbehaves):
#   ... same command ... --flatten-l2w

# Step 3: View standalone:
./isaaclab.sh -p scripts/tools/simready_assets/open_usd_in_isaacsim.py \
  --usd /path/to/asset_physics.usd

# Step 4: Test with Franka teleop:
./isaaclab.sh -p scripts/environments/teleoperation/teleop_se3_agent_cinematic.py \
  --asset /path/to/asset_physics.usd --device cpu
```

### Skills Rule (ALWAYS follow — use ALL THREE skills on every asset)
When processing any SimReady asset, apply all three skills:

1. **simready-behaviors** — Read `.cursor/skills/simready-behaviors/SKILL.md`. Use the 16 behaviors to classify each part. Match joint type, axis, limits, damping from the behavior table. Output a manifest.json for make_simready.py.

2. **robot-model** — Read `.cursor/skills/robot-model/SKILL.md`. Validate every parameter against Franka specs: can the robot exert enough force? Is the handle graspable (< 80mm)? Is friction within limits? Is the interaction point within reach?

3. **simready-math** — Read `.cursor/skills/simready-math/SKILL.md`. Use `geometry.py`, `units.py`, `transforms.py` for ALL numerical computations. Divergence theorem for mass. Deterministic answers only — no mental arithmetic.

**The skills are not documentation — they are checklists. Run through them.**

### Delivery Rule (ALWAYS follow)
After any SimReady asset is built/converted, ALWAYS provide TWO ready-to-run commands:
1. **Standalone** — load the asset by itself, verify shift+drag works
2. **With robot** — load next to Franka, verify both robot teleop and asset drag work

```bash
# Standalone
./isaaclab.sh -p scripts/tools/simready_assets/open_usd_in_isaacsim.py \
  --usd /path/to/asset_physics.usd

# With robot
./isaaclab.sh -p scripts/environments/teleoperation/teleop_se3_agent_cinematic.py \
  --asset /path/to/asset_physics.usd --device cpu
```

### GitHub
- V8: `https://github.com/therobotsimguy/v8-usd-to-simready`
Only commit files directly changed by current task.

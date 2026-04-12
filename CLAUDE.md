# SimReady Asset Pipeline — V9

## Architecture
`[Raw USD] → simready_agent_v9.py → (Gemini vision + Claude classify + make_simready.py + MuJoCo validate) → asset_physics.usd`

Key files:
- `simready_agent_v9.py` (V9 orchestrator — Claude Agent SDK)
- `make_simready.py` (physics engine — called as CLI tool by V9)
- `validate_dynamics.py` (MuJoCo behavioral validation)
- `render_views.py` + `gemini_vision.py` (Blender + Gemini visual analysis)
- `teleop_se3_agent_cinematic.py` (robot test)

Location: `/home/msi/IsaacLab/scripts/tools/simready_assets/` (all files live here)
Raw assets: `/home/msi/Downloads/Not_simready/`
V8 repo (archived, no longer synced): `https://github.com/therobotsimguy/v8-usd-to-simready`

## Status
V9 tested: Refrigerator_B01_01 (7/7 + 20/20 behavioral) | InstrumentTrolley_B (7/7 + 11/11 behavioral)

## USD Physics Rules
- Flat hierarchy: movable parts = siblings of `main_body` under `/root` (PhysX swallows nested RigidBodyAPI)
- NO ArticulationRootAPI (incompatible with kinematic bodies)
- `main_body` = kinematic RigidBodyAPI (`physics:kinematicEnabled = true`); use `--dynamic` for draggable body (no kinematicEnabled)
- No PhysicsScene in asset USD (host app provides it)
- Collision & wheels (full rules + trolley tables): V8 **[PRINCIPLES_FRIDGE_TROLLEY.md](https://github.com/therobotsimguy/v8-usd-to-simready/blob/main/PRINCIPLES_FRIDGE_TROLLEY.md)**; `simready-collision` skill points there. Budget ~5 convexDecomposition max
- AssetBaseCfg, NOT ArticulationCfg (ArticulationView overrides shift+drag)
- NO RigidBodyAPI on grandchildren
- Joint localPos: `world_point_to_local`, vertex-based bbox (not BBoxCache)
- Drives: low damping (5 prismatic, 2 revolute), stiffness=0
- RevoluteJoint axis=Z (vertical hinges), PrismaticJoint axis=Y (drawers)

## make_simready.py Rules
- Reparent: depth-sorted, separate batch edits (SdfBatchNamespaceEdit crash prevention)
- Joint anchors: saved BEFORE reparent (reparent clears pivot ops). Pass `anchor_world=saved_anchors[name]` to `detect_hinge_edge`
- Drawer direction: auto-detect from body center
- Collision scope: body=recursive meshes, movable parts=direct children only (door junk meshes skipped by name — see PRINCIPLES_FRIDGE_TROLLEY.md)
- Mass clamp: revolute 2–100kg (fridge doors from bbox), prismatic 0.5–5kg, continuous 0.05–1kg
- Friction: `material:binding:physics` relationship required (not just PhysicsMaterialAPI attrs)
- contactOffset=0.00005 at runtime only, NOT in asset USD. Robot finger meshes get convexDecomposition at runtime (convexHull bloats fingers 66%)
- Wheels: structural parts (fixer/bolts/body) under body Xform; rotating parts (tire/disc/detail) under wheel Xform; limits [-9999,9999]; all convexDecomposition
- Wheel axis: detect from tire bbox — thin dimension = axle direction (Y if tire thin in Y, X if thin in X). LLM often gets this wrong.
- Wheel anchors: use tire bbox center after structural split, NOT DCC pivot (pivot marks caster swivel, not axle)
- Dynamic body mass: clamp to 5–100kg (trolley/shell drag). Auto-estimate overshoots on large bbox.

## Skills (use ALL on every asset)
1. **simready-behaviors** — classify parts, match joint params from 16 behavior types
2. **robot-model** — validate against Franka specs (force, grip, reach)
3. **simready-math** — all numerical computations (no mental arithmetic)

## Commands
```bash
# V9 full pipeline (classify + apply + validate — recommended)
python3 scripts/tools/simready_assets/simready_agent_v9.py --input /path/to/asset.usd
# V9 dynamic mode (trolleys / draggable body)
python3 scripts/tools/simready_assets/simready_agent_v9.py --input /path/to/asset.usd --dynamic
# Franka teleop (shift+drag doors/drawers/wheels)
ISAACLAB_PATH=/home/msi/IsaacLab ./isaaclab.sh -p scripts/environments/teleoperation/teleop_se3_agent_cinematic.py --asset /path/to/asset_physics.usd --device cpu
# Behavioral validation only (MuJoCo, headless)
python3 scripts/tools/simready_assets/validate_dynamics.py --input /path/to/asset_physics.usd
# make_simready.py directly (audit only, no agent)
python3 scripts/tools/simready_assets/make_simready.py --input /path/to/asset.usd
```

After every asset build, give the **Franka teleop** command with `ISAACLAB_PATH=/home/msi/IsaacLab` prefix and `--device cpu`.

## GitHub
- IsaacLab repo, branch `simready-asset-generator` — all V9 work lives here
- V8 repo (archived): `https://github.com/therobotsimguy/v8-usd-to-simready` — no longer synced
- Only commit files directly changed by current task

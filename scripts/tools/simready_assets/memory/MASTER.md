# SimReady Asset Pipeline — Complete Lessons & Memory
# All versions: V3 → V4 → V7
# Last updated: 2026-04-07

---

## SETUP

### Isaac Sim & Isaac Lab
- Freshly installed 2026-03-25
- Run command: `./isaaclab.sh -p <script.py>` from `/home/msi/IsaacLab`
- Teleop with Franka (CPU for joint sliders): `./isaaclab.sh -p scripts/environments/teleoperation/teleop_se3_agent_cinematic.py --asset <path> --asset_pos 0.8 0.0 0.0 --device cpu`
- GPU mode (DIRECT_GPU_API): joint sliders blocked, interact via tensor API or gripper only

### Blender MCP
- Startup order: Open Blender → verify MCP addon active → confirm "MCP Server started on port 9876" in terminal → then start Claude
- Direct socket (most reliable — MCP tool integration is flaky):
```python
import socket, json
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(300)
sock.connect(('localhost', 9876))
sock.sendall(json.dumps({"type": "execute_code", "params": {"code": "<blender python>"}}).encode())
```
- Quick test: `python3 -c "import socket; s=socket.socket(); s.settimeout(2); s.connect(('localhost',9876)); print('OK'); s.close()"`

### GitHub
- Username: therobotsimguy
- Token: **store in env / credential manager — do not commit plaintext tokens**
- Branch: simready-asset-generator

---

## FEEDBACK RULES (apply always)

1. **Flag errors immediately** — don't silently continue when AI models fail. Tell user: "X is failing, here's what I need to fix it." Don't bury in logs.
2. **Discuss before coding** — when user says "let's discuss", discuss. Don't pick an option and implement it. Present options, wait for approval.
3. **Never use old version code in new version** — build from scratch. Importing old code causes cascading hacks.
4. **Commit and push to GitHub** — after adding or editing SimReady/V7 files in this repo, commit with a clear message and `git push` to `origin` (branch `simready-asset-generator` unless user says otherwise). Never commit secrets (`api_keys.json`, tokens).

---

## V7 PIPELINE (current)

### Architecture
```
Image → Stage A (Gemini geometry + Claude behavior) → stage_a.json
      → Stage B (vision stack validates geometry)   → stage_b.json
      → Stage C (math engine, positions, dividers)  → stage_c.json
      → Stage D (Blender script → MCP → .blend/.usd)
      → Stage E (validates Blender output)          → stage_e.json
      → Stage F (USD physics layer)                 → _physics.usd
```

### Key Design
- Zero AI decisions in Stage D/F — pure translation from spec
- Stage C is the math brain — ALL positions computed there
- Stage F reads spec + USD, writes physics metadata
- Output dir: `<image_name>_v7/`

### Stage C: Position Math
- `FRONT_Y = D/2` where D = cabinet depth
- Doors: `cy = FRONT_Y - pd/2` (front face flush)
- Drawers: `cy = FRONT_Y - pd/2`
- Handles/knobs: `handle_y = parent_pos[1] + parent_d/2 + pd/2` (protrudes from parent front face)
- Invisible parts: `n_doors - 1` vertical dividers inferred automatically

### Stage D: Blender Script

**Cylinder/Knob Origin Bug (CRITICAL)**
- `primitive_cylinder_add(location=(x,y,z))` + `transform_apply(rotation=True)` resets `obj.location` to (0,0,0)
- Fix: create at origin (0,0,0), apply all transforms, then set `obj.location = (x,y,z)` at end

**Joint Pivot = Object Origin (CRITICAL — from knowledge base)**
- Origin must be AT the joint pivot, not at mesh center
- For revolute doors: `set_origin_keep_visual(obj, ±w/2, 0, 0)` — shifts origin to hinge edge
- For prismatic drawers: `set_origin_keep_visual(obj, 0, -d/2, 0)` — shifts origin to back face
- This makes USD export `Xform translate = pivot world position`
- Stage F then uses `localPos1 = (0,0,0)` correctly

**set_origin_keep_visual**
```python
def set_origin_keep_visual(obj, rel_x, rel_y, rel_z):
    delta = Vector((rel_x, rel_y, rel_z))
    obj.location = obj.location + delta
    for v in obj.data.vertices:
        v.co -= delta
```

**Parenting**
```python
bpy.context.view_layer.update()  # ensure matrix_world current
child.parent = parent
child.matrix_parent_inverse = parent.matrix_world.inverted()
```

**USD Export Behavior**
- Box objects (transform_apply scale only): position baked into mesh vertices, NO Xform translate in USD
- Cylinder objects (location set after transforms): position stored as Xform translate
- After pivot-origin shift: revolute/prismatic objects export WITH correct Xform translate = pivot position

**Coordinate System**
- X: left(−) to right(+)
- Y: back(−) to front(+) — depth, drawers slide in +Y
- Z: bottom to top — height, revolute doors rotate around Z

### Stage F: USD Physics

**ArticulationRootAPI vs RigidBodyAPI (CRITICAL)**
- Root body: `ArticulationRootAPI` ONLY — NO `RigidBodyAPI`
- Adding `RigidBodyAPI` to root = floating-base → whole object flies away on sim start
- All non-root children get `RigidBodyAPI`

**Joint localPos**
- `localPos0` = child's Xform translate (pivot in parent/world frame)
- `localPos1` = (0,0,0) — child's origin IS the pivot
- Read via `_get_xform_translate(stage, part_path)`

**Joint Types**
- `ROTATIONAL` → RevoluteJoint, axis=Z, limits=[-120°,0°] left hinge or [0°,120°] right hinge
- `LINEAR_TRANSLATIONAL` → PrismaticJoint, axis=Y, limits=[0, depth*0.85]
- Everything else → FixedJoint (handles, knobs, dividers, top panel)

**Collision**
- CollisionAPI + MeshCollisionAPI on Mesh prims (children of Xform)
- `approximation = "convexHull"` for all moving parts

**Isaac Sim Load (standing rule for agent + user)**

- **Always minimal Kit** — `from isaacsim import SimulationApp` + `omni.usd.get_context().open_stage(...)`, same pattern as `load_in_isaacsim.py` and `scripts/tools/simready_assets/open_usd_in_isaacsim.py`.
- **Never** use `isaaclab.app.AppLauncher` just to open a generated USD; it boots the full Isaac Lab stack and the scene/viewport won’t match a plain Sim open.
- When giving run instructions after a pipeline run, **include the real path** to the file we produced (usually `*_physics.usd` after Stage F).

Per-folder loader (USD fixed next to script — e.g. `cabinet_2_v7` or `cabinet_2_simready_out`):
```bash
cd /home/msi/IsaacLab
./isaaclab.sh -p scripts/tools/simready_assets/cabinet_2_simready_out/load_in_isaacsim.py
```

Generic opener (pass any absolute or repo-relative `--usd`):
```bash
cd /home/msi/IsaacLab
./isaaclab.sh -p scripts/tools/simready_assets/open_usd_in_isaacsim.py \
  --usd scripts/tools/simready_assets/cabinet_2_simready_out/teak_outdoor_sideboard_physics.usd
```

In Sim: Space to play; Shift+drag on object to apply force.

**GitHub index:** upstream repo also has `scripts/tools/simready_assets/memory/MEMORY.md` (table of links). This file (`MASTER.md`) is the full on-disk playbook.

---

## V4 PIPELINE (superseded by V7)

### Architecture
```
Phase 1 (parallel): Path A (6 AI agents: Gemini×3 + Claude×3) ‖ Path B (4 vision models)
Math Engine: geometry_math.py computes exact xyz from A+B
Phase 2 (race): Path C (Claude+Gemini write Blender scripts) → Blender → Path D (judge loop)
  D: pure math judge — validates against pre-computed coords, feeds specific feedback back to C
  Max 3 attempts. C MUST use pre-computed coordinates, not invent positions.
```

### Key Lessons
- Gemini writes broken 30-line Blender scripts — always prefer Claude
- D feedback is specific: "Door_Left X off by 340mm" — stacks across retry attempts
- Behavioral constraints auto-generated from behavior analysis (generic for any object type)
- Drawer geometry: front+bottom+left+right+back walls, open top
- Door dividers: N doors → N-1 vertical stiles between them

---

## V3 PIPELINE (superseded)

### Architecture (11 stages, Stages 0-8 + 10)
Stage order: 0 → 1 → 2 → **4 → 3** → 4.5 → 5 → 6 → 7 → 8 → 10
(Stage 4 runs BEFORE Stage 3 — constraint solver before topology validation)

### AI Models
- SAM 3 (text-prompted segmentation)
- Gemini Pro (gemini-2.5-flash) — semantic understanding
- Depth Pro (apple/ml-depth-pro) — metric depth, checkpoint at models/ml-depth-pro/checkpoints/depth_pro.pt
- DepthAnything 3 (depth-anything/DA3-LARGE) — metric depth cross-validation
- XGBoost / TRELLIS: evaluated and REMOVED

### Critical Bug Fixes

**Hollow Body Collision**
- `convexDecomposition` fills door openings → gripper can't enter
- Fix: `prim.RemoveAPI(UsdPhysics.CollisionAPI)` on body visual mesh + separate convexHull boxes per panel
- Must use `Sdf.TokenListOp` Deleted Items (not just RemoveAPI — PhysX re-adds it at spawn)
- Divider panels recessed 3 wall-thicknesses from front

**Triangle Face Bug**
- `restructure.py` adds ~37 triangle faces to body mesh → phantom collision even with CollisionAPI deleted
- Fix: `_remove_triangle_faces()` — strip tri faces, keep only quads, recompute normals
- Golden: 59 quad faces = works. With triangles: 96 faces = blocks gripper
- Do NOT add `dissolve_degenerate` or `fill` in Blender cleanup — creates the same extra triangles

**Joint Anchor Bug**
- Stage 6 was NOT setting `physics:localPos0` → PhysX defaulted to (0,0,0) → doors dragged to body center
- Fix: `localPos0` = child prim's translate (hinge position in parent space), `localPos1` = (0,0,0)
- Child's origin IS the hinge point (set by `kinematic_builders.py`)

**Door Mass Bug**
- Was using bounding box × steel density → doors = 100kg each (immovable by Franka)
- Fix: actual mesh volume from USD × role-based density + mass caps (door: 15kg, knob: 0.3kg, body: 50kg)

**Body Anchoring**
- Kinematic body: "Articulations with kinematic bodies are not supported"
- FixedJoint to world: snaps body to (0,0,0) regardless of spawn position
- Fix: don't anchor body at all. 40kg dynamic body + gravity + ground contact = stable

**Damping Formula**
- Old: `damping = I / close_time` → 4% critical → doors oscillate wildly
- Fix: `damping = 0.8 × 2 × sqrt(stiffness × I)` → 80% critical → smooth close
- Sanity check: if damping < 50% critical, override to 80%

**Stage 3/4 Order**
- Stage 3 before Stage 4 → all components at (0,0,0) → 53 false overlap warnings → confidence = 0.00
- Fix: Stage 4 runs first, writes solved positions back into components, then Stage 3 validates

**Gemini Friction Values**
- Gemini sets 0.5 for metal (rubber-level). Must include material friction table: metal=0.15, plastic=0.25, rubber=0.70

**Flat Shading on Boxes**
- `shade_smooth()` on boxes creates white arc artifacts at edges
- Fix: `shade_flat()` for boxes, `shade_smooth()` only for cylinders

**Prismatic Joint Limits**
- Old: slide = body_depth × 0.8 → drawer exits body
- Fix: slide = actual_drawer_depth × 0.75 (25% always stays inside)

**Drawer Interior Panels**
- Interior panels must have NO collision — PhysX pushes drawers out of body if they do
- Front panel is separate FIXED child WITH collision

### Physics Reasoning Track
- Parallel Gemini+Claude track: Gemini observes → Claude reasons → Gemini verifies → Python merges
- Output: hardware_spec.json with stiffness, damping, bracket dims per component
- Stage 6 reads stiffness_Nm_per_rad, damping_Nms_per_rad for joint drives

### Run Commands (V3)
```bash
# Full pipeline:
cd scripts/tools/v3 && python orchestrator_v3.py --image input/oven.png --type "oven"

# Teleop (CPU for sliders):
./isaaclab.sh -p scripts/environments/teleoperation/teleop_se3_agent_cinematic.py \
  --asset scripts/tools/simready_assets/oven_math/built_in_double_electric_oven_simready.usd \
  --asset_pos 0.8 0.0 0.0 --device cpu
```

---

## BLENDER GENERAL LESSONS

### Origin = Joint Pivot Rule
- The object's Blender origin defines the joint pivot in Isaac Sim
- For revolute joints: origin must be exactly at rotation axis center (hinge edge)
- For prismatic joints: origin at slide-start position (back face)
- Method: `set_origin_keep_visual()` or use 3D cursor → Set Origin → Origin to 3D Cursor

### Apply Transforms Before Parenting
- Always apply Rotation & Scale (`Ctrl+A`) on BOTH parent and child before parenting
- Do NOT apply Location if origin must stay at joint pivot

### Flat Hierarchy for PhysX Articulations
- All links must be siblings under root — not nested under body mesh
- Nested hierarchy causes PhysX joint resolution errors

### Collision Mesh Best Practices
- Use `convexHull` for thin flat panels (doors, walls) — sufficient and fast
- Use `convexDecomposition` only for complex concave geometry
- Visual mesh ≠ collision mesh for hollow bodies (oven, cabinet)
- Prefix collision meshes with `COL_` for organization

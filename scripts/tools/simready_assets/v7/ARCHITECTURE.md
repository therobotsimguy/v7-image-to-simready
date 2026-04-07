# V7: Image to SimReady — Architecture

## Pipeline

```
IMAGE
  │
  A — Gemini (geometry) + Claude (behavior 16×15)
  │
  B — Vision stack (DINO + SAM3 + DepthPro + DA3)
  │
  C — Reconcile A+B dims + Math Engine → unified spec
  │
  D — Write + execute Blender script → .blend + .usd
  │
  E — Validate .blend (8 Blender variables)
      Validate .usd (geometry survived export)
  │
  F — Add PhysX to .usd:
      joints, limits, collision, mass,
      localPos0/1, articulation root, rigid body flags
  │
  SimReady USD ✓
```

---

## Stage A — Template Filler

Two models fill the same structured template per part:

**Gemini fills (visual analysis):**
- Object type: RIGID or ARTICULATED
- Parts list, geometry type, shapes
- Dimensions in mm
- Materials (type, color_rgb, metallic, roughness)
- Pivot positions (where hinge/slide origin is)
- Parent-child hierarchy
- Cavity openings flag
- Root object

**Claude fills (behavior reasoning):**
- behavior_type: one of 16 behavior types
- active_constraints: only the constraints that apply for that behavior type
- interaction: initiated_by, action, return
- invalid_behaviors: what this part cannot do and why

**Output:** one JSON per part — geometry from Gemini + behavior from Claude

---

## Stage B — Vision Stack

4 models run in parallel:
- Grounding DINO → open-vocabulary detection, bounding boxes, part count
- SAM3 → pixel masks per part
- DepthPro → metric depth map in meters + focal length
- DepthAnything3 → relative depth for cross-validation

Math reconciliation:
- DINO boxes + SAM3 masks → confirmed parts
- Confirmed parts × depth maps → real-world dimensions

**Note:** B cannot measure object depth (front-to-back) from single image.
Depth, hidden dims → always fall back to A.

---

## Stage C — Reconciler + Math Engine

Pure Python, no AI calls.

**Step 1 — Reconcile dims per part (A vs B):**

| Situation | Rule |
|---|---|
| B is null (depth, hidden parts) | Use A |
| B confidence < 0.80 | Use A |
| Differ < 20%, B confidence > 0.80 | Average A+B |
| Differ < 20%, B confidence > 0.80, high confidence | B wins |
| Differ > 20% | Flag conflict, use B, log warning |

**Step 2 — Validate contract before passing to D:**

RIGID contract (required):
- geometry, dims_reconciled, material, smooth_shading

ARTICULATED contract (required — all 8):
- geometry, dims_reconciled, material, smooth_shading
- pivot (exact for revolute, center ok for prismatic/fixed)
- parent (all parts except root)
- cavity_face_delete (boolean)
- is_separate_object (boolean)

C hard-fails if any required field is missing or invalid.
D never sees incomplete data.

**Step 3 — Math Engine:**
- Takes reconciled dims → computes exact xyz positions
- Deterministic — same inputs always produce same outputs
- All units in meters

**Output:** unified spec — dims_reconciled + position_xyz added to each part

---

## Stage D — Blender Script Writer

Pure translator: spec → Blender Python script. Zero decisions.

For each part:
1. geometry field → pick Blender primitive
2. position_xyz → place exactly (from Math Engine)
3. dims_reconciled → scale exactly
4. pivot → set_origin_keep_visual() if needed
5. material → Principled BSDF with exact values
6. parent → obj.parent assignment
7. cavity_face_delete → delete front faces if true
8. smooth shading → shade_smooth() on all objects

Executes script in Blender via MCP socket (localhost:9876).
Exports: cabinet.blend + cabinet.usd

D trusts C completely. No validation in D.

---

## Stage E — Validator

Two-step file analysis. No live Blender connection.

**Step 1 — .blend file** (run headless: blender --background --python):
```
✓ correct number of objects
✓ each part exists by name
✓ dims correct (bbox ±5%)
✓ origin/pivot at correct position
✓ parent-child hierarchy correct
✓ materials assigned (color, roughness, metallic)
✓ smooth shading on
✓ cavity faces deleted (check face normals)
✓ is_separate_object — each moving part is its own object
```

**Step 2 — .usd file** (pxr / usd-core, already installed):
```
✓ all meshes exported (nothing dropped)
✓ transforms correct (scale applied, dims in meters)
✓ hierarchy preserved from Blender
✓ materials exported (UsdShade bindings exist)
✓ no degenerate meshes (zero-size objects)
```

Logic:
- If Step 1 FAILS → do not check USD, report Blender issues
- If Step 1 PASSES → check USD
- If both PASS → proceed to F

---

## Stage F — USD Physics Stage

Opens validated .usd with pxr, adds PhysX properties.
Reads joint/behavior data directly from spec.

**All objects (rigid + articulated):**
- PhysicsCollisionAPI → collision geometry
- PhysicsMassAPI → mass + inertia (dims × material density)
- RigidBodyAPI → dynamic or kinematic flag

**Articulated only:**
- ArticulationRootAPI → on root body
- Joint type from behavior_type:
  - ROTATIONAL → RevoluteJoint
  - LINEAR_TRANSLATIONAL → PrismaticJoint
  - SEQUENTIAL / fixed → FixedJoint
- localPos0 → computed from parent bbox + pivot position
- localPos1 → from child origin
- Joint limits from behavior active_constraints:
  - 2_range_limits → hi/lo in meters or degrees
  - 6_force_torque → max force/torque
  - 14_safety → hard stop values
- Mass from 9_material density:
  - wood → 600 kg/m³
  - metal → 7800 kg/m³
  - glass → 2500 kg/m³
  - plastic → 1200 kg/m³

**Output:** SimReady USD with full PhysX properties

---

## What Blender Owns vs USD Owns

**Blender is 100% responsible for:**
| Thing | Why |
|---|---|
| Mesh geometry | Vertices, faces, panels, walls |
| Object origins / pivot points | Where door hinges, drawer slides |
| Scale & transforms | All dims in meters, transform_apply() |
| Materials | Principled BSDF nodes |
| Smooth shading | Per-face normals |
| Cavity openings | Delete front faces blocking openings |
| Separate objects | Each moving part its own Blender object |
| Object hierarchy | Parent-child relationships USD inherits |

**USD is 100% responsible for:**
| Thing | Why |
|---|---|
| Joints | Revolute, prismatic, fixed — PhysX stage |
| Joint limits | Min/max degrees or meters |
| Collision geometry | PhysicsCollisionAPI |
| Mass / inertia | PhysicsMassAPI |
| localPos0 / localPos1 | Joint anchor positions |
| Articulation root | Root of physics chain |
| Rigid body flags | Kinematic, dynamic |

---

## Behavior Template — 16 × 15

**16 behavior types** (what a part DOES):
1. ROTATIONAL, 2. LINEAR_TRANSLATIONAL, 3. GRASPING, 4. INSERTION,
5. DEFORMATION, 6. CONTACT_BASED, 7. SEQUENTIAL, 8. DYNAMIC,
9. SLIDING_FRICTION, 10. WIPING_SWEEPING, 11. TWISTING_TORQUE,
12. STACKING, 13. COMPLIANT_FORCE, 14. IMPACT_STRIKING,
15. PULLING_TENSION, 16. ROLLING

**15 semantic constraint domains** (rules governing HOW it does it):
1. Directional, 2. Range Limits, 3. Pivot Placement, 4. Clearance,
5. Sequential, 6. Force/Torque, 7. Contact/Friction, 8. Symmetry,
9. Material, 10. Internal Volume, 11. Kinematic Chain, 12. Energy,
13. Feedback, 14. Safety, 15. Aesthetic

Lookup table in behavior_template.py determines which constraints
apply per behavior. Claude fills only active constraints.
F reads active_constraints to set joint limits, forces, and mass.

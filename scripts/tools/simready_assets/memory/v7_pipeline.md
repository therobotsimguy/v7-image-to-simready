# V7 Pipeline Architecture

## Stages
- **Stage A** (`stage_a.py`): Gemini fills geometry section, Claude fills behavior section in parallel. Output: `stage_a.json`
- **Stage B** (`stage_b.py`): Vision stack — validates geometry against image. Output: `stage_b.json`
- **Stage C** (`stage_c.py`): Math engine — computes position_xyz for every part, infers invisible parts (dividers), reconciles dims. Output: `stage_c.json`
- **Stage D** (`stage_d.py`): Blender script writer — translates spec → Python script → sends via MCP socket (port 9876). Output: `.blend` + `.usd`
- **Stage E** (`stage_e.py`): Validates Blender output against spec
- **Stage F** (`stage_f.py`): USD physics layer — adds ArticulationRootAPI, RigidBodyAPI, CollisionAPI, MassAPI, joints. Output: `_physics.usd`

## Entry Point
Run individual stages or full pipeline from `v7/` directory.

## Output Structure
Each run creates `<image_name>_v7/` with stage_a.json through stage_f outputs.

## Key Design
- Zero AI decisions in Stage D/F — pure translation from spec
- Stage C is the math brain — all positions computed there
- Stage F reads spec + USD, writes physics metadata

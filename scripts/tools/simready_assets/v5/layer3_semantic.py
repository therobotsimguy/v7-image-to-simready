#!/usr/bin/env python3
"""V5 Layer 3: Semantic Filtering — "What SHOULD each part do?"

Compact single-model approach:
- No knowledge base loading (was 40k chars, AI ignores most of it)
- Claude only — no parallel Gemini (saves 30-60s)
- ~300 token prompt per object instead of 40k
- AI decides WHAT (joint type, axis, pivot side)
- geometry_math computes HOW MUCH (damping, torque, limits from bbox)
"""

import json
import os
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))
_ASSETS_DIR = os.path.dirname(_DIR)
sys.path.insert(0, _ASSETS_DIR)

from v5.behavior_contract import BehaviorContract, BehaviorSpec, ConstraintCheck, CONSTRAINT_DOMAINS
from v5.ai_agents import load_api_keys, call_claude, parse_json
from geometry_math import (
    compute_pivot_position, compute_local_offset,
    is_point_inside_bbox, validate_joint_limits,
    validate_mass, validate_part_fits_parent,
    arm_length_from_bbox, torque_from_gravity,
    damping_for_revolute, damping_for_prismatic, stiffness_for_prismatic,
    required_force_revolute, required_force_prismatic,
    inertia_box,
)


# Compact prompt — AI decides joint type + pivot side only
# All numeric values are computed by geometry_math, not guessed by AI
_COMPACT_CONTRACT_PROMPT = """Robotics physicist. Specify joint behavior for each movable part of a {object_type}.

ROOT (chassis): {root_name} | {root_dims_mm} mm W×D×H | origin {root_origin}

MOVABLE PARTS (name | dims W×D×H mm | centroid from root origin mm):
{parts_compact}

For each part return ONLY:
- joint_type: revolute | prismatic | fixed
- joint_axis: X | Y | Z
- pivot_side: left | right | bottom | top | back | center | front
- joint_limits_deg: [lo, hi] for revolute (null for prismatic)
- joint_limits_m: [lo, hi] for prismatic in meters (null for revolute)
- mass_kg: realistic mass

PHYSICS RULES:
- Doors (vertical flat panel, hinged side): revolute Z axis, pivot at left or right edge, 0→90° to 0→120°
- Shelves/drawers (horizontal flat panel inside box): prismatic Y axis, limits [-depth*0.75, 0.0]
- Knobs/dials (small cylinder on face): revolute Y axis, 0→270°
- Racks (slide out): prismatic Y axis, negative limits
- Lockers/latches on doors: revolute Z, same limits as parent door
- Wheel stoppers: revolute Y, 0→90°

JSON ONLY:
{{"parts": [{{"name": "...", "joint_type": "revolute", "joint_axis": "Z", "pivot_side": "left", "joint_limits_deg": [0, 110], "joint_limits_m": null, "mass_kg": 5.0}}]}}"""


def run_layer3(contract: BehaviorContract):
    print("\n" + "=" * 60)
    print("  LAYER 3: Semantic Filtering  (compact prompt, Claude only)")
    print("=" * 60)

    keys = load_api_keys()
    ckey = keys["anthropic"]["api_key"]

    root = contract.get_part(contract.root_part)
    if not root:
        print("  ERROR: no root part"); contract.layer3_complete = False; return contract

    # Build compact parts description — one line per part
    movable = [p for p in contract.parts if not p.is_static]
    lines = []
    for p in movable:
        cx = round(((p.bbox_min[0]+p.bbox_max[0])/2 - root.origin[0])*1000)
        cy = round(((p.bbox_min[1]+p.bbox_max[1])/2 - root.origin[1])*1000)
        cz = round(((p.bbox_min[2]+p.bbox_max[2])/2 - root.origin[2])*1000)
        lines.append(
            f"{p.name} | {p.dims_mm[0]:.0f}×{p.dims_mm[1]:.0f}×{p.dims_mm[2]:.0f}mm"
            f" | centroid ({cx},{cy},{cz})mm from root"
        )

    root_dims = f"{root.dims_mm[0]:.0f}×{root.dims_mm[1]:.0f}×{root.dims_mm[2]:.0f}"
    prompt = _COMPACT_CONTRACT_PROMPT.format(
        object_type=contract.object_type or "appliance",
        root_name=contract.root_part,
        root_dims_mm=root_dims,
        root_origin=f"({root.origin[0]*1000:.0f},{root.origin[1]*1000:.0f},{root.origin[2]*1000:.0f})mm",
        parts_compact="\n".join(lines),
    )

    print(f"  Prompt: {len(prompt)} chars, {len(movable)} movable parts → Claude...")
    try:
        raw = call_claude(ckey, prompt, max_tokens=32768)
        ai_result = parse_json(raw)
    except Exception as e:
        print(f"  Claude FAILED: {e}")
        contract.layer3_complete = False
        return contract

    ai_parts = {p["name"]: p for p in ai_result.get("parts", [])}
    print(f"  AI returned specs for {len(ai_parts)} parts")

    # ── Static parts: fixed joint ─────────────────────────────────────────────
    for part in contract.parts:
        if part.is_static:
            spec = BehaviorSpec(
                behavior_type="static", is_valid=True,
                joint_type="fixed", collision_type="boundingCube",
            )
            part.primary_behavior = spec
            part.valid_behaviors = [spec]

    # ── Non-static parts: AI decides type/axis/pivot, math computes numbers ───
    for part in contract.parts:
        if part.is_static:
            continue

        ai = ai_parts.get(part.name)
        if not ai:
            print(f"    WARN {part.name}: not in AI result — marking static")
            part.is_static = True
            spec = BehaviorSpec(behavior_type="static", is_valid=True, joint_type="fixed", collision_type="boundingCube")
            part.primary_behavior = spec
            part.valid_behaviors = [spec]
            continue

        # Map pivot_side → pivot_type understood by compute_pivot_position
        pivot_side = ai.get("pivot_side", "center")
        pivot_map = {
            "left": "left_edge", "right": "right_edge",
            "bottom": "bottom_edge", "top": "top_edge",
            "back": "back_center", "front": "front_center",
            "center": "center",
        }
        pivot_type = pivot_map.get(pivot_side, "center")
        pivot_pos = compute_pivot_position(part.bbox_min, part.bbox_max, pivot_type)

        jtype = ai.get("joint_type", "fixed")
        jaxis = ai.get("joint_axis", "Y")

        # ── Geometry math computes all numeric parameters ─────────────────────
        arm = arm_length_from_bbox(part.bbox_min, part.bbox_max, pivot_type, jaxis)
        if ai.get("mass_kg"):
            part.mass_kg = float(ai["mass_kg"])

        if jtype == "revolute":
            force_nm = torque_from_gravity(part.mass_kg, arm)
            damping  = damping_for_revolute(part.mass_kg, arm)
            stiffness = 0.0
        elif jtype == "prismatic":
            force_nm  = required_force_prismatic(part.mass_kg)
            damping   = damping_for_prismatic(part.mass_kg)
            stiffness = stiffness_for_prismatic(part.mass_kg)
        else:
            force_nm = damping = stiffness = 0.0

        spec = BehaviorSpec(
            behavior_type="rotational" if jtype == "revolute" else ("linear" if jtype == "prismatic" else "static"),
            is_valid=True,
            joint_type=jtype,
            joint_axis=jaxis,
            damping=damping,
            stiffness=stiffness,
            force_nm=force_nm,
            pivot_position=pivot_pos,
            pivot_description=pivot_type,
            collision_type=ai.get("collision_type", "boundingCube"),
            collision_enabled_between_bodies=False,
        )

        # Route limits to correct field based on joint type
        if jtype == "prismatic":
            raw = ai.get("joint_limits_m") or ai.get("joint_limits_deg")
            if raw:
                spec.joint_limits_m = tuple(raw)
        else:
            raw_deg = ai.get("joint_limits_deg")
            raw_m   = ai.get("joint_limits_m")
            if raw_deg:
                spec.joint_limits_deg = tuple(raw_deg)
            if raw_m:
                spec.joint_limits_m = tuple(raw_m)

        # Compute localPos0 (pivot relative to root origin)
        part.joint_local_pos0 = compute_local_offset(pivot_pos, root.origin)

        # Pivot edge snapping — if pivot is off the correct edge by >50mm, snap it
        px, py, pz = pivot_pos
        bmin, bmax = part.bbox_min, part.bbox_max
        tol = 0.05
        desc = pivot_type.lower()
        snapped = False
        if "left" in desc and abs(px - bmin[0]) > tol:
            pivot_pos = (bmin[0], (bmin[1]+bmax[1])/2, (bmin[2]+bmax[2])/2); snapped = True
        elif "right" in desc and abs(px - bmax[0]) > tol:
            pivot_pos = (bmax[0], (bmin[1]+bmax[1])/2, (bmin[2]+bmax[2])/2); snapped = True
        elif "bottom" in desc and abs(pz - bmin[2]) > tol:
            pivot_pos = ((bmin[0]+bmax[0])/2, (bmin[1]+bmax[1])/2, bmin[2]); snapped = True
        elif "top" in desc and abs(pz - bmax[2]) > tol:
            pivot_pos = ((bmin[0]+bmax[0])/2, (bmin[1]+bmax[1])/2, bmax[2]); snapped = True
        if snapped:
            spec.pivot_position = pivot_pos
            part.joint_local_pos0 = compute_local_offset(pivot_pos, root.origin)

        part.primary_behavior = spec
        part.valid_behaviors = [spec]

        lp = part.joint_local_pos0 or (0,0,0)
        lim = spec.joint_limits_deg or spec.joint_limits_m or "?"
        print(f"    {part.name}: {jtype} {jaxis} limits={lim} pivot={pivot_type} "
              f"localPos0=({lp[0]*1000:.0f},{lp[1]*1000:.0f},{lp[2]*1000:.0f})mm "
              f"arm={arm*1000:.0f}mm damping={damping:.1f}")

    contract.layer3_complete = True
    n = sum(1 for p in contract.parts if p.primary_behavior)
    print(f"\n  Layer 3 complete: {n} behavior specs (Claude only, compact prompt)")
    return contract


if __name__ == "__main__":
    contract_path = sys.argv[1] if len(sys.argv) > 1 else None
    if contract_path:
        with open(contract_path) as f:
            contract = BehaviorContract.from_json(f.read())
        contract = run_layer3(contract)
        print("\n" + contract.to_json()[:5000])

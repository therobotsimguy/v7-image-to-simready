#!/usr/bin/env python3
"""V5 Layer 2: Plausible Behaviors — "What COULD each part do?"

Fast path: name heuristics classify ~95% of parts instantly.
Slow path: single compact Claude call for truly unknown parts only.
No Gemini. No knowledge base. No parallel agents.
"""

import json
import os
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))
_ASSETS_DIR = os.path.dirname(_DIR)
sys.path.insert(0, _ASSETS_DIR)

from v5.behavior_contract import BehaviorContract, BEHAVIORS
from geometry_math import bbox_volume_mm3
from v5.ai_agents import load_api_keys, call_claude, parse_json


PART_BEHAVIOR_MATRIX = {
    "oven_door":    ["rotational", "grasping", "sequential"],
    "cabinet_door": ["rotational", "grasping", "sequential"],
    "drawer":       ["linear", "grasping", "sequential", "pulling_tension"],
    "knob":         ["rotational", "twisting_torque", "grasping"],
    "dial":         ["rotational", "twisting_torque", "grasping", "contact"],
    "rack":         ["linear", "pulling_tension", "grasping"],
    "shelf":        ["linear", "pulling_tension"],
    "button":       ["linear", "contact"],
    "switch":       ["rotational", "contact"],
    "lid":          ["rotational", "grasping", "sequential"],
    "handle":       ["grasping", "pulling_tension"],
    "lever":        ["rotational", "grasping"],
    "slider":       ["linear", "grasping"],
    "valve":        ["rotational", "twisting_torque"],
    "bolt":         ["rotational", "insertion", "twisting_torque"],
    "peg":          ["insertion", "linear", "grasping"],
    "chassis":      [],
    "frame":        [],
    "body":         [],
    "panel":        [],
    "static":       [],
}

_STATIC_TYPES = {"chassis", "frame", "body", "panel", "static"}

# Keywords that immediately identify a part type from its name
_NAME_RULES = [
    (["chassis", "carcass", "cabinet_body", "main_body"],           "chassis"),
    (["ventgrill", "ventilator", "vent_",  "topbody", "toppanel",
      "backpanel", "sidepanel", "interiorsheet", "lightback",
      "ledlight", "lightglass", "lights_", "lightframe",
      "lightrefract", "wheelshaft", "wheelmount", "wheelcap",
      "wheelbolt", "wheeltire", "mount_", "mounts_", "screen_",
      "switch_frame", "interiorlocker", "lockercilinder",
      "lockerbox", "lockerbase", "locker_washer"],                   "panel"),
    (["frame", "structure", "housing", "carcass"],                  "frame"),
    (["back_panel", "side_panel", "rear_panel", "backplate"],       "panel"),
    (["door"],                                                       "cabinet_door"),
    (["drawer"],                                                     "drawer"),
    (["knob"],                                                       "knob"),
    (["rack"],                                                       "rack"),
    (["shelf", "shelv"],                                             "shelf"),
    (["handle", "pull_"],                                            "handle"),
    (["button"],                                                     "button"),
    (["switch"],                                                     "switch"),
    (["hinge"],                                                      "panel"),
    (["locker_body", "locker_"],                                     "cabinet_door"),
    (["stopper"],                                                    "lever"),
]


def _classify_by_name(name: str) -> str:
    n = name.lower()
    for keywords, part_type in _NAME_RULES:
        if any(k in n for k in keywords):
            return part_type
    # Generic fallbacks
    if n.endswith("_body_01") or n.endswith("_body_02"):
        return "chassis"
    return "unknown"


# Compact prompt — no knowledge base, no verbose descriptions
_COMPACT_IDENTIFY = """3D model parts needing classification. For each, return part_type and is_static.

Types: oven_door, cabinet_door, drawer, knob, shelf, rack, button, handle, lever, panel, chassis

Parts (name | dims WxDxH mm):
{parts_list}

JSON only: {{"object_type": "appliance|furniture|tool", "parts": [{{"name": "...", "part_type": "...", "is_static": true/false}}]}}"""


def run_layer2(contract: BehaviorContract):
    print("\n" + "=" * 60)
    print("  LAYER 2: Plausible Behaviors  (name-first, no Gemini)")
    print("=" * 60)

    # ── Step 1: name-based classification (instant) ───────────────────────────
    unknown_parts = []
    for part in contract.parts:
        part.part_type = _classify_by_name(part.name)
        if part.part_type == "unknown":
            unknown_parts.append(part)

    print(f"  Name-based: {len(contract.parts)-len(unknown_parts)}/{len(contract.parts)} classified, "
          f"{len(unknown_parts)} unknown → Claude")

    # ── Step 2: single compact Claude call for unknowns only ──────────────────
    if unknown_parts:
        keys = load_api_keys()
        parts_list = "\n".join(
            f"{p.name} | {p.dims_mm[0]:.0f}×{p.dims_mm[1]:.0f}×{p.dims_mm[2]:.0f}mm"
            for p in unknown_parts
        )
        prompt = _COMPACT_IDENTIFY.format(parts_list=parts_list)
        print(f"  Claude classifying {len(unknown_parts)} unknowns...")
        try:
            raw = call_claude(keys["anthropic"]["api_key"], prompt, max_tokens=2048)
            result = parse_json(raw)
            if result.get("object_type"):
                contract.object_type = result["object_type"]
            ai_map = {p["name"]: p for p in result.get("parts", [])}
            for part in unknown_parts:
                ai = ai_map.get(part.name, {})
                if ai.get("part_type"):
                    part.part_type = ai["part_type"]
                    part.is_static = ai.get("is_static", part.part_type in _STATIC_TYPES)
        except Exception as e:
            print(f"  WARN Claude failed: {e} — marking unknowns as panel/static")
            for part in unknown_parts:
                part.part_type = "panel"
                part.is_static = True

    # ── Step 3: use containment tree to mark cosmetic sub-parts as static ─────
    # If a part's centroid is inside a moving part's bbox, and it has no
    # independent behavior (its name doesn't suggest motion), mark it as static.
    # The merge step will absorb it into the parent body.
    containment = getattr(contract, "containment_tree", {})
    for parent_name, children in containment.items():
        parent = contract.get_part(parent_name)
        if not parent or parent.is_static:
            continue  # static parents don't drive child classification
        for child_name in children:
            child = contract.get_part(child_name)
            if child and child.part_type in ("panel", "static", "handle", "chassis", "frame"):
                # Cosmetic child of a moving part — will be merged, mark static
                child.is_static = True

    # ── Step 4: apply static flag and plausible behaviors ─────────────────────
    for part in contract.parts:
        if part.part_type in _STATIC_TYPES:
            part.is_static = True
        part.plausible_behaviors = PART_BEHAVIOR_MATRIX.get(part.part_type, [])

    # ── Step 5: size sanity checks ────────────────────────────────────────────
    for part in contract.parts:
        w, d, h = part.dims_mm
        max_dim = max(w, d, h)
        min_dim = min(w, d, h)

        if part.part_type == "knob" and max_dim > 200:
            part.part_type = "cabinet_door" if max_dim > 300 else "panel"
            part.is_static = part.part_type == "panel"
            print(f"  SANITY {part.name}: knob→{part.part_type} (too large: {max_dim:.0f}mm)")

        if part.part_type == "chassis" and max_dim < 200:
            part.part_type = "panel"; part.is_static = True
            print(f"  SANITY {part.name}: chassis→panel (too small: {max_dim:.0f}mm)")

        if part.part_type in ("cabinet_door", "oven_door") and min_dim > 150:
            part.part_type = "chassis"; part.is_static = True
            print(f"  SANITY {part.name}: door→chassis (not flat: {min_dim:.0f}mm)")

        part.plausible_behaviors = PART_BEHAVIOR_MATRIX.get(part.part_type, [])

    # Parts with no plausible behaviors → static
    for part in contract.parts:
        if not part.is_static and not part.plausible_behaviors:
            part.is_static = True

    # ── Step 6: root selection ────────────────────────────────────────────────
    static_parts = [p for p in contract.parts if p.is_static]
    if static_parts:
        root = max(static_parts, key=lambda p: bbox_volume_mm3(p.dims_mm))
    else:
        root = max(contract.parts, key=lambda p: bbox_volume_mm3(p.dims_mm))
        root.is_static = True; root.part_type = "chassis"
    contract.root_part = root.name

    for part in contract.parts:
        if part.name != contract.root_part:
            part.parent_part = contract.root_part

    moving = sum(1 for p in contract.parts if not p.is_static)
    print(f"\n  Root: {root.name}  |  Moving: {moving}/{len(contract.parts)}")
    for p in contract.parts:
        if not p.is_static:
            print(f"    {p.name}: {p.part_type} → {p.plausible_behaviors}")

    contract.layer2_complete = True
    return contract


if __name__ == "__main__":
    contract_path = sys.argv[1] if len(sys.argv) > 1 else None
    if contract_path:
        with open(contract_path) as f:
            contract = BehaviorContract.from_json(f.read())
        contract = run_layer2(contract)
        print("\n" + contract.to_json()[:3000])

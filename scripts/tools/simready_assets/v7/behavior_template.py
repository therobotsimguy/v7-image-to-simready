"""
V7 Behavior Template — 16 behaviors × 15 semantic constraints.

For each part of an object:
  - Gemini fills: geometry, dims, material, pivot, parent, hierarchy
  - Claude fills: behavior_type + active_constraints only

The lookup table BEHAVIOR_CONSTRAINTS determines which of the 15
constraints apply for each behavior type. Skipped ones are auto-filled.
"""

# ═══════════════════════════════════════════════════════════════════
# 16 BEHAVIOR TYPES
# ═══════════════════════════════════════════════════════════════════

BEHAVIORS = [
    "ROTATIONAL",           # 1  twist/turn — door, knob, valve, hinge
    "LINEAR_TRANSLATIONAL", # 2  slide/push/pull — drawer, sliding door
    "GRASPING",             # 3  grip/hold — handle, knob, surface
    "INSERTION",            # 4  push in — plug, key, peg, connector
    "DEFORMATION",          # 5  bend/flex — soft objects, springs
    "CONTACT_BASED",        # 6  tap/stroke/press — button, surface
    "SEQUENTIAL",           # 7  requires prior action — unlock then open
    "DYNAMIC",              # 8  free movement — gravity, momentum, ballistic
    "SLIDING_FRICTION",     # 9  push across surface without grasping
    "WIPING_SWEEPING",      # 10 repetitive surface contact with force control
    "TWISTING_TORQUE",      # 11 twist while grasped — jar lid, bolt, dial
    "STACKING",             # 12 place on surface, gravity settles it
    "COMPLIANT_FORCE",      # 13 impedance control, yield to surface
    "IMPACT_STRIKING",      # 14 high-speed kinetic energy — hammer, strike
    "PULLING_TENSION",      # 15 extract against resistance — stuck drawer, plug
    "ROLLING",              # 16 ball/cylinder rolls without slipping v = ω·r
]

# ═══════════════════════════════════════════════════════════════════
# 15 SEMANTIC CONSTRAINT DOMAINS
# ═══════════════════════════════════════════════════════════════════

CONSTRAINTS = {
    1:  "directional",       # motion must match intended direction
    2:  "range_limits",      # motion must stay within physical bounds
    3:  "pivot_placement",   # rotation around correct pivot point
    4:  "clearance",         # motion must not cause self-collision
    5:  "sequential",        # actions may have prerequisites
    6:  "force_torque",      # applied force must be realistic
    7:  "contact_friction",  # surfaces must maintain proper contact
    8:  "symmetry",          # motion may be symmetric or asymmetric
    9:  "material",          # material properties affect behavior
    10: "internal_volume",   # internal geometry restricts motion
    11: "kinematic_chain",   # motion respects mechanical structure
    12: "energy",            # motion requires appropriate energy source
    13: "feedback",          # motion may require sensors/stops
    14: "safety",            # motion includes safety limits
    15: "aesthetic",         # motion affects visual appearance
}

# ═══════════════════════════════════════════════════════════════════
# LOOKUP TABLE — which constraints apply per behavior
# True = Claude must fill this constraint
# False = auto-skipped with reason
# ═══════════════════════════════════════════════════════════════════

BEHAVIOR_CONSTRAINTS = {
    #                          1      2      3      4      5      6      7      8      9      10     11     12     13     14     15
    "ROTATIONAL":           [True,  True,  True,  True,  True,  True,  True,  True,  True,  True,  True,  True,  True,  True,  True ],
    "LINEAR_TRANSLATIONAL": [True,  True,  False, True,  True,  True,  True,  True,  True,  True,  True,  True,  True,  True,  True ],
    "GRASPING":             [False, True,  False, True,  True,  True,  True,  True,  True,  False, True,  True,  True,  True,  False],
    "INSERTION":            [True,  True,  False, True,  True,  True,  True,  True,  True,  False, True,  True,  True,  True,  False],
    "DEFORMATION":          [True,  True,  False, True,  True,  True,  True,  True,  True,  True,  False, True,  True,  True,  True ],
    "CONTACT_BASED":        [True,  True,  False, True,  True,  True,  True,  False, True,  False, False, True,  True,  True,  True ],
    "SEQUENTIAL":           [True,  True,  True,  True,  True,  True,  True,  True,  True,  True,  True,  True,  True,  True,  True ],
    "DYNAMIC":              [True,  True,  False, True,  False, True,  True,  False, True,  True,  True,  True,  True,  True,  True ],
    "SLIDING_FRICTION":     [True,  True,  False, True,  False, True,  True,  True,  True,  True,  False, True,  True,  True,  True ],
    "WIPING_SWEEPING":      [True,  True,  False, True,  True,  True,  True,  False, True,  True,  False, True,  True,  True,  True ],
    "TWISTING_TORQUE":      [True,  True,  True,  True,  True,  True,  True,  True,  True,  False, True,  True,  True,  True,  False],
    "STACKING":             [True,  True,  False, True,  True,  True,  True,  True,  True,  True,  False, True,  True,  True,  True ],
    "COMPLIANT_FORCE":      [True,  True,  False, True,  False, True,  True,  False, True,  True,  True,  True,  True,  True,  True ],
    "IMPACT_STRIKING":      [True,  True,  False, True,  True,  True,  False, False, True,  True,  True,  True,  True,  True,  True ],
    "PULLING_TENSION":      [True,  True,  False, True,  True,  True,  True,  False, True,  True,  True,  True,  True,  True,  False],
    "ROLLING":              [True,  True,  False, True,  False, True,  True,  True,  True,  True,  False, True,  True,  True,  True ],
}

# Skip reasons — why a constraint doesn't apply for certain behaviors
SKIP_REASONS = {
    3:  "N/A — no pivot point for this motion type",
    1:  "N/A — direction is not constrained for this behavior",
    5:  "N/A — no prerequisite action required",
    8:  "N/A — symmetry not relevant for this behavior",
    10: "N/A — internal volume does not restrict this motion",
    11: "N/A — not part of a kinematic chain",
    15: "N/A — aesthetic not affected by this behavior",
    7:  "N/A — contact/friction not relevant for this behavior",
}


# ═══════════════════════════════════════════════════════════════════
# TEMPLATE BUILDER
# ═══════════════════════════════════════════════════════════════════

def build_behavior_template(part_name: str, behavior_type: str) -> dict:
    """
    Build the behavior section of the V7 template for a given part.
    Returns the structure Claude must fill (active_constraints only).
    Skipped constraints are auto-populated.
    """
    if behavior_type not in BEHAVIOR_CONSTRAINTS:
        raise ValueError(f"Unknown behavior type: {behavior_type}. Must be one of: {BEHAVIORS}")

    applies = BEHAVIOR_CONSTRAINTS[behavior_type]

    active = {}
    skipped = {}

    for idx, applies_flag in enumerate(applies, start=1):
        name = f"{idx}_{CONSTRAINTS[idx]}"
        if applies_flag:
            active[name] = ""  # Claude fills this
        else:
            skipped[name] = SKIP_REASONS.get(idx, "N/A")

    return {
        "part": part_name,
        "behavior_type": behavior_type,
        "interaction": {
            "initiated_by": "",   # e.g. "handle", "finger", "robot_ee"
            "action": "",         # e.g. "pull", "rotate", "press"
            "return": "",         # e.g. "push back in", "gravity closes", "spring returns"
        },
        "active_constraints": active,
        "skipped_constraints": skipped,
        "invalid_behaviors": [],  # Claude lists what this part CANNOT do and why
    }


def build_part_template(part_name: str) -> dict:
    """
    Build the full V7 template for one part.
    Gemini fills the geometry section.
    Claude fills the behavior section.
    """
    return {
        # ── Gemini fills this ──────────────────────────────────────
        "part": part_name,
        "geometry": "",           # e.g. "box, 5-sided open top"
        "dims_mm": {
            "width": 0,
            "depth": 0,
            "height": 0,
        },
        "material": {
            "type": "",           # wood | metal | glass | plastic | ceramic
            "color_rgb": [0, 0, 0],
            "metallic": 0,        # 0 or 1
            "roughness": 0.5,
        },
        "is_separate_object": True,
        "pivot": "",              # e.g. "left edge hinge", "center prismatic Y", "N/A"
        "parent": "",             # e.g. "cabinet_body", "door_left", "none"
        "cavity_face_delete": False,  # True if front face blocks opening

        # ── Claude fills this ─────────────────────────────────────
        "behavior": build_behavior_template(part_name, "ROTATIONAL"),
        # ^ Claude replaces behavior_type and fills active_constraints
    }


# ═══════════════════════════════════════════════════════════════════
# CLAUDE PROMPT — behavior section only
# ═══════════════════════════════════════════════════════════════════

def claude_behavior_prompt(part_name: str, behavior_type: str, object_context: str) -> str:
    """Generate the prompt Claude receives to fill the behavior section."""
    template = build_behavior_template(part_name, behavior_type)

    import json
    active_keys = list(template["active_constraints"].keys())

    return f"""You are analyzing the physical behavior of a part for robot simulation.

Object context: {object_context}
Part: {part_name}
Behavior type: {behavior_type}

Fill in ONLY the active constraints listed below. Be specific and concise.
For each constraint write one sentence describing the rule for THIS specific part.

Active constraints to fill:
{json.dumps(active_keys, indent=2)}

Also fill:
- interaction.initiated_by: what initiates this motion (handle, finger, robot_ee, gravity)
- interaction.action: the verb (pull, rotate, press, slide, twist)
- interaction.return: how it returns to rest state
- invalid_behaviors: list 2-3 behaviors this part CANNOT do and why

Return ONLY valid JSON matching this structure:
{json.dumps(template, indent=2)}"""


# ═══════════════════════════════════════════════════════════════════
# EXAMPLE USAGE
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import json

    # Show template for a drawer (LINEAR_TRANSLATIONAL)
    print("=== DRAWER TEMPLATE ===")
    t = build_behavior_template("drawer", "LINEAR_TRANSLATIONAL")
    print(json.dumps(t, indent=2))

    print("\n=== DOOR TEMPLATE ===")
    t = build_behavior_template("door_left", "ROTATIONAL")
    print(json.dumps(t, indent=2))

    print("\n=== HANDLE TEMPLATE ===")
    t = build_behavior_template("handle", "GRASPING")
    print(json.dumps(t, indent=2))

    print("\n=== FULL PART TEMPLATE ===")
    t = build_part_template("drawer")
    print(json.dumps(t, indent=2))

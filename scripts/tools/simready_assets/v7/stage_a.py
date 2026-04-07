#!/usr/bin/env python3
"""
V7 Stage A — Template Filler
Gemini fills geometry section, Claude fills behavior section.
Both run in parallel, outputs merged into one spec per part.
"""

import json
import os
import time
import threading
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))
_TOOLS_DIR = os.path.dirname(os.path.dirname(_DIR))

sys.path.insert(0, os.path.dirname(_DIR))
from v7.behavior_template import BEHAVIORS, CONSTRAINTS, BEHAVIOR_CONSTRAINTS, SKIP_REASONS

BEST_GEMINI = "gemini-2.5-pro"
BEST_CLAUDE = "claude-opus-4-6"


# ═══════════════════════════════════════════════════════════════════
# API HELPERS
# ═══════════════════════════════════════════════════════════════════

def load_api_keys():
    path = os.path.join(_TOOLS_DIR, "api_keys.json")
    with open(path) as f:
        return json.load(f)

def call_gemini(api_key, prompt, image_path):
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=api_key)
    with open(image_path, "rb") as f:
        data = f.read()
    import mimetypes
    mime = mimetypes.guess_type(image_path)[0] or "image/png"
    contents = [
        types.Part.from_bytes(data=data, mime_type=mime),
        prompt,
    ]
    resp = client.models.generate_content(
        model=BEST_GEMINI, contents=contents,
        config=types.GenerateContentConfig(max_output_tokens=32768, temperature=0.1),
    )
    return resp.text

def call_claude(api_key, prompt, image_path):
    import anthropic
    import base64
    import mimetypes
    client = anthropic.Anthropic(api_key=api_key)
    with open(image_path, "rb") as f:
        b64 = base64.standard_b64encode(f.read()).decode()
    mime = mimetypes.guess_type(image_path)[0] or "image/png"
    resp = client.messages.create(
        model=BEST_CLAUDE,
        max_tokens=16384,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
            {"type": "text", "text": prompt},
        ]}]
    )
    return resp.content[0].text

def parse_json(text):
    text = text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        return json.loads(text[start:end])
    return json.loads(text)


# ═══════════════════════════════════════════════════════════════════
# GEMINI PROMPT — geometry section
# ═══════════════════════════════════════════════════════════════════

GEMINI_PROMPT = """Look at this image carefully. You are filling a structured template for 3D asset generation.

Return ONLY valid JSON in this exact format:

{
  "object": {
    "type": "descriptive name (e.g. oak sideboard cabinet)",
    "category": "furniture | appliance | tool | fastener | kitchenware | other",
    "articulation": "RIGID | ARTICULATED",
    "overall_dims_mm": {"width": <num>, "depth": <num>, "height": <num>}
  },
  "parts": [
    {
      "part": "unique_snake_case_name",
      "geometry": "describe shape (e.g. box, 5-sided open front, cylinder, revolution profile)",
      "dims_mm": {"width": <num>, "depth": <num>, "height": <num>},
      "material": {
        "type": "wood | metal | glass | plastic | ceramic | rubber",
        "color_rgb": [<r 0-1>, <g 0-1>, <b 0-1>],
        "metallic": <0 or 1>,
        "roughness": <0.0-1.0>
      },
      "is_separate_object": <true|false>,
      "pivot": "describe pivot (e.g. left edge hinge, center prismatic Y, bottom center, N/A fixed)",
      "parent": "parent part name or none",
      "cavity_face_delete": <true|false>
    }
  ]
}

Rules:
- List EVERY visible part separately (body, each drawer, each door, each handle, legs)
- is_separate_object = true for anything that moves independently
- cavity_face_delete = true only for the main body/chassis if it has door or drawer openings
- pivot: be specific about which edge for hinges
- parent: root body has "none", everything else names its parent
- dims in mm, be realistic for real-world object

Return ONLY the JSON, no explanations."""


# ═══════════════════════════════════════════════════════════════════
# CLAUDE PROMPT — behavior section
# ═══════════════════════════════════════════════════════════════════

def build_claude_prompt(gemini_parts: list) -> str:
    behaviors_list = "\n".join(f"  {i+1}. {b}" for i, b in enumerate(BEHAVIORS))
    parts_list = "\n".join(f"  - {p['part']}" for p in gemini_parts)

    return f"""You are analyzing physical behaviors of parts of an object for robot simulation.

Gemini has identified these parts:
{parts_list}

For EACH part, fill in the behavior section.

The 16 behavior types are:
{behaviors_list}

For each part return a JSON object with this structure:
{{
  "part": "part_name",
  "behavior_type": "ONE_OF_THE_16_TYPES",
  "interaction": {{
    "initiated_by": "what initiates motion (handle | finger | robot_ee | gravity | none)",
    "action": "the verb (pull | rotate | press | slide | twist | grip | none)",
    "return": "how it returns to rest (push back | gravity closes | spring returns | static | none)"
  }},
  "active_constraints": {{
    // Fill ONLY the constraints relevant to this behavior type.
    // For each relevant constraint write one specific sentence for THIS part.
    // Use these constraint names: directional, range_limits, pivot_placement,
    // clearance, sequential, force_torque, contact_friction, symmetry,
    // material, internal_volume, kinematic_chain, energy, feedback, safety, aesthetic
  }},
  "invalid_behaviors": ["behavior it CANNOT do — reason", ...]
}}

Return a JSON array of behavior objects, one per part.
Return ONLY the JSON array, no explanations."""


# ═══════════════════════════════════════════════════════════════════
# MERGE — combine Gemini geometry + Claude behavior
# ═══════════════════════════════════════════════════════════════════

def merge(gemini_output: dict, claude_behaviors: list) -> dict:
    behavior_map = {b["part"]: b for b in claude_behaviors}
    for part in gemini_output["parts"]:
        name = part["part"]
        if name in behavior_map:
            part["behavior"] = behavior_map[name]
        else:
            # fallback — no behavior found
            part["behavior"] = {"behavior_type": "UNKNOWN", "active_constraints": {}}
    return gemini_output


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def run_stage_a(image_path: str, output_path: str = None) -> dict:
    t0 = time.time()
    print(f"\n{'='*60}")
    print(f"  STAGE A — Template Filler")
    print(f"  Image: {os.path.basename(image_path)}")
    print(f"{'='*60}")

    keys = load_api_keys()
    gkey = keys["gemini"]["api_key"]
    ckey = keys["anthropic"]["api_key"]

    results = {}
    errors = {}

    # ── Run Gemini first (Claude needs Gemini's part list) ──────────
    print(f"\n  [A1] Gemini — geometry analysis...")
    t1 = time.time()
    try:
        raw = call_gemini(gkey, GEMINI_PROMPT, image_path)
        results["gemini"] = parse_json(raw)
        print(f"  [A1] ✓ {len(results['gemini']['parts'])} parts found ({time.time()-t1:.1f}s)")
        for p in results["gemini"]["parts"]:
            print(f"       - {p['part']} ({p['geometry']})")
    except Exception as e:
        errors["gemini"] = str(e)
        print(f"  [A1] ✗ Gemini failed: {e}")
        return None

    # ── Run Claude in parallel with part list from Gemini ───────────
    print(f"\n  [A2] Claude — behavior analysis ({len(results['gemini']['parts'])} parts)...")
    t2 = time.time()
    claude_prompt = build_claude_prompt(results["gemini"]["parts"])
    try:
        raw = call_claude(ckey, claude_prompt, image_path)
        # parse array
        text = raw.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        start = text.find("[")
        end = text.rfind("]") + 1
        behaviors = json.loads(text[start:end])
        results["claude"] = behaviors
        print(f"  [A2] ✓ {len(behaviors)} behaviors filled ({time.time()-t2:.1f}s)")
        for b in behaviors:
            print(f"       - {b['part']}: {b['behavior_type']}")
    except Exception as e:
        errors["claude"] = str(e)
        print(f"  [A2] ✗ Claude failed: {e}")
        results["claude"] = []

    # ── Merge ───────────────────────────────────────────────────────
    print(f"\n  [A3] Merging geometry + behavior...")
    spec = merge(results["gemini"], results["claude"])

    elapsed = time.time() - t0
    print(f"\n  ✓ Stage A complete — {len(spec['parts'])} parts, {elapsed:.1f}s total")
    print(f"{'='*60}")

    if output_path:
        with open(output_path, "w") as f:
            json.dump(spec, f, indent=2)
        print(f"  Saved: {output_path}")

    return spec


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    image = args.image
    name = os.path.splitext(os.path.basename(image))[0]
    out_dir = os.path.join(os.path.dirname(image), f"{name}_v7")
    os.makedirs(out_dir, exist_ok=True)
    output = args.output or os.path.join(out_dir, "stage_a.json")

    spec = run_stage_a(image, output)
    if spec:
        print(f"\n  Output: {output}")

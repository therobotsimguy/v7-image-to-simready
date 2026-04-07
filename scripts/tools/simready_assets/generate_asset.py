#!/usr/bin/env python3
"""Multi-Agent Asset Generator — general purpose.

Architecture:
  Phase 1 — Path A (6 AI agents) ‖ Path B (4 vision models) — all parallel
    Path A: Gemini×3 + Claude×3 → semantic understanding
    Path B: DINO + SAM3 + DepthPro + DepthAnything3 → measurements

  Phase 2 — Path C: AI reconciliation + Blender script generation
    Gemini + Claude reconcile A+B, then Claude writes the Blender script.

  Phase 3 — Execute in Blender via MCP

Works for ANY object: bolts, cabinets, glasses, ovens, etc.
No hardcoded templates — C generates the script based on what the object is.

Usage:
    python generate_asset.py --image cabinet.png
    python generate_asset.py --image bolt.png --no-execute
"""

import argparse
import base64
import json
import os
import socket
import sys
import time
import threading

_DIR = os.path.dirname(os.path.abspath(__file__))
_TOOLS_DIR = os.path.dirname(_DIR)

# Always top models
BEST_GEMINI = "gemini-3.1-pro-preview"
BEST_CLAUDE = "claude-opus-4-6"

# Path B: Vision Stack
from vision_stack import run_vision_stack
# Path D: Judge
from judge import run_judge


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT PROMPTS — general purpose, works for any object
# ═══════════════════════════════════════════════════════════════════════════════

PROMPTS = {
    "gemini_type": """Look at this image. Identify the object and its structure.
Answer in EXACTLY this JSON format, nothing else:
{
  "object_type": "what is this (e.g., hex bolt, sideboard cabinet, wine glass, microwave oven)",
  "category": "fastener | furniture | kitchenware | appliance | tool | other",
  "manufacturing": "how was it made (e.g., machined, panel construction, blown glass, injection molded)",
  "geometry_approach": "revolution (rotational symmetry) | panel_construction (boxes joined) | shell (thin walls) | solid (single block) | composite (mixed)",
  "components": [
    {"name": "part name", "shape": "cylinder | box | sphere | cone | hex_prism | custom_profile", "is_separate_object": false}
  ]
}
Count distinct parts carefully. Describe each visible component.
Return ONLY the JSON.""",

    "gemini_dims": """Look at this image. Estimate real-world dimensions of this object.
Answer in EXACTLY this JSON format:
{
  "overall_width_mm": <number>,
  "overall_depth_mm": <number>,
  "overall_height_mm": <number>,
  "components": [
    {"name": "part name", "width_mm": <num>, "depth_mm": <num>, "height_mm": <num>}
  ]
}
For rotational objects: width=depth=diameter, height=length along axis.
Return ONLY the JSON.""",

    "gemini_materials": """Look at this image. Identify ALL materials and their appearance.
Answer in EXACTLY this JSON format:
{
  "materials": [
    {
      "name": "descriptive name (e.g., galvanized steel, walnut wood, clear glass)",
      "type": "metal | wood | glass | plastic | ceramic | rubber | fabric",
      "color_rgb": [<r 0-1>, <g 0-1>, <b 0-1>],
      "metallic": <0 or 1>,
      "roughness": <0-1>,
      "applied_to": ["list of component names that use this material"]
    }
  ]
}
Return ONLY the JSON.""",

    "claude_behavior": """Look at this image. Analyze the physical behavior of this object.
Answer in EXACTLY this JSON format:
{
  "object_type": "what is this object",
  "behaviors": [
    {"part": "part name", "motion": "none | linear | rotational | free", "axis": "X | Y | Z | none", "description": "brief description"}
  ],
  "structural_notes": "how parts connect, what's hidden, what constraints exist",
  "simulation_notes": "what matters for physics simulation (mass, friction, joints)"
}
If the object has no moving parts (e.g., a bolt), behaviors should have motion=none.
Return ONLY the JSON.""",

    "claude_bodies": """Look at this image. Define the body list for 3D modeling.
Each body = one Blender object. Parts that are fixed together = joined into one body.
Parts that move independently or have different materials = separate bodies.
Answer in EXACTLY this JSON format:
{
  "bodies": [
    {"name": "Body_Name", "material": "material name", "geometry": "description of shape and how to build it in Blender", "separate": true_or_false, "reason": "why separate or joined"}
  ],
  "total_objects": <int>,
  "origin_hint": "where the object origin should be (e.g., bottom center, geometric center)"
}
Return ONLY the JSON.""",

    "claude_geometry": """Look at this image. Describe the EXACT geometry needed to build this in Blender 4.3.
Think about manufacturing: how was this object made? That determines the modeling approach.
Answer in EXACTLY this JSON format:
{
  "approach": "revolution | extrude | primitive_assembly | boolean | sculpt",
  "blender_operations": [
    {"step": 1, "operation": "what to do", "details": "specific Blender API calls or approach"}
  ],
  "critical_details": ["list of details that must be correct for realism"],
  "common_mistakes": ["what to avoid when modeling this object"]
}
For revolution objects (bolts, glasses): describe the 2D profile to revolve.
For panel objects (furniture): describe each panel's position.
For complex objects: describe step by step.
Return ONLY the JSON.""",
}

FIX_PROMPT = """You are an expert Blender 4.3 Python scripter.

The 3D scene is already built in Blender. Most of it is correct.
Only the following objects have issues that need fixing:

{issues}

Write a SHORT script (no more than 50 lines) that fixes ONLY these objects.
- Delete each broken object by name, then recreate it correctly.
- Do NOT touch any other objects in the scene.
- Do NOT clear the scene.
- All dimensions in METERS.
- Apply scale after creating each object.
- Reassign the same material as the original if possible.

{blender_rules}

Write ONLY Python code, no markdown fences, no explanations."""

_BLENDER_RULES = """## Blender 4.3 API rules:
- NO `use_auto_smooth` (removed in 4.x)
- NO `Specular` input on Principled BSDF (removed)
- Use `bpy.ops.mesh.primitive_*_add()` for primitives
- For revolution/lathe objects: use `from_pydata()` with computed vertices
- For threaded bolts: modulate vertex radius along helix
- Apply scale: `bpy.ops.object.transform_apply(scale=True)`
- Materials: create node tree with ShaderNodeOutputMaterial + ShaderNodeBsdfPrincipled
- All dimensions in METERS (convert from mm).
- The script runs via exec() inside Blender — no `if __name__` guards.
- Write ONLY Python code, no markdown fences, no explanations."""

SCRIPT_GEN_PROMPT = """You are an expert Blender 4.3 Python scripter. Write a COMPLETE Blender script to create the 3D object described below.

## Object Analysis (from 6 AI agents + 4 vision models):
{spec_data}

## Rules:
1. Clear the scene first (remove all objects, meshes, materials).
2. All dimensions in METERS (convert from mm).
3. Create proper materials using Principled BSDF nodes.
4. Each body listed as separate = a separate Blender object.
5. Apply transforms (scale, rotation) after creating each object.
6. Use smooth shading on all objects.
7. Set object origins correctly for physics articulation:
   - DOORS: origin MUST be at the HINGE EDGE (left or right edge), NOT center.
   - DRAWERS: origin at center is fine (prismatic joint slides along axis).
   - To move the origin WITHOUT moving the visible geometry, use this pattern:
     ```
     def set_origin_keep_visual(obj, new_origin_x, new_origin_y, new_origin_z):
         from mathutils import Vector
         new_origin = Vector((new_origin_x, new_origin_y, new_origin_z))
         offset = new_origin - obj.location
         obj.location = new_origin
         for v in obj.data.vertices:
             v.co -= offset
     ```
8. Export to USD and save .blend file at the end.

{blender_rules}

## Output paths:
- USD: {output_usd}
- Blend: {output_blend}

## IMPORTANT:
- Write the COMPLETE script. No placeholders, no "TODO".
- Print object count, vertex count, and dimensions at the end.
- CRITICAL: If PRE-COMPUTED COORDINATES are provided above, you MUST use those exact
  xyz positions and sizes. Do NOT recalculate positions yourself. The math engine has
  already computed correct, consistent coordinates. Copy them directly as constants.
  Inventing your own coordinates will produce misaligned, inconsistent geometry.

Write ONLY the Python script."""


# ═══════════════════════════════════════════════════════════════════════════════
# API HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def load_api_keys():
    with open(os.path.join(_TOOLS_DIR, "api_keys.json")) as f:
        return json.load(f)

def image_to_base64(path):
    with open(path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode()

def call_gemini(api_key, model, prompt, image_path=None):
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=api_key)
    contents = []
    if image_path:
        with open(image_path, "rb") as f:
            data = f.read()
        import mimetypes
        mime = mimetypes.guess_type(image_path)[0] or "image/png"
        contents.append(types.Part.from_bytes(data=data, mime_type=mime))
    contents.append(prompt)
    resp = client.models.generate_content(
        model=model, contents=contents,
        config=types.GenerateContentConfig(max_output_tokens=8192, temperature=0.1),
    )
    return resp.text

def call_claude(api_key, model, prompt, image_path=None):
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    content = []
    if image_path:
        b64 = image_to_base64(image_path)
        import mimetypes
        mime = mimetypes.guess_type(image_path)[0] or "image/png"
        content.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}})
    content.append({"type": "text", "text": prompt})
    resp = client.messages.create(model=model, max_tokens=16384,
                                   messages=[{"role": "user", "content": content}])
    return resp.content[0].text

def parse_json_response(text):
    """Extract JSON from AI response, handling markdown fences."""
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

def extract_script(text):
    """Extract Python script from AI response, stripping markdown fences."""
    text = text.strip()
    if "```python" in text:
        text = text.split("```python")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
    return text


# ═══════════════════════════════════════════════════════════════════════════════
# BLENDER MCP
# ═══════════════════════════════════════════════════════════════════════════════

def send_to_blender(script, port=9876):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(180)
    sock.connect(("localhost", port))
    sock.sendall(json.dumps({"type": "execute_code", "params": {"code": script}}).encode())
    response = b""
    while True:
        try:
            chunk = sock.recv(8192)
            if not chunk: break
            response += chunk
            try: json.loads(response.decode()); break
            except: continue
        except socket.timeout: break
    sock.close()
    if not response: raise RuntimeError("No response from Blender")
    return json.loads(response.decode())

def blender_screenshot(filepath, port=9876):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(30)
        sock.connect(("localhost", port))
        sock.sendall(json.dumps({"type": "get_viewport_screenshot", "params": {"filepath": filepath}}).encode())
        resp = b""
        while True:
            try:
                c = sock.recv(65536)
                if not c: break
                resp += c
                try: json.loads(resp.decode()); break
                except: continue
            except socket.timeout: break
        sock.close()
        return json.loads(resp.decode()).get("status") == "success"
    except:
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# BEHAVIOR → GEOMETRIC CONSTRAINTS
# ═══════════════════════════════════════════════════════════════════════════════

def derive_constraints(behavior_data, bodies_data):
    """Convert behavior analysis into DO/DON'T rules for script generation.

    These rules prevent the script from creating geometry that would
    block moving parts or violate physical behavior requirements.
    """
    rules = []
    behaviors = behavior_data.get("behaviors", [])
    bodies = bodies_data.get("bodies", [])

    for b in behaviors:
        part = b.get("part", "").lower()
        motion = b.get("motion", "none").lower()
        axis = b.get("axis", "").upper()

        if motion == "linear":
            # Sliding parts (drawers, sliding doors)
            rules.append(
                f"CONSTRAINT ({part}, linear on {axis}): "
                f"DO NOT place any structural geometry (rails, dividers, trim) in front of "
                f"the {part} face that would visually block or intersect its slide path. "
                f"The {part} front face must be flush with or slightly recessed from the carcass front. "
                f"Any horizontal rail between rows must be BEHIND the {part} front face, not in front of it."
            )
            rules.append(
                f"CONSTRAINT ({part}, clearance): "
                f"Leave at least 3-5mm gap around all sides of the {part} so it can slide freely. "
                f"The {part} must NOT touch the carcass sides, top, or bottom panels."
            )
            if "drawer" in part:
                rules.append(
                    f"CONSTRAINT ({part}, geometry): "
                    f"Each drawer MUST be a 5-sided open-top box, NOT just a front panel. "
                    f"It needs: front panel (visible face), bottom panel, left side wall, right side wall, "
                    f"and back wall. The top is OPEN. The box extends back into the carcass ~80% of the "
                    f"carcass depth. Wall thickness ~12mm. Join all 5 panels into ONE drawer object. "
                    f"The front panel is the decorative face; the other 4 panels form the box behind it."
                )

        elif motion == "rotational":
            # Swinging parts (doors, lids)
            rules.append(
                f"CONSTRAINT ({part}, rotational on {axis}): "
                f"DO NOT place any geometry in the {part}'s swing arc. "
                f"The {part} must be able to swing open 90+ degrees without collision. "
                f"Adjacent {part}s should not collide when opened simultaneously."
            )
            rules.append(
                f"CONSTRAINT ({part}, hinge): "
                f"The {part}'s origin/pivot MUST be at the hinge edge using set_origin_keep_visual(). "
                f"Knobs/handles go on the OPPOSITE side of the hinge."
            )
            count = b.get("count", 0)
            if "door" in part and count > 1:
                rules.append(
                    f"CONSTRAINT ({part}, dividers): "
                    f"There are {count} {part}s side by side. The carcass frame MUST include "
                    f"{count - 1} vertical divider stiles between them. Each stile is a vertical panel "
                    f"(panel_thickness wide, full depth, full row height) that the {part}s close against. "
                    f"Without these stiles, there would be visible gaps between adjacent {part}s."
                )

    # Global structural rules from bodies
    separate_parts = [b["name"] for b in bodies if b.get("separate", True)]
    joined_parts = [b["name"] for b in bodies if not b.get("separate", True)]

    if separate_parts:
        rules.append(
            f"CONSTRAINT (separation): These MUST be separate Blender objects: {separate_parts}. "
            f"Do NOT join them into the carcass/frame."
        )

    if joined_parts:
        rules.append(
            f"CONSTRAINT (joining): These should be joined into ONE frame object: {joined_parts}. "
            f"Use bpy.ops.object.join() after creating all frame panels."
        )

    # Visual rules
    rules.append(
        "CONSTRAINT (visual): Drawer fronts and door fronts must be the OUTERMOST geometry "
        "on the front face. No rail, divider, or frame element should protrude past them. "
        "Rails between rows should be recessed or flush with the inner edge of the front panels."
    )

    rules.append(
        "CONSTRAINT (proportions): Moving parts (doors, drawers) should fill their grid cell "
        "with only a small gap (3-5mm) around edges. They should NOT be significantly smaller "
        "than their cell opening."
    )

    # Cavity opening rule — if ANY part has rotational or linear motion (doors/drawers),
    # the body mesh must NOT have faces covering the opening
    has_openings = any(b.get("motion", "none") in ("linear", "rotational") for b in behaviors)
    if has_openings:
        rules.append(
            "CONSTRAINT (cavity openings): The body/chassis/carcass mesh must NOT have any faces "
            "covering the door or drawer openings. When a door opens, the cavity behind it must be "
            "visible and accessible — no front panel faces blocking the opening. The door IS the "
            "front face when closed. If importing an existing mesh (OBJ/blend), find and DELETE any "
            "front-facing faces (normal pointing -Y or toward camera) in the door/drawer opening area. "
            "Verify by hiding the doors in Blender and checking that you can see into the cavity."
        )

    return rules


# ═══════════════════════════════════════════════════════════════════════════════
# MATH ENGINE — pre-compute exact coordinates from A+B spec
# ═══════════════════════════════════════════════════════════════════════════════

def compute_coordinates(results, vdata):
    """Pre-compute exact xyz positions from A+B data.

    Returns a coordinate table that C MUST use — no guessing allowed.
    """
    gt = results.get("gemini_type", {}).get("parsed", {})
    gd = results.get("gemini_dims", {}).get("parsed", {})
    row_ratios = vdata.get("row_ratios", {})

    category = gt.get("category", "other")
    geo_approach = gt.get("geometry_approach", "")

    # Overall dims in meters
    W = gd.get("overall_width_mm", 1000) / 1000
    D = gd.get("overall_depth_mm", 400) / 1000
    H = gd.get("overall_height_mm", 800) / 1000

    coords = {
        "overall": {"width_m": round(W, 4), "depth_m": round(D, 4), "height_m": round(H, 4)},
        "objects": [],
    }

    if geo_approach == "panel_construction" or category == "furniture":
        # Use geometry_math.py for furniture
        T = gd.get("panel_thickness_mm", 20) / 1000
        leg_h = gd.get("leg_height_mm", 0) / 1000

        # Determine grid from A
        components = gt.get("components", [])
        n_cols = gt.get("grid", {}).get("columns", 3) if "grid" in gt else 3
        n_rows = gt.get("grid", {}).get("rows", 2) if "grid" in gt else 2

        # Row heights from vision ratios or dims
        row_heights_mm = gd.get("row_heights_mm", [])
        carcass_h = H - leg_h
        inner_h = carcass_h - (n_rows + 1) * T

        if row_ratios and n_rows == 2:
            door_ratio = row_ratios.get("door_ratio", 0.67)
            drawer_ratio = row_ratios.get("drawer_ratio", 0.33)
            row_heights = [inner_h * door_ratio, inner_h * drawer_ratio]
        elif row_heights_mm and len(row_heights_mm) == n_rows:
            row_heights = [rh / 1000 for rh in row_heights_mm]
            # Rescale to fit
            total = sum(row_heights)
            if total > 0:
                row_heights = [rh * inner_h / total for rh in row_heights]
        else:
            row_heights = [inner_h / n_rows] * n_rows

        try:
            from geometry_math import CabinetGrid
            grid = CabinetGrid(
                width=W, depth=D, height=carcass_h,
                columns=n_cols, rows=n_rows, panel_t=T,
                row_heights=row_heights, leg_height=leg_h,
            )

            coords["grid"] = {
                "columns": n_cols, "rows": n_rows,
                "panel_t_m": round(T, 4),
                "leg_h_m": round(leg_h, 4),
                "carcass_h_m": round(carcass_h, 4),
                "row_heights_m": [round(h, 4) for h in row_heights],
                "col_width_m": round(grid.col_w, 4),
                "front_y_m": round(grid.front_panel_y(), 4),
                "gap_m": round(grid.gap, 4),
            }

            # Carcass panels
            coords["carcass_panels"] = []
            for p in grid.carcass_panels():
                coords["carcass_panels"].append({
                    "name": p["name"],
                    "center": [round(p["cx"], 4), round(p["cy"], 4), round(p["cz"], 4)],
                    "size": [round(p["w"], 4), round(p["d"], 4), round(p["h"], 4)],
                })

            # Legs
            coords["legs"] = []
            for i, (lx, ly, lz) in enumerate(grid.leg_positions()):
                coords["legs"].append({
                    "name": f"Leg_{i}",
                    "center": [round(lx, 4), round(ly, 4), round(lz, 4)],
                    "size": [round(grid.leg_width, 4), round(grid.leg_width, 4), round(grid.leg_height, 4)],
                })

            # Determine row types — try multiple sources
            row_types = []

            # Source 1: gemini_type grid.row_contents
            row_contents = gt.get("grid", {}).get("row_contents", []) if "grid" in gt else []
            for rc in row_contents:
                rc_lower = rc.lower() if isinstance(rc, str) else ""
                if "drawer" in rc_lower:
                    row_types.append("drawers")
                elif "door" in rc_lower:
                    row_types.append("doors")
                else:
                    row_types.append("unknown")

            # Source 2: if row_types incomplete, infer from claude_behavior
            if len(row_types) != n_rows or "unknown" in row_types:
                cb = results.get("claude_behavior", {}).get("parsed", {})
                behaviors = cb.get("behaviors", [])
                has_drawers = any("drawer" in b.get("part", "").lower() for b in behaviors)
                has_doors = any("door" in b.get("part", "").lower() for b in behaviors)

                if n_rows == 2 and has_drawers and has_doors:
                    # Standard: doors bottom, drawers top
                    row_types = ["doors", "drawers"]
                elif n_rows == 1 and has_drawers:
                    row_types = ["drawers"]
                elif n_rows == 1 and has_doors:
                    row_types = ["doors"]
                else:
                    row_types = ["unknown"] * n_rows

            # Source 3: if still unknown, check vision spatial layout
            if "unknown" in row_types:
                spatial = vdata.get("spatial_layout", {})
                if spatial.get("bottom_row") and spatial.get("top_row") and n_rows == 2:
                    row_types = [spatial["bottom_row"], spatial["top_row"]]

            coords["grid"]["row_types"] = row_types

            # Per-cell objects (doors, drawers, hardware)
            for row in range(n_rows):
                rt = row_types[row] if row < len(row_types) else "unknown"
                for col in range(n_cols):
                    cx = grid.col_center(col)
                    cz = grid.row_center(row)
                    fy = grid.front_panel_y()

                    if rt == "drawers":
                        # Drawer: 5-sided box, depth = ~80% of carcass depth
                        dw, dt, dh = grid.door_dims(col, row)  # front panel dims
                        drawer_depth = round(D * 0.8, 4)
                        cell_size = [round(dw, 4), drawer_depth, round(dh, 4)]
                        cell_type = "drawer"
                    else:
                        # Door: flat panel
                        dw, dt, dh = grid.door_dims(col, row)
                        cell_size = [round(dw, 4), round(dt, 4), round(dh, 4)]
                        cell_type = "door"

                    coords["objects"].append({
                        "name": f"Cell_r{row}_c{col}",
                        "type": cell_type,
                        "col": col, "row": row,
                        "center": [round(cx, 4), round(fy, 4), round(cz, 4)],
                        "size": cell_size,
                        "col_center_x": round(cx, 4),
                        "row_center_z": round(cz, 4),
                        "row_bottom_z": round(grid.row_bottom(row), 4),
                        "row_top_z": round(grid.row_top(row), 4),
                        "col_left_x": round(grid.col_left_edge(col), 4),
                        "col_right_x": round(grid.col_right_edge(col), 4),
                    })

                    # Knob/pull positions
                    kx, ky, kz = grid.knob_position(col, row)
                    coords["objects"][-1]["knob_pos"] = [round(kx, 4), round(ky, 4), round(kz, 4)]
                    px, py, pz = grid.pull_position(col, row)
                    coords["objects"][-1]["pull_pos"] = [round(px, 4), round(py, 4), round(pz, 4)]

        except Exception as e:
            coords["error"] = f"CabinetGrid failed: {e}"

    elif geo_approach == "revolution":
        # For bolts, glasses — just pass dims, C handles profile
        coords["approach"] = "revolution"
        comp_dims = gd.get("components", [])
        for cd in comp_dims:
            coords["objects"].append({
                "name": cd.get("name", "part"),
                "width_m": round(cd.get("width_mm", 0) / 1000, 4),
                "depth_m": round(cd.get("depth_mm", 0) / 1000, 4),
                "height_m": round(cd.get("height_mm", 0) / 1000, 4),
            })

    return coords


# ═══════════════════════════════════════════════════════════════════════════════
# PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

def run_agent(func, key, model, prompt, image_path, result_dict, agent_name):
    """Thread target for a single agent."""
    try:
        raw = func(key, model, prompt, image_path=image_path)
        result_dict[agent_name] = {"raw": raw, "parsed": parse_json_response(raw), "error": None}
    except Exception as e:
        result_dict[agent_name] = {"raw": "", "parsed": None, "error": str(e)}


def build_spec_summary(results, vdata):
    """Build a complete text summary of all agent + vision results for Path C."""
    lines = ["# COMPLETE OBJECT ANALYSIS", ""]

    # Path A results
    for name in ["gemini_type", "gemini_dims", "gemini_materials",
                 "claude_behavior", "claude_bodies", "claude_geometry"]:
        r = results.get(name, {})
        parsed = r.get("parsed", {})
        if parsed:
            lines.append(f"## {name}:")
            lines.append(json.dumps(parsed, indent=2))
            lines.append("")

    # Pre-computed coordinates from math engine
    coords = compute_coordinates(results, vdata)
    if coords:
        lines.append("## PRE-COMPUTED COORDINATES (from math engine)")
        lines.append("MANDATORY: Use these EXACT coordinates. Do NOT compute your own positions.")
        lines.append("Every panel, door, drawer, knob, and handle position is pre-calculated.")
        lines.append("Copy these numbers directly into your script as constants.")
        lines.append("")
        lines.append(json.dumps(coords, indent=2))
        lines.append("")

    # Derive behavioral constraints
    behavior = results.get("claude_behavior", {}).get("parsed", {})
    bodies = results.get("claude_bodies", {}).get("parsed", {})
    if behavior or bodies:
        constraints = derive_constraints(behavior or {}, bodies or {})
        if constraints:
            lines.append("## GEOMETRIC CONSTRAINTS (derived from behavior analysis):")
            lines.append("These are MANDATORY rules. Violating them will break physics simulation.")
            lines.append("")
            for i, rule in enumerate(constraints, 1):
                lines.append(f"  {i}. {rule}")
            lines.append("")

    # Path B results
    if vdata:
        lines.append("## Vision Stack (measured data):")
        lines.append(f"  Confirmed counts: {vdata.get('counts', {})}")
        lines.append(f"  Overall measured dims: {vdata.get('overall_dims', {})}")
        lines.append(f"  Row ratios: {vdata.get('row_ratios', {})}")
        lines.append(f"  Depth consistency: {vdata.get('depth_consistency', 'N/A')}")

        spatial = vdata.get("spatial_layout", {})
        if spatial:
            lines.append(f"  Spatial layout: {spatial}")

        measured = vdata.get("measured_by_type", {})
        if measured:
            lines.append(f"  Per-component measured dimensions:")
            for label, mdata in measured.items():
                lines.append(f"    {label}: {mdata['avg_width_mm']:.0f}×{mdata['avg_height_mm']:.0f}mm (n={mdata['count']})")

        sampled = vdata.get("sampled_colors", {})
        if sampled:
            lines.append(f"  Pixel-sampled colors (from image):")
            for label, cdata in sampled.items():
                metal = "metallic" if cdata.get("is_metallic") else "textured"
                lines.append(f"    {label}: RGB={cdata['avg_rgb']} ({metal})")

        lines.append("")

    return "\n".join(lines)


def apply_physx_to_usd(usd_path, behavior_data, bodies_data):
    """Path D physics finalizer.

    Takes V4's just-exported geometry-only USD and stamps PhysX joints onto it
    based on the behavior + bodies dicts that Path A already produced. No AI,
    no Blender re-import, no V5. Pure deterministic pxr post-pass.

    For each top-level Xform under the root prim:
      - Match against `behaviors` by part-name substring (door/drawer/knob/handle).
      - Unmatched prim → chassis (articulation root, fixed to world).
      - Doors (rotational) → RevoluteJoint, axis Z, limits [0, 110°].
      - Drawers (linear) → PrismaticJoint, axis Y, limits [0, 0.85*depth].
      - Knobs/handles → FixedJoint to their parent door/drawer.

    Pivot resolution:
      - If the prim already has a non-zero xformOp:translate (V4 set this for
        doors via set_origin_keep_visual), the mesh verts are already body-local
        and translate is the pivot in chassis space.
      - Otherwise (drawers/knobs/handles built at world position with origin at
        world 0), compute the mesh bbox center as the pivot and shift the mesh
        points so they become body-local.

    After the pivot is captured, the prim's xformOp:translate is zeroed so PhysX
    is the sole positioning authority (no double offset).
    """
    try:
        from pxr import Usd, UsdPhysics, UsdGeom, Sdf, Gf
    except ImportError:
        print("  Path D physics: pxr not available — run inside an environment with USD installed")
        return False

    print("\n" + "=" * 70)
    print("  PATH D — PHYSICS FINALIZER (deterministic pxr post-pass)")
    print("=" * 70)

    stage = Usd.Stage.Open(usd_path)
    if not stage:
        print(f"  ERROR: cannot open {usd_path}")
        return False

    # Find articulation root: prefer /root, else first top-level Xform
    root = stage.GetPrimAtPath("/root")
    if not root or not root.IsValid():
        for p in stage.GetPseudoRoot().GetChildren():
            if p.GetTypeName() == "Xform":
                root = p
                break
    if not root or not root.IsValid():
        print("  ERROR: no root Xform found in USD")
        return False

    UsdPhysics.ArticulationRootAPI.Apply(root)

    behaviors = behavior_data.get("behaviors", []) if behavior_data else []

    def find_behavior(prim_name):
        # Prefer the part word that appears EARLIEST in the prim name, so e.g.
        # "Knob_Door_Left" matches "knob" (position 0) instead of "door" (position 5).
        nl = prim_name.lower()
        candidates = []
        for b in behaviors:
            part_word = (b.get("part", "") or "").lower().strip()
            if part_word and part_word in nl:
                candidates.append((nl.find(part_word), b))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    def classify_by_name(prim_name):
        """Name-based fallback classification when behavior data is missing or
        uses unexpected keys. Returns one of: 'attachment', 'door', 'drawer',
        'lid', 'shelf', 'chassis'."""
        nl = prim_name.lower()
        if "knob" in nl or "handle" in nl or "pull" in nl:
            return "attachment"
        if "door" in nl:
            return "door"
        if "drawer" in nl:
            return "drawer"
        if "lid" in nl:
            return "lid"
        if "shelf" in nl:
            return "shelf"
        return "chassis"

    # ── Pass 1: classify, compute pivots, shift verts to body-local ──────
    parts_info = []  # list of (prim, mesh, behavior, pivot, motion, kind)
    chassis_candidates = []  # (prim, mesh, bbox_volume, name_score)

    for prim in root.GetChildren():
        if prim.GetTypeName() != "Xform":
            continue

        kind = classify_by_name(prim.GetName())
        b = find_behavior(prim.GetName())
        mesh = next((c for c in prim.GetChildren() if c.GetTypeName() == "Mesh"), None)

        if mesh is None:
            continue

        # Determine motion: name-based default, overridden by behavior dict if present.
        if kind == "door" or kind == "lid":
            motion = "rotational"
        elif kind == "drawer":
            motion = "linear"
        elif kind == "attachment":
            motion = "none"
        else:
            motion = "none"
        if b:
            b_motion = (b.get("motion", "") or "").lower().strip()
            if b_motion:
                motion = b_motion

        if kind == "chassis":
            # Score chassis-likeness by name. Compute bbox volume too.
            nl = prim.GetName().lower()
            name_score = 0
            for kw in ("main_frame", "main", "frame", "carcass", "chassis", "body", "cabinet"):
                if kw in nl:
                    name_score = 10 if kw in ("main_frame", "carcass", "chassis") else 5
                    break
            pts = mesh.GetAttribute("points").Get()
            if pts and len(pts) > 0:
                xs = [p[0] for p in pts]; ys = [p[1] for p in pts]; zs = [p[2] for p in pts]
                vol = (max(xs)-min(xs)) * (max(ys)-min(ys)) * (max(zs)-min(zs))
            else:
                vol = 0.0
            chassis_candidates.append((prim, mesh, vol, name_score))
            continue

        # Pivot: prefer existing translate, else compute from mesh bbox + shift
        translate_attr = prim.GetAttribute("xformOp:translate")
        existing_t = translate_attr.Get() if translate_attr and translate_attr.HasValue() else None

        if existing_t and any(abs(v) > 1e-9 for v in (existing_t[0], existing_t[1], existing_t[2])):
            pivot = Gf.Vec3f(float(existing_t[0]), float(existing_t[1]), float(existing_t[2]))
        else:
            pts = mesh.GetAttribute("points").Get()
            if not pts or len(pts) == 0:
                continue
            xs = [p[0] for p in pts]; ys = [p[1] for p in pts]; zs = [p[2] for p in pts]
            cx = (min(xs) + max(xs)) / 2.0
            cy = (min(ys) + max(ys)) / 2.0
            cz = (min(zs) + max(zs)) / 2.0
            pivot = Gf.Vec3f(cx, cy, cz)
            new_pts = [(p[0] - cx, p[1] - cy, p[2] - cz) for p in pts]
            mesh.GetAttribute("points").Set(new_pts)

        parts_info.append((prim, mesh, b, pivot, motion, kind))

    # Pick the chassis: prefer name-scored candidates, then largest bbox volume.
    if not chassis_candidates:
        print("  ERROR: no chassis prim found (every prim looked like a moving part)")
        return False
    chassis_candidates.sort(key=lambda x: (x[3], x[2]), reverse=True)
    chassis_prim, chassis_mesh, _, _ = chassis_candidates[0]

    chassis_path = str(chassis_prim.GetPath())
    print(f"  Chassis: {chassis_path}")

    # ── Chassis: rigid body + collision + fixed-to-world ─────────────────
    UsdPhysics.RigidBodyAPI.Apply(chassis_prim)
    UsdPhysics.MassAPI.Apply(chassis_prim).CreateMassAttr(30.0)
    if chassis_mesh:
        UsdPhysics.CollisionAPI.Apply(chassis_mesh)
        UsdPhysics.MeshCollisionAPI.Apply(chassis_mesh).CreateApproximationAttr("convexDecomposition")
    fj = UsdPhysics.FixedJoint.Define(stage, f"{chassis_path}/fixed_to_world")
    fj.CreateBody1Rel().SetTargets([chassis_path])

    # Drop any other chassis_candidates that lost the contest — treat them as
    # static decoration merged into the chassis (no rigid body, just collision).
    for prim, mesh, _, _ in chassis_candidates[1:]:
        if mesh:
            UsdPhysics.CollisionAPI.Apply(mesh)
            UsdPhysics.MeshCollisionAPI.Apply(mesh).CreateApproximationAttr("convexHull")
        print(f"    {prim.GetName()}: extra chassis candidate → static collider only")

    # ── Pass 2: pivot lookup for parent matching ─────────────────────────
    pivot_lookup = {prim.GetName(): pivot for prim, _, _, pivot, _, _ in parts_info}
    name_to_path = {prim.GetName(): str(prim.GetPath()) for prim, _, _, _, _, _ in parts_info}

    # ── Pass 3: write physics for moving parts ───────────────────────────
    joint_count = 0

    for prim, mesh, behavior, pivot, motion, kind in parts_info:
        path = str(prim.GetPath())
        name = prim.GetName()
        name_l = name.lower()
        part_word = (behavior.get("part", "") or "").lower() if behavior else ""

        axis_str = (behavior.get("axis", "") or "").upper() if behavior else ""
        if axis_str not in ("X", "Y", "Z"):
            axis_str = "Z" if motion == "rotational" else "Y"

        # Zero out translate — PhysX localPos0 is the sole positioning authority.
        ta = prim.GetAttribute("xformOp:translate")
        if ta:
            ta.Set(Gf.Vec3d(0, 0, 0))

        # Mass per kind (sane defaults)
        is_attachment = (kind == "attachment")
        if is_attachment:
            mass_kg = 0.1
        elif kind == "drawer":
            mass_kg = 3.0
        elif kind in ("door", "lid"):
            mass_kg = 5.0
        else:
            mass_kg = 1.0

        UsdPhysics.RigidBodyAPI.Apply(prim)
        UsdPhysics.MassAPI.Apply(prim).CreateMassAttr(mass_kg)
        UsdPhysics.CollisionAPI.Apply(mesh)
        UsdPhysics.MeshCollisionAPI.Apply(mesh).CreateApproximationAttr("convexHull")

        if is_attachment:
            # Find parent door/drawer by name match. Handles both
            # "Knob_Door_Left" (prefix) and "Right_Door_Knob" (suffix).
            parent_path = chassis_path
            parent_pivot = Gf.Vec3f(0, 0, 0)
            best_match = None
            best_overlap = 0
            for other_name, other_path in name_to_path.items():
                if other_name == name:
                    continue
                ol = other_name.lower()
                # Skip other attachments
                if "knob" in ol or "handle" in ol or "pull" in ol:
                    continue
                if "door" not in ol and "drawer" not in ol and "lid" not in ol:
                    continue
                # Score by how many tokens of `other_name` appear in our name
                tokens = [t for t in ol.replace("-", "_").split("_") if t]
                overlap = sum(1 for t in tokens if t in name_l)
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_match = other_name
            if best_match:
                parent_path = name_to_path[best_match]
                parent_pivot = pivot_lookup[best_match]

            local0 = Gf.Vec3f(pivot[0] - parent_pivot[0],
                              pivot[1] - parent_pivot[1],
                              pivot[2] - parent_pivot[2])
            j = UsdPhysics.FixedJoint.Define(stage, f"{path}/fixed_joint")
            j.CreateBody0Rel().SetTargets([parent_path])
            j.CreateBody1Rel().SetTargets([path])
            j.CreateLocalPos0Attr(local0)
            j.CreateLocalPos1Attr(Gf.Vec3f(0, 0, 0))
            joint_count += 1
            print(f"    {name}: fixed → {parent_path.split('/')[-1]}")
            continue

        if motion == "rotational":
            j = UsdPhysics.RevoluteJoint.Define(stage, f"{path}/revolute_joint")
            j.CreateBody0Rel().SetTargets([chassis_path])
            j.CreateBody1Rel().SetTargets([path])
            j.CreateAxisAttr(axis_str)
            j.CreateLowerLimitAttr(0.0)
            j.CreateUpperLimitAttr(110.0)
            j.CreateLocalPos0Attr(pivot)
            j.CreateLocalPos1Attr(Gf.Vec3f(0, 0, 0))
            j.CreateCollisionEnabledAttr(False)
            drive = UsdPhysics.DriveAPI.Apply(j.GetPrim(), "angular")
            drive.CreateTargetPositionAttr(0.0)
            drive.CreateDampingAttr(50.0)
            drive.CreateStiffnessAttr(0.0)
            joint_count += 1
            print(f"    {name}: revolute axis={axis_str} pivot=({pivot[0]:.3f},{pivot[1]:.3f},{pivot[2]:.3f}) limits=[0,110°]")

        elif motion == "linear":
            pts = mesh.GetAttribute("points").Get()
            if pts:
                axis_idx = {"X": 0, "Y": 1, "Z": 2}[axis_str]
                vs = [p[axis_idx] for p in pts]
                slide = (max(vs) - min(vs)) * 0.85
            else:
                slide = 0.3
            j = UsdPhysics.PrismaticJoint.Define(stage, f"{path}/prismatic_joint")
            j.CreateBody0Rel().SetTargets([chassis_path])
            j.CreateBody1Rel().SetTargets([path])
            j.CreateAxisAttr(axis_str)
            j.CreateLowerLimitAttr(0.0)
            j.CreateUpperLimitAttr(float(slide))
            j.CreateLocalPos0Attr(pivot)
            j.CreateLocalPos1Attr(Gf.Vec3f(0, 0, 0))
            j.CreateCollisionEnabledAttr(False)
            drive = UsdPhysics.DriveAPI.Apply(j.GetPrim(), "linear")
            drive.CreateTargetPositionAttr(0.0)
            drive.CreateDampingAttr(100.0)
            drive.CreateStiffnessAttr(0.0)
            joint_count += 1
            print(f"    {name}: prismatic axis={axis_str} pivot=({pivot[0]:.3f},{pivot[1]:.3f},{pivot[2]:.3f}) limits=[0,{slide:.3f}m]")

    stage.GetRootLayer().Save()
    print(f"  ✓ Path D physics: {joint_count} joints written")
    return True


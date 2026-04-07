#!/usr/bin/env python3
"""
V7 Stage C — Reconciler + Math Engine

1. Reconcile dims per part (A vs B):
   - B null / low confidence → use A
   - Differ < 20% → average
   - Differ > 20% → flag conflict, use B
   - B high confidence → B wins

2. Validate contract:
   - RIGID: geometry, dims, material, smooth_shading
   - ARTICULATED: all 8 Blender-owned fields

3. Math Engine:
   - Compute exact xyz position per part (deterministic)
   - All units in meters

Output: stage_c.json — unified spec ready for Stage D
"""

import json
import os
import sys
import time

_DIR = os.path.dirname(os.path.abspath(__file__))
_SIMREADY_DIR = os.path.dirname(_DIR)
_TOOLS_DIR = os.path.dirname(_SIMREADY_DIR)

sys.path.insert(0, _SIMREADY_DIR)
from image_to_simready.geometry_math import CabinetGrid


# ═══════════════════════════════════════════════════════════════════
# RECONCILER
# ═══════════════════════════════════════════════════════════════════

def _reconcile_dim(a_val, b_val, b_conf, dim_name, part_name, conflicts):
    """Reconcile a single dimension between A and B."""
    if b_val is None or b_val <= 0:
        return a_val, "A_only"

    if b_conf < 0.80:
        return a_val, "A_low_b_conf"

    diff_pct = abs(a_val - b_val) / max(a_val, 1) * 100

    if diff_pct > 20:
        conflicts.append(
            f"CONFLICT {part_name}.{dim_name}: A={a_val:.0f}mm B={b_val:.0f}mm diff={diff_pct:.1f}% → using B"
        )
        return b_val, "B_conflict"

    if b_conf >= 0.90:
        return b_val, "B_wins"

    avg = (a_val + b_val) / 2
    return avg, "A+B_avg"


def reconcile_dims(stage_a: dict, stage_b: dict) -> dict:
    """Match B components to A parts by label similarity, reconcile dims."""
    conflicts = []
    b_components = stage_b.get("components", [])
    b_overall    = stage_b.get("overall_dims", {})

    # Build B lookup: label → best component (highest confidence)
    b_by_label = {}
    for comp in b_components:
        if not comp.get("confirmed"):
            continue
        label = comp["label"].lower()
        existing = b_by_label.get(label)
        if existing is None or comp.get("dino_confidence", 0) > existing.get("dino_confidence", 0):
            b_by_label[label] = comp

    reconciled_parts = []

    for part in stage_a["parts"]:
        name = part["part"]
        a_dims = part["dims_mm"]
        a_w = a_dims.get("width", 0)
        a_d = a_dims.get("depth", 0)
        a_h = a_dims.get("height", 0)

        # Try to match B component by name similarity
        b_match = None
        name_lower = name.lower().replace("_", " ")
        for label, comp in b_by_label.items():
            if label in name_lower or name_lower in label or any(w in label for w in name_lower.split()):
                b_match = comp
                break

        if b_match and "measured_width_mm" in b_match:
            b_w    = b_match.get("measured_width_mm", 0)
            b_h    = b_match.get("measured_height_mm", 0)
            b_conf = b_match.get("b_confidence", 0.85)

            rw, sw = _reconcile_dim(a_w, b_w, b_conf, "width",  name, conflicts)
            rh, sh = _reconcile_dim(a_h, b_h, b_conf, "height", name, conflicts)
            # Depth: B can't measure it from single image
            rd, sd = a_d, "A_only"
        else:
            rw, sw = a_w, "A_only"
            rd, sd = a_d, "A_only"
            rh, sh = a_h, "A_only"

        p = dict(part)
        p["dims_reconciled"] = {
            "width_mm":  round(rw, 1),
            "depth_mm":  round(rd, 1),
            "height_mm": round(rh, 1),
        }
        p["dims_source"] = {"width": sw, "depth": sd, "height": sh}
        p["b_match"] = b_match["label"] if b_match else None
        reconciled_parts.append(p)

    return reconciled_parts, conflicts, b_overall


# ═══════════════════════════════════════════════════════════════════
# CONTRACT VALIDATOR
# ═══════════════════════════════════════════════════════════════════

RIGID_REQUIRED      = ["geometry", "dims_reconciled", "material"]
ARTICULATED_REQUIRED = RIGID_REQUIRED + ["pivot", "parent", "cavity_face_delete", "is_separate_object"]

def validate_contract(parts: list, articulation: str) -> list:
    """Check all required fields are present. Returns list of errors."""
    errors = []
    required = ARTICULATED_REQUIRED if articulation == "ARTICULATED" else RIGID_REQUIRED

    for p in parts:
        name = p["part"]
        for field in required:
            val = p.get(field)
            if val is None or val == "" or val == {}:
                errors.append(f"MISSING {name}.{field}")

        # Pivot must be specific for revolute parts
        behavior = p.get("behavior", {})
        btype = behavior.get("behavior_type", "")
        if btype == "ROTATIONAL" and articulation == "ARTICULATED":
            pivot = p.get("pivot", "")
            if "N/A" in str(pivot) or pivot == "":
                errors.append(f"INVALID {name}.pivot — ROTATIONAL part must have explicit pivot position")

        # Material fields
        mat = p.get("material", {})
        if isinstance(mat, dict):
            for mf in ["type", "color_rgb", "roughness"]:
                if mf not in mat or mat[mf] is None:
                    errors.append(f"MISSING {name}.material.{mf}")

    return errors


# ═══════════════════════════════════════════════════════════════════
# MATH ENGINE — compute xyz positions
# ═══════════════════════════════════════════════════════════════════

def compute_positions(parts: list, articulation: str, b_overall: dict) -> list:
    """Compute exact xyz positions for each part. All units in meters."""

    # Find root/body dimensions
    root = next((p for p in parts if p.get("parent") in ("none", None, "")), parts[0])
    root_dims = root["dims_reconciled"]

    W = root_dims["width_mm"]  / 1000
    D = root_dims["depth_mm"]  / 1000
    H = root_dims["height_mm"] / 1000

    # Use B overall width if available and better
    if b_overall.get("total_width_mm"):
        b_W = b_overall["total_width_mm"] / 1000
        if abs(b_W - W) / max(W, 0.01) < 0.20:
            W = (W + b_W) / 2

    if articulation == "RIGID":
        # Single object — origin at bottom center
        for p in parts:
            p["position_xyz"] = [0.0, 0.0, 0.0]
        return parts

    # ARTICULATED — use CabinetGrid for furniture
    # Count rows and columns from part names
    drawers = [p for p in parts if "drawer" in p["part"] and "handle" not in p["part"]]
    doors   = [p for p in parts if "door"   in p["part"] and "knob"   not in p["part"]]
    n_cols  = max(len(drawers), len(doors), 1)
    n_rows  = 2 if (drawers and doors) else 1

    # Panel thickness
    T = 0.020  # 20mm default

    # Leg height
    legs = [p for p in parts if "leg" in p["part"]]
    leg_h = legs[0]["dims_reconciled"]["height_mm"] / 1000 if legs else 0.0

    # Row heights from reconciled dims
    carcass_h  = H - leg_h
    drawer_h   = drawers[0]["dims_reconciled"]["height_mm"] / 1000 if drawers else carcass_h * 0.25
    door_h     = doors[0]["dims_reconciled"]["height_mm"]   / 1000 if doors   else carcass_h * 0.65
    row_heights = [door_h, drawer_h] if drawers and doors else [carcass_h - 2*T]

    try:
        grid = CabinetGrid(
            width=W, depth=D, height=carcass_h,
            columns=n_cols, rows=n_rows, panel_t=T,
            row_heights=row_heights, leg_height=leg_h,
        )
    except Exception as e:
        print(f"  [C] CabinetGrid error: {e} — falling back to simple layout")
        grid = None

    # Cabinet front face Y in world space (positive Y = toward viewer)
    FRONT_Y = D / 2

    for p in parts:
        name = p["part"]
        dims = p["dims_reconciled"]
        pw = dims["width_mm"]  / 1000
        pd = dims["depth_mm"]  / 1000
        ph = dims["height_mm"] / 1000

        xyz = [0.0, 0.0, 0.0]

        if p.get("parent") in ("none", None, ""):
            # Root body — centered, origin at bottom-center
            xyz = [0.0, 0.0, leg_h + carcass_h / 2]

        elif "drawer" in name and "handle" not in name and grid:
            # Drawers: front face flush with cabinet front face when closed
            # Center Y = FRONT_Y - drawer_depth/2
            col_idx = ["left", "middle", "right"].index(
                next((w for w in ["left", "middle", "right"] if w in name), "middle")
            ) if n_cols == 3 else 0
            try:
                cx = grid.col_center(col_idx)
                cz = grid.row_center(1) + leg_h
                cy = FRONT_Y - pd / 2
                xyz = [round(cx, 4), round(cy, 4), round(cz, 4)]
            except:
                xyz = [0.0, FRONT_Y - pd / 2, leg_h + door_h + T + drawer_h / 2]

        elif "door" in name and "knob" not in name and grid:
            # Doors: front face flush with cabinet front face when closed
            # Center Y = FRONT_Y - door_thickness/2
            col_idx = ["left", "middle", "right"].index(
                next((w for w in ["left", "middle", "right"] if w in name), "middle")
            ) if n_cols == 3 else 0
            try:
                cx = grid.col_center(col_idx)
                cz = grid.row_center(0) + leg_h
                cy = FRONT_Y - pd / 2
                xyz = [round(cx, 4), round(cy, 4), round(cz, 4)]
            except:
                xyz = [0.0, FRONT_Y - pd / 2, leg_h + door_h / 2]

        elif "handle" in name or "knob" in name:
            # Protrude beyond parent's front face
            # handle_center_Y = parent_center_Y + parent_depth/2 + handle_depth/2
            parent_name = p.get("parent", "")
            parent = next((pp for pp in parts if pp["part"] == parent_name), None)
            if parent and "position_xyz" in parent:
                ppx = parent["position_xyz"]
                parent_d = parent["dims_reconciled"]["depth_mm"] / 1000
                handle_y = ppx[1] + parent_d / 2 + pd / 2
                xyz = [round(ppx[0], 4), round(handle_y, 4), round(ppx[2], 4)]
            else:
                xyz = [0.0, FRONT_Y + pd / 2, leg_h + carcass_h / 2]

        elif "top" in name or "top_panel" in name:
            xyz = [0.0, 0.0, round(leg_h + carcass_h + ph / 2, 4)]

        elif "leg" in name:
            corner_map = {"left": -W/2 + pw/2, "right": W/2 - pw/2}
            lx = corner_map.get(next((w for w in ["left", "right"] if w in name), "left"), 0)
            xyz = [round(lx, 4), 0.0, round(leg_h / 2, 4)]

        elif "divider" in name:
            # Already set by _infer_invisible_parts — keep as-is
            pass

        p["position_xyz"] = [round(v, 4) for v in xyz]

    return parts


# ═══════════════════════════════════════════════════════════════════
# INVISIBLE PARTS INFERENCE
# ═══════════════════════════════════════════════════════════════════

def _infer_invisible_parts(parts: list, articulation: str) -> list:
    """
    Add structurally-required parts that are invisible in the image:
      - Vertical dividers between door bays  (n_doors - 1)
      - Vertical dividers between drawer bays (n_drawers - 1)

    These are deduced from part counts, not vision.
    """
    if articulation == "RIGID":
        return parts

    T = 0.020  # panel thickness 20mm

    root = next((p for p in parts if p.get("parent") in ("none", None, "")), parts[0])
    root_dims = root["dims_reconciled"]
    W = root_dims["width_mm"]  / 1000
    D = root_dims["depth_mm"]  / 1000
    H = root_dims["height_mm"] / 1000
    body_mat = root.get("material", {"type": "wood", "color_rgb": [0.6, 0.4, 0.2], "metallic": 0, "roughness": 0.7})

    doors   = sorted([p for p in parts if "door"   in p["part"] and "knob"   not in p["part"]],
                     key=lambda p: p.get("position_xyz", [0,0,0])[0])
    drawers = sorted([p for p in parts if "drawer" in p["part"] and "handle" not in p["part"]],
                     key=lambda p: p.get("position_xyz", [0,0,0])[0])

    new_parts = []

    # ── Door dividers ────────────────────────────────────────────────
    for i in range(len(doors) - 1):
        d_left  = doors[i]
        d_right = doors[i + 1]
        # Place divider at the seam between the two doors
        left_edge  = d_left["position_xyz"][0]  + d_left["dims_reconciled"]["width_mm"]  / 1000 / 2
        right_edge = d_right["position_xyz"][0] - d_right["dims_reconciled"]["width_mm"] / 1000 / 2
        div_x = (left_edge + right_edge) / 2
        door_h = d_left["dims_reconciled"]["height_mm"] / 1000
        door_z = d_left["position_xyz"][2]

        new_parts.append({
            "part":             f"door_divider_{i+1}",
            "geometry":         "box",
            "dims_mm":          {"width": T*1000, "depth": root_dims["depth_mm"], "height": door_h*1000},
            "dims_reconciled":  {"width_mm": T*1000, "depth_mm": root_dims["depth_mm"], "height_mm": door_h*1000},
            "dims_source":      {"width": "inferred", "depth": "inferred", "height": "inferred"},
            "material":         body_mat,
            "is_separate_object": False,
            "pivot":            "N/A fixed",
            "parent":           root["part"],
            "cavity_face_delete": False,
            "behavior":         {"behavior_type": "CONTACT_BASED", "active_constraints": {}},
            "b_match":          None,
            "position_xyz":     [round(div_x, 4), 0.0, round(door_z, 4)],
        })

    # ── Drawer dividers ──────────────────────────────────────────────
    for i in range(len(drawers) - 1):
        dr_left  = drawers[i]
        dr_right = drawers[i + 1]
        left_edge  = dr_left["position_xyz"][0]  + dr_left["dims_reconciled"]["width_mm"]  / 1000 / 2
        right_edge = dr_right["position_xyz"][0] - dr_right["dims_reconciled"]["width_mm"] / 1000 / 2
        div_x = (left_edge + right_edge) / 2
        drawer_h = dr_left["dims_reconciled"]["height_mm"] / 1000
        drawer_z = dr_left["position_xyz"][2]

        new_parts.append({
            "part":             f"drawer_divider_{i+1}",
            "geometry":         "box",
            "dims_mm":          {"width": T*1000, "depth": root_dims["depth_mm"], "height": drawer_h*1000},
            "dims_reconciled":  {"width_mm": T*1000, "depth_mm": root_dims["depth_mm"], "height_mm": drawer_h*1000},
            "dims_source":      {"width": "inferred", "depth": "inferred", "height": "inferred"},
            "material":         body_mat,
            "is_separate_object": False,
            "pivot":            "N/A fixed",
            "parent":           root["part"],
            "cavity_face_delete": False,
            "behavior":         {"behavior_type": "CONTACT_BASED", "active_constraints": {}},
            "b_match":          None,
            "position_xyz":     [round(div_x, 4), 0.0, round(drawer_z, 4)],
        })

    if new_parts:
        print(f"  [C] Inferred {len(new_parts)} invisible parts:")
        for np_ in new_parts:
            print(f"    + {np_['part']}  x={np_['position_xyz'][0]:.3f}")

    return parts + new_parts


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def run_stage_c(stage_a: dict, stage_b: dict, output_path: str = None) -> dict:
    t0 = time.time()
    print(f"\n{'='*60}")
    print(f"  STAGE C — Reconciler + Math Engine")
    print(f"{'='*60}")

    articulation = stage_a["object"]["articulation"]
    print(f"  Object: {stage_a['object']['type']} ({articulation})")
    print(f"  Parts: {len(stage_a['parts'])}")

    # ── Step 1: Reconcile dims ───────────────────────────────────────
    print(f"\n  [C1] Reconciling dims (A vs B)...")
    parts, conflicts, b_overall = reconcile_dims(stage_a, stage_b)

    if conflicts:
        print(f"  ⚠ {len(conflicts)} conflicts:")
        for c in conflicts:
            print(f"    {c}")
    else:
        print(f"  ✓ No conflicts")

    for p in parts:
        src = p["dims_source"]
        dr  = p["dims_reconciled"]
        print(f"  {p['part']:30s} {dr['width_mm']:.0f}×{dr['depth_mm']:.0f}×{dr['height_mm']:.0f}mm  [{src['width']}]")

    # ── Step 2: Validate contract ────────────────────────────────────
    print(f"\n  [C2] Validating contract ({articulation})...")
    errors = validate_contract(parts, articulation)
    if errors:
        print(f"  ✗ {len(errors)} contract errors:")
        for e in errors:
            print(f"    {e}")
        print(f"  ⚠ Proceeding with warnings — D will receive incomplete spec")
    else:
        print(f"  ✓ Contract valid")

    # ── Step 3: Math Engine ─────────────────────────────────────────
    print(f"\n  [C3] Computing positions (Math Engine)...")
    parts = compute_positions(parts, articulation, b_overall)
    for p in parts:
        xyz = p.get("position_xyz", [0,0,0])
        print(f"  {p['part']:30s} xyz={xyz}")

    # ── Step 4: Infer invisible structural parts ─────────────────────
    print(f"\n  [C4] Inferring invisible parts...")
    parts = _infer_invisible_parts(parts, articulation)

    # ── Assemble output ──────────────────────────────────────────────
    spec = {
        "object": stage_a["object"],
        "parts": parts,
        "meta": {
            "conflicts": conflicts,
            "contract_errors": errors,
            "b_overall": b_overall,
            "b_row_ratios": stage_b.get("row_ratios", {}),
            "b_part_counts": stage_b.get("part_counts", {}),
        }
    }

    elapsed = time.time() - t0
    print(f"\n  ✓ Stage C complete — {elapsed:.1f}s")
    print(f"{'='*60}")

    if output_path:
        with open(output_path, "w") as f:
            json.dump(spec, f, indent=2)
        print(f"  Saved: {output_path}")

    return spec


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage_a", required=True)
    parser.add_argument("--stage_b", required=True)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    with open(args.stage_a) as f:
        stage_a = json.load(f)
    with open(args.stage_b) as f:
        stage_b = json.load(f)

    out_dir = os.path.dirname(args.stage_a)
    output  = args.output or os.path.join(out_dir, "stage_c.json")

    run_stage_c(stage_a, stage_b, output)

#!/usr/bin/env python3
"""Geometry Math Engine — Deterministic spatial computation for 3D asset generation.

AI describes structure (columns, rows, panel thickness). This module computes
exact 3D positions. No AI arithmetic — same inputs always produce same outputs.

Follows the same principles as v3/physics_math.py:
  1. Single source of truth for all position calculations
  2. Deterministic results (no LLM guessing coordinates)
  3. Edge case handling
  4. All units in meters

Usage:
    grid = CabinetGrid(width=1.3, depth=0.45, height=0.8,
                        columns=3, rows=2, panel_t=0.02,
                        row_heights=[0.52, 0.18], leg_height=0.10)

    cx = grid.col_center(0)    # center X of column 0
    cz = grid.row_center(1)    # center Z of row 1 (drawers)
    fy = grid.front_y()        # Y position of front face
"""

import math
from dataclasses import dataclass, field
from typing import List, Tuple, Optional


# ═══════════════════════════════════════════════════════════════════
# PRIMITIVE HELPERS
# ═══════════════════════════════════════════════════════════════════

def panel_center(edge_start: float, thickness: float) -> float:
    """Center of a panel given its starting edge and thickness."""
    return edge_start + thickness / 2


def distribute_equal(total_space: float, count: int, divider_thickness: float) -> List[float]:
    """Distribute N items equally in a space, accounting for dividers.

    Returns list of item widths (all equal).
    Total: count * item_width + (count-1) * divider_thickness = total_space
    """
    if count <= 0:
        return []
    divider_total = (count - 1) * divider_thickness
    item_width = (total_space - divider_total) / count
    return [item_width] * count


def centers_in_range(start: float, total: float, count: int,
                     item_width: float, divider_t: float) -> List[float]:
    """Compute center positions of N items distributed in a range.

    start: left/bottom edge of the available space
    total: total available space
    count: number of items
    item_width: width of each item
    divider_t: thickness of dividers between items

    Returns list of center positions.
    """
    centers = []
    cursor = start
    for i in range(count):
        center = cursor + item_width / 2
        centers.append(center)
        cursor += item_width + divider_t
    return centers


# ═══════════════════════════════════════════════════════════════════
# CABINET GRID — the main calculator for furniture
# ═══════════════════════════════════════════════════════════════════

@dataclass
class CabinetGrid:
    """Deterministic position calculator for cabinet/dresser/sideboard geometry.

    Coordinate system:
        Origin = bottom-center of carcass (NOT including legs)
        X = width (left = -W/2, right = +W/2)
        Y = depth (back = -D/2, front = +D/2)
        Z = height (bottom = 0, top = H)

    The carcass bottom sits at Z = leg_height.
    """

    # Overall dimensions
    width: float          # total outer width (X)
    depth: float          # total outer depth (Y)
    height: float         # carcass height (Z), NOT including legs

    # Grid layout
    columns: int          # number of columns
    rows: int             # number of rows (bottom to top)

    # Panel specs
    panel_t: float        # panel/stile thickness

    # Row heights (bottom to top). Must sum to < height - 2*panel_t
    row_heights: List[float] = field(default_factory=list)

    # Legs
    leg_height: float = 0.0
    leg_width: float = 0.04
    leg_inset: float = 0.05   # distance from corner to leg center

    # Clearance
    gap: float = 0.003        # gap between moving parts and frame

    def __post_init__(self):
        """Compute derived values."""
        self.z0 = self.leg_height  # bottom of carcass in world Z

        # Interior dimensions
        self.inner_w = self.width - 2 * self.panel_t
        self.inner_h = self.height - 2 * self.panel_t
        self.inner_d = self.depth - 2 * self.panel_t

        # Column widths (equal distribution)
        self.col_widths = distribute_equal(self.inner_w, self.columns, self.panel_t)
        self.col_w = self.col_widths[0] if self.col_widths else 0

        # Column centers (X positions)
        self._col_centers = centers_in_range(
            start=-self.width / 2 + self.panel_t,
            total=self.inner_w,
            count=self.columns,
            item_width=self.col_w,
            divider_t=self.panel_t,
        )

        # Row positions (Z), computed bottom to top
        # If row_heights not provided, distribute equally
        if not self.row_heights:
            divider_total = (self.rows - 1) * self.panel_t
            rh = (self.inner_h - divider_total) / self.rows
            self.row_heights = [rh] * self.rows

        self._row_bottoms = []
        self._row_centers = []
        cursor_z = self.z0 + self.panel_t  # start above bottom panel
        for rh in self.row_heights:
            self._row_bottoms.append(cursor_z)
            self._row_centers.append(cursor_z + rh / 2)
            cursor_z += rh + self.panel_t

    # ── Column positions ──

    def col_center(self, col: int) -> float:
        """X center of column `col` (0-indexed from left)."""
        return self._col_centers[col]

    def col_left_edge(self, col: int) -> float:
        """Left edge X of column `col`."""
        return self._col_centers[col] - self.col_w / 2

    def col_right_edge(self, col: int) -> float:
        """Right edge X of column `col`."""
        return self._col_centers[col] + self.col_w / 2

    # ── Row positions ──

    def row_center(self, row: int) -> float:
        """Z center of row `row` (0-indexed from bottom)."""
        return self._row_centers[row]

    def row_bottom(self, row: int) -> float:
        """Bottom Z of row `row`."""
        return self._row_bottoms[row]

    def row_top(self, row: int) -> float:
        """Top Z of row `row`."""
        return self._row_bottoms[row] + self.row_heights[row]

    def row_height(self, row: int) -> float:
        """Height of row `row`."""
        return self.row_heights[row]

    # ── Horizontal divider between rows ──

    def hdivider_z(self, below_row: int) -> float:
        """Z center of horizontal divider ABOVE row `below_row`."""
        return self.row_top(below_row) + self.panel_t / 2

    # ── Vertical stile between columns ──

    def vstile_x(self, left_col: int) -> float:
        """X center of vertical stile between col `left_col` and col `left_col+1`."""
        return self.col_right_edge(left_col) + self.panel_t / 2

    # ── Depth positions ──

    def front_y(self) -> float:
        """Y of the front face of the carcass."""
        return self.depth / 2

    def back_y(self) -> float:
        """Y of the back face."""
        return -self.depth / 2

    def front_panel_y(self) -> float:
        """Y center of a front-facing panel (door/drawer front)."""
        return self.depth / 2 - self.panel_t / 2

    def hardware_y(self, protrusion: float = 0.005) -> float:
        """Y center of hardware (handle/knob) protruding from front face."""
        return self.depth / 2 + protrusion

    # ── Moving part dimensions (with clearance) ──

    def door_dims(self, col: int, row: int) -> Tuple[float, float, float]:
        """(width, thickness, height) of a door in cell (col, row), with clearance."""
        w = self.col_w - self.gap * 2
        h = self.row_heights[row] - self.gap * 2
        return (w, self.panel_t, h)

    def drawer_front_dims(self, col: int, row: int) -> Tuple[float, float, float]:
        """(width, thickness, height) of a drawer front panel."""
        w = self.col_w - self.gap * 2
        h = self.row_heights[row] - self.gap * 2
        return (w, self.panel_t, h)

    def drawer_box_dims(self, col: int, row: int) -> Tuple[float, float, float]:
        """(width, depth, height) of a drawer box (the part that slides)."""
        w = self.col_w - self.gap * 2 - 0.01  # extra clearance for box
        d = self.inner_d - 0.01               # leave space behind
        h = self.row_heights[row] - self.gap * 2 - 0.01
        return (w, d, h)

    # ── Cell center (for any object placed in a grid cell) ──

    def cell_center(self, col: int, row: int) -> Tuple[float, float, float]:
        """(X, Y, Z) center of cell (col, row). Y = interior center."""
        return (self.col_center(col), 0.0, self.row_center(row))

    def cell_front_center(self, col: int, row: int) -> Tuple[float, float, float]:
        """(X, Y, Z) center of front face of cell (col, row)."""
        return (self.col_center(col), self.front_panel_y(), self.row_center(row))

    # ── Knob/handle position on a door ──

    def knob_position(self, col: int, row: int,
                      x_offset_ratio: float = 0.35,
                      z_offset_ratio: float = 0.55,
                      protrusion: float = 0.012) -> Tuple[float, float, float]:
        """Position of a round knob on door at (col, row).

        x_offset_ratio: 0.0 = left edge, 0.5 = center, 1.0 = right edge
        z_offset_ratio: 0.0 = bottom, 0.5 = center, 1.0 = top
        """
        cx = self.col_center(col) + self.col_w * (x_offset_ratio - 0.5)
        cz = self.row_bottom(row) + self.row_heights[row] * z_offset_ratio
        cy = self.hardware_y(protrusion)
        return (cx, cy, cz)

    def pull_position(self, col: int, row: int,
                      z_offset_ratio: float = 0.5,
                      protrusion: float = 0.005) -> Tuple[float, float, float]:
        """Position of a bar pull centered on cell (col, row)."""
        cx = self.col_center(col)
        cz = self.row_bottom(row) + self.row_heights[row] * z_offset_ratio
        cy = self.hardware_y(protrusion)
        return (cx, cy, cz)

    # ── Carcass panel positions ──

    def carcass_panels(self) -> List[dict]:
        """Return all carcass panel specs: name, center (cx,cy,cz), size (w,d,h).

        These are the FIXED panels that get joined into one carcass mesh.
        """
        W, D, H, T = self.width, self.depth, self.height, self.panel_t
        z0 = self.z0

        panels = [
            # Top
            {"name": "Top", "cx": 0, "cy": 0, "cz": z0 + H - T/2,
             "w": W, "d": D, "h": T},
            # Bottom
            {"name": "Bottom", "cx": 0, "cy": 0, "cz": z0 + T/2,
             "w": W, "d": D, "h": T},
            # Left side
            {"name": "Left", "cx": -W/2 + T/2, "cy": 0, "cz": z0 + H/2,
             "w": T, "d": D, "h": H},
            # Right side
            {"name": "Right", "cx": W/2 - T/2, "cy": 0, "cz": z0 + H/2,
             "w": T, "d": D, "h": H},
            # Back
            {"name": "Back", "cx": 0, "cy": -D/2 + T/2, "cz": z0 + H/2,
             "w": W, "d": T, "h": H},
        ]

        # Horizontal dividers between rows
        for r in range(len(self.row_heights) - 1):
            z = self.hdivider_z(r)
            panels.append({
                "name": f"HDiv_{r}",
                "cx": 0, "cy": 0, "cz": z,
                "w": W - 2*T, "d": D - T, "h": T,
            })

        # Vertical stiles between columns
        for c in range(self.columns - 1):
            sx = self.vstile_x(c)
            # Stiles run full interior height by default
            panels.append({
                "name": f"VStile_{c}",
                "cx": sx, "cy": 0, "cz": z0 + H/2,
                "w": T, "d": D - T, "h": H - 2*T,
            })

        return panels

    def carcass_panels_for_row(self, row: int) -> List[dict]:
        """Vertical stiles only for a specific row (e.g., stiles between doors
        but not between drawers)."""
        T = self.panel_t
        panels = []
        for c in range(self.columns - 1):
            sx = self.vstile_x(c)
            panels.append({
                "name": f"VStile_{c}_row{row}",
                "cx": sx, "cy": self.depth/2 - T/2, "cz": self.row_center(row),
                "w": T, "d": T, "h": self.row_heights[row],
            })
        return panels

    # ── Leg positions ──

    def leg_positions(self) -> List[Tuple[float, float, float]]:
        """(X, Y, Z) center positions for 4 legs."""
        W, D = self.width, self.depth
        ins = self.leg_inset
        lh = self.leg_height
        return [
            (-W/2 + ins, -D/2 + ins, lh / 2),  # back-left
            ( W/2 - ins, -D/2 + ins, lh / 2),  # back-right
            (-W/2 + ins,  D/2 - ins, lh / 2),  # front-left
            ( W/2 - ins,  D/2 - ins, lh / 2),  # front-right
        ]

    # ── Summary ──

    def summary(self) -> str:
        """Print a readable summary of all computed positions."""
        lines = [
            f"CabinetGrid: {self.width:.3f} x {self.depth:.3f} x {self.height:.3f} m",
            f"  Legs: {self.leg_height:.3f}m, carcass bottom Z = {self.z0:.3f}",
            f"  Panel thickness: {self.panel_t*1000:.0f}mm",
            f"  Columns: {self.columns}, col_width: {self.col_w*1000:.1f}mm",
            f"  Rows: {self.rows}, heights: {[f'{h*1000:.0f}mm' for h in self.row_heights]}",
            f"  Gap: {self.gap*1000:.1f}mm",
            "",
            "  Column centers (X):",
        ]
        for c in range(self.columns):
            lines.append(f"    col {c}: X = {self.col_center(c)*1000:.1f}mm")
        lines.append("  Row centers (Z):")
        for r in range(self.rows):
            lines.append(f"    row {r}: Z = {self.row_center(r)*1000:.1f}mm "
                        f"(bottom={self.row_bottom(r)*1000:.1f}, "
                        f"top={self.row_top(r)*1000:.1f})")
        lines.append(f"  Front Y: {self.front_y()*1000:.1f}mm")
        lines.append(f"  Back Y: {self.back_y()*1000:.1f}mm")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# REVOLUTION GEOMETRY — for rotational objects (bolt, glass, knob)
# ═══════════════════════════════════════════════════════════════════

@dataclass
class RevolutionProfile:
    """Compute vertex positions for a body of revolution.

    Define a profile as (radius, z) points, revolve around Z axis.
    """

    outer_profile: List[Tuple[float, float]]  # (radius, z) points, bottom to top
    inner_profile: Optional[List[Tuple[float, float]]] = None  # for thin shells
    segments: int = 64  # angular segments

    def compute_vertices(self) -> List[Tuple[float, float, float]]:
        """Generate all vertices by revolving the profile."""
        profile = list(self.outer_profile)
        if self.inner_profile:
            profile += list(self.inner_profile)

        verts = []
        for i in range(self.segments):
            angle = (2 * math.pi * i) / self.segments
            cos_a = math.cos(angle)
            sin_a = math.sin(angle)
            for radius, z in profile:
                verts.append((radius * cos_a, radius * sin_a, z))
        return verts

    def compute_faces(self) -> List[Tuple[int, int, int, int]]:
        """Generate quad faces connecting adjacent rings."""
        n_profile = len(self.outer_profile)
        if self.inner_profile:
            n_profile += len(self.inner_profile)

        faces = []
        for i in range(self.segments):
            next_i = (i + 1) % self.segments
            for j in range(n_profile - 1):
                v0 = i * n_profile + j
                v1 = i * n_profile + (j + 1)
                v2 = next_i * n_profile + (j + 1)
                v3 = next_i * n_profile + j
                faces.append((v0, v1, v2, v3))
        return faces


# ═══════════════════════════════════════════════════════════════════
# THREAD GEOMETRY — for bolts/screws
# ═══════════════════════════════════════════════════════════════════

def thread_radius(z_from_start: float, angle: float, pitch: float,
                  major_r: float, minor_r: float) -> float:
    """Compute thread surface radius at a given Z position and angle.

    This creates the helical thread profile by modulating radius.
    """
    phase = ((z_from_start / pitch) + (angle / (2 * math.pi))) % 1.0

    if phase < 0.25:
        t = phase / 0.25
        r = minor_r + (major_r - minor_r) * t
    elif phase < 0.5:
        r = major_r
    elif phase < 0.75:
        t = (phase - 0.5) / 0.25
        r = major_r - (major_r - minor_r) * t
    else:
        r = minor_r

    return r


def iso_metric_thread_depth(pitch: float) -> float:
    """ISO metric thread depth: H * 5/8 where H = pitch * sqrt(3)/2."""
    H = pitch * math.sqrt(3) / 2
    return H * 5 / 8


# ═══════════════════════════════════════════════════════════════════
# CONVENIENCE: Quick position checks
# ═══════════════════════════════════════════════════════════════════

def edges_touch(edge1: float, edge2: float, tolerance: float = 0.001) -> bool:
    """Check if two edges are touching (within tolerance)."""
    return abs(edge1 - edge2) < tolerance


def fits_inside(inner_size: float, outer_size: float, min_clearance: float = 0.002) -> bool:
    """Check if inner_size fits inside outer_size with minimum clearance."""
    return inner_size + 2 * min_clearance <= outer_size


# ═══════════════════════════════════════════════════════════════════
# V5 PIPELINE MATH — used by every layer
# ═══════════════════════════════════════════════════════════════════

def bbox_volume_mm3(dims_mm: Tuple[float, float, float]) -> float:
    """Bounding box volume in mm³."""
    return dims_mm[0] * dims_mm[1] * dims_mm[2]


def bbox_volume_m3(dims_mm: Tuple[float, float, float]) -> float:
    """Bounding box volume in m³ from mm dimensions."""
    return (dims_mm[0] / 1000) * (dims_mm[1] / 1000) * (dims_mm[2] / 1000)


def estimate_mass_kg(dims_mm: Tuple[float, float, float], density_kg_m3: float, fill_factor: float = 0.2) -> float:
    """Estimate mass from bounding box dimensions, density, and fill factor.

    fill_factor: 0.2 for hollow objects (ovens, cabinets), 0.8 for solid objects (bolts).
    """
    vol = bbox_volume_m3(dims_mm)
    return round(vol * density_kg_m3 * fill_factor, 2)


def material_density(material_names: List[str]) -> float:
    """Get density (kg/m³) from material name keywords."""
    DENSITIES = {
        "stainless": 8000, "steel": 7800, "chrome": 7190, "metal": 7800,
        "wood": 600, "glass": 2500, "enamel": 2500, "plastic": 1200,
        "rubber": 1100, "aluminum": 2700, "brass": 8500, "copper": 8960,
    }
    for mat_name in material_names:
        for keyword, d in DENSITIES.items():
            if keyword in mat_name.lower():
                return d
    return 1000  # default: water


def compute_pivot_position(
    bbox_min: Tuple[float, float, float],
    bbox_max: Tuple[float, float, float],
    pivot_type: str,
) -> Tuple[float, float, float]:
    """Compute pivot world position from bounding box and pivot type.

    pivot_type: bottom_edge, top_edge, left_edge, right_edge, hinge_edge,
                center, back_center, front_center, bottom_center
    """
    cx = (bbox_min[0] + bbox_max[0]) / 2
    cy = (bbox_min[1] + bbox_max[1]) / 2
    cz = (bbox_min[2] + bbox_max[2]) / 2

    pivots = {
        "bottom_edge": (cx, bbox_min[1], bbox_min[2]),
        "top_edge": (cx, bbox_min[1], bbox_max[2]),
        "left_edge": (bbox_min[0], cy, cz),
        "right_edge": (bbox_max[0], cy, cz),
        "hinge_edge": (bbox_min[0], cy, cz),
        "center": (cx, cy, cz),
        "back_center": (cx, bbox_max[1], cz),
        "front_center": (cx, bbox_min[1], cz),
        "bottom_center": (cx, cy, 0.0),
    }
    return pivots.get(pivot_type, (cx, cy, cz))


def compute_local_offset(
    world_pos: Tuple[float, float, float],
    parent_origin: Tuple[float, float, float],
) -> Tuple[float, float, float]:
    """Compute position relative to parent origin (for localPos0)."""
    return (
        round(world_pos[0] - parent_origin[0], 4),
        round(world_pos[1] - parent_origin[1], 4),
        round(world_pos[2] - parent_origin[2], 4),
    )


def is_point_inside_bbox(
    point: Tuple[float, float, float],
    bbox_min: Tuple[float, float, float],
    bbox_max: Tuple[float, float, float],
    tolerance: float = 0.01,
) -> bool:
    """Check if a point is inside a bounding box (with tolerance)."""
    return all(
        bbox_min[i] - tolerance <= point[i] <= bbox_max[i] + tolerance
        for i in range(3)
    )


def is_bbox_inside_bbox(
    inner_min: Tuple[float, float, float],
    inner_max: Tuple[float, float, float],
    outer_min: Tuple[float, float, float],
    outer_max: Tuple[float, float, float],
    tolerance: float = 0.05,
) -> bool:
    """Check if inner bbox fits inside outer bbox."""
    return all(
        outer_min[i] - tolerance <= inner_min[i] and inner_max[i] <= outer_max[i] + tolerance
        for i in range(3)
    )


def validate_joint_limits(joint_type: str, limits) -> Tuple[bool, str]:
    """Validate joint limits are physically reasonable."""
    if joint_type == "revolute" and limits:
        lo, hi = limits
        if lo >= hi:
            return False, f"lower limit {lo}° >= upper limit {hi}°"
        if hi - lo > 360:
            return False, f"range {hi-lo}° exceeds 360°"
        if lo < -360 or hi > 360:
            return False, f"limits [{lo},{hi}]° outside [-360, 360]"
        return True, "OK"
    elif joint_type == "prismatic" and limits:
        lo, hi = limits
        if lo >= hi:
            return False, f"lower limit {lo}m >= upper limit {hi}m"
        if abs(hi - lo) > 2.0:
            return False, f"travel {abs(hi-lo)*1000:.0f}mm exceeds 2000mm"
        return True, "OK"
    return True, "no limits to validate"


def validate_mass(child_mass: float, parent_mass: float) -> Tuple[bool, str]:
    """Validate child mass is less than parent mass."""
    if child_mass > parent_mass:
        return False, f"child mass {child_mass}kg > parent mass {parent_mass}kg"
    return True, "OK"


def validate_part_fits_parent(
    part_dims_mm: Tuple[float, float, float],
    parent_dims_mm: Tuple[float, float, float],
) -> Tuple[bool, str]:
    """Validate part dimensions fit within parent."""
    for i, axis in enumerate(["width", "depth", "height"]):
        if part_dims_mm[i] > parent_dims_mm[i] * 1.1:  # 10% tolerance
            return False, f"part {axis} {part_dims_mm[i]:.0f}mm > parent {parent_dims_mm[i]:.0f}mm"
    return True, "OK"


def meters_to_cm(m: float) -> float:
    """Convert meters to centimeters (for USD prismatic joint limits)."""
    return m * 100


def vertex_shift_for_pivot(
    vertex_world: Tuple[float, float, float],
    pivot: Tuple[float, float, float],
) -> Tuple[float, float, float]:
    """Compute shifted vertex position (local to pivot)."""
    return (
        vertex_world[0] - pivot[0],
        vertex_world[1] - pivot[1],
        vertex_world[2] - pivot[2],
    )


# ═══════════════════════════════════════════════════════════════════
# PHYSICS EQUATIONS — compute exact parameters from geometry + mass
# ═══════════════════════════════════════════════════════════════════

GRAVITY = 9.81  # m/s²


def torque_from_gravity(mass_kg: float, arm_length_m: float) -> float:
    """Torque required to hold a part against gravity.
    τ = m × g × L (worst case: arm horizontal)
    """
    return round(mass_kg * GRAVITY * arm_length_m, 2)


def damping_for_revolute(mass_kg: float, arm_length_m: float, settling_time_s: float = 2.0) -> float:
    """Compute damping for a revolute joint so it settles in ~N seconds.

    Critical damping: c = 2 × sqrt(I × k)
    For gravity-driven: c ≈ 2 × m × g × L / ω_target
    Simplified: damping ≈ mass × arm_length × gravity × settling_time / π
    """
    return round(mass_kg * arm_length_m * GRAVITY * settling_time_s / math.pi, 2)


def damping_for_prismatic(mass_kg: float, settling_time_s: float = 1.5) -> float:
    """Compute damping for a prismatic joint (drawer/rack sliding).

    c ≈ 2 × mass × displacement_rate
    Simplified: damping ≈ mass × gravity × settling_time / 2
    """
    return round(mass_kg * GRAVITY * settling_time_s / 2, 2)


def stiffness_for_prismatic(mass_kg: float) -> float:
    """Compute stiffness for a prismatic joint so it holds closed at rest.

    k = mass × gravity × 3  — light enough for a robot to pull open,
    strong enough to prevent gravity-induced drift at rest.
    Drive target defaults to 0 (closed position) in PhysX.
    """
    return round(mass_kg * GRAVITY * 3, 2)


def inertia_box(mass_kg: float, width_m: float, height_m: float) -> float:
    """Moment of inertia for a rectangular panel rotating about one edge.
    I = (1/3) × m × L² (for rotation about edge, not center)
    L = the dimension along the rotation direction
    """
    return round(mass_kg * max(width_m, height_m) ** 2 / 3, 4)


def arm_length_from_bbox(
    bbox_min: Tuple[float, float, float],
    bbox_max: Tuple[float, float, float],
    pivot_type: str,
    joint_axis: str,
) -> float:
    """Compute the effective arm length (distance from pivot to center of mass).

    For a door hinged at bottom_edge rotating on X:
      arm = half the height (Z span)
    For a door hinged at left_edge rotating on Z:
      arm = half the width (X span)
    For a knob rotating at center:
      arm = radius = half the diameter
    """
    spans = {
        "X": bbox_max[0] - bbox_min[0],
        "Y": bbox_max[1] - bbox_min[1],
        "Z": bbox_max[2] - bbox_min[2],
    }

    if pivot_type in ("bottom_edge", "top_edge"):
        # Rotating about horizontal axis → arm is along Z (height)
        return spans["Z"] / 2
    elif pivot_type in ("left_edge", "right_edge", "hinge_edge"):
        # Rotating about vertical axis → arm is along X (width)
        return spans["X"] / 2
    elif pivot_type == "center":
        # Knob: arm = radius of the knob
        return max(spans["X"], spans["Z"]) / 2
    elif pivot_type in ("back_center", "front_center"):
        # Sliding: arm = half the depth
        return spans["Y"] / 2
    else:
        return max(spans.values()) / 2


def required_force_revolute(mass_kg: float, arm_length_m: float) -> float:
    """Force needed to open/close a revolute part (at the handle).
    F = τ / arm_length = m × g (when arm is horizontal)
    """
    return round(mass_kg * GRAVITY, 2)


def required_force_prismatic(mass_kg: float, friction_coeff: float = 0.1) -> float:
    """Force needed to slide a prismatic part (drawer/rack).
    F = μ × m × g
    """
    return round(friction_coeff * mass_kg * GRAVITY, 2)


# ═══════════════════════════════════════════════════════════════════
# SELF-TEST
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Test: 3-column 2-row cabinet (like the sideboard)
    grid = CabinetGrid(
        width=1.30, depth=0.45, height=0.80,
        columns=3, rows=2, panel_t=0.020,
        row_heights=[0.52, 0.18],  # bottom=doors, top=drawers
        leg_height=0.10,
    )

    print(grid.summary())
    print()

    # Check all positions make sense
    print("Door positions (col, row=0):")
    for c in range(3):
        pos = grid.cell_front_center(c, 0)
        dims = grid.door_dims(c, 0)
        print(f"  Door {c}: center=({pos[0]*1000:.1f}, {pos[1]*1000:.1f}, {pos[2]*1000:.1f})mm "
              f"size=({dims[0]*1000:.1f} x {dims[1]*1000:.1f} x {dims[2]*1000:.1f})mm")

    print("\nDrawer positions (col, row=1):")
    for c in range(3):
        pos = grid.cell_front_center(c, 1)
        dims = grid.drawer_front_dims(c, 1)
        print(f"  Drawer {c}: center=({pos[0]*1000:.1f}, {pos[1]*1000:.1f}, {pos[2]*1000:.1f})mm "
              f"size=({dims[0]*1000:.1f} x {dims[1]*1000:.1f} x {dims[2]*1000:.1f})mm")

    print("\nKnob positions:")
    for c in range(3):
        kpos = grid.knob_position(c, 0)
        print(f"  Knob {c}: ({kpos[0]*1000:.1f}, {kpos[1]*1000:.1f}, {kpos[2]*1000:.1f})mm")

    print("\nPull positions:")
    for c in range(3):
        ppos = grid.pull_position(c, 1)
        print(f"  Pull {c}: ({ppos[0]*1000:.1f}, {ppos[1]*1000:.1f}, {ppos[2]*1000:.1f})mm")

    print("\nCarcass panels:")
    for p in grid.carcass_panels():
        print(f"  {p['name']}: center=({p['cx']*1000:.1f}, {p['cy']*1000:.1f}, {p['cz']*1000:.1f})mm "
              f"size=({p['w']*1000:.1f} x {p['d']*1000:.1f} x {p['h']*1000:.1f})mm")

    print("\nLeg positions:")
    for i, lp in enumerate(grid.leg_positions()):
        print(f"  Leg {i}: ({lp[0]*1000:.1f}, {lp[1]*1000:.1f}, {lp[2]*1000:.1f})mm")

    # Verify: do edges touch?
    print("\nEdge checks:")
    print(f"  Col 0 right edge: {grid.col_right_edge(0)*1000:.1f}mm")
    print(f"  VStile 0 center:  {grid.vstile_x(0)*1000:.1f}mm")
    print(f"  Col 1 left edge:  {grid.col_left_edge(1)*1000:.1f}mm")

    # Thread test
    print("\nThread geometry (M16):")
    pitch = 0.002
    depth = iso_metric_thread_depth(pitch)
    print(f"  Pitch: {pitch*1000:.1f}mm, Thread depth: {depth*1000:.3f}mm")
    print(f"  Major R: 8.0mm, Minor R: {(0.008-depth)*1000:.3f}mm")

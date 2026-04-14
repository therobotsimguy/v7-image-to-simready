#!/usr/bin/env python3
# Copyright (c) 2022-2026, The Isaac Lab Project Developers.
# SPDX-License-Identifier: BSD-3-Clause
"""Build a tiny articulated rigid body in Newton with ModelBuilder (no USD).

**What you see in GL:** only **two colored boxes** — there is no separate door mesh.
  * **Base** (``--base-color``, e.g. blue) — wider block on a **free joint** to the world (can slide/tip on the ground plane).
  * **Link** (``--link-color``, e.g. yellow/orange) — **thinner slab** = door (revolute) or drawer (prismatic), hinged/slid to the base.
    **Right-click + drag** either box; friction with the ground keeps it from being too slippery.

Joint modes:
  * ``revolute`` — slab hinged on +X side of the base (axis Z).
  * ``prismatic`` — drawer sliding along +X.

Per-shape RGB is set on the GL viewer via ``ViewerGL.update_shape_colors`` (not the
solver graph-coloring ``ModelBuilder.color()`` API, which is for VBD parallelization).

Requires the same Newton + GL stack as ``open_usd_in_newton.py``. Use conda env **teleop**
(Python **3.11**), not Isaac Lab's default env (``pyglet<2`` breaks ViewerGL)::

  conda activate teleop
  pip install newton 'pyglet>=2.1.6,<3' imgui-bundle PyOpenGL

Example::

  python scripts/tools/simready_assets/newton_articulated_toy.py \\
    --viewer gl --device cuda:0 --joint revolute \\
    --base-color 0.15,0.35,0.85 --link-color 0.95,0.45,0.12
"""

from __future__ import annotations

import argparse
import sys


def _argv_requests_gl_viewer(argv: list[str]) -> bool:
    for i, a in enumerate(argv):
        if a == "--viewer" and i + 1 < len(argv) and argv[i + 1] == "gl":
            return True
        if a.startswith("--viewer=") and a.split("=", 1)[1] == "gl":
            return True
    return False


def _preflight_gl_viewer_deps() -> None:
    if not _argv_requests_gl_viewer(sys.argv):
        return
    try:
        from imgui_bundle import imgui as _imgui_check  # noqa: F401
        from imgui_bundle import imguizmo as _imguizmo_check  # noqa: F401
        from imgui_bundle.python_backends import pyglet_backend as _pyglet_backend_check  # noqa: F401
    except ImportError as e:
        print(
            "Newton ViewerGL UI stack import failed.\n"
            f"  ({e})\n"
            "Install:\n"
            "  pip install 'pyglet>=2.1.6,<3' imgui-bundle PyOpenGL\n",
            file=sys.stderr,
        )
        sys.exit(1)


_preflight_gl_viewer_deps()

import warp as wp

import newton
import newton.examples
from newton import Axis, ModelBuilder


def _apply_viewer_picking_strength(viewer, stiffness: float, damping: float) -> None:
    """ViewerGL reads stiffness/damping from ``pick_state`` GPU struct, not only Python attrs."""
    p = getattr(viewer, "picking", None)
    if p is None or p.pick_state is None:
        return
    p.pick_stiffness = float(stiffness)
    p.pick_damping = float(damping)
    host = p.pick_state.numpy()
    host[0]["pick_stiffness"] = float(stiffness)
    host[0]["pick_damping"] = float(damping)
    p.pick_state = wp.array(host, dtype=p.pick_state.dtype, device=p.model.device, ndim=1)


def _parse_rgb(s: str) -> tuple[float, float, float]:
    parts = [p.strip() for p in s.split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("expected three comma-separated floats, e.g. 0.2,0.5,0.9")
    return tuple(float(x) for x in parts)  # type: ignore[return-value]


def build_toy(
    joint: str,
) -> tuple[ModelBuilder, tuple[int, int], tuple[int, int]]:
    """Return (builder, (j_base_free, j_motion), (shape_base, shape_link))."""
    b = ModelBuilder()
    b.default_shape_cfg.mu = 0.6
    b.default_joint_cfg.limit_ke = 1.0e4
    b.default_joint_cfg.limit_kd = 1.0e1

    b.add_ground_plane()

    # Dynamic base + free joint to world: whole cabinet can move on the ground (not welded).
    base = b.add_link(
        xform=wp.transform(wp.vec3(0.0, 0.0, 0.35)),
        mass=120.0,
        label="base",
        is_kinematic=False,
    )
    sh_base = b.add_shape_box(base, hx=0.3, hy=0.22, hz=0.35, label="base_box")

    j_base_free = b.add_joint_free(child=base, parent=-1, label="base_free")

    if joint == "revolute":
        link = b.add_link(
            xform=wp.transform(wp.vec3(0.55, 0.0, 0.35)),
            mass=4.0,
            label="door",
        )
        # Thicker than a real door panel so it reads as its own box in the viewer (was easy to miss).
        sh_link = b.add_shape_box(link, hx=0.22, hy=0.09, hz=0.32, label="door_panel")
        j_motion = b.add_joint_revolute(
            parent=base,
            child=link,
            parent_xform=wp.transform(wp.vec3(0.3, 0.0, 0.0)),
            child_xform=wp.transform(wp.vec3(-0.22, 0.0, 0.0)),
            axis=Axis.Z,
            limit_lower=-1.4,
            limit_upper=1.4,
            label="hinge",
        )
    elif joint == "prismatic":
        link = b.add_link(
            xform=wp.transform(wp.vec3(0.0, 0.0, 0.2)),
            mass=2.0,
            label="drawer",
        )
        sh_link = b.add_shape_box(link, hx=0.22, hy=0.18, hz=0.08, label="drawer_box")
        j_motion = b.add_joint_prismatic(
            parent=base,
            child=link,
            parent_xform=wp.transform(wp.vec3(0.0, 0.0, -0.05)),
            child_xform=wp.transform(wp.vec3(0.0, 0.0, 0.0)),
            axis=Axis.X,
            limit_lower=0.0,
            limit_upper=0.4,
            label="slide",
        )
    else:
        raise ValueError(joint)

    # Joint indices must be monotonic contiguous for add_articulation.
    assert j_motion == j_base_free + 1, "expected motion joint immediately after base free joint"
    b.add_articulation([j_base_free, j_motion], label="cabinet")

    return b, (j_base_free, j_motion), (sh_base, sh_link)


def main() -> None:
    parser = newton.examples.create_parser()
    parser.add_argument(
        "--joint",
        choices=("revolute", "prismatic"),
        default="revolute",
        help="revolute: door on hinge Z; prismatic: drawer slides along +X.",
    )
    parser.add_argument(
        "--base-color",
        type=_parse_rgb,
        default=(0.2, 0.45, 0.85),
        help="RGB for base box in viewer, comma-separated 0–1 (e.g. 0.2,0.45,0.85).",
    )
    parser.add_argument(
        "--link-color",
        type=_parse_rgb,
        default=(0.9, 0.35, 0.15),
        help="RGB for door/drawer box in viewer.",
    )
    parser.add_argument(
        "--pick-stiffness",
        type=float,
        default=280.0,
        help="Mouse spring strength for ViewerGL picking (higher = snappier door/drawer motion).",
    )
    parser.add_argument(
        "--pick-damping",
        type=float,
        default=22.0,
        help="Mouse pick damping (reduces oscillation).",
    )
    viewer, args = newton.examples.init(parser)

    fps = 60
    frame_dt = 1.0 / fps
    sim_substeps = 4
    sim_dt = frame_dt / sim_substeps

    builder, _joints, (shape_base, shape_link) = build_toy(args.joint)
    model = builder.finalize()

    solver = newton.solvers.SolverXPBD(model, iterations=8)
    state_0 = model.state()
    state_1 = model.state()
    control = model.control()
    contacts = model.contacts()

    newton.eval_fk(model, model.joint_q, model.joint_qd, state_0)

    viewer.set_model(model)
    if args.viewer == "gl" and hasattr(viewer, "update_shape_colors"):
        _apply_viewer_picking_strength(viewer, args.pick_stiffness, args.pick_damping)
        viewer.update_shape_colors(
            {
                shape_base: args.base_color,
                shape_link: args.link_color,
            }
        )
        viewer.show_visual = True
        viewer.show_collision = True
        viewer.show_static = True
        # From +Y so base vs side slab read as two blocks (not one merged lump).
        viewer.set_camera(pos=wp.vec3(-0.15, 1.65, 0.52), pitch=-12.0, yaw=-92.0)

    sim_time = 0.0

    def step() -> None:
        nonlocal state_0, state_1, sim_time
        for _ in range(sim_substeps):
            state_0.clear_forces()
            viewer.apply_forces(state_0)
            model.collide(state_0, contacts)
            solver.step(state_0, state_1, control, contacts, sim_dt)
            state_0, state_1 = state_1, state_0
        sim_time += frame_dt

    def render() -> None:
        if wp.get_device().is_cuda:
            wp.synchronize()
        viewer.begin_frame(sim_time)
        viewer.log_state(state_0)
        viewer.end_frame()

    class Loop:
        def __init__(self, v):
            self.viewer = v

        def step(self) -> None:
            step()

        def render(self) -> None:
            render()

    print(
        f"Newton toy: joint={args.joint}\n"
        "  WHAT YOU SEE: exactly **two boxes** — no separate door asset.\n"
        "    • BLUE = BASE on a **free joint** (not welded): you can pick/drag the **whole cabinet** on the ground.\n"
        "    • YELLOW/ORANGE = **door or drawer** on a hinge/slide to the base.\n"
        "  Right-click + drag on either colored box. Press **H** if UI steals clicks.\n"
        f"  Pick: stiffness={args.pick_stiffness}, damping={args.pick_damping}. "
        f"(shape ids: base={shape_base}, link={shape_link})",
        flush=True,
    )
    newton.examples.run(Loop(viewer), args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)

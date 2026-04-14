#!/usr/bin/env python3
# Copyright (c) 2022-2026, The Isaac Lab Project Developers (https://github.com/isaac-sim/IsaacLab/blob/main/CONTRIBUTORS.md).
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause
"""Load a SimReady (or other UsdPhysics) USD into Newton and run the GL or USD timeline viewer.
#
# **Only the asset you pass is loaded** (e.g. a SimReady fridge ``*_physics.usd``) — no robot, no extra props.
#
# SimReady fridges omit PhysicsArticulationRootAPI by design; Newton accepts that via
# finalize(skip_validation_joints=True). SchemaResolverNewton + SchemaResolverPhysx supply
# defaults for attributes PhysX-authored assets often omit (e.g. shape ke/kd).
#
# **Recommended environment:** ``conda activate teleop`` (Python **3.11**). This flow was tuned there;
# Isaac Lab's default env often pins ``pyglet<2``, which breaks Newton ViewerGL — keep Newton + SimReady
# USD in ``teleop`` (or another dedicated env), not mixed with Kit's pyglet.
#
# Usage:
#   conda activate teleop
#   pip install newton usd-core newton-usd-schemas 'pyglet>=2.1.6,<3' imgui-bundle PyOpenGL
#   python3 scripts/tools/simready_assets/open_usd_in_newton.py \
#     --usd /path/to/asset_physics.usd --viewer gl --device cuda:0
#   # optional placement:
#   python3 ... --usd ... --usd-pos 0.6 0.0 0.0 --usd-rot-z 180 --viewer gl --device cuda:0
#
# Solvers (``--solver``): ``xpbd`` (default), ``vbd``, ``mujoco``, ``semi_implicit``.
# MuJoCo needs: ``pip install mujoco mujoco-warp`` (versions pinned by ``newton[examples]`` if you use that).
#
# **VBD:** Newton's rigid ``SolverVBD`` only implements **BALL, FIXED, CABLE** joints — not
# **REVOLUTE/PRISMATIC** (typical SimReady fridge doors/wheels). Use ``--solver xpbd``.
#
# **Mesh-only USD (not SimReady):** Raw DCC exports often have **no** ``UsdPhysics.RigidBodyAPI`` prims.
# Newton then imports **zero** bodies (meshes are ignored). Run ``make_simready.py --fix`` and use
# ``*_physics.usd``, or pass ``--allow-empty-usd-physics`` to open the viewer anyway (ground plane only).
#
# **CoACD / mesh approximation:** SimReady ``*_physics.usd`` often sets ``physics:approximation=convexDecomposition``.
# If the ``coacd`` package is installed, Newton runs **CoACD** on many meshes — that can **freeze** a laptop
# for a long time. By default this script passes ``skip_mesh_approximation=True`` (fast). Opt in with
# ``--mesh-approximation`` when you need full decomposition and can afford the CPU/RAM cost.
#
# **Runtime FPS:** Mesh–plane / mesh–rich XPBD is heavy. Use ``--substeps 2``, ``--xpbd-iters 4``, and/or
# lower ``--fps`` if you need more wall-clock FPS; use ``--substeps 6`` and/or ``--xpbd-iters 10`` if motion
# looks jittery. First launch pays one-time Warp kernel compile (then cached).
#
# **MuJoCo:** Newton's MuJoCo exporter expects joints to belong to an **articulation** (non-negative
# ``joint_articulation``). SimReady/V8 fridges omit ``PhysicsArticulationRootAPI``, so every joint stays
# ``-1`` and conversion fails (``No root found in the joint graph``). Use ``--solver xpbd`` for those USDs.
# Mesh-heavy scenes can also rarely hit native ``malloc``/abort on CUDA; try ``--device cpu``.
#
# ``--viewer gl`` needs **Pyglet >= 2.1**, **imgui-bundle**, and **PyOpenGL** (same env):
#   pip install 'pyglet>=2.1.6,<3' imgui-bundle PyOpenGL
# Newton's own UI code prints "imgui_bundle not found" for *any* ImportError in that stack
# (e.g. missing OpenGL), which is misleading.
#
# If the window is **empty**, SimReady assets are often **cm**-scaled and use collision meshes;
# this script auto-frames the camera from body positions and turns on collision + static drawing.
# Press **F** in the Newton viewer to frame again.
#
# **Picking (right-click drag):** ViewerGL only picks **non-kinematic** rigid bodies. SimReady/V8
# fridges keep ``main_body`` **kinematic**, so right-click on the shell does nothing — pick **doors**,
# drawers, or wheels (dynamic). Press **H** to hide the UI if it captures mouse events.
#
# Isaac Lab pins ``pyglet<2``; use the **teleop** (Py 3.11) env for ``--viewer gl``, or
# ``--viewer usd`` / ``--viewer viser`` from a Lab-pinned env without upgrading pyglet there.
#
# Headless / inspect in usdview:
#   python3 ... --viewer usd --output-path /tmp/sim.usd --num-frames 120 --device cuda:0
#
# Avoid ``--quiet`` with ``--viewer usd`` on some setups (can trigger a process crash in Warp/Newton).
"""

from __future__ import annotations

import math
import sys


def _argv_requests_gl_viewer(argv: list[str]) -> bool:
    for i, a in enumerate(argv):
        if a == "--viewer" and i + 1 < len(argv) and argv[i + 1] == "gl":
            return True
        if a.startswith("--viewer=") and a.split("=", 1)[1] == "gl":
            return True
    return False


def _preflight_gl_viewer_deps() -> None:
    """Run before Warp/Newton so a missing PyOpenGL etc. fails with a clear message."""
    if not _argv_requests_gl_viewer(sys.argv):
        return
    try:
        from imgui_bundle import imgui as _imgui_check  # noqa: F401
        from imgui_bundle import imguizmo as _imguizmo_check  # noqa: F401
        from imgui_bundle.python_backends import pyglet_backend as _pyglet_backend_check  # noqa: F401
    except ImportError as e:
        print(
            "Newton ViewerGL failed to load the ImGui/OpenGL UI stack.\n"
            f"  ({e})\n"
            "Install (in the same conda env as this script):\n"
            "  pip install 'pyglet>=2.1.6,<3' imgui-bundle PyOpenGL\n",
            file=sys.stderr,
        )
        sys.exit(1)


_preflight_gl_viewer_deps()

import warp as wp

import newton
import newton.examples
from newton import ModelBuilder
from newton.usd import SchemaResolverNewton, SchemaResolverPhysx


def _usd_rigid_body_api_count(usd_path: str) -> int:
    """Return count of prims with UsdPhysics.RigidBodyAPI; -1 if stage cannot be inspected."""
    try:
        from pxr import Usd, UsdPhysics
    except ImportError:
        return -1

    stage = Usd.Stage.Open(usd_path)
    if not stage:
        return -1

    n = 0
    for prim in stage.Traverse():
        if UsdPhysics.RigidBodyAPI(prim):
            n += 1
    return n


def _check_vbd_rigid_joint_support(model) -> None:
    """SolverVBD rigid path only supports a subset of joint types (Newton 1.0.x)."""
    import numpy as np

    jt = model.joint_type
    if jt is None or int(model.joint_count) == 0:
        return

    allowed = {
        int(newton.JointType.BALL),
        int(newton.JointType.FIXED),
        int(newton.JointType.CABLE),
    }
    types = np.unique(jt.numpy())
    bad: list[str] = []
    for t in types:
        ti = int(t)
        if ti not in allowed:
            try:
                name = newton.JointType(ti).name
            except ValueError:
                name = str(ti)
            bad.append(name)
    if not bad:
        return

    labels = getattr(model, "joint_label", None)
    detail = f" Unsupported joint type(s): {', '.join(bad)}."
    if labels is not None:
        detail += f" Joint labels: {list(labels)}."

    print(
        "SolverVBD (rigid) in this Newton release does not support all joint types on this asset."
        f"{detail}\n"
        "Use ``--solver xpbd`` for SimReady fridges. (MuJoCo needs articulation roots; typical V8 fridge USD lacks them.)\n",
        file=sys.stderr,
    )
    sys.exit(1)


def _check_mujoco_articulation_support(model) -> None:
    """SolverMuJoCo's USD path needs joints in an articulation; orphan joints break topological_sort."""
    import numpy as np

    ja = model.joint_articulation
    if ja is None or int(model.joint_count) == 0:
        return

    arr = ja.numpy()
    if arr.size == 0:
        return
    if np.all(arr < 0):
        print(
            "SolverMuJoCo cannot convert this Newton model: every joint has joint_articulation == -1 "
            "(no articulation root). Typical SimReady/V8 refrigerator USD omits "
            "PhysicsArticulationRootAPI on purpose, which produces this layout.\n"
            "Use ``--solver xpbd`` (or ``semi_implicit`` for quick checks). "
            "MuJoCo here would need an USD with a proper articulation root or a Newton-side fix.\n",
            file=sys.stderr,
        )
        sys.exit(1)


def _fit_gl_camera_from_bodies(viewer, state_0, wp_mod) -> None:
    """Place ViewerGL so cm-scale SimReady assets are in frame; extend far clip if needed."""
    import numpy as np

    if not hasattr(viewer, "set_camera") or not hasattr(viewer, "camera"):
        return
    bq = state_0.body_q
    if bq is None:
        return
    pos = np.asarray(bq.numpy(), dtype=np.float64)[:, :3]
    cmin = pos.min(axis=0)
    cmax = pos.max(axis=0)
    center = (cmin + cmax) * 0.5
    extent = float(np.max(cmax - cmin))
    if extent < 1e-6:
        extent = 1.0

    viewer.camera.far = max(float(viewer.camera.far), extent * 25.0)

    dist = max(extent * 1.5, 0.5)
    cam = center + np.array([dist * 0.85, -dist * 0.95, dist * 0.45], dtype=np.float64)
    fwd = center - cam
    fn = float(np.linalg.norm(fwd))
    if fn < 1e-9:
        return
    fwd = fwd / fn
    horiz = float(np.hypot(fwd[0], fwd[1]))
    pitch = float(np.degrees(np.arctan2(fwd[2], horiz)))
    yaw = float(np.degrees(np.arctan2(fwd[1], fwd[0])))

    viewer.set_camera(
        pos=wp_mod.vec3(float(cam[0]), float(cam[1]), float(cam[2])),
        pitch=pitch,
        yaw=yaw,
    )


def main() -> None:
    parser = newton.examples.create_parser()
    parser.add_argument("--usd", type=str, required=True, help="Path to .usd / .usda (UsdPhysics).")
    parser.add_argument(
        "--usd-pos",
        type=float,
        nargs=3,
        default=None,
        metavar=("X", "Y", "Z"),
        help="Optional world translation of the loaded USD root (meters). Default: use stage pose.",
    )
    parser.add_argument(
        "--usd-rot-z",
        type=float,
        default=None,
        help="Optional rotation about world +Z in degrees (combined with --usd-pos). Default: 0.",
    )
    parser.add_argument(
        "--solver",
        type=str,
        default="xpbd",
        choices=("xpbd", "vbd", "mujoco", "semi_implicit"),
        help=(
            "xpbd: Newton XPBD + collide (default; best for SimReady revolute doors/wheels). "
            "vbd: VBD rigid (only BALL/FIXED/CABLE joints — not typical fridges). "
            "mujoco: MuJoCo Warp (needs articulation roots in USD; not SimReady fridges). "
            "semi_implicit: no collide (debug)."
        ),
    )
    parser.add_argument(
        "--mujoco-njmax",
        type=int,
        default=800,
        help="SolverMuJoCo njmax (mesh-rich scenes; raise if contacts are dropped).",
    )
    parser.add_argument(
        "--mujoco-nconmax",
        type=int,
        default=600,
        help="SolverMuJoCo nconmax.",
    )
    parser.add_argument(
        "--allow-empty-usd-physics",
        action="store_true",
        help=(
            "Allow USD files with no UsdPhysics rigid bodies (Newton imports nothing; simulation is empty "
            "aside from the ground plane). For raw mesh-only assets, prefer make_simready → *_physics.usd."
        ),
    )
    parser.add_argument(
        "--mesh-approximation",
        action="store_true",
        help=(
            "Run USD-driven mesh remeshing (convexDecomposition → CoACD, etc.). Default is OFF so full "
            "SimReady fridges load quickly; enabling this can take many minutes and stress CPU/RAM."
        ),
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=60,
        help="Viewer update rate (lower = fewer physics frames per wall-clock second).",
    )
    parser.add_argument(
        "--substeps",
        type=int,
        default=4,
        help=(
            "Physics substeps per rendered frame (default 4 for smoother contacts; "
            "try 6–8 if still jittery, 1–2 for max FPS)."
        ),
    )
    parser.add_argument(
        "--xpbd-iters",
        type=int,
        default=8,
        help="XPBD solver iterations (only --solver xpbd). Lower = faster, less accurate contacts.",
    )
    parser.add_argument(
        "--no-cuda-graph",
        action="store_true",
        help="Disable CUDA graph replay for the substep loop (XPBD/VBD + collide on CUDA only).",
    )
    args = parser.parse_args()

    rb_api = _usd_rigid_body_api_count(args.usd)
    if rb_api == 0 and not args.allow_empty_usd_physics:
        print(
            "This USD has **no** prims with ``UsdPhysics.RigidBodyAPI`` (checked with usd-core). "
            "Typical raw refrigerator geometry (e.g. ``Refrigerator_A01_01.usd``) is **mesh-only** for Newton — "
            "`ModelBuilder.add_usd` will not create any rigid bodies or collision from those meshes alone.\n\n"
            "To simulate the fridge in Newton, build SimReady physics USD first, for example:\n"
            "  python3 scripts/tools/simready_assets/make_simready.py \\\n"
            "    --input /path/to/Refrigerator_A01_01.usd --fix\n"
            "then open the generated ``*_physics.usd`` under ``simready_out/``.\n\n"
            "To open the viewer anyway (empty scene + ground only), re-run with:\n"
            "  --allow-empty-usd-physics\n",
            file=sys.stderr,
        )
        sys.exit(1)
    if rb_api == 0 and args.allow_empty_usd_physics:
        print(
            "Warning: USD has no RigidBodyAPI prims — Newton model has no imported bodies (ground plane only).",
            flush=True,
        )

    if args.solver == "mujoco":
        try:
            import mujoco  # noqa: F401
        except ImportError:
            print(
                "Solver ``mujoco`` requires MuJoCo and mujoco-warp in this env, e.g.:\n"
                "  pip install mujoco==3.5.0 mujoco-warp==3.5.0.2\n"
                "(or install ``newton[examples]`` which pulls compatible versions.)",
                file=sys.stderr,
            )
            sys.exit(1)

    if args.device:
        wp.set_device(args.device)

    viewer, args = newton.examples.init(parser)

    fps = max(1, int(args.fps))
    frame_dt = 1.0 / fps
    sim_substeps = max(1, int(args.substeps))
    sim_dt = frame_dt / sim_substeps

    builder = ModelBuilder()
    if args.solver == "vbd":
        # Match newton.examples.basic_shapes VBD branch: stiffer contacts for rigid meshes.
        builder.default_shape_cfg.ke = 1.0e6
        builder.default_shape_cfg.kd = 1.0e1
        builder.default_shape_cfg.mu = 0.5
    else:
        builder.default_shape_cfg.ke = 1.0e5
        builder.default_shape_cfg.kd = 1.0e2
        builder.default_shape_cfg.mu = 0.5

    if not args.mesh_approximation:
        print(
            "Skipping USD mesh remeshing (CoACD/VHACD queue). For full convex decomposition pass "
            "--mesh-approximation (slow, can freeze the machine on large assets).",
            flush=True,
        )

    add_usd_kw: dict = dict(
        schema_resolvers=[SchemaResolverNewton(), SchemaResolverPhysx()],
        collapse_fixed_joints=True,
        verbose=False,
        skip_mesh_approximation=not args.mesh_approximation,
    )
    if args.usd_pos is not None or args.usd_rot_z is not None:
        px, py, pz = (0.0, 0.0, 0.0)
        if args.usd_pos is not None:
            px, py, pz = float(args.usd_pos[0]), float(args.usd_pos[1]), float(args.usd_pos[2])
        rz = 0.0 if args.usd_rot_z is None else float(args.usd_rot_z)
        half = math.radians(rz) * 0.5
        qx, qy, qz, qw = 0.0, 0.0, math.sin(half), math.cos(half)
        add_usd_kw["xform"] = wp.transform((px, py, pz), (qx, qy, qz, qw))

    builder.add_usd(args.usd, **add_usd_kw)
    builder.add_ground_plane()

    if args.solver == "mujoco":
        newton.solvers.SolverMuJoCo.register_custom_attributes(builder)

    if args.solver == "vbd":
        builder.color()

    model = builder.finalize(skip_validation_joints=True)

    if args.solver == "vbd":
        _check_vbd_rigid_joint_support(model)

    if args.solver == "mujoco":
        _check_mujoco_articulation_support(model)

    if args.solver == "xpbd":
        solver = newton.solvers.SolverXPBD(model, iterations=max(1, int(args.xpbd_iters)))
    elif args.solver == "vbd":
        try:
            solver = newton.solvers.SolverVBD(model, iterations=10)
        except NotImplementedError as e:
            print(
                f"SolverVBD could not be constructed: {e}\n"
                "Use ``--solver xpbd`` for SimReady fridges (revolute joints). "
                "MuJoCo is only viable if the USD has articulation roots.\n",
                file=sys.stderr,
            )
            sys.exit(1)
    elif args.solver == "mujoco":
        try:
            solver = newton.solvers.SolverMuJoCo(
                model,
                njmax=args.mujoco_njmax,
                nconmax=args.mujoco_nconmax,
            )
        except ValueError as e:
            if "root" in str(e).lower() and "joint" in str(e).lower():
                print(
                    f"{e}\n"
                    "This usually means orphan joints (no articulation). "
                    "Same fix as above: prefer ``--solver xpbd`` for SimReady/V8 fridge USDs.\n",
                    file=sys.stderr,
                )
                sys.exit(1)
            raise
    else:
        solver = newton.solvers.SolverSemiImplicit(model, joint_attach_ke=1600.0, joint_attach_kd=20.0)

    state_0 = model.state()
    state_1 = model.state()
    control = model.control()
    # MuJoCo handles contact internally; Newton collide + Contacts buffer is for XPBD/VBD/etc.
    uses_newton_collide = args.solver in ("xpbd", "vbd")
    contacts = model.contacts() if uses_newton_collide else None

    viewer.set_model(model)
    newton.eval_fk(model, model.joint_q, model.joint_qd, state_0)

    if args.viewer == "gl":
        # SimReady USD often uses collision-only flags; ground is static.
        viewer.show_visual = True
        viewer.show_collision = True
        viewer.show_static = True
        if state_0.body_q is not None:
            _fit_gl_camera_from_bodies(viewer, state_0, wp)
            print(
                "Newton GL: camera auto-framed from body positions (cm-scale aware). "
                "Press F to frame again, H toggles UI, drag mouse to orbit.",
                flush=True,
            )
        else:
            viewer.set_camera(
                pos=wp.vec3(2.0, -2.0, 1.5),
                pitch=-20.0,
                yaw=-120.0,
            )
            print(
                "Newton GL: no rigid bodies — default camera (mesh-only USD / --allow-empty-usd-physics). "
                "Press F after loading a physics USD if needed.",
                flush=True,
            )
    else:
        viewer.set_camera(
            pos=wp.vec3(2.5, -2.5, 1.2),
            pitch=-15.0,
            yaw=-135.0,
        )
        if hasattr(viewer, "camera") and hasattr(viewer.camera, "fov"):
            viewer.camera.fov = 60.0

    sim_time = 0.0

    def run_physics_substeps() -> None:
        nonlocal state_0, state_1
        for _ in range(sim_substeps):
            state_0.clear_forces()
            viewer.apply_forces(state_0)
            if contacts is not None:
                model.collide(state_0, contacts)
            solver.step(state_0, state_1, control, contacts, sim_dt)
            state_0, state_1 = state_1, state_0

    sim_graph = None
    if (
        wp.get_device().is_cuda
        and not args.no_cuda_graph
        and uses_newton_collide
        and args.solver in ("xpbd", "vbd")
    ):
        try:
            with wp.ScopedCapture() as capture:
                run_physics_substeps()
            sim_graph = capture.graph
            print("CUDA graph capture enabled for physics substep loop.", flush=True)
        except Exception as e:
            print(f"CUDA graph capture skipped ({e}); running substep loop eagerly.", flush=True)

    def step() -> None:
        nonlocal sim_time
        if sim_graph is not None:
            wp.capture_launch(sim_graph)
        else:
            run_physics_substeps()
        sim_time += frame_dt

    def render() -> None:
        # Avoid drawing while GPU physics from this frame may still be in flight (reduces visual jitter).
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

    newton.examples.run(Loop(viewer), args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)

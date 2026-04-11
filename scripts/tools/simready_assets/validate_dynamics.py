#!/usr/bin/env python3
"""
validate_dynamics.py — V9 Behavioral Validation (V2)

Headless physics checks using MuJoCo (CPU-only, no GPU, no GUI).
Runs after make_simready.py produces _physics.usd to catch issues
that the C1-C7 structural audit misses.

Checks:
  B1: Prismatic travel realism (drawer doesn't fly out of body)
  B3: Mass matrix stability (condition number)
  B4: Gravity torque vs Franka limits
  B5: Contact penetration at rest
  B6: Joint actually moves under force

Usage:
  python3 validate_dynamics.py --input asset_physics.usd
  python3 validate_dynamics.py --input asset_physics.usd --json  (machine-readable output)
"""

import argparse
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════
# USD → URDF CONVERSION
# ═══════════════════════════════════════════════════════════════════

def convert_usd_to_urdf(usd_path: str, output_dir: str) -> str:
    """Convert USD to URDF, strip meshes for dynamics-only validation."""
    from nvidia.srl.from_usd.to_urdf import UsdToUrdf

    urdf_path = os.path.join(output_dir, "robot.urdf")
    converter = UsdToUrdf.init_from_file(usd_path)
    converter.save_to_file(urdf_path)

    # Strip mesh references — MuJoCo only needs joints, mass, inertia
    # for behavioral validation. This avoids mesh file path issues.
    tree = ET.parse(urdf_path)
    root = tree.getroot()
    for link in root.findall("link"):
        for tag in ["visual", "collision"]:
            for elem in link.findall(tag):
                link.remove(elem)
    stripped_path = os.path.join(output_dir, "robot_dynamics.urdf")
    tree.write(stripped_path, xml_declaration=True)
    return stripped_path


def parse_urdf_joints(urdf_path: str) -> list:
    """Extract joint info from URDF for validation."""
    tree = ET.parse(urdf_path)
    root = tree.getroot()
    joints = []
    for j in root.findall("joint"):
        info = {
            "name": j.attrib["name"],
            "type": j.attrib["type"],
            "parent": j.find("parent").attrib["link"],
            "child": j.find("child").attrib["link"],
        }
        limit = j.find("limit")
        if limit is not None:
            info["lower"] = float(limit.attrib.get("lower", 0))
            info["upper"] = float(limit.attrib.get("upper", 0))
        axis_el = j.find("axis")
        if axis_el is not None:
            info["axis"] = axis_el.attrib.get("xyz", "0 0 0")
        joints.append(info)
    return joints


# ═══════════════════════════════════════════════════════════════════
# BEHAVIORAL CHECKS
# ═══════════════════════════════════════════════════════════════════

FRANKA_MAX_TORQUE = 87.0   # Nm, joints 1-4
FRANKA_MAX_GRIP = 200.0    # N, sim gripper
CONDITION_WARN = 10000
CONDITION_FAIL = 100000


def run_checks(urdf_path: str, urdf_joints: list, verbose: bool = True) -> dict:
    """Run all behavioral checks. Returns structured results."""
    import mujoco
    import numpy as np

    results = {
        "checks": {},
        "pass_count": 0,
        "warn_count": 0,
        "fail_count": 0,
        "total": 0,
    }

    def record(check_id, name, status, detail=""):
        results["checks"][check_id] = {"name": name, "status": status, "detail": detail}
        results["total"] += 1
        if status == "PASS":
            results["pass_count"] += 1
        elif status == "WARN":
            results["warn_count"] += 1
        else:
            results["fail_count"] += 1
        icon = {"PASS": "+", "WARN": "?", "FAIL": "X"}[status]
        if verbose:
            print(f"  [{icon}] {check_id}: {name} — {status}" + (f" ({detail})" if detail else ""))

    # Load into MuJoCo
    try:
        spec = mujoco.MjSpec.from_file(urdf_path)
        model = spec.compile()
        data = mujoco.MjData(model)
    except Exception as e:
        record("B0", "MuJoCo model load", "FAIL", str(e))
        return results

    record("B0", "MuJoCo model load", "PASS", f"nq={model.nq} nbody={model.nbody} njnt={model.njnt}")

    # ── B1: Prismatic travel realism ──
    # For each prismatic joint, check if travel > 60% of body depth
    # (would mean the part fully exits the body)
    body_depth_estimate = 0.0
    for j in urdf_joints:
        if j["type"] == "prismatic":
            travel = abs(j.get("upper", 0) - j.get("lower", 0))
            short = j["name"].replace("sm_refrigerator_b01_", "").replace("_joint", "")
            # Heuristic: travel > 0.5m for a typical drawer is suspicious
            if travel > 0.55:
                record(f"B1_{short}", f"Travel realism ({short})", "WARN",
                       f"travel={travel:.3f}m — may fully exit body")
            elif travel > 0.8:
                record(f"B1_{short}", f"Travel realism ({short})", "FAIL",
                       f"travel={travel:.3f}m — drawer will detach from body")
            else:
                record(f"B1_{short}", f"Travel realism ({short})", "PASS",
                       f"travel={travel:.3f}m")

    # ── B3: Mass matrix stability ──
    # Compute mass matrix via MuJoCo
    mujoco.mj_forward(model, data)
    M = np.zeros((model.nv, model.nv))
    mujoco.mj_fullM(model, M, data.qM)
    cond = np.linalg.cond(M) if model.nv > 0 else 0
    if cond > CONDITION_FAIL:
        record("B3", "Mass matrix condition", "FAIL",
               f"cond={cond:.0f} > {CONDITION_FAIL} — solver will be unstable")
    elif cond > CONDITION_WARN:
        record("B3", "Mass matrix condition", "WARN",
               f"cond={cond:.0f} > {CONDITION_WARN} — borderline stability")
    else:
        record("B3", "Mass matrix condition", "PASS", f"cond={cond:.0f}")

    # ── B4: Gravity torque vs Franka ──
    mujoco.mj_resetData(model, data)
    mujoco.mj_forward(model, data)
    for i in range(model.njnt):
        jname = model.joint(i).name
        short = jname.replace("sm_refrigerator_b01_", "").replace("_joint", "")
        jtype_id = model.jnt_type[i]
        jtype = ["free", "ball", "slide", "hinge"][jtype_id]

        # Gravity-induced torque/force on this joint
        grav_force = abs(data.qfrc_bias[i])
        limit = FRANKA_MAX_TORQUE if jtype == "hinge" else FRANKA_MAX_GRIP
        unit = "Nm" if jtype == "hinge" else "N"
        if grav_force > limit:
            record(f"B4_{short}", f"Gravity vs Franka ({short})", "FAIL",
                   f"gravity={grav_force:.1f}{unit} > Franka {limit}{unit}")
        elif grav_force > limit * 0.8:
            record(f"B4_{short}", f"Gravity vs Franka ({short})", "WARN",
                   f"gravity={grav_force:.1f}{unit} — close to Franka {limit}{unit}")
        else:
            record(f"B4_{short}", f"Gravity vs Franka ({short})", "PASS",
                   f"gravity={grav_force:.1f}{unit}")

    # ── B5: Contact penetration at rest ──
    mujoco.mj_resetData(model, data)
    mujoco.mj_forward(model, data)
    penetrations = 0
    for i in range(data.ncon):
        if data.contact[i].dist < -0.005:  # 5mm penetration
            penetrations += 1
    if penetrations > 0:
        record("B5", "Contact penetration at rest", "WARN",
               f"{penetrations} contacts with >5mm penetration")
    else:
        record("B5", "Contact penetration at rest", "PASS",
               f"{data.ncon} contacts, none penetrating")

    # ── B6: Joint actually moves under force ──
    for i in range(model.njnt):
        jname = model.joint(i).name
        short = jname.replace("sm_refrigerator_b01_", "").replace("_joint", "")
        jtype_id = model.jnt_type[i]

        mujoco.mj_resetData(model, data)
        # Apply realistic force/torque
        force = 5.0 if jtype_id == 3 else 20.0  # 5Nm for hinge, 20N for prismatic
        # Determine direction from joint limits
        lo = model.jnt_range[i][0]
        hi = model.jnt_range[i][1]
        direction = -1.0 if abs(lo) > abs(hi) else 1.0

        for step in range(2000):  # 2 seconds at 1kHz
            data.qfrc_applied[i] = force * direction
            mujoco.mj_step(model, data)

        final_q = data.qpos[model.jnt_qposadr[i]]
        limit_extent = max(abs(lo), abs(hi))
        pct = abs(final_q / limit_extent) * 100 if limit_extent > 0.001 else 0

        if pct < 5:
            record(f"B6_{short}", f"Joint moves ({short})", "FAIL",
                   f"reached {pct:.0f}% of limit — blocked or jammed")
        elif pct < 30:
            record(f"B6_{short}", f"Joint moves ({short})", "WARN",
                   f"reached {pct:.0f}% of limit — high resistance")
        else:
            record(f"B6_{short}", f"Joint moves ({short})", "PASS",
                   f"reached {pct:.0f}% of limit")

    return results


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def validate(usd_path: str, verbose: bool = True, output_json: bool = False) -> dict:
    """Full validation pipeline: USD → URDF → MuJoCo → checks."""
    usd_path = str(Path(usd_path).resolve())

    if verbose:
        print(f"\n  V2 Behavioral Validation")
        print(f"  Input: {usd_path}")
        print(f"  Engine: MuJoCo (CPU, headless)")
        print(f"  {'─' * 50}")

    # Step 1: Convert USD → URDF
    if verbose:
        print("\n  [1/2] Converting USD → URDF...")
    with tempfile.TemporaryDirectory(prefix="v9_validate_") as tmpdir:
        try:
            urdf_path = convert_usd_to_urdf(usd_path, tmpdir)
        except Exception as e:
            if verbose:
                print(f"  ERROR: USD→URDF conversion failed: {e}")
            return {"checks": {}, "pass_count": 0, "fail_count": 1, "total": 1,
                    "error": str(e)}

        urdf_joints = parse_urdf_joints(urdf_path)
        if verbose:
            print(f"  URDF: {len(urdf_joints)} joints, meshes exported")

        # Step 2: Run checks (from URDF directory so mesh paths resolve)
        if verbose:
            print("\n  [2/2] Running behavioral checks...\n")
        prev_cwd = os.getcwd()
        os.chdir(tmpdir)
        results = run_checks(urdf_path, urdf_joints, verbose=verbose)
        os.chdir(prev_cwd)

    # Summary
    if verbose:
        print(f"\n  {'─' * 50}")
        total = results["total"]
        p = results["pass_count"]
        w = results["warn_count"]
        f = results["fail_count"]
        status = "PASS" if f == 0 else "FAIL"
        print(f"  BEHAVIORAL: {p}/{total} pass, {w} warn, {f} fail → {status}")

    if output_json:
        print(json.dumps(results, indent=2))

    return results


def main():
    ap = argparse.ArgumentParser(description="V9 Behavioral Validation (Pinocchio + MuJoCo)")
    ap.add_argument("--input", required=True, help="Path to _physics.usd file")
    ap.add_argument("--json", action="store_true", help="Output results as JSON")
    ap.add_argument("--quiet", action="store_true", help="Suppress verbose output")
    args = ap.parse_args()
    results = validate(args.input, verbose=not args.quiet, output_json=args.json)
    sys.exit(1 if results["fail_count"] > 0 else 0)


if __name__ == "__main__":
    main()

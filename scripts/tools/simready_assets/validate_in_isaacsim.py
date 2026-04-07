#!/usr/bin/env python3
"""Validate a SimReady USD in Isaac Sim.

Loads the USD, checks physics is set up correctly, reports joints found.

Usage:
    ./isaaclab.sh -p scripts/tools/simready_assets/validate_in_isaacsim.py \
        --usd scripts/tools/simready_assets/cabinet_2/cabinet_2_simready.usd
"""

import argparse
import os

from isaaclab.app import AppLauncher

parser = argparse.ArgumentParser(description="SimReady USD Validator")
parser.add_argument("--usd", required=True, help="Path to SimReady USD")
parser.add_argument("--steps", type=int, default=200, help="Sim steps for stability test")
AppLauncher.add_app_launcher_args(parser)
args = parser.parse_args()

app_launcher = AppLauncher(vars(args))
simulation_app = app_launcher.app

# ── Imports after app launch ─────────────────────────────────────────────────
import json
import omni.usd
import isaaclab.sim as sim_utils
from isaaclab.sim import SimulationContext
from isaaclab.sim.spawners.from_files.from_files_cfg import UsdFileCfg
from pxr import Usd, UsdGeom, UsdPhysics, Gf

USD_PATH = os.path.abspath(args.usd)

print()
print("=" * 60)
print("  SIMREADY VALIDATOR")
print(f"  USD: {USD_PATH}")
print("=" * 60)

# ── Simulation setup ──────────────────────────────────────────────────────────
sim_cfg = sim_utils.SimulationCfg(dt=0.01, device="cpu")
sim = SimulationContext(sim_cfg)
sim.set_camera_view(eye=[2.0, 2.0, 1.5], target=[0.0, 0.0, 0.4])

sim_utils.GroundPlaneCfg().func("/World/GroundPlane", sim_utils.GroundPlaneCfg())
sim_utils.DomeLightCfg(intensity=2000.0).func("/World/Light", sim_utils.DomeLightCfg(intensity=2000.0))

asset_cfg = UsdFileCfg(usd_path=USD_PATH)
asset_cfg.func("/World/Asset", asset_cfg, translation=(0.0, 0.0, 0.0))

sim.reset()

stage = omni.usd.get_context().get_stage()

# ── Check ArticulationRootAPI ─────────────────────────────────────────────────
art_roots = [p for p in stage.Traverse() if p.HasAPI(UsdPhysics.ArticulationRootAPI)]
print(f"\n  ArticulationRoots: {len(art_roots)}")
for r in art_roots:
    print(f"    {r.GetPath()}")

if not art_roots:
    print("  ERROR: No ArticulationRootAPI found — asset has no physics root")

# ── Discover joints ───────────────────────────────────────────────────────────
joints = []
for prim in stage.Traverse():
    tname = prim.GetTypeName()
    if tname == "PhysicsRevoluteJoint":
        j = UsdPhysics.RevoluteJoint(prim)
        lo = j.GetLowerLimitAttr().Get()
        hi = j.GetUpperLimitAttr().Get()
        joints.append({
            "path": str(prim.GetPath()),
            "type": "revolute",
            "axis": str(j.GetAxisAttr().Get()) if j.GetAxisAttr() else "?",
            "lower_deg": lo,
            "upper_deg": hi,
        })
    elif tname == "PhysicsPrismaticJoint":
        j = UsdPhysics.PrismaticJoint(prim)
        lo = j.GetLowerLimitAttr().Get()
        hi = j.GetUpperLimitAttr().Get()
        joints.append({
            "path": str(prim.GetPath()),
            "type": "prismatic",
            "axis": str(j.GetAxisAttr().Get()) if j.GetAxisAttr() else "?",
            "lower_cm": lo,
            "upper_cm": hi,
        })
    elif tname == "PhysicsFixedJoint":
        joints.append({"path": str(prim.GetPath()), "type": "fixed"})

revolute  = [j for j in joints if j["type"] == "revolute"]
prismatic = [j for j in joints if j["type"] == "prismatic"]
fixed     = [j for j in joints if j["type"] == "fixed"]

print(f"\n  Joints found: {len(joints)} total")
print(f"    Revolute:  {len(revolute)}")
print(f"    Prismatic: {len(prismatic)}")
print(f"    Fixed:     {len(fixed)}")

if revolute:
    print("\n  Revolute joints:")
    for j in revolute[:10]:
        lo = f"{j['lower_deg']:.1f}" if j['lower_deg'] is not None else "?"
        hi = f"{j['upper_deg']:.1f}" if j['upper_deg'] is not None else "?"
        name = j["path"].split("/")[-2]
        print(f"    {name}: {j['axis']} [{lo}° → {hi}°]")
    if len(revolute) > 10:
        print(f"    ... and {len(revolute)-10} more")

if prismatic:
    print("\n  Prismatic joints:")
    for j in prismatic[:10]:
        lo = f"{j['lower_cm']*10:.0f}" if j['lower_cm'] is not None else "?"
        hi = f"{j['upper_cm']*10:.0f}" if j['upper_cm'] is not None else "?"
        name = j["path"].split("/")[-2]
        print(f"    {name}: {j['axis']} [{lo}mm → {hi}mm]")
    if len(prismatic) > 10:
        print(f"    ... and {len(prismatic)-10} more")

# ── Gravity stability test (USD stage, no ArticulationView needed) ────────────
print(f"\n  Gravity stability test ({args.steps} steps)...")

# Get root asset prim world position before
asset_prim = stage.GetPrimAtPath("/World/Asset")
def get_position(prim):
    xf = UsdGeom.Xformable(prim)
    if not xf:
        return None
    t = xf.ComputeLocalToWorldTransform(Usd.TimeCode.Default()).ExtractTranslation()
    return (t[0], t[1], t[2])

pos_before = get_position(asset_prim)

for _ in range(args.steps):
    sim.step()

pos_after = get_position(asset_prim)

if pos_before and pos_after:
    import math
    drift_mm = math.sqrt(sum((pos_after[i]-pos_before[i])**2 for i in range(3))) * 1000
    gravity_ok = drift_mm < 10.0
    print(f"    Drift: {drift_mm:.1f}mm [{'OK' if gravity_ok else 'FLOATING/FALLING'}]")
else:
    gravity_ok = False
    print("    Could not measure drift")

# ── Summary ───────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("  VALIDATION SUMMARY")
print("=" * 60)
has_joints = len(joints) > 0
has_root   = len(art_roots) > 0
print(f"  {'PASS' if has_root   else 'FAIL'}  ArticulationRootAPI present")
print(f"  {'PASS' if has_joints else 'FAIL'}  Joints written ({len(joints)} total: {len(revolute)}R {len(prismatic)}P {len(fixed)}F)")
print(f"  {'PASS' if gravity_ok else 'FAIL'}  Gravity stable")

all_passed = has_root and has_joints and gravity_ok
print()
print(f"  RESULT: {'PASS — asset is SimReady' if all_passed else 'FAIL — see issues above'}")
print("=" * 60)

telemetry = {
    "usd": USD_PATH,
    "art_roots": len(art_roots),
    "joints_total": len(joints),
    "revolute": len(revolute),
    "prismatic": len(prismatic),
    "fixed": len(fixed),
    "gravity_ok": gravity_ok,
    "passed": all_passed,
}
with open("/tmp/isaaclab_telemetry.json", "w") as f:
    json.dump(telemetry, f, indent=2)
print(f"\n  Telemetry: /tmp/isaaclab_telemetry.json")

simulation_app.close()

#!/usr/bin/env python3
"""
v12_pipeline.py — V12 SimReady Pipeline (standalone)

Complete pipeline: raw USD → V12 SimReady output.
Calls V11 (make_simready.py) internally for physics, then applies V12 upgrades.

V12 = V11 + SDF collision + dual export + sidecar JSON

Usage:
  python3 v12_pipeline.py --input /path/to/raw_asset.usd --fix
  python3 v12_pipeline.py --input /path/to/raw_asset.usd --fix --dynamic
  python3 v12_pipeline.py --input /path/to/raw_asset.usd --fix --classify-json /path/to/classify.json
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from pxr import Usd, UsdGeom, UsdPhysics, Gf, Sdf

SCRIPT_DIR = Path(__file__).parent.resolve()
MAKE_SIMREADY = SCRIPT_DIR / "make_simready.py"
V12_UPGRADE = SCRIPT_DIR / "v12_upgrade.py"


def find_physics_usd(output_dir):
    """Find the _physics.usd file produced by make_simready.py."""
    for f in Path(output_dir).glob("*_physics.usd"):
        return str(f)
    return None


def run_v11(input_usd, fix=True, provider="anthropic", model=None,
            classify_json=None, dynamic=False, object_json=None, output_dir=None):
    """Run V11 make_simready.py to build physics from raw USD."""
    cmd = [sys.executable, str(MAKE_SIMREADY), "--input", input_usd]
    if fix:
        cmd.append("--fix")
    if provider:
        cmd.extend(["--provider", provider])
    if model:
        cmd.extend(["--model", model])
    if classify_json:
        cmd.extend(["--classify-json", classify_json])
    if dynamic:
        cmd.append("--dynamic")
    if object_json:
        cmd.extend(["--object-json", object_json])
    if output_dir:
        cmd.extend(["--output-dir", output_dir])

    print(f"\n{'=' * 60}")
    print(f"  V12 Pipeline — Phase 1: V11 Physics Build")
    print(f"{'=' * 60}")
    print(f"  Running: {' '.join(cmd[:6])}...")

    result = subprocess.run(cmd, capture_output=False, text=True)
    if result.returncode != 0:
        print(f"  ERROR: V11 make_simready.py failed (exit {result.returncode})")
        return None
    return True


def run_v12_upgrade(physics_usd, output_dir):
    """Apply V12 upgrades: SDF + dual export + sidecar JSON."""
    print(f"\n{'=' * 60}")
    print(f"  V12 Pipeline — Phase 2: V12 Upgrades")
    print(f"{'=' * 60}")

    # Copy physics USD to v12 output
    basename = os.path.splitext(os.path.basename(physics_usd))[0]
    asset_name = basename.replace("_physics", "")
    os.makedirs(output_dir, exist_ok=True)
    v12_usd = os.path.join(output_dir, f"{basename}.usd")
    shutil.copy2(physics_usd, v12_usd)

    # Copy textures
    src_tex = os.path.join(os.path.dirname(physics_usd), "Textures")
    dst_tex = os.path.join(output_dir, "Textures")
    if os.path.isdir(src_tex) and not os.path.isdir(dst_tex):
        shutil.copytree(src_tex, dst_tex)
        print(f"  Textures bundled")

    # --- SDF collision ---
    print(f"\n  [1/3] Upgrading collision to SDF (exact mesh surface)...")
    stage = Usd.Stage.Open(v12_usd)
    n_switched = 0
    for prim in stage.Traverse():
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            mc = UsdPhysics.MeshCollisionAPI.Apply(prim)
            mc.CreateApproximationAttr("sdf")
            for prop_name in [p.GetName() for p in prim.GetAuthoredProperties()]:
                if "physxConvex" in prop_name:
                    prim.RemoveProperty(prop_name)
            n_switched += 1
    stage.GetRootLayer().Save()
    print(f"    Switched {n_switched} colliders to SDF")

    # --- Dual export ---
    print(f"\n  [2/3] Creating articulation variant...")
    artic_usd = os.path.join(output_dir, f"{asset_name}_articulation.usd")
    shutil.copy2(v12_usd, artic_usd)
    artic_stage = Usd.Stage.Open(artic_usd)
    dp = artic_stage.GetDefaultPrim()

    # Add ArticulationRootAPI
    dp_spec = artic_stage.GetRootLayer().GetPrimAtPath(dp.GetPath())
    schemas = dp_spec.GetInfo("apiSchemas")
    items = list(schemas.prependedItems) if schemas and hasattr(schemas, "prependedItems") else []
    if "PhysicsArticulationRootAPI" not in items:
        items.append("PhysicsArticulationRootAPI")
        new_list = Sdf.TokenListOp()
        new_list.prependedItems = items
        dp_spec.SetInfo("apiSchemas", new_list)

    for prim in artic_stage.Traverse():
        if not prim.HasAPI(UsdPhysics.RigidBodyAPI):
            continue
        kin_attr = prim.GetAttribute("physics:kinematicEnabled")
        if kin_attr and kin_attr.Get():
            prim.RemoveProperty("physics:kinematicEnabled")
            joint_path = prim.GetPath().AppendChild("FixedJoint")
            joint = UsdPhysics.FixedJoint.Define(artic_stage, joint_path)
            joint.CreateBody1Rel().SetTargets([prim.GetPath()])
            joint.CreateLocalPos0Attr(Gf.Vec3f(0, 0, 0))
            joint.CreateLocalPos1Attr(Gf.Vec3f(0, 0, 0))
            break
    artic_stage.GetRootLayer().Save()
    print(f"    Saved: {artic_usd}")

    # --- Sidecar JSON ---
    print(f"\n  [3/3] Generating sidecar physics JSON...")
    stage = Usd.Stage.Open(v12_usd)

    def _mesh_bbox(prim):
        bmin = [1e30]*3; bmax = [-1e30]*3; found = False
        for child in Usd.PrimRange(prim):
            if child.GetTypeName() != "Mesh": continue
            pts = child.GetAttribute("points")
            if not pts or not pts.HasValue(): continue
            l2w = UsdGeom.Xformable(child).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            for pt in pts.Get():
                wp = l2w.TransformAffine(Gf.Vec3d(float(pt[0]),float(pt[1]),float(pt[2])))
                for i in range(3): bmin[i]=min(bmin[i],wp[i]); bmax[i]=max(bmax[i],wp[i])
                found = True
        return (bmin, bmax) if found else None

    parts = []
    for prim in stage.Traverse():
        if not prim.HasAPI(UsdPhysics.RigidBodyAPI): continue
        mass_attr = prim.GetAttribute("physics:mass")
        mass = mass_attr.Get() if mass_attr and mass_attr.HasValue() else None
        kin_attr = prim.GetAttribute("physics:kinematicEnabled")
        is_kin = kin_attr.Get() if kin_attr and kin_attr.HasValue() else False
        bbox = _mesh_bbox(prim)
        bounds = None
        if bbox:
            bmin, bmax = bbox
            bounds = {
                "min": [round(bmin[i],6) for i in range(3)],
                "max": [round(bmax[i],6) for i in range(3)],
                "size": [round(abs(bmax[i]-bmin[i]),6) for i in range(3)],
            }
        n_col = sum(1 for d in Usd.PrimRange(prim) if d.HasAPI(UsdPhysics.CollisionAPI))
        parts.append({
            "name": prim.GetName(), "path": str(prim.GetPath()),
            "is_kinematic": is_kin, "mass_kg": round(mass,4) if mass else None,
            "bounds": bounds, "colliders": n_col, "collision_type": "sdf",
        })

    joints = []
    for prim in stage.Traverse():
        if not prim.IsA(UsdPhysics.Joint): continue
        ji = {"name": prim.GetName(), "type": prim.GetTypeName()}
        for an in ["physics:axis","physics:lowerLimit","physics:upperLimit"]:
            a = prim.GetAttribute(an)
            if a and a.HasValue():
                v = a.Get()
                ji[an.split(":")[-1]] = round(v,4) if isinstance(v,float) else v
        b0 = prim.GetRelationship("physics:body0").GetTargets()
        b1 = prim.GetRelationship("physics:body1").GetTargets()
        ji["body0"] = str(b0[0]) if b0 else "world"
        ji["body1"] = str(b1[0]) if b1 else None
        drive = {}
        for attr in prim.GetAttributes():
            if "drive" in attr.GetName() and attr.HasValue():
                drive[attr.GetName().split(":")[-1]] = round(attr.Get(),4) if isinstance(attr.Get(),float) else attr.Get()
        if drive: ji["drive"] = drive
        joints.append(ji)

    total_mass = sum(p["mass_kg"] for p in parts if p["mass_kg"])
    spec = {
        "version": "V12",
        "asset_name": dp.GetName() if dp else "unknown",
        "summary": {
            "total_mass_kg": round(total_mass,2),
            "rigid_bodies": len(parts),
            "joints": len(joints),
            "collision": "SDF",
        },
        "parts": parts, "joints": joints,
    }
    json_path = os.path.join(output_dir, f"{asset_name}_physics.json")
    with open(json_path, "w") as f:
        json.dump(spec, f, indent=2, default=str)
    print(f"    {len(parts)} bodies, {len(joints)} joints, total mass={total_mass:.2f}kg")
    print(f"    Saved: {json_path}")

    return v12_usd, artic_usd, json_path


def main():
    ap = argparse.ArgumentParser(description="V12 SimReady Pipeline (standalone)")
    ap.add_argument("--input", required=True, help="Raw USD file")
    ap.add_argument("--fix", action="store_true", help="Apply physics (required for first build)")
    ap.add_argument("--output-dir", default=None, help="Output directory")
    ap.add_argument("--provider", default="anthropic", choices=["openai", "anthropic"])
    ap.add_argument("--model", default=None)
    ap.add_argument("--classify-json", default=None, help="Pre-made classification JSON")
    ap.add_argument("--object-json", default=None, help="Gemini object understanding JSON")
    ap.add_argument("--dynamic", action="store_true", help="Dynamic body (trolley/draggable)")
    args = ap.parse_args()

    input_path = os.path.abspath(args.input)
    if not os.path.isfile(input_path):
        print(f"ERROR: {input_path} not found")
        sys.exit(1)

    # Output directory
    if args.output_dir:
        out_dir = args.output_dir
    else:
        out_dir = os.path.join(os.path.dirname(input_path), "v12_out")

    # V11 builds into a temp simready_out, then V12 upgrades into final output
    v11_out = os.path.join(os.path.dirname(input_path), "simready_out")

    # Phase 1: V11
    ok = run_v11(input_path, fix=args.fix, provider=args.provider, model=args.model,
                 classify_json=args.classify_json, dynamic=args.dynamic,
                 object_json=args.object_json, output_dir=v11_out)
    if not ok and args.fix:
        sys.exit(1)

    # Find physics USD
    physics_usd = find_physics_usd(v11_out)
    if not physics_usd:
        print(f"ERROR: No _physics.usd found in {v11_out}")
        sys.exit(1)

    # Phase 2: V12 upgrades
    v12_usd, artic_usd, json_path = run_v12_upgrade(physics_usd, out_dir)

    # Summary
    print(f"\n{'=' * 60}")
    print(f"  V12 PIPELINE COMPLETE")
    print(f"{'=' * 60}")
    print(f"  {v12_usd}")
    print(f"    → SDF collision, shift+drag works")
    print(f"  {artic_usd}")
    print(f"    → ArticulationRootAPI, drive targets work")
    print(f"  {json_path}")
    print(f"    → Full physics specification")
    print(f"\n  Test:")
    print(f"    ISAACLAB_PATH=/home/msi/IsaacLab ./isaaclab.sh -p scripts/environments/teleoperation/teleop_se3_agent_cinematic.py \\")
    print(f"      --asset {os.path.abspath(v12_usd)} --device cpu")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()

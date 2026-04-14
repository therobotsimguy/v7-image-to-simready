#!/usr/bin/env python3
"""
v12_pipeline.py — V12 SimReady Pipeline (standalone)

Complete pipeline: raw USD → V12 SimReady output.
One command, fast, no agent SDK.

V12 features:
  - SDF collision (exact mesh surface, Lightwheel quality)
  - Dual export: _physics.usd (shift+drag) + _articulation.usd (drive targets)
  - Sidecar physics JSON
  - Gemini mass (distributed by volume ratio)
  - Prompt caching on classification calls

Usage:
  python3 v12_pipeline.py --input /path/to/raw_asset.usd
  python3 v12_pipeline.py --input /path/to/raw_asset.usd --dynamic
  python3 v12_pipeline.py --input /path/to/raw_asset.usd --classify-json /path/to/classify.json
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

# Add script dir to path so we can import make_simready directly
SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from pxr import Usd, UsdGeom, UsdPhysics, Gf, Sdf

# Import make_simready functions directly (no subprocess, no agent SDK)
from make_simready import run as run_make_simready


def find_physics_usd(output_dir):
    """Find the _physics.usd file produced by make_simready."""
    for f in Path(output_dir).glob("*_physics.usd"):
        return str(f)
    return None


def apply_sdf(stage):
    """Switch all collision shapes to SDF."""
    n = 0
    for prim in stage.Traverse():
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            mc = UsdPhysics.MeshCollisionAPI.Apply(prim)
            mc.CreateApproximationAttr("sdf")
            for prop_name in [p.GetName() for p in prim.GetAuthoredProperties()]:
                if "physxConvex" in prop_name:
                    prim.RemoveProperty(prop_name)
            n += 1
    return n


def create_articulation_variant(physics_usd, output_path):
    """Create ArticulationRootAPI variant."""
    shutil.copy2(physics_usd, output_path)
    stage = Usd.Stage.Open(output_path)
    dp = stage.GetDefaultPrim()

    dp_spec = stage.GetRootLayer().GetPrimAtPath(dp.GetPath())
    schemas = dp_spec.GetInfo("apiSchemas")
    items = list(schemas.prependedItems) if schemas and hasattr(schemas, "prependedItems") else []
    if "PhysicsArticulationRootAPI" not in items:
        items.append("PhysicsArticulationRootAPI")
        new_list = Sdf.TokenListOp()
        new_list.prependedItems = items
        dp_spec.SetInfo("apiSchemas", new_list)

    for prim in stage.Traverse():
        if not prim.HasAPI(UsdPhysics.RigidBodyAPI):
            continue
        kin_attr = prim.GetAttribute("physics:kinematicEnabled")
        if kin_attr and kin_attr.Get():
            prim.RemoveProperty("physics:kinematicEnabled")
            joint_path = prim.GetPath().AppendChild("FixedJoint")
            joint = UsdPhysics.FixedJoint.Define(stage, joint_path)
            joint.CreateBody1Rel().SetTargets([prim.GetPath()])
            joint.CreateLocalPos0Attr(Gf.Vec3f(0, 0, 0))
            joint.CreateLocalPos1Attr(Gf.Vec3f(0, 0, 0))
            break

    stage.GetRootLayer().Save()


def generate_physics_json(stage, output_path):
    """Generate sidecar physics JSON."""
    dp = stage.GetDefaultPrim()

    def _bbox(prim):
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
        bbox = _bbox(prim)
        bounds = {"min": [round(bbox[0][i],6) for i in range(3)],
                  "max": [round(bbox[1][i],6) for i in range(3)],
                  "size": [round(abs(bbox[1][i]-bbox[0][i]),6) for i in range(3)]} if bbox else None
        n_col = sum(1 for d in Usd.PrimRange(prim) if d.HasAPI(UsdPhysics.CollisionAPI))
        parts.append({"name": prim.GetName(), "path": str(prim.GetPath()),
                       "is_kinematic": is_kin, "mass_kg": round(mass,4) if mass else None,
                       "bounds": bounds, "colliders": n_col, "collision_type": "sdf"})

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
                v = attr.Get()
                drive[attr.GetName().split(":")[-1]] = round(v,4) if isinstance(v,float) else v
        if drive: ji["drive"] = drive
        joints.append(ji)

    total_mass = sum(p["mass_kg"] for p in parts if p["mass_kg"])
    spec = {"version": "V12", "asset_name": dp.GetName() if dp else "unknown",
            "summary": {"total_mass_kg": round(total_mass,2), "rigid_bodies": len(parts),
                         "joints": len(joints), "collision": "SDF"},
            "parts": parts, "joints": joints}
    with open(output_path, "w") as f:
        json.dump(spec, f, indent=2, default=str)
    return spec


def main():
    ap = argparse.ArgumentParser(description="V12 SimReady Pipeline")
    ap.add_argument("--input", required=True, help="Raw USD file")
    ap.add_argument("--output-dir", default=None, help="Output directory")
    ap.add_argument("--dynamic", action="store_true", help="Dynamic body (trolley/draggable)")
    ap.add_argument("--classify-json", default=None, help="Pre-made classification JSON")
    ap.add_argument("--object-json", default=None, help="Gemini object understanding JSON")
    ap.add_argument("--provider", default="anthropic", choices=["openai", "anthropic"])
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    input_path = os.path.abspath(args.input)
    if not os.path.isfile(input_path):
        print(f"ERROR: {input_path} not found")
        sys.exit(1)

    if args.output_dir:
        out_dir = os.path.abspath(args.output_dir)
    else:
        out_dir = os.path.join(os.path.dirname(input_path), "v12_out")

    v11_out = os.path.join(os.path.dirname(input_path), "simready_out")

    print(f"\n{'=' * 60}")
    print(f"  V12 SimReady Pipeline")
    print(f"{'=' * 60}")
    print(f"  Input:  {input_path}")
    print(f"  Output: {out_dir}/")

    # ── Phase 1: Build physics (direct function call, no subprocess) ──
    print(f"\n  [1/4] Building physics (classification + joints + mass + collision)...")
    physics_usd = run_make_simready(
        input_path, fix=True, provider=args.provider, model=args.model,
        output_dir=v11_out, classify_json=args.classify_json,
        dynamic_body=args.dynamic, object_json=args.object_json)

    if not physics_usd:
        physics_usd = find_physics_usd(v11_out)
    if not physics_usd:
        print(f"  ERROR: No _physics.usd produced")
        sys.exit(1)

    # ── Phase 2: Copy to output + SDF upgrade ──
    basename = os.path.splitext(os.path.basename(physics_usd))[0]
    asset_name = basename.replace("_physics", "")
    os.makedirs(out_dir, exist_ok=True)

    v12_usd = os.path.join(out_dir, f"{basename}.usd")
    shutil.copy2(physics_usd, v12_usd)

    # Copy textures
    for tex_name in ("Textures", "textures", "materials"):
        src_tex = os.path.join(os.path.dirname(input_path), tex_name)
        dst_tex = os.path.join(out_dir, tex_name)
        if os.path.isdir(src_tex) and not os.path.isdir(dst_tex):
            shutil.copytree(src_tex, dst_tex)

    print(f"\n  [2/4] Upgrading collision to SDF...")
    stage = Usd.Stage.Open(v12_usd)
    n_sdf = apply_sdf(stage)
    stage.GetRootLayer().Save()
    print(f"    {n_sdf} colliders → SDF")

    # ── Phase 3: Dual export ──
    print(f"\n  [3/4] Creating articulation variant...")
    artic_usd = os.path.join(out_dir, f"{asset_name}_articulation.usd")
    create_articulation_variant(v12_usd, artic_usd)

    # ── Phase 4: Sidecar JSON ──
    print(f"\n  [4/4] Generating physics JSON...")
    stage = Usd.Stage.Open(v12_usd)
    json_path = os.path.join(out_dir, f"{asset_name}_physics.json")
    spec = generate_physics_json(stage, json_path)
    s = spec["summary"]
    print(f"    {s['rigid_bodies']} bodies, {s['joints']} joints, {s['total_mass_kg']}kg, SDF collision")

    # ── Done ──
    print(f"\n{'=' * 60}")
    print(f"  V12 COMPLETE")
    print(f"{'=' * 60}")
    print(f"  {v12_usd}")
    print(f"  {artic_usd}")
    print(f"  {json_path}")
    print(f"\n  Test:")
    print(f"    ISAACLAB_PATH=/home/msi/IsaacLab ./isaaclab.sh -p scripts/environments/teleoperation/teleop_se3_agent_cinematic.py \\")
    print(f"      --asset {os.path.abspath(v12_usd)} --device cpu")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Load the cabinet physics USD in Isaac Sim.
Run from the teleop conda env:

    conda activate teleop
    isaacsim scripts/tools/simready_assets/cabinet_2_simready_out/load_in_isaacsim.py
"""

import os

USD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "teak_outdoor_sideboard_physics.usd")

from isaacsim import SimulationApp
app = SimulationApp({"headless": False, "width": 1280, "height": 720})

import omni.usd
import omni.kit.commands
from pxr import UsdGeom, Gf

# Open stage
print(f"Opening: {USD_PATH}")
omni.usd.get_context().open_stage(USD_PATH)
app.update()

# Set up a default camera view
stage = omni.usd.get_context().get_stage()
UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)

# Move viewport camera to a good angle
try:
    import omni.kit.viewport.utility as vp_util
    viewport = vp_util.get_active_viewport()
    if viewport:
        from omni.kit.viewport.utility.camera_state import ViewportCameraState
        camera_state = ViewportCameraState(viewport.viewport_api)
        camera_state.set_position_world(Gf.Vec3d(2.5, -2.5, 1.8), True)
        camera_state.set_target_world(Gf.Vec3d(0.0, 0.0, 0.5), True)
except Exception as e:
    print(f"Camera setup skipped: {e}")

app.update()
print("Stage loaded. Close the window to exit.")

# Run
while app.is_running():
    app.update()

app.close()

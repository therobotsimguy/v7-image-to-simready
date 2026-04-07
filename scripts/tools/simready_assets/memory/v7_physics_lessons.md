# V7 USD Physics Lessons (Stage F)

## ArticulationRootAPI vs RigidBodyAPI on Root
- Root body (main_frame/cabinet_body) must have **ArticulationRootAPI only** — NO RigidBodyAPI
- Adding RigidBodyAPI to root = floating-base articulation (whole cabinet floats/flies away on sim start)
- Children get RigidBodyAPI, root does not

## Joint localPos
- `localPos0`: anchor position in body0 (parent) local frame
- `localPos1`: anchor position in body1 (child) local frame
- After Stage D pivot-origin fix: child's Xform translate = pivot world position
- `localPos0` = read from child's Xform translate (that's the pivot in world/parent frame)
- `localPos1` = (0,0,0) — because child's origin IS the pivot

## Joint Types
- `ROTATIONAL` → RevoluteJoint, axis=Z, limits=[-120°,0°] left hinge or [0°,120°] right hinge
- `LINEAR_TRANSLATIONAL` → PrismaticJoint, axis=Y (front-back), limits=[0, depth*0.85]
- Everything else → FixedJoint

## Collision
- Apply CollisionAPI + MeshCollisionAPI to Mesh prims (children of Xform)
- Use approximation="convexHull" for moving parts

## Isaac Sim Run Command
```bash
cd /home/msi/IsaacLab
./isaaclab.sh -p scripts/tools/simready_assets/cabinet_2_v7/load_in_isaacsim.py
```
Press Space to start sim, Shift+drag on object to apply force.

## Physics Scene
Place at `/root/physics_scene`, gravity direction=(0,0,-1), magnitude=9.81

---
name: simready-collision
description: >-
  SimReady collision & friction — bodies, wheels, grip. Full rules, trolley wheel
  tables, and anti-patterns live in V8 PRINCIPLES_FRIDGE_TROLLEY.md on GitHub;
  use this skill as a pointer and quick reminder.
---

# SimReady Collision Skill

## Canonical document (read this first)

**All** detailed rules — **body hybrid hull vs decomposition**, **friction / GripMaterial / contactOffset**, **wheels & casters** (bracket vs tire, axle axis, anchor vs pivot, structural split keywords), **full trolley investigation tables**, **door/drawer/handle notes**, and **anti-patterns** — are in the V8 repo:

**[PRINCIPLES_FRIDGE_TROLLEY.md](https://github.com/therobotsimguy/v8-usd-to-simready/blob/main/PRINCIPLES_FRIDGE_TROLLEY.md)**

Local clone (if present): `/home/msi/v8-usd-to-simready/PRINCIPLES_FRIDGE_TROLLEY.md`

Do **not** duplicate long tables here; update the V8 file when behavior or learnings change, then sync `make_simready.py` to V8.

## Two-rule reminder (summary only)

1. **Body:** Hybrid collision — `convexDecomposition` only where concavity / cloak risk demands it; `convexHull` for small parts; **hard cap** on decomp count per asset (see principles + `MAX_DECOMP_BUDGET` in code). **Wheels:** decomp on all tire/disc/detail meshes under the wheel RB after structural split.

2. **Grip:** **`material:binding:physics`** on every collider; **GripMaterial** on handles; **`contactOffset`** only at runtime in teleop — not in asset USD.

## simready-criteria

Use **simready-criteria** skill (C2, C3) as the **audit scorecard**; use **PRINCIPLES_FRIDGE_TROLLEY.md** as the **design reference** for why those choices exist.

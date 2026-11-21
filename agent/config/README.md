# Agent config: what each teammate fills in

Claude decides only from these files. Nothing is measured or guessed during a run. Replace every `null` with a measured value; leave a value `null` only if the feature is not used. Keys starting with `_` are help text.

Units: cm in the robot base frame (x forward, y left, z up, table = 0), degrees, motor steps 0-4095.

| File | Owner | Needed by | What |
|---|---|---|---|
| `workspace.json` | Environment | 17:00 | Reach limits, input zone, no-go zones, bin positions and sizes, camera ids, calibration error, bump markers |
| `motion.json` | Motion | 19:30 | Taught home and bin poses (motor steps), hover and grasp heights, gripper values, speeds, measured pick results |
| `vision.json` | Vision | 19:30 | Locked camera settings, detection thresholds, code reading method and measured read rate, height classes |
| `catalog.json` | Agent + team | 18:00 | Every tablet pack that may appear: printed name, other spellings, its bin |
| `rules.json` | Agent + team | 18:00 | `mode: name_only` for now (expiry rules switched off until the cameras can read dates), the Unknown bin, failure policy |

## How to measure (do not estimate)
- **Reach:** drive the tip to the point with the arm loaded (holding a pack). It counts only if it arrives within 1 cm and the shoulder reports no overload.
- **Bins:** for each bin set `reach_tested: true` only after a real drop into it worked.
- **Poses:** move the arm there (leader arm or by hand), then copy `joints[].steps` from `GET http://127.0.0.1:8020/api/v1/arms`.
- **Calibration:** copy `rms_mm`, `max_mm`, `points` from the homography file after fitting. Above 10 mm RMS: recalibrate.
- **Camera ids:** `GET http://127.0.0.1:8020/api/v1/cameras`. Re-check after any replug.

## Interfaces the tools expect
- Vision: `scan()` -> `[{id, x_cm, y_cm, angle, width_cm, height_class}]` (position only; Claude reads the name); `crop(id)` -> path of a wrist-camera close-up taken from the read pose (full resolution, not 320x240); `verify_place(id, bin)` -> `{ok, photo}`
- Motion: `pick(x_cm, y_cm, angle, height_class)` -> `{holding, reason}`; `place(bin)` -> `{placed, reason}`; `home()`; a taught read pose: about 8-10 cm above the pack, wrist camera looking straight down
- Environment: `pixel_to_table(u, v)` -> `(x_cm, y_cm)`; `camera_bumped()` -> bool

## Teaching the fixed positions (at the arm laptop, about 15 minutes)
Packs lie in **fixed slots** marked with tape on the sheet. Every position is taught once by putting the arm there; nobody types coordinates. Station and the IK service must run (`venue/elrobot/ik-calib/start_ik.sh`).

1. Set `follower_bus` in `motion.json`, `top_id` and `gripper_id` in `workspace.json` (`curl 127.0.0.1:8020/api/v1/cameras`).
2. Clear the slots, then `.venv/bin/python agent/teach.py empty` (stand-camera photo of the empty table).
3. `.venv/bin/python agent/teach.py limp`, support the arm, and for each position move it there by hand, then record:
   - `teach.py home`: safe pose, out of the stand camera's view
   - per slot (`slot1`, `slot2`, `slot3`): `hover` (about 5 cm above the pack), `grip` (jaws around the pack), `read` (wrist camera 8-10 cm above, looking straight down at the print)
   - per bin: `teach.py bin Painkillers hover`, `teach.py bin Painkillers drop` (and the same for every bin)
4. `teach.py hold`, then `teach.py list` must say `all poses taught`.
5. Check each one slowly, hand on the 12 V plug: `teach.py go slot1 hover`, `teach.py go slot1 grip`, ...
6. `AGENT_BACKEND=real AGENT_DRY_RUN=1 ./demo.sh`, then without `AGENT_DRY_RUN`.

**Changing the movement** needs no code: edit the step order in `motion.json` → `sequences` (e.g. add `"slot.pre"` before `"slot.grip"`), then teach the new pose (`teach.py slot1 pre`). `teach.py list` shows what is still missing.

Every real move is solved first and refused if the solution's motor steps differ from the taught ones by more than 150 (a flipped joint). STOP is checked before every move.

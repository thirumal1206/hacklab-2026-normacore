# Agent config: what each teammate fills in

Claude decides only from these files. Nothing is measured or guessed during a run. Replace every `null` with a measured value; leave a value `null` only if the feature is not used. Keys starting with `_` are help text.

Units: cm in the robot base frame (x forward, y left, z up, table = 0), degrees, motor steps 0-4095.

| File | Owner | Needed by | What |
|---|---|---|---|
| `workspace.json` | Environment | 17:00 | Reach limits, input zone, no-go zones, bin positions and sizes, camera ids, calibration error, bump markers |
| `motion.json` | Motion | 19:30 | Taught home and bin poses (motor steps), hover and grasp heights, gripper values, speeds, measured pick results |
| `vision.json` | Vision | 19:30 | Locked camera settings, detection thresholds, code reading method and measured read rate, height classes |
| `catalog.json` | Agent + team | 18:00 | Every product that may appear: code, name, size, fragile |
| `rules.json` | Agent + team | 18:00 | Today's date, recall list, bins in priority order, vetoes, failure policy |

## How to measure (do not estimate)
- **Reach:** drive the tip to the point with the arm loaded (holding a pack). It counts only if it arrives within 1 cm and the shoulder reports no overload.
- **Bins:** for each bin set `reach_tested: true` only after a real drop into it worked.
- **Poses:** move the arm there (leader arm or by hand), then copy `joints[].steps` from `GET http://127.0.0.1:8020/api/v1/arms`.
- **Calibration:** copy `rms_mm`, `max_mm`, `points` from the homography file after fitting. Above 10 mm RMS: recalibrate.
- **Camera ids:** `GET http://127.0.0.1:8020/api/v1/cameras`. Re-check after any replug.

## Interfaces the tools expect
- Vision: `scan()` -> `[{id, x_cm, y_cm, angle, width_cm, height_class, code, product, expiry, crop}]` (`code`, `expiry` may be null); `verify_place(id, bin)` -> `{ok, photo}`
- Motion: `pick(x_cm, y_cm, angle, height_class)` -> `{holding, reason}`; `place(bin)` -> `{placed, reason}`; `home()`
- Environment: `pixel_to_table(u, v)` -> `(x_cm, y_cm)`; `camera_bumped()` -> bool

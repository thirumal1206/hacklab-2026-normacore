# ElRobot calibration through the elrobot-ik HTTP API

Standalone tools from our ElRobot setup. They talk to NormaCore's `elrobot-ik` service (HTTP on 127.0.0.1:8020, from
`norma-core/software/station/examples/elrobot-ik-api`), not to `arm.py`. Kept separate so they add no dependencies to the
main project.

| File | What |
|---|---|
| `start_ik.sh` | Starts elrobot-ik on 127.0.0.1:8020 (Station must already run: `station --web --tcp`) |
| `calib_overview.py` | Overview camera -> table homography: the arm touches a 12-point grid, you click the jaw tips on a local page (127.0.0.1:5070), it fits and saves `calib/overview_homography.json` |
| `floor_probe.py` | Finds the table height along x by stepping down until the arm stops following (contact) |
| `pick_place.py` | Pick at (x, y), place at (x, y), rest. Plans every pose first, aborts before motion on failure |
| `servo_regs.py` | Read-only register dump: voltage, max torque, gains, load, status (run from the elrobot-ik-api folder) |
| `ik_client.py`, `safety.py` | HTTP client (IK branch closest to the current joints, tilt search) and shared settings |
| `calib/overview_homography.json` | Calibration from 21.11.2026 (see below) |

## Run
```
cp env.example .env            # FOLLOWER_ID, OVERVIEW_CAMERA, IK_URL; check the IDs with GET /api/v1/arms and /cameras
./start_ik.sh                  # in its own terminal
uv run --with httpx --with flask --with python-dotenv --with pillow python calib_overview.py
uv run --with httpx --with python-dotenv python pick_place.py 0.197 0.038 0.207 0.158        # dry run; add --go to move
```
Clear the grid area first (x 0.13..0.25 m, y +-0.12 m). E-stop: the 12 V follower plug. Do not move the overview camera
after calibrating. Camera IDs change when a camera is replugged; the follower is identified by its bus serial, and the
API reports `role: follower` only when motor 1 reads at least 10 V.

## Measured 21.11.2026 (one arm, one table)
- Homography: 11 points, RMS 10.1 mm, max 16.7 mm (worst at the centre column, where the jaw tips hide under the wrist board).
- Floor: about z 0.029 at x 0.19; fit `0.03385 - 0.02625*x` still matched. Touch-downs use `FLOOR_OFFSET=0.010`.
- Pick and place: ball at (0.197, 0.038) picked (gripper stalled at 0.13-0.14 open) and placed on a card at (0.207, 0.158), twice.
- Shoulder sag: all servos come out at max torque 500/1000, P 16, I 0. Under load the arm sits 3 to 23 mm below the
  commanded z (pose and direction dependent). On the return move M2 went into `SEK_SERVO_ERROR` (likely overload).
  Not fixed: changing torque limits or gains is a motor-config decision.
- `floor_probe.py` was not re-run on this table (its `MIN_Z` is -0.005: it relies on contact detection).

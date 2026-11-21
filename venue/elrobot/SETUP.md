# ElRobot Setup on MacBook (NormaCore Station)

Goal: both arms visible in Station at http://localhost:8889, both calibrated BY HAND, follower mirrors leader.

From our teammate's setup on the same kit (Leukerbad, Nov 2026), adapted to our serials. Status 21.11.2026: both buses detected; follower auto-calibrated (see the warning in section 3), leader not calibrated.

## Known devices
| What | ID | State |
|---|---|---|
| Leader bus (pistol handle) | `5B61037139`, port `/dev/cu.usbmodem5B610371391` | connected, 8 motors OK |
| Follower bus (gripper + camera) | `5B79016927`, port `/dev/cu.usbmodem5B790169271` | connected 14:37 UTC, 8 motors detected |
| Cameras | 2x "KD-USB Cameras" | connected via hub |
| Driver boards | USB-serial bridge WCH CH343 (`1a86:55d3`) | no microcontroller |

## 1. Start Station (localhost)
Do NOT start the GUI app from Applications (it launches a second station on the same ports). Run the binary in a terminal tab that stays open:

```bash
cd venue && "/Applications/NormaCore Station.app/Contents/Resources/bin/station" --web 127.0.0.1:8889 --tcp 127.0.0.1:8888
```

- Web UI: http://localhost:8889
- TCP for the Python client: localhost:8888
- Log: `station.log` (only if started with `> station.log 2>&1`), data in `station_data/`
- Bound to 127.0.0.1 on purpose: Station has no authentication, and `--web --tcp` alone listens on 0.0.0.0, reachable from the venue WiFi.

## 2. Wiring so that Station sees BOTH arms
Power bricks unplugged from the strip while wiring.

1. Both bases clamped to the table (2 clamps each).
2. Mac USB-C -> adapter -> Sabrent hub (the hub's fixed black cable).
3. Hub port 1 -> white USB -> **leader** driver board.
4. Hub port 2 -> white USB -> **follower** driver board.
5. Hub ports 3 and 4 -> the two cameras.
6. Press the button of every used hub port: LED on = port live. LED off = device invisible.
7. Power, in this order, hands off the arms:
   - **5 V brick -> leader** board (pistol handle).
   - **12 V brick -> follower** board (gripper + camera).
   - Never 12 V into the leader: it burns the 7.4 V servos. Read the OUTPUT line on the label first.

Check at http://localhost:8889: **2 bus cards**, each with **M1..M8, STATUS OK**, and 2 cameras.

If the hub cannot carry 4 devices (devices drop out): give the hub its own power adapter, or plug one driver board directly into the Mac's second USB-C port.

## 3. Manual calibration (do it for BOTH arms, one after the other)

Auto Calibrate on ElRobot also leaves the motors' protection current at its calibration values (50 for motors 1-4, default 500): the cleanup that restores them runs only for SO-101 in norma-core. Seen 21.11.2026: the shoulder (motor 2) tripped overload on a 10 deg lift after Auto Calibrate.
We guide every joint by hand. Never use "Auto Calibrate": it drives each joint until it stalls and knows nothing about the table, so collisions end up saved as joint limits.

Order: leader first (light, back-drivable), then follower.

For each bus card:
1. Bus card dropdown must be "(Self-controlled)". CURR column = 0 on all motors (torque off, arm limp).
2. Hold the arm and put it in a **middle pose**: every joint about mid-travel, gripper or trigger half open.
3. Click **Calibrate**. The page resets the calibration and shows "Move all motors through their full range".
4. Move the arm **freely, in combined motions**, slowly, until every joint M1..M8 (including gripper/trigger) has reached both of its ends at least once. Station only records min and max per motor, so the order does not matter. Stop at the mechanical stop, do not push into it. Keep the arm clear of the table and of itself.
5. Watch the table while moving: RANGE min must be smaller than max, and % must stay between 0 and 100.
6. Press **Save**.

Follower note: with torque off it is limp and heavier. Support it with one hand the whole time, do not let it drop.

Bad result and fix:
| Symptom | Cause | Fix |
|---|---|---|
| RANGE min > max | Joint crossed the encoder wrap (0/4095) | Put the joint mid-travel, click Calibrate again |
| % above 100 or below 0 | Range recorded too small or old auto result | Redo steps 2 to 6 |
| RANGE stays 0000..4095 | Joint was not moved | Move it, then Save |
| "Motor ID mismatch" in log | One-off bus collision during reset | Click Calibrate again |

## 4. Teleop: follower mirrors leader
Only after BOTH arms are calibrated by hand.
1. Put both arms in roughly the same pose. Hand on the 12 V plug in the power strip (e-stop).
2. On the **follower** bus card, open the dropdown (default "(Self-controlled)").
3. Select the **leader bus serial** (`5B61037139`) as source. Mirroring starts.
4. Move the leader slowly, small motions first.
5. Stop: set the dropdown back to "(Self-controlled)". Emergency: pull the 12 V follower plug.

"(Web-controlled)" drives the follower from the browser instead of the leader.

## 5. Cameras
On the bus card, pick the gripper camera as MAIN and the table camera as AUX. If macOS asks for camera access, allow it. Station records motors + cameras automatically in its history, export to Parquet later (repo `docs/datasets/export-parquet`).

## If it fails
| Symptom | Most likely cause | Fix |
|---|---|---|
| "connect a robot", no bus | Hub port button off, hub not plugged into the Mac, charge-only cable | Press the port button, check the hub cable, or plug the board directly into the Mac |
| Only 1 bus shown | Second board's hub port off or no USB | Press its button, reseat the white cable |
| Bus shown, "No motors connected" | Arm PSU not plugged in, or 3-pin servo cable loose | Check DC plug and the servo cable at the board |
| Follower jerks on mirror start | Not calibrated, or poses very different | Redo step 3, match poses before mirroring |
| Camera missing | Hub port off or hub underpowered | Press the button, power the hub, or plug the camera into the Mac |
| Web UI dead | Station stopped (terminal closed) | Run the command from step 1 again |

Repo with source, examples, URDF: github.com/norma-core/norma-core

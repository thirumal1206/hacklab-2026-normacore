# ElRobot by NormaCore: Hardware Inventory and Specs

Kit received 14.11.2026 at the Leukerbad Robotics Hackathon. Source: NormaCore "thank you" card (normacore.dev, "From Madrid with love") and the repo `github.com/norma-core/norma-core` (`hardware/elrobot/`).
`(?)` = not confirmed from photos yet.

## Model
- **ElRobot**, a fully 3D-printed **7+1 DoF** arm (7 joints + 1 parallel-jaw gripper) built for imitation learning.
- Delivered as a **leader + follower pair**, both assembled, black PLA.
- Specs: reach 430 mm, about 800 g per arm.
- Wrist roll, handle and trigger parts come from SO-ARM101, but this is NOT an SO-101: it has 8 motors per arm instead of 6.

## Kit contents (from photos)
| # | Item | Qty | Notes |
|---|---|---|---|
| 1 | ElRobot **follower** arm, assembled | 1 | 8x ST3215 **12 V** (30 kg) servos, parallel-jaw gripper, gripper camera mounted on the wrist |
| 2 | ElRobot **leader** arm, assembled | 1 | 8x ST3215 **7.4 V** servos (C044 1:191 or C046 1:147 for back-drivability), pistol handle + trigger + hook |
| 3 | Feetech ST3215 spare servos (boxed) | 2 | Check the label: 12 V or 7.4 V, which gear ratio |
| 4 | Quick-grip clamps (black, red release) | 4 | 2 per arm, to fix the bases to the table |
| 5 | Small printed housings with NormaCore logo | 4 | 2 appear to hold the bus servo driver boards (USB-C + DC visible), 2 empty or camera mounts (?) |
| 6 | Bare PCB in ESD bag | 1 | Spare bus servo driver board or USB camera board (?) |
| 7 | Power supplies in white boxes | 2 | Expected: **12 V / 4 A for the follower**, **5 V / 3 A for the leader** (CHECK LABELS) |
| 8 | Power adapter in a plastic bag | 1 | Probably for the USB hub (?) |
| 9 | IEC C13 to Schuko power cords | 2 | For the boxed power bricks |
| 10 | USB-A to USB-C cables (white braided) | 2 | Data cables, one per driver board |
| 11 | Sabrent 4-port USB hub with switches | 1 | Cameras go through the hub. Needs external power for high-fps cameras |
| 12 | Table camera on long black printed stand (knob + USB cable) | 1 | Overhead/side scene camera (UVC) |
| 13 | Black plate with NormaCore logo and clip | 1 | Mount/base for the stand or the phone (?) |
| 14 | Thank-you card with QR code to normacore.dev | 1 | |

## Joint map (follower URDF `hardware/elrobot/simulation/elrobot_follower.urdf`)
| Motor ID | URDF joint | Limits (rad) |
|---|---|---|
| 1 | rev_motor_01 | -1.551 .. 1.551 |
| 2 | rev_motor_02 | -1.612 .. 1.612 |
| 3 | rev_motor_03 | -1.761 .. 1.761 |
| 4 | rev_motor_04 | -1.753 .. 1.753 |
| 5 | rev_motor_05 | -2.62 .. 3.252 |
| 6 | rev_motor_06 | -1.378 .. 1.764 |
| 7 | rev_motor_07 | -3.201 .. 2.734 |
| 8 | rev_motor_08 (gripper, drives 2 prismatic jaws, 0 .. 25.5 mm each) | 0 .. 2.203 |
Joint semantics (which one is shoulder pan, elbow and so on) still need to be verified on the real arm.

## Electrical
- Bus: Feetech ST3215 serial bus servos, TTL half-duplex, typically 1 Mbps. One driver board per arm (Waveshare Bus Servo Adapter A or Seeed XIAO driver).
- The driver board takes power only from the DC jack. USB carries data only.
- **NEVER plug 12 V into the leader (7.4 V servos). That burns the motors.** Label both PSUs and cables before powering on.
- The USB cables must be data cables, not charge-only.

## Software options
1. **NormaCore Station** (official, supports ElRobot): single binary with a web UI at `http://localhost:8889`, data collection, teleop, dataset export to Parquet.
   - macOS: `station-macos-arm64.dmg` from GitHub Releases (v0.1.0-beta.10 or newer). Run `station --web 127.0.0.1:8889 --tcp 127.0.0.1:8888` (TCP 8888, web 8889).
   - Python client `station_py`, examples: `software/station/examples/st3215-remote-teleop-py` (leader to follower mirroring) and `so101-autocalibration-py`.
   - SmolVLA fine-tune pipeline in `software/ai/smolvla_py` (written for SO-101, would need adapting to 8 DoF).
2. **Direct Feetech SDK** (`scservo_sdk` / `feetech-servo-sdk`): read/write registers in Python. Goal position = 0x2A, present position = 0x38, torque enable = 0x28. Position units are 0..4095 per revolution.
3. **LeRobot**: no official ElRobot robot class (SO-101 has 6 motors). It would need a custom 8-motor config with `FeetechMotorsBus`. Medium effort.
4. **Simulation**: the URDF in the repo, MuJoCo, and the web playground at normacore.dev/elrobot-urdf.

## Open questions
1. Are the servo IDs 1..8 already set and calibrated by NormaCore?
2. PSU labels: which brick is 12 V and which is 5 V/7.4 V?
3. Gripper camera model (UVC?) and table camera model?
4. What is the bare PCB in the ESD bag?
5. Does Station see both buses on macOS out of the box? Check the serial numbers in the web UI.
6. Training compute: on site GPU, cloud (Nebius per smolvla_py docs), or the Jetson?

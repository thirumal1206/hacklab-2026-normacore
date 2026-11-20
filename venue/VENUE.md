# Venue checklist (NormaCore track)

Everything below is untested on real hardware. Record what you see in `CHANGELOG.md`.

## Before leaving (each laptop)
1. `.venv/bin/python arm.py selfcheck --repo <norma-core checkout>` must print `ALL OK`.
2. Print `venue/aruco_markers_A4.png` at 100%, no scaling. Measure a marker: 50 mm.
3. Station binary: `venue/bin/station` (arm64 macOS, built from norma-core 6b494f7, plus one local patch so the built-in camera is listed; sha256 in CHANGELOG). On Linux, build from source instead.
4. Allow camera access for the terminal app you start it from (macOS asks once).

## At the opening (ask NormaCore staff)
- Where does Station run (June: on a Raspberry Pi), what is its address, and which version or branch? Use a norma-core checkout of the **same** version for `--repo`: an older checkout's protobufs cannot read the newer VP8 camera frames.
- Camera codec: `arm.py` decodes JPEG, NCHW and VP8 (VP8 tested on encoded test video, not on a real Station). If images fail, ask for `codec: jpeg` in the Station's `usb-video` config.
- Station on another machine: start the daemon with `--station <pi-ip>:8888`. It has no authentication: anyone on that network can move the arm.

## At the table
1. **Which robot?** SO-101 has 6 motors (ids 1-6), ElRobot has 8 (ids 1-8). `venue/start.sh` warns `need ids 1-N` if the count is wrong.
2. Hand on the arm. `arm.py relax` makes it limp (emergency stop).
3. Start: `STATION_BIN=venue/bin/station NORMA_REPO=<checkout> venue/start.sh so101|elrobot`
   - Check the printed camera queues. `arm.py snapshot top wrist`, open both PNGs, swap with `TOP=<md5> WRIST=<md5>` if reversed.
   - The Station listens on 127.0.0.1 only. Do not open it to the venue network: it has no authentication.
4. **Guided path for steps 3-6:** `arm.py bringup` (hand on the arm). It asks y/n at each step, prints the `ARM_SIGN`/`ARM_GRIP_FLIP` to restart with, and walks you through marking each ArUco marker (type `dx dy` in cm to jog the tip). The manual steps below are the fallback.
   - Joint directions, by hand (keep your hand on the arm): Small moves with `arm.py joints ...` one joint at a time, compare with what the sim does (`arm.py serve --backend sim` on another port).
   - Joint moves the wrong way: restart with `ARM_SIGN="1,-1,1,..."` (one value per motor, -1 flips).
   - Gripper: `arm.py grip open`. If the jaws close, restart with `ARM_GRIP_FLIP=1`.
   - ElRobot: joint angles come from mapping the calibrated range onto the URDF limits (as NormaCore's viewer does). Not verified.
5. **Calibrate the top camera** (fixed, looking down): lay the 4 markers flat and spread out, `arm.py calibrate top`, then for each marker `arm.py move X Y 1` onto its centre and `arm.py calib-mark ID`. Reprojection error must be under 5 px.
6. **Smoke test:** one object, `arm.py locate`, `arm.py pick ID`, `arm.py place X Y`. Then 10 timed trials.
7. **Operator:** start Claude Code (or Codex) inside `operator/`; it reads `AGENTS.md`. Claude Code also offers the `arm` MCP server from `operator/.mcp.json` on first start: approve it, and images arrive inline in the tool results (the CLI still works without it). Always confirm results with `locate` or `look top`: operators over-claim from images.
8. **Live view for the judges:** `arm.py view` in another terminal, open http://127.0.0.1:8766 (ARM_PORT+1) on a big screen. It shows the latest image and every command, with the operator's `say` lines highlighted. It updates after each command finishes, not during a move.

## Fallback: taught grid (if IK or calibration is off)
June's winner did this. It needs no kinematic model and no `calib-mark`.
1. Tape the ArUco sheet flat in the workspace, then park the arm out of view and run `arm.py calibrate-sheet top`. The table frame becomes the sheet: cm, origin at its top-left corner, x right, y down.
2. `arm.py relax`. By hand, put the jaw tips at grasp height on 9 points (a 3x3 grid covering the objects and the box, coordinates in sheet cm), jaws straight. At each point, hold the arm still and run `arm.py teach X Y`. The servos keep reporting position while limp.
3. From one taught point, lift the arm straight up about 6 cm and run `arm.py teach-lift`.
4. Use `gpick ID|X Y` and `gplace ID|X Y` instead of `pick`/`place`; `locate` works in sheet cm. Tell the operator to do the same.
- Sim: 8/10 cube pick-and-place, interpolation error 0.6-0.9 cm. Jaw rotation assumes +1 deg of roll = +1 deg of jaw; if yaw goes the wrong way, restart with `ARM_GRID_ROLL=-1`. Taught points are saved in `runs/grid.json`; delete it to leave grid mode.

## If the operator is Codex
- Codex's default sandbox blocks the CLI's connection to the daemon (verified). Start the daemon with `ARM_SOCK=$PWD/runs/arm.sock` and give the operator the same variable: the socket works under `codex sandbox --allow-unix-socket <path>` (verified), but how a running Codex session is given that permission is not verified (the config key did not work in my test). If Codex asks to run a command outside the sandbox, that approval is the fallback.
- Codex is installed in `tools/codex` (0.169.1); log in yourself with `tools/codex/node_modules/.bin/codex login`.

## Known limits
- SO-101: gripper-down reach about x 10-32 cm. ElRobot: x up to 26 cm, z up to 10 cm; the arm may collide with the table (not modelled).
- Frames can be uniformly black at the start; `look` waits up to 6 s for a non-black frame.
- Flat objects may not be graspable: in sim the SO-101 jaw tips stop at 0.6 cm above the table, so a pencil lying flat (0.7 cm) failed 10/10. Try a real pencil early; real jaws differ.
- Unknown until Saturday: the actual task, judging, and the camera count and placement.

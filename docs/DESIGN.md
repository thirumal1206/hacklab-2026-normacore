# NormaCore track: design

Track (from the acceptance email): integrate the Station API into Codex or Claude so it can control the robot by looking through the cameras. The exact task and the judging are announced at the opening on Sat 21 Nov. Replaces `docs/archive/PLAN.md`.

## 1. Thesis

The LLM supervises and deterministic code moves the arm.
- The agent decides what to do, where it is, and whether it worked, from camera images.
- Code does the geometry: camera-to-table mapping, IK, and grasp primitives. It also enforces safety: workspace clamps, a speed cap and torque-off.

Evidence:
- **Published work** (collected by a research subagent; I have not checked these papers myself):
  - SO-101 Bench (arXiv 2610.36420): direct tool calls 35%, with a code harness (segmentation, localisation, IK) 67%, with a learned grasp 85%. The authors' conclusion is that "the harness matters more than the model".
  - VIA (arXiv 2607.11119): Claude Code and Codex with bare `set_pose`/`observe` scored 6.7–23%. With a preview-and-verify interface they scored 65–85%.
  - Set-of-Mark: grounding rose from 25.7 to 86.4 when the model picks a numbered mark instead of regressing coordinates.
  - No system found that has the LLM output joint angles successfully.
- **Our own measurements (sim, seed 7, Sonnet operator):**
  - Reading positions off the metric grid overlay, the agent was within 0.3–0.45 cm of ground truth for all 4 cubes and 1.0 cm for the box.
  - Its yaw estimates were wrong (one sign flip), which caused missed grasps.
  - So positions can come from the LLM, and orientation must come from pixels (`snap`).

## 2. Station API (verified from source, not yet run)

Source: norma-core `main` @ `6b494f7` (2026-11-10), read-only clone. Prebuilt release: v0.1.0-beta.10.

| Need | How |
|---|---|
| Connect | TCP `:8888`. Frames are an 8-byte little-endian length followed by a protobuf (`normfs.ClientRequest`). Handshake `SetupRequest{version:1}`, then a keep-alive ping every 1 s. Client: `software/station/shared/station_py/client.py`. |
| Command motors | Write a `StationCommandsPack` of `DriverCommand{type: STC_ST3215_COMMAND, body: st3215.Command}` to queue `commands`. Use `sync_write` per bus. Registers: torque 0x28, acceleration 0x29, goal position 0x2A, goal speed 0x2E. |
| Joint state | Follow `st3215/inference` (`InferenceState`: buses, then motors). Each motor has raw register bytes (present position at 0x38, sign-magnitude, 0–4095) and the calibrated `range_min`/`range_max`. |
| Cameras | One queue per camera, `usbvideo/<md5(unique_id)>`, carrying `RxEnvelope{type: ET_FRAMES, frames: FramesPack}` as JPEG or NCHW. Stored frames are resized so the short side is `resize_target` (224 by default). **Set it to ≥480 for the VLM.** |
| All in one | Queue `inference/normvla`: `Frame{joints[position_norm, goal, current_ma], images[jpeg 224x224]}` every 100 ms. Also available in shared memory at `/tmp/normvla`. |
| Python protobufs | Generated code is committed in `target/gen_python`. It depends on `shared/gremlin_py` (pure Python, stdlib only; audited the imports: no subprocess, eval or network outside the client socket). No protoc needed. |
| Security | No auth, and it binds to `0.0.0.0` on ports 8888 and 8889. On venue Wi-Fi, anyone can move the arm, so firewall it or bind to localhost. |

Hardware: NormaCore's own ElRobot is a **7+1 DoF** arm with a URDF in `hardware/elrobot/simulation/`. We don't know whether the event provides an SO-101 or an ElRobot. `arm.py` now supports both: `serve --backend station --robot so101|elrobot` (profile in `arm.py`, IK in `elrobot/ik.py` from the compiled URDF). ElRobot top-down reach: x up to 26 cm and z up to 10 cm only. Kinematics checked (`elrobot/check.py`); nothing run on hardware, and collisions are not modelled.

## 3. Architecture

```
Claude Code / Codex  --shell-->  arm.py (CLI)  --HTTP 127.0.0.1-->  arm.py serve (daemon)
                                                                     |- skills: pick/place/move/park/grip
                                                                     |- perception: look (grid), locate (marks), snap
                                                                     |- safety: workspace, z floor, speed cap, joint ranges, relax
                                                                     `- backend: SimBackend (MuJoCo) | StationBackend (TCP 8888)
```

- **One daemon** holds the sim or the Station connection, and owns all safety checks. It is single-threaded, so commands never overlap. A CLI call is stateless and returns JSON, and images are written as PNG paths.
- **Perception tools:**
  - `look`: images with a 5 cm metric grid projected onto the table, so the LLM reads positions directly in cm.
  - `locate`: colour blobs mapped to table centimetres, a numbered marks image (Set-of-Mark), `kind` (object or container) and `inside` (which container each object is in).
  - `snap`: inside `pick X Y` with no yaw. It refines the LLM's rough point to the nearest object's centre and yaw.
- **Camera-to-table mapping:** a homography per fixed camera.
  - Sim: computed from the MJCF camera.
  - Real arm: `calibrate CAM` detects the ArUco markers from `venue/aruco_markers_A4.png`. Then `calib-mark ID` records the gripper tip's FK position on each marker centre. This needs no ruler and no base-frame guess, and gives a reprojection error. The tip itself is the reference, so homography errors and kinematic errors partly cancel.
- **Real joints:** degrees = (raw − mid(range)) / (4096/360), the same convention as LeRobot's `so101_follower` and the MJCF. The per-joint `SIGN` must be checked at the venue (§6, step 3). Gripper: 0–100 over the calibrated range.

## 4. CLI or MCP

**Decision: CLI first. If the judges want an "integration", add a ~20-line MCP wrapper later that calls the same daemon.** Done 2026-11-20: `mcp_server.py` (stdlib only, no `mcp` package), registered in `operator/.mcp.json`.

| | CLI (`arm.py`) | MCP server | CLI-Anything (HKUDS) |
|---|---|---|---|
| Works in Claude Code | yes; images via the Read tool (verified) | yes | — |
| Works in Codex | yes; images via `view_image` | yes, image results not verified | Codex support is "experimental" |
| New dependencies | none (stdlib) | the `mcp` package (needs an audit) | plugin plus code generator |
| Testable by a human from a shell | yes | no | yes |
| Token overhead | one help text | tool schemas on every turn | — |

- **CLI-Anything verdict: don't use it.** It generates CLIs for GUI apps from their source code (GIMP, Blender), with no robotics examples. A 10-command CLI written by hand is smaller and clearer.
- **External data (Scalekit, 75 runs, small sample):** MCP used 4–32x more tokens, and its failures were remote timeouts. That cost is mostly from having many tools, and it barely matters with about 10 tools.

## 5. Simulation's role

- MuJoCo SO-101 tabletop, the same model as the real arm's IK.
- It is the dev and regression harness for the whole loop: agent, CLI, perception, IK.
- **Measured success = privileged `score` (the operator never sees it), over fixed seeds, with fresh operators.**
  - The operator is a fresh subagent that only sees the CLI help and the task prompt. It plays the role Claude Code or Codex will have at the event.
- At the venue, the sim also works as a "dry run" twin: `serve --backend sim` and the same commands, to rehearse before touching the real arm.

## 6. Venue runbook (draft)

1. Start the Station (built and run locally on 2026-11-19; see CHANGELOG for the build recipe).
   - Set `usb-video.resize_target: 480` in `station.yaml`.
   - Bind localhost: `station --config station.yaml --tcp 127.0.0.1:8888` (no `--tcp` flag means no listener).
   - On macOS, run it from a terminal app that holds camera permission; a process started from another app's shell is denied. The macOS driver lists external cameras only (verified).
   - Camera queue name `usbvideo/<md5(unique_id)>` verified. Set `resolution` explicitly (e.g. `1280x720`); `auto` gave 640x480. Some frames can be uniformly black (cause not established); `image` waits up to 6 s for a non-black frame. Check the first `look` at the venue by eye.
   - Check `st3215/inference` shows motors 1–6 with calibrated ranges (the Station web UI on :8889).
2. Start the daemon:
   ```
   python arm.py serve --backend station --repo <norma-core> --cam top=usbvideo/<md5> --cam wrist=usbvideo/<md5>
   ```
   The camera queue names are the folders in `station_data/usbvideo/`.
3. Sign check, with a hand on the arm:
   - `joints 0 0 0 0 0 50`. Compare the pose with the sim at the same joints.
   - Then `joints 10 0 0 0 0 50` and so on, one joint at a time. Flip `SIGN` where a joint moves the wrong way.
4. Mount the top camera fixed, looking down. Lay the 4 ArUco markers flat and spread out in the workspace.
   - `calibrate top`, then for each marker: `move X Y 1` onto its centre, then `calib-mark ID`.
   - Reprojection error should be < 5 px.
   - Then remove the markers, or keep them outside the workspace.
5. Smoke test: one cube, `locate`, then `pick 1`, then `place`. Then 10 timed trials. Record them in `CHANGELOG.md`.

## 7. Experiments (record every result in CHANGELOG.md)

| ID | Question | Metric | Status |
|---|---|---|---|
| E1 | Operator with `locate` (CV-assisted) | cubes in box, attempts | done: 4/4, 5 attempts |
| E2 | Operator vision-only (grid reading) | cubes in box, position error | done: 3/4; position error 0.3–1.0 cm, yaw wrong |
| E3 | Grasp primitive alone (no LLM) across seeds; fix the failing yaw cases | success / 10 seeds | done: roll 0 9/10, roll −90 10/10. Pick now retries once with the jaws rotated |
| E4 | Fresh operators with `operator/AGENTS.md`, marks, snap and retry | cubes in box over 3 seeds | done: 12/12 cubes, 3/3 seeds; 9 of 12 cubes needed one pick |
| E6 | Objects that are not colour cubes (pen, cup, eraser, tape roll; unsaturated) | cubes in box, 3 seeds | done: baseline 1/4; colour-independent `locate`/`snap` 10/12; plus multi-seed IK, travel fallback, round-object yaw 45: 11/12 (3/4, 4/4, 4/4). Open: thin bar near the base, `dropped_on_the_way` false alarms for round objects |
| E5 | Codex CLI as operator (same prompt) | same as E4 | needs Codex installed (audit first) |
| R1–R3 | Real arm: sign check, calibration reprojection error, 10-trial pick | px error, success/10 | at the venue |

## 8. Risks

| Risk | Mitigation |
|---|---|
| Arm differs (ElRobot 7+1 DoF) | The backend split keeps everything above it. Swap the IK model (URDF to MuJoCo). |
| Real grasp offsets (jaw geometry, servo deadband 20 ticks ≈ 1.8°) | `snap`, `holding`/`dropped` checks, retry rules in the operator prompt. Measure R3 early. |
| Lighting and colours on real objects | `snap` uses saturation and needs a dull table. The fallback is the LLM's grid reading, which is good to about 0.5 cm in sim. |
| Agent chains commands without checking | Operator rule: one physical command per turn, read the result before the next. |
| Network exposure of the Station | Localhost or firewall; `relax` command; a hand on the arm during the first runs. |
| Close neighbours nudged by the open jaw | Agent re-locates before each pick. Possible fix: choose the jaw axis away from the nearest neighbour. |

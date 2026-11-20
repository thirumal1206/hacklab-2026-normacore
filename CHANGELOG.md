# Changelog

Newest first. Every entry: what changed, the measured result, the decision it led to.

## 2026-11-20

- **Voice assistant mode ("Jarvis").** `say` now also speaks (macOS `say -v Daniel`, non-blocking; `ARM_TTS=say|voicebox|off`, `ARM_VOICE=<voice>`; the Voicebox path follows its README and is untested, nothing installed). The live view has a Talk button (browser speech recognition) and a text box; both append to `runs/inbox.jsonl` and show "You: ..." in the log. `arm.py listen [--wait S]` (and MCP `listen`) returns the next instruction. `gesture wave|nod|yes|no` moves around park. AGENTS.md has an assistant loop: listen, act, `say` one sentence, listen.
  - **Checked (sim):** POST to /command -> `listen` returned it, the next `listen` timed out with null; wave, nod and yes ran; speech played; MCP `listen` and `gesture` worked; the page shows the input bar and the "You:" line. **Not checked:** the Talk button with a real microphone (Chrome sends audio to Google for recognition; Safari may work on-device), and a full Claude assistant loop.

- **Taught-grid fallback (the June winner's approach): no kinematic model.** `teach X Y` stores the current joints as the grasp pose at a table point; `teach-lift` stores one lift offset; each joint is fitted as a quadratic in (x, y) by least squares (needs 6+ points, 3x3 best). `gpick`/`gplace` run open, lifted, grasp (slow), close, slow lift; they retry with the jaws +90. `calibrate-sheet` sets the camera homography from the printed ArUco sheet alone (marker centres 5/15 cm by 5.5/18.5 cm, measured from the 300 dpi PNG). In grid mode, `locate` looks 5 cm around the taught points.
  - **Sim check (SO-101, poses taught via IK as a stand-in for hand teaching, 3x3 at x 16/22/28, y -14/-1/12):** tip error at 5 off-grid points 0.57-0.89 cm; red cube into the box 8/10 (same 8/10 as the IK pick on these seeds). `calibrate-sheet` was not tested on a camera image; hand teaching was not tested on hardware.

- **Research-driven additions (RESEARCH.md).**
  - `background [cam]`: empty-table photo; `_blobs` subtracts it per pixel instead of the table's median colour. In sim (uniform table) `locate` was identical with and without it (seeds 3, 5); the benefit is expected on real, textured tables, not measured.
  - `locate` objects carry `shape` (block, long, round, thin). Pencil scene and E6 scene both classified correctly.
  - `push ID|X Y TX TY`: closed jaws at 1 cm, straight line in 1 cm steps. Pencil moved 2.6 and 2.9 cm of a 5.7 cm request (seeds 3, 5), and it rotates: a rough reposition only, not a way into the box.
  - June winner details added to RESEARCH.md (taught grasp grid, overload-safe lifts).

- **VP8 camera frames.** `StationBackend` now keeps each camera's VP8 chain since the last keyframe (keyframe detected from the VP8 bitstream, so it does not depend on the protobuf version) and decodes the latest frame with OpenCV's bundled FFmpeg via an in-memory IVF file: no new package.
  - **Checked:** 75 frames encoded with ffmpeg/libvpx (keyframes every 30); joining mid-stream at frame 10 and decoding up to frame 66 gives a 7-frame chain, 11 ms, PSNR 46.9 dB against ffmpeg's own decode of frame 66 (a different frame scores 29.4).
  - **Not checked:** a real dev-branch Station. Found in the source: protobufs generated from `main` raise on `FF_VP8` (enum value 2 unknown), so `--repo` must be a checkout of the Station's own version. Added to VENUE.md.
- **Thin objects.** `_blobs` called a thin bar "round" (low fill ratio of its box) and gave yaw 45; now round needs a near-square box. The pencil now gets its real yaw (31 vs body 31.2 deg).
  - The pencil still fails 0/10 in a grasp-only check: the SO-101 jaw tips stop on the table at z 0.6 cm (commanded 0.4), and the pencil is 0.7 cm tall. Tried a lower grasp height; it gained nothing and pressed into the table, so reverted. Regression check, cup: 8/10, same as before the change. Operator rule 7: after 2 failed picks of a flat object, say so and move on. Not rerun with the LLM (no grasp behaviour changed).

- **Rehearsal on non-cube objects through MCP (seed 5, new scene `so101_tabletop_pencil.xml`: E6 objects with the pen replaced by an unpainted 0.7 x 8 cm pencil).** Same operator setup and knock-out as before. **3/4 (`score`): cup, eraser, tape roll in; pencil 0/4 picks.** The knock-out (eraser out at 26 s) was found and fixed again. 38 commands, 69 s, 37 turns, $0.30. The operator stopped honestly with "Not done".
  - Pencil failure causes (from the log): grasp height 1.2 cm is above the pencil's top (0.7 cm), and `snap` reported yaw 45 for it (the round-object rule), not along its axis. Not fixed yet.
  - `locate` was seen to drop the tape roll on place (`dropped_on_the_way: true`) while it still landed in the box: the known false alarm for round objects.
- **Research (subagent, web and GitHub; sources in its report, not all verified by me):**
  - The June 2026 edition (20-21 Jun, CIC Berlin) had the **same NormaCore brief**. Hardware: ElRobot, 2 USB cameras, **Station on a Raspberry Pi**. Reported winner: Claude (Haiku) via a FastMCP server on Station as a slow supervisor over a SmolVLA policy (github.com/tejavadlamudi03/norma-robot). Team 4 notes "pure Claude Vision too imprecise for grasping".
  - November: opening Sat 11:00, submission Sun 17:45, judging 18:00, pitches 19:00. No published judging criteria; HackLab's builder advice: a live demo under a minute, one clear behaviour, a recorded backup, honesty about what is scripted.
  - norma-core `dev` (65f9ab9, 2026-10-20, PR #178) adds an ElRobot IK HTTP+MCP API and a **VP8 camera codec** (`codec: vp8|jpeg`; the README says jpeg is the default, but the sample `station.yaml` sets vp8). **Checked in the source: our `_decode` handles only JPEG and NCHW.** If the venue Station stores VP8, our camera path fails.

- **MCP server.** `mcp_server.py`: stdio JSON-RPC, stdlib only (no `mcp` package, so nothing new to audit), 10 tools mapping 1:1 to the operator commands (`look`, `locate`, `pick`, `place`, `park`, `status`, `move`, `grip`, `relax`, `say`), calling the same daemon via `arm.call`. `look`/`locate` return the PNGs inline as image content. `score`, `reset`, `nudge` are deliberately not exposed. Registered for Claude Code in `operator/.mcp.json` (relative paths).
  - **Checked:** initialize, tools/list, tool calls against the sim (park, locate with 1 image, pick red, place green-box, look with 2 images; `score` then showed red in box), a bad id returns `isError` with the daemon's message, an unknown method returns -32601. `claude mcp list` in `operator/` shows `arm` as "Pending approval" (Claude Code asks on first start).
  - **Full run through MCP (same rehearsal: seed 5, "tidy the table", blue knocked out once 2 are in):** headless `claude -p --model sonnet --mcp-config .mcp.json --strict-mcp-config`, only `mcp__arm__*` tools allowed, in `operator/` (reads AGENTS.md). **4/4 in box (`score`), 5 picks, 0 failures, 0 errors.** Disturbance at 24 s; next `locate` found blue at 15.9,-9.2, operator said "Blue got moved out of the box. Picking it again." 27 arm commands in 32 s (CLI run: 30 in 45 s); 29 model turns, 42 s, $0.25. Its report said 33 tool calls; the log has 27 (count over-claimed, result correct).
  - Not checked: Codex with MCP.

- **Rehearsal: open-ended task with a disturbance (sim seed 5, fresh Sonnet operator, live view open).**
  - Task: "Tidy the table: put every object into the box." Operator saw only `operator/AGENTS.md`. A watcher teleported a cube that was already in the box back onto the table (`nudge`, new sim-only command, not in the operator's docs) once 2 cubes were in.
  - **Result: 4/4 in box (privileged `score`), 5 picks, 0 failed grasps.** The disturbance hit at 30 s (blue out to 16,-9); the operator's next `locate` (4 s later) found blue at 15.9,-9.2, it re-picked and placed it, and reported "probably moved externally". Green (same colour as the box) was confirmed with `look top`, as the rules say.
  - 30 commands (6 park, 6 locate, 5 say, 5 pick, 5 place, 2 look, 1 status) in 45 s wall time; median 2 s between commands. Sim motion is near-instant, so the real arm will be slower; not measured.
  - `score` is no longer written to the live-view log (privileged metric).

- **Venue bring-up and live view.** Day-1 risk is hardware setup, and judges need to see what the agent sees.
  - `arm.py bringup`: guided y/n flow: status, camera snapshot check, each joint moved 10 deg and back with the model's expected tip motion in words, gripper open check, then prints the `ARM_SIGN`/`ARM_GRIP_FLIP` restart line; then ArUco calibration with a `dx dy` jog loop per marker, reprojection verdict, and a `locate` smoke test.
  - `arm.py view`: separate read-only page on ARM_PORT+1 (127.0.0.1) reading `runs/events.jsonl`, which the daemon now writes for every command (truncated at start). Shows the latest image and the command log; `say "TEXT"` lines are highlighted. `operator/AGENTS.md` asks the operator to `say` its plan before each object.
  - **Checked in sim (seed 3):** bringup with all-yes answers ran to the end (no markers in sim, so it stopped at calibration as designed); with "n" for joint 1 and gripper it printed `ARM_SIGN=-1,1,1,1,1,1 ARM_GRIP_FLIP=1`; the jog loop with stubbed markers accepted moves, rejected bad input, reported 1.2 px OK and ran `locate`. View: page, events (23 logged), PNG 200, `..%2Farm.py` 404.
  - Marks image labels: white dot on each object centre, label in a black box above it, containers labelled above their top edge, overlapping labels shifted up. Checked by eye in the live view (sim seed 3, red cube inside the green box): all 5 labels readable, none overlapping.
  - Not checked: a real ArUco detection through bringup. The view updates only between commands (the daemon is single-threaded by design).

- **ElRobot support (kinematics only, no hardware, no sim scene yet).** The event is NormaCore's, so their own 7+1 DoF ElRobot is a likely robot.
  - `elrobot/` holds the repo's URDF and meshes (MIT licence) with a MuJoCo `<compiler>` tag added (`meshdir`, `inertiafromgeom`; the URDF has zero-inertia links). It compiles: 7 arm joints + gripper (the two jaw prismatic joints are independent in MuJoCo; the `mimic` tag is ignored, irrelevant for IK).
  - `elrobot/ik.py`: damped least squares IK with the same interface as the SO-101 solver, in the robot frame (x forward, y left, z up). The URDF arm points along +y, so `x_robot = y_urdf`, `y_robot = -x_urdf`. Tool point = jaw tips, 10 cm beyond `Gripper_Base` (read from mesh bounds). Base joint is seeded toward the target azimuth, with canonical-posture fallbacks.
  - **Measured reach with the gripper pointing down:** up to x about 0.30 m at z = 1.2 cm, about 0.26 m at z = 7 cm. **No solution above z = 0.10 m**: joint 6 (wrist pitch) sits near its limit (97 of 101 deg) when pointing down. So the ElRobot profile is x 6-26 cm, y +-15 cm, travel height 9 cm (SO-101: 11 cm). Allowing a tilted gripper would extend reach; not done.
  - `arm.py` is now robot-generic: a per-robot profile (`SO101`, `ELROBOT`: joint count, workspace, heights, park pose); `serve --backend station --robot elrobot`. `StationBackend` maps each motor's calibrated range linearly onto the URDF joint limits, which is what NormaCore's own viewer does (`station-viewer/src/devices/elrobot/config.ts`, read from source). Unverified on hardware: the direction of each joint, and which gripper direction is open.
  - **Check (`elrobot/check.py`, passes):** 6 poses (positions across x 10-24 cm, y -10..+10 cm, yaws 0-85) at travel, hover and grasp heights through the real `Arm.move` code with a kinematics-only stand-in backend: 18/18 reached, tip error <= 0.3 cm, jaw yaw error 0.
  - SO-101 sim regression after the refactor: seed 3 `pick red`, `place green-box`, `score` shows red in the box.
  - **Not covered:** collisions of links with the table or other objects, real joint zero and direction, ElRobot in the tabletop sim scene (so no vision-in-the-loop run), gripper open/close direction. The `roll_offset -90` retry is a no-op-equivalent for the symmetric ElRobot jaws (it rotates the jaws 90 deg, which still grasps a cube).

- **E6: non-cube objects (sim variant `so101-sim/so101_tabletop_objects.xml`; pen, cup, eraser, tape roll; all unsaturated; `--scene objects`).** Same body names, so scoring is unchanged.
  - **Baseline (old tool, seed 3, Sonnet): 1/4.** `locate` listed only the green box and `snap` found nothing, so the agent grasped at eyeballed positions.
  - **E7 (privileged poses, seeds 3 and 7, yaw and yaw+90):** pen, cup and tape roll were lifted every time; the eraser was not (1.2 cm tall, below the grasp height). So the baseline failed on perception, not on the grasp. I made the eraser 2 cm tall (a flat object needs a grasp height that follows object height: not implemented).
  - **Fix:** `Arm._blobs` finds any object whose colour differs from the table (median Lab of the workspace, lightness weighted by half, so soft shadows do not count). `locate` lists them as `dark`/`mid`/`light` plus a number; `snap` falls back to them. Positions on seed 3 were within 0.3 cm of truth for all four. The cube scene shows no extra blobs.
  - **Rerun (fresh Sonnet operators, rules updated, privileged `score`): seed 3 3/4, seed 7 3/4, seed 11 4/4 = 10/12** (baseline 1/4). Not a like-for-like comparison with E4's 12/12 on cubes.
  - **Failure causes seen (operator reports plus score):**
    - A missed pick on a round object (cup) knocked it off the table (seed 7), and a rolled-away object cannot be recovered.
    - Seed 3: after several tries the arm was left over the box and `park` could no longer reach the pose (IK failures near y = 16); the operator ran out of commands.
    - `pick` failed with "IK did not reach" for objects that drifted near the base (x about 15-16).
    - The bar (8.7 cm) is almost as long as the box (9 cm inside), so it overhangs; `locate` still calls it inside.
    - `place` often reported `dropped_on_the_way: true` for cylinders, although they ended in the box.
  - Not done at that point: grasp height that follows object height, a pick-approach for objects near the base, a recovery path when `park` fails.
- **Rust ports compared with Python (2026-11-20; `rust/`, run `../.venv/bin/python -I rust/compare.py`).** Done at the user's request after the profile below said it would not matter. Dependency-free Rust (std only, so nothing to audit), same algorithms, identical inputs.
  - **ElRobot IK** (300 random targets, same seed, no multi-seed fallback): solved 288/300 in both, 0 disagreements, max joint difference 0.0000 deg, identical worst tip error (1.78 mm, checked with Python FK). Time per solve: **Python 2.31 ms, Rust 0.026 ms (about 90x faster)**.
  - **Segmentation** (one 640x480 sim frame, non-cube scene): mask pixels 13297 in both, 5 components in both, centroids within 0.01 px. Time per frame: **Python (OpenCV) 5.5 ms, Rust 16.0 ms (about 3x slower)**. The Python path calls optimised C++; my Rust is a straightforward per-pixel version (float Lab with `cbrt`/`powf`, naive morphology) and was not tuned.
  - **Reading:** results match, so success rates do not change. Rust wins only where Python loops in the interpreter (IK); about 2 ms per solve, roughly 15 ms per pick, against seconds of arm motion and LLM turns. Not wired into `arm.py`: the Python tool stays as is. Not measured: a Rust port of the SO-101 IK (needs its kinematics exported from the MJCF), rendering, or the Station client.
- **Python latency profile (2026-11-20, SO-101 sim, M-series Mac, median of 10-30 runs); decision on a Rust port.**
  - IK solve 0.4 ms; one camera render 11 ms; `snap` 12 ms; `locate` 46 ms (render plus segmentation); a full `pick` (6 moves) 136 ms in the sim, which has no real motion time.
  - Real-arm motion is limited by the speed caps in the code (`_go` 60 deg/s, `StationBackend.MAX_SPEED` 600 ticks/s, about 53 deg/s), so one pick plausibly takes several seconds of motion. That is an estimate from the constants, not measured on hardware. One LLM turn is seconds as well.
  - So our compute is under about 0.1 s per command. A Rust port of the same algorithm gives the same success rates by construction and would save tens of milliseconds per command. **Decision: no port.** Station itself is already Rust.
- **Codex CLI installed and checked (E5 prep, 2026-11-20).** Claude stays the main operator; Codex is a compatibility check because the track says "Codex or Claude".
  - **Audit before install:** `@openai/codex` is published by OpenAI accounts from `github.com/openai/codex`; the wrapper has no install scripts; both packages carry SLSA provenance. One published advisory (sandbox bypass), patched in 0.39.0. Not checked: Codex's source. Pinned 0.169.1 (3 days old; 0.171.0 was 12 h old) and installed with `--ignore-scripts` into `tools/codex` (local, nothing global). `npm audit signatures`: 2/2 registry signatures and 2/2 attestations verified. `codex --version`: 0.169.1.
  - **Finding:** under Codex's default sandbox (`codex sandbox -- arm.py status`) the CLI cannot reach its daemon on 127.0.0.1: `URLError: Operation not permitted`. `-c sandbox_workspace_write.network_access=true` did not change that in `codex sandbox`, which may ignore the key, so the override is untested. `codex sandbox --allow-unix-socket <path>` exists, so a Unix-socket transport for the daemon is a candidate fix. Claude Code's own sandbox may have the same restriction: not tested.
  - **Unix socket transport (`ARM_SOCK=<path>`, optional; TCP stays the default).** The daemon listens on a socket file (mode 0600) and the CLI connects to it. Same results as TCP on `status`; errors still exit 1.
    - Under `codex sandbox`: without the allow flag, `PermissionError: Operation not permitted`. With `--allow-unix-socket <path>`: `status`, `look top` and `locate` all work.
    - **Not working:** the config route (`-c 'network.allow_unix_sockets=["<path>"]'`, with or without `network.enabled=true`) was still blocked in `codex sandbox`. The key exists in the binary, but I could not make it take effect, and I cannot test an agent session without a login. So how a running Codex agent gets the permission is **unknown**. Fallbacks to check: approving the command to run outside the sandbox when Codex asks, or a Codex sandbox mode that allows it.
  - **Not done:** no model run with Codex (needs the user's own `codex login`); no E5 score.
- **Venue kit (2026-11-20).** Nothing here has touched real hardware or a camera.
  - `venue/start.sh so101|elrobot`: starts Station on 127.0.0.1 with `venue/station.yaml`, lists the camera queues, starts the arm daemon with the right profile, and stops Station on exit. Dry run with cameras off: Station starts, the ElRobot profile expects motor ids 1-8, `status` answers, `snapshot` fails cleanly, shutdown also stops Station.
  - `arm.py selfcheck [--repo DIR]`: 7 checks (python, numpy/opencv, mujoco, ArUco sheet, SO-101 sim + IK, ElRobot URDF + IK, Station protobufs). All pass here.
  - `arm.py snapshot CAM...`: raw frames, no calibration needed, to tell which queue is which camera.
  - Venue switches: `ARM_SIGN="1,-1,..."` flips a joint that moves the wrong way; `ARM_GRIP_FLIP=1` if 0 means open.
  - `venue/bin/station`: the release binary built from norma-core `6b494f7` with one local patch (the built-in camera is listed as well as external ones; the repo lists external cameras only). sha256 `146fd2d7...2e83`. `venue/VENUE.md` is the one-page checklist.
- **Fixes after E6 (2026-11-20).**
  - `Arm.move` now tries several arm postures as IK seeds (the single-seed solver dead-ended). Reach map (SO-101, gripper down): with seeds, grasp height reaches x 10-32 cm across y +-24 cm; only the hover and travel heights close to the base stay out of reach.
  - `pick` and `place` arrive at the travel height, or at a lower one (9 cm, then hover) where the arm cannot reach it. `park` no longer stays wedged: if it cannot lift straight up, it goes to the park pose anyway.
  - **Cup failure root cause:** grasping the cup at exactly its centre lifted it, but a target 0.2-0.4 cm off made the open jaw hit it and fling it 15 cm off the table. A grid of jaw yaw x offset (5 offsets, seed 7): yaw 15, 45, 60 and 75 survived all offsets; 0 and 30 failed on 2 of 5. Round blobs (fill ratio of the bounding box < 0.88) now report yaw 45.
  - Cube regression after the fixes: seed 3 `pick red`, `place green-box`: red in the box.
  - **Round 3 (after the IK fixes, before the round-object fix): seed 3 3/4, seed 7 3/4, seed 11 not valid.** I killed all daemons with `pkill` while the seed 11 operator was running, so its run ended in "Connection refused". That was my mistake. The seed 3 operator reported 4/4, but `score` showed one object not in the box: operators over-claim from images.
  - **Round 4 (all fixes): seed 3 3/4, seed 7 4/4, seed 11 4/4 = 11/12** (baseline 1/4, first rerun 10/12). Failure left: a thin 8 cm bar that drifted near the base (x about 15) was missed by repeated picks and nudged out of the box (seed 3).
  - Still open: thin-bar grasp near the base; `place` often reports `dropped_on_the_way: true` for round objects that end up in the box anyway (the check is gripper <= 5, which a cylinder held at the jaw tips can trip); objects get crowded in the box; the bar nearly spans the box. Operators still occasionally chain commands and claim success from images, so `score` or a final `locate` must back any claim.

## 2026-11-19

- **Station built and run locally (macOS arm64, no arm, localhost only).** Source pinned to norma-core `6b494f7`, built in a scratch clone with `cargo build --release -p station`.
  - **Audit before build:** `cargo audit` on `Cargo.lock` reports 29 advisories in 11 crates (`rustls`, `rustls-webpki`, `quinn-proto`, `h2`, `time`, `zstd*`, `quick-xml`, `webpki-roots`). I traced them with `cargo tree`: the ones in the station graph come through `normfs-cloud` (S3 sync client) and `zstd` storage compression. They are DoS, memory-safety and TLS-validation issues. Not proven unreachable. Mitigation: no cloud config, bind `127.0.0.1`.
  - All 358 registry crates are pinned by `Cargo.lock`; no git dependencies. `build.rs` only runs `prost-build`, `git rev-parse` and the Info.plist link flag.
  - The macOS camera code links a prebuilt `libavf.a` committed in the repo. I rebuilt it from `avf.m` (873 lines; grepped: no exec, network or dlopen) with the repo's own Makefile and used that.
  - Build prerequisites not in the README: `protoc` (brew bottle) and `cmake` (brew bottle; `turbojpeg-sys` builds libjpeg-turbo). The web UI is embedded at compile time from `station-viewer/dist`; a stub `index.html` is enough, so no `npm ci`.
  - Result: Station starts; `--tcp 127.0.0.1:8888` binds localhost only (the listener starts only when `--tcp` is given). Queue files live under `station_data/<instance_id>/`.
  - **Our Python client path works against the real Station:** handshake 0.1 s, `enqueue("commands", ...)` accepted, `follow("inference/normvla")` accepted. Bare queue names (`commands`, `st3215/inference`) are right; the server adds the instance-id prefix.
  - **Camera path verified with the Mac webcam:**
    - macOS permission: a process started from the app's shell was denied at once. Started from the Terminal panel, the prompt appeared and Allow worked.
    - Station's macOS driver lists only **external** cameras (`AVCaptureDeviceTypeExternal` in `avf.m`), so the built-in FaceTime camera is invisible. For this test only, I added `AVCaptureDeviceTypeBuiltInWideAngleCamera` to the scratch clone and rebuilt. At the venue the cameras are USB, so no patch.
    - Queue `usbvideo/5ad8c326...` equals `md5(unique_id)`, as in the design. Station recorded it at 640x480 (`resolution: auto` picked that; `resize_target: 480` set the short side).
    - `arm.py serve --backend station --cam top=usbvideo/<md5>`: `calibrate top` ran on a real frame (no markers in view, none found). The saved frame decodes to a normal image, mean brightness 134 of 255.
    - **Black frames, cause not established:** some frames were uniformly black (mean 15.9, the black level of limited-range video), others normal (mean 134). I first guessed auto-exposure warm-up; later checks did not support that. The user then closed the camera for privacy, which fits black frames. Not resolved; I stopped testing with the camera open at the user's request.
    - `StationBackend.image` now waits up to 6 s for a non-black frame (mean > 40) and then returns whatever it has. Handler errors in the follow tasks are now logged instead of vanishing.
    - Frame deleted after viewing (it showed the user).
  - Still untested: ArUco detection on a printed sheet, calibration with the arm, motors, `inference/normvla` with joints.
- **`StationBackend` camera-only mode:** with fewer than 6 motors on `st3215/inference` it now warns and disables motion (`drive` raises) instead of refusing to start. Tested against the running Station: `arm.py serve --backend station` starts, `status` answers, `park` returns "no arm ... cannot move".
- **Midway evaluation.** The deterministic harness plus the LLM as supervisor works in sim.
  - E1: 4/4. E2 (vision-only, old tool): 3/4. E4 (current tool and rules): **12/12 cubes, 3/3 seeds**.
  - Each fix was targeted at a measured failure, not a guess.
  - Open questions for the real arm: joint signs, calibration accuracy, and real grasp offsets. All three are untested until Saturday.
- **`locate` ids are now colour names** (`red`, `blue2`, `green-box`). E4 operators reported that numeric ids shifted between calls. Regression test on seed 3: `pick red`, then `place green-box`, then `score` shows red in the box.
- **E4 result (Sonnet operators following `operator/AGENTS.md`, seeds 3/7/11): 12/12 cubes in the box, 3/3 seeds** (all confirmed by `score`).
  - Seeds 3 and 11: 8 physical commands each, no retries.
  - Seed 7 needed 2 more picks:
    - Red fell out on the way. `dropped_on_the_way: true` flagged it, and the operator recovered.
    - Green was placed half on the box edge, and a no-op pick nudged it in.
  - Remaining issues:
    - A green cube in a green box is invisible to `locate`. The operators used `look top`.
    - 2 of the 3 operators sent `place` and `park` in parallel once; the commands still ran in order.

- **Track switch.** Moved from the Hugging Face Desk Hero track to NormaCore: "let Claude or Codex control the robot through the Station API by looking through the cameras". `PLAN.md` (LeRobot policy training) is obsolete; `DESIGN.md` replaces it.
- **Station API read from source** (norma-core `main` @ `6b494f7`, 2026-11-10; read-only clone, nothing run). Verified the command path, state queue, camera queues and combined `inference/normvla` queue. Details in `DESIGN.md` §2.
- **`arm.py` v0 (sim backend).** Daemon + CLI, stdlib HTTP, no new dependencies. Commands: look (metric grid overlay), locate (colour blobs to table cm), pick, place, move, grip, park, joints, status, reset.
  - Grid overlay projects correctly on the top and front sim cameras (checked by eye on seed 3).
  - `locate` on seed 3: all visible cubes found as 3.1 cm squares (true size 3.0 cm); box found as 10.6 x 10.1 cm (true 9.8 cm outer).
  - First pick-and-place via `locate` -> `pick` -> `place` put the blue cube in the box on the first try.
  - Found: the default home pose hides cubes from the top camera. Added `park` (arm upright and back), which clears the view.
- **Station backend written** (`StationBackend` in `arm.py`): it follows `st3215/inference` and the camera queues, and drives the arm with sync_write goal speed and goal position, clamped to calibrated ranges. It also has `relax` (torque off) and ArUco calibration (`calibrate`, `calib-mark`). `aruco_markers_A4.png` is ready to print; the detector found all 4 ids on it.
  - Offline check: the command encodes and decodes through the generated gremlin code (`sync_write` 0x2A, 6 motors, 63-byte pack).
  - **Not run against a real Station yet.**
- **Research** (subagent; sources listed in `DESIGN.md` §1/§4): the harness matters more than the model; Set-of-Mark beats raw coordinates; CLI is fine for about 10 tools; CLI-Anything is not useful here. NormaCore has no existing LLM/MCP integration. The same track ran in June 2026, and one participant reported they "still could not pick up a cube".
- **E3 grasp primitive (scripted, privileged poses, red cube, seeds 0–9):**
  - roll 0: 9/10
  - roll −90: 10/10
  - roll +90: 8/10
  - Seed 7 fails at roll 0 and works at −90.
  - The differences are noise-level (N=10), but they explain the repeat red failures in E1 and E2.
  - Decision: when `pick` misses, it re-snaps and retries once with the jaws rotated 90°. Verified on seed 7 red: `holding` true on the retry.
- **E2 result (vision-only, Sonnet): 3/4 cubes in the box** (confirmed by `score`).
  - Position estimates read from the grid vs ground truth: red 0.3, green 0.4, yellow 0.45, blue 0.36 cm. Box 1.0 cm.
  - Yaw estimates were wrong; red was read as 20°, but it is 70°.
  - Failures:
    - The agent chained 3 picks without checking `holding`.
    - Missed picks pushed cubes away.
    - Placing at the box edge knocked a cube out.
    - The pick approach cannot reach y ≥ 20 cm.
  - Decision: geometry (centre, yaw) comes from pixels, so `pick X Y` now snaps to the nearest object's centre and yaw. Operator rules live in `operator/AGENTS.md`: one physical command per step, re-locate before every pick, no edge placements.
- **E1 result (CV-assisted, Sonnet): 4/4 cubes in the box** (confirmed by `score`), in 5 pick attempts.
  - The red cube took 2 attempts: `holding` was true, but the cube stayed behind.
  - Picks nudged neighbours by 1–3 cm.
  - The green cube merged with the green box in `locate`.
  - Decision:
    - `locate` now returns numbered marks plus a marks image (Set-of-Mark), `kind` (object or container) and `inside`.
    - `pick ID` and `place ID` added.
    - `place` reports `dropped_on_the_way`.
    - `pick` arrives from a travel height of 11 cm (gripper-down IK fails above about 12 cm near x = 20).
  - Close-neighbour nudging remains. It comes from the open jaw's swing; the agent re-locates instead.
- **Experiment E1/E2 started**: two fresh Sonnet operators, seed 7, all 4 cubes into the box. E1 may use `locate`; E2 must read positions off the grid image only. Ground truth for seed 7 saved in `outputs_truth_seed7.log`.

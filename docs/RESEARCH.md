# Research digest

Collected by research subagents (web, papers, public code). Figures marked (unverified) come from search snippets. Sources for field reports are kept outside this repo.

## What fails elsewhere, and why
| Problem | Evidence | Our status |
|---|---|---|
| VLM raw coordinates are imprecise | A strong VLM with a perfect plan scored 33% using its raw coordinates (unverified); field reports: "pure LLM vision too imprecise for grasping", and an LLM nudge loop (~2 s per decision) could not grasp | Covered: the LLM picks numbered marks; geometry comes from code |
| Keypoint-VLM methods on real arms | MOKA 45%, ReKep 34%, CoPa 45% (arXiv 2504.12636, 2505.00693) | Not used |
| Calibration and kinematics offsets | SO-101 FK 25-50 mm off until fitted from touch points | `calib-mark` uses the tip itself as reference; not measured on hardware |
| Setup problems, not code | 37 of 66 public incident reports were power, wiring or calibration | `bringup`; follower 12 V, leader 5 V, never swapped |
| Servo slop | STS3215 backlash 0.87 deg, dead zone 10 counts | Retry with rotated jaws; `snap` re-centres |
| Thin and flat objects | Parallel jaws have a thinness limit; tricks: push to an edge, pin and sweep | Pencil 0/10 in sim; `push`; operator skips after 2 failures |
| LLM over-claims success | Not quantified anywhere; seen in our own runs | Verify with `locate`, `score` in sim |

## Alternatives
- **Learned policies (ACT, SmolVLA, pi0.5) on SO-101:** ACT 37/40 with 100 demos, 1/40 with 12 (arXiv 2610.37170); SmolVLA about 50%. A field report: 47 demos, a cloud GPU, an 8-joint patch, and the grasp was still inconsistent. One policy per task, and the task is unknown in advance. **Not for us**, except as a day-2 stretch.
- **Grasp networks** (GG-CNN, Contact-GraspNet, AnyGrasp): 83-93%, but they need depth and a GPU.
- **Open-vocabulary detectors:** seconds per image on CPU; empty-table subtraction is faster and steadier.

## Field learnings from earlier editions (anonymised)
Approaches seen: most teams mapped a chosen pixel to **hand-taught joint poses** (3x3 grid, 5x3 board, or nearest taught references) instead of IK and calibration; the LLM ran as a slow supervisor (~0.3 Hz) behind an MCP server with all safety in the server; most added voice and a few gestures. None reported measured success rates or recovery.

### ST3215 / Station hardware facts
| Fact | Value | In our code |
|---|---|---|
| Two motor buses (leader + follower) are on Station together | Must pin the follower bus by serial; "auto" is ambiguous and freezes things | `ARM_BUS`; motion refused while ambiguous |
| Uncalibrated motor | `range_min == range_max` (often 0/0) | Motion refused with a clear message |
| ElRobot calibrated ranges | Centred on tick 2048; spans differ from URDF limits (joint 2 ~152 vs 185 deg, joint 3 ~111 vs 202 deg) | Degrees around the range centre (4096 ticks = 360 deg); the linear range-to-URDF mapping is behind `ARM_ELROBOT_MAP=linear`. Joint directions still unverified |
| Overload fault | Status register 0x41 bit 0x20; Station then skips commands with `motor_error`; clears itself once unloaded | Detected during moves: hold position and report |
| Cause of overload | Shoulder (joint 2) gravity torque when raising while extended | Lift in short steps; error text says "lift less extended / tuck the elbow" |
| Station current threshold | 200 (lower values trip overload on lifts) | `venue/station.yaml` |
| Servo profile | Comes out of calibration at speed 0 = maximum. Field values: speed 250 + accel 10 to start, ~600/20 once trusted | `ARM_MAX_SPEED` (default 250), `ARM_ACCEL` (default 10, written at connect) |
| Register order | Speed (0x2E) and accel (0x29) before goal (0x2A) | Speed then goal per move; accel at connect |
| Present position | 0x38, sign-magnitude (bit 0x8000) | Same |
| Gripper | ElRobot motor 8 = jaws, motor 7 = wrist rotation. Squeezing on a held object stalls the servo | Close in steps; stop when the jaws stop moving (= gripped) |
| Grasp settle | Wait until all joints are within ~12 ticks for 2 reads before opening | Stall/overload detection; settle check not added |
| Joint state source | Read from `st3215/inference`, never from the fused camera queue (it freezes with the cameras) | Same |
| Station flags | Started without `--tcp` serves nothing on 8888 | In VENUE.md |
| Cameras | Two USB cameras on one controller run out of bandwidth and freeze; queue names change on every replug; identify the wrist camera by seeing the jaws | Watchdog: a feed older than 3 s raises a clear error. Use a powered hub or separate ports |
| Typical frames | 224 px JPEG at ~10 fps from Station's fused queue | We use per-camera queues at 480 px |

### Motion and grasp practice
- Never raise the shoulder while the arm is extended: tuck elbow and wrist first, then lift, then go home.
- Move in small steps with pauses when loaded (field values: 0.03 of a joint's range per step, 0.5 s apart); detect a stall as "error improved by less than ~0.4% of range for 4 checks", then relieve load (retract the elbow) and retry.
- Gripper: open ~0.78 of range to approach, ~0.95 to release; a closed jaw stalls at ~0.22-0.34 on a typical object.
- Taught references beat a closed-loop VLM servo: blend the nearest 3 taught (pixel, ticks) pairs with weights 1/(d^2+1); record every square rather than interpolate sparse ones (interpolated squares were nearly constant and unsafe).
- Visual servo as last resort only: coordinate descent one joint at a time, gain 0.10 of range, step cap 0.07, settle 0.5 s, stop within 7.5% of the image or after 34 iterations; flip a joint's sign when the error grows.

### Demo practice
- A live demo under a minute, one clear behaviour, a recorded backup, honesty about what is scripted.
- Bundle a whole motion into one tool call so the LLM supervises once per task, not per step.
- A slow, reliable scripted pick is the fallback demo.

## The plan we follow
Thesis unchanged: the LLM chooses and verifies; code measures and moves. An object dataset only pays off with a learner, which we don't train. Instead: empty-table subtraction, shape classes, push for flat objects, stall-based grasp detection, the taught grid as fallback, and `bringup` plus measured calibration error before any demo.

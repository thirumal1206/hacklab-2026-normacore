# Research digest (2026-11-20)

Sources were collected by research subagents. Figures marked (unverified) come from search snippets.

## What failed elsewhere, and why
| Problem | Evidence | Our status |
|---|---|---|
| VLM raw coordinates are imprecise | GPT-4o with a perfect plan scored 33% using its raw coordinates (unverified). June team 4: "pure Claude Vision too imprecise for grasping". | Covered: the LLM picks numbered marks; pixels come from code |
| Keypoint-VLM methods on real arms | MOKA 45%, ReKep 34%, CoPa 45% (arXiv 2504.12636, 2505.00693) | Not using them |
| Calibration and kinematics offsets | SO-101 FK 25-50 mm off until fitted from touch points (miilv/controlr PR 16) | Covered: `calib-mark` uses the tip itself as reference. Accuracy never measured on hardware |
| Setup problems, not code | 37 of 66 public incident reports were power, wiring or calibration (bluvtx09/bringup-check) | `bringup` command; 12 V follower / 5 V leader, never swapped |
| Servo slop | STS3215 backlash 0.87 deg, dead zone 10 counts (robonine.com) | Retry with rotated jaws; `snap` re-centres |
| Gripper stall and overcurrent | Squeezes stall in about 53% of episodes; the pizza kit sets current limits of 100 mA (gripper 40 mA) | Our holding check uses gripper position only. Low limits may make the arm weak |
| Thin and flat objects | The parallel-jaw thinness limit is known. Tricks: push to the table edge, pin and sweep | Pencil 0/10 in sim; operator skips after 2 failures |
| LLM over-claims success | Not quantified anywhere; we measured it ourselves | Covered: verify with `locate` and `score` |

## Alternatives that worked
- **ACT on SO-101:** 37/40 with 100 demos, 1/40 with 12 demos (arXiv 2610.37170). pi0.5 reached about 70% and SmolVLA about 50%; SmolVLA never moved in 47% of episodes.
- **Why we can't use it:** it needs about 100 demos per task, recorded with teleop on the real arm, plus a CUDA GPU. It is also one policy per task, and the task is unknown until Saturday.
- **June winner:** Claude as a slow supervisor over SmolVLA. Their README says the LLM-driven pick and place was reliable and the learned policy was inconsistent.
- **Grasp networks** (GG-CNN, Contact-GraspNet, AnyGrasp): 83-93%, but they need depth input and a GPU. We have neither.
- **Open-vocabulary detectors:** Grounding DINO takes seconds per image on CPU. OWLv2 and YOLO-World have no Mac numbers. The research found classical background subtraction against an empty-table photo is faster and sturdier.

## The pizza repo (FunRobots/robot-pizza-cup)
- It's an event starter kit with no results, built on teleop, then parquet, then SmolVLA. It has no LLM control.
- **Reusable:** pin Station to v0.1.0-beta.10 (release binaries for macOS arm64 and Linux arm64/Pi), the `usb-video` config (`formats: []` auto-picks; a bad entry disables the camera driver), and the calibration flow (open the calibration page, move every joint by hand, Auto Calibrate the follower only, Save). Also: on Linux, add the user to `dialout`.
- **Trap:** its `train.py` and `run_policy.py` hardcode 6 motors, while ElRobot has 8.

## The narrowed plan
**Thesis unchanged:** Claude chooses and verifies; code measures and moves. Every source agrees on this split, including the June winner and June team 4.

An object "dataset" only pays off with a learner, and we have no learner. What we prepare instead:
1. **Empty-table reference photo** for segmentation (`look`/`locate` diff against it). This is the robust version of what we already do, and it covers random objects of any colour.
2. **Object-class grasp table** (small, in code): cube/box, long thin (pen), round (cup/ball), flat (card/pencil), soft/food. Each class gets a grasp height, a jaw-angle rule, and a fallback. Each is measured in sim scenes.
3. **Push primitive** for flat objects: slide the object toward the box or the table edge with closed jaws.
4. **Holding check from gripper current or stall** on the real arm, besides position.
5. **At the venue:** use the Station version they run with a matching checkout, then `bringup`, then measure calibration error in mm before any demo.

Don't do: train ACT or SmolVLA tonight, use grasp networks, give the LLM raw pixel coordinates, or download large models.

## June winner, Team 5 "NORMA" (LinkedIn post the user found)
- **Taught 3x3 grasp grid** (`grid_grasp.json`): joint poses recorded by hand at grid points on a sheet, bilinear interpolation between them, then a scripted home -> approach -> grip -> lift -> carry -> drop -> home. **No kinematic model or camera calibration needed.** This is our fallback if IK or calibration is off on the real arm.
- **Claude through a FastMCP server** on Station: observe, move_joints, set_vla_goal, save_skill. All safety lives in the server: joint targets clamped, step size limited, uncalibrated joints rejected, atomic sync-write. That's the same split as ours.
- **ST3215 overload faults during lifts.** Their fixes: read joints from `st3215/inference`, use small incremental moves with pauses, and tuck the elbow before lifting.
- Voice and phone UI (Whisper). Judges liked the breadth.

## All 5 June NormaCore teams (LinkedIn spotlight posts the user found)
| Team | Pixel -> motion | AI | Extras |
|---|---|---|---|
| 1 "Joe" | 5x3 board of taught joint targets, IDW for unrecorded squares | FastMCP for Claude Code/Cursor | Voice (Whisper+OpenAI), gestures (say_hi, dance), Station Viewer fork with camera/calibration overlays |
| 2 | Claude picks a pixel -> interpolated taught pose | Claude | Voice (Web Speech, ElevenLabs), HF model + dataset |
| 3 | Gemini ER points -> taught poses; left/center/right buckets; visual-servo fallback (pixel error -> joint nudge) | Gemini ER + Live | Click-image-point calibration, voice/video |
| 4 "Waffle" | SmolVLA trained on demos (Nebius GPU), 8-joint patch | Supervisor planned, not finished | Multi-stage tasks |
| 5 NORMA (winner) | 3x3 taught grid + scripted pick; SmolVLA | Claude via FastMCP, safety in the server | Voice, phone UI, overload-safe moves, tooling |

- **Baseline to beat:** 4 of 5 teams mapped pixels to taught joint poses (no IK, no calibration). 4 of 5 added voice. All of the Claude teams used an MCP server.
- **What we have that they lacked:** a real kinematic model plus calibration (the taught grid is our fallback), arbitrary-object segmentation with marks, grasp verification with retry, recovery from disturbances, a live view of the agent's reasoning, and measured success rates.
- **What we lack that the judges saw everywhere:** voice control and robot personality (gestures).

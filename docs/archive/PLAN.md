# HackLab 2026 – Desk Hero plan

Goal: a live, reliable SO-101 demo that solves one everyday desk problem with a policy trained by LeRobot, plus a clear story. Judges see one live run, not a success rate.

Written for execution by Sonnet/Haiku. Each task has an owner tier, exact steps, a done check and stop conditions. When a stop condition hits, stop and report; do not improvise around it.

## Ground rules (apply to every task)

- Project root: `~/Documents/Personal/projects/HackLab_2026`. Python: `.venv/bin/python`, CLIs in `.venv/bin/`. LeRobot 0.7.1, source in `lerobot/`.
- Before installing or downloading anything new: lock with `uv pip compile`, run `pip-audit` on the lock, show the result, install only with `-c <lock>`. Models: safetensors only, pinned revision, no `trust_remote_code`.
- Never push a dataset/model, start a paid job, commit or post without Thirumal's explicit OK for that action. Never handle tokens in chat; Thirumal runs `hf auth login` himself.
- Local runs: `export HF_HUB_OFFLINE=1`. Long jobs: wrap in `caffeinate -dims`, run in the background, log to `outputs_*.log`, run `./watch_mem.sh` alongside training.
- Never guess a CLI flag. Check `<cli> --help` or the source in `lerobot/src/lerobot/` first.
- Report numbers with the seed range and N. Treat differences under 4/20 as noise.

## Decisions Thirumal must make (block the tasks marked with them)

- D1 Desk task: DECIDED. "Put the pen in the cup." Task string for recording and eval: `Put the pen in the cup.` Use a thick marker, not a thin pen, for the first attempts (easier grasp).
- D2 Hardware before the event: do we own an SO-101 leader+follower pair now, or only at the venue?
- D3 GPU for training: Hugging Face Jobs (paid, needs Hub push), a rented GPU box, or venue-provided.

## Phase 0 – Close out sim (Sonnet, low effort, ~30 min)

Last sim experiment. Everything else in sim is frozen.

T0.1 Temporal ensembling eval on the existing 200-demo ACT model.
1. Add one flag to `eval_policy.py`: `--ensemble=C` sets `policy.config.temporal_ensemble_coeff = C` and `policy.config.n_action_steps = 1`, then re-creates the ensembler. Check in `lerobot/src/lerobot/policies/act/modeling_act.py` how the ensembler is built in `__init__`/`reset` and rebuild it the same way; do not assume setting the config is enough.
2. Run: `HF_HUB_OFFLINE=1 caffeinate -dims .venv/bin/python eval_policy.py outputs/act_so101_red200/checkpoints/030000/pretrained_model 20 --cube=red --ensemble=0.01 > outputs_eval_red200_ens.log`
- Done check: log ends with `full success: X/20`. Report X against 6/20 (no ensembling).
- Stop if: an exception about `n_action_steps`/ensembler, or inference > 1 s per step (ensembling runs the model every step; that is slow on MPS and would also be slow on the real arm).
- Keep the result as a lesson for the real arm only. Do not tune further.

## Phase 1 – Real-arm runbook (Sonnet, medium effort; needs D2)

T1.1 Write `RUNBOOK.md`: the exact commands for the venue, in order, each verified against `--help`:
1. `lerobot-find-port` for leader and follower.
2. `lerobot-setup-motors` (only if the arm arrives unassembled/unconfigured).
3. `lerobot-calibrate` for follower (`so101_follower`) and leader (`so101_leader`), with ids `desk_follower`/`desk_leader`.
4. `lerobot-find-cameras` and pick two: a fixed front/overhead camera and the wrist camera. Same names as the sim (`front`, `wrist`) so `eval_policy.py` logic transfers.
5. `lerobot-teleoperate` with cameras shown, for a 5-minute warm-up.
6. `lerobot-record`: `--dataset.single_task` = D1 sentence, `--dataset.num_episodes=60`, `--dataset.episode_time_s=20`, `--dataset.reset_time_s=10`, `--dataset.push_to_hub=false`, local `--dataset.root`.
7. Training command (Phase 2) and the rollout command `lerobot-rollout --robot.type=so101_follower --policy.path=...`.
8. `lerobot-rollout --strategy.type=dagger` with the leader arm: the human takes over when the policy misses and the corrections get recorded. Read `lerobot/src/lerobot/` for its exact flags. This directly targets our measured failure (1–3 cm misses, no retry).
- Done check: every command in the file passes `--help` parsing with its flags (run each with `--help` appended or a dry config check). If we own the hardware (D2), run steps 1–5 for real.
- Stop if: a flag in the docs does not exist in 0.7.1.

T1.2 Recording protocol (put it in `RUNBOOK.md`, no code):
- Object start positions: 3 marked spots, 20 episodes each, small random rotation. Target (cup) fixed and taped down.
- Same lighting, camera mounts fixed with tape, no people moving in the front camera view.
- 10% of episodes: deliberately approach slightly off, then correct (real closed-loop recovery, which the scripted sim expert never showed).
- Discard failed episodes immediately (re-record key during recording).

## Phase 2 – Fast training path (Sonnet, medium effort; needs D3)

MPS took hours for 30k ACT steps. At the venue we have about a day.

T2.1 Measure GPU throughput on the existing sim dataset `datasets/so101_sim_red200` (stand-in for the real one):
- If D3 = HF Jobs or a rented box: Thirumal OKs the push (private) and the cost first. Then train ACT 20k steps, batch 32, and record the wall time and cost.
- Done check: a checkpoint downloaded locally that runs through `eval_policy.py ... 20 --cube=red` and scores no worse than the MPS model (≥ 4/20).
- Write the measured time and the exact command into `RUNBOOK.md`.

T2.2 Fallback if no GPU: ACT on MPS, 20k steps, batch 8, started overnight on day 1. Write the expected time into `RUNBOOK.md` from `outputs_act_red200_train.log`.

## Phase 3 – Demo safety net (Sonnet, low effort)

T3.1 Retry wrapper for the live run: after the policy closes the gripper, if the gripper position says "closed on nothing" (fully closed) or the object is not lifted, reset `policy.reset()` and rerun from a hover pose, max 2 retries. Implement as a small script around `lerobot-rollout` or our own loop; no new dependencies. Test it in sim first with the ACT red200 model: done check = success count with retry ≥ success count without on seeds 0–19.

T3.2 Backup video: record 3 good real runs on phone + `act_policy_red200.gif` from sim, in case the live run fails.

## Phase 4 – Story and pitch (Thirumal + Opus, ~1 h)

- One-line problem, why a robot, live run, what we learned (data quality beats model size; recovery demos; DAgger).
- Optional polish only if Phases 1–3 are done: a voice/text front-end choosing which object to fetch (SmolVLA language conditioning or a small LLM selecting the task string).

## Venue timeline (2 days)

| When | What |
|---|---|
| Day 1, first 2 h | Assemble/calibrate, cameras, teleop warm-up (RUNBOOK 1–5) |
| Day 1, next 3 h | Record 60 episodes (both of us: one teleops, one resets/discards) |
| Day 1, evening | Train (GPU ~1–2 h, or MPS overnight) |
| Day 2, morning | First rollout, 10 trials, note failures; DAgger 20 corrections; retrain |
| Day 2, midday | Retry wrapper on, rehearse demo 5×, record backup video |
| Day 2, afternoon | Pitch |

## Out of scope (do not do)

More sim tuning (noise demos, SmolVLA on MPS, chunk sizes), CLI-Anything, new policies beyond ACT unless ACT fails on real data.

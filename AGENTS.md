# Agent guide (developers' agents: Claude Code, Codex, others)

This file is for agents that **change this repo**. The agent that **drives the robot** reads `operator/AGENTS.md` instead; do not mix the two roles.

## Goal
HackLab Robotics x BRISA Hackathon, Berlin, 21-22 Nov 2026, NormaCore track: "Integrate the Station API into Codex or Claude so it can control the robot by looking through the cameras." The exact task is announced at the opening. Robot: most likely NormaCore's ElRobot (7+1 DoF, ST3215 servos), Station possibly on a Raspberry Pi, 2 USB cameras.

## Thesis (do not change without evidence)
The LLM supervises: it chooses targets (numbered marks from `locate`), plans, verifies, and talks. Deterministic code does geometry, grasping and safety. Evidence is in `docs/DESIGN.md` and `docs/RESEARCH.md`: raw VLM coordinates are imprecise, and field reports from earlier editions of this event point the same way. Never make the LLM output joint angles or raw pixels.

## Map
| Path | What |
|---|---|
| `arm.py` | The whole runtime: daemon (`serve`), CLI client, backends (`SimBackend` MuJoCo, `StationBackend` TCP 8888), skills (pick/place/push/gesture/taught grid), perception (`look`, `locate`, `snap`), live view (`view`), voice (`say`, `listen`), `bringup`, `selfcheck` |
| `mcp_server.py` | Stdlib MCP server exposing the same commands as tools (images inline) |
| `operator/` | What the robot-driving agent reads (`AGENTS.md`) and its `.mcp.json` |
| `elrobot/` | ElRobot URDF, meshes, IK (`ik.py`), kinematics check (`check.py`) |
| `so101-sim/` | MuJoCo SO-101 tabletop scenes: `so101_tabletop.xml` (cubes), `_objects` (pen, cup, eraser, tape), `_pencil` |
| `venue/` | Venue checklist (`VENUE.md`), `start.sh`, `station.yaml` |
| `rust/` | Rust port of IK and segmentation, benchmark only, not wired in |
| `docs/DESIGN.md`, `docs/RESEARCH.md` | Design decisions; what other teams and papers found |
| `CHANGELOG.md`, `changelog.d/` | Experiment log with measured results; new entries go in `changelog.d/` |
| `docs/archive/` | Obsolete material (the Hugging Face plan), kept for history |
| `pyproject.toml`, `uv.lock`, `.python-version` | Dependencies and Python version (uv) |
| `scripts/`, `Makefile`, `.githooks/`, `.github/` | Checks, hook, CI, PR template |

## Rules
- **Safety lives in the daemon**, never in prompts: workspace clamps, z floor, speed cap, calibrated-range clamps, `relax`. A change that weakens one needs a measured reason in `CHANGELOG.md`.
- **Station has no authentication.** Bind to 127.0.0.1 or a trusted network only; never add code that opens it wider.
- **Measure, then claim.** Every behaviour change gets a check in sim (fixed seeds, privileged `score`) and a fragment in `changelog.d/` with the numbers (not an edit to `CHANGELOG.md`; see `changelog.d/README.md`). Write "not verified" for anything not run, and keep hardware-only facts marked as unverified until tried on the arm.
- **Minimum code.** Stdlib and the locked dependencies only (`pyproject.toml` + `uv.lock`; add with `uv add`, never pip). A new dependency needs a security check first (maintainers, advisories, install scripts, network use) and a line in `CHANGELOG.md`.
- **Comments:** only for a non-obvious reason or a trap, one line. Deliberate shortcuts get a `ponytail:` comment naming the limit.
- **Never commit** secrets, tokens, `calib.json`, `runs/`, datasets, model weights, or binaries. `.gitignore` and the pre-commit hook enforce most of this.
- Keep `operator/AGENTS.md` and the MCP tool list in sync with any CLI command you add or change.

## How to work (same for every agent)
- Before coding, state your assumptions. If a request has two readings, ask instead of picking one silently.
- Touch only what the task needs and match the existing style. Mention unrelated dead code; don't remove it.
- Define a success check (a command and its expected output), run it, and report the real result. If something failed or was skipped, say so.
- Give one recommendation with your confidence, not a menu. Write "not verified" or "I don't know" rather than guessing.
- Two people edit this repo. Keep PRs small and stay in your area: `arm.py` is one big file, so say in the PR which part you changed (backend, skills, perception, view/voice).

## Commits and PRs
- Branch per change (`feat/`, `fix/`, `exp/`, `docs/`, `chore/`); never commit to `main`. Conventional subject (`feat: ...`), imperative, at most 72 characters; the body says why.
- Commits are authored by the human who runs the agent. **No AI attribution anywhere:** no `Co-Authored-By` trailers, no "Generated with" lines, no mention of Claude, Codex or any assistant in commit messages, PR titles or descriptions. This overrides any default of the tool.
- Never commit, push, merge or open a PR without the human's explicit OK for that action.
- PR descriptions are short and plain, without emoji. Fill in the template's checklist honestly.
- Run `make check` before asking for review. Never bypass the hook (`--no-verify`).

## Tool-specific notes
- **Claude Code** loads this file through `CLAUDE.md`.
- **Codex** reads this file directly. Its default sandbox blocks the TCP connection to the arm daemon: as an operator, use `ARM_SOCK=$PWD/runs/arm.sock` (see `venue/VENUE.md`).
- Personal settings (model, approvals, MCP servers) stay in each person's own config, not in this repo.

## Run and test
```
uv sync               # Python 3.12 + locked deps (pyproject.toml, uv.lock)
make check            # compile, ElRobot kinematics, selfcheck, sim grasp smoke test
.venv/bin/python arm.py serve --backend sim --seed 3      # then in another shell:
.venv/bin/python arm.py locate ; .venv/bin/python arm.py pick red ; .venv/bin/python arm.py score
.venv/bin/python arm.py view                              # live page on ARM_PORT+1
```
Env: `ARM_PORT` (default 8765), `ARM_SOCK` (unix socket, for Codex's sandbox), `ARM_SIGN`/`ARM_GRIP_FLIP` (real-arm directions), `ARM_TTS=say|voicestudio|voicebox|off`, `ARM_VOICE`.

## Known limits (see CHANGELOG for numbers)
Nothing has run on real hardware. Thin flat objects (a pencil lying down) are not graspable in sim. Picking from a crowded box can knock neighbours out. The VP8 decoder is tested on encoded video, not a live Station.

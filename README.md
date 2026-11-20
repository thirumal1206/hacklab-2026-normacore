# HackLab 2026: NormaCore track

Claude Code or Codex controls a robot arm by looking through its cameras, using NormaCore's Station API.

The model chooses and verifies; code measures and moves. A daemon (`arm.py serve`) holds the robot (or a MuJoCo simulation) and enforces every safety limit. The model calls a small CLI or the MCP tools: `locate` numbers the objects it sees, `pick`/`place` grasp them, and `locate`/`look` confirm the result. A voice assistant, Friday, sits on top.

**New here? Open [`docs/field-guide.html`](docs/field-guide.html) in a browser first:** architecture, sequence diagrams, the pick pipeline, safety layers, results and what we tried.

## 1. One-time setup (macOS)
You need [Homebrew](https://brew.sh). Then, in a terminal:

```bash
brew install uv gh
gh auth login                                  # your own GitHub account
gh repo clone thirumal1206/hacklab-2026-normacore
cd hacklab-2026-normacore
uv sync                                        # installs Python 3.12 and the pinned packages into .venv
make hooks                                     # pre-commit checks on every commit
make check                                     # must end with: ALL CHECKS PASSED
```

For the robot operator you also need **Claude Code** (`claude`) or **Codex**, each logged in with your own account.

## 2. Run the simulation
Open three terminal tabs, all in the repo folder.

**Tab 1: the robot** (a simulated SO-101 arm, four cubes and a box):
```bash
.venv/bin/python arm.py serve --backend sim --seed 3
```
It prints `arm daemon on 127.0.0.1:8765` and keeps running. `--seed` changes where the objects start. `--scene objects` uses a pen, cup, eraser and tape roll instead of cubes; `--scene pencil` adds a thin pencil.

**Tab 2: the live view** (what the robot sees and does):
```bash
.venv/bin/python arm.py view
```
Open http://127.0.0.1:8766 in Chrome. It stays empty until the first command runs.

**Tab 3: drive it yourself** to see how it works:
```bash
.venv/bin/python arm.py locate          # parks the arm, lists objects; the marks image appears in the live view
.venv/bin/python arm.py pick red        # picks the object with id "red"
.venv/bin/python arm.py place green-box # drops it in a free corner of the box
.venv/bin/python arm.py score           # simulation only: which cubes are really in the box
.venv/bin/python arm.py reset 5         # new layout (seed 5)
```
`.venv/bin/python arm.py --help` lists every command.

## 3. Let the model drive
**Claude Code** (recommended: Haiku is fast and cheap):
```bash
cd operator
claude --model haiku
```
Approve the `arm` MCP server when asked, then type: *Tidy the table: put every object into the box.* It reads `operator/AGENTS.md` and works through the MCP tools. Watch the live view. Check the result yourself with `arm.py score`: the model's own report can be wrong.

**Codex:** its sandbox blocks the daemon's network port. Start the daemon with a socket instead, and run Codex from `operator/` with the same variable:
```bash
ARM_SOCK=$PWD/runs/arm.sock .venv/bin/python arm.py serve --backend sim --seed 3
```
How Codex is given permission for the socket is not verified yet; see `venue/VENUE.md`.

## 4. Talk to Friday (voice assistant)
1. Start the daemon with a voice: `ARM_TTS=say` uses the built-in macOS voice. `ARM_TTS=voicestudio` uses the better voice if VoiceStudio runs locally on port 3900. `ARM_TTS=off` is silent.
2. In `operator/`, start `claude --model haiku` and type: *Run in assistant mode.*
3. In the live view, click **Hands-free** once and allow the microphone. Then speak: "Friday, put the red cube in the box." Only sentences with "Friday" reach her. "Friday, stop" freezes the arm. You can also type into the box and press Enter.

Use headphones: otherwise the microphone can hear her voice. Chrome sends microphone audio to Google for speech recognition.

## 5. Daily workflow
```bash
git switch main && git pull && uv sync         # start of the day
git switch -c feat/<topic>                     # one branch per change
make check                                     # before every PR
```
Add a short note with your measured result in `changelog.d/`, then open a PR for the other person to review. Rules are in `CONTRIBUTING.md`; agents follow `AGENTS.md`.

## Troubleshooting
| Symptom | Fix |
|---|---|
| `Address already in use` | A daemon is still running: `pkill -f "arm.py serve"` (or `"arm.py view"`), then start again. |
| Live view stays empty | Run any command (`arm.py locate`); the page only shows results of commands. |
| `uv sync` fails on `mujoco` | Run it inside the repo folder: uv must pick Python 3.12 from `.python-version`. |
| `Connection refused` from `arm.py` | Tab 1 is not running, or it uses another port (`ARM_PORT`). |
| The model says "done" but it isn't | Trust `arm.py score` (simulation) or `arm.py locate`, never its report. |

## Real robot
Follow `venue/VENUE.md`: Station version and address, `arm.py bringup` (joint directions, gripper, calibration), smoke test, then the operator. The taught-grid fallback there needs no kinematic model.

## Documents
- `docs/field-guide.html`: the visual overview
- `docs/DESIGN.md`: architecture and decisions
- `docs/RESEARCH.md`: what fails elsewhere, and field learnings
- `venue/VENUE.md`: the plan for the event days
- `CHANGELOG.md` and `changelog.d/`: every experiment with its numbers
- `AGENTS.md` (repo-editing agents), `operator/AGENTS.md` (the robot-driving agent), `CONTRIBUTING.md` (how we work)

## Status
Measured in simulation: cubes 12/12, mixed objects 11/12, recovery from a knocked-out object, a flat pencil not graspable. **Nothing has run on real hardware yet.**

Private repository. Third-party parts keep their licences: `elrobot/` comes from norma-core (MIT), and `so101-sim/robotstudio_so101` carries its own licence.

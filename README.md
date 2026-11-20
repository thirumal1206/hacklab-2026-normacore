# HackLab 2026: NormaCore track

Claude Code or Codex controls a robot arm by looking through its cameras, using NormaCore's Station API.

The LLM chooses and verifies; code measures and moves. A daemon (`arm.py serve`) holds the robot (or a MuJoCo simulation) and enforces every safety limit. The agent calls a small CLI or the MCP tools: `locate` numbers the objects it sees, `pick`/`place` grasp them with IK, and `locate`/`look` confirm the result.

## Quick start (simulation, no hardware)
```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
make hooks                                                # pre-commit checks
make check                                                # should end with ALL CHECKS PASSED
.venv/bin/python arm.py serve --backend sim --seed 3      # terminal 1
.venv/bin/python arm.py view                              # terminal 2: open http://127.0.0.1:8766
cd operator && claude                                     # terminal 3: approve the "arm" MCP server, then ask it to tidy the table
```

## Real robot
Follow `venue/VENUE.md`: Station version and address, `arm.py bringup` (joint directions, gripper, calibration), smoke test, then the operator. The taught-grid fallback there needs no kinematic model.

## Documents
- `docs/DESIGN.md`: architecture and decisions
- `docs/RESEARCH.md`: what other teams and papers found
- `CHANGELOG.md` and `changelog.d/`: every experiment with its numbers
- `AGENTS.md`: rules for agents changing this repo
- `operator/AGENTS.md`: rules for the agent driving the robot
- `CONTRIBUTING.md`: how we work

## Status
Measured in sim (fresh Sonnet operators, privileged scoring): cubes 12/12; mixed objects 11/12; recovery from a knocked-out object; a 0.7 cm pencil lying flat is not graspable. **Nothing has run on real hardware yet.**

Private repository. Third-party parts keep their licences: `elrobot/` comes from norma-core (MIT), and `so101-sim/robotstudio_so101` carries its own licence.

# Contributing

Two people, one robot, 48 hours. These rules keep `main` always demo-ready.

## Setup (once per laptop)
```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
make hooks      # installs the pre-commit hook (.githooks/pre-commit)
make check
```

## Workflow
1. `main` is always runnable. Never push directly to it during the event, except for a one-line emergency fix you both agree on.
2. Branch per change: `feat/<topic>`, `fix/<topic>`, `exp/<topic>` (experiments), `docs/<topic>`.
3. Small pull requests (aim for under 300 changed lines), one purpose each. The other person reviews; merge with squash.
4. Pull `main` before starting, and rebase your branch before opening the PR.
5. Robot time is shared: say in chat before running anything on the real arm, and keep a hand near it.

## Commits
- Conventional style: `feat: push primitive for flat objects`, `fix: VP8 keyframe detection`, `docs: venue checklist`, `exp: pencil grasp 0/10`.
- Subject in the imperative, at most 72 characters; a body explains *why* when it isn't obvious.
- One logical change per commit. No generated files, logs, images from `runs/`, weights, or datasets.

## Definition of done
- `make check` passes, and the pre-commit hook passes (never `--no-verify`).
- Behaviour changes are measured in sim (fixed seeds, `score`), and the numbers go into `CHANGELOG.md`. Anything not tried on hardware says "not verified".
- If a CLI command changes: `operator/AGENTS.md`, `mcp_server.py` and the `arm.py` docstring are updated together.
- Safety limits (workspace, z floor, speed, calibrated ranges) are never loosened without a measured reason.

## Code style
- Python 3.11+, stdlib plus `requirements.txt`. New dependencies need a security check (maintainers, advisories, install scripts, network use) noted in the PR.
- Minimum code, no speculative abstractions. Comments only for a non-obvious reason or a trap.
- Match the existing style; `python -m py_compile` must pass.

## Secrets and safety
- No tokens, keys, passwords or personal data in the repo, in issues, or in commit messages. Each person logs in to services in their own terminal.
- Station has no authentication: bind it to 127.0.0.1 or a network you trust.
- The repo is private until the organisers ask for a public submission.

## Agents
Agents changing code follow `AGENTS.md` (Claude Code loads it via `CLAUDE.md`). The robot-driving agent follows `operator/AGENTS.md`. Review agent-written changes like any other PR.

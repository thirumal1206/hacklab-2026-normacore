# Calibration profiles, one per laptop

Station keeps the arm's calibration per laptop, so positions recorded on one laptop (crosses, home, bins, table height,
the camera homography) are wrong on another. Each folder here is one laptop's recorded set.

| Profile | Laptop | Recorded |
|---|---|---|
| `ivan/` | Ivan's MacBook Air | 21.11.2026: 9 crosses (RMS 5 mm), home, bin1-bin5, grip and table height |

**Which profile is used:** `AGENT_PROFILE=<folder>` if set, otherwise the profile whose `profile.json` lists this laptop's
name (`scutil --get LocalHostName`) under `hostnames`. With no match, real motion is refused (dry runs still work).

**Use Ivan's profile:** on his laptop, put his `LocalHostName` into `ivan/profile.json` → `hostnames` once, or run with
`AGENT_PROFILE=ivan`, e.g. `CANDY_GO=1 AGENT_PROFILE=ivan operator/candy/run.sh`.

**New laptop:** `cp -r agent/config/profiles/ivan agent/config/profiles/<name>`, set `name` and `hostnames` in its
`profile.json`, then record its own points with `AGENT_PROFILE=<name> .venv/bin/python agent/record_points.py`.
Recording only writes into the active profile, so other laptops' profiles are never overwritten.

Shared files (catalog, rules, vision) stay in `agent/config/` and are used by every profile.

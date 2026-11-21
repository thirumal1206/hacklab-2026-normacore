"""What happened in the last session, for feedback: per pack the decision, the outcome and what the arm did.

    .venv/bin/python agent/report.py            # markdown to stdout (paste into the team chat or the changelog)
"""
import json
import os
from pathlib import Path

RUNS = Path(os.environ.get("AGENT_RUNS_DIR", Path(__file__).resolve().parent.parent / "runs" / "agent"))
FAR_MM = 10  # a move that ends further than this from its target is worth a look


def rows(name):
    f = RUNS / name
    return [json.loads(x) for x in f.read_text().splitlines() if x.strip()] if f.exists() else []


def main():
    proof = rows("proof.jsonl")
    start = max((i for i, r in enumerate(proof) if r.get("session") == "start"), default=0)
    proof = proof[start:]
    since = proof[0]["time"] if proof else ""
    motion = [r for r in rows("motion.jsonl") if r.get("time", "") >= since]
    s = proof[0] if proof else {}
    print(f"# Sorting run {since}\n\nBackend: {s.get('backend')}, dry run: {s.get('dry_run')}, config {s.get('config_sha256')}\n")

    decisions = [r for r in proof if r.get("id") and not r.get("event")]
    print("| Pack | Claude read | Bin | Outcome | Arm |\n|---|---|---|---|---|")
    for r in decisions:
        out = ("verified" if r.get("verified") else "flagged: " + r["reason"] if r.get("flagged")
               else "refused: " + r["refused"] if r.get("refused") else "dry run" if r.get("dry_run") else "placed, not verified")
        m = [x for x in motion if x.get("pack") == r["id"] and x["kind"] == "move" and x.get("off_mm") is not None]
        arm = f"{len(m)} moves, max off {max(x['off_mm'] for x in m)} mm, {sum(x['seconds'] for x in m):.1f} s" if m else ""
        print(f"| {r['id']} | {r.get('read_name') or r.get('code') or ''} | {r.get('bin') or ''} | {out} | {arm} |")

    problems = [x for x in motion if x.get("result") or (x.get("off_mm") or 0) > FAR_MM]
    print(f"\n## Arm problems ({len(problems)})")
    for x in problems:
        what = x.get("result") or f"ended {x['off_mm']} mm from target (asked {x['target_cm']}, reached {x['reached_cm']})"
        print(f"- {x['time'][11:]} {x.get('pack')} {x.get('seq')} / {x['step']}: {what}")
    if not problems:
        print("- none")

    grips = [x for x in motion if x["kind"] == "grip" and x["step"] == "close"]
    if grips:
        print(f"\n## Gripper after close\n" + ", ".join(f"{x.get('pack')} {x['opening']}" for x in grips)
              + "\n(above grasp.empty_below = holding; tune it if packs are held but reported empty, or the reverse)")
    said = [r for r in proof if r.get("event") in ("heard", "say")]
    if said:
        print("\n## Conversation\n" + "\n".join(f"- {'You' if r['event'] == 'heard' else 'Friday'}: {r['text']}" for r in said))


if __name__ == "__main__":
    main()

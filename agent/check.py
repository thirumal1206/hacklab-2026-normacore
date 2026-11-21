"""Agent tools without hardware: vetoes, a full fake batch, and the MCP round trip. Exit 1 on failure."""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sorter import RUNS, FakeBackend, Sorter  # noqa: E402

ok = True


def check(name, cond):
    global ok
    ok &= bool(cond)
    print(("ok    " if cond else "FAIL  ") + name)


(RUNS / "proof.jsonl").unlink(missing_ok=True)
s = Sorter(FakeBackend(seed=3))
packs = {p["id"]: p for p in s.scan()["packs"]}
check("scan lists 8 packs", len(packs) == 8)

for pid, bad in (("p2", "Stock"), ("p3", "Stock"), ("p4", "Stock"), ("p5", "Stock"), ("p6", "Stock"), ("p1", "Expired"), ("p1", "Recall")):
    r = s.sort(pid, bad, "test")
    check(f"veto {pid} -> {bad}: {r.get('refused')}", r.get("refused") and r.get("expected"))
check("out of reach refused", "out of reach" in s.sort("p8", "Stock", "test").get("refused", ""))
check("label read with an unknown code still not Stock", s.sort("p4", "Stock", "t", "1234567890123", "2027-03-31").get("refused"))
check("inspect returns a crop", Path(s.inspect("p4")["image"]).exists())

want = {"p1": "Stock", "p2": "Expired", "p3": "Recall", "p4": "Stock", "p5": "Manual check", "p6": "Manual check", "p7": "Stock"}
for pid, b in want.items():
    r = s.sort(pid, b, "policy", *(("4006381333931", "2027-03-31") if pid == "p4" else ()))
    if r.get("refused", "").startswith("grasp"):
        s.flag(pid, r["refused"])
        continue
    check(f"{pid} -> {b} placed and verified", r.get("placed") and r.get("verified"))
s.flag("p8", "out of reach")
summ = s.summary()
check(f"summary {summ['sorted']} flagged {summ['flagged']}", not summ["left_on_table"])
check("proof log has one line per call", len((RUNS / "proof.jsonl").read_text().splitlines()) >= 17)

# ---- guards ----
import fcntl  # noqa: E402
import os  # noqa: E402

import sorter  # noqa: E402

check("already handled pack refused", "already handled" in s.sort("p1", "Stock", "again").get("error", ""))
check("pack not in the last scan refused", "not in the last scan" in s.sort("p99", "Stock", "x").get("error", ""))

g = Sorter(FakeBackend(seed=3))
g.scan()
sorter.STOP.touch()
try:
    check("stop switch: scan refused", "stopped" in g.scan().get("error", ""))
    check("stop switch: sort refused, pack stays open", "stopped" in g.sort("p1", "Stock", "x").get("error", "") and "p1" not in g.done)
finally:
    sorter.STOP.unlink()
check("after removing STOP, sort works", g.sort("p1", "Stock", "x").get("verified"))

for _ in range(sorter.MAX_TRIES):
    g.sort("p2", "Stock", "insist")
check(f"{sorter.MAX_TRIES} refused tries -> flagged", g.sort("p2", "Stock", "insist").get("flagged"))

with (sorter.RUNS / "arm.lock").open("w") as other:  # another session holds the arm
    fcntl.flock(other, fcntl.LOCK_EX)
    check("arm lock: second mover refused", "another session" in g.sort("p7", "Stock", "x").get("error", ""))
check("arm lock released: sort works", g.sort("p7", "Stock", "x").get("verified"))

os.environ["AGENT_DRY_RUN"] = "1"
d = Sorter(FakeBackend(seed=3))
d.scan()
r = d.sort("p1", "Stock", "plan")
check("dry run: planned, not moved", r.get("dry_run") and not r.get("placed") and "p1" in d.scan()["packs"].__repr__())
os.environ.pop("AGENT_DRY_RUN")

missing = sorter.missing_config()
check(f"empty templates: real backend refused ({len(missing)} values missing)", len(missing) > 10)
check("proof log records the config fingerprint", '"config_sha256"' in (RUNS / "proof.jsonl").read_text())

msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "scan", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "inspect", "arguments": {"id": "p4"}}}]
out = subprocess.run([sys.executable, str(Path(__file__).parent / "mcp_server.py")], input="\n".join(map(json.dumps, msgs)) + "\n",
                     capture_output=True, text=True, timeout=60).stdout.splitlines()
replies = [json.loads(x) for x in out]
check("mcp: 6 tools listed", len(replies[1]["result"]["tools"]) == 6)
check("mcp: scan returns packs", len(json.loads(replies[2]["result"]["content"][0]["text"])["packs"]) == 8)
check("mcp: inspect returns an image", replies[3]["result"]["content"][1]["type"] == "image")

print("AGENT CHECK OK" if ok else "AGENT CHECK FAILED")
sys.exit(0 if ok else 1)

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
res = s.scan()
packs = {p["id"]: p for p in res["packs"]}
check("name-only mode: scan lists 7 packs, positions only, no names", res["mode"] == "name_only" and len(packs) == 7
      and all(p["name"] is None for p in packs.values()))
check("list_bins maps products to bins", {"name": "Ibuprofen 400 mg", "bin": "Painkillers"} in s.list_bins()["products"])
check("inspect returns a close-up", Path(s.inspect("p1")["image"]).exists())

for pid, b, name, why in (("p1", "Painkillers", None, "no name read"), ("p2", "Allergy", "Paracetamol 500", "belongs in Painkillers"),
                          ("p5", "Painkillers", "Xyzomed 50 mg", "not in the catalogue"), ("p4", "Stock", "Vitamin C", "unknown bin")):
    r = s.sort(pid, b, "test", read_name=name)
    check(f"veto {pid} -> {b} (read {name!r}): {r.get('refused')}", why in (r.get("refused") or ""))
check("out of reach refused", "out of reach" in s.sort("p7", "Painkillers", "test", read_name="Ibuprofen").get("refused", ""))
check("spelling variant matches (Cetirizine)", s.match("CETIRIZINE 10mg")["bin"] == "Allergy")
check("too short a read matches nothing", s.match("10") is None)

want = {"p1": ("Painkillers", "Ibuprofen 400 mg"), "p2": ("Painkillers", "Paracetamol 500"), "p3": ("Allergy", "Cetirizin 10 mg"),
        "p4": ("Vitamins", "Vitamin C 500 mg"), "p5": ("Unknown", "Xyzomed 50 mg"), "p6": ("Unknown", None)}
for pid, (b, name) in want.items():
    r = s.sort(pid, b, "policy", read_name=name)
    if (r.get("refused") or "").startswith("grasp"):
        s.flag(pid, r["refused"])
        continue
    check(f"{pid} -> {b} placed and verified", r.get("placed") and r.get("verified"))
s.flag("p7", "out of reach")
summ = s.summary()
check(f"summary {summ['sorted']} flagged {summ['flagged']}", not summ["left_on_table"])
check("proof log keeps the name Claude read", '"read_name": "Cetirizin 10 mg"' in (RUNS / "proof.jsonl").read_text())

# ---- guards ----
import fcntl  # noqa: E402
import os  # noqa: E402

import sorter  # noqa: E402

check("already handled pack refused", "already handled" in s.sort("p1", "Painkillers", "again", read_name="Ibuprofen").get("error", ""))
check("pack not in the last scan refused", "not in the last scan" in s.sort("p99", "Unknown", "x").get("error", ""))

g = Sorter(FakeBackend(seed=3))
g.scan()
sorter.STOP.touch()
try:
    check("stop switch: scan refused", "stopped" in g.scan().get("error", ""))
    check("stop switch: sort refused, pack stays open", "stopped" in g.sort("p1", "Unknown", "x").get("error", "") and "p1" not in g.done)
finally:
    sorter.STOP.unlink()
check("after removing STOP, sort works", g.sort("p1", "Unknown", "x").get("verified"))

for _ in range(sorter.MAX_TRIES):
    g.sort("p2", "Vitamins", "insist", read_name="Paracetamol")
check(f"{sorter.MAX_TRIES} refused tries -> flagged", g.sort("p2", "Vitamins", "insist", read_name="Paracetamol").get("flagged"))

with (sorter.RUNS / "arm.lock").open("w") as other:  # another session holds the arm
    fcntl.flock(other, fcntl.LOCK_EX)
    check("arm lock: second mover refused", "another session" in g.sort("p3", "Allergy", "x", read_name="Cetirizin").get("error", ""))
check("arm lock released: sort works", g.sort("p3", "Allergy", "x", read_name="Cetirizin").get("verified"))

os.environ["AGENT_DRY_RUN"] = "1"
d = Sorter(FakeBackend(seed=3))
d.scan()
r = d.sort("p1", "Painkillers", "plan", read_name="Ibuprofen")
check("dry run: planned, not moved", r.get("dry_run") and not r.get("placed") and "p1" in d.scan()["packs"].__repr__())
os.environ.pop("AGENT_DRY_RUN")

missing = sorter.missing_config()
check(f"empty templates: real backend refused ({len(missing)} values missing)", "pose: home" in missing)
check("proof log records the config fingerprint", '"config_sha256"' in (RUNS / "proof.jsonl").read_text())

msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "scan", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "inspect", "arguments": {"id": "p4"}}}]
out = subprocess.run([sys.executable, str(Path(__file__).parent / "mcp_server.py")], input="\n".join(map(json.dumps, msgs)) + "\n",
                     capture_output=True, text=True, timeout=60).stdout.splitlines()
replies = [json.loads(x) for x in out]
check("mcp: vision preview tool listed", "vision_preview" in {t["name"] for t in replies[1]["result"]["tools"]})
check("mcp: scan returns packs", len(json.loads(replies[2]["result"]["content"][0]["text"])["packs"]) == 7)
check("mcp: inspect returns an image", replies[3]["result"]["content"][1]["type"] == "image")

import dashboard  # noqa: E402

os.environ["ARM_TTS"] = "off"
v = Sorter(FakeBackend(seed=3))
dashboard.heard("sort the table please")
check("voice: listen returns what was said", v.listen(1)["instruction"] == "sort the table please")
check("voice: nothing said -> null", v.listen(0.5)["instruction"] is None)
check("voice: say is logged for the dashboard", v.say("Done.")["said"] == "Done." and '"event": "say"' in (RUNS / "proof.jsonl").read_text())
dashboard.heard("Friday stop now")
try:
    check("voice: a stop word freezes the tools at once", sorter.STOP.exists() and "stopped" in v.scan().get("error", ""))
    check("voice: listen reports the stop", v.listen(1)["stopped"])
finally:
    sorter.STOP.unlink(missing_ok=True)
v.scan()
dashboard.heard("which bin is the nasal spray in")
check("voice: speech during a sort comes back as heard_while_busy", v.sort("p1", "Unknown", "x").get("heard_while_busy"))

st = dashboard.state()
check("dashboard state: current session, scan and events", st["session"] and st["scan"]["packs"] and st["events"])
print("AGENT CHECK OK" if ok else "AGENT CHECK FAILED")
sys.exit(0 if ok else 1)

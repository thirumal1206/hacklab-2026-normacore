"""real.py and teach.py against a fake elrobot-ik server: teach, scan, pick, place, guards. No arm needed. Exit 1 on failure."""
import json
import os
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp())
(HERE.parent / "runs" / "agent" / "motion.jsonl").unlink(missing_ok=True)
shutil.copytree(HERE / "config", TMP / "config")
os.environ["AGENT_CONFIG_DIR"] = str(TMP / "config")
os.environ["AGENT_BACKEND"] = "real"
os.environ["ARM_TTS"] = "off"
sys.path.insert(0, str(HERE))
import real  # noqa: E402
import sorter  # noqa: E402
import teach  # noqa: E402

ok = True


def check(name, cond):
    global ok
    ok &= bool(cond)
    print(("ok    " if cond else "FAIL  ") + name)


H = np.array(json.loads((HERE.parent / "venue/elrobot/ik-calib/calib/overview_homography.json").read_text())["H_pixel_to_xy"])


def pixel(x, y, width=320):
    u, v, k = np.linalg.inv(H) @ [x, y, 1.0]
    return int(u / k / (1280 / width)), int(v / k / (1280 / width))


class Fake:
    pose = {"x": 0.15, "y": 0.0, "z": 0.08, "roll": -1.57, "pitch": 0.0, "yaw": 0.0}
    opening, filled, flip, moves = 1.0, set(), False, []

    @staticmethod
    def steps(p):  # any deterministic pose -> steps mapping will do
        return [int(2048 + p["x"] * 1000), int(2048 + p["y"] * 1000), int(2048 + p["z"] * 1000), 2048, 2048, 2048, 2048, 2048]

    @staticmethod
    def near(p, q):
        return abs(p["x"] - q["x"]) < 0.003 and abs(p["y"] - q["y"]) < 0.003


class Srv(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, obj, ctype="application/json"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/v1/arms":
            p = Fake.pose
            self.reply([{"id": "5B79016927", "role": "follower", "gripper": Fake.opening,
                         "pose": {"position": {k: p[k] for k in "xyz"}, "rpy": {k: p[k] for k in ("roll", "pitch", "yaw")}},
                         "joints": [{"motor": i + 1, "steps": s} for i, s in enumerate(Fake.steps(p))]}])
        elif self.path.startswith("/api/v1/cameras/"):
            img = np.full((240, 320, 3), 235, np.uint8)
            if "top" in self.path:
                for s in Fake.filled:
                    g = real.slot(s)["grip"]
                    u, v = pixel(g["x"], g["y"])
                    cv2.rectangle(img, (u - 15, v - 8), (u + 15, v + 8), (90, 90, 90), -1)
            self.reply(cv2.imencode(".jpg", img)[1].tobytes(), "image/jpeg")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path.endswith("/move-to"):
            p = {**body["position"], **body["orientation"]["rpy"]}
            st = Fake.steps(p)
            if Fake.flip:
                st[2] += 500
            if body["execute"]:
                Fake.pose = p
                Fake.moves.append(p)
            self.reply({"reachable": True, "executed": body["execute"], "motor_steps": st, "motion": {"status": "reached"}})
        elif self.path.endswith("/gripper"):
            here = next((s for s in Fake.filled if Fake.near(real.slot(s)["grip"], Fake.pose) and Fake.pose["z"] < 0.05), None)
            if body["open"] < 0.5 and here:  # closing on a pack: jaws stall, the pack leaves the slot
                Fake.opening = 0.13
                Fake.filled.discard(here)
            else:
                Fake.opening = body["open"]
            self.reply({"ok": True})
        else:
            self.reply({"ok": True})


srv = ThreadingHTTPServer(("127.0.0.1", 0), Srv)
threading.Thread(target=srv.serve_forever, daemon=True).start()
m = json.loads((TMP / "config/motion.json").read_text())
m["ik_url"], m["grasp"]["settle_s"] = f"http://127.0.0.1:{srv.server_port}", 0
(TMP / "config/motion.json").write_text(json.dumps(m))
w = json.loads((TMP / "config/workspace.json").read_text())
w["cameras"].update(top_id="top", gripper_id="wrist")
w["occupancy"]["empty_image"] = str(TMP / "empty.jpg")
(TMP / "config/workspace.json").write_text(json.dumps(w))
(TMP / "config/catalog.json").write_text(json.dumps({"products": [{"name": "Ibuprofen 400 mg", "aliases": ["ibuprofen"], "bin": "Painkillers"}]}))

check("before teaching: real backend refuses to start", sorter.missing_config())

# teach: put the (fake) arm somewhere, record it
spots = {"home": (0.15, 0.0, 0.10)}
for i, s in enumerate(("slot1", "slot2", "slot3")):
    x, y = 0.15 + 0.03 * i, -0.06 + 0.06 * i
    spots[f"{s} hover"], spots[f"{s} grip"], spots[f"{s} read"] = (x, y, 0.08), (x, y, 0.035), (x, y, 0.12)
for i, b in enumerate(("Painkillers", "Allergy", "Vitamins", "Unknown")):
    spots[f"bin {b} hover"], spots[f"bin {b} drop"] = (0.12, 0.10 - 0.06 * i, 0.10), (0.12, 0.10 - 0.06 * i, 0.06)
for name, (x, y, z) in spots.items():
    Fake.pose = {"x": x, "y": y, "z": z, "roll": -1.57, "pitch": 0.0, "yaw": 0.0}
    teach.main(name.split())
teach.main(["empty"])
check("after teaching every pose and the empty photo: config complete", not sorter.missing_config())

Fake.filled = {"slot1", "slot3"}
s = sorter.Sorter()
res = s.scan()
check(f"scan finds the filled slots {sorted(p['id'] for p in res['packs'])}", sorted(p["id"] for p in res["packs"]) == ["slot1", "slot3"])
check("inspect: arm goes to the read pose and returns a wrist photo",
      Path(s.inspect("slot1")["image"]).exists() and any(abs(p["z"] - 0.12) < 1e-6 for p in Fake.moves))

Fake.moves.clear()
r = s.sort("slot1", "Painkillers", "read Ibuprofen", read_name="Ibuprofen 400 mg")
seq = [(round(p["z"], 3)) for p in Fake.moves]
check(f"sort plays hover, grip, lift, bin hover, drop, up, home (z {seq})", seq == [0.08, 0.035, 0.08, 0.1, 0.06, 0.1, 0.1])
check("pick held (jaws stalled), slot now empty, verified", r.get("placed") and r.get("verified"))
check("wrong bin still vetoed in real mode", "belongs in Painkillers" in (s.sort("slot3", "Vitamins", "x", read_name="Ibuprofen").get("refused") or ""))

# a teammate adds a step in the config only: a lower "pre" approach before the grip
m = json.loads((TMP / "config/motion.json").read_text())
m["sequences"]["pick"] = ["open", "slot.hover", "slot.pre", "slot.grip", "close", "wait:0", "slot.hover"]
(TMP / "config/motion.json").write_text(json.dumps(m))
check("a new step in the config becomes a pose to teach", "pose: slot3 pre" in sorter.missing_config())
Fake.pose = {"x": 0.21, "y": 0.06, "z": 0.05, "roll": -1.57, "pitch": 0.0, "yaw": 0.0}
teach.main(["slot3", "pre"])
Fake.moves.clear()
r = s.sort("slot3", "Painkillers", "read Ibuprofen", read_name="Ibuprofen 400 mg")
check(f"the arm plays the edited sequence (z {[round(p['z'], 3) for p in Fake.moves]})",
      [round(p["z"], 3) for p in Fake.moves][:4] == [0.08, 0.05, 0.035, 0.08] and r.get("verified"))
m["sequences"]["pick"] = ["open", "slot.hover", "jump"]
(TMP / "config/motion.json").write_text(json.dumps(m))
Fake.moves.clear()
try:
    real.run_seq("pick", s=real.slot("slot1"))
    check("a misspelt step is refused", False)
except RuntimeError as e:
    check("a misspelt step is refused before the first move", "unknown step 'jump'" in str(e) and not Fake.moves)
m["sequences"]["pick"] = ["open", "slot.hover", "slot.grip", "close", "slot.hover"]
(TMP / "config/motion.json").write_text(json.dumps(m))

Fake.flip = True
try:
    real.move(real.slot("slot3")["hover"], "flip test")
    check("flipped IK solution refused", False)
except RuntimeError as e:
    check("flipped IK solution refused, arm not moved", "flipped" in str(e))
Fake.flip = False

r = s.pick(0, 0) if False else real.pick(15.0, -6.0)
check("empty slot: closing finds nothing, holding false", not r["holding"] and "nothing caught" in r["reason"])

real.STOP.parent.mkdir(parents=True, exist_ok=True)
real.STOP.touch()
try:
    r = real.pick(21.0, 6.0)
    check("STOP before a move: pick returns stopped, no move sent", not r["holding"] and "stopped" in r["reason"])
finally:
    real.STOP.unlink()

log = [json.loads(x) for x in (real.RUNS / "motion.jsonl").read_text().splitlines() if x.strip()][-200:]
moves = [r for r in log if r["kind"] == "move" and r.get("off_mm") is not None]
check(f"motion log: every executed move has asked, reached, off_mm, seconds, steps ({len(moves)} moves)",
      moves and all({"target_cm", "reached_cm", "off_mm", "seconds", "steps", "pack", "seq"} <= r.keys() for r in moves))
check("motion log: the sort for slot1 shows pick then place steps", any(r["pack"] == "slot1" and r["seq"] == "place" for r in log))
check("motion log: the flipped joint is logged as refused", any("flipped" in (r.get("result") or "") for r in log))
check("motion log: the empty grasp is logged", any("nothing caught" in (r.get("result") or "") for r in log))
check("motion log: STOP is logged", any(r.get("result") == "stopped" for r in log))
check("motion log: gripper opening read back after close", any(r["kind"] == "grip" and r["step"] == "close" and r["opening"] == 0.13 for r in log))

import subprocess  # noqa: E402

rep = subprocess.run([sys.executable, str(HERE / "report.py")], capture_output=True, text=True).stdout
check("report: lists the packs and the arm problems", "| slot1 | Ibuprofen 400 mg | Painkillers | verified |" in rep and "flipped joint" in rep)

srv.shutdown()
shutil.rmtree(TMP)
print("REAL CHECK OK" if ok else "REAL CHECK FAILED")
sys.exit(0 if ok else 1)

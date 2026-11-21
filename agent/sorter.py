"""Sorting tools for Claude: scan, inspect, list_bins, sort, flag, summary.

Claude decides the bin; this code enforces the rules (vetoes), runs pick -> place -> verify, and writes the proof log.
Backend: AGENT_BACKEND=fake (default, agent/fixtures) or real (agent/real.py from the teammates, same functions as FakeBackend).
"""
import datetime as dt
import fcntl
import json
import os
import random
import time
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
RUNS = ROOT / "runs" / "agent"


STOP = RUNS / "STOP"  # touch it to freeze every acting tool
MAX_TRIES = 3  # sort calls per pack, vetoes included
REQUIRED = {  # file: dotted paths that must be measured before the real robot runs ("*" = every list item)
    "workspace.json": ["reach.min_x_cm", "reach.max_x_cm", "reach.max_abs_y_cm", "input_zone.x_min_cm", "input_zone.x_max_cm",
                       "input_zone.y_min_cm", "input_zone.y_max_cm", "bins.*.x_cm", "bins.*.y_cm", "cameras.top_id", "calibration.rms_mm"],
    "motion.json": ["poses.home.steps", "poses.hover_z_cm", "grasp.open", "speed.loaded.speed"],
}


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


def missing_config():
    def get(node, parts):
        if not parts:
            return [node]
        if parts[0] == "*":
            return [v for item in node for v in get(item, parts[1:])]
        return get(node.get(parts[0]) if isinstance(node, dict) else None, parts[1:]) if node is not None else [None]

    out = []
    for name, paths in REQUIRED.items():
        cfg = json.loads((HERE / "config" / name).read_text())
        out += [f"{name}:{p}" for p in paths if any(v is None for v in get(cfg, p.split(".")))]
    bins = {b["name"]: b for b in json.loads((HERE / "config" / "workspace.json").read_text())["bins"]}
    out += [f"workspace.json:bins.{n}.reach_tested" for n, b in bins.items() if not b.get("reach_tested")]
    return out


def fingerprint():
    import hashlib

    h = hashlib.sha256()
    for f in sorted((HERE / "config").glob("*.json")):
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


def load(name, fake):
    path = (HERE / "fixtures" / name) if fake and (HERE / "fixtures" / name).exists() else (HERE / "config" / name)
    return json.loads(path.read_text())


class FakeBackend:
    """Stands in for Vision + Motion until the real robot is wired. Grasps fail about 15% of the time."""

    def __init__(self, seed=1):
        self.rng = random.Random(seed)
        self.table = {p["id"]: dict(p) for p in json.loads((HERE / "fixtures" / "packs.json").read_text())}

    def scan(self):
        return [dict(p) for p in self.table.values()]

    def crop(self, pack_id):
        import cv2
        import numpy as np

        img = np.full((120, 420, 3), 245, np.uint8)
        for i, line in enumerate(self.table[pack_id]["label"].split(" | ")):
            cv2.putText(img, line, (10, 35 + 35 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (20, 20, 20), 2)
        path = RUNS / f"crop_{pack_id}.png"
        cv2.imwrite(str(path), img)
        return str(path)

    def pick(self, x_cm, y_cm, angle, height_class):
        return {"holding": self.rng.random() > 0.15, "reason": "fake grasp"}

    def place(self, bin_name):
        return {"placed": True, "reason": "fake place"}

    def verify_place(self, pack_id, bin_name):
        self.table.pop(pack_id, None)
        return {"ok": True, "photo": None}

    def home(self):
        return {"ok": True}

    def camera_bumped(self):
        return False


class Sorter:
    def __init__(self, backend=None):
        if backend is None:
            if os.environ.get("AGENT_BACKEND", "fake") == "fake":
                backend = FakeBackend()
            else:
                import real  # agent/real.py, provided by Vision + Motion

                backend = real
        self.b = backend
        self.fake = isinstance(backend, FakeBackend)
        self.ws = load("workspace.json", False)
        self.catalog = {p["code"]: p for p in load("catalog.json", self.fake)["products"]}
        self.rules = load("rules.json", self.fake)
        self.today = dt.date.fromisoformat(self.rules["today"])
        self.bins = [b["name"] for b in self.ws["bins"]]
        self.packs, self.done, self.tries = {}, {}, {}
        self.dry = os.environ.get("AGENT_DRY_RUN") == "1"
        RUNS.mkdir(parents=True, exist_ok=True)
        self.log = RUNS / "proof.jsonl"
        missing = [] if self.fake else missing_config()
        if missing:
            raise RuntimeError("config incomplete, fill in: " + ", ".join(missing))
        self.write({"time": now(), "session": "start", "backend": "fake" if self.fake else "real", "dry_run": self.dry,
                    "config_sha256": fingerprint()})

    def guard(self):
        """Reason every acting tool must refuse right now, or None."""
        if STOP.exists():
            return f"stopped by a person ({STOP} exists); delete it to continue"
        return None

    # ---- facts the rules need ----
    def facts(self, p):
        code, exp = p.get("code"), p.get("expiry")
        days = (dt.date.fromisoformat(exp) - self.today).days if exp else None
        return {
            "product": self.catalog.get(code, {}).get("name") if code else None,
            "known": code in self.catalog,
            "readable": code is not None and exp is not None,
            "recalled": code in self.rules.get("recalled_codes", []),
            "expired": days is not None and days < 0,
            "days_left": days,
            "near_expiry": days is not None and 0 <= days < self.rules["near_expiry_days"],
            "in_reach": self.in_reach(p),
        }

    def in_reach(self, p):
        r = self.ws["reach"]
        return r["min_x_cm"] <= p["x_cm"] <= r["max_x_cm"] and abs(p["y_cm"]) <= r["max_abs_y_cm"]

    def expected_bin(self, f):
        if f["recalled"]:
            return "Recall"
        if f["expired"]:
            return "Expired"
        if not (f["known"] and f["readable"]) or f["near_expiry"]:
            return "Manual check"
        return "Stock"

    def veto(self, f, bin_name):
        """Reason the move is refused, or None. Only Stock is guarded; a cautious bin is always allowed."""
        if bin_name not in self.bins:
            return f"unknown bin {bin_name!r}; bins: {self.bins}"
        if bin_name == "Recall" and not f["recalled"]:
            return "not on the recall list"
        if bin_name == "Expired" and not f["expired"]:
            return "not expired"
        if bin_name != "Stock":
            return None
        for key, why in (("recalled", "recalled"), ("expired", "expired"), ("near_expiry", "expires within "
                         f"{self.rules['near_expiry_days']} days")):
            if f[key]:
                return f"never Stock: {why}"
        if not f["known"]:
            return "never Stock: product not in the catalogue"
        if not f["readable"]:
            return "never Stock: code or expiry unreadable"
        return None

    # ---- tools ----
    def scan(self):
        if self.guard():
            return {"error": self.guard()}
        if self.b.camera_bumped():
            return {"error": "camera moved since calibration: recalibrate before sorting"}
        self.packs = {p["id"]: p for p in self.b.scan()}
        out = []
        for p in self.packs.values():
            f = self.facts(p)
            out.append({"id": p["id"], "product": f["product"], "code": p.get("code"), "expiry": p.get("expiry"),
                        "days_left": f["days_left"], "recalled": f["recalled"], "in_reach": f["in_reach"],
                        "fragile": self.catalog.get(p.get("code"), {}).get("fragile", False)})
        res = {"today": str(self.today), "packs": out, "bins": self.bins}
        (RUNS / "scan.json").write_text(json.dumps({"time": now(), **res}))  # for the dashboard
        self.write({"time": now(), "event": "scan", "packs": len(out)})
        return res

    def inspect(self, pack_id):
        p = self.packs[pack_id]
        img = p.get("crop") or self.b.crop(pack_id)
        self.write({"time": now(), "id": pack_id, "event": "inspect", "image": img})
        return {"id": pack_id, "image": img,
                "hint": "Read the code and expiry from the label. Pass them to sort as read_code / read_expiry (YYYY-MM-DD)."}

    def list_bins(self):
        return {"bins": [{"name": b["name"], "capacity": b.get("capacity")} for b in self.ws["bins"]],
                "rules": [f"{r['bin']}: {r['when']}" for r in load("rules.json", False)["bins_in_priority_order"]]}

    def sort(self, pack_id, bin_name, reason, read_code=None, read_expiry=None):
        if self.guard():
            return {"error": self.guard()}
        if pack_id not in self.packs:
            return {"error": f"unknown id {pack_id}: not in the last scan"}
        if pack_id in self.done:
            return {"error": f"{pack_id} is already handled ({self.done[pack_id].get('bin') or 'flagged'})"}
        self.tries[pack_id] = self.tries.get(pack_id, 0) + 1
        if self.tries[pack_id] > MAX_TRIES:
            return self.flag(pack_id, f"{MAX_TRIES} sort attempts refused or failed")
        p = dict(self.packs[pack_id])
        source = "scan"
        if read_code or read_expiry:  # Claude read the label crop; the catalogue still has to confirm the code
            p["code"], p["expiry"], source = read_code or p.get("code"), read_expiry or p.get("expiry"), "label read by Claude"
        f = self.facts(p)
        if not f["in_reach"]:
            return self.record(p, f, bin_name, reason, source, refused="out of reach: ask a person to move it closer")
        why = self.veto(f, bin_name)
        if why:
            return self.record(p, f, bin_name, reason, source, refused=why, expected=self.expected_bin(f))
        if self.dry:
            return self.record(p, f, bin_name, reason, source, dry_run=True, placed=False, verified=False,
                               would=f"pick at ({p['x_cm']}, {p['y_cm']}) angle {p.get('angle', 0)}, place in {bin_name}")
        lock = (RUNS / "arm.lock").open("w")
        try:  # one mover at a time, also across separate sessions
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.tries[pack_id] -= 1
            return {"error": "another session is moving the arm; wait and retry"}
        try:
            t0 = time.time()
            for attempt in (1, 2):
                if self.guard():
                    return self.record(p, f, bin_name, reason, source, refused=self.guard())
                g = self.b.pick(p["x_cm"], p["y_cm"], p.get("angle", 0), p.get("height_class", "normal"))
                if g["holding"]:
                    break
            else:
                return self.record(p, f, bin_name, reason, source, refused=f"grasp failed twice ({g['reason']}): flag it")
            pl = self.b.place(bin_name)
            v = self.b.verify_place(pack_id, bin_name) if pl["placed"] else {"ok": False, "photo": None}
            self.b.home()
            return self.record(p, f, bin_name, reason, source, placed=pl["placed"], verified=v["ok"], photo=v.get("photo"),
                               attempts=attempt, seconds=round(time.time() - t0, 1))
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()

    def flag(self, pack_id, reason):
        p = self.packs.get(pack_id, {"id": pack_id})
        return self.record(p, self.facts(p) if "x_cm" in p else {}, None, reason, "scan", flagged=True)

    def summary(self):
        rows = list(self.done.values())
        by_bin = {}
        for r in rows:
            if r.get("verified"):
                by_bin[r["bin"]] = by_bin.get(r["bin"], 0) + 1
        return {"sorted": by_bin, "flagged": [r["id"] for r in rows if r.get("flagged")],
                "refused": {r["id"]: r["refused"] for r in rows if r.get("refused")},
                "left_on_table": [i for i in self.packs if i not in self.done], "proof_log": str(self.log)}

    def record(self, p, f, bin_name, reason, source, **result):
        row = {"time": now(), "id": p["id"], "product": f.get("product"), "code": p.get("code"), "expiry": p.get("expiry"),
               "source": source, "bin": bin_name, "reason": reason, **result}
        self.write(row)
        if "expected" not in result and not str(result.get("refused", "")).startswith("stopped"):  # veto or stop: pack stays open
            self.done[p["id"]] = row
        return row

    def write(self, row):
        with self.log.open("a") as fh:
            fh.write(json.dumps(row) + "\n")

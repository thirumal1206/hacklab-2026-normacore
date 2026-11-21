"""Candy sorter: find candies with the top camera, pick one or many with Ivan's batch steps, under voice control.

    .venv/bin/python agent/candy.py spots                the taped crosses the arm knows (put candies on them)
    .venv/bin/python agent/candy.py pick bin2 3 4        candies on cross 3 and 4 into bin2 (dry run; add --go to move)
    .venv/bin/python agent/candy.py look                 camera detection (needs a camera calibration for this camera position)
    .venv/bin/python agent/candy.py pick bin2 c1 c3      dry run (solve only); add --go to move
    .venv/bin/python agent/candy.py pick bin1 all --go

While a pick runs, speech from the dashboard (runs/agent/inbox.jsonl) is obeyed between every step:
"stop" / "wait" pauses (the arm holds) and "continue" resumes at the same step; "bin 3" / "put the rest in bin 3"
sends the candy in the jaws and all later ones there; "cancel" drops the candy in hand where it was going and ends.
Positions come from robot_homography.json (pixel -> robot, fitted on 1280-wide frames).
"""
import json
import os
import re
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import real  # noqa: E402  (Ivan's motion: free_move, grip, jaw_yaw, bin_pose, home)

RUNS, STOP, INBOX = real.RUNS, real.STOP, real.RUNS / "inbox.jsonl"
LOG = RUNS / "candy.jsonl"
HUES = [(8, "red"), (20, "orange"), (34, "yellow"), (85, "green"), (130, "blue"), (160, "purple"), (180, "red")]
CTRL = {"cancel": r"\b(cancel|abort|that'?s enough|enough)\b", "stop": r"\b(stop|wait|pause|freeze|halt|hold on)\b",
        "resume": r"\b(continue|resume|go on|carry on|keep going)\b"}
NUMBERS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "to": "2", "too": "2", "for": "4"}


def log(**row):
    RUNS.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as fh:
        fh.write(json.dumps({"time": time.strftime("%H:%M:%S"), **row}) + "\n")


# ---------------------------------------------------------------- vision --

def camera():
    cam = os.environ.get("CANDY_CAMERA") or real.cfg("workspace.json")["cameras"].get("top_id")
    if not cam:
        cams = real.api("GET", "/cameras")
        raise RuntimeError("set CANDY_CAMERA to the top camera id: " + ", ".join(c["id"] for c in cams))
    return cam


def to_robot(u, v, width):
    h = json.loads((real.HERE / "config" / "robot_homography.json").read_text())
    s = h["image_size"][0] / width  # the homography was fitted on image_size frames
    x, y = cv2.perspectiveTransform(np.float32([[[u * s, v * s]]]), np.array(h["H_pixel_to_xy"]))[0, 0]
    return float(x) * 100, float(y) * 100


def colour(hsv_pixels):
    h, s, v = np.median(hsv_pixels, axis=0)
    if v < 70:
        return "dark"
    if s < 60:
        return "white" if v > 170 else "silver"
    return next(name for top, name in HUES if h <= top)


def look(img=None):
    """Candies on the white sheet: [{id, colour, x_cm, y_cm, angle, px}], plus a numbered image for Claude."""
    img = img if img is not None else cv2.imdecode(np.frombuffer(real.api("GET", f"/cameras/{camera()}/image"), np.uint8),
                                                   cv2.IMREAD_COLOR)
    hgt, wid = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = ((hsv[..., 1] > 70) | (hsv[..., 2] < 90)).astype(np.uint8) * 255  # coloured or dark on the white sheet
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    k = max(3, wid // 40) | 1  # join a wrapper's coloured ends across its silver middle into one candy
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(mask)
    area_px = wid * hgt / (320 * 240)
    reach = real.cfg("motion.json").get("candy", {}).get("reach_cm", {"x": [9, 30], "y": [-18, 18]})
    out = []
    for i in range(1, n):
        a = stats[i, 4]
        if not (6 * area_px <= a <= 900 * area_px):
            continue
        u, v = cent[i]
        x, y = to_robot(u, v, wid)
        if not (reach["x"][0] <= x <= reach["x"][1] and reach["y"][0] <= y <= reach["y"][1]):
            continue  # off the sheet, the arm itself, the bins
        pts = np.column_stack(np.nonzero(lab == i))[:, ::-1].astype(np.float32)
        (cu, cv), (w, h), ang = cv2.minAreaRect(pts)
        axis = np.deg2rad(ang if w >= h else ang + 90)  # long axis in the image
        x2, y2 = to_robot(u + 10 * np.cos(axis), v + 10 * np.sin(axis), wid)
        out.append({"colour": colour(hsv[lab == i]), "x_cm": round(x, 1), "y_cm": round(y, 1),
                    "angle": round(float(np.degrees(np.arctan2(y2 - y, x2 - x))), 0), "px": [int(u), int(v)], "area": int(a)})
    out.sort(key=lambda c: (-c["x_cm"], c["y_cm"]))
    marked = img.copy()
    for k, c in enumerate(out, 1):
        c["id"] = f"c{k}"
        cv2.circle(marked, tuple(c["px"]), max(8, wid // 40), (0, 0, 255), 2)
        cv2.putText(marked, c["id"], (c["px"][0] + 8, c["px"][1] - 8), cv2.FONT_HERSHEY_SIMPLEX, wid / 900, (0, 0, 255), 2)
    RUNS.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(RUNS / "candies.jpg"), marked)
    (RUNS / "candies.json").write_text(json.dumps(out))
    log(event="look", candies=len(out))
    return out


def spots():
    """The crosses taped on the sheet, at the robot positions Ivan recorded with the arm (no camera needed)."""
    pts = json.loads((real.HERE / "config" / "robot_points.json").read_text())["points"]
    far = real.cfg("motion.json").get("candy", {}).get("unreachable_crosses", ["cross_5", "cross_8"])  # too close to hover over
    return [{"id": "cross" + p["name"].split("_")[1], "colour": "?", "x_cm": round(p["pose"]["x"] * 100, 1),
             "y_cm": round(p["pose"]["y"] * 100, 1), "angle": 0, "reachable": p["name"] not in far}
            for p in pts if p["name"].startswith("cross_")]


# ----------------------------------------------------------------- voice --

def parse(text, bins):
    low = text.lower()
    out = {k: bool(re.search(rx, low)) for k, rx in CTRL.items()}
    words = " ".join(NUMBERS.get(w, w) for w in re.findall(r"[a-z0-9]+", low))
    out["bin"] = next((b for b in bins if re.search(rf"\bbin {re.escape(b[3:])}\b", words) or b in words.replace(" ", "")), None)
    return out


class Ears:
    """Reads what people said on the dashboard since the last check; speaks short acknowledgements."""

    def __init__(self, bins):
        self.bins, self.n, self.voice, self.unclear = bins, self.count(), None, []

    @staticmethod
    def count():
        return len(INBOX.read_text().splitlines()) if INBOX.exists() else 0

    def say(self, text):
        log(event="say", text=text)
        print(f"FRIDAY: {text}", flush=True)
        if os.environ.get("ARM_TTS", "say") == "off":
            return
        if self.voice is None:
            sys.path.insert(0, str(real.HERE.parent))
            import arm

            self.voice = arm.Voice()
        self.voice.say(text)

    def news(self):
        lines = INBOX.read_text().splitlines() if INBOX.exists() else []
        new, self.n = lines[self.n:], len(lines)
        out = []
        for line in new:
            text = json.loads(line)["text"]
            log(event="heard", text=text)
            c = parse(text, self.bins)
            if not (c["bin"] or c["cancel"] or c["stop"] or c["resume"]):
                self.unclear.append(text)
            out.append(c)
        return out


# ---------------------------------------------------------------- motion --

def pick(ids, bin_name, go=False, candies=None):
    """Pick the candies `ids` (or ["all"]) from the last look into bin_name, one after another, under voice control."""
    real.DRY = not go
    if candies is None:  # crosses unless the ids come from a camera look
        crossy = ids == ["all"] or all(re.fullmatch(r"(cross)?\d+", str(i)) for i in ids)
        candies = [s for s in spots() if s["reachable"]] if crossy else json.loads((RUNS / "candies.json").read_text())
        ids = ids if ids == ["all"] else [i if str(i).startswith(("cross", "c")) and not str(i).isdigit() else f"cross{i}" for i in ids]
    byid = {c["id"]: c for c in candies}
    todo = [c for c in candies] if ids == ["all"] else [byid[i] for i in ids if i in byid]
    unknown = [i for i in ids if i != "all" and i not in byid]
    m, f, d = real.free_settings()
    bins = [b["name"] for b in m["poses"]["bins"]]
    if bin_name not in bins:
        return {"error": f"unknown bin {bin_name}; bins: {', '.join(bins)}"}
    g, zg = m["grasp"], d["table_z_m"] + f["grip_z_above_table_cm"] / 100
    hover = zg + f["hover_above_grip_cm"] / 100
    ears, st = Ears(bins), {"bin": bin_name, "cancel": False, "pauses": 0, "switches": []}

    def hear():
        for c in ears.news():
            if c["bin"] and c["bin"] != st["bin"]:
                st["bin"] = c["bin"]
                st["switches"].append(c["bin"])
                ears.say(f"Switching to {c['bin']}.")
            if c["cancel"] and not st["cancel"]:
                st["cancel"] = True
                ears.say("Cancelling after this one.")

    def wait_if_stopped():
        hear()
        if not STOP.exists():
            return
        st["pauses"] += 1
        ears.say("Paused.")
        log(event="paused")
        while STOP.exists():
            time.sleep(0.25)
            hear()
        ears.say("Continuing.")
        log(event="resumed")

    def step(fn):
        """Run one motion step; if STOP stops it before it moves, pause and run the same step again."""
        while True:
            wait_if_stopped()
            try:
                return fn()
            except real.Stopped:
                continue

    done, t0 = [], time.monotonic()
    for c in todo:
        hear()
        if st["cancel"]:
            break
        t = time.monotonic()
        x, y = c["x_cm"] / 100, c["y_cm"] / 100
        rpy = (d["grip"]["roll"], d["grip"]["pitch"], real.jaw_yaw(c.get("angle", 0), f))
        at = dict(zip(("x", "y", "z", "roll", "pitch", "yaw"), (x, y, zg, *rpy)))
        none = (0, range(0))
        try:
            if real.DRY or real.jaw_goal() < g["open"] - 0.05:
                step(lambda: real.grip(g["open"]))
            step(lambda: real.free_move(x, y, hover, rpy, "hover", None, *none))
            step(lambda: real.free_move(x, y, zg, rpy, "down", None, *none))
            opening = step(lambda: real.grip(g["close"], settle=False, at=at))
            if opening is not None and opening >= g["empty_below"]:
                step(lambda: real.grip(max(opening - g.get("relief", 0.04), 0.0), at=at))
            step(lambda: real.free_move(x, y, hover, rpy, "up", None, *none, loaded=True))
            if opening is not None and opening < g["empty_below"]:
                done.append({"id": c["id"], "result": f"missed: jaws closed to {opening:.2f}"})
                log(event="missed", id=c["id"], opening=opening)
                continue
            hear()  # a bin switch said while the candy is in the jaws still counts
            target = st["bin"]
            drop = real.bin_pose(target)["drop"]
            step(lambda: real.move(drop, f"{target} drop", loaded=True))
            step(lambda: real.grip(g["open"], at=drop))
        except real.Refused as e:
            done.append({"id": c["id"], "result": f"refused: {e}"})
            log(event="refused", id=c["id"], reason=str(e))
            continue
        res = {"id": c["id"], "colour": c["colour"], "bin": target, "seconds": round(time.monotonic() - t, 1),
               "result": "dry run" if real.DRY else "dropped"}
        done.append(res)
        log(event="picked", **res)
    step(real.home)
    handled = {r["id"] for r in done}
    out = {"done": done, "remaining": [c["id"] for c in todo if c["id"] not in handled], "bin_now": st["bin"],
           "switched_to": st["switches"], "pauses": st["pauses"], "cancelled": st["cancel"],
           "seconds": round(time.monotonic() - t0, 1)}
    if ears.unclear:
        out["heard_while_busy"] = ears.unclear
    if unknown:
        out["not_picked"] = {i: "not a reachable cross (reachable: " + ", ".join(c["id"] for c in candies) + ")" for i in unknown}
    return out


def listen(wait_s=300):
    """Next thing said on the dashboard, or None after wait_s."""
    global _heard
    lines = INBOX.read_text().splitlines() if INBOX.exists() else []
    if "_heard" not in globals():
        _heard = len(lines)
    deadline = time.time() + float(wait_s)
    while True:
        lines = INBOX.read_text().splitlines() if INBOX.exists() else []
        if len(lines) > _heard:
            _heard += 1
            text = json.loads(lines[_heard - 1])["text"]
            log(event="heard", text=text)
            return {"instruction": text, "stopped": STOP.exists()}
        if time.time() > deadline:
            return {"instruction": None}
        time.sleep(0.3)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--go"]
    if args[:1] == ["spots"]:
        print(json.dumps(spots(), indent=1))
    elif args[:1] == ["look"]:
        print(json.dumps(look(), indent=1))
    elif len(args) >= 3 and args[0] == "pick":
        print(json.dumps(pick(args[2:], args[1], go="--go" in sys.argv), indent=1))
    else:
        sys.exit(__doc__)

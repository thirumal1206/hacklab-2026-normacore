"""Pick a list of objects one after another into one bin, as fast as possible: no holding check, no retries,
no home in between. Positions are taken once (from one camera frame) and given on the command line.

    .venv/bin/python agent/batch_pick.py bin1 X,Y[,ANGLE] X,Y[,ANGLE] ...  [--go]      (cm, deg; default dry)
    .venv/bin/python agent/batch_pick.py bin1 --vision [--go]     positions from live vision (no Claude)

--vision with live_vision.py running (runs/agent/world.json fresh): live mode. The next object is chosen each round while
the arm is at the bin or home (not over the table), its latest position is re-read right before the hover move, and a
spot is tried at most twice (a miss stays visible and is retried once). When the table looks empty the arm goes home
and looks again (objects under the arm are hidden), then finishes.

--vision without live vision: one frame, Ash's outline detector (vision_targets.detect_outlines, unchanged) on the paper sheet, mapped to robot cm with our
robot_homography.json (fitted on robot-touched crosses), minAreaRect per object, jaw closing angle = long axis + 90.
Objects wider than the jaws, tiny marks, the arc line and anything out of reach are skipped. Order: nearest to the bin first. The marked frame is saved to
runs/agent/vision_batch.jpg. With --go the arm goes home first so it is out of the photo.

Per object: hover (jaws opening on the way), down, close, up, bin drop, open: 6 commands, time per command printed.
Home once at the end. Speed/accel come from motion.json. STOP file (runs/agent/STOP) still stops before any move.
"""
import json
import math
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import parallax  # noqa: E402
import real  # noqa: E402
import voice_ctl  # noqa: E402


def parse(arg):
    v = [float(s) for s in arg.split(",")]
    return v[0], v[1], v[2] if len(v) > 2 else 0.0


CAMERA = os.environ.get("VISION_CAMERA", "835446b047547e89f1f2e588eedf268b")  # top camera queue id (changes on replug)
# ADJUST if the paper moves: the white sheet in the 1280x960 frame, mug and box outside. As in Ash's profile, the detector
# searches the whole sheet; outlines touching its border (mug, box, arm) are dropped by the detector itself.
PAPER_PX = [(445, 336), (790, 333), (1150, 880), (200, 880)]  # top right kept left of the box (bin1) and its shadow
SCALE = 1.5  # Ash tuned the detector on 1920-wide frames; our frames are 1280 wide
REACH_CM = (8.0, 24.0)  # distance from the base axis
MAX_WIDTH_CM = 6.5  # jaws open about 5 cm; outlines come out 0.5-1 cm wider (wings fan out, dark objects add their shadow)
LENGTH_CM = (1.5, 12.0)  # shorter: tape cross or speck; longer: the drawn arc line
MIN_WIDTH_CM = 1.5  # thinner: a piece of the drawn arc line
LIVE_WAIT_S = 1.2  # after home: arm out of the view, the tracker needs 3 frames (0.6 s at 5 Hz) to confirm
SAME_SPOT_CM = 2.0  # an object this close to an earlier attempt is the same one (a miss)
TRIES_PER_SPOT = 2
MAX_PICKS = 20
MAX_REPLANS = 10
JAW_TRIES_DEG = (0, 10, -10, 20, -20)  # the IK solver gets stuck in narrow wrist-angle bands; the jaws tolerate 20 deg  # target gone or moved between choosing and moving, in a row: give up


def vision_targets(drop):
    """One frame -> [(x_cm, y_cm, jaw_angle_deg)], nearest to the bin first. Saves a marked image."""
    import cv2
    import numpy as np

    import vision_targets as vt
    h = json.loads((real.CONFIG / "robot_homography.json").read_text())
    H = np.array(h["H_pixel_to_xy"])
    img = cv2.imdecode(np.frombuffer(real.api("GET", f"/cameras/{CAMERA}/image"), np.uint8), cv2.IMREAD_COLOR)
    if list(img.shape[1::-1]) != h["image_size"]:
        sys.exit(f"camera frame {img.shape[1]}x{img.shape[0]} differs from the mapping {h['image_size']}: refit first")
    poly = np.int32(PAPER_PX)
    big = cv2.resize(img, None, fx=SCALE, fy=SCALE)
    outlines = [o / SCALE for o in vt.detect_outlines(big, (poly * SCALE).astype(np.int32))]
    found, keep = [], []
    for outline in outlines:
        xy = cv2.perspectiveTransform(outline.astype(np.float64), H).astype(np.float32) * 100
        (cx, cy), (w, l), ang = cv2.minAreaRect(xy)
        cx, cy = parallax.correct(cx, cy)  # object top above the table, per position
        long_axis = (ang + (90 if l > w else 0) + 90) % 180 - 90  # Ash's convention: long axis, deg from +x to +y
        width, length = min(w, l), max(w, l)
        jaw = (long_axis + 90) % 180 if length > 1.1 * width else 0.0  # close across the long axis
        dist = math.hypot(cx, cy)
        why = ("too wide" if width > MAX_WIDTH_CM else "too thin" if width < MIN_WIDTH_CM else "too small" if length < LENGTH_CM[0] else
               "too long" if length > LENGTH_CM[1] else
               "out of reach" if not REACH_CM[0] <= dist <= REACH_CM[1] else None)
        found.append((outline, cx, cy, jaw, width, length, why))
        if why is None:
            keep.append((cx, cy, jaw))
    keep.sort(key=lambda t: math.hypot(t[0] - drop["x"] * 100, t[1] - drop["y"] * 100))
    cv2.polylines(img, [poly], True, (0, 200, 0), 1)
    for outline, cx, cy, jaw, width, length, why in found:
        col = (0, 0, 255) if why else (0, 200, 0)
        cv2.drawContours(img, [outline.astype(np.int32)], -1, col, 2)
        u, v = outline[:, 0, :].mean(0).astype(int)
        n = next((i for i, t in enumerate(keep, 1) if t[:2] == (cx, cy)), None)
        cv2.putText(img, f"#{n}" if n else why, (int(u) + 10, int(v) - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.8 if n else 0.5,
                    (255, 0, 0) if n else col, 2)
        print(f"  {'#' + str(n) if n else 'skip':5} ({cx:5.1f}, {cy:5.1f}) cm  {width:.1f} x {length:.1f} cm  "
              f"jaw {jaw:5.1f} deg  {why or ''}")
    real.RUNS.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(real.RUNS / "vision_batch.jpg"), img)
    print(f"vision: {len(keep)} of {len(found)} objects to pick, marked frame runs/agent/vision_batch.jpg")
    return keep


class Picker:
    """One pick-and-place per call: hover (jaws opening on the way), down, close, up, bin drop, open."""

    def __init__(self, bin_name, ctl):
        self.bin_name, self.ctl = bin_name, ctl
        m, self.f, self.d = real.free_settings()
        self.g = m["grasp"]
        self.zg = self.d["table_z_m"] + self.f["grip_z_above_table_cm"] / 100
        self.hover = self.zg + self.f["hover_above_grip_cm"] / 100
        self.none = (0, range(0))  # no joint-jump guard: fast mode
        self.times = []

    def bin(self, planned=None):
        """A bin said during the run ("bin 3") wins over the plan; else the planned bin, else the run's bin."""
        if self.ctl and self.ctl.bin != self.bin_name:
            return self.ctl.bin
        return planned or self.bin_name

    def rpy(self, angle):
        return self.d["grip"]["roll"], self.d["grip"]["pitch"], real.jaw_yaw(angle, self.f)

    def step(self, label, fn, *a, **k):  # run one command (after the voice check) and note how long it took
        while True:
            voice_ctl.check(self.ctl)
            s = time.monotonic()
            try:
                out = fn(*a, **k)
                break
            except real.Stopped:  # STOP came between the check and the command: pause there, then retry
                if self.ctl is None:
                    raise
        self.times.append(f"{label} {time.monotonic() - s:.1f}")
        return out

    def solvable(self, x_cm, y_cm, angle):
        """IK for hover and grip height, no motion. Returns the jaw angle to use: the object's own, or the nearest of
        JAW_TRIES_DEG the solver accepts. Raises real.Refused only if every one is refused."""
        refused = None
        for off in JAW_TRIES_DEG:
            a = (angle + off) % 180
            try:
                for z in (self.hover, self.zg):
                    while True:
                        voice_ctl.check(self.ctl)
                        try:
                            real.free_move(x_cm / 100, y_cm / 100, z, self.rpy(a), "check", None, *self.none, execute=False)
                            break
                        except real.Stopped:  # paused by voice during the check: wait, then solve again
                            if self.ctl is None:
                                raise
            except real.Refused as e:
                refused = refused or e
                continue
            if off:
                print(f"  jaw {angle:.0f} deg refused by IK, using {a:.0f} deg", flush=True)
            return a
        raise refused

    def pick(self, x_cm, y_cm, angle, bin_name=None):
        """Returns the bin used. On voice abort a held object is put back where it was, then Aborted is raised."""
        self.times = []
        x, y, g, none = x_cm / 100, y_cm / 100, self.g, self.none
        rpy = self.rpy(angle)
        at = dict(zip(("x", "y", "z", "roll", "pitch", "yaw"), (x, y, self.zg, *rpy)))
        held = False
        try:
            if not real.DRY:
                real.set_jaw(g["open"])  # no separate open command: the hover move carries gripper = open
            self.step("hover", real.free_move, x, y, self.hover, rpy, "hover", None, *none)
            self.step("down", real.free_move, x, y, self.zg, rpy, "down", None, *none)
            self.step("close", real.grip, g["close"], settle=False, at=at)  # no relief step: close and go
            held = True
            self.step("up", real.free_move, x, y, self.hover, rpy, "up", None, *none, loaded=True)
            bin_now = self.bin(bin_name)  # read after the lift: "bin 3" said until here still counts
            drop = real.bin_pose(bin_now)["drop"]
            self.step("bin", real.move, drop, f"{bin_now} drop", loaded=True)
            self.step("open", real.grip, g["open"], at=drop)
            return bin_now
        except voice_ctl.Aborted:
            real.STOP.unlink(missing_ok=True)
            if held:  # put it back where it was picked, from straight above
                real.free_move(x, y, self.hover, rpy, "abort hover", None, *none, loaded=True)
                real.free_move(x, y, self.zg, rpy, "abort down", None, *none, loaded=True)
                real.grip(g["open"], at=at)
                real.free_move(x, y, self.hover, rpy, "abort up", None, *none)
            voice_ctl.say("Aborted." + (" Put it back." if held else ""))
            raise


PLAN_NEAR_CM = 4.0  # a planned object whose track id changed (hidden under the arm) is found again by label and place


def live(p, go, plan=None, notify=None):
    """Pick from live_vision's world.json until the table is empty (see the module doc). plan (planner.py): pick only
    these objects, each into its own bin; an object counts as planned by track id, or by the same label near the planned spot.
    notify(kind, text): progress for a listener (arm_server.py): "picked", "skip", "retry", "done"."""
    import live_vision
    notify = notify or (lambda kind, text: None)
    if go:
        real.home()
        time.sleep(LIVE_WAIT_S)
    t0, tries, skipped, picks, misses, replans, at_home, found = time.monotonic(), [], [], 0, 0, 0, True, None

    def tried(o, spots=None):
        return sum(math.hypot(o["x_cm"] - x, o["y_cm"] - y) < SAME_SPOT_CM for x, y in (tries if spots is None else spots))

    def planned(o):
        if plan is None:
            return {"bin": None}
        return next((e for e in plan if e["id"] == o["id"]), None) or next(
            (e for e in plan if e.get("label") and e["label"] == o.get("label")
             and math.hypot(o["x_cm"] - e["x_cm"], o["y_cm"] - e["y_cm"]) < PLAN_NEAR_CM), None)

    def to_bin_cm(o, bin_name):
        drop = real.bin_pose(bin_name)["drop"]
        return math.hypot(o["x_cm"] - drop["x"] * 100, o["y_cm"] - drop["y"] * 100)

    def world():
        w = live_vision.world()
        if w is None:
            raise RuntimeError("live vision stopped: runs/agent/world.json is stale")
        return [o for o in w if o["pickable"]]

    try:
        while picks < MAX_PICKS and replans < MAX_REPLANS:
            voice_ctl.check(p.ctl)
            todo = [o for o in world() if tried(o) < (1 if real.DRY else TRIES_PER_SPOT) and not tried(o, skipped) and planned(o)]
            todo.sort(key=lambda o: to_bin_cm(o, p.bin(planned(o)["bin"])))  # nearest to its bin first
            if found is None:
                found = len(plan) if plan is not None else len(todo)
                if p.ctl and plan is None:
                    voice_ctl.say(f"I see {found} to pick, into bin {p.bin()[3:]}." if found else "I see nothing I can pick.")
            if not todo:
                if at_home:
                    break
                p.step("home", real.home)  # something may hide under the arm: look again from home
                at_home = True
                if go:
                    time.sleep(LIVE_WAIT_S)
                continue
            o = todo[0]
            try:
                angle = p.solvable(o["x_cm"], o["y_cm"], o["jaw_deg"])
            except real.Refused as e:
                print(f"  skip #{o['id']} ({o['x_cm']:.1f}, {o['y_cm']:.1f}) cm: {e}")
                skipped.append((o["x_cm"], o["y_cm"]))  # not a miss: never tried
                notify("skip", f"Cannot reach the {o.get('label') or 'object'}.")
                continue
            now = next((n for n in world() if n["id"] == o["id"]), None)  # re-check right before moving over it
            target = planned(o) or {"bin": None}
            if now is None or math.hypot(now["x_cm"] - o["x_cm"], now["y_cm"] - o["y_cm"]) > SAME_SPOT_CM:
                replans += 1
                print(f"  #{o['id']} {'gone' if now is None else 'moved'}: choosing again")
                continue
            replans = 0
            if tried(now):
                misses += 1
                print(f"  #{now['id']} still there after a pick: retrying once")
                notify("retry", f"Missed the {now.get('label') or 'object'}, trying once more.")
            tries.append((now["x_cm"], now["y_cm"]))
            t = time.monotonic()
            angle = (angle + now["jaw_deg"] - o["jaw_deg"]) % 180  # keep the IK offset if the angle estimate moved
            bin_used = p.pick(now["x_cm"], now["y_cm"], angle, target["bin"])
            picks, at_home = picks + 1, False
            print(f"{picks}. #{now['id']} {now.get('label') or ''} ({now['x_cm']:.1f}, {now['y_cm']:.1f}) cm jaw {angle:.0f} -> {bin_used}  "
                  f"{time.monotonic() - t:.1f} s   [{', '.join(p.times)}]", flush=True)
            notify("picked", f"Put the {now.get('label') or 'object'} in bin {bin_used[3:]}.")
            if p.ctl and p.ctl.cancel:
                break
    except voice_ctl.Aborted:
        pass  # pick() already put a held object back and said so
    real.home()
    if go and picks:  # objects still lying at a tried spot were missed
        time.sleep(LIVE_WAIT_S)
        misses += sum(tried(o) >= 1 for o in (live_vision.world() or []) if o["pickable"] and planned(o))
    done = picks - misses
    print(f"done: {done} of {found or 0} ({picks} picks, {misses} missed) in {time.monotonic() - t0:.1f} s")
    notify("done", f"Finished: {done} of {found or 0} picked, {time.monotonic() - t0:.0f} seconds.")
    return {"done": done, "found": found or 0, "seconds": round(time.monotonic() - t0, 1), "bin": p.bin()}


def main(argv, ctl=None, plan=None, notify=None):
    """ctl: a voice_ctl.Control (voice_batch.py passes one); None = no voice control. plan: planner.py picks (live mode only)."""
    go, vision = "--go" in argv, "--vision" in argv
    args = [a for a in argv if a not in ("--go", "--vision")]
    if len(args) < (1 if vision else 2):
        sys.exit(__doc__)
    if not go:
        real.DRY = True
    bin_name = args[0]
    p = Picker(bin_name, ctl)
    if vision:
        import live_vision
        if live_vision.world() is not None:
            print(f"live vision: picking into {bin_name}{'' if go else ' (dry run)'}")
            return live(p, go, plan, notify)
        print("live vision not running: one frame with Ash's detector")
        if go:
            real.home()  # out of the photo
        targets = vision_targets(real.bin_pose(bin_name)["drop"])
        ok = []  # solve every target first (no motion); an unreachable one is skipped instead of stopping the batch
        for x_cm, y_cm, angle in targets:
            try:
                ok.append((x_cm, y_cm, p.solvable(x_cm, y_cm, angle)))
            except real.Refused as e:
                print(f"  skip ({x_cm:.1f}, {y_cm:.1f}) cm: {e}")
            except voice_ctl.Aborted:
                voice_ctl.say("Aborted.")
                return {"done": 0, "found": len(targets), "seconds": 0, "bin": p.bin()}
        targets = ok
        if ctl:
            voice_ctl.say(f"Picking {len(targets)} candies into bin {bin_name[3:]}." if targets else "I see nothing I can pick.")
        print(f"{len(targets)} reachable, picking into {bin_name}{'' if go else ' (dry run)'}")
    else:
        targets = [parse(a) for a in args[1:]]
    t0, done = time.monotonic(), 0
    for i, (x_cm, y_cm, angle) in enumerate(targets, 1):
        t = time.monotonic()
        try:
            bin_now = p.pick(x_cm, y_cm, angle)
        except voice_ctl.Aborted:
            break
        done += 1
        print(f"{i}/{len(targets)} ({x_cm:.1f}, {y_cm:.1f}) cm -> {bin_now}  {time.monotonic() - t:.1f} s   "
              f"[{', '.join(p.times)}]", flush=True)
        if ctl and ctl.cancel:
            break
    real.home()
    print(f"done: {done} of {len(targets)} objects in {time.monotonic() - t0:.1f} s")
    return {"done": done, "found": len(targets), "seconds": round(time.monotonic() - t0, 1), "bin": p.bin()}


if __name__ == "__main__":
    main(sys.argv[1:])

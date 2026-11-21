"""Pick a list of objects one after another into one bin, as fast as possible: no holding check, no retries,
no home in between. Positions are taken once (from one camera frame) and given on the command line.

    .venv/bin/python agent/batch_pick.py bin1 X,Y[,ANGLE] X,Y[,ANGLE] ...  [--go]      (cm, deg; default dry)
    .venv/bin/python agent/batch_pick.py bin1 --vision [--go]     positions from one top-camera frame (no Claude)

--vision: Ash's outline detector (vision_targets.detect_outlines, unchanged) on the paper sheet, mapped to robot cm with our
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
import real  # noqa: E402
import voice_ctl  # noqa: E402


def parse(arg):
    v = [float(s) for s in arg.split(",")]
    return v[0], v[1], v[2] if len(v) > 2 else 0.0


CAMERA = os.environ.get("VISION_CAMERA", "835446b047547e89f1f2e588eedf268b")  # top camera queue id (changes on replug)
# ADJUST if the paper moves: the white sheet in the 1280x960 frame, mug and box outside. As in Ash's profile, the detector
# searches the whole sheet; outlines touching its border (mug, box, arm) are dropped by the detector itself.
PAPER_PX = [(445, 336), (812, 333), (1150, 880), (200, 880)]
SCALE = 1.5  # Ash tuned the detector on 1920-wide frames; our frames are 1280 wide
REACH_CM = (8.0, 24.0)  # distance from the base axis
MAX_WIDTH_CM = 6.0  # jaws open about 5 cm; outlines come out about 1 cm wider than the object (wings fan out)
LENGTH_CM = (1.5, 12.0)  # shorter: tape cross or speck; longer: the drawn arc line
MIN_WIDTH_CM = 1.5  # thinner: a piece of the drawn arc line
# ADJUST per object height: the mapping is fitted on the table, a candy top is ~2 cm higher and the camera looks at it
# from the +x side, so it appears closer to the base. Measured 22.11 with the wrist camera on 3 candies: x -1.3..-1.5 cm.
VISION_OFFSET_CM = (1.4, 0.1)


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
        cx, cy = cx + VISION_OFFSET_CM[0], cy + VISION_OFFSET_CM[1]
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


def main(argv, ctl=None):
    """ctl: a voice_ctl.Control (voice_batch.py passes one); None = no voice control."""
    go, vision = "--go" in argv, "--vision" in argv
    args = [a for a in argv if a not in ("--go", "--vision")]
    if len(args) < (1 if vision else 2):
        sys.exit(__doc__)
    if not go:
        real.DRY = True
    bin_name = args[0]
    if vision:
        if go:
            real.home()  # out of the photo
        targets = vision_targets(real.bin_pose(bin_name)["drop"])
    else:
        targets = [parse(a) for a in args[1:]]
    m, f, d = real.free_settings()
    g = m["grasp"]
    zg = d["table_z_m"] + f["grip_z_above_table_cm"] / 100
    hover = zg + f["hover_above_grip_cm"] / 100
    drop = real.bin_pose(bin_name)["drop"]
    none = (0, range(0))  # no joint-jump guard: fast mode
    if vision:  # solve every target first (no motion); an unreachable one is skipped instead of stopping the batch
        ok = []
        for x_cm, y_cm, angle in targets:
            rpy = (d["grip"]["roll"], d["grip"]["pitch"], real.jaw_yaw(angle, f))
            try:
                for z in (hover, zg):
                    while True:
                        voice_ctl.check(ctl)
                        try:
                            real.free_move(x_cm / 100, y_cm / 100, z, rpy, "check", None, *none, execute=False)
                            break
                        except real.Stopped:  # paused by voice during the check: wait, then solve again
                            if ctl is None:
                                raise
                ok.append((x_cm, y_cm, angle))
            except real.Refused as e:
                print(f"  skip ({x_cm:.1f}, {y_cm:.1f}) cm: {e}")
            except voice_ctl.Aborted:
                voice_ctl.say("Aborted.")
                return {"done": 0, "found": len(targets), "seconds": 0, "bin": ctl.bin}
        targets = ok
        if ctl:
            voice_ctl.say(f"Picking {len(targets)} candies into bin {bin_name[3:]}." if targets else "I see nothing I can pick.")
        print(f"{len(targets)} reachable, picking into {bin_name}{'' if go else ' (dry run)'}")
    t0, done = time.monotonic(), 0
    for i, (x_cm, y_cm, angle) in enumerate(targets, 1):
        t, times = time.monotonic(), []

        def step(label, fn, *a, **k):  # run one command (after the voice check) and note how long it took
            while True:
                voice_ctl.check(ctl)
                s = time.monotonic()
                try:
                    out = fn(*a, **k)
                    break
                except real.Stopped:  # STOP came between the check and the command: pause there, then retry
                    if ctl is None:
                        raise
            times.append(f"{label} {time.monotonic() - s:.1f}")
            return out

        x, y = x_cm / 100, y_cm / 100
        rpy = (d["grip"]["roll"], d["grip"]["pitch"], real.jaw_yaw(angle, f))
        at = dict(zip(("x", "y", "z", "roll", "pitch", "yaw"), (x, y, zg, *rpy)))
        held = False
        try:
            if not real.DRY:
                real.set_jaw(g["open"])  # no separate open command: the hover move carries gripper = open
            step("hover", real.free_move, x, y, hover, rpy, "hover", None, *none)
            step("down", real.free_move, x, y, zg, rpy, "down", None, *none)
            step("close", real.grip, g["close"], settle=False, at=at)  # no relief step: close and go
            held = True
            step("up", real.free_move, x, y, hover, rpy, "up", None, *none, loaded=True)
            bin_now = ctl.bin if ctl else bin_name  # "bin 3" said before here still counts for this candy
            drop = real.bin_pose(bin_now)["drop"]
            step("bin", real.move, drop, f"{bin_now} drop", loaded=True)
            step("open", real.grip, g["open"], at=drop)
            held = False
        except voice_ctl.Aborted:
            real.STOP.unlink(missing_ok=True)
            if held:  # put it back where it was picked, from straight above
                real.free_move(x, y, hover, rpy, "abort hover", None, *none, loaded=True)
                real.free_move(x, y, zg, rpy, "abort down", None, *none, loaded=True)
                real.grip(g["open"], at=at)
                real.free_move(x, y, hover, rpy, "abort up", None, *none)
            voice_ctl.say("Aborted." + (" Put the candy back." if held else ""))
            break
        done += 1
        print(f"{i}/{len(targets)} ({x_cm:.1f}, {y_cm:.1f}) cm -> {bin_now}  {time.monotonic() - t:.1f} s   "
              f"[{', '.join(times)}]", flush=True)
        if ctl and ctl.cancel:
            break
    real.home()
    print(f"done: {done} of {len(targets)} objects in {time.monotonic() - t0:.1f} s")
    return {"done": done, "found": len(targets), "seconds": round(time.monotonic() - t0, 1),
            "bin": ctl.bin if ctl else bin_name}

if __name__ == "__main__":
    main(sys.argv[1:])

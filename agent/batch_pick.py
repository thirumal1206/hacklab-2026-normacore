"""Pick a list of objects one after another into one bin, as fast as possible: no holding check, no retries,
no home in between. Positions are taken once (from one camera frame) and given on the command line.

    .venv/bin/python agent/batch_pick.py bin1 X,Y[,ANGLE] X,Y[,ANGLE] ...  [--go]      (cm, deg; default dry)

Per object: hover (jaws opening on the way), down, close, up, bin drop, open: 6 commands, time per command printed.
Home once at the end. Speed/accel come from motion.json. STOP file (runs/agent/STOP) still stops before any move.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import real  # noqa: E402


def parse(arg):
    v = [float(s) for s in arg.split(",")]
    return v[0], v[1], v[2] if len(v) > 2 else 0.0


def main(argv):
    go = "--go" in argv
    args = [a for a in argv if a != "--go"]
    if len(args) < 2:
        sys.exit(__doc__)
    if not go:
        real.DRY = True
    bin_name, targets = args[0], [parse(a) for a in args[1:]]
    m, f, d = real.free_settings()
    g = m["grasp"]
    zg = d["table_z_m"] + f["grip_z_above_table_cm"] / 100
    hover = zg + f["hover_above_grip_cm"] / 100
    drop = real.bin_pose(bin_name)["drop"]
    none = (0, range(0))  # no joint-jump guard: fast mode
    t0 = time.monotonic()
    for i, (x_cm, y_cm, angle) in enumerate(targets, 1):
        t, times = time.monotonic(), []

        def step(label, fn, *a, **k):  # run one command and note how long it took
            s = time.monotonic()
            out = fn(*a, **k)
            times.append(f"{label} {time.monotonic() - s:.1f}")
            return out

        x, y = x_cm / 100, y_cm / 100
        rpy = (d["grip"]["roll"], d["grip"]["pitch"], real.jaw_yaw(angle, f))
        if not real.DRY:
            real.set_jaw(g["open"])  # no separate open command: the hover move carries gripper = open
        step("hover", real.free_move, x, y, hover, rpy, "hover", None, *none)
        step("down", real.free_move, x, y, zg, rpy, "down", None, *none)
        at = dict(zip(("x", "y", "z", "roll", "pitch", "yaw"), (x, y, zg, *rpy)))
        step("close", real.grip, g["close"], settle=False, at=at)  # no relief step: close and go
        step("up", real.free_move, x, y, hover, rpy, "up", None, *none, loaded=True)
        step("bin", real.move, drop, f"{bin_name} drop", loaded=True)
        step("open", real.grip, g["open"], at=drop)
        print(f"{i}/{len(targets)} ({x_cm:.1f}, {y_cm:.1f}) cm -> {bin_name}  {time.monotonic() - t:.1f} s   "
              f"[{', '.join(times)}]", flush=True)
    real.home()
    print(f"done: {len(targets)} objects in {time.monotonic() - t0:.1f} s")


if __name__ == "__main__":
    main(sys.argv[1:])

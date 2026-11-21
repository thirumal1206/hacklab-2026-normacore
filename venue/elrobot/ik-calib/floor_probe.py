"""Floor probe: find the real table height (tool-point z) at several x positions.

For each X in PROBE_X: hover at z HOVER_Z (gripper closed, yaw 0, y 0), go to START_Z,
then step down STEP_Z at a time. Contact = the measured z stays more than CONTACT_LAG
above the commanded z, or the motion reports stalled, or the API refuses (collision).
Then lift back to HOVER_Z. At the end the arm goes to safety.REST_POSE and holds it.

    uv run python floor_probe.py

E-stop: pull the 12 V follower plug (support the arm).
Result: printed table + logs/floor_probe_<time>.json + a fitted line z_floor = a + b*x.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

import safety
from ik_client import IKClient, IKError
from safety import Target

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")
IK_URL = os.environ.get("IK_URL", "http://127.0.0.1:8020").strip()
FOLLOWER_ID = os.environ.get("FOLLOWER_ID", "5B79015365").strip()

# ---------------------------------------------------------------- ADJUST ----
PROBE_X = [0.15, 0.19, 0.23]  # ADJUST: x positions to probe (m), y = 0
PROBE_Y = float(os.environ.get("PROBE_Y", "0.0"))  # ADJUST: keep the probe line clear of objects
HOVER_Z = 0.070               # ADJUST: travel height between probes
START_Z = 0.045               # ADJUST: fast descent to here, then fine steps
STEP_Z = 0.002                # ADJUST: fine step (m)
MIN_Z = -0.005                # ADJUST: never command below this
CONTACT_LAG = 0.0015          # ADJUST: measured z above commanded z by this = touching
SPEED = 150                   # ADJUST: slower than the agent's default
ACCEL = 8                     # ADJUST
# -----------------------------------------------------------------------------

safety.SPEED = SPEED
safety.ACCEL = ACCEL


def pose(ik: IKClient) -> dict:
    for attempt in range(4):
        try:
            return ik.follower(FOLLOWER_ID)["pose"]["position"]
        except IKError as e:
            if e.code not in ("stale_frame", "no_frame") or attempt == 3:
                raise
            time.sleep(1.0)
    raise RuntimeError("unreachable")


def settle(ik: IKClient, timeout: float = 6.0) -> dict:
    """Read the pose until it stops changing (after a timeout/unknown status)."""
    last, still, t0 = None, 0, time.time()
    p = pose(ik)
    while time.time() - t0 < timeout and still < 3:
        time.sleep(0.3)
        p = pose(ik)
        cur = (p["x"], p["y"], p["z"])
        still = still + 1 if last and max(abs(a - b) for a, b in zip(cur, last)) < 0.0005 else 0
        last = cur
    return p


def move(ik: IKClient, x: float, z: float) -> tuple[str, dict]:
    """Move to (x, PROBE_Y, z), gripper closed. Returns (status, measured pose)."""
    for attempt in range(4):
        try:
            done = ik.move_to(FOLLOWER_ID, Target(x, PROBE_Y, z, 0.0), execute=True, gripper=0.0)
            break
        except IKError as e:
            if e.code not in ("stale_frame", "no_frame") or attempt == 3:
                raise
            print(f"  ({e.code}, retrying)")
            time.sleep(1.0)
    status = done["motion"]["status"]
    time.sleep(0.6)                       # let the servos finish before judging "still"
    p = settle(ik)
    return status, p


def probe_one(ik: IKClient, x: float) -> dict:
    print(f"\n--- x = {x:.3f} ---")
    status, p = move(ik, x, HOVER_Z)
    print(f"hover   cmd z {HOVER_Z:.4f}  meas z {p['z']:.4f}  ({status})")
    status, p = move(ik, x, START_Z)
    print(f"start   cmd z {START_Z:.4f}  meas z {p['z']:.4f}  ({status})")
    if p["z"] - START_Z > CONTACT_LAG:
        return {"x": x, "floor_z": p["z"], "reason": "contact already at START_Z, raise START_Z"}

    z = START_Z
    prev = p["z"]
    result = {"x": x, "floor_z": None, "reason": "no contact down to MIN_Z"}
    while z - STEP_Z >= MIN_Z:
        z = round(z - STEP_Z, 4)
        try:
            status, p = move(ik, x, z)
        except IKError as e:
            p = pose(ik)
            print(f"step    cmd z {z:.4f}  REFUSED: {e.short()}")
            result = {"x": x, "floor_z": None, "api_limit_z": z, "meas_z": p["z"], "reason": f"API refused: {e.code}"}
            break
        lag = p["z"] - z
        drop = prev - p["z"]
        print(f"step    cmd z {z:.4f}  meas z {p['z']:.4f}  lag {lag*1000:+.1f} mm  drop {drop*1000:.1f} mm  ({status})")
        # "stalled" is reported even in free air (gravity sag), so contact = the tool point
        # stops following: it dropped less than 40 % of the commanded step.
        if drop < 0.4 * STEP_Z:
            result = {"x": x, "floor_z": round(p["z"], 4), "cmd_z": z, "sag_mm": round(-lag * 1000, 1),
                      "meas_x": round(p["x"], 4), "reason": "stopped following"}
            break
        prev = p["z"]

    status, p = move(ik, x, HOVER_Z)
    print(f"lift    cmd z {HOVER_Z:.4f}  meas z {p['z']:.4f}  ({status})")
    print(f"=> floor at x {x:.3f}: {result['floor_z']}  ({result['reason']})")
    return result


def main() -> int:
    ik = IKClient(IK_URL)
    try:
        arm = ik.follower(FOLLOWER_ID)
    except IKError as e:
        print(f"Cannot start: {e.short()}")
        return 1
    if not arm["calibrated"]:
        print("Follower is not calibrated, refusing.")
        return 1
    p = arm["pose"]["position"]
    print(f"Follower {arm['id']} torque={arm['torque_enabled']} at x {p['x']:.3f} y {p['y']:.3f} z {p['z']:.3f}")
    print(f"Speed {SPEED}, accel {ACCEL}, step {STEP_Z*1000:.0f} mm. E-stop = 12 V follower plug.")

    results = []
    try:
        # Start from the rest pose. gripper=0.0 also seeds motor 8 inside its limit
        # (a limp gripper can sit below 0 rad, which makes every solve fail with limit_violation).
        done = ik.move_to(FOLLOWER_ID, safety.REST_POSE, execute=True, gripper=0.0)
        print(f"to rest: {done['motion']['status']}  meas {settle(ik, 10.0)}")
        for x in PROBE_X:
            results.append(probe_one(ik, x))
    except (IKError, KeyboardInterrupt) as e:
        print(f"\nStopped: {e.short() if isinstance(e, IKError) else 'Ctrl+C'}")
    finally:
        try:
            q = pose(ik)
            if q["z"] < HOVER_Z - 0.005:
                move(ik, q["x"], HOVER_Z)
            done = ik.move_to(FOLLOWER_ID, safety.REST_POSE, execute=True, gripper=0.0)
            print(f"\nRest pose: {done['motion']['status']} (arm holds it under torque)")
        except IKError as e:
            print(f"\nCould not reach rest pose: {e.short()}  -> support the arm / check state")

    print("\n x      floor_z  reason")
    for r in results:
        print(f" {r['x']:.3f}  {r['floor_z']}  {r['reason']}")
    pts = [(r["x"], r["floor_z"]) for r in results if r["floor_z"] is not None]
    fit = None
    if len(pts) >= 2:
        n = len(pts)
        mx = sum(x for x, _ in pts) / n
        mz = sum(z for _, z in pts) / n
        b = sum((x - mx) * (z - mz) for x, z in pts) / sum((x - mx) ** 2 for x, _ in pts)
        a = mz - b * mx
        fit = {"a": round(a, 5), "b": round(b, 5)}
        print(f"\nFit: z_floor = {a:.4f} + {b:.4f} * x   ({b*100:.1f} mm per 10 cm of reach)")

    out = HERE / "logs" / f"floor_probe_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"results": results, "fit": fit, "speed": SPEED, "step": STEP_Z}, indent=2))
    print(f"Saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

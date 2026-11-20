"""Compare the Rust ports (armcore) with the Python originals on identical inputs. Run: ../.venv/bin/python -I compare.py"""
import statistics as st
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "rust/target/release/armcore"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "elrobot"))
import arm  # noqa: E402
from ik import JOINTS, ElRobotSolver  # noqa: E402

tmp = Path(tempfile.mkdtemp())


def run(*a):
    r = subprocess.run([str(BIN), *map(str, a)], capture_output=True, text=True, check=True)
    return r.stdout, r.stderr.strip()


# ---------- IK ----------
s = ElRobotSolver()
m = s.m
lines = []
for n in JOINTS:
    j = m.joint(n)
    assert np.allclose(m.jnt_pos[j.id], 0), "joint not at body origin"
    b = j.bodyid[0]
    lines.append(" ".join(repr(float(v)) for v in [*m.body_pos[b], *m.body_quat[b], *m.jnt_axis[j.id], *np.rad2deg(m.jnt_range[j.id])]))
(tmp / "chain.txt").write_text("\n".join(lines))
# the Python tool's tip is body Gripper_Base + R @ TOOL; the chain's last body must be that body
assert m.joint(JOINTS[-1]).bodyid[0] == s.body

rng = np.random.default_rng(0)
targets = np.c_[rng.uniform(0.06, 0.26, 300), rng.uniform(-0.15, 0.15, 300), rng.uniform(0.01, 0.09, 300)]
seeds = []
for t in targets:
    q = np.zeros(8)
    q[0] = np.clip(np.degrees(np.arctan2(t[1], t[0])), s.lo[0], s.hi[0])
    seeds.append(q[:7])
(tmp / "targets.txt").write_text("\n".join(" ".join(repr(float(v)) for v in [*t, *sd]) for t, sd in zip(targets, seeds)))

py_res, py_ms = [], []
for rep in range(3):
    t0 = time.perf_counter()
    for t, sd in zip(targets, seeds):
        try:
            q = s._solve(t, None, np.r_[sd, 0.0])
        except RuntimeError:
            q = None
        if rep == 0:
            py_res.append(q)
    py_ms.append((time.perf_counter() - t0) * 1000 / len(targets))
out, err = run("ik", tmp / "chain.txt", tmp / "targets.txt", 50)
rs_res = [None if l.startswith("0") else np.array(l.split()[1:], float) for l in out.strip().splitlines()]
both = [(p, r, t) for p, r, t in zip(py_res, rs_res, targets) if p is not None and r is not None]
tip_err = lambda q, t: np.linalg.norm(s._fk(np.r_[q, 0.0])[0] - t) * 1000
print("== IK, 300 random ElRobot targets, same seed, no multi-seed fallback ==")
print(f"solved: python {sum(p is not None for p in py_res)}/300, rust {sum(r is not None for r in rs_res)}/300, both {len(both)}")
print(f"disagreements on success: {sum((p is None) != (r is None) for p, r in zip(py_res, rs_res))}")
print(f"max joint difference where both solved: {max(np.abs(p[:7] - r).max() for p, r, _ in both):.4f} deg")
print(f"tip error of the Rust solution, checked by Python FK: max {max(tip_err(r, t) for _, r, t in both):.3f} mm (Python's own: max {max(tip_err(p[:7], t) for p, _, t in both):.3f} mm)")
print(f"time per solve: python {st.median(py_ms):.3f} ms, {err}")

# ---------- segmentation ----------
b = arm.SimBackend(seed=3, scene="objects")
A = arm.Arm(b)
A.park()
img = b.image("top")
H, W = img.shape[:2]
P = b.prof
x0 = P["x"][0] + 0.06
corners = np.array([[x0, P["y"][0]], [P["x"][1], P["y"][0]], [P["x"][1], P["y"][1]], [x0, P["y"][1]]], np.float32)
roi = np.zeros((H, W), np.uint8)
cv2.fillPoly(roi, [cv2.perspectiveTransform(corners[None], b.plane_homography("top", 0.0))[0].astype(np.int32)], 1)
(tmp / "img.rgb").write_bytes(np.ascontiguousarray(img).tobytes())
(tmp / "roi.u8").write_bytes(roi.tobytes())


def py_seg():
    lab = cv2.cvtColor(img, cv2.COLOR_RGB2LAB).astype(np.float32)
    d = lab - np.median(lab[roi > 0], axis=0)
    mask = ((np.sqrt((0.5 * d[..., 0]) ** 2 + d[..., 1] ** 2 + d[..., 2] ** 2) > 14) & (roi > 0)).astype(np.uint8)
    mask = cv2.morphologyEx(cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    comps = []
    for c in cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]:
        if cv2.contourArea(c) >= 80:
            mo = cv2.moments(c)
            comps.append((mo["m10"] / mo["m00"], mo["m01"] / mo["m00"]))
    return mask, comps


ts = []
for _ in range(30):
    t0 = time.perf_counter()
    mask, comps = py_seg()
    ts.append((time.perf_counter() - t0) * 1000)
out, err = run("seg", tmp / "img.rgb", tmp / "roi.u8", W, H, 30)
rc = [tuple(map(float, l.split()[1:])) for l in out.splitlines() if l.startswith("comp")]
rmask = int(next(l for l in out.splitlines() if l.startswith("mask_px")).split()[1])
print(f"\n== segmentation, one {W}x{H} frame (sim top camera, parked, seed 3, non-cube scene) ==")
print(f"mask pixels after morphology: python {int(mask.sum())}, rust {rmask}")
print(f"components >= 80 px: python {len(comps)}, rust {len(rc)}")
dist = [min(np.hypot(px - cx, py - cy) for cx, cy, _ in rc) for px, py in comps] if rc else []
print(f"each Python blob's nearest Rust centroid (pixels): {[round(d, 2) for d in dist]}")
print(f"time per frame: python {st.median(ts):.2f} ms, {err}")

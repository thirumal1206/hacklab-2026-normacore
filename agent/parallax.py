"""Height (parallax) correction for the top camera, per position on the table.

robot_homography.json maps pixels to the table plane. An object top h cm above the table is seen where the camera ray
through it hits the table, i.e. pushed away from the point under the camera C by h/Hc of its distance to C:
    seen = true + k (true - C)   with k = h / (Hc - h)      ->   true = C + (seen - C) / (1 + k)
C (camera xy in robot cm) and Hc (camera height) come from agent/parallax_cal.py (a candy on each robot-touched
cross). Without config/parallax.json the old constant offset is used.
"""
import json

import real

PATH = real.CONFIG / "parallax.json"
OBJECT_H_CM = 2.0  # ADJUST per object type: height of the top the camera sees (candy about 2 cm)
FALLBACK_CM = (1.4, 0.1)  # constant offset measured 22.11 with the wrist camera (used without parallax.json)
_cache = {"mtime": None, "data": None}


def load():
    try:
        mtime = PATH.stat().st_mtime
    except OSError:
        return None
    if _cache["mtime"] != mtime:
        _cache["mtime"], _cache["data"] = mtime, json.loads(PATH.read_text())
    return _cache["data"]


def correct(x_cm, y_cm, h_cm=OBJECT_H_CM):
    """Table-plane position seen by the top camera -> true robot xy of an object top h_cm high."""
    p = load()
    if p is None:
        return x_cm + FALLBACK_CM[0], y_cm + FALLBACK_CM[1]
    cx, cy = p["camera_xy_cm"]
    s = (p["camera_height_cm"] - h_cm) / p["camera_height_cm"]  # = 1 / (1 + k)
    return cx + (x_cm - cx) * s, cy + (y_cm - cy) * s

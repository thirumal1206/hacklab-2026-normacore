"""Object catalog: the things that can be on the table, so labels are instant and always the same few names.

    .venv/bin/python agent/catalog.py                                   list the catalog
    .venv/bin/python agent/catalog.py add "black battery pack" battery   teach: only that object on the table (or add its id)

agent/config/objects.json (edit it freely, it is reloaded when it changes):
    {"objects": [{"name": "red candy", "category": "candy", "colors": ["red"], "size_cm": [3.0, 9.0]}, ...],
     "size_tolerance": 0.35}
colors: any of COLORS (what color_name() reports for the top camera). size_cm: [width, length] as live vision measures
it (outline incl. wings and shadow). An object matches an entry when its colour is listed and width and length are both
within size_tolerance (fraction) of the entry; the closest size wins. No match: None (Claude names it later).
"""
import json

import cv2
import numpy as np

import real

FILE = real.CONFIG / "objects.json"
COLORS = ("red", "orange", "yellow", "green", "blue", "purple", "black", "white", "grey")
SAT_MIN, VAL_MIN = 70, 50  # a pixel this saturated and bright has a hue worth naming
COLORED_SHARE = 0.25  # this share of saturated pixels makes the object "coloured"

_cache = {"mtime": None, "data": {"objects": []}}


def load():
    try:
        mtime = FILE.stat().st_mtime
    except OSError:
        return {"objects": []}
    if mtime != _cache["mtime"]:
        try:
            _cache.update(mtime=mtime, data=json.loads(FILE.read_text()))
        except ValueError as e:  # a half-saved edit: keep the last good catalog
            print(f"catalog: {FILE.name}: {e}", flush=True)
    return _cache["data"]


def names():
    return [o["name"] for o in load().get("objects", [])]


def category(name):
    return next((o.get("category", "other") for o in load().get("objects", []) if o["name"] == name), None)


def color_name(img, contour):
    """Dominant colour inside a pixel contour of the BGR frame."""
    x, y, w, h = cv2.boundingRect(contour)
    mask = np.zeros((h, w), np.uint8)
    cv2.drawContours(mask, [contour - [x, y]], -1, 255, -1)
    mask = cv2.erode(mask, np.ones((5, 5), np.uint8))  # not the paper at the edge
    hsv = cv2.cvtColor(img[y:y + h, x:x + w], cv2.COLOR_BGR2HSV)[mask > 0]
    if len(hsv) == 0:
        return "grey"
    sat = hsv[(hsv[:, 1] > SAT_MIN) & (hsv[:, 2] > VAL_MIN)]
    if len(sat) >= COLORED_SHARE * len(hsv):
        hue = sat[:, 0].astype(int)
        votes = {"red": np.sum((hue < 8) | (hue >= 165)), "orange": np.sum((hue >= 8) & (hue < 20)),
                 "yellow": np.sum((hue >= 20) & (hue < 35)), "green": np.sum((hue >= 35) & (hue < 85)),
                 "blue": np.sum((hue >= 85) & (hue < 130)), "purple": np.sum((hue >= 130) & (hue < 165))}
        return max(votes, key=votes.get)
    v = float(np.median(hsv[:, 2]))
    return "black" if v < 80 else "white" if v > 185 else "grey"


def match(color, width_cm, length_cm):
    """-> (name, category) of the best catalog entry, or None."""
    data = load()
    tol = float(data.get("size_tolerance", 0.35))
    best = None
    for o in data.get("objects", []):
        if color not in o.get("colors", []):
            continue
        w, l = sorted(o["size_cm"])
        dw, dl = abs(width_cm - w) / w, abs(length_cm - l) / l
        t = float(o.get("tolerance", tol))  # per entry, else the catalog's
        if dw <= t and dl <= t and (best is None or dw + dl < best[0]):
            best = (dw + dl, o["name"], o.get("category", "other"))
    return best and (best[1], best[2])


def add(name, cat, obj_id=None):
    """Teach an entry from what live vision sees now: the only object on the table, or the one with obj_id."""
    import live_vision  # here, not at the top: live_vision imports this module
    objs = live_vision.world(max_age_s=2.0)
    if objs is None:
        raise SystemExit("live vision is not running")
    if obj_id is not None:
        objs = [o for o in objs if o["id"] == obj_id]
    if len(objs) != 1:
        raise SystemExit(f"need exactly one object, see {len(objs)}: put only this one on the table, or give its id")
    o = objs[0]
    data = json.loads(FILE.read_text()) if FILE.exists() else {"objects": [], "size_tolerance": 0.35}
    data["objects"] = [e for e in data["objects"] if e["name"] != name]
    data["objects"].append({"name": name, "category": cat, "colors": [o["color"]],
                            "size_cm": [round(o["width_cm"], 1), round(o["length_cm"], 1)]})
    FILE.write_text(json.dumps(data, indent=1) + "\n")
    print(f"{name}: {o['color']}, {o['width_cm']:.1f} x {o['length_cm']:.1f} cm ({cat}) -> {FILE}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 4 and sys.argv[1] == "add":
        add(sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else None)
    else:
        for e in load().get("objects", []):
            print(f"  {e['name']:<24} {e.get('category', 'other'):<10} {'/'.join(e['colors']):<12} {e['size_cm'][0]} x {e['size_cm'][1]} cm")
        print("teach one: put only it on the table, then  .venv/bin/python agent/catalog.py add \"black battery pack\" battery")

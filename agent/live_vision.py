"""Live vision: finds objects on the paper about 5 times a second, tracks them with stable ids and writes the world state.

    .venv/bin/python agent/live_vision.py              run; live page on http://127.0.0.1:8771, state in runs/agent/world.json
    .venv/bin/python agent/live_vision.py background   store an empty-table photo (clear the table first): best detection
    .venv/bin/python agent/live_vision.py once         one detection, print it, save runs/agent/world.jpg

Detection (no outlines, so a gap in an edge cannot lose an object):
  - background mode (runs/agent/background.jpg exists): object = differs from the empty-table photo
  - paper mode (default): object = coloured, clearly darker than the paper around it, or textured (print); glare ignored
  Thin drawn lines and tape crosses are removed by an opening filter; anything touching the paper border (mug, box,
  the arm reaching in) is dropped. Pixels -> robot cm with robot_homography.json plus parallax.correct.
Labels (LABELS=off disables): objects that stay put LABEL_AFTER_S get a short label and category from Claude (one marked
image per batch, in a background thread, claude_cli.MODEL_LABEL), kept per track id; the 5 Hz loop never waits for it.
Tracking: a detection within MATCH_CM of a track updates it (averaged); a track is "confirmed" after CONFIRM sightings
and dropped after DROP_S without one. Ids stay stable while an object stays put or moves a little.
"""
import json
import math
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import batch_pick  # noqa: E402  (camera id, paper polygon, size and reach filters)
import claude_cli  # noqa: E402
import parallax  # noqa: E402
import real  # noqa: E402

WORLD = real.RUNS / "world.json"
OVERLAY = real.RUNS / "world.jpg"
BACKGROUND = real.RUNS / "background.jpg"
PORT = int(os.environ.get("VISION_PORT", "8771"))
HZ = 5.0
MATCH_CM = 2.5  # a detection this close to a track is the same object
CONFIRM = 3  # sightings before an object counts
DROP_S = 1.0  # unseen this long: gone
ALPHA = 0.5  # weight of the newest detection in the averaged position
MIN_AREA_PX = 250  # at 1280x960; a candy is about 1500-3000 px
# ADJUST if detection is too eager or too blind
SAT_MIN = 70  # paper mode: saturation that counts as "coloured"
PAPER_DELTA = 25  # paper mode: this much darker than the local paper counts
GLARE_V = 225  # paper mode: brighter than this and colourless = reflection, ignored
FRESH_S = 0.5  # world.json lists only objects seen this recently (no ghosts of moved objects); 2 frames at 5 Hz
TEXTURE_MIN = 14  # paper mode: printed wrappers are busy, paper is smooth (blurred |Laplacian|)
LABEL_AFTER_S = 1.5  # an object must stay this long before it is labelled (not a hand passing by)
LABEL_TRIES = 2
LABEL_FRAME = real.RUNS / "label_frame.jpg"
CATEGORIES = ("candy", "battery", "pen", "tool", "food", "packaging", "toy", "other")
BG_DELTA = 35  # background mode: colour difference that counts


def grab():
    img = cv2.imdecode(np.frombuffer(real.api("GET", f"/cameras/{batch_pick.CAMERA}/image"), np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("cannot decode camera image")
    return img


_bg = {"mtime": None, "img": None}


def background():
    """The empty-table photo, loaded once (again only when the file changes); None in paper mode."""
    try:
        mtime = BACKGROUND.stat().st_mtime
    except OSError:
        return None
    if mtime != _bg["mtime"]:
        _bg.update(mtime=mtime, img=cv2.imread(str(BACKGROUND)))
    return _bg["img"]


def object_mask(img, paper):
    bg = background()
    if bg is not None and bg.shape != img.shape:
        raise RuntimeError(f"background.jpg is {bg.shape[1]}x{bg.shape[0]}, camera {img.shape[1]}x{img.shape[0]}: retake it")
    if bg is not None:
        a, b = cv2.GaussianBlur(img, (5, 5), 0).astype(np.int16), cv2.GaussianBlur(bg, (5, 5), 0).astype(np.int16)
        m = (np.abs(a - b).max(axis=2) > BG_DELTA).astype(np.uint8) * 255
    else:
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        local = cv2.medianBlur(gray, 51)  # paper brightness around each pixel (handles shading)
        # glare (shiny tape, reflections): very bright and colourless; never an object, and its edges are not "print"
        glare = cv2.dilate(((hsv[..., 2] > GLARE_V) & (hsv[..., 1] < 40)).astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
        lap = np.abs(cv2.Laplacian(cv2.GaussianBlur(gray, (3, 3), 0), cv2.CV_16S, ksize=3)).astype(np.float32)
        textured = ((cv2.blur(lap, (5, 5)) > TEXTURE_MIN) & ~glare).astype(np.uint8) * 255
        darker = local.astype(np.int16) - gray.astype(np.int16) > PAPER_DELTA  # only darker: brighter is glare
        plain = ((hsv[..., 1] > SAT_MIN) | darker).astype(np.uint8) * 255
        # open each cue on its own: a pen line is a few px wide in "plain" and about 8 px in "textured"
        m = cv2.morphologyEx(plain, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8)) | \
            cv2.morphologyEx(textured, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    if bg is not None:
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))  # removes specks
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((11, 11), np.uint8))  # joins a wrapper's wings to its body
    return cv2.bitwise_and(m, paper)


def detect(img, H, paper, poly):
    """-> list of dicts in robot cm (height offset applied), plus the pixel contours for drawing."""
    out = []
    contours, _ = cv2.findContours(object_mask(img, paper), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in contours:
        if cv2.contourArea(c) < MIN_AREA_PX:
            continue
        if any(cv2.pointPolygonTest(poly, (float(px), float(py)), True) <= 4 for px, py in c[:, 0, :]):
            continue  # touches the paper border: mug, box, arm
        xy = cv2.perspectiveTransform(c.astype(np.float64), H).astype(np.float32) * 100
        (cx, cy), (w, l), ang = cv2.minAreaRect(xy)
        cx, cy = parallax.correct(cx, cy)  # object top above the table, per position
        long_axis = (ang + (90 if l > w else 0) + 90) % 180 - 90
        width, length = min(w, l), max(w, l)
        jaw = (long_axis + 90) % 180 if length > 1.1 * width else 0.0
        d = {"x_cm": cx, "y_cm": cy, "jaw_deg": jaw, "width_cm": width, "length_cm": length, "contour": c}
        out.append(judge(d))
    return out


def judge(d):
    """Set pickable/why from size and reach (the tracker calls it again on the averaged values)."""
    width, length, dist = d["width_cm"], d["length_cm"], math.hypot(d["x_cm"], d["y_cm"])
    why = ("too wide" if width > batch_pick.MAX_WIDTH_CM else "too thin" if width < batch_pick.MIN_WIDTH_CM else
           "too small" if length < batch_pick.LENGTH_CM[0] else "too long" if length > batch_pick.LENGTH_CM[1] else
           "out of reach" if not batch_pick.REACH_CM[0] <= dist <= batch_pick.REACH_CM[1] else None)
    d.update(pickable=why is None, why=why)
    return d


class Tracker:
    def __init__(self):
        self.tracks, self.next_id = [], 1

    def update(self, dets, now):
        free = list(range(len(dets)))
        for t in sorted(self.tracks, key=lambda t: -t["hits"]):  # established tracks choose first
            best = min(free, key=lambda i: math.hypot(dets[i]["x_cm"] - t["x_cm"], dets[i]["y_cm"] - t["y_cm"]), default=None)
            if best is None or math.hypot(dets[best]["x_cm"] - t["x_cm"], dets[best]["y_cm"] - t["y_cm"]) > MATCH_CM:
                continue
            d = dets[best]
            free.remove(best)
            for k in ("x_cm", "y_cm", "width_cm", "length_cm"):
                t[k] = (1 - ALPHA) * t[k] + ALPHA * d[k]
            dj = (d["jaw_deg"] - t["jaw_deg"] + 90) % 180 - 90  # average angles modulo 180
            t["jaw_deg"] = (t["jaw_deg"] + ALPHA * dj) % 180
            t.update(hits=t["hits"] + 1, seen=now, contour=d["contour"])
            judge(t)  # on the averaged size: no flicker at a limit
        for i in free:
            self.tracks.append({**dets[i], "id": self.next_id, "hits": 1, "first": now, "seen": now})
            self.next_id += 1
        self.tracks = [t for t in self.tracks if now - t["seen"] <= DROP_S]
        return [t for t in self.tracks if t["hits"] >= CONFIRM]


class Labeler:
    """Claude labels for confirmed tracks, one call at a time in a background thread."""

    def __init__(self):
        self.labels, self.tries, self.busy = {}, {}, False
        self.enabled = os.environ.get("LABELS", "on") != "off"

    def maybe_start(self, img, poly, objs, now):
        if not self.enabled or self.busy:
            return
        todo = [t for t in objs if t["id"] not in self.labels and self.tries.get(t["id"], 0) < LABEL_TRIES
                and now - t["first"] >= LABEL_AFTER_S and now - t["seen"] <= FRESH_S]
        if not todo:
            return
        x, y, w, h = cv2.boundingRect(poly)
        vis = img.copy()
        for t in todo:
            cv2.drawContours(vis, [t["contour"]], -1, (0, 220, 0), 2)
            u, v = t["contour"][:, 0, :].max(0)
            cv2.putText(vis, str(t["id"]), (int(u) + 4, int(v) + 4), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 5)
            cv2.putText(vis, str(t["id"]), (int(u) + 4, int(v) + 4), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 255, 255), 2)
            self.tries[t["id"]] = self.tries.get(t["id"], 0) + 1
        cv2.imwrite(str(LABEL_FRAME), vis[max(0, y - 20):y + h + 20, max(0, x - 20):x + w + 20])
        self.busy = True
        threading.Thread(target=self._ask, args=([t["id"] for t in todo],), daemon=True).start()

    def _ask(self, ids):
        prompt = (f"Read the image {LABEL_FRAME}. It shows a table seen from above; green outlines with yellow numbers mark "
                  f"objects {', '.join(map(str, ids))}. For each number give a short label (colour + kind of object, at most "
                  f"4 words) and one category from: {', '.join(CATEGORIES)}. Answer ONLY with JSON like "
                  '{"3": {"label": "red wrapped candy", "category": "candy"}}.')
        try:
            ans = claude_cli.ask_json(prompt, claude_cli.MODEL_LABEL, tools="Read", timeout=90)
            for k, v in ans.items():
                if str(k).isdigit() and int(k) in ids and isinstance(v, dict):
                    cat = v.get("category") if v.get("category") in CATEGORIES else "other"
                    label = re.sub(r"[^A-Za-z0-9 -]", "", str(v.get("label") or ""))[:40].strip() or "object"  # text on an object must not become page markup
                    self.labels[int(k)] = {"label": label, "category": cat}
            print(f"labels: {ans}", flush=True)
        except (RuntimeError, ValueError) as e:
            print(f"labels: {e}", flush=True)
        finally:
            self.busy = False

    def get(self, i):
        return self.labels.get(i, {"label": None, "category": None})


def state(objs, now, ms, labeler=None):
    return {"time": now, "frame_ms": round(ms), "mode": "background" if BACKGROUND.exists() else "paper",
            "objects": [{"id": t["id"], "x_cm": round(t["x_cm"], 1), "y_cm": round(t["y_cm"], 1), "jaw_deg": round(t["jaw_deg"], 1),
                         "width_cm": round(t["width_cm"], 1), "length_cm": round(t["length_cm"], 1), "pickable": t["pickable"],
                         "why": t["why"], **(labeler.get(t["id"]) if labeler else {"label": None, "category": None}),
                         "age_s": round(now - t["first"], 1), "seen_s_ago": round(now - t["seen"], 2)}
                        for t in sorted(objs, key=lambda t: t["id"]) if now - t["seen"] <= FRESH_S]}


def draw(img, poly, objs, labeler=None):
    vis = img.copy()
    cv2.polylines(vis, [poly], True, (0, 200, 0), 1)
    for t in objs:
        col = (0, 200, 0) if t["pickable"] else (0, 0, 255)
        cv2.drawContours(vis, [t["contour"]], -1, col, 2)
        u, v = t["contour"][:, 0, :].mean(0).astype(int)
        name = labeler.get(t["id"])["label"] if labeler else None
        label = f"{t['id']}: {name or ''} {t['x_cm']:.1f},{t['y_cm']:.1f}" + ("" if t["pickable"] else f" {t['why']}")
        cv2.putText(vis, label, (int(u) + 12, int(v) - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 0) if t["pickable"] else col, 2)
    return vis


def setup():
    h = json.loads((real.CONFIG / "robot_homography.json").read_text())
    poly = np.int32(batch_pick.PAPER_PX)
    w, hgt = h["image_size"]
    paper = np.zeros((hgt, w), np.uint8)
    cv2.fillConvexPoly(paper, poly, 255)
    return np.array(h["H_pixel_to_xy"]), paper, poly, h["image_size"]


def write_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)  # readers never see a half-written file


PAGE = """<!doctype html><meta charset="utf-8"><title>Live vision</title>
<style>body{font:14px system-ui;margin:12px;background:#111;color:#eee}img{max-width:100%;border-radius:6px}
table{border-collapse:collapse;margin-top:8px}td,th{padding:3px 10px;border-bottom:1px solid #333;text-align:right}</style>
<div id="h"></div><img id="i"><table id="t"></table>
<script>
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function tick(){const s=await (await fetch('/world')).json();
document.getElementById('h').textContent=`${s.objects.length} objects (${s.objects.filter(o=>o.pickable).length} pickable) · ${s.mode} mode · ${s.frame_ms} ms/frame`;
document.getElementById('i').src='/world.jpg?'+Date.now();
document.getElementById('t').innerHTML='<tr><th>id</th><th>x cm</th><th>y cm</th><th>jaw °</th><th>w×l cm</th><th>label</th><th>age s</th><th></th></tr>'+
 s.objects.map(o=>`<tr><td>${esc(o.id)}</td><td>${esc(o.x_cm)}</td><td>${esc(o.y_cm)}</td><td>${esc(o.jaw_deg)}</td><td>${esc(o.width_cm)}×${esc(o.length_cm)}</td><td style="text-align:left">${esc(o.label)}${o.category?' ('+esc(o.category)+')':''}</td><td>${esc(o.age_s)}</td><td>${o.pickable?'':esc(o.why)}</td></tr>`).join('')}
tick();setInterval(tick,400);
</script>"""


class Page(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            body, ctype = PAGE.encode(), "text/html; charset=utf-8"
        elif path == "/world" and WORLD.exists():
            body, ctype = WORLD.read_bytes(), "application/json"
        elif path == "/world.jpg" and OVERLAY.exists():
            body, ctype = OVERLAY.read_bytes(), "image/jpeg"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def run():
    H, paper, poly, size = setup()
    real.RUNS.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", PORT), Page).serve_forever, daemon=True).start()
    print(f"live vision: http://127.0.0.1:{PORT}  ({'background' if BACKGROUND.exists() else 'paper'} mode), Ctrl-C to stop", flush=True)
    tr, last_img, lab = Tracker(), 0.0, Labeler()
    while True:
        t0 = time.monotonic()
        try:
            img = grab()
            if list(img.shape[1::-1]) != size:
                raise RuntimeError(f"frame {img.shape[1]}x{img.shape[0]} differs from the mapping {size}")
            objs = tr.update(detect(img, H, paper, poly), time.time())
            write_json(WORLD, state(objs, time.time(), (time.monotonic() - t0) * 1000, lab))
            lab.maybe_start(img, poly, objs, time.time())
            if time.monotonic() - last_img > 0.4:
                cv2.imwrite(str(OVERLAY.with_suffix(".tmp.jpg")), draw(img, poly, objs, lab))
                OVERLAY.with_suffix(".tmp.jpg").replace(OVERLAY)
                last_img = time.monotonic()
        except (OSError, RuntimeError, ValueError, cv2.error) as e:  # log and keep going; world.json goes stale
            print(f"vision: {e}", flush=True)
            time.sleep(0.5)
        time.sleep(max(0.0, 1 / HZ - (time.monotonic() - t0)))


def world(max_age_s=1.0):
    """For the arm worker: the latest confirmed objects (each seen within FRESH_S), or None if the service is not running."""
    try:
        s = json.loads(WORLD.read_text())
    except (OSError, ValueError):
        return None
    return s["objects"] if time.time() - s["time"] <= max_age_s else None


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "background":
        cv2.imwrite(str(BACKGROUND), grab())
        print(f"empty-table photo saved: {BACKGROUND} (delete it to go back to paper mode)")
    elif cmd == "once":
        H, paper, poly, _ = setup()
        img = grab()
        dets = detect(img, H, paper, poly)
        for i, d in enumerate(dets, 1):
            d["id"] = i
            print(f"  {i}: ({d['x_cm']:5.1f}, {d['y_cm']:5.1f}) cm  {d['width_cm']:.1f} x {d['length_cm']:.1f}  "
                  f"jaw {d['jaw_deg']:5.1f}  {d['why'] or 'pickable'}")
        cv2.imwrite(str(OVERLAY), draw(img, poly, dets))
        print(f"{sum(d['pickable'] for d in dets)} pickable of {len(dets)}, image {OVERLAY}")
    else:
        try:
            run()
        except KeyboardInterrupt:
            print("stopped")

"""Click the jaw tips in the photos record_points.py took at each cross; writes cross_pixels.csv for `record_points.py fit`.

    AGENT_PROFILE=thiru .venv/bin/python agent/mark_crosses.py      then open http://127.0.0.1:5071
"""
import csv
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import real  # noqa: E402

PTS = real.CONFIG / "robot_points.json"
pts = json.loads(PTS.read_text())
crosses = [p for p in pts["points"] if p["name"].startswith("cross_") and p["pose"] and (real.CONFIG / f"{p['name']}.jpg").exists()]
W, H = pts["cross_image_size"]
PAGE = """<!doctype html><meta charset=utf-8><title>Mark the crosses</title>
<style>body{font:15px system-ui;margin:20px;background:#111;color:#eee}img{width:960px;max-width:100%;cursor:crosshair;display:block;margin-top:8px}</style>
<div id=t></div><img id=i><script>
const N=__N__;let k=0,clicks={};
function show(){if(k>=N.length){fetch('done',{method:'POST',body:JSON.stringify(clicks)}).then(r=>r.text()).then(x=>document.body.innerHTML='<h2>'+x+'</h2>');return}
 document.getElementById('t').textContent=`${k+1}/${N.length}: click the CENTRE OF THE JAW TIPS (${N[k]})`;document.getElementById('i').src=N[k]+'.jpg?'+k}
document.getElementById('i').onclick=e=>{const r=e.target.getBoundingClientRect(),im=e.target;
 clicks[N[k]]=[(e.clientX-r.left)/r.width,(e.clientY-r.top)/r.height];k++;show()};show()</script>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        name = self.path.split("?")[0].lstrip("/")
        body, ctype = PAGE.replace("__N__", json.dumps([c["name"] for c in crosses])).encode(), "text/html"
        if name.endswith(".jpg") and (real.CONFIG / name).exists() and name.startswith("cross_"):
            body, ctype = (real.CONFIG / name).read_bytes(), "image/jpeg"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        clicks = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        out = Path(real.HERE.parent / pts["cross_pixels_csv"])
        with out.open("w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["cross", "u_px", "v_px", "a_distance_mm", "b_distance_mm", "estimated_robot_x_mm", "estimated_robot_y_mm"])
            for c in crosses:
                if c["name"] in clicks:
                    fu, fv = clicks[c["name"]]
                    w.writerow([c["name"].split("_")[1], round(fu * W, 1), round(fv * H, 1), "", "",
                                round(c["pose"]["x"] * 1000, 1), round(c["pose"]["y"] * 1000, 1)])
        msg = f"saved {len(clicks)} crosses to {out.name}. Now run: AGENT_PROFILE={real.PROFILE} .venv/bin/python agent/record_points.py fit"
        print(msg, flush=True)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(msg.encode())


if __name__ == "__main__":
    if not crosses:
        sys.exit("no cross photos yet: record the crosses with record_points.py first")
    print(f"{len(crosses)} cross photos: open http://127.0.0.1:5071", flush=True)
    HTTPServer(("127.0.0.1", 5071), Handler).serve_forever()

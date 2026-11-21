"""Read-only free-placement preview. Motion remains blocked pending an approved executor."""
import json
import os
import math
from pathlib import Path

import cv2
import numpy as np

import real
from vision_targets import targets

HERE = Path(__file__).parent


def reach_reasons(obj, profile):
    reasons = []
    if not profile.get("motion_verified") or not profile.get("execution_enabled"):
        reasons.append("calibration or execution is provisional")
    polygon = profile.get("verified_pick_polygon_xy_cm")
    margin = profile.get("boundary_margin_cm")
    footprint = np.asarray(obj.get("footprint_xy_cm", []), dtype=np.float32)
    if footprint.shape != (4, 2) or not np.isfinite(footprint).all():
        return reasons + ["invalid object footprint"]
    if not polygon or margin is None:
        reasons.append("verified pick boundary and clearance are missing")
    else:
        boundary = np.asarray(polygon, dtype=np.float32)
        if (boundary.ndim != 2 or boundary.shape[1] != 2 or len(boundary) < 3
                or not np.isfinite(boundary).all() or not cv2.isContourConvex(boundary)
                or not math.isfinite(margin) or margin < 0):
            reasons.append("invalid verified boundary")
        elif any(cv2.pointPolygonTest(boundary, tuple(map(float, p)), True) < margin for p in footprint):
            reasons.append("object footprint crosses the pick boundary or clearance")
    opening = profile.get("detection", {}).get("gripper_max_opening_cm")
    width = obj.get("width_cm")
    if opening is None or width is None or not math.isfinite(width) or not 0 < width <= opening:
        reasons.append("gripper fit is unverified or object is too wide")
    if obj.get("height_cm") is None:
        reasons.append("object height is unknown")
    if obj.get("angle") is None:
        reasons.append("object orientation is ambiguous")
    return reasons


class Backend:
    def __init__(self):
        self.profile_path = Path(os.environ.get(
            "AGENT_VISION_PROFILE", HERE / "config/provisional/free-placement.provisional.json"))
        self.last = {}

    def scan(self):
        profile = json.loads(self.profile_path.read_text())
        calibration = json.loads((self.profile_path.parent / profile["calibration"]["file"]).read_text())
        camera = profile["cameras"]["top_id"]
        if camera != calibration["camera_id"]:
            raise ValueError("Camera and calibration IDs differ")
        image = cv2.imdecode(np.frombuffer(real.api("GET", f"/cameras/{camera}/image"), np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("Cannot decode external camera frame")
        self.last = targets(image, profile, calibration)
        for obj in self.last["provisional_detections"]:
            obj["reach_reasons"] = reach_reasons(obj, profile) + [
                "free-placement motion adapter and camera-bump verification are pending"]
        return []

    def diagnostics(self):
        self.scan()
        return self.last

    def camera_bumped(self):
        return False  # Preview exposes no executable targets; motion requires a real bump check.

    def validate_target(self, obj):
        return "Free-placement preview cannot execute motion"

    def crop(self, pack_id):
        raise RuntimeError("No validated free-placement read pose")

    def pick(self, *args):
        raise RuntimeError("No validated free-placement motion adapter")

    def plan_pick(self, object_id, bin_name):
        """Build a dry-run plan from a scan-owned target and taught destination."""
        import time
        import copy
        previous = self.last
        matches = [o for o in previous.get("provisional_detections", []) if o["id"] == object_id]
        if len(matches) != 1:
            return {"error": "Unknown target; call vision_preview again"}
        if time.time() - previous["captured_at_unix"] > 15:
            return {"error": "Stale scan; call vision_preview again"}
        target = copy.deepcopy(matches[0])
        profile = json.loads(self.profile_path.read_text())
        self.scan()
        current = self.last["provisional_detections"]
        nearby = [o for o in current if math.hypot(o["x_cm"]-target["x_cm"], o["y_cm"]-target["y_cm"]) <= 0.3]
        if len(nearby) != 1:
            return {"error": "Target moved or is ambiguous; rescan"}
        fresh = nearby[0]
        if target["angle"] is None or fresh["angle"] is None:
            return {"error": "Ambiguous orientation"}
        angle_diff = abs((fresh["angle"]-target["angle"]+90) % 180-90)
        if angle_diff > 5 or abs(fresh["width_cm"]-target["width_cm"]) > 0.3:
            return {"error": "Target changed; rescan"}
        return build_plan(fresh, bin_name, profile, real.cfg("motion.json"))


def build_plan(target, bin_name, profile, motion):
    """Coordinates are meters/radians at the IK boundary; no API writes here."""
    bins = [b for b in motion["poses"]["bins"] if b["name"] == bin_name]
    if len(bins) != 1:
        return {"error": "Unknown destination bin"}
    reasons = reach_reasons(target, profile)
    if not target.get("inside_calibration_support"):
        reasons.append("target extends outside calibration support")
    hover = profile.get("hover_height_cm")
    grasp = profile.get("grasp_height_cm")
    offset = profile.get("jaw_yaw_offset_deg")
    if hover is None or grasp is None or not math.isfinite(hover) or not math.isfinite(grasp) or not hover > grasp >= 0:
        reasons.append("hover and grasp heights need measurement")
        hover, grasp = None, None
    if offset is None or not math.isfinite(offset):
        reasons.append("jaw orientation relative to object needs measurement")
        offset = None
    yaw = None if offset is None or target["angle"] is None else math.radians(target["angle"] + offset)
    def pose(height):
        return {"x": target["x_cm"]/100, "y": target["y_cm"]/100,
                "z": None if height is None else height/100,
                "roll": -math.pi/2, "pitch": 0, "yaw": yaw}
    steps = [{"action":"move", "name":"home", "pose":motion["poses"]["home"]},
             {"action":"open"},
             {"action":"move", "name":"pickup_hover", "pose":pose(hover)},
             {"action":"move", "name":"pickup_grasp", "pose":pose(grasp)},
             {"action":"close"}, {"action":"verify_holding"},
             {"action":"move", "name":"pickup_lift", "pose":pose(hover)}]
    for step in motion["sequences"]["place"]:
        if step.startswith("bin."):
            name = step[4:]
            steps.append({"action":"move","name":"bin_"+name,"pose":bins[0].get(name)})
        elif step == "home":
            steps.append({"action":"move","name":"home","pose":motion["poses"]["home"]})
        elif step in ("open","close") or step.startswith("wait:"):
            steps.append({"action":step})
        else:
            return {"error": "Unsupported destination sequence step: " + step}
    for step in steps:
        if step["action"] == "move" and step["pose"] is None:
            reasons.append("untaught pose: "+step["name"])
    reasons += ["IK path, joint continuity and camera stability need validation"]
    return {"dry_run":True,"executable":False,"target":target,"bin":bin_name,
            "steps":steps,"blockers":reasons}

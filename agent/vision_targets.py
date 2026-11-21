"""Detect generic object candidates and estimate their projected table-plane geometry."""
import argparse
import time
from urllib.request import urlopen

import cv2
import numpy as np

URL = "http://127.0.0.1:8020/api/v1/cameras/835446b047547e89f1f2e588eedf268b/image"


def detect_outlines(frame, corners, min_area_px=150):
    x, y, w, h = cv2.boundingRect(corners)
    polygon = corners - np.array([x, y], dtype=np.int32)
    gray = cv2.cvtColor(frame[y:y+h, x:x+w], cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 24, 72)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    mask = np.zeros_like(edges)
    cv2.fillConvexPoly(mask, polygon, 255)
    mask = cv2.erode(mask, np.ones((5, 5), np.uint8),
                     borderType=cv2.BORDER_CONSTANT, borderValue=0)
    edges = cv2.bitwise_and(edges, mask)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_area_px:
            continue
        if any(cv2.pointPolygonTest(polygon, (float(px), float(py)), True) <= 3
               for px, py in contour[:, 0, :]):
            continue
        candidates.append(contour.astype(np.float32) + np.array([[[x, y]]], dtype=np.float32))
    return candidates


def transform_points(points, matrix, scale):
    scaled = points.astype(np.float64) * scale
    denominator = scaled[..., 0] * matrix[2, 0] + scaled[..., 1] * matrix[2, 1] + matrix[2, 2]
    if np.any(np.abs(denominator) < 1e-8):
        raise ValueError("Invalid homography projection")
    return cv2.perspectiveTransform(scaled, matrix)


def targets(frame, profile, calibration):
    import uuid
    if list(frame.shape[1::-1]) != calibration["native_image_size"]:
        raise ValueError("Camera resolution differs from calibration")
    corners = np.array(profile["search_polygon_native_px"], dtype=np.int32)
    matrix = np.array(calibration["H_pixel_to_xy"], dtype=np.float64)
    scale = profile["calibration"]["native_pixel_scale"]
    support = np.array(profile["candidate_pick_polygon_xy_cm"], dtype=np.float32)
    scan_id = uuid.uuid4().hex
    objects = []
    for index, outline in enumerate(detect_outlines(frame, corners, profile.get("detection", {}).get("min_area_px", 150))):
        robot_outline = (transform_points(outline, matrix, scale) * 100).astype(np.float32)
        center, (a, b), angle = cv2.minAreaRect(robot_outline)
        rectangle = cv2.boxPoints((center, (a, b), angle))
        inside = all(cv2.pointPolygonTest(support, tuple(map(float, p)), False) >= 0
                     for p in rectangle)
        yaw = (angle + (90 if b > a else 0) + 90) % 180 - 90
        objects.append({
            "id": f"{scan_id}:{index}", "x_cm": round(center[0], 3),
            "y_cm": round(center[1], 3), "angle": round(yaw, 2) if max(a, b) > 1.1 * min(a, b) else None,
            "width_cm": round(min(a, b), 3), "length_cm": round(max(a, b), 3),
            "footprint_xy_cm": rectangle.tolist(), "height_class": None, "height_cm": None,
            "geometry_frame": "projection_onto_calibration_plane",
            "grasp_verified": False,
            "inside_calibration_support": inside,
            "in_reach": False,
            "reason": "Physical calibration, camera-bump check and motion feasibility require verification"
        })
    return {
        "scan_id": scan_id, "captured_at_unix": time.time(),
        "mode": "provisional_diagnostic", "objects": [],
        "provisional_detections": objects,
        "angle_convention": "Bounding-box long axis in robot XY, degrees from +X toward +Y, modulo 180"
    }


def main():
    import json
    from pathlib import Path
    parser = argparse.ArgumentParser(description="OpenCV image-to-robot targets for motion-team review")
    parser.add_argument("--image", help="Saved image instead of live capture")
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--profile", type=Path,
                        default=Path(__file__).with_name("free-placement.provisional.json"))
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text())
    calibration = json.loads((args.profile.parent / profile["calibration"]["file"]).read_text())
    if profile["cameras"]["top_id"] != calibration["camera_id"]:
        raise ValueError("Profile and calibration camera IDs differ")
    url = f"http://127.0.0.1:8020/api/v1/cameras/{calibration['camera_id']}/image"
    deadline = time.monotonic() + args.seconds
    while True:
        start = time.perf_counter()
        try:
            if args.image:
                frame = cv2.imread(args.image)
            else:
                with urlopen(url, timeout=3) as response:
                    frame = cv2.imdecode(np.frombuffer(response.read(), np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                raise ValueError("Cannot decode camera image")
            result = targets(frame, profile, calibration)
            result["capture_and_processing_ms"] = round((time.perf_counter() - start) * 1000, 1)
        except (OSError, ValueError, cv2.error) as error:
            result = {"objects": [], "provisional_detections": [], "error": str(error)}
        print(json.dumps(result, allow_nan=False), flush=True)
        if args.image or time.monotonic() >= deadline:
            break
        time.sleep(max(0, 0.2 - (time.perf_counter() - start)))


if __name__ == "__main__":
    main()

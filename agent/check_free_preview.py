"""Offline preview checks; no robot connection."""
import copy
from unittest.mock import patch
import cv2
import numpy as np
from free_placement import Backend, reach_reasons
from vision_targets import detect_outlines
import mcp_server
from sorter import Sorter

profile = {"motion_verified": True, "execution_enabled": True,
           "verified_pick_polygon_xy_cm": [[0,0],[20,0],[20,20],[0,20]],
           "boundary_margin_cm": 1, "detection": {"gripper_max_opening_cm": 5}}
obj = {"footprint_xy_cm": [[2,2],[4,2],[4,4],[2,4]], "width_cm": 2, "height_cm": 1, "angle": 0}
assert reach_reasons(obj, profile) == []
outside = dict(obj, footprint_xy_cm=[[0,2],[4,2],[4,4],[0,4]])
assert any("boundary" in r for r in reach_reasons(outside, profile))
assert reach_reasons(dict(obj, width_cm=6), profile)
assert reach_reasons(dict(obj, height_cm=None), profile)
provisional = dict(profile, motion_verified=False)
assert reach_reasons(obj, provisional)
assert Backend().validate_target(obj)
s = Sorter(backend=Backend())
s.packs["forged"] = dict(obj, id="forged", x_cm=3, y_cm=3)
with patch("real.api", side_effect=AssertionError("Unexpected robot request")):
    assert s.inspect("forged")["error"]
    assert not s.in_reach(s.packs["forged"])
    assert s.inspect("stale")["error"]
mcp_server.sorter = s
assert mcp_server.run_tool("inspect", {"id": "forged"})["isError"]
image = np.full((600,800,3),255,np.uint8)
for a,b in [((60,60),(80,90)),((160,140),(210,350)),((340,150),(700,450))]:
    cv2.rectangle(image,a,b,(0,0,0),-1)
polygon = np.array([[10,10],[790,10],[790,590],[10,590]],np.int32)
assert len(detect_outlines(image,polygon)) == 3
print("PASS: polygon clearance, gripper size, provisional/unknown height, inspect denial, MCP errors, generic sizes")
from free_placement import build_plan
motion={"poses":{"home":{"x":.15},"bins":[{"name":"left","hover":{"x":.1,"z":.1},"drop":{"x":.1,"z":.04}}]},
        "sequences":{"place":["bin.hover","bin.drop","open","bin.hover","home"]}}
measured=dict(profile,hover_height_cm=8,grasp_height_cm=1,jaw_yaw_offset_deg=90)
target=dict(obj,x_cm=12,y_cm=-3,inside_calibration_support=True)
with patch("real.api",side_effect=AssertionError("Dry run attempted I/O")):
    plan=build_plan(target,"left",measured,motion)
assert not plan["executable"] and plan["dry_run"]
grasp=next(s for s in plan["steps"] if s.get("name")=="pickup_grasp")["pose"]
assert grasp["x"]==.12 and grasp["y"]==-.03 and grasp["z"]==.01
assert next(s for s in plan["steps"] if s.get("name")=="bin_drop")["pose"]==motion["poses"]["bins"][0]["drop"]
assert build_plan(target,"unknown",measured,motion)["error"]
print("PASS: dry-run arbitrary XY conversion, fixed bins preserved, no writes, unknown bin rejected")
import time
backend=Backend()
backend.last={"captured_at_unix":time.time()-20,"provisional_detections":[dict(target,id="test")]}
assert "Stale" in backend.plan_pick("test","left")["error"]
backend.last["captured_at_unix"]=time.time()
def moved():
    backend.last={"provisional_detections":[dict(target,id="new",x_cm=16)]}
    return []
with patch.object(backend,"scan",side_effect=moved):
    assert "moved" in backend.plan_pick("test","left")["error"]
print("PASS: stale and moved targets rejected")

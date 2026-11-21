"""Thin httpx wrapper around the NormaCore elrobot-ik HTTP API (/api/v1).

Deliberately has NO torque method: releasing torque drops the arm.
Speed, acceleration and orientation come from safety.py (settings, not limits).
"""
from __future__ import annotations

import math
import httpx

import safety

# Short, readable explanations for the API error codes (README "Errors").
ERROR_HINTS = {
    "unreachable": "target out of reach: bring it closer to the base, lower, or change yaw",
    "collision": "path or target collides with the table or the arm itself: raise z or turn the jaws",
    "not_calibrated": "arm is not calibrated: every motor range must be frozen in the Station UI",
    "motor_error": "one or more motors report an error flag: stop and tell the user",
    "invalid_gripper": "gripper value must be between 0 (closed) and 1 (open)",
    "invalid_request": "malformed request body",
    "limit_violation": "solver inconsistency, nothing was sent: report it",
    "station_disconnected": "Station is not connected: check the Station terminal tab",
    "no_frame": "no motor or camera frame yet: wait a moment and retry",
    "stale_frame": "motor state is too old: wait a moment, check health, retry",
    "unsupported_codec": "camera codec is neither VP8 nor JPEG",
    "not_found": "unknown arm or camera id",
}


class IKError(Exception):
    """An error answer from the IK API (or no answer at all)."""

    def __init__(self, code: str, detail: str = "", context: dict | None = None, status: int | None = None):
        self.code = code
        self.detail = detail
        self.context = context or {}
        self.status = status
        super().__init__(self.short())

    def short(self) -> str:
        hint = ERROR_HINTS.get(self.code, "")
        parts = [f"ERROR {self.code}"]
        if self.status:
            parts[0] += f" (HTTP {self.status})"
        if hint:
            parts.append(hint)
        if self.detail and self.detail != hint:
            parts.append(f"detail: {self.detail}")
        for key in ("candidates", "motors", "position_error", "angle_error"):
            if key in self.context:
                parts.append(f"{key}: {self.context[key]}")
        return ". ".join(parts)


class IKClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        # An execute waits for the motion (up to 8 s per attempt, 3 attempts).
        self._http = httpx.Client(base_url=self.base_url + "/api/v1",
                                  timeout=httpx.Timeout(45.0, connect=3.0))

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------ plumbing --
    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            r = self._http.request(method, path, **kwargs)
        except httpx.ConnectError as e:
            raise IKError("ik_api_down", f"IK API not reachable at {self.base_url}. Run ./start_ik.sh first.") from e
        except httpx.TimeoutException as e:
            raise IKError("ik_api_timeout", f"no answer from the IK API for {path}. Read the arm state before doing anything else.") from e
        if r.status_code >= 400:
            try:
                body = r.json()
            except ValueError:
                body = {}
            code = body.get("error") or ("not_found" if r.status_code == 404 else f"http_{r.status_code}")
            detail = body.get("detail", r.text[:200])
            if not isinstance(detail, str):
                detail = str(detail)[:300]
            raise IKError(code, detail, body.get("context") if isinstance(body.get("context"), dict) else {}, r.status_code)
        return r

    # ------------------------------------------------------------- service --
    def health(self) -> dict:
        return self._request("GET", "/health").json()

    def arms(self) -> list[dict]:
        return self._request("GET", "/arms").json()

    def follower(self, arm_id: str) -> dict:
        """The arm record for arm_id. Refuses anything whose role is not 'follower'."""
        arms = self.arms()
        for arm in arms:
            if arm["id"] == arm_id:
                if arm.get("role") != "follower":
                    raise IKError("not_follower", f"arm {arm_id} has role '{arm.get('role')}', refusing to drive it")
                return arm
        raise IKError("not_found", f"arm {arm_id} is not on the Station", {"candidates": [a["id"] for a in arms]})

    # -------------------------------------------------------------- motion --
    TILTS = (0.0, -0.15, -0.28, -0.40, -0.55, -0.70, -0.85)

    def _move_body(self, target: safety.Target, tilt: float, execute: bool, gripper: float | None,
                   speed: int | None, accel: int | None) -> dict:
        t = tilt + safety.TILT_TRIM
        body = {
            "position": {"x": target.x, "y": target.y, "z": target.z},
            "orientation": {"rpy": {"roll": safety.ROLL + t * math.sin(target.yaw),
                                    "pitch": safety.PITCH + t * math.cos(target.yaw), "yaw": target.yaw}},
            "execute": bool(execute),
            "speed": speed or safety.SPEED,
            "accel": accel or safety.ACCEL,
        }
        if gripper is not None:
            body["gripper"] = min(1.0, max(0.0, float(gripper)))
        return body

    def move_to(self, arm_id: str, target: safety.Target, execute: bool = False,
                gripper: float | None = None, speed: int | None = None, accel: int | None = None,
                near_steps: list[int] | None = None, tilt: float | None = None) -> dict:
        """Solve (execute=False) or solve and move (execute=True).

        tilt given: use exactly that jaw tilt. near_steps given (current motor steps 1..7): try every tilt and
        return the solution closest to the current joints, with 'tilt' and 'max_joint_delta' added.
        Otherwise: vertical first, then steeper forward tilts until one solves (reach out to ~0.36 m).
        15.11.2026: neighbouring heights could solve to a flipped elbow (motor 3 jumped 350 steps for 8 mm),
        the joint-space move swung the jaws into the table. near_steps picks the closest branch.
        """
        url = f"/arms/{arm_id}/move-to"
        if tilt is not None:
            r = self._request("POST", url, json=self._move_body(target, tilt, execute, gripper, speed, accel)).json()
            r["tilt"] = tilt
            return r
        last_err: IKError | None = None
        best: dict | None = None
        for t in self.TILTS:
            try:
                r = self._request("POST", url, json=self._move_body(
                    target, t, execute and near_steps is None, gripper, speed, accel)).json()
            except IKError as e:
                last_err = e
                # vertical may collide or hit a joint limit where a tilt works: try the next tilt
                if e.code not in ("unreachable", "collision", "limit_violation"):
                    raise
                continue
            r["tilt"] = t
            if near_steps is None:
                return r
            steps = r.get("motor_steps") or []
            # flip check on motors 1-6 only: motor 7 turns the jaws (a wanted yaw change is not a flip)
            r["max_joint_delta"] = max((abs(a - b) for a, b in zip(steps[:6], near_steps[:6])), default=0)
            r["_closeness"] = sum(abs(a - b) for a, b in zip(steps[:7], near_steps[:7]))
            if best is None or r["_closeness"] < best["_closeness"]:
                best = r
        if best is None:
            raise last_err  # type: ignore[misc]
        if execute:
            r = self._request("POST", url, json=self._move_body(target, best["tilt"], True, gripper, speed, accel)).json()
            r["tilt"], r["max_joint_delta"] = best["tilt"], best["max_joint_delta"]
            return r
        return best

    def gripper(self, arm_id: str, opening: float) -> dict:
        body = {"open": opening, "speed": safety.SPEED, "accel": safety.ACCEL}
        return self._request("POST", f"/arms/{arm_id}/gripper", json=body).json()

    # ------------------------------------------------------------- cameras --
    def cameras(self) -> list[dict]:
        return self._request("GET", "/cameras").json()

    def camera_image(self, camera_id: str) -> bytes:
        return self._request("GET", f"/cameras/{camera_id}/image").content

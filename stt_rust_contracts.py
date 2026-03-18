"""Typed contracts for Python <-> Rust STT gesture planning APIs.

This module centralizes request/response normalization so both voice STT and
assistant-side gesture planning consume one canonical schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional


@dataclass(frozen=True)
class RustGesturePlanRequest:
    """Canonical request payload for Rust `/gesture-plan` endpoint."""

    text: str
    gesture_mode: str
    avatar_mode: str
    office_mode: Optional[bool] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "gesture_mode": self.gesture_mode,
            "motion_style": self.gesture_mode,
            "avatar_mode": self.avatar_mode,
            "office_mode": self.office_mode,
        }


def _coerce_keyframes(value: Any) -> List[Dict[str, Any]]:
    keyframes: List[Dict[str, Any]] = []
    if not isinstance(value, list):
        return keyframes
    for item in value:
        if not isinstance(item, Mapping):
            continue
        t = item.get("t")
        pose = item.get("pose")
        if not isinstance(pose, Mapping):
            continue
        if not isinstance(t, (int, float)):
            continue
        keyframes.append({"t": int(t), "pose": dict(pose)})
    return keyframes


def _coerce_timeline(value: Any) -> List[Dict[str, Any]]:
    timeline: List[Dict[str, Any]] = []
    if not isinstance(value, list):
        return timeline
    for item in value:
        if not isinstance(item, Mapping):
            continue
        word = item.get("word")
        if not isinstance(word, str) or not word.strip():
            continue
        entry: Dict[str, Any] = {
            "word": word.strip(),
            "intent": str(item.get("intent") or "lexical"),
            "template": str(item.get("template") or "default"),
        }
        for key in ("start_ms", "end_ms"):
            raw = item.get(key)
            if isinstance(raw, (int, float)):
                entry[key] = int(raw)
        timeline.append(entry)
    return timeline


def sanitize_rust_motion_response(payload: Any) -> Dict[str, Any]:
    """Normalize Rust STT motion payloads into a strict frontend-ready shape."""
    if not isinstance(payload, Mapping):
        return {}
    data: Dict[str, Any] = {}

    gesture_mode = payload.get("gesture_mode") or payload.get("gestureMode") or payload.get("motion_style") or payload.get("motionStyle")
    if isinstance(gesture_mode, str) and gesture_mode.strip():
        data["gesture_mode"] = gesture_mode.strip()

    avatar_mode = payload.get("avatar_mode") or payload.get("avatarMode")
    if isinstance(avatar_mode, str) and avatar_mode.strip():
        data["avatar_mode"] = avatar_mode.strip()

    gesture_summary = payload.get("gesture_summary")
    if isinstance(gesture_summary, Mapping):
        data["gesture_summary"] = dict(gesture_summary)

    timeline = _coerce_timeline(payload.get("gesture_timeline"))
    if timeline:
        data["gesture_timeline"] = timeline

    avatar_motion = payload.get("avatar_motion")
    if isinstance(avatar_motion, Mapping):
        motion = dict(avatar_motion)
        keyframes = _coerce_keyframes(motion.get("keyframes"))
        motion["keyframes"] = keyframes
        if isinstance(motion.get("duration_ms"), (int, float)):
            motion["duration_ms"] = int(motion["duration_ms"])
        data["avatar_motion"] = motion

    audio_analysis = payload.get("audio_analysis")
    if isinstance(audio_analysis, Mapping):
        data["audio_analysis"] = dict(audio_analysis)

    speaker_segments = payload.get("speaker_segments")
    if isinstance(speaker_segments, list):
        data["speaker_segments"] = [dict(entry) for entry in speaker_segments if isinstance(entry, Mapping)]

    collaboration_mode = payload.get("collaboration_mode")
    if isinstance(collaboration_mode, bool):
        data["collaboration_mode"] = collaboration_mode

    return data

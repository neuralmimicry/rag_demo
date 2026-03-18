from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

GESTURE_MODES = {"bsl", "gesticulation"}
AVATAR_MODES = {"office", "chat"}

_GESTURE_MODE_ALIASES = {
    "sign": "bsl",
    "signing": "bsl",
    "sign_language": "bsl",
    "bsl_signing": "bsl",
    "gesture": "gesticulation",
    "gestures": "gesticulation",
    "default": "gesticulation",
}

_AVATAR_MODE_ALIASES = {
    "office_mode": "office",
    "desk": "office",
    "work": "office",
    "chat_mode": "chat",
    "conversation": "chat",
}

_POSE_SIGNED_KEYS = (
    "headYaw",
    "headPitch",
    "spineLean",
    "shoulderRise",
    "leftShoulderPitch",
    "rightShoulderPitch",
    "leftShoulderRoll",
    "rightShoulderRoll",
    "leftWristYaw",
    "rightWristYaw",
    "leftHip",
    "rightHip",
    "leftAnkle",
    "rightAnkle",
)

_POSE_UNSIGNED_KEYS = ("leftElbow", "rightElbow", "leftKnee", "rightKnee")
_HAND_KEYS = ("thumb", "index", "middle", "ring", "pinky")

_BASE_HAND_POSE: Dict[str, float] = {
    "thumb": 0.18,
    "index": 0.08,
    "middle": 0.08,
    "ring": 0.1,
    "pinky": 0.14,
}

_BASE_POSE: Dict[str, Any] = {
    "headYaw": 0.0,
    "headPitch": 0.0,
    "spineLean": 0.0,
    "shoulderRise": 0.0,
    "leftShoulderPitch": 0.0,
    "rightShoulderPitch": 0.0,
    "leftShoulderRoll": 0.0,
    "rightShoulderRoll": 0.0,
    "leftElbow": 0.32,
    "rightElbow": 0.32,
    "leftWristYaw": 0.0,
    "rightWristYaw": 0.0,
    "leftHip": 0.0,
    "rightHip": 0.0,
    "leftKnee": 0.2,
    "rightKnee": 0.2,
    "leftAnkle": 0.0,
    "rightAnkle": 0.0,
    "leftHand": dict(_BASE_HAND_POSE),
    "rightHand": dict(_BASE_HAND_POSE),
}

_BSL_DELTAS: Dict[str, Dict[str, Any]] = {
    "rest": {
        "shoulderRise": 0.12,
        "leftShoulderPitch": 0.22,
        "rightShoulderPitch": 0.22,
        "leftShoulderRoll": -0.44,
        "rightShoulderRoll": 0.44,
        "leftElbow": 0.22,
        "rightElbow": 0.22,
        "leftWristYaw": -0.12,
        "rightWristYaw": 0.12,
        "leftHand": {"thumb": 0.04, "index": 0.02, "middle": 0.02, "ring": 0.03, "pinky": 0.03},
        "rightHand": {"thumb": 0.04, "index": 0.02, "middle": 0.02, "ring": 0.03, "pinky": 0.03},
    },
    "greeting": {
        "headPitch": 0.06,
        "spineLean": 0.1,
        "shoulderRise": 0.3,
        "leftShoulderPitch": 0.46,
        "rightShoulderPitch": 0.46,
        "leftShoulderRoll": -0.68,
        "rightShoulderRoll": 0.68,
        "leftElbow": 0.44,
        "rightElbow": 0.44,
        "leftWristYaw": -0.44,
        "rightWristYaw": 0.44,
        "leftHand": {"thumb": -0.12, "index": -0.08, "middle": -0.08, "ring": -0.08, "pinky": -0.08},
        "rightHand": {"thumb": -0.12, "index": -0.08, "middle": -0.08, "ring": -0.08, "pinky": -0.08},
    },
    "question": {
        "headPitch": -0.1,
        "headYaw": 0.08,
        "spineLean": 0.08,
        "shoulderRise": 0.22,
        "leftShoulderPitch": 0.35,
        "rightShoulderPitch": 0.4,
        "leftShoulderRoll": -0.5,
        "rightShoulderRoll": 0.58,
        "leftElbow": 0.38,
        "rightElbow": 0.44,
        "leftWristYaw": -0.2,
        "rightWristYaw": 0.36,
        "leftHand": {"thumb": -0.03, "index": -0.04, "middle": -0.02, "ring": 0.01, "pinky": 0.03},
        "rightHand": {"thumb": -0.1, "index": -0.12, "middle": -0.1, "ring": -0.08, "pinky": -0.05},
    },
    "affirm": {
        "headPitch": 0.1,
        "spineLean": 0.05,
        "leftShoulderPitch": 0.32,
        "rightShoulderPitch": 0.32,
        "leftShoulderRoll": -0.4,
        "rightShoulderRoll": 0.4,
        "leftElbow": 0.28,
        "rightElbow": 0.28,
        "leftWristYaw": -0.28,
        "rightWristYaw": 0.28,
        "leftHand": {"thumb": 0.22, "index": 0.34, "middle": 0.3, "ring": 0.28, "pinky": 0.26},
        "rightHand": {"thumb": 0.22, "index": 0.34, "middle": 0.3, "ring": 0.28, "pinky": 0.26},
    },
    "negate": {
        "headYaw": -0.16,
        "headPitch": 0.04,
        "leftShoulderPitch": 0.36,
        "rightShoulderPitch": 0.24,
        "leftShoulderRoll": -0.52,
        "rightShoulderRoll": 0.36,
        "leftElbow": 0.36,
        "rightElbow": 0.25,
        "leftWristYaw": -0.5,
        "rightWristYaw": 0.18,
        "leftHand": {"thumb": 0.28, "index": 0.45, "middle": 0.42, "ring": 0.38, "pinky": 0.34},
        "rightHand": {"thumb": 0.1, "index": 0.15, "middle": 0.16, "ring": 0.2, "pinky": 0.22},
    },
    "polite": {
        "headPitch": 0.06,
        "spineLean": 0.12,
        "leftShoulderPitch": 0.4,
        "rightShoulderPitch": 0.4,
        "leftShoulderRoll": -0.48,
        "rightShoulderRoll": 0.48,
        "leftElbow": 0.4,
        "rightElbow": 0.4,
        "leftWristYaw": -0.08,
        "rightWristYaw": 0.08,
        "leftHand": {"thumb": -0.06, "index": -0.06, "middle": -0.04, "ring": -0.02, "pinky": 0.0},
        "rightHand": {"thumb": -0.06, "index": -0.06, "middle": -0.04, "ring": -0.02, "pinky": 0.0},
    },
    "topic": {
        "headPitch": 0.02,
        "spineLean": 0.08,
        "shoulderRise": 0.2,
        "leftShoulderPitch": 0.34,
        "rightShoulderPitch": 0.34,
        "leftShoulderRoll": -0.5,
        "rightShoulderRoll": 0.5,
        "leftElbow": 0.33,
        "rightElbow": 0.33,
        "leftWristYaw": -0.34,
        "rightWristYaw": 0.34,
        "leftHand": {"thumb": 0.08, "index": 0.2, "middle": 0.22, "ring": 0.18, "pinky": 0.16},
        "rightHand": {"thumb": 0.08, "index": 0.2, "middle": 0.22, "ring": 0.18, "pinky": 0.16},
    },
    "action": {
        "headPitch": -0.02,
        "spineLean": 0.14,
        "shoulderRise": 0.26,
        "leftShoulderPitch": 0.44,
        "rightShoulderPitch": 0.44,
        "leftShoulderRoll": -0.56,
        "rightShoulderRoll": 0.56,
        "leftElbow": 0.46,
        "rightElbow": 0.46,
        "leftWristYaw": -0.2,
        "rightWristYaw": 0.2,
        "leftHand": {"thumb": 0.12, "index": 0.1, "middle": 0.09, "ring": 0.13, "pinky": 0.18},
        "rightHand": {"thumb": 0.12, "index": 0.1, "middle": 0.09, "ring": 0.13, "pinky": 0.18},
    },
    "number": {
        "headPitch": 0.02,
        "shoulderRise": 0.22,
        "leftShoulderPitch": 0.3,
        "rightShoulderPitch": 0.3,
        "leftShoulderRoll": -0.44,
        "rightShoulderRoll": 0.44,
        "leftElbow": 0.32,
        "rightElbow": 0.32,
        "leftWristYaw": -0.38,
        "rightWristYaw": 0.38,
        "leftHand": {"thumb": 0.1, "index": -0.14, "middle": 0.04, "ring": 0.16, "pinky": 0.22},
        "rightHand": {"thumb": 0.1, "index": -0.14, "middle": 0.04, "ring": 0.16, "pinky": 0.22},
    },
    "fingerspell_a": {
        "headPitch": 0.02,
        "shoulderRise": 0.24,
        "leftShoulderPitch": 0.32,
        "rightShoulderPitch": 0.36,
        "leftShoulderRoll": -0.48,
        "rightShoulderRoll": 0.52,
        "leftElbow": 0.38,
        "rightElbow": 0.42,
        "leftWristYaw": -0.22,
        "rightWristYaw": 0.24,
    },
    "fingerspell_b": {
        "headPitch": -0.02,
        "shoulderRise": 0.24,
        "leftShoulderPitch": 0.36,
        "rightShoulderPitch": 0.3,
        "leftShoulderRoll": -0.52,
        "rightShoulderRoll": 0.48,
        "leftElbow": 0.42,
        "rightElbow": 0.38,
        "leftWristYaw": -0.26,
        "rightWristYaw": 0.18,
    },
}

_GESTICULATION_DELTAS: Dict[str, Dict[str, Any]] = {
    "rest": {
        "headPitch": 0.03,
        "shoulderRise": 0.08,
        "leftShoulderPitch": 0.16,
        "rightShoulderPitch": 0.16,
        "leftShoulderRoll": -0.26,
        "rightShoulderRoll": 0.26,
        "leftElbow": 0.16,
        "rightElbow": 0.16,
        "leftWristYaw": -0.08,
        "rightWristYaw": 0.08,
        "leftHand": {"thumb": 0.03, "index": 0.06, "middle": 0.08, "ring": 0.1, "pinky": 0.12},
        "rightHand": {"thumb": 0.03, "index": 0.06, "middle": 0.08, "ring": 0.1, "pinky": 0.12},
    },
    "emphasis": {
        "headPitch": 0.08,
        "spineLean": 0.08,
        "shoulderRise": 0.2,
        "leftShoulderPitch": 0.26,
        "rightShoulderPitch": 0.26,
        "leftShoulderRoll": -0.4,
        "rightShoulderRoll": 0.4,
        "leftElbow": 0.24,
        "rightElbow": 0.24,
        "leftWristYaw": -0.24,
        "rightWristYaw": 0.24,
        "leftHand": {"thumb": 0.08, "index": 0.12, "middle": 0.14, "ring": 0.16, "pinky": 0.18},
        "rightHand": {"thumb": 0.08, "index": 0.12, "middle": 0.14, "ring": 0.16, "pinky": 0.18},
    },
    "explain": {
        "headPitch": 0.02,
        "spineLean": 0.06,
        "leftShoulderPitch": 0.22,
        "rightShoulderPitch": 0.18,
        "leftShoulderRoll": -0.34,
        "rightShoulderRoll": 0.28,
        "leftElbow": 0.2,
        "rightElbow": 0.16,
        "leftWristYaw": -0.2,
        "rightWristYaw": 0.12,
        "leftHand": {"thumb": 0.05, "index": 0.1, "middle": 0.12, "ring": 0.15, "pinky": 0.18},
        "rightHand": {"thumb": 0.03, "index": 0.08, "middle": 0.1, "ring": 0.12, "pinky": 0.14},
    },
    "question": {
        "headPitch": -0.08,
        "headYaw": 0.1,
        "spineLean": 0.05,
        "leftShoulderPitch": 0.2,
        "rightShoulderPitch": 0.3,
        "leftShoulderRoll": -0.3,
        "rightShoulderRoll": 0.45,
        "leftElbow": 0.17,
        "rightElbow": 0.28,
        "leftWristYaw": -0.1,
        "rightWristYaw": 0.28,
        "leftHand": {"thumb": 0.03, "index": 0.06, "middle": 0.08, "ring": 0.1, "pinky": 0.12},
        "rightHand": {"thumb": 0.0, "index": -0.06, "middle": -0.04, "ring": 0.02, "pinky": 0.08},
    },
    "acknowledge": {
        "headPitch": 0.12,
        "spineLean": 0.03,
        "leftShoulderPitch": 0.24,
        "rightShoulderPitch": 0.24,
        "leftShoulderRoll": -0.3,
        "rightShoulderRoll": 0.3,
        "leftElbow": 0.17,
        "rightElbow": 0.17,
        "leftWristYaw": -0.14,
        "rightWristYaw": 0.14,
        "leftHand": {"thumb": 0.1, "index": 0.16, "middle": 0.16, "ring": 0.15, "pinky": 0.13},
        "rightHand": {"thumb": 0.1, "index": 0.16, "middle": 0.16, "ring": 0.15, "pinky": 0.13},
    },
    "negate": {
        "headYaw": -0.12,
        "headPitch": 0.04,
        "leftShoulderPitch": 0.3,
        "rightShoulderPitch": 0.14,
        "leftShoulderRoll": -0.42,
        "rightShoulderRoll": 0.2,
        "leftElbow": 0.3,
        "rightElbow": 0.12,
        "leftWristYaw": -0.34,
        "rightWristYaw": 0.06,
        "leftHand": {"thumb": 0.2, "index": 0.3, "middle": 0.3, "ring": 0.28, "pinky": 0.24},
        "rightHand": {"thumb": 0.05, "index": 0.08, "middle": 0.1, "ring": 0.12, "pinky": 0.14},
    },
    "directional": {
        "headYaw": 0.08,
        "leftShoulderPitch": 0.16,
        "rightShoulderPitch": 0.28,
        "leftShoulderRoll": -0.24,
        "rightShoulderRoll": 0.44,
        "leftElbow": 0.13,
        "rightElbow": 0.32,
        "leftWristYaw": -0.04,
        "rightWristYaw": 0.34,
        "leftHand": {"thumb": 0.03, "index": 0.08, "middle": 0.1, "ring": 0.12, "pinky": 0.14},
        "rightHand": {"thumb": 0.06, "index": -0.14, "middle": 0.04, "ring": 0.14, "pinky": 0.2},
    },
    "subtle": {
        "headPitch": 0.01,
        "leftShoulderPitch": 0.12,
        "rightShoulderPitch": 0.12,
        "leftShoulderRoll": -0.2,
        "rightShoulderRoll": 0.2,
        "leftElbow": 0.1,
        "rightElbow": 0.1,
        "leftWristYaw": -0.06,
        "rightWristYaw": 0.06,
        "leftHand": {"thumb": 0.02, "index": 0.05, "middle": 0.07, "ring": 0.08, "pinky": 0.1},
        "rightHand": {"thumb": 0.02, "index": 0.05, "middle": 0.07, "ring": 0.08, "pinky": 0.1},
    },
}

_TOKEN_RE = re.compile(r"([A-Za-z0-9']+)([^A-Za-z0-9']*)")
_NUMERIC_RE = re.compile(r"^\d+(?:st|nd|rd|th)?$")

_GREETING_TERMS = {"hello", "hi", "hey", "welcome", "morning", "afternoon", "evening"}
_QUESTION_TERMS = {"what", "why", "when", "where", "who", "how", "which", "can", "could", "would", "should"}
_AFFIRM_TERMS = {"yes", "yeah", "yep", "sure", "correct", "agree", "absolutely", "definitely"}
_NEGATE_TERMS = {"no", "not", "never", "dont", "don't", "cannot", "cant", "can't", "without"}
_POLITE_TERMS = {"please", "thanks", "thank", "appreciate", "sorry"}
_DIRECTION_TERMS = {"you", "your", "yours", "we", "our", "us", "they", "their", "them", "i", "me", "my"}
_ACTION_TERMS = {
    "build",
    "create",
    "deliver",
    "deploy",
    "launch",
    "ship",
    "run",
    "send",
    "plan",
    "improve",
    "analyze",
    "assist",
    "help",
    "translate",
    "sign",
    "gesture",
    "speak",
}
_DOMAIN_TERMS = {
    "neuralmimicry",
    "aarnn",
    "refiner",
    "continuum",
    "tracey",
    "trace",
    "neuromorphic",
    "avatar",
    "office",
    "chat",
    "bsl",
    "sign",
    "language",
}
_EMPHASIS_TERMS = {
    "important",
    "critical",
    "key",
    "urgent",
    "must",
    "always",
    "never",
    "best",
    "first",
    "main",
    "primary",
}
_STOPWORDS = {
    "the",
    "a",
    "an",
    "to",
    "of",
    "in",
    "on",
    "at",
    "for",
    "and",
    "or",
    "if",
    "is",
    "are",
    "was",
    "were",
    "be",
    "it",
    "as",
    "with",
    "by",
    "from",
}


def _to_number(value: Any, fallback: float = 0.0) -> float:
    try:
        number = float(value)
    except Exception:
        return fallback
    if number != number:  # NaN guard
        return fallback
    return number


def _clamp(value: float, low: float, high: float) -> float:
    if value < low:
        return low
    if value > high:
        return high
    return value


def _normalize_hand(raw: Optional[Dict[str, Any]], fallback: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    source = raw if isinstance(raw, dict) else {}
    base = fallback or _BASE_HAND_POSE
    return {
        key: _clamp(_to_number(source.get(key), base.get(key, 0.0)), 0.0, 1.0)
        for key in _HAND_KEYS
    }


def _normalize_pose(raw: Optional[Dict[str, Any]], fallback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    base = fallback or _BASE_POSE
    pose: Dict[str, Any] = {}
    for key in _POSE_SIGNED_KEYS:
        pose[key] = _clamp(_to_number(source.get(key), _to_number(base.get(key), 0.0)), -1.0, 1.0)
    for key in _POSE_UNSIGNED_KEYS:
        pose[key] = _clamp(_to_number(source.get(key), _to_number(base.get(key), 0.0)), 0.0, 1.0)
    pose["leftHand"] = _normalize_hand(source.get("leftHand"), _normalize_hand(base.get("leftHand")))
    pose["rightHand"] = _normalize_hand(source.get("rightHand"), _normalize_hand(base.get("rightHand")))
    return pose


def _blend_hand(a: Dict[str, float], b: Dict[str, float], alpha: float) -> Dict[str, float]:
    t = _clamp(alpha, 0.0, 1.0)
    return {
        key: _clamp(a[key] + (b[key] - a[key]) * t, 0.0, 1.0)
        for key in _HAND_KEYS
    }


def _blend_pose(a: Dict[str, Any], b: Dict[str, Any], alpha: float) -> Dict[str, Any]:
    t = _clamp(alpha, 0.0, 1.0)
    out: Dict[str, Any] = {}
    for key in _POSE_SIGNED_KEYS:
        out[key] = _clamp(_to_number(a.get(key)) + (_to_number(b.get(key)) - _to_number(a.get(key))) * t, -1.0, 1.0)
    for key in _POSE_UNSIGNED_KEYS:
        out[key] = _clamp(_to_number(a.get(key)) + (_to_number(b.get(key)) - _to_number(a.get(key))) * t, 0.0, 1.0)
    out["leftHand"] = _blend_hand(_normalize_hand(a.get("leftHand")), _normalize_hand(b.get("leftHand")), t)
    out["rightHand"] = _blend_hand(_normalize_hand(a.get("rightHand")), _normalize_hand(b.get("rightHand")), t)
    return out


def _apply_pose_delta(base_pose: Dict[str, Any], delta: Dict[str, Any], scale: float) -> Dict[str, Any]:
    t = max(0.0, _to_number(scale, 1.0))
    out = _normalize_pose(base_pose, _BASE_POSE)
    for key in _POSE_SIGNED_KEYS:
        change = _to_number(delta.get(key), 0.0) * t
        out[key] = _clamp(out[key] + change, -1.0, 1.0)
    for key in _POSE_UNSIGNED_KEYS:
        change = _to_number(delta.get(key), 0.0) * t
        out[key] = _clamp(out[key] + change, 0.0, 1.0)
    for side in ("leftHand", "rightHand"):
        hand_delta = delta.get(side) if isinstance(delta.get(side), dict) else {}
        hand = _normalize_hand(out.get(side))
        for finger in _HAND_KEYS:
            hand[finger] = _clamp(hand[finger] + _to_number(hand_delta.get(finger), 0.0) * t, 0.0, 1.0)
        out[side] = hand
    return out


def _tokenize(text: str, *, max_tokens: int = 28) -> List[Tuple[str, str]]:
    tokens: List[Tuple[str, str]] = []
    for match in _TOKEN_RE.finditer(text):
        word = (match.group(1) or "").strip()
        if not word:
            continue
        tail = match.group(2) or ""
        tokens.append((word, tail))
        if len(tokens) >= max_tokens:
            break
    return tokens


def _classify_intent(word: str) -> str:
    lowered = word.lower().replace("’", "'")
    normalized = lowered.strip("'")
    compact = normalized.replace("'", "")
    if not normalized:
        return "filler"
    if _NUMERIC_RE.match(compact):
        return "number"
    if normalized in _GREETING_TERMS:
        return "greeting"
    if normalized in _QUESTION_TERMS:
        return "question"
    if normalized in _AFFIRM_TERMS:
        return "affirm"
    if normalized in _NEGATE_TERMS:
        return "negate"
    if normalized in _POLITE_TERMS:
        return "polite"
    if normalized in _DIRECTION_TERMS:
        return "directional"
    if normalized in _ACTION_TERMS:
        return "action"
    if normalized in _DOMAIN_TERMS:
        return "topic"
    if normalized in _STOPWORDS:
        return "filler"
    return "content"


def _select_bsl_template(intent: str, index: int) -> str:
    if intent in {"greeting", "question", "affirm", "negate", "polite", "topic", "action", "number"}:
        return intent
    if intent == "filler":
        return "rest"
    return "fingerspell_a" if index % 2 == 0 else "fingerspell_b"


def _select_gesticulation_template(word: str, intent: str) -> str:
    lowered = word.lower()
    if intent == "question":
        return "question"
    if intent == "negate":
        return "negate"
    if intent in {"affirm", "polite", "greeting"}:
        return "acknowledge"
    if intent == "directional":
        return "directional"
    if intent == "filler":
        return "subtle"
    if lowered in _EMPHASIS_TERMS:
        return "emphasis"
    if intent in {"topic", "action"}:
        return "emphasis"
    return "explain"


def _word_intensity(word: str, intent: str, mode: str) -> float:
    lowered = word.lower()
    if mode == "bsl":
        if intent == "filler":
            return 0.58
        if intent in {"question", "negate", "affirm", "number", "topic"}:
            return 1.04
        return 0.9
    score = 0.45 + min(len(lowered), 10) * 0.03
    if intent in {"question", "negate"}:
        score += 0.2
    if intent in {"topic", "action"}:
        score += 0.12
    if lowered in _EMPHASIS_TERMS:
        score += 0.18
    if lowered in _STOPWORDS:
        score *= 0.65
    if intent == "filler":
        score *= 0.6
    return _clamp(score, 0.3, 1.1)


def _estimate_word_duration_ms(word: str, mode: str) -> int:
    length = max(1, min(len(word), 12))
    if mode == "bsl":
        return int(_clamp(185 + length * 20, 165, 540))
    return int(_clamp(125 + length * 14, 110, 360))


def _pause_after_token_ms(punctuation_tail: str, mode: str) -> int:
    if any(mark in punctuation_tail for mark in ".!?"):
        return 150 if mode == "bsl" else 110
    if any(mark in punctuation_tail for mark in ",;:"):
        return 90 if mode == "bsl" else 65
    return 34 if mode == "bsl" else 24


def _hand_from_letter(letter: str, *, mirror: bool = False) -> Dict[str, float]:
    lower = letter.lower() if isinstance(letter, str) else "a"
    idx = ord(lower) - 97 if "a" <= lower <= "z" else 0
    row = idx // 5
    col = idx % 5
    curl_base = 0.1 + row * 0.16
    spread = (col - 2) * 0.08
    thumb = _clamp(0.16 + (row % 3) * 0.08 + (-spread if mirror else spread) * -0.35, 0.0, 1.0)
    index = _clamp(curl_base + abs(spread) * (0.55 if mirror else 0.35), 0.0, 1.0)
    middle = _clamp(curl_base + 0.06 + (0.1 if col == 2 else 0.0), 0.0, 1.0)
    ring = _clamp(curl_base + 0.1 + (0.08 if col >= 3 else 0.0), 0.0, 1.0)
    pinky = _clamp(curl_base + 0.14 + (0.1 if col == 4 else 0.0), 0.0, 1.0)
    return {"thumb": thumb, "index": index, "middle": middle, "ring": ring, "pinky": pinky}


def _build_bsl_pose(word: str, template_name: str, intensity: float, index: int) -> Dict[str, Any]:
    rest_pose = _apply_pose_delta(_BASE_POSE, _BSL_DELTAS["rest"], max(0.4, intensity * 0.85))
    template_delta = _BSL_DELTAS.get(template_name) or _BSL_DELTAS["topic"]
    pose = _apply_pose_delta(rest_pose, template_delta, intensity)
    if template_name.startswith("fingerspell"):
        lead = word[0] if word else "a"
        trail = word[-1] if word else lead
        left_shape = _hand_from_letter(lead, mirror=False)
        right_shape = _hand_from_letter(trail, mirror=True)
        pose["leftHand"] = _blend_hand(_normalize_hand(pose.get("leftHand")), left_shape, 0.86)
        pose["rightHand"] = _blend_hand(_normalize_hand(pose.get("rightHand")), right_shape, 0.86)
        char_code = ord(lead.lower()) if lead and lead[0].isalpha() else 97
        twist = ((char_code % 7) - 3) * 0.08
        pose["leftWristYaw"] = _clamp(_to_number(pose.get("leftWristYaw")) - twist, -1.0, 1.0)
        pose["rightWristYaw"] = _clamp(_to_number(pose.get("rightWristYaw")) + twist, -1.0, 1.0)
    side_shift = 0.06 if index % 2 == 0 else -0.06
    pose["spineLean"] = _clamp(_to_number(pose.get("spineLean")) + side_shift, -1.0, 1.0)
    return _normalize_pose(pose, _BASE_POSE)


def _build_gesticulation_pose(template_name: str, intensity: float, index: int) -> Dict[str, Any]:
    rest_pose = _apply_pose_delta(_BASE_POSE, _GESTICULATION_DELTAS["rest"], max(0.3, intensity * 0.8))
    template_delta = _GESTICULATION_DELTAS.get(template_name) or _GESTICULATION_DELTAS["explain"]
    pose = _apply_pose_delta(rest_pose, template_delta, intensity)
    head_sway = (0.05 if index % 2 == 0 else -0.05) * min(1.0, intensity)
    pose["headYaw"] = _clamp(_to_number(pose.get("headYaw")) + head_sway, -1.0, 1.0)
    return _normalize_pose(pose, _BASE_POSE)


def _append_frame(frames: List[Dict[str, Any]], timestamp_ms: int, pose: Dict[str, Any]) -> None:
    t = max(0, int(timestamp_ms))
    if frames and t <= int(frames[-1]["t"]):
        t = int(frames[-1]["t"]) + 1
    frames.append({"t": t, "pose": _normalize_pose(pose, _BASE_POSE)})


def _amplitude_for_context(gesture_mode: str, avatar_mode: str) -> float:
    if gesture_mode == "bsl":
        return 1.0 if avatar_mode == "office" else 0.74
    return 0.76 if avatar_mode == "office" else 0.5


def _normalize_token(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return normalized


def sanitize_gesture_mode(
    value: Optional[str],
    *,
    default_mode: str = "gesticulation",
    bsl_enabled: bool = True,
) -> str:
    candidate = _normalize_token(value or "")
    if not candidate:
        candidate = _normalize_token(default_mode or "gesticulation")
    if candidate in {"british_sign_language", "bsl_british_sign_language"}:
        candidate = "bsl"
    candidate = _GESTURE_MODE_ALIASES.get(candidate, candidate)
    if candidate not in GESTURE_MODES:
        fallback_raw = _normalize_token(default_mode or "gesticulation")
        fallback = _GESTURE_MODE_ALIASES.get(fallback_raw, fallback_raw)
        candidate = fallback if fallback in GESTURE_MODES else "gesticulation"
    if candidate == "bsl" and not bsl_enabled:
        return "gesticulation"
    return candidate


def sanitize_avatar_mode(
    value: Optional[str],
    *,
    office_mode: Optional[bool] = None,
    default_mode: str = "chat",
) -> str:
    candidate = _normalize_token(value or "")
    if not candidate and office_mode is not None:
        candidate = "office" if office_mode else "chat"
    if not candidate:
        candidate = _normalize_token(default_mode or "chat")
    candidate = _AVATAR_MODE_ALIASES.get(candidate, candidate)
    if candidate not in AVATAR_MODES:
        candidate = "chat"
    return candidate


def plan_stt_avatar_motion(
    transcript: str,
    *,
    gesture_mode: str = "gesticulation",
    avatar_mode: str = "chat",
    bsl_enabled: bool = True,
    max_words: int = 28,
) -> Dict[str, Any]:
    normalized_text = re.sub(r"\s+", " ", str(transcript or "")).strip()
    selected_mode = sanitize_gesture_mode(gesture_mode, bsl_enabled=bsl_enabled)
    selected_avatar_mode = sanitize_avatar_mode(avatar_mode)

    response: Dict[str, Any] = {
        "gesture_mode": selected_mode,
        "avatar_mode": selected_avatar_mode,
        "gesture_summary": {
            "style": "bsl_signing" if selected_mode == "bsl" else "semantic_gesticulation",
            "token_count": 0,
        },
    }
    if not normalized_text:
        return response

    tokens = _tokenize(normalized_text, max_tokens=max_words)
    if not tokens:
        return response

    amplitude = _amplitude_for_context(selected_mode, selected_avatar_mode)
    if selected_mode == "bsl":
        rest_pose = _apply_pose_delta(_BASE_POSE, _BSL_DELTAS["rest"], amplitude)
    else:
        rest_pose = _apply_pose_delta(_BASE_POSE, _GESTICULATION_DELTAS["rest"], amplitude)
    rest_pose = _normalize_pose(rest_pose, _BASE_POSE)

    frames: List[Dict[str, Any]] = []
    timeline: List[Dict[str, Any]] = []
    _append_frame(frames, 0, rest_pose)

    cursor = 80
    previous_pose = rest_pose
    for index, (word, punctuation_tail) in enumerate(tokens):
        intent = _classify_intent(word)
        duration_ms = _estimate_word_duration_ms(word, selected_mode)
        pause_ms = _pause_after_token_ms(punctuation_tail, selected_mode)
        intensity = amplitude * _word_intensity(word, intent, selected_mode)

        if selected_mode == "bsl":
            template_name = _select_bsl_template(intent, index)
            peak_pose = _build_bsl_pose(word, template_name, intensity, index)
        else:
            template_name = _select_gesticulation_template(word, intent)
            peak_pose = _build_gesticulation_pose(template_name, intensity, index)

        attack_pose = _blend_pose(previous_pose, peak_pose, 0.58)
        release_pose = _blend_pose(peak_pose, rest_pose, 0.56)

        attack_time = cursor + int(duration_ms * 0.22)
        peak_time = cursor + int(duration_ms * 0.58)
        release_time = cursor + int(duration_ms * 0.9)

        _append_frame(frames, attack_time, attack_pose)
        _append_frame(frames, peak_time, peak_pose)
        _append_frame(frames, release_time, release_pose)

        word_start = cursor
        word_end = cursor + duration_ms
        timeline.append(
            {
                "word": word,
                "intent": intent,
                "template": template_name,
                "start_ms": word_start,
                "end_ms": word_end,
            }
        )

        cursor = word_end + pause_ms
        previous_pose = release_pose

    duration_ms = int(_clamp(cursor + 180, 700, 18000))
    _append_frame(frames, duration_ms, rest_pose)

    response["gesture_summary"]["token_count"] = len(tokens)
    response["gesture_timeline"] = timeline
    response["avatar_motion"] = {
        "duration_ms": duration_ms,
        "keyframes": frames,
    }
    return response

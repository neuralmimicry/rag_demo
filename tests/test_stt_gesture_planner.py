from stt_gesture_planner import plan_stt_avatar_motion, sanitize_gesture_mode


def _max_abs_pose_value(payload, key: str) -> float:
    frames = payload["avatar_motion"]["keyframes"]
    return max(abs(float(frame["pose"].get(key, 0.0))) for frame in frames)


def _max_hand_curl(payload) -> float:
    frames = payload["avatar_motion"]["keyframes"]
    best = 0.0
    for frame in frames:
        pose = frame["pose"]
        for side in ("leftHand", "rightHand"):
            hand = pose.get(side) or {}
            value = (
                float(hand.get("thumb", 0.0))
                + float(hand.get("index", 0.0))
                + float(hand.get("middle", 0.0))
                + float(hand.get("ring", 0.0))
                + float(hand.get("pinky", 0.0))
            ) / 5.0
            best = max(best, value)
    return best


def test_plan_outputs_motion_and_timeline():
    payload = plan_stt_avatar_motion(
        "Hello can you explain Refiner and Continuum deployment?",
        gesture_mode="bsl",
        avatar_mode="office",
    )

    assert payload["gesture_mode"] == "bsl"
    assert payload["avatar_mode"] == "office"
    assert payload["avatar_motion"]["duration_ms"] >= 700
    assert len(payload["avatar_motion"]["keyframes"]) >= 6
    assert payload["gesture_summary"]["token_count"] > 0
    assert len(payload["gesture_timeline"]) == payload["gesture_summary"]["token_count"]

    previous_t = -1
    for frame in payload["avatar_motion"]["keyframes"]:
        assert frame["t"] > previous_t
        previous_t = frame["t"]


def test_office_mode_is_more_expressive_than_chat_mode():
    office = plan_stt_avatar_motion(
        "Please sign this update about AARNN and Tracey.",
        gesture_mode="bsl",
        avatar_mode="office",
    )
    chat = plan_stt_avatar_motion(
        "Please sign this update about AARNN and Tracey.",
        gesture_mode="bsl",
        avatar_mode="chat",
    )

    office_roll = _max_abs_pose_value(office, "leftShoulderRoll")
    chat_roll = _max_abs_pose_value(chat, "leftShoulderRoll")
    assert office_roll >= chat_roll + 0.05


def test_bsl_has_richer_hand_shapes_than_gesticulation():
    bsl = plan_stt_avatar_motion(
        "NeuralMimicry Refiner Continuum Tracey",
        gesture_mode="bsl",
        avatar_mode="office",
    )
    gest = plan_stt_avatar_motion(
        "NeuralMimicry Refiner Continuum Tracey",
        gesture_mode="gesticulation",
        avatar_mode="office",
    )

    assert _max_hand_curl(bsl) >= _max_hand_curl(gest) + 0.04


def test_bsl_mode_falls_back_when_disabled():
    assert sanitize_gesture_mode("bsl", bsl_enabled=False) == "gesticulation"
    payload = plan_stt_avatar_motion(
        "Can you sign this?",
        gesture_mode="bsl",
        avatar_mode="chat",
        bsl_enabled=False,
    )
    assert payload["gesture_mode"] == "gesticulation"


def test_bsl_human_readable_label_aliases_are_supported():
    assert sanitize_gesture_mode("BSL (British Sign Language)") == "bsl"
    assert sanitize_gesture_mode("British Sign Language") == "bsl"

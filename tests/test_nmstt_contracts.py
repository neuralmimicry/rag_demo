from refiner.nmstt_contracts import NmsttGesturePlanRequest, sanitize_nmstt_motion_response


def test_nmstt_gesture_plan_request_contains_motion_style_alias():
    payload = NmsttGesturePlanRequest(
        text="hello",
        gesture_mode="bsl",
        avatar_mode="office",
        office_mode=True,
    ).as_dict()
    assert payload["text"] == "hello"
    assert payload["gesture_mode"] == "bsl"
    assert payload["motion_style"] == "bsl"
    assert payload["avatar_mode"] == "office"
    assert payload["office_mode"] is True


def test_sanitize_nmstt_motion_response_filters_invalid_keyframes_and_timeline():
    result = sanitize_nmstt_motion_response(
        {
            "gesture_mode": "bsl",
            "avatar_mode": "office",
            "gesture_timeline": [
                {"word": "hello", "intent": "greeting", "template": "hello", "start_ms": 0, "end_ms": 400},
                {"word": "", "start_ms": "bad"},
            ],
            "avatar_motion": {
                "duration_ms": 700,
                "keyframes": [
                    {"t": 0, "pose": {"leftShoulderRoll": 0.1}},
                    {"t": "bad", "pose": {"leftShoulderRoll": 0.2}},
                    {"t": 200, "pose": "bad"},
                ],
            },
        }
    )
    assert result["gesture_mode"] == "bsl"
    assert result["avatar_mode"] == "office"
    assert len(result["gesture_timeline"]) == 1
    assert result["gesture_timeline"][0]["word"] == "hello"
    assert result["avatar_motion"]["duration_ms"] == 700
    assert len(result["avatar_motion"]["keyframes"]) == 1

from assistant_pipeline.runtime.first_arrival_gate import (
    claim_first_arrival,
    reset_first_arrival_claims_for_tests,
)


def test_first_arrival_gate_suppresses_cross_channel_duplicates(monkeypatch):
    reset_first_arrival_claims_for_tests()
    monkeypatch.setenv("REFINER_AARON_FIRST_ARRIVAL_ENABLED", "1")
    monkeypatch.setenv("REFINER_AARON_FIRST_ARRIVAL_WINDOW_SEC", "10")
    monkeypatch.setenv("REFINER_AARON_FIRST_ARRIVAL_CHANNELS", "whatsapp,alexa")

    first = claim_first_arrival(owner="alice", prompt="Check status", channel="whatsapp")
    duplicate = claim_first_arrival(owner="alice", prompt="Check status", channel="alexa")

    assert first["suppressed"] is False
    assert duplicate["suppressed"] is True
    assert duplicate["winner_channel"] == "whatsapp"
    reset_first_arrival_claims_for_tests()


def test_first_arrival_gate_allows_same_channel_repeat(monkeypatch):
    reset_first_arrival_claims_for_tests()
    monkeypatch.setenv("REFINER_AARON_FIRST_ARRIVAL_ENABLED", "1")
    monkeypatch.setenv("REFINER_AARON_FIRST_ARRIVAL_WINDOW_SEC", "10")
    monkeypatch.setenv("REFINER_AARON_FIRST_ARRIVAL_CHANNELS", "whatsapp")

    first = claim_first_arrival(owner="alice", prompt="Check status", channel="whatsapp")
    repeat = claim_first_arrival(owner="alice", prompt="Check status", channel="whatsapp")

    assert first["suppressed"] is False
    assert repeat["suppressed"] is False
    assert repeat["reason"] == "same_channel_repeat"
    reset_first_arrival_claims_for_tests()


from solver_command_policy import evaluate_command_policy
from solver_command_trust import CommandTrustStore


def test_command_trust_store_promotes_repeat_successes(tmp_path):
    store = CommandTrustStore(str(tmp_path / "command_trust.json"), max_shapes=20)
    decision = evaluate_command_policy("PYTHONPATH=. python -m pytest -q")

    initial = store.assess(decision)
    for _ in range(4):
        updated = store.record(decision, success=True, exit_code=0)

    assert initial.level == "new"
    assert updated.known is True
    assert updated.total_runs == 4
    assert updated.success_rate == 1.0
    assert updated.level == "established"
    assert updated.shape == "python -m pytest -q"


def test_command_trust_store_flags_repeat_failures(tmp_path):
    store = CommandTrustStore(str(tmp_path / "command_trust.json"), max_shapes=20)
    decision = evaluate_command_policy("python -m pytest")

    for _ in range(3):
        updated = store.record(decision, success=False, exit_code=1)

    snapshot = store.snapshot(limit=1)[0]

    assert updated.level == "watch"
    assert updated.effective_risk == "medium"
    assert snapshot["trust_level"] == "watch"
    assert snapshot["consecutive_failures"] == 3

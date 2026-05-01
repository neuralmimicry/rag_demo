from assistant_pipeline.runtime import OwnerAwareCapacityLimiter


def test_owner_aware_capacity_limiter_enforces_per_owner_ceiling() -> None:
    limiter = OwnerAwareCapacityLimiter(3, max_concurrent_per_owner=2)

    assert limiter.acquire_for("alice", timeout=0.0) is True
    assert limiter.acquire_for("alice", timeout=0.0) is True
    assert limiter.acquire_for("alice", timeout=0.0) is False

    limiter.release_for("alice")
    assert limiter.acquire_for("alice", timeout=0.0) is True


def test_owner_aware_capacity_limiter_preserves_capacity_for_other_owners() -> None:
    limiter = OwnerAwareCapacityLimiter(2, max_concurrent_per_owner=1)

    assert limiter.acquire_for("alice", timeout=0.0) is True
    assert limiter.acquire_for("bob", timeout=0.0) is True
    assert limiter.acquire_for("alice", timeout=0.0) is False

    limiter.release_for("alice")
    assert limiter.acquire_for("alice", timeout=0.0) is True

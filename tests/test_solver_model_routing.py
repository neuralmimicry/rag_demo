from refiner.workflows.solver.reliability import select_planner_role


def test_fast_planner_role_is_reserved_for_routine_non_code_work():
    """Keep the latency-oriented role away from code-changing plans."""
    assert select_planner_role(fast_planner_available=True, source_requires_code=False) == "fast_planner"
    assert select_planner_role(fast_planner_available=True, source_requires_code=True) == "planner"
    assert select_planner_role(fast_planner_available=False, source_requires_code=False) == "planner"

from refiner import project_solver


def test_solver_completion_exit_code_is_zero_when_complete():
    assert project_solver._solver_completion_exit_code({"needs_more_iterations": False}) == 0


def test_solver_completion_exit_code_is_nonzero_when_incomplete():
    assert project_solver._solver_completion_exit_code({"needs_more_iterations": True}) == 2


def test_solver_completion_exit_code_defaults_to_zero_without_summary():
    assert project_solver._solver_completion_exit_code(None) == 0

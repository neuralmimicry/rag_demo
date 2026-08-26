from refiner import project_solver


def test_counter_requirements_get_safe_local_plan(tmp_path):
    source = project_solver.RequirementSource(
        path="requirements.md",
        requirements_text=(
            "Create index.html with increment and reset buttons. "
            "Add styles.css and app.js."
        ),
        requirement_lines=[],
        todo_lines=[],
        context_excerpt="",
    )
    intent = project_solver._classify_local_intent(source, str(tmp_path))
    plan = project_solver._build_local_plan_from_intent(
        intent,
        source,
        str(tmp_path),
        {"languages": ["javascript"], "build_systems": []},
        allow_run=True,
        required_ids={"REQ-001"},
    )

    assert intent["intent"] == "static_counter_app"
    assert plan is not None
    paths = {step["path"] for step in plan["plan"] if "path" in step}
    assert {"index.html", "styles.css", "app.js", "smoke_test.js"} <= paths
    commands = [step.get("command", "") for step in plan["plan"]]
    assert any("node --check app.js" in command for command in commands)
    assert any("http.server" in command for command in commands)

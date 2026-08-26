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
    assert plan["done"] is True
    paths = {step["path"] for step in plan["plan"] if "path" in step}
    assert {"index.html", "styles.css", "app.js", "smoke_test.js"} <= paths
    commands = [step.get("command", "") for step in plan["plan"]]
    assert any("node --check app.js" in command for command in commands)
    assert any("http.server" in command for command in commands)


def test_smoke_test_is_a_valid_runnable_example_for_a_module(tmp_path):
    module = tmp_path / "app.js"
    smoke_test = tmp_path / "smoke_test.js"
    module.write_text("// REQ-001\nconsole.log('counter');\n", encoding="utf-8")
    smoke_test.write_text("// REQ-001\nconsole.log('example');\n", encoding="utf-8")

    registry = project_solver._build_module_registry(
        {
            "requirements": [
                {"id": "REQ-001", "title": "Counter", "description": "Counter app"},
            ]
        },
        {
            "requirements.md": [
                {
                    "is_code": True,
                    "abs_path": str(module),
                    "path": "app.js",
                    "summary": "Counter module",
                    "requirement_ids": ["REQ-001"],
                },
                {
                    "is_code": True,
                    "abs_path": str(smoke_test),
                    "path": "smoke_test.js",
                    "summary": "Runnable counter smoke test",
                    "requirement_ids": ["REQ-001"],
                },
            ]
        },
        str(tmp_path),
    )

    app_entry = next(item for item in registry["modules"] if item["path"] == "app.js")
    assert app_entry["tests"] == ["smoke_test.js"]
    assert app_entry["examples"] == ["smoke_test.js"]
    assert registry["missing_examples"] == []

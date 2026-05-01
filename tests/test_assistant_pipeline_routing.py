from assistant_pipeline.routing import (
    assistant_routing_policy_from_config,
    build_assistant_rag_mcp_system_prompt,
    build_assistant_requirements_system_prompt,
    build_execution_plan_system_prompt,
    resolve_route_intent,
)


def test_resolve_route_intent_expands_requirements_profiles_when_enabled() -> None:
    policy = assistant_routing_policy_from_config({"enabled": True})

    decision = resolve_route_intent(
        route="assistant_requirements",
        payload={"mode": "ask"},
        policy=policy,
        is_marketing_assistant=True,
    )

    assert decision.intent_id == "assistant_requirements:marketing:ask"
    assert decision.prompt_profile == "marketing"
    assert decision.metadata["routing_enabled"] is True


def test_resolve_route_intent_marks_rag_only_assistant_answer_as_cacheable() -> None:
    policy = assistant_routing_policy_from_config({"enabled": True})

    decision = resolve_route_intent(
        route="assistant_rag_mcp",
        payload={"rag": {"index": "ops"}},
        policy=policy,
        has_rag=True,
        has_mcp=False,
    )

    assert decision.intent_id == "assistant_rag_mcp:rag_grounded"
    assert decision.cacheable is True
    assert decision.prompt_profile == "rag_grounded"


def test_prompt_profiles_preserve_existing_instruction_shapes() -> None:
    policy = assistant_routing_policy_from_config({"enabled": True})
    req_decision = resolve_route_intent(
        route="assistant_requirements",
        payload={"mode": "draft"},
        policy=policy,
        is_marketing_assistant=False,
    )
    rag_decision = resolve_route_intent(
        route="assistant_rag_mcp",
        payload={"rag": {"index": "ops"}},
        policy=policy,
        has_rag=True,
        has_mcp=False,
    )

    req_prompt = build_assistant_requirements_system_prompt(
        req_decision,
        gesture_mode="none",
        capabilities_hint="RAG, assistant, planner",
    )
    rag_prompt = build_assistant_rag_mcp_system_prompt(
        rag_decision,
        capabilities_hint="RAG, assistant, planner",
        skills_hint="analysis",
        rag_context_present=True,
    )

    assert "Requirements Register" in req_prompt
    assert "Capabilities summary" in req_prompt
    assert "preserve the supplied source citation labels" in rag_prompt
    assert "Relevant skills:" in rag_prompt


def test_execution_plan_route_uses_governed_prompt_profile() -> None:
    policy = assistant_routing_policy_from_config({"enabled": True})

    decision = resolve_route_intent(
        route="execution_plan",
        payload={"prompt": "Stabilise the release gate."},
        policy=policy,
    )
    prompt = build_execution_plan_system_prompt(decision)

    assert decision.intent_id == "execution_plan:governed_change"
    assert decision.prompt_profile == "execution_planner"
    assert decision.metadata["governed_execution"] is True
    assert "governed software delivery" in prompt
    assert "School Monitor" not in prompt

from refiner_ai_aarnn import AarnnEngine


def test_aarnn_offline_analysis_still_exposes_aer_context(tmp_path):
    engine = AarnnEngine(
        repo_root=str(tmp_path),
        endpoint=None,
        socket_path=None,
        sensory_size=8,
        output_size=4,
    )

    analysis = engine.analyze_task(
        "Implement an AARNN-generated SNN with an AER communication layer.",
        workflow="project_solver",
        role="planner",
    )

    assert analysis["relevant"] is True
    assert analysis["mode"] == "offline_heuristic"
    assert analysis["aer_payload_hex"].startswith("41455231")
    assert "aarnn" in analysis["specialties"]
    assert "aer" in analysis["specialties"]

    prompt_context = engine.prompt_context(
        "Implement an AARNN-generated SNN with an AER communication layer.",
        workflow="project_solver",
        role="planner",
    )
    assert "AER1" in prompt_context
    assert "AARNN" in prompt_context


def test_generic_snn_aer_spec_uses_non_aarnn_prompt_guidance(tmp_path):
    engine = AarnnEngine.from_spec(
        {
            "name": "VisionSpikes",
            "type": "snn_aer",
            "repo_root": str(tmp_path),
            "roles": ["reviewer"],
            "specialties": ["snn", "aer", "vision"],
            "guidance_lines": ["Prefer event-camera spike pipelines when reviewing sensory paths."],
            "description": "Generic SNN/AER specialist for event-driven vision.",
            "weight": 0.4,
        },
        default_name="SNN/AER Specialist",
    )

    assert engine is not None
    assert engine.engine_type == "snn_aer"
    assert engine.name == "VisionSpikes"
    assert engine.prefer_aarnn_designs is False

    analysis = engine.analyze_task(
        "Review the event-camera SNN/AER vision stack for spike routing issues.",
        workflow="project_solver",
        role="reviewer",
    )
    summary = AarnnEngine.configuration_summary_from_spec(
        {
            "name": "VisionSpikes",
            "type": "snn_aer",
            "repo_root": str(tmp_path),
            "roles": ["reviewer"],
            "specialties": ["snn", "aer", "vision"],
            "description": "Generic SNN/AER specialist for event-driven vision.",
            "weight": 0.4,
        },
        default_name="SNN/AER Specialist",
    )
    prompt_context = engine.format_prompt_context(analysis)

    assert analysis["relevant"] is True
    assert analysis["engine"] == "snn_aer"
    assert "vision" in analysis["specialties"]
    assert summary["type"] == "snn_aer"
    assert summary["name"] == "VisionSpikes"
    assert summary["description"] == "Generic SNN/AER specialist for event-driven vision."
    assert summary["health"]["mode"] == "offline_heuristic"
    assert "VisionSpikes" in prompt_context
    assert "Engine type: snn_aer" in prompt_context
    assert "event-camera spike pipelines" in prompt_context
    assert "AARNN-grown" not in prompt_context

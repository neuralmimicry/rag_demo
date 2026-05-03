from refiner import run_refiner


def test_resolve_llm_selection_falls_back_to_accessible_ollama_config():
    llm_configs = [
        {"name": "shared-openai", "type": "openai", "model": "gpt-4o-mini"},
        {
            "name": "local-ollama",
            "type": "ollama",
            "model": "llama3.2",
            "base_url": "http://ollama.neuralmimicry.ai",
        },
    ]

    provider, model, base_url, credential, matched = run_refiner._resolve_llm_selection(
        llm_configs,
        None,
        None,
        None,
        lambda name, provider_type: None,
        default_cfg=llm_configs[0],
    )

    assert provider == "ollama"
    assert model == "llama3.2"
    assert base_url == "http://ollama.neuralmimicry.ai"
    assert credential is None
    assert matched == llm_configs[1]


def test_resolve_llm_selection_does_not_keep_explicit_external_provider_without_credentials():
    llm_configs = [
        {"name": "shared-openai", "type": "openai", "model": "gpt-4o-mini"},
        {
            "name": "local-ollama",
            "type": "ollama",
            "model": "llama3.2",
            "base_url": "http://ollama.neuralmimicry.ai",
        },
    ]

    provider, model, base_url, credential, matched = run_refiner._resolve_llm_selection(
        llm_configs,
        "openai",
        "gpt-4o-mini",
        None,
        lambda name, provider_type: None,
    )

    assert provider == "ollama"
    assert model == "llama3.2"
    assert base_url == "http://ollama.neuralmimicry.ai"
    assert credential is None
    assert matched == llm_configs[1]

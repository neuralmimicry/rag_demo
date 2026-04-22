import pytest

from refiner.refiner_settings import (
    SettingsValidationError,
    metadata_with_settings,
    settings_from_metadata,
    validate_settings_patch,
)


def test_validate_settings_patch_merges_with_existing_values():
    current = {
        "llm": {
            "default_provider": "openai",
            "default_model": "gpt-5.4",
            "default_reasoning_effort": "medium",
        },
        "assistant": {
            "default_profile": "requirements",
            "use_memory": True,
        },
        "solver": {
            "command_policy_mode": "standard",
        },
        "ui": {
            "show_solver_replay": True,
        },
    }

    merged = validate_settings_patch(
        {
            "assistant": {"use_memory": False},
            "solver": {"command_policy_mode": "strict"},
        },
        current=current,
    )

    assert merged["llm"]["default_provider"] == "openai"
    assert merged["assistant"]["use_memory"] is False
    assert merged["solver"]["command_policy_mode"] == "strict"
    assert merged["ui"]["show_solver_replay"] is True


def test_validate_settings_patch_rejects_unknown_keys():
    with pytest.raises(SettingsValidationError) as exc_info:
        validate_settings_patch({"llm": {"unknown_setting": "value"}})

    assert "llm.unknown_setting" in "; ".join(exc_info.value.issues)


def test_settings_roundtrip_through_metadata_normalizes_payload():
    metadata = metadata_with_settings(
        {"source": "profile"},
        {
            "assistant": {"default_profile": "marketing"},
            "ui": {"show_solver_replay": False},
        },
        updated_at="2026-04-14T12:00:00Z",
    )

    settings = settings_from_metadata(metadata)

    assert metadata["source"] == "profile"
    assert settings["assistant"]["default_profile"] == "marketing"
    assert settings["ui"]["show_solver_replay"] is False
    assert settings["llm"]["default_reasoning_effort"] == "medium"

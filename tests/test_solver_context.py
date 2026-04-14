from solver_context import (
    PromptSection,
    assemble_prompt_sections,
    estimate_prompt_budget_chars,
)


class _Provider:
    def __init__(self, context_window: int):
        self._context_window = context_window

    def get_context_window(self) -> int:
        return self._context_window


def test_estimate_prompt_budget_caps_large_context_windows():
    provider = _Provider(1_000_000)

    budget = estimate_prompt_budget_chars(provider, llm_max_tokens=4000)

    assert budget == 120_000


def test_assemble_prompt_sections_prefers_required_and_high_priority_sections():
    sections = [
        PromptSection("required", "R" * 500, priority=100, required=True),
        PromptSection("high", "H" * 500, priority=90),
        PromptSection("low", "L" * 500, priority=10),
    ]

    result = assemble_prompt_sections(sections, budget_chars=1100)
    usage = {entry.name: entry for entry in result.sections}

    assert usage["required"].included is True
    assert usage["high"].included is True
    assert usage["low"].included is False
    assert "L" * 40 not in result.text


def test_assemble_prompt_sections_trims_when_budget_is_tight():
    sections = [
        PromptSection(
            "required",
            "A" * 600,
            priority=100,
            required=True,
            min_chars=180,
            trim_mode="middle",
        )
    ]

    result = assemble_prompt_sections(sections, budget_chars=250)

    assert result.sections[0].included is True
    assert result.sections[0].final_chars <= 250
    assert "...[truncated]..." in result.text

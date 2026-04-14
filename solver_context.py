"""Prompt budgeting helpers for the project solver.

Refiner's solver prompt is intentionally rich: requirements register, repo
search hits, test excerpts, audit findings, verification failures, previous
actions, and more. The problem is not *lack* of context but uncontrolled
prompt growth. This module scores prompt sections and assembles the best-fit
prompt for the available budget.

The implementation is intentionally deterministic and transparent:

- callers decide the section priority and minimum size,
- required sections are always retained,
- lower-priority sections are trimmed or omitted first, and
- the result reports what was kept, trimmed, and dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence


@dataclass(frozen=True)
class PromptSection:
    """One candidate section for prompt assembly."""

    name: str
    content: str
    priority: int
    required: bool = False
    min_chars: int = 0
    max_chars: Optional[int] = None
    trim_mode: str = "tail"


@dataclass(frozen=True)
class PromptSectionUsage:
    """Result metadata for one section."""

    name: str
    included: bool
    original_chars: int
    final_chars: int
    reason: str


@dataclass(frozen=True)
class PromptBudgetResult:
    """Final prompt and the inclusion report used to build it."""

    text: str
    budget_chars: int
    used_chars: int
    sections: List[PromptSectionUsage]

    @property
    def omitted_sections(self) -> List[str]:
        return [section.name for section in self.sections if not section.included]


def estimate_prompt_budget_chars(
    provider: object,
    llm_max_tokens: Optional[object],
    *,
    reserve_output_tokens: int = 2500,
    reserve_input_tokens: int = 3000,
    floor_chars: int = 20_000,
    cap_chars: int = 120_000,
) -> int:
    """Estimate a conservative prompt budget from the provider context window."""

    context_window = 128_000
    getter = getattr(provider, "get_context_window", None)
    if callable(getter):
        try:
            value = int(getter())
            if value > 4096:
                context_window = value
        except Exception:
            pass

    try:
        requested_output = int(llm_max_tokens) if llm_max_tokens is not None else 0
    except Exception:
        requested_output = 0
    requested_output = max(reserve_output_tokens, requested_output)

    usable_tokens = max(4000, context_window - requested_output - reserve_input_tokens)
    chars = usable_tokens * 4
    return max(floor_chars, min(cap_chars, chars))


def _trim_text(text: str, limit: int, mode: str) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    marker = "\n...[truncated]...\n"
    if limit <= len(marker) + 8:
        return text[:limit]
    if mode == "head":
        keep = limit - len(marker)
        return text[:keep].rstrip() + marker
    if mode == "middle":
        keep = limit - len(marker)
        left = keep // 2
        right = keep - left
        return text[:left].rstrip() + marker + text[-right:].lstrip()
    keep = limit - len(marker)
    return marker + text[-keep:].lstrip()


def _normalise_section(section: PromptSection) -> str:
    text = (section.content or "").strip()
    if not text:
        return ""
    if section.max_chars and section.max_chars > 0 and len(text) > section.max_chars:
        return _trim_text(text, section.max_chars, section.trim_mode)
    return text


def assemble_prompt_sections(
    sections: Sequence[PromptSection],
    budget_chars: int,
) -> PromptBudgetResult:
    """Assemble a prompt from prioritized sections while respecting the budget."""

    usages: List[PromptSectionUsage] = []
    included_texts: List[Optional[str]] = [None] * len(sections)
    normalised: List[str] = []
    rendered_lengths: List[int] = []

    for section in sections:
        text = _normalise_section(section)
        normalised.append(text)
        rendered_lengths.append(len(text) + 2 if text else 0)

    used = 0
    required_indices = [idx for idx, section in enumerate(sections) if section.required and normalised[idx]]
    for idx in required_indices:
        included_texts[idx] = normalised[idx]
        used += rendered_lengths[idx]

    if used > budget_chars and required_indices:
        shrinkable = sorted(
            required_indices,
            key=lambda idx: (sections[idx].priority, rendered_lengths[idx]),
        )
        for idx in shrinkable:
            section = sections[idx]
            current = included_texts[idx] or ""
            minimum = max(120, int(section.min_chars or 0))
            if len(current) <= minimum:
                continue
            remaining_required = used - rendered_lengths[idx]
            allowed = max(minimum, budget_chars - remaining_required - 2)
            trimmed = _trim_text(current, allowed, section.trim_mode)
            used -= rendered_lengths[idx]
            included_texts[idx] = trimmed
            rendered_lengths[idx] = len(trimmed) + 2
            used += rendered_lengths[idx]
            if used <= budget_chars:
                break

    optional_indices = [
        idx
        for idx, section in enumerate(sections)
        if not section.required and normalised[idx]
    ]
    optional_indices.sort(key=lambda idx: (-sections[idx].priority, idx))

    for idx in optional_indices:
        section = sections[idx]
        text = normalised[idx]
        if not text:
            continue
        remaining = budget_chars - used
        if remaining <= 0:
            continue
        if rendered_lengths[idx] <= remaining:
            included_texts[idx] = text
            used += rendered_lengths[idx]
            continue
        minimum = max(160, int(section.min_chars or 0))
        if remaining < minimum:
            continue
        trimmed = _trim_text(text, max(0, remaining - 2), section.trim_mode)
        if not trimmed:
            continue
        included_texts[idx] = trimmed
        used += len(trimmed) + 2

    rendered_parts: List[str] = []
    for idx, section in enumerate(sections):
        original_text = normalised[idx]
        included_text = included_texts[idx]
        if included_text:
            rendered_parts.append(included_text)
            if len(included_text) < len(original_text):
                reason = "trimmed"
            elif section.max_chars and len(original_text) < len((section.content or "").strip()):
                reason = "capped"
            else:
                reason = "included"
            usages.append(
                PromptSectionUsage(
                    name=section.name,
                    included=True,
                    original_chars=len((section.content or "").strip()),
                    final_chars=len(included_text),
                    reason=reason,
                )
            )
        else:
            usages.append(
                PromptSectionUsage(
                    name=section.name,
                    included=False,
                    original_chars=len((section.content or "").strip()),
                    final_chars=0,
                    reason="omitted",
                )
            )

    text = "\n\n".join(part for part in rendered_parts if part).strip()
    return PromptBudgetResult(
        text=text,
        budget_chars=max(0, int(budget_chars)),
        used_chars=len(text),
        sections=usages,
    )

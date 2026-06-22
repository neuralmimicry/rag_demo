"""Prompt-profile helpers for routed assistant and RAG intents."""

from __future__ import annotations

from assistant_pipeline.routing.intent_router import RouteIntent


def build_assistant_requirements_system_prompt(
    decision: RouteIntent,
    *,
    gesture_mode: str,
    capabilities_hint: str = "",
    marketing_vocab_hint: str = "",
    persona_guidance: str = "",
    channel_guidance: str = "",
) -> str:
    """Build the system prompt for requirements and marketing assistant flows."""

    if decision.prompt_profile == "marketing":
        system = (
            "You are the NeuralMimicry marketing assistant. Answer questions about NeuralMimicry, its products, "
            "and its services in a concise, helpful, business-oriented tone. "
            "For simple greetings, respond in 1-3 short sentences, introduce your role briefly, and invite a relevant next question. "
            "Do not output requirements-document structures unless explicitly asked to draft requirements."
        )
        if gesture_mode == "bsl":
            system = (
                f"{system} "
                "The frontend avatar can sign in BSL, so do not claim that you cannot sign or gesture physically."
            )
        if marketing_vocab_hint:
            system = f"{system}\n\nSpeech/STT vocabulary hints:\n{marketing_vocab_hint}"
    else:
        system = (
            "You are a requirements assistant. Help the user craft clear, testable requirements. "
            "Ask concise clarifying questions when needed. "
            "When drafting, output structured Markdown with sections: Overview, Goals, Non-Goals, "
            "Functional Requirements, Non-Functional Requirements, Acceptance Criteria, Risks. "
            "Include a 'Requirements Register' section with one requirement per line in the format "
            "'- REQ-001: Short title' (zero-padded, unique IDs). Add any detail as indented bullets "
            "beneath each REQ line so the register can be parsed."
        )
    if persona_guidance:
        system = f"{system}\n\nPersona guidance:\n{persona_guidance}"
    if channel_guidance:
        system = f"{system}\n\nChannel guidance:\n{channel_guidance}"
    if capabilities_hint:
        system = f"{system}\n\nCapabilities summary:\n{capabilities_hint}"
    return system


def build_assistant_rag_mcp_system_prompt(
    decision: RouteIntent,
    *,
    capabilities_hint: str = "",
    skills_hint: str = "",
    rag_context_present: bool = False,
    persona_guidance: str = "",
    channel_guidance: str = "",
) -> str:
    """Build the system prompt for RAG and MCP-assisted answering."""

    system_lines = [
        "You are a practical, concise assistant. Use UK British English spelling.",
        "Use the provided RAG context and MCP data where relevant.",
        "If the context is insufficient, state what is missing.",
        "Prefer RAG for stable unstructured context and MCP for live structured data/actions.",
    ]
    if decision.prompt_profile == "mcp_live":
        system_lines.append("Treat MCP data as the primary live source of truth for this request.")
    elif decision.prompt_profile == "rag_grounded":
        system_lines.append("Ground the answer in retrieved evidence and avoid unsupported extrapolation.")
    elif decision.prompt_profile == "rag_mcp_live":
        system_lines.append("Combine retrieved evidence with live MCP data and state clearly when they diverge.")
    if capabilities_hint:
        system_lines.extend(["Capabilities summary:", capabilities_hint])
    if skills_hint:
        system_lines.extend(["Relevant skills:", skills_hint])
    if rag_context_present:
        system_lines.append(
            "When using RAG context, preserve the supplied source citation labels, "
            "including page/block locators, in the answer where they support factual claims."
        )
    if persona_guidance:
        system_lines.extend(["Persona guidance:", persona_guidance])
    if channel_guidance:
        system_lines.extend(["Channel guidance:", channel_guidance])
    return "\n".join(system_lines)


def build_assistant_form_fill_system_prompt(
    decision: RouteIntent,
    *,
    capabilities_hint: str = "",
    channel_guidance: str = "",
) -> str:
    """Build the system prompt for structured form filling."""

    system = (
        "You are a form assistant. Return ONLY valid JSON. "
        "Output an array of objects with keys: field_id, value, rationale (optional). "
        "Only use field_id values from the allowed list. "
        "Do not include markdown or extra text."
    )
    if capabilities_hint:
        system = f"{system}\n\nCapabilities summary:\n{capabilities_hint}"
    if channel_guidance:
        system = f"{system}\n\nChannel guidance:\n{channel_guidance}"
    return system


def build_playground_plan_system_prompt(decision: RouteIntent) -> str:
    """Build the system prompt for the playground planning flow."""

    return (
        "You are School Monitor, a friendly assistant for non-technical pupils. "
        "Use UK British English spelling and phrasing. "
        "Keep responses short, simple, and upbeat. Return ONLY valid JSON with keys: "
        "summary (string), steps (array of strings), requirements_text (string), project_name (string). "
        "Summary should be 1-2 sentences. Steps should be 4-7 short, easy-to-follow items. "
        "Requirements text should be brief and practical, include a short overview and a "
        "'Requirements Register' section with 6-10 lines formatted like '- REQ-001: ...'. "
        "Keep the scope small and fast to build. "
        "If the project is a web app, prefer Node.js and a playful, colourful UI with cards, "
        "levels, and rewards similar to a child-friendly dashboard."
    )


def build_execution_plan_system_prompt(decision: RouteIntent) -> str:
    """Build the system prompt for governed engineering planning."""

    return (
        "You are the Refiner execution planner for governed software delivery. "
        "Use UK British English spelling and phrasing. "
        "Return ONLY valid JSON with keys: summary (string), steps (array of strings), "
        "requirements_text (string), project_name (string). "
        "Summary should be 1-3 concise engineering sentences. "
        "Steps should be 4-8 concrete technical actions. "
        "Requirements text should include a brief overview plus a "
        "'Requirements Register' section with 6-12 lines formatted like '- REQ-001: ...'. "
        "Optimise for incremental, test-backed, non-destructive implementation in an existing codebase. "
        "Include explicit verification, rollout, and operational notes when the prompt implies delivery or runtime impact. "
        "Do not use child-focused wording, classroom framing, or playful UI assumptions."
    )

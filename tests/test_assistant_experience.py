from assistant_pipeline.experience import (
    assistant_experience_response_meta,
    channel_prompt_guidance,
    derive_engagement_markers,
    normalise_channel_context,
    persona_prompt_guidance,
    resolve_assistant_persona,
)


def test_normalise_channel_context_handles_string_and_context_map():
    context = normalise_channel_context(
        {
            "channel": "whatsapp",
            "channel_context": {
                "external_user_id": "user-42",
                "handoff_requested": True,
                "handoff_reason": "Needs account manager",
            },
        }
    )

    assert context["name"] == "whatsapp"
    assert context["external_user_id"] == "user-42"
    assert context["handoff_requested"] is True
    assert context["handoff_reason"] == "Needs account manager"
    assert "handoff" in channel_prompt_guidance(context).lower()


def test_resolve_assistant_persona_merges_custom_overrides():
    persona = resolve_assistant_persona(
        {
            "assistant_profile": "sales",
            "assistant_profile_config": {
                "tone": "formal and concise",
                "constraints": ["Keep claims grounded."],
            },
        },
        default_profile="requirements",
    )

    guidance = persona_prompt_guidance(persona)
    assert persona["id"] == "sales"
    assert persona["tone"] == "formal and concise"
    assert persona["constraints"] == ["Keep claims grounded."]
    assert "Persona profile" in guidance


def test_derive_engagement_markers_infers_conversion_and_sentiment():
    markers = derive_engagement_markers(
        payload={"goal_completed": True},
        channel_context={"name": "web", "handoff_requested": False},
        reply_text="Great result, this is now fixed and successful.",
        atlassian_result={"status": "applied"},
    )
    meta = assistant_experience_response_meta(
        channel_context={"name": "web"},
        persona={"id": "support"},
        markers=markers,
    )

    assert markers["sentiment_label"] == "positive"
    assert markers["conversion_completed"] is True
    assert meta["assistant_profile"] == "support"
    assert meta["channel"] == "web"


def test_normalise_channel_context_supports_voice_and_messaging_aliases():
    telegram = normalise_channel_context({"channel": "tg"})
    google_home = normalise_channel_context({"channel": "google home"})
    alexa = normalise_channel_context({"channel_context": {"name": "amazon_alexa"}})

    assert telegram["name"] == "telegram"
    assert google_home["name"] == "google_home"
    assert alexa["name"] == "alexa"

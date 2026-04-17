import json
import logging
from pathlib import Path
import time

from llm_providers import LLMResponse
from refiner_ai_orchestration import orchestrate_provider_candidates, orchestration_status
from refiner_ai_specialists import analyze_specialist_engines


class _FakeProvider:
    def __init__(self, name: str, model: str, text: str, delay: float = 0.0):
        self.name = name
        self.model = model
        self.text = text
        self.delay = delay
        self.calls = []

    def predict(
        self,
        messages,
        max_tokens=None,
        temperature=0.2,
        system=None,
        timeout=None,
        reasoning_effort=None,
    ):
        self.calls.append(
            {
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system,
                "timeout": timeout,
                "reasoning_effort": reasoning_effort,
                "started_at": time.time(),
            }
        )
        time.sleep(self.delay)
        return LLMResponse(
            text=self.text,
            raw={"provider": self.name, "model": self.model},
            provider=self.name,
            model=self.model,
        )

    def health_check(self, timeout=None):
        _ = timeout
        return {"ok": True, "latency_ms": int(self.delay * 1000)}

    def estimate_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)

    def get_context_window(self) -> int:
        return 32000


class _FallbackModelProvider(_FakeProvider):
    def __init__(self, name: str, model: str, fallback_model: str, text: str, delay: float = 0.0):
        super().__init__(name=name, model=model, text=text, delay=delay)
        self.fallback_model = fallback_model

    def predict(
        self,
        messages,
        max_tokens=None,
        temperature=0.2,
        system=None,
        timeout=None,
        reasoning_effort=None,
    ):
        self.calls.append(
            {
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system,
                "timeout": timeout,
                "reasoning_effort": reasoning_effort,
                "started_at": time.time(),
            }
        )
        time.sleep(self.delay)
        self.model = self.fallback_model
        return LLMResponse(
            text=self.text,
            raw={"provider": self.name, "model": self.model},
            provider=self.name,
            model=self.model,
        )


class _FakeSpecialistEngine:
    def __init__(
        self,
        name: str,
        *,
        engine_type: str,
        specialties,
        roles=None,
        delay: float = 0.0,
        score: float = 0.5,
        weight: float = 0.0,
    ):
        self.name = name
        self.engine_type = engine_type
        self.specialties = list(specialties)
        self.roles = list(roles or [])
        self.delay = delay
        self.score = score
        self.weight = weight
        self.calls = []

    def analyze_task(self, text, *, workflow, role):
        self.calls.append({"text": text, "workflow": workflow, "role": role, "started_at": time.time()})
        time.sleep(self.delay)
        return {
            "engine": self.engine_type,
            "engine_name": self.name,
            "relevant": True,
            "score": self.score,
            "roles": list(self.roles),
            "specialties": list(self.specialties),
            "weight": self.weight,
        }

    def format_prompt_context(self, analysis):
        return f"{analysis['engine_name']} specialist context"


def test_orchestrator_prefers_valid_json_and_runs_candidates_concurrently(tmp_path, monkeypatch):
    invalid = _FakeProvider("fast_invalid", "v1", "not json", delay=0.12)
    valid = _FakeProvider("slow_valid", "v2", '{"summary": "ok"}', delay=0.12)
    metrics_path = tmp_path / "provider_metrics.json"
    monkeypatch.setenv("REFINER_AI_REGISTRY_PATH", str(metrics_path))
    monkeypatch.setenv("REFINER_AARNN_ENABLED", "0")

    provider = orchestrate_provider_candidates(
        [invalid, valid],
        workflow="playground_plan",
        role="planner",
        include_configured=False,
        config_path=str(tmp_path / "missing-config.json"),
        selection_mode="best",
        max_candidates=2,
    )
    assert provider is not None

    start = time.time()
    response = provider.predict(
        messages=[{"role": "user", "content": "Build a reading quiz."}],
        system="Return ONLY valid JSON with keys: summary",
    )
    elapsed = time.time() - start

    assert response.provider == "slow_valid"
    assert json.loads(response.text)["summary"] == "ok"
    assert elapsed < 0.22
    assert invalid.calls
    assert valid.calls


def test_orchestrator_best_mode_returns_early_for_interactive_high_quality_success(tmp_path, monkeypatch):
    fast = _FakeProvider("fast", "m1", "NeuralMimicry is an adaptive neuromorphic AI platform.", delay=0.02)
    slow = _FakeProvider("slow", "m2", "A slower but still valid marketing answer.", delay=0.25)
    metrics_path = tmp_path / "provider_metrics.json"
    monkeypatch.setenv("REFINER_AI_REGISTRY_PATH", str(metrics_path))
    monkeypatch.setenv("REFINER_AI_EARLY_SUCCESS_SETTLE_SECONDS", "0.03")
    monkeypatch.setenv("REFINER_AARNN_ENABLED", "0")

    provider = orchestrate_provider_candidates(
        [fast, slow],
        workflow="assistant_requirements",
        role="assistant",
        include_configured=False,
        config_path=str(tmp_path / "missing-config.json"),
        selection_mode="best",
        max_candidates=2,
    )
    assert provider is not None

    start = time.time()
    response = provider.predict(
        messages=[{"role": "user", "content": "What is NeuralMimicry?"}],
        system="Answer in concise prose.",
    )
    elapsed = time.time() - start

    assert response.provider == "fast"
    assert elapsed < 0.16
    assert fast.calls[0]["timeout"] == 45
    assert response.raw["refiner_ai"]["returned_early"] is True


def test_orchestrator_best_mode_still_waits_for_slow_valid_json_after_fast_invalid_response(tmp_path, monkeypatch):
    invalid = _FakeProvider("fast_invalid", "v1", "not json", delay=0.02)
    valid = _FakeProvider("slow_valid", "v2", '{"summary": "ok"}', delay=0.08)
    metrics_path = tmp_path / "provider_metrics.json"
    monkeypatch.setenv("REFINER_AI_REGISTRY_PATH", str(metrics_path))
    monkeypatch.setenv("REFINER_AI_EARLY_SUCCESS_SETTLE_SECONDS", "0.01")
    monkeypatch.setenv("REFINER_AARNN_ENABLED", "0")

    provider = orchestrate_provider_candidates(
        [invalid, valid],
        workflow="assistant_requirements",
        role="assistant",
        include_configured=False,
        config_path=str(tmp_path / "missing-config.json"),
        selection_mode="best",
        max_candidates=2,
    )
    assert provider is not None

    start = time.time()
    response = provider.predict(
        messages=[{"role": "user", "content": "Build a reading quiz."}],
        system="Return ONLY valid JSON with keys: summary",
    )
    elapsed = time.time() - start

    assert response.provider == "slow_valid"
    assert json.loads(response.text)["summary"] == "ok"
    assert elapsed >= 0.07
    assert elapsed < 0.16


def test_orchestrator_fastest_mode_returns_first_acceptable_success(tmp_path, monkeypatch):
    fast = _FakeProvider("fast", "m1", "A quick acceptable answer.", delay=0.02)
    slow = _FakeProvider("slow", "m2", "A much slower acceptable answer.", delay=0.25)
    metrics_path = tmp_path / "provider_metrics.json"
    monkeypatch.setenv("REFINER_AI_REGISTRY_PATH", str(metrics_path))
    monkeypatch.setenv("REFINER_AARNN_ENABLED", "0")

    provider = orchestrate_provider_candidates(
        [fast, slow],
        workflow="assistant_requirements",
        role="assistant",
        include_configured=False,
        config_path=str(tmp_path / "missing-config.json"),
        selection_mode="fastest",
        max_candidates=2,
    )
    assert provider is not None

    start = time.time()
    response = provider.predict(
        messages=[{"role": "user", "content": "Say hello."}],
        system="Answer in concise prose.",
    )
    elapsed = time.time() - start

    assert response.provider == "fast"
    assert elapsed < 0.12
    assert response.raw["refiner_ai"]["returned_early"] is True


def test_orchestrator_returns_single_provider_unchanged_when_no_alt_candidates(monkeypatch):
    solo = _FakeProvider("solo", "m1", '{"summary": "ok"}')
    monkeypatch.setenv("REFINER_AARNN_ENABLED", "0")

    provider = orchestrate_provider_candidates(
        [solo],
        workflow="assistant_requirements",
        role="assistant",
        include_configured=False,
        config_path="tests/does-not-exist.json",
    )

    assert provider is solo


def test_specialist_engines_run_concurrently_and_merge_context():
    aarnn = _FakeSpecialistEngine(
        "AARNNPrimary",
        engine_type="aarnn",
        specialties=["aarnn", "aer", "snn"],
        roles=["planner"],
        delay=0.15,
        score=0.55,
        weight=0.05,
    )
    vision = _FakeSpecialistEngine(
        "VisionSpikes",
        engine_type="snn_aer",
        specialties=["snn", "aer", "vision"],
        roles=["planner"],
        delay=0.15,
        score=0.5,
        weight=0.2,
    )

    started = time.time()
    meta = analyze_specialist_engines(
        [aarnn, vision],
        text="Design an AER spiking vision controller.",
        workflow="project_solver",
        role="planner",
    )
    elapsed = time.time() - started

    assert elapsed < 0.27
    assert meta["relevant"] is True
    assert meta["engine_count"] == 2
    assert meta["selected"]["engine_name"] == "VisionSpikes"
    assert {entry["engine"] for entry in meta["engines"]} == {"aarnn", "snn_aer"}
    assert "AARNNPrimary specialist context" in meta["context"]
    assert "VisionSpikes specialist context" in meta["context"]
    assert "vision" in meta["combined_specialties"]


def test_orchestrator_wraps_single_provider_when_specialist_engines_present(monkeypatch):
    solo = _FakeProvider("solo", "m1", '{"summary": "ok"}')
    specialist = _FakeSpecialistEngine(
        "AARNNPrimary",
        engine_type="aarnn",
        specialties=["aarnn", "aer", "snn"],
        roles=["assistant"],
        score=0.8,
    )
    monkeypatch.setenv("REFINER_AARNN_ENABLED", "0")
    monkeypatch.setenv("REFINER_SPECIALIST_ENGINES_ALWAYS_ROUTE", "1")

    provider = orchestrate_provider_candidates(
        [solo],
        workflow="assistant_requirements",
        role="assistant",
        include_configured=False,
        config_path="tests/does-not-exist.json",
        specialist_engines=[specialist],
    )

    assert provider is not None
    assert provider is not solo

    response = provider.predict(
        messages=[{"role": "user", "content": "Design an AER-based assistant workflow."}],
        system="Return ONLY valid JSON with keys: summary",
    )

    assert response.provider == "solo"
    assert response.raw["refiner_ai"]["specialist_engines"]["engine_count"] == 1
    assert "AARNNPrimary specialist context" in (solo.calls[0]["system"] or "")


def test_orchestrator_logs_dispatch_and_resolved_runtime_model(tmp_path, monkeypatch, caplog):
    invalid = _FakeProvider("openai", "gpt-4o-mini", "not json", delay=0.02)
    fallback = _FallbackModelProvider(
        "gemini",
        "gemini-1.5-flash",
        "gemini-2.5-flash",
        '{"summary": "ok"}',
        delay=0.02,
    )
    actions_log = []
    metrics_path = tmp_path / "provider_metrics.json"
    monkeypatch.setenv("REFINER_AI_REGISTRY_PATH", str(metrics_path))
    monkeypatch.setenv("REFINER_AARNN_ENABLED", "0")
    caplog.set_level(logging.INFO)

    provider = orchestrate_provider_candidates(
        [invalid, fallback],
        workflow="playground_plan",
        role="planner",
        include_configured=False,
        config_path=str(tmp_path / "missing-config.json"),
        selection_mode="best",
        max_candidates=2,
        actions_log=actions_log,
    )
    assert provider is not None

    response = provider.predict(
        messages=[{"role": "user", "content": "Build a reading quiz."}],
        system="Return ONLY valid JSON with keys: summary",
    )

    selected = response.raw["refiner_ai"]["selected"]
    candidates = response.raw["refiner_ai"]["candidates"]

    assert response.provider == "gemini"
    assert response.model == "gemini-2.5-flash"
    assert selected["model"] == "gemini-2.5-flash"
    assert selected["configured_model"] == "gemini-1.5-flash"
    assert any(item["model"] == "gemini-2.5-flash" for item in candidates)
    assert any(
        "AI orchestration dispatch playground_plan:planner to 2/2 candidate(s)" in entry
        for entry in actions_log
    )
    assert any(
        "AI orchestration result playground_plan:planner <- gemini/gemini-2.5-flash (configured gemini-1.5-flash)"
        in entry
        for entry in actions_log
    )
    assert any(
        "AI orchestration selected gemini/gemini-2.5-flash (configured gemini-1.5-flash)"
        in entry
        for entry in actions_log
    )
    assert "AI orchestration dispatch playground_plan:planner to 2/2 candidate(s)" in caplog.text
    assert "AI orchestration result playground_plan:planner <- gemini/gemini-2.5-flash (configured gemini-1.5-flash)" in caplog.text
    assert "AI orchestration selected gemini/gemini-2.5-flash (configured gemini-1.5-flash)" in caplog.text


def test_orchestrator_auto_attaches_aarnn_alongside_generic_specialists(tmp_path, monkeypatch):
    generic_root = tmp_path / "generic_snn"
    generic_root.mkdir()
    aarnn_root = tmp_path / "aarnn_rust"
    aarnn_root.mkdir()
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "ai_orchestration": {
                    "enabled": True,
                    "engines": [
                        {
                            "name": "VisionSpikes",
                            "type": "snn_aer",
                            "repo_root": str(generic_root),
                            "roles": ["assistant"],
                            "specialties": ["snn", "aer", "vision"],
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    solo = _FakeProvider("solo", "m1", '{"summary": "ok"}')
    monkeypatch.setenv("REFINER_AARNN_ENABLED", "1")
    monkeypatch.setenv("REFINER_AARNN_REPO_ROOT", str(aarnn_root))
    monkeypatch.setenv("REFINER_AARNN_SOCKET", str(tmp_path / "missing.sock"))
    monkeypatch.delenv("REFINER_AARNN_ENDPOINT", raising=False)
    monkeypatch.setenv("REFINER_SPECIALIST_ENGINES_ALWAYS_ROUTE", "1")

    provider = orchestrate_provider_candidates(
        [solo],
        workflow="assistant_requirements",
        role="assistant",
        include_configured=False,
        config_path=str(config_path),
    )

    assert provider is not None
    assert provider is not solo

    response = provider.predict(
        messages=[{"role": "user", "content": "Design an AER spiking assistant workflow."}],
        system="Return ONLY valid JSON with keys: summary",
    )
    specialist_meta = response.raw["refiner_ai"]["specialist_engines"]

    assert specialist_meta["engine_count"] == 2
    assert {entry["engine"] for entry in specialist_meta["engines"]} == {"aarnn", "snn_aer"}
    assert "VisionSpikes" in specialist_meta["context"]
    assert "AARNN" in specialist_meta["context"]


def test_orchestration_status_summarises_registry_and_aarnn(tmp_path, monkeypatch):
    repo_root = tmp_path / "aarnn_rust"
    repo_root.mkdir()
    metrics_path = tmp_path / "provider_metrics.json"
    metrics_path.write_text(
        json.dumps(
            {
                "updated_at": 1710000000.0,
                "candidates": {
                    "openai/gpt-4o-mini": {
                        "provider": "openai",
                        "model": "gpt-4o-mini",
                        "specialties": ["planning", "json"],
                        "stats": {
                            "successes": 8,
                            "failures": 2,
                            "total": 10,
                            "ewma_latency_ms": 420.5,
                            "ewma_quality": 1.9,
                            "last_status": "success",
                        },
                        "health": {"ok": True, "mode": "healthy", "checked_at": 1710000000.0},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "llm_providers": [
                    {"name": "OpenAIPrimary", "type": "openai", "model": "gpt-4o-mini"}
                ],
                "ai_orchestration": {
                    "enabled": True,
                    "registry_path": str(metrics_path),
                    "providers": [
                        {
                            "name": "GeminiResearch",
                            "provider": "gemini",
                            "model": "gemini-1.5-flash",
                            "roles": ["researcher"],
                            "specialties": ["research", "citations"],
                            "weight": 0.3,
                        }
                    ],
                    "engines": [
                        {
                            "name": "AARNNNeuromorphic",
                            "type": "aarnn",
                            "repo_root": str(repo_root),
                            "roles": ["planner", "assistant"],
                            "specialties": ["aarnn", "snn", "neuromorphic", "aer"],
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("REFINER_AI_REGISTRY_PATH", raising=False)
    monkeypatch.setenv("REFINER_AARNN_ENABLED", "1")
    monkeypatch.setenv("REFINER_AARNN_SOCKET", str(tmp_path / "missing.sock"))
    monkeypatch.delenv("REFINER_AARNN_ENDPOINT", raising=False)

    status = orchestration_status(
        config_path=str(config_path),
        probe_engines=False,
        candidate_limit=5,
    )

    assert status["enabled"] is True
    assert status["provider_count"] == 2
    assert status["metrics"]["candidate_count"] == 1
    assert status["metrics"]["candidates"][0]["candidate_id"] == "openai/gpt-4o-mini"
    assert status["engines"][0]["type"] == "aarnn"
    assert status["engines"][0]["available"] is True
    assert status["engines"][0]["health"]["mode"] == "offline_heuristic"


def test_orchestration_status_includes_generic_and_auto_attached_aarnn(tmp_path, monkeypatch):
    generic_root = tmp_path / "generic_snn"
    generic_root.mkdir()
    aarnn_root = tmp_path / "aarnn_rust"
    aarnn_root.mkdir()
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "ai_orchestration": {
                    "enabled": True,
                    "engines": [
                        {
                            "name": "VisionSpikes",
                            "type": "snn_aer",
                            "repo_root": str(generic_root),
                            "roles": ["planner"],
                            "specialties": ["snn", "aer", "vision"],
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("REFINER_AARNN_ENABLED", "1")
    monkeypatch.setenv("REFINER_AARNN_REPO_ROOT", str(aarnn_root))
    monkeypatch.setenv("REFINER_AARNN_SOCKET", str(tmp_path / "missing.sock"))
    monkeypatch.delenv("REFINER_AARNN_ENDPOINT", raising=False)

    status = orchestration_status(
        config_path=str(config_path),
        probe_engines=False,
        candidate_limit=5,
    )

    assert status["engine_count"] == 2
    assert {engine["type"] for engine in status["engines"]} == {"aarnn", "snn_aer"}
    assert {engine["name"] for engine in status["engines"]} == {"AARNN", "VisionSpikes"}
    assert all(engine["health"]["mode"] == "offline_heuristic" for engine in status["engines"])


def test_orchestration_status_reports_routing_contract_path_and_version(tmp_path, monkeypatch):
    contract_path = tmp_path / "ai-routing-profiles.json"
    contract_path.write_text(
        json.dumps(
            {
                "version": 7,
                "workflow_profiles": {
                    "assistant_requirements": {
                        "general": ["requirements"],
                        "assistant": ["json"],
                    }
                },
                "keyword_tags": {"json": ["json"]},
                "provider_specialties": {"openai": ["code"]},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("REFINER_AI_ROUTING_PROFILES_PATH", str(contract_path))

    status = orchestration_status(
        config_path=str(tmp_path / "missing-config.json"),
        include_metrics=False,
    )

    assert status["routing_profiles_path"] == str(contract_path.resolve())
    assert status["routing_profiles_version"] == 7


def test_refiner_routing_contract_matches_gail_copy_when_available():
    refiner_path = Path(__file__).resolve().parents[1] / "config" / "ai-routing-profiles.json"
    gail_path = Path(__file__).resolve().parents[2] / "gail" / "config" / "ai-routing-profiles.json"
    if not gail_path.is_file():
        return

    assert refiner_path.read_text(encoding="utf-8") == gail_path.read_text(encoding="utf-8")

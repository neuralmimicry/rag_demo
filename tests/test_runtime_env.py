from runtime_env import (
    apply_managed_ollama_defaults,
    build_effective_llm_env,
    is_cluster_local_url,
    is_loopback_url,
)


def test_is_loopback_url_detects_local_hosts():
    assert is_loopback_url("http://localhost:11434")
    assert is_loopback_url("localhost:11434")
    assert is_loopback_url("http://127.0.0.1:11434")
    assert is_loopback_url("http://[::1]:11434")
    assert is_loopback_url("http://0.0.0.0:11434")
    assert not is_loopback_url("http://ollama.ollama.svc.cluster.local:11434")


def test_is_cluster_local_url_detects_kubernetes_service_hosts():
    assert is_cluster_local_url("http://ollama.ollama.svc.cluster.local:11434")
    assert is_cluster_local_url("http://ollama.ollama.svc:11434")
    assert not is_cluster_local_url("http://ollama.neuralmimicry.ai")


def test_apply_managed_ollama_defaults_replaces_loopback_base_url():
    env = {"OLLAMA_BASE_URL": "http://localhost:11434"}
    process_env = {
        "OLLAMA_BASE_URL": "http://ollama.neuralmimicry.ai",
        "OLLAMA_DEFAULT_MODEL": "llama3.2",
        "OLLAMA_MODEL": "llama3.2",
        "SOLVER_OLLAMA_MODEL": "llama3.2",
    }

    apply_managed_ollama_defaults(env, process_env=process_env)

    assert env["OLLAMA_BASE_URL"] == "http://ollama.neuralmimicry.ai"
    assert env["OLLAMA_DEFAULT_MODEL"] == "llama3.2"
    assert env["OLLAMA_MODEL"] == "llama3.2"
    assert env["SOLVER_OLLAMA_MODEL"] == "llama3.2"


def test_apply_managed_ollama_defaults_replaces_cluster_local_base_url():
    env = {"OLLAMA_BASE_URL": "http://ollama.ollama.svc.cluster.local:11434"}
    process_env = {"OLLAMA_BASE_URL": "http://ollama.neuralmimicry.ai"}

    apply_managed_ollama_defaults(env, process_env=process_env)

    assert env["OLLAMA_BASE_URL"] == "http://ollama.neuralmimicry.ai"


def test_apply_managed_ollama_defaults_preserves_explicit_remote_override():
    env = {"OLLAMA_BASE_URL": "https://custom-ollama.example.com"}
    process_env = {"OLLAMA_BASE_URL": "http://ollama.neuralmimicry.ai"}

    apply_managed_ollama_defaults(env, process_env=process_env)

    assert env["OLLAMA_BASE_URL"] == "https://custom-ollama.example.com"


def test_build_effective_llm_env_uses_process_defaults_when_secret_missing():
    env = build_effective_llm_env(
        {"OPENAI_API_KEY": "user-key"},
        process_env={
            "OLLAMA_BASE_URL": "http://ollama.neuralmimicry.ai",
            "OLLAMA_DEFAULT_MODEL": "llama3.2",
        },
    )

    assert env["OPENAI_API_KEY"] == "user-key"
    assert env["OLLAMA_BASE_URL"] == "http://ollama.neuralmimicry.ai"
    assert env["OLLAMA_DEFAULT_MODEL"] == "llama3.2"


def test_build_effective_llm_env_replaces_cluster_local_secret_base_url():
    env = build_effective_llm_env(
        {"OLLAMA_BASE_URL": "http://ollama.ollama.svc.cluster.local:11434"},
        process_env={"OLLAMA_BASE_URL": "http://ollama.neuralmimicry.ai"},
    )

    assert env["OLLAMA_BASE_URL"] == "http://ollama.neuralmimicry.ai"

"""
Auto-select the lowest-cost platform/tooling to run project workflows.

The selector detects local tooling (QEMU, Podman, Docker, Kubernetes, OpenShift,
AWS, GCP, Azure CLIs) and project signals (Dockerfiles, k8s manifests, Terraform
providers) to pick the minimal viable tier for iterative delivery.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import os
import platform
import re
import shutil

from repo_context import DEFAULT_IGNORED_DIRS


DEFAULT_PLATFORM_ORDER = ["local", "container", "k8s", "openshift", "cloud"]
DEFAULT_CONTAINER_ORDER = ["podman", "docker"]
DEFAULT_CLOUD_ORDER = ["gcp", "aws", "azure"]


@dataclass
class PlatformSelection:
    tier: str
    engine: Optional[str]
    provider: Optional[str]
    available: bool
    reason: str
    detected: Dict[str, Any]
    env: Dict[str, str]


def _normalize_arch(value: str) -> str:
    raw = (value or "").lower()
    if raw in {"x86_64", "amd64"}:
        return "x86_64"
    if raw in {"aarch64", "arm64"}:
        return "aarch64"
    if raw.startswith("armv7"):
        return "armv7"
    if raw in {"i386", "i686", "x86"}:
        return "x86"
    return raw


def _find_files(root: str, names: List[str]) -> bool:
    if not os.path.isdir(root):
        return False
    for name in names:
        if os.path.exists(os.path.join(root, name)):
            return True
    return False


def _scan_for_k8s(root: str) -> bool:
    hits = 0
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORED_DIRS]
        for name in files:
            if not name.endswith(('.yml', '.yaml')):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                    content = handle.read(4096)
            except Exception:
                continue
            if "apiVersion" in content and "kind:" in content:
                hits += 1
                if hits >= 2:
                    return True
        if hits:
            return True
    return False


def _scan_for_openshift(root: str) -> bool:
    tokens = ["route.openshift.io", "DeploymentConfig", "BuildConfig", "ImageStream", "OpenShift"]
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORED_DIRS]
        for name in files:
            if not name.endswith(('.yml', '.yaml')):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                    content = handle.read(4096)
            except Exception:
                continue
            if any(token in content for token in tokens):
                return True
    return False


def _scan_terraform_providers(root: str) -> List[str]:
    providers = set()
    pattern = re.compile(r"provider\s+\"(?P<name>[a-z0-9_-]+)\"", re.IGNORECASE)
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORED_DIRS]
        for name in files:
            if not name.endswith('.tf'):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                    content = handle.read(8192)
            except Exception:
                continue
            for match in pattern.finditer(content):
                providers.add(match.group("name").lower())
    return sorted(providers)


def _detect_tools() -> Dict[str, bool]:
    return {
        "qemu": any(shutil.which(cmd) for cmd in ["qemu-system-x86_64", "qemu-system-aarch64", "qemu-img"]),
        "podman": shutil.which("podman") is not None,
        "docker": shutil.which("docker") is not None,
        "kubectl": shutil.which("kubectl") is not None,
        "oc": shutil.which("oc") is not None,
        "gcloud": shutil.which("gcloud") is not None,
        "aws": shutil.which("aws") is not None,
        "az": shutil.which("az") is not None,
    }


def detect_signals(project_root: str) -> Dict[str, Any]:
    dockerfile = _find_files(project_root, ["Dockerfile", "dockerfile"])
    compose = _find_files(project_root, ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"])
    k8s = _find_files(project_root, ["k8s", "kubernetes", "manifests", "helm", "charts"]) or _scan_for_k8s(project_root)
    openshift = _find_files(project_root, ["openshift", ".openshift"]) or _scan_for_openshift(project_root)
    terraform_providers = _scan_terraform_providers(project_root)

    return {
        "dockerfile": dockerfile,
        "compose": compose,
        "k8s": k8s,
        "openshift": openshift,
        "terraform_providers": terraform_providers,
    }


def _cloud_from_providers(providers: List[str]) -> List[str]:
    mapping = {
        "aws": "aws",
        "google": "gcp",
        "google-beta": "gcp",
        "azurerm": "azure",
        "azuread": "azure",
    }
    clouds = []
    for provider in providers:
        if provider in mapping and mapping[provider] not in clouds:
            clouds.append(mapping[provider])
    return clouds


def _select_cloud_provider(preferred: List[str], available: Dict[str, bool], hinted: List[str]) -> Optional[str]:
    for name in hinted:
        if name in preferred and available.get(name):
            return name
    for name in preferred:
        if available.get(name):
            return name
    for name in hinted:
        if name in preferred:
            return name
    return preferred[0] if preferred else None


def select_platform(project_root: str, config: Optional[Dict[str, Any]] = None) -> PlatformSelection:
    cfg = config or {}
    auto_mode = bool(cfg.get("auto", True))
    force_tier = cfg.get("force_tier") or cfg.get("tier")
    force_provider = cfg.get("force_provider") or cfg.get("provider")
    signals = detect_signals(project_root)
    tools = _detect_tools()

    preferred_order = list(cfg.get("preferred_order") or DEFAULT_PLATFORM_ORDER)
    container_order = list(cfg.get("container_preference") or DEFAULT_CONTAINER_ORDER)
    cloud_order = list(cfg.get("cloud_preference") or DEFAULT_CLOUD_ORDER)
    if force_tier:
        force_tier = str(force_tier).strip().lower()
        preferred_order = [force_tier] + [t for t in preferred_order if t != force_tier]

    host_arch = _normalize_arch(platform.machine())
    target_arch = _normalize_arch(str(cfg.get("target_arch") or ""))
    require_emulation = bool(cfg.get("require_emulation")) or (
        bool(target_arch) and host_arch and target_arch != host_arch
    )

    requires_container = bool(cfg.get("require_container")) or bool(signals["dockerfile"] or signals["compose"])
    requires_k8s = bool(cfg.get("require_k8s")) or bool(signals["k8s"])
    requires_openshift = bool(cfg.get("require_openshift")) or bool(signals["openshift"])
    tf_providers = signals.get("terraform_providers") or []
    cloud_hints = _cloud_from_providers(tf_providers)
    requires_cloud = bool(cfg.get("require_cloud")) or bool(cloud_hints)

    detected_options = {
        "qemu": tools["qemu"],
        "podman": tools["podman"],
        "docker": tools["docker"],
        "kubernetes": tools["kubectl"],
        "openshift": tools["oc"],
        "gcp": tools["gcloud"],
        "aws": tools["aws"],
        "azure": tools["az"],
    }

    if require_emulation:
        if tools["qemu"]:
            env = {
                "PIPELINE_PLATFORM": "qemu",
                "PIPELINE_PLATFORM_TIER": "qemu",
            }
            return PlatformSelection(
                tier="qemu",
                engine="qemu",
                provider=None,
                available=True,
                reason="target architecture mismatch; QEMU available",
                detected={"signals": signals, "tools": detected_options, "host_arch": host_arch, "target_arch": target_arch},
                env=env,
            )
        env = {"PIPELINE_PLATFORM": "qemu", "PIPELINE_PLATFORM_TIER": "qemu"}
        return PlatformSelection(
            tier="qemu",
            engine="qemu",
            provider=None,
            available=False,
            reason="target architecture mismatch; QEMU not detected",
            detected={"signals": signals, "tools": detected_options, "host_arch": host_arch, "target_arch": target_arch},
            env=env,
        )

    container_engine = None
    for engine in container_order:
        if tools.get(engine):
            container_engine = engine
            break

    k8s_cli = "oc" if tools["oc"] else ("kubectl" if tools["kubectl"] else None)

    cloud_available = {
        "gcp": tools["gcloud"],
        "aws": tools["aws"],
        "azure": tools["az"],
    }
    if force_provider:
        cloud_provider = str(force_provider).strip().lower()
    else:
        cloud_provider = _select_cloud_provider(cloud_order, cloud_available, cloud_hints)

    def _env_for(tier: str, engine: Optional[str], provider: Optional[str]) -> Dict[str, str]:
        env = {
            "PIPELINE_PLATFORM": tier,
            "PIPELINE_PLATFORM_TIER": tier,
        }
        if engine:
            env["PIPELINE_CONTAINER_ENGINE"] = engine
        if k8s_cli:
            env["PIPELINE_K8S_CLI"] = k8s_cli
        if provider:
            env["PIPELINE_CLOUD_PROVIDER"] = provider
            env["PIPELINE_CLOUD_CLI"] = {"gcp": "gcloud", "aws": "aws", "azure": "az"}.get(provider, "")
        env["PIPELINE_PLATFORM_OPTIONS"] = ",".join(k for k, v in detected_options.items() if v)
        return env

    if requires_cloud or (force_tier == "cloud" and not auto_mode):
        env = _env_for("cloud", None, cloud_provider)
        return PlatformSelection(
            tier="cloud",
            engine=None,
            provider=cloud_provider,
            available=bool(cloud_provider and cloud_available.get(cloud_provider)),
            reason="cloud provider required by config or Terraform",
            detected={"signals": signals, "tools": detected_options, "cloud_hints": cloud_hints},
            env=env,
        )

    if requires_openshift or (force_tier == "openshift" and not auto_mode):
        env = _env_for("openshift", container_engine, "openshift")
        return PlatformSelection(
            tier="openshift",
            engine=container_engine,
            provider="openshift",
            available=bool(k8s_cli == "oc"),
            reason="OpenShift manifests detected",
            detected={"signals": signals, "tools": detected_options},
            env=env,
        )

    if requires_k8s or (force_tier == "k8s" and not auto_mode):
        env = _env_for("k8s", container_engine, None)
        return PlatformSelection(
            tier="k8s",
            engine=container_engine,
            provider=None,
            available=bool(k8s_cli),
            reason="Kubernetes manifests detected",
            detected={"signals": signals, "tools": detected_options},
            env=env,
        )

    if requires_container or (force_tier == "container" and not auto_mode):
        env = _env_for("container", container_engine, None)
        return PlatformSelection(
            tier="container",
            engine=container_engine,
            provider=None,
            available=bool(container_engine),
            reason="Container build or compose files detected",
            detected={"signals": signals, "tools": detected_options},
            env=env,
        )

    if not auto_mode and force_tier == "local":
        env = _env_for("local", None, None)
        return PlatformSelection(
            tier="local",
            engine=None,
            provider=None,
            available=True,
            reason="forced to local tier",
            detected={"signals": signals, "tools": detected_options},
            env=env,
        )

    for tier in preferred_order:
        if tier == "local":
            env = _env_for("local", None, None)
            return PlatformSelection(
                tier="local",
                engine=None,
                provider=None,
                available=True,
                reason="No higher-tier requirements detected",
                detected={"signals": signals, "tools": detected_options},
                env=env,
            )
        if tier == "container" and container_engine:
            env = _env_for("container", container_engine, None)
            return PlatformSelection(
                tier="container",
                engine=container_engine,
                provider=None,
                available=True,
                reason="Container engine available",
                detected={"signals": signals, "tools": detected_options},
                env=env,
            )
        if tier == "k8s" and k8s_cli:
            env = _env_for("k8s", container_engine, None)
            return PlatformSelection(
                tier="k8s",
                engine=container_engine,
                provider=None,
                available=True,
                reason="Kubernetes CLI available",
                detected={"signals": signals, "tools": detected_options},
                env=env,
            )
        if tier == "openshift" and k8s_cli == "oc":
            env = _env_for("openshift", container_engine, "openshift")
            return PlatformSelection(
                tier="openshift",
                engine=container_engine,
                provider="openshift",
                available=True,
                reason="OpenShift CLI available",
                detected={"signals": signals, "tools": detected_options},
                env=env,
            )
        if tier == "cloud" and cloud_provider:
            env = _env_for("cloud", None, cloud_provider)
            return PlatformSelection(
                tier="cloud",
                engine=None,
                provider=cloud_provider,
                available=bool(cloud_available.get(cloud_provider)),
                reason="Cloud CLI available",
                detected={"signals": signals, "tools": detected_options, "cloud_hints": cloud_hints},
                env=env,
            )

    env = {"PIPELINE_PLATFORM": "local", "PIPELINE_PLATFORM_TIER": "local"}
    return PlatformSelection(
        tier="local",
        engine=None,
        provider=None,
        available=True,
        reason="fallback to local",
        detected={"signals": signals, "tools": detected_options},
        env=env,
    )

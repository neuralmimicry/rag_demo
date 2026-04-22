"""Versioned artefact helpers for filesystem-backed RAG collections."""

from __future__ import annotations

import json
import os
import re
import shutil
from typing import Any

from assistant_pipeline.retrieval.dense_artifacts import (
    DenseIndexArtifact,
    attach_dense_artifact,
    dense_artifact_path_for_index,
    ensure_dense_artifact,
)

_SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_segment(value: str, *, default: str) -> str:
    cleaned = _SAFE_SEGMENT_RE.sub("_", str(value or "").strip())
    return cleaned or default


def versioned_index_artifact_path(root: str, owner: str, name: str, version_id: str) -> str:
    """Return the immutable on-disk path for one collection version."""

    safe_root = str(root or "").strip()
    if not safe_root:
        return ""
    safe_owner = _safe_segment(owner, default="default")
    safe_name = _safe_segment(name, default="index")
    safe_version = _safe_segment(version_id, default="version")
    return os.path.join(safe_root, "collections", safe_owner, safe_name, safe_version, "index.json")


def _write_json_atomic(path: str, payload: Any) -> None:
    tmp = f"{path}.tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def load_index_artifact(path: str) -> Any:
    """Load a stored index and attach its dense sidecar when present."""

    artifact_path = str(path or "").strip()
    if not artifact_path or not os.path.exists(artifact_path):
        return None
    try:
        with open(artifact_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return None
        from refiner.rag_engine import RagIndex

        index = RagIndex.from_dict(payload)
    except Exception:
        return None
    dense_path = dense_artifact_path_for_index(artifact_path)
    if dense_path and os.path.exists(dense_path):
        try:
            with open(dense_path, "r", encoding="utf-8") as handle:
                dense_payload = json.load(handle)
            if isinstance(dense_payload, dict):
                attach_dense_artifact(index, DenseIndexArtifact.from_dict(dense_payload))
        except Exception:
            pass
    return index


def write_versioned_index_artifact(root: str, owner: str, name: str, version_id: str, index: Any) -> str:
    """Persist one immutable collection version and return its path."""

    path = versioned_index_artifact_path(root, owner, name, version_id)
    if not path:
        return ""
    _write_json_atomic(path, index.to_dict())
    dense_path = dense_artifact_path_for_index(path)
    if dense_path:
        dense_artifact = ensure_dense_artifact(index)
        _write_json_atomic(dense_path, dense_artifact.to_dict())
    return path


def delete_versioned_collection_artifacts(root: str, owner: str, name: str) -> bool:
    """Delete immutable artefacts for one collection, if present."""

    safe_root = str(root or "").strip()
    if not safe_root:
        return False
    safe_owner = _safe_segment(owner, default="default")
    safe_name = _safe_segment(name, default="index")
    target = os.path.join(safe_root, "collections", safe_owner, safe_name)
    if not os.path.isdir(target):
        return False
    try:
        shutil.rmtree(target)
        return True
    except Exception:
        return False

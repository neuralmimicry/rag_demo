"""Helpers for coordinated RAG collection publication.

The immutable version artefact is written first, staged in Postgres, and only
then mirrored into the legacy flat active file. This keeps publication phases
explicit while preserving backwards compatibility for older consumers.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Dict, Iterable, Mapping, Optional

from assistant_pipeline.ingestion.artifact_store import write_versioned_index_artifact
from assistant_pipeline.retrieval.dense_artifacts import dense_artifact_path_for_index


@dataclass(frozen=True)
class CollectionPublicationPaths:
    """Filesystem paths associated with one publication attempt."""

    version_artifact_path: str = ""
    version_dense_artifact_path: str = ""
    active_artifact_path: str = ""
    active_dense_artifact_path: str = ""

    @property
    def primary_artifact_path(self) -> str:
        """Return the preferred long-lived artefact path for assistant reads."""

        return str(self.version_artifact_path or self.active_artifact_path or "").strip()


def _publication_mode(paths: CollectionPublicationPaths) -> str:
    return "versioned+legacy_mirror" if paths.version_artifact_path else "legacy_only"


def collection_publication_metadata(
    base_metadata: Optional[Mapping[str, Any]],
    *,
    paths: CollectionPublicationPaths,
    publish_state: str,
    compatibility_mirror_status: str,
) -> Dict[str, Any]:
    """Build one normalised metadata payload for staged or final publication."""

    metadata = dict(base_metadata or {})
    metadata["publish_state"] = str(publish_state or "").strip() or "published"
    metadata["publication_mode"] = _publication_mode(paths)
    metadata["compatibility_mirror_status"] = str(compatibility_mirror_status or "").strip() or "unknown"
    if paths.version_artifact_path:
        metadata["version_artifact_path"] = paths.version_artifact_path
    if paths.version_dense_artifact_path:
        metadata["version_dense_artifact_path"] = paths.version_dense_artifact_path
    if paths.active_artifact_path:
        metadata["active_artifact_path"] = paths.active_artifact_path
    if paths.active_dense_artifact_path:
        metadata["active_dense_artifact_path"] = paths.active_dense_artifact_path
    return metadata


def write_collection_version_artifact(
    rag_store: Any,
    *,
    owner: str,
    name: str,
    version_id: str,
    index: Any,
) -> CollectionPublicationPaths:
    """Persist the immutable version artefact when shared storage is configured."""

    artifact_root = str(getattr(rag_store, "root", "") or "").strip()
    if not artifact_root:
        return CollectionPublicationPaths()
    version_artifact_path = write_versioned_index_artifact(artifact_root, owner, name, version_id, index)
    return CollectionPublicationPaths(
        version_artifact_path=version_artifact_path,
        version_dense_artifact_path=dense_artifact_path_for_index(version_artifact_path),
    )


def mirror_collection_artifact(
    rag_store: Any,
    *,
    owner: str,
    name: str,
    index: Any,
    paths: CollectionPublicationPaths,
) -> CollectionPublicationPaths:
    """Publish the active compatibility artefact, mirroring the version artefact when possible."""

    mirror_from = str(paths.version_artifact_path or "").strip()
    active_artifact_path = ""
    mirror_method = getattr(rag_store, "mirror_index_artifact", None)
    if mirror_from and callable(mirror_method):
        active_artifact_path = str(mirror_method(owner, name, mirror_from) or "").strip()
    if not active_artifact_path:
        active_artifact_path = str(rag_store.save_index(owner, index) or "").strip()
    return replace(
        paths,
        active_artifact_path=active_artifact_path,
        active_dense_artifact_path=dense_artifact_path_for_index(active_artifact_path),
    )


def stage_collection_publication(
    metadata_store: Any,
    *,
    owner: str,
    name: str,
    version_id: str,
    paths: CollectionPublicationPaths,
    base_metadata: Optional[Mapping[str, Any]] = None,
    scope: str = "user",
) -> Dict[str, Any]:
    """Record that an immutable version is staged and awaiting active mirroring."""

    if metadata_store is None or not paths.version_artifact_path:
        return {}
    staged_metadata = collection_publication_metadata(
        base_metadata,
        paths=paths,
        publish_state="staged",
        compatibility_mirror_status="pending",
    )
    metadata_store.stage_collection_version(
        owner,
        name,
        version_id=version_id,
        status="publishing",
        scope=scope,
        artifact_path=paths.version_artifact_path,
        metadata=staged_metadata,
    )
    return staged_metadata


def record_collection_publication(
    metadata_store: Any,
    *,
    owner: str,
    name: str,
    version_id: str,
    paths: CollectionPublicationPaths,
    source_count: int,
    documents: Iterable[Any],
    chunks: Iterable[Any],
    base_metadata: Optional[Mapping[str, Any]] = None,
    scope: str = "user",
) -> Dict[str, Any]:
    """Finalise a publication and mark the version active in Postgres."""

    final_metadata = collection_publication_metadata(
        base_metadata,
        paths=paths,
        publish_state="published",
        compatibility_mirror_status="published" if paths.version_artifact_path else "primary_only",
    )
    if metadata_store is not None:
        metadata_store.record_collection_version(
            owner,
            name,
            artifact_path=paths.primary_artifact_path,
            source_count=source_count,
            documents=documents,
            chunks=chunks,
            metadata=final_metadata,
            version_id=version_id,
            scope=scope,
        )
    return final_metadata

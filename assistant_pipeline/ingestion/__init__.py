"""RAG ingestion helpers extracted from the Refiner monolith."""

from assistant_pipeline.ingestion.artifact_store import (
    delete_versioned_collection_artifacts,
    load_index_artifact,
    versioned_index_artifact_path,
    write_versioned_index_artifact,
)
from assistant_pipeline.ingestion.publication import (
    CollectionPublicationPaths,
    collection_publication_metadata,
    mirror_collection_artifact,
    record_collection_publication,
    stage_collection_publication,
    write_collection_version_artifact,
)
from assistant_pipeline.ingestion.source_loader import build_rag_documents, coerce_rag_sources

__all__ = [
    "CollectionPublicationPaths",
    "build_rag_documents",
    "collection_publication_metadata",
    "coerce_rag_sources",
    "delete_versioned_collection_artifacts",
    "load_index_artifact",
    "mirror_collection_artifact",
    "record_collection_publication",
    "stage_collection_publication",
    "versioned_index_artifact_path",
    "write_collection_version_artifact",
    "write_versioned_index_artifact",
]

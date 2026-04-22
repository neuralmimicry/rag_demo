"""Assistant pipeline package for Refiner's assistant and RAG routes.

The package introduces stable seams between HTTP handlers, request orchestration,
persistence, and retrieval helpers so the existing Refiner API can evolve without
pushing more route logic back into `refiner_web.py`.
"""

from assistant_pipeline.contracts import ServiceError, ServiceResult
from assistant_pipeline.dependencies import AssistantPipelineDependencies

__all__ = [
    "AssistantPipelineDependencies",
    "ServiceError",
    "ServiceResult",
]

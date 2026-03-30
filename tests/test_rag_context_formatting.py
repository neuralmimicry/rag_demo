import pytest
from rag_engine import RagMatch

refiner_web = pytest.importorskip("refiner_web", exc_type=ImportError)


def test_render_rag_context_uses_locator_citations():
    match = RagMatch(
        chunk_id="doc-1:0001",
        source="spec.pdf",
        score=3.2,
        text="Retry logic is applied before the queue is drained.",
        metadata={
            "page_start": 2,
            "page_end": 2,
            "block_start": 3,
            "block_end": 4,
            "heading_path": ["Operations", "Retry Handling"],
        },
        citation="spec.pdf [p.2 b.3-4]",
    )

    rendered = refiner_web._render_rag_context([match])
    payload = refiner_web._serialize_rag_match(match)

    assert "[spec.pdf [p.2 b.3-4]]" in rendered
    assert "Heading path: Operations > Retry Handling" in rendered
    assert payload["citation"] == "spec.pdf [p.2 b.3-4]"

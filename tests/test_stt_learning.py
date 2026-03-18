import os

from stt_learning import SttLearningStore


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def test_seed_from_local_content(tmp_path):
    site_root = tmp_path / "site"
    _write(
        str(site_root / "src" / "about.jsx"),
        "export const blurb = 'NeuralMimicry develops AARNN and Continuum for sovereign AI.';",
    )
    _write(
        str(site_root / "src" / "services.jsx"),
        "<section>Tracey security fabric and Refiner delivery platform.</section>",
    )

    store = SttLearningStore(
        str(tmp_path / "learning"),
        seed_paths=[str(site_root)],
        seed_urls=[],
        allow_network=False,
        max_seed_files=20,
        max_seed_docs=20,
    )

    hint = store.build_prompt_hint()
    assert "NeuralMimicry" in hint
    assert "aarnn" in hint.lower()

    matches = store.query_context("What is Continuum?", limit=2)
    assert matches
    assert any("continuum" in (entry.get("text") or "").lower() for entry in matches)


def test_learning_redacts_pii(tmp_path):
    site_root = tmp_path / "site"
    _write(str(site_root / "home.txt"), "NeuralMimicry Refiner AARNN")

    store = SttLearningStore(
        str(tmp_path / "learning"),
        seed_paths=[str(site_root)],
        seed_urls=[],
        allow_network=False,
        max_seed_files=10,
        max_seed_docs=10,
    )

    store.learn_from_text(
        "My name is Alice Example. Email alice@example.com and call +44 7712 345678 about Refiner.",
        source="voice_stt",
    )

    assert store.memory_docs
    text = store.memory_docs[-1]["text"].lower()
    assert "alice@example.com" not in text
    assert "[email]" in text
    assert "[phone]" in text
    assert "my name is [name]" in text


def test_new_terms_need_repetition_before_prompt(tmp_path):
    site_root = tmp_path / "site"
    _write(str(site_root / "home.txt"), "NeuralMimicry neuromorphic Refiner")

    store = SttLearningStore(
        str(tmp_path / "learning"),
        seed_paths=[str(site_root)],
        seed_urls=[],
        allow_network=False,
        max_seed_files=10,
        max_seed_docs=10,
        learn_min_count=3,
        prompt_terms=60,
    )

    term = "xylophonix"
    store.learn_from_text(f"{term} appeared once.")
    assert term not in store.build_prompt_hint().lower()

    store.learn_from_text(f"{term} appeared twice.")
    store.learn_from_text(f"{term} appeared three times.")
    assert term in store.build_prompt_hint().lower()

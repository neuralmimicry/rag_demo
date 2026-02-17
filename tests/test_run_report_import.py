def test_run_rag_import_and_attr():
    # Ensure importing CLI module does not execute workflow and exposes run
    import run_rag  # noqa: F401
    assert hasattr(run_rag, 'run')

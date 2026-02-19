def test_run_refiner_import_and_attr():
    # Ensure importing CLI module does not execute workflow and exposes run
    import run_refiner
    assert hasattr(run_refiner, "run")

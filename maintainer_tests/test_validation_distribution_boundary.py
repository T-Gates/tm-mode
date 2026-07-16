from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_release_contract_checks_live_outside_instance_validation_tree():
    assert not (REPO / "tests" / "test_release_pin.py").exists()
    assert (REPO / "maintainer_tests" / "test_release_pin.py").is_file()


def test_ci_runs_maintainer_tests_separately_from_instance_validation():
    workflow = (REPO / ".github" / "workflows" / "test.yml").read_text(
        encoding="utf-8"
    )
    assert "python -m pytest -q maintainer_tests" in workflow

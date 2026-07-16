import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_release_contract_checks_live_outside_instance_validation_tree():
    assert not (REPO / "tests" / "test_release_pin.py").exists()
    assert (REPO / "maintainer_tests" / "test_release_pin.py").is_file()

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=60,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "maintainer_tests/" not in output, output


def test_ci_runs_maintainer_tests_separately_from_instance_validation():
    workflow = (REPO / ".github" / "workflows" / "test.yml").read_text(
        encoding="utf-8"
    )
    matrix_declaration = "      matrix:\n"
    maintainer_step = (
        "      - name: Run maintainer-only product checks\n"
        "        run: python -m pytest -q maintainer_tests\n"
    )
    assert matrix_declaration in workflow
    assert maintainer_step in workflow
    assert workflow.index(maintainer_step) > workflow.index(matrix_declaration)

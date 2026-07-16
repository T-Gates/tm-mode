import os
import re
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_release_contract_checks_live_outside_instance_validation_tree(monkeypatch):
    assert not (REPO / "tests" / "test_release_pin.py").exists()
    assert (REPO / "maintainer_tests" / "test_release_pin.py").is_file()

    monkeypatch.setenv("PYTEST_ADDOPTS", "maintainer_tests")
    monkeypatch.setenv(
        "PYTEST_PLUGINS", "tm_mode_missing_ambient_pytest_plugin"
    )
    child_env = os.environ.copy()
    for key in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        child_env.pop(key, None)
    child_env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=60,
        env=child_env,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "maintainer_tests/" not in output, output


def test_ci_runs_maintainer_tests_separately_from_instance_validation():
    workflow = (REPO / ".github" / "workflows" / "test.yml").read_text(
        encoding="utf-8"
    )
    job_header = "  pytest:\n"
    job_start = workflow.index(job_header)
    job_body_start = job_start + len(job_header)
    next_job = re.search(
        r"(?m)^  [A-Za-z0-9_-]+:\n", workflow[job_body_start:]
    )
    job_end = (
        job_body_start + next_job.start() if next_job else len(workflow)
    )
    pytest_job = workflow[job_start:job_end]

    maintainer_step = (
        "      - name: Run maintainer-only product checks\n"
        "        run: python -m pytest -q maintainer_tests\n"
    )
    assert "    strategy:\n" in pytest_job
    assert "      matrix:\n" in pytest_job
    assert "      - run: python -m pytest -q\n" in pytest_job
    assert maintainer_step in pytest_job

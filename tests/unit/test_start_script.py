"""Regression tests for start.sh deployment behavior."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
START_SH = REPO_ROOT / "start.sh"


def test_uv_pip_install_pins_target_venv():
    """`uv pip install` must target .venv explicitly.

    Without an explicit --python (or VIRTUAL_ENV), uv falls back to an active
    CONDA_PREFIX, installing onemancompany outside .venv. The backend then
    fails to import on first deploy with ModuleNotFoundError.
    """
    script = START_SH.read_text()
    install_lines = [
        line for line in script.splitlines() if "uv pip install" in line and not line.lstrip().startswith("#")
    ]
    assert install_lines, "expected at least one `uv pip install` invocation in start.sh"
    for line in install_lines:
        assert '--python "$PYTHON"' in line or 'VIRTUAL_ENV=' in line, (
            f"uv pip install must pin the target interpreter to .venv, got: {line!r}"
        )

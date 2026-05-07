import subprocess
import stat
from pathlib import Path


def test_bootstrap_script_has_no_syntax_errors():
    script = Path(__file__).parents[2] / "ops/bootstrap.sh"
    result = subprocess.run(["bash", "-n", str(script)], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()


def test_backup_script_has_no_syntax_errors():
    script = Path(__file__).parents[2] / "ops/backup.sh"
    result = subprocess.run(["bash", "-n", str(script)], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()


def test_bootstrap_script_is_executable():
    script = Path(__file__).parents[2] / "ops/bootstrap.sh"
    mode = script.stat().st_mode
    assert mode & stat.S_IXUSR, "bootstrap.sh is not executable"

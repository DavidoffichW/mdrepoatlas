from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    src = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "mdrepoatlas.cli", *args],
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def test_stdout_is_clean_markdown_and_diagnostics_use_stderr(tmp_path: Path) -> None:
    (tmp_path / "hello.py").write_text("print('مرحبا')\n", encoding="utf-8")

    completed = run_cli(str(tmp_path), "-o", "-")

    assert completed.returncode == 0
    assert completed.stdout.startswith("---\n")
    assert "print('مرحبا')" in completed.stdout
    assert "mdrepoatlas: wrote" not in completed.stdout
    assert "mdrepoatlas: wrote stdout" in completed.stderr


def test_cli_output_inside_source_does_not_ingest_itself(tmp_path: Path) -> None:
    (tmp_path / "hello.py").write_text("print('hello')\n", encoding="utf-8")
    output = tmp_path / "snapshot.md"

    first = run_cli(str(tmp_path), "-o", str(output))
    second = run_cli(str(tmp_path), "-o", str(output))

    assert first.returncode == second.returncode == 0
    assert output.exists()
    text = output.read_text(encoding="utf-8")
    assert "<code>snapshot.md</code>" not in text
    assert not list(tmp_path.glob(".snapshot.md.*.tmp"))


def test_invalid_source_and_argument_errors_are_nonzero(tmp_path: Path) -> None:
    missing = run_cli(str(tmp_path / "missing"), "-o", "-")
    invalid_limit = run_cli(str(tmp_path), "--max-file-bytes", "-1")

    assert missing.returncode == 1
    assert "source directory is unavailable" in missing.stderr
    assert invalid_limit.returncode == 2
    assert "must be zero or greater" in invalid_limit.stderr


def test_sensitive_override_is_explicit_on_stderr(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("TOKEN=value", encoding="utf-8")

    completed = run_cli(str(tmp_path), "-o", "-", "--include-sensitive")

    assert completed.returncode == 0
    assert "<code>.env</code>" in completed.stdout
    assert "sensitive-path safeguards are disabled" in completed.stderr


def test_legacy_sanitize_paths_flag_remains_accepted(tmp_path: Path) -> None:
    (tmp_path / "hello.py").write_text("print('hello')\n", encoding="utf-8")

    completed = run_cli(str(tmp_path), "-o", "-", "--sanitize-paths")

    assert completed.returncode == 0
    assert str(tmp_path) not in completed.stdout
    assert 'source_root: "."' in completed.stdout


def test_c2md_module_compatibility() -> None:
    env = os.environ.copy()
    python_paths = [str(Path(__file__).resolve().parents[1] / "src")]
    if env.get("PYTHONPATH"):
        python_paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_paths)
    completed = subprocess.run(
        [sys.executable, "-m", "c2md.cli", "--version"],
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout.strip().endswith("0.2.0")

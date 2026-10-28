from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


VALIDATOR = Path(__file__).resolve().parents[1] / "tools" / "validate_release.py"


def git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repository,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


@pytest.fixture
def release_repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "release-repository"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.name", "Release Test")
    git(repository, "config", "user.email", "release-test@example.invalid")
    (repository / "pyproject.toml").write_text(
        '[project]\nname = "example"\nversion = "0.2.0"\n', encoding="utf-8"
    )
    git(repository, "add", "pyproject.toml")
    git(repository, "commit", "-m", "Release candidate")
    commit = git(repository, "rev-parse", "HEAD")
    git(repository, "tag", "v0.2.0")
    return repository, commit


def validate(
    repository: Path, tag: str, expected_commit: str, branch: str = "main"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(VALIDATOR),
            "--tag",
            tag,
            "--expected-commit",
            expected_commit,
            "--release-branch",
            branch,
        ],
        cwd=repository,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def test_release_identity_accepts_matching_tag_commit_and_main(
    release_repository: tuple[Path, str],
) -> None:
    repository, commit = release_repository

    completed = validate(repository, "v0.2.0", commit)

    assert completed.returncode == 0
    assert completed.stdout.strip() == commit


@pytest.mark.parametrize("tag", ["0.2.0", "release-0.2.0", "vnext", "v0.2"])
def test_release_identity_rejects_invalid_tag(
    release_repository: tuple[Path, str], tag: str
) -> None:
    repository, commit = release_repository

    completed = validate(repository, tag, commit)

    assert completed.returncode != 0
    assert "invalid release tag" in completed.stderr


def test_release_identity_rejects_missing_tag(
    release_repository: tuple[Path, str],
) -> None:
    repository, commit = release_repository

    completed = validate(repository, "v9.9.9", commit)

    assert completed.returncode != 0
    assert "tag does not exist" in completed.stderr


def test_release_identity_rejects_wrong_expected_commit(
    release_repository: tuple[Path, str],
) -> None:
    repository, _ = release_repository

    completed = validate(repository, "v0.2.0", "0" * 40)

    assert completed.returncode != 0
    assert "does not match expected commit" in completed.stderr


def test_release_identity_rejects_abbreviated_expected_commit(
    release_repository: tuple[Path, str],
) -> None:
    repository, commit = release_repository

    completed = validate(repository, "v0.2.0", commit[:12])

    assert completed.returncode != 0
    assert "full 40-character SHA" in completed.stderr


def test_release_identity_rejects_tag_version_mismatch(
    release_repository: tuple[Path, str],
) -> None:
    repository, commit = release_repository
    git(repository, "tag", "v0.2.1", commit)

    completed = validate(repository, "v0.2.1", commit)

    assert completed.returncode != 0
    assert "does not match package version" in completed.stderr


def test_release_identity_rejects_commit_outside_release_branch(
    release_repository: tuple[Path, str],
) -> None:
    repository, _ = release_repository
    git(repository, "switch", "-c", "feature")
    (repository / "pyproject.toml").write_text(
        '[project]\nname = "example"\nversion = "0.3.0"\n', encoding="utf-8"
    )
    git(repository, "add", "pyproject.toml")
    git(repository, "commit", "-m", "Unmerged release")
    commit = git(repository, "rev-parse", "HEAD")
    git(repository, "tag", "v0.3.0")

    completed = validate(repository, "v0.3.0", commit)

    assert completed.returncode != 0
    assert "not contained in release branch" in completed.stderr


def test_release_workflow_is_manual_and_validated() -> None:
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"
    ).read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "push:" not in workflow
    assert "if: github.ref == 'refs/heads/main'" in workflow
    assert "tools/validate_release.py" in workflow
    assert "needs: validate-and-build" in workflow
    assert "environment:" in workflow
    assert "python -m twine check dist/*" in workflow
    assert "python -m twine upload dist/*" in workflow

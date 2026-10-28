"""Validate the immutable identity selected for an mdrepoatlas release."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib


TAG_PATTERN = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
COMMIT_PATTERN = re.compile(r"[0-9a-fA-F]{40}\Z")


class ReleaseValidationError(RuntimeError):
    """Raised when the selected tag, commit, or branch policy is invalid."""


def git(*arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip() or "git failed"
        raise ReleaseValidationError(detail)
    return completed.stdout.strip()


def validate(tag: str, expected_commit: str, release_branch: str) -> str:
    if TAG_PATTERN.fullmatch(tag) is None:
        raise ReleaseValidationError(f"invalid release tag: {tag}")
    if COMMIT_PATTERN.fullmatch(expected_commit) is None:
        raise ReleaseValidationError("expected commit must be a full 40-character SHA")

    try:
        tag_commit = git("rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}")
    except ReleaseValidationError as exc:
        raise ReleaseValidationError(f"tag does not exist: {tag}") from exc
    if tag_commit.lower() != expected_commit.lower():
        raise ReleaseValidationError(
            f"tag {tag} does not match expected commit {expected_commit}"
        )

    try:
        pyproject = git("show", f"{tag_commit}:pyproject.toml")
        package_version = tomllib.loads(pyproject)["project"]["version"]
    except (ReleaseValidationError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseValidationError(
            "cannot read project.version from the tagged pyproject.toml"
        ) from exc
    requested_version = tag[1:]
    if package_version != requested_version:
        raise ReleaseValidationError(
            f"tag {tag} does not match package version {package_version}"
        )

    branch_check = subprocess.run(
        ["git", "merge-base", "--is-ancestor", tag_commit, release_branch],
        capture_output=True,
        check=False,
    )
    if branch_check.returncode != 0:
        raise ReleaseValidationError(
            f"release commit is not contained in release branch {release_branch}"
        )
    return tag_commit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--release-branch", required=True)
    arguments = parser.parse_args()
    try:
        commit = validate(
            arguments.tag, arguments.expected_commit, arguments.release_branch
        )
    except ReleaseValidationError as exc:
        print(f"release validation failed: {exc}", file=sys.stderr)
        return 1
    print(commit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

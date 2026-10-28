from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path

import pytest

import mdrepoatlas.core as core
from mdrepoatlas import SnapshotError, SnapshotResult, write_snapshot


def render(source: Path, **kwargs) -> tuple[str, SnapshotResult]:
    output = io.StringIO()
    result = write_snapshot(source, output, **kwargs)
    return output.getvalue(), result


def listed(text: str, path: str) -> bool:
    return f"<code>{path}</code>" in text


def test_normal_traversal_and_api_result(tmp_path: Path) -> None:
    source = tmp_path / "repo"
    (source / "src" / "nested").mkdir(parents=True)
    (source / "README.md").write_text("# Example\n", encoding="utf-8")
    (source / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (source / "src" / "nested" / "value.txt").write_text("value\n", encoding="utf-8")

    text, result = render(source)

    assert isinstance(result, SnapshotResult)
    assert result.discovered_files == 3
    assert result.embedded_files == 3
    assert result.omitted_files == 0
    assert "├── README.md" in text
    assert "src/nested/value.txt" in text
    assert "### python (1)" in text
    assert "print('ok')" in text


def test_root_and_nested_gitignore_with_negation(tmp_path: Path) -> None:
    source = tmp_path / "repo"
    (source / "pkg").mkdir(parents=True)
    (source / ".gitignore").write_text("*.log\n!important.log\n", encoding="utf-8")
    (source / "debug.log").write_text("ignored", encoding="utf-8")
    (source / "important.log").write_text("kept", encoding="utf-8")
    (source / "pkg" / ".gitignore").write_text("*.tmp\n!keep.tmp\n", encoding="utf-8")
    (source / "pkg" / "drop.tmp").write_text("ignored", encoding="utf-8")
    (source / "pkg" / "keep.tmp").write_text("kept", encoding="utf-8")

    text, result = render(source)

    assert not listed(text, "debug.log")
    assert listed(text, "important.log")
    assert not listed(text, "pkg/drop.tmp")
    assert listed(text, "pkg/keep.tmp")
    assert result.gitignore_files == 2


def test_user_exclusions_and_negation(tmp_path: Path) -> None:
    (tmp_path / "drop.txt").write_text("drop", encoding="utf-8")
    (tmp_path / "keep.txt").write_text("keep", encoding="utf-8")

    text, _ = render(tmp_path, exclude=["*.txt", "!keep.txt"])

    assert not listed(text, "drop.txt")
    assert listed(text, "keep.txt")


def test_sensitive_defaults_require_explicit_override(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("TOKEN=secret", encoding="utf-8")
    (tmp_path / ".env.example").write_text("TOKEN=replace-me", encoding="utf-8")
    (tmp_path / "server.pem").write_text("not-a-real-key", encoding="utf-8")

    protected, _ = render(tmp_path, use_default_excludes=False)
    unprotected, _ = render(tmp_path, use_default_excludes=False, include_sensitive=True)

    assert not listed(protected, ".env")
    assert listed(protected, ".env.example")
    assert not listed(protected, "server.pem")
    assert listed(unprotected, ".env")
    assert listed(unprotected, "server.pem")


def test_linked_worktree_git_file_is_excluded(tmp_path: Path) -> None:
    (tmp_path / ".git").write_text("gitdir: C:/private/repository", encoding="utf-8")
    (tmp_path / "main.py").write_text("print('ok')", encoding="utf-8")

    text, _ = render(tmp_path)

    assert not listed(text, ".git")
    assert "C:/private/repository" not in text
    assert listed(text, "main.py")


def test_invalid_exclusion_pattern_has_a_clear_error(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("print('ok')", encoding="utf-8")

    with pytest.raises(SnapshotError, match="invalid user exclusion pattern"):
        render(tmp_path, exclude=["!"])


def test_symlinks_and_outside_targets_are_not_followed(tmp_path: Path) -> None:
    source = tmp_path / "repo"
    outside = tmp_path / "outside"
    source.mkdir()
    outside.mkdir()
    (source / "inside.txt").write_text("inside", encoding="utf-8")
    (outside / "private.txt").write_text("outside", encoding="utf-8")
    try:
        (source / "outside-link").symlink_to(outside, target_is_directory=True)
        (source / "loop").symlink_to(source, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    text, result = render(source)

    assert listed(text, "inside.txt")
    assert "private.txt" not in text
    assert result.skipped_symlinks == 2


def test_output_path_inside_source_is_excluded(tmp_path: Path) -> None:
    source = tmp_path / "repo"
    source.mkdir()
    (source / "main.py").write_text("print('ok')", encoding="utf-8")
    output_path = source / "snapshot.md"
    output_path.write_text("old snapshot", encoding="utf-8")

    text, result = render(source, output_path=output_path)

    assert listed(text, "main.py")
    assert not listed(text, "snapshot.md")
    assert result.discovered_files == 1


def test_file_and_total_limits_report_omissions(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("12345", encoding="utf-8")
    (tmp_path / "b.txt").write_text("67890", encoding="utf-8")
    (tmp_path / "large.txt").write_text("x" * 20, encoding="utf-8")

    text, result = render(tmp_path, max_file_bytes=10, max_total_bytes=5)

    assert "large.txt</code> — lang=text, status=too_large" in text
    assert "b.txt</code> — lang=text, status=total_limit" in text
    assert "> OMITTED: per-file content limit exceeded." in text
    assert "> OMITTED: total content limit reached." in text
    assert result.embedded_files == 1
    assert result.omitted_files == 2
    assert result.emitted_bytes == 5


def test_binary_unicode_and_invalid_utf8(tmp_path: Path) -> None:
    (tmp_path / "binary.bin").write_bytes(b"abc\x00def")
    (tmp_path / "unicode.py").write_text("message = 'مرحبا 🌍'\n", encoding="utf-8")
    (tmp_path / "invalid.txt").write_bytes(b"valid\xffinvalid")

    text, result = render(tmp_path, use_default_excludes=False)

    assert "message = 'مرحبا 🌍'" in text
    assert "binary.bin</code> — lang=unknown, status=binary" in text
    assert "invalid.txt</code> — lang=text, status=decode_error" in text
    assert result.omitted_files == 2


def test_unreadable_file_is_an_explicit_omission(tmp_path: Path, monkeypatch) -> None:
    blocked = tmp_path / "blocked.txt"
    blocked.write_text("private", encoding="utf-8")
    original_open = Path.open

    def guarded_open(path: Path, *args, **kwargs):
        if path == blocked:
            raise PermissionError("blocked for test")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    text, result = render(tmp_path)

    assert "blocked.txt</code> — lang=text, status=unreadable" in text
    assert "> OMITTED: file could not be read." in text
    assert result.omitted_files == 1


def test_changed_input_aborts_without_copying_partial_output(tmp_path: Path, monkeypatch) -> None:
    changing = tmp_path / "changing.txt"
    changing.write_text("first", encoding="utf-8")
    original_analyze = core._analyze_files

    def analyze_then_change(*args, **kwargs):
        records, total = original_analyze(*args, **kwargs)
        changing.write_text("second", encoding="utf-8")
        return records, total

    monkeypatch.setattr(core, "_analyze_files", analyze_then_change)
    output = io.StringIO()

    with pytest.raises(SnapshotError, match="input changed before rendering"):
        write_snapshot(tmp_path, output)
    assert output.getvalue() == ""


def test_markdown_fences_paths_and_unicode_remain_structural(tmp_path: Path) -> None:
    special = tmp_path / "a[1]#`name.py"
    special.write_text("# heading\n```\n`````\nمرحبا\n", encoding="utf-8")

    text, _ = render(tmp_path)

    assert "<code>a[1]#`name.py</code>" in text
    assert "``````python\n# heading" in text
    assert "مرحبا" in text


def test_repeated_runs_are_byte_identical_and_private_metadata_is_absent(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("print('same')\r\n", encoding="utf-8", newline="")

    first, first_result = render(tmp_path)
    second, second_result = render(tmp_path)

    assert first == second
    assert first_result == second_result
    assert str(tmp_path) not in first
    assert "generated_at" not in first
    assert "output_file" not in first
    assert "source_root: \".\"" in first
    expected_hash = hashlib.sha256((tmp_path / "main.py").read_bytes()).hexdigest()
    assert expected_hash in first


@pytest.mark.skipif(os.name != "nt", reason="Windows path separator behavior")
def test_windows_backslash_user_exclusion(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "secret.txt").write_text("secret", encoding="utf-8")
    (nested / "keep.txt").write_text("keep", encoding="utf-8")

    text, _ = render(tmp_path, exclude=[r"nested\secret.txt"])

    assert not listed(text, "nested/secret.txt")
    assert listed(text, "nested/keep.txt")

"""Bounded, deterministic repository-to-Markdown snapshots."""

from __future__ import annotations

import errno
import hashlib
import html
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable, Iterator, Sequence, TextIO

from pathspec.gitignore import GitIgnoreSpec
from pathspec.patterns.gitwildmatch import GitWildMatchPatternError

from . import __version__


DEFAULT_MAX_FILE_BYTES = 512 * 1024
DEFAULT_MAX_TOTAL_BYTES = 10 * 1024 * 1024
_COPY_CHUNK_CHARS = 64 * 1024
_HASH_CHUNK_BYTES = 64 * 1024

DEFAULT_EXCLUDES = (
    ".git", ".hg", ".svn", ".idea/", ".vscode/", "__pycache__/",
    ".pytest_cache/", ".mypy_cache/", ".ruff_cache/", ".tox/", ".venv/",
    "venv/", "env/", "node_modules/", ".next/", ".nuxt/", ".svelte-kit/",
    ".astro/", "coverage/", "CMakeFiles/", "cmake-build-*/", "dist/", "build/",
    "out/", ".cache/", "tmp/", "temp/", "*.log", "*.o", "*.obj", "*.a",
    "*.so", "*.dll", "*.dylib", "*.exe", "*.pdb", "*.mod", "*.out",
    "*.zip", "*.tar", "*.gz", "*.7z", "*.woff", "*.woff2", "*.ttf",
    "*.otf", "*.mp3", "*.mp4", "*.mov", "*.avi",
)

SENSITIVE_EXCLUDES = (
    ".env", ".env.*", "!.env.example", "!.env.sample", "!.env.template",
    ".aws/", ".ssh/", ".npmrc", ".pypirc", "*.pem", "*.key", "id_rsa",
    "id_ed25519", "credentials.json", "secrets/", "secrets.*",
)

_LANGUAGES = {
    ".py": "python", ".md": "markdown", ".txt": "text", ".yml": "yaml",
    ".yaml": "yaml", ".toml": "toml", ".json": "json", ".xml": "xml",
    ".html": "html", ".css": "css", ".js": "javascript", ".jsx": "jsx",
    ".ts": "typescript", ".tsx": "tsx", ".ps1": "powershell", ".sh": "bash",
    ".ini": "ini", ".cfg": "ini", ".sql": "sql", ".c": "c", ".h": "c",
    ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp", ".f": "fortran",
    ".for": "fortran", ".f77": "fortran", ".f90": "fortran",
    ".f95": "fortran", ".f03": "fortran", ".f08": "fortran", ".go": "go",
    ".rs": "rust", ".java": "java", ".cs": "csharp", ".rb": "ruby",
    ".php": "php", ".kt": "kotlin", ".swift": "swift", ".m": "objectivec",
    ".mm": "objectivec", ".vue": "vue", ".svelte": "svelte",
}


class SnapshotError(RuntimeError):
    """Raised when a complete, internally consistent snapshot cannot be made."""


class _UnsafePathError(SnapshotError):
    """Raised when an opened object no longer matches its discovered path."""


@dataclass(frozen=True)
class SnapshotResult:
    """Concise result summary returned by :func:`write_snapshot`."""

    discovered_files: int
    embedded_files: int
    omitted_files: int
    emitted_bytes: int
    source_bytes: int
    excluded_paths: int
    skipped_symlinks: int
    gitignore_files: int


@dataclass(frozen=True)
class _IgnoreLayer:
    base: str
    spec: GitIgnoreSpec


@dataclass(frozen=True)
class _DiscoveredFile:
    path: str
    absolute_path: Path
    identity: tuple[int, int]
    size_hint: int


@dataclass(frozen=True)
class _FileRecord:
    path: str
    absolute_path: Path
    identity: tuple[int, int]
    language: str
    size: int | None
    sha256: str | None
    status: str
    emitted_bytes: int

    @property
    def omitted(self) -> bool:
        return self.status != "embedded"


@dataclass(frozen=True)
class _DiscoveryResult:
    files: tuple[_DiscoveredFile, ...]
    excluded_paths: int
    skipped_symlinks: int
    gitignore_files: int


def _make_spec(patterns: Iterable[str], *, label: str) -> GitIgnoreSpec | None:
    lines = [str(pattern).replace("\\", "/") for pattern in patterns if str(pattern).strip()]
    if not lines:
        return None
    try:
        return GitIgnoreSpec.from_lines(lines)
    except (GitWildMatchPatternError, re.error) as exc:
        raise SnapshotError(f"invalid {label} pattern: {exc}") from exc


def _check_spec(spec: GitIgnoreSpec | None, path: str) -> bool | None:
    if spec is None:
        return None
    return spec.check_file(path).include


def _path_for_layer(path: str, base: str) -> str | None:
    if not base:
        return path
    if path == base:
        return ""
    prefix = base + "/"
    if path.startswith(prefix):
        return path[len(prefix) :]
    return None


def _is_link_or_reparse(info: os.stat_result) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(reparse_flag and attributes & reparse_flag)


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _same_identity(expected: tuple[int, int], actual: os.stat_result) -> bool:
    observed = _identity(actual)
    if expected[1] == 0 or observed[1] == 0:
        return True
    if expected[0] == 0 or observed[0] == 0:
        return observed[1] == expected[1]
    return observed == expected


def _unsafe_path(path: str, detail: str) -> _UnsafePathError:
    return _UnsafePathError(f"unsafe path change detected for {path}: {detail}")


def _windows_final_path(file_descriptor: int) -> Path:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    get_final_path = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
    get_final_path.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    get_final_path.restype = wintypes.DWORD
    handle = msvcrt.get_osfhandle(file_descriptor)
    size = 1024
    while True:
        buffer = ctypes.create_unicode_buffer(size)
        length = get_final_path(handle, buffer, size, 0)
        if length == 0:
            error = ctypes.get_last_error()
            raise OSError(error, "GetFinalPathNameByHandleW failed")
        if length < size:
            value = buffer.value
            break
        size = length + 1
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(value)


def _verify_opened_file(
    stream: BinaryIO,
    *,
    root: Path,
    relative_path: str,
    expected_identity: tuple[int, int],
) -> os.stat_result:
    opened = os.fstat(stream.fileno())
    if not stat.S_ISREG(opened.st_mode):
        raise _unsafe_path(relative_path, "opened object is not a regular file")
    if not _same_identity(expected_identity, opened):
        raise _unsafe_path(relative_path, "opened object identity changed")
    if os.name == "nt":
        try:
            final_path = _windows_final_path(stream.fileno())
            final_path.relative_to(root)
        except (OSError, ValueError) as exc:
            raise _unsafe_path(relative_path, "opened object is outside the source root") from exc
    return opened


@contextmanager
def _open_verified_binary(
    root: Path,
    root_identity: tuple[int, int],
    relative_path: str,
    expected_identity: tuple[int, int],
) -> Iterator[BinaryIO]:
    parts = PurePosixPath(relative_path).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise _unsafe_path(relative_path, "invalid relative path")
    if os.name == "nt":
        path = root.joinpath(*parts)
        with path.open("rb") as stream:
            _verify_opened_file(
                stream,
                root=root,
                relative_path=relative_path,
                expected_identity=expected_identity,
            )
            yield stream
        return

    no_follow = getattr(os, "O_NOFOLLOW", 0)
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    close_on_exec = getattr(os, "O_CLOEXEC", 0)
    non_blocking = getattr(os, "O_NONBLOCK", 0)
    if not no_follow or not directory_flag:
        raise SnapshotError("secure no-follow file opening is unavailable on this platform")
    directory_fds: list[int] = []
    file_fd: int | None = None
    stream: BinaryIO | None = None
    try:
        root_fd = os.open(root, os.O_RDONLY | directory_flag | no_follow | close_on_exec)
        directory_fds.append(root_fd)
        root_info = os.fstat(root_fd)
        if not _same_identity(root_identity, root_info):
            raise _unsafe_path(relative_path, "source root identity changed")
        current_fd = root_fd
        for component in parts[:-1]:
            current_fd = os.open(
                component,
                os.O_RDONLY | directory_flag | no_follow | close_on_exec,
                dir_fd=current_fd,
            )
            directory_fds.append(current_fd)
        file_fd = os.open(
            parts[-1],
            os.O_RDONLY | no_follow | close_on_exec | non_blocking,
            dir_fd=current_fd,
        )
        stream = os.fdopen(file_fd, "rb", closefd=True)
        file_fd = None
        _verify_opened_file(
            stream,
            root=root,
            relative_path=relative_path,
            expected_identity=expected_identity,
        )
        yield stream
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise _unsafe_path(relative_path, "symlink or replaced directory component") from exc
        raise
    finally:
        if stream is not None:
            stream.close()
        if file_fd is not None:
            os.close(file_fd)
        for descriptor in reversed(directory_fds):
            os.close(descriptor)


class _IgnorePolicy:
    def __init__(
        self,
        *,
        default_patterns: Sequence[str],
        user_patterns: Sequence[str],
        include_sensitive: bool,
        reserved_paths: set[str],
    ) -> None:
        self._defaults = _make_spec(default_patterns, label="default exclusion")
        self._user = _make_spec(user_patterns, label="user exclusion")
        self._sensitive = (
            None if include_sensitive else _make_spec(SENSITIVE_EXCLUDES, label="sensitive exclusion")
        )
        self._reserved_paths = reserved_paths

    def ignores(self, path: str, *, is_directory: bool, layers: Sequence[_IgnoreLayer]) -> bool:
        candidate = path + "/" if is_directory else path
        decision: bool | None = None
        matched = _check_spec(self._defaults, candidate)
        if matched is not None:
            decision = matched
        for layer in layers:
            local = _path_for_layer(candidate, layer.base)
            if local is None:
                continue
            matched = _check_spec(layer.spec, local)
            if matched is not None:
                decision = matched
        matched = _check_spec(self._user, candidate)
        if matched is not None:
            decision = matched
        if path in self._reserved_paths:
            return True
        if _check_spec(self._sensitive, candidate) is True:
            return True
        return bool(decision)


def _read_gitignore(path: Path, base: str) -> _IgnoreLayer:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        display = base + "/.gitignore" if base else ".gitignore"
        raise SnapshotError(f"cannot read {display}: {exc}") from exc
    try:
        spec = GitIgnoreSpec.from_lines(text.splitlines())
    except (GitWildMatchPatternError, re.error) as exc:
        display = base + "/.gitignore" if base else ".gitignore"
        raise SnapshotError(f"invalid pattern in {display}: {exc}") from exc
    return _IgnoreLayer(base=base, spec=spec)


def _normalize_reserved(root: Path, candidates: Iterable[os.PathLike[str] | str]) -> set[str]:
    reserved: set[str] = set()
    for candidate in candidates:
        try:
            absolute = Path(candidate).expanduser().resolve(strict=False)
            relative = absolute.relative_to(root)
        except (OSError, ValueError):
            continue
        reserved.add(relative.as_posix())
    return reserved


def _discover(
    root: Path,
    *,
    user_patterns: Sequence[str],
    use_gitignore: bool,
    use_default_excludes: bool,
    include_sensitive: bool,
    reserved_paths: set[str],
) -> _DiscoveryResult:
    policy = _IgnorePolicy(
        default_patterns=DEFAULT_EXCLUDES if use_default_excludes else (),
        user_patterns=user_patterns,
        include_sensitive=include_sensitive,
        reserved_paths=reserved_paths,
    )
    files: list[_DiscoveredFile] = []
    excluded_paths = 0
    skipped_symlinks = 0
    gitignore_files = 0

    def walk(directory: Path, base: str, inherited_layers: tuple[_IgnoreLayer, ...]) -> None:
        nonlocal excluded_paths, skipped_symlinks, gitignore_files
        layers = inherited_layers
        ignore_file = directory / ".gitignore"
        if use_gitignore and ignore_file.is_file() and not ignore_file.is_symlink():
            layers = inherited_layers + (_read_gitignore(ignore_file, base),)
            gitignore_files += 1
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda item: item.name)
        except OSError as exc:
            display = base or "."
            raise SnapshotError(f"cannot traverse {display}: {exc}") from exc
        for entry in entries:
            relative = entry.name if not base else f"{base}/{entry.name}"
            relative = PurePosixPath(relative).as_posix()
            try:
                entry_info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise SnapshotError(f"cannot inspect {relative}: {exc}") from exc
            if _is_link_or_reparse(entry_info):
                skipped_symlinks += 1
                continue
            is_directory = stat.S_ISDIR(entry_info.st_mode)
            is_file = stat.S_ISREG(entry_info.st_mode)
            if not (is_directory or is_file):
                excluded_paths += 1
                continue
            if policy.ignores(relative, is_directory=is_directory, layers=layers):
                excluded_paths += 1
                continue
            absolute = Path(entry.path).absolute()
            try:
                absolute.relative_to(root)
            except ValueError as exc:
                raise SnapshotError(f"path escaped source root: {relative}") from exc
            if is_directory:
                walk(absolute, relative, layers)
            else:
                files.append(
                    _DiscoveredFile(
                        relative, absolute, _identity(entry_info), entry_info.st_size
                    )
                )

    walk(root, "", ())
    files.sort(key=lambda item: (_priority(item.path), item.path))
    return _DiscoveryResult(tuple(files), excluded_paths, skipped_symlinks, gitignore_files)


def _hash_stream(stream) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = stream.read(_HASH_CHUNK_BYTES)
        if not chunk:
            break
        digest.update(chunk)
        total += len(chunk)
    return digest.hexdigest(), total


def _looks_binary(raw: bytes) -> bool:
    sample = raw[:4096]
    if sample.startswith(b"\xef\xbb\xbf"):
        sample = sample[3:]
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    control = sum(byte < 32 and byte not in (9, 10, 13) for byte in sample)
    return control / len(sample) > 0.10


def _looks_minified(path: str, text: str) -> bool:
    lower = path.lower()
    if lower.endswith((".min.js", ".min.css")):
        return True
    lines = text.splitlines()
    if len(lines) <= 3 and any(len(line) >= 2000 for line in lines):
        return True
    return sum(len(line) >= 500 for line in lines) >= 3


def _normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _analyze_file(
    root: Path,
    root_identity: tuple[int, int],
    item: _DiscoveredFile,
    max_file_bytes: int,
) -> _FileRecord:
    path = item.absolute_path
    try:
        with _open_verified_binary(root, root_identity, item.path, item.identity) as stream:
            before = os.fstat(stream.fileno())
            size = before.st_size
            if max_file_bytes > 0 and size > max_file_bytes:
                file_hash, bytes_read = _hash_stream(stream)
                raw = None
            else:
                raw = stream.read(max_file_bytes + 1 if max_file_bytes > 0 else -1)
                bytes_read = len(raw)
                file_hash = hashlib.sha256(raw).hexdigest()
            after = os.fstat(stream.fileno())
    except OSError:
        return _FileRecord(
            item.path, path, item.identity, _language(item.path), item.size_hint,
            None, "unreadable", 0,
        )
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns or bytes_read != size:
        raise SnapshotError(f"input changed while reading: {item.path}")
    if raw is None:
        return _FileRecord(
            item.path, path, item.identity, _language(item.path), size,
            file_hash, "too_large", 0,
        )
    if _looks_binary(raw):
        return _FileRecord(
            item.path, path, item.identity, _language(item.path), size,
            file_hash, "binary", 0,
        )
    try:
        text = _normalize_text(raw.decode("utf-8-sig"))
    except UnicodeDecodeError:
        return _FileRecord(
            item.path, path, item.identity, _language(item.path), size,
            file_hash, "decode_error", 0,
        )
    if _looks_minified(item.path, text):
        return _FileRecord(
            item.path, path, item.identity, _language(item.path), size,
            file_hash, "minified", 0,
        )
    emitted = len(text.encode("utf-8"))
    return _FileRecord(
        item.path, path, item.identity, _language(item.path), size,
        file_hash, "embedded", emitted,
    )


def _analyze_files(
    root: Path,
    root_identity: tuple[int, int],
    files: Sequence[_DiscoveredFile],
    max_file_bytes: int,
    max_total_bytes: int,
) -> tuple[list[_FileRecord], int]:
    records: list[_FileRecord] = []
    emitted_total = 0
    for item in files:
        record = _analyze_file(root, root_identity, item, max_file_bytes)
        if record.status == "embedded" and max_total_bytes > 0:
            if emitted_total + record.emitted_bytes > max_total_bytes:
                record = _FileRecord(
                    record.path, record.absolute_path, record.identity,
                    record.language, record.size, record.sha256, "total_limit", 0,
                )
        if record.status == "embedded":
            emitted_total += record.emitted_bytes
        records.append(record)
    return records, emitted_total


def _load_verified_text(
    root: Path,
    root_identity: tuple[int, int],
    record: _FileRecord,
    max_file_bytes: int,
) -> str:
    try:
        with _open_verified_binary(
            root, root_identity, record.path, record.identity
        ) as stream:
            raw = stream.read(max_file_bytes + 1 if max_file_bytes > 0 else -1)
    except OSError as exc:
        raise SnapshotError(f"input became unreadable: {record.path}") from exc
    if max_file_bytes > 0 and len(raw) > max_file_bytes:
        raise SnapshotError(f"input grew beyond the file limit: {record.path}")
    if len(raw) != record.size or hashlib.sha256(raw).hexdigest() != record.sha256:
        raise SnapshotError(f"input changed before rendering: {record.path}")
    try:
        text = _normalize_text(raw.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise SnapshotError(f"input encoding changed before rendering: {record.path}") from exc
    if len(text.encode("utf-8")) != record.emitted_bytes:
        raise SnapshotError(f"input changed before rendering: {record.path}")
    return text


def _language(path: str) -> str:
    return _LANGUAGES.get(PurePosixPath(path).suffix.lower(), "")


def _priority(path: str) -> int:
    pure = PurePosixPath(path)
    base, parts, extension = pure.name, pure.parts, pure.suffix.lower()
    if base in {"README.md", "README", "LICENSE", "LICENSE.md", "NOTICE", "CHANGELOG.md", "CHANGELOG"}:
        return 0
    if base in {"pyproject.toml", "requirements.txt", "Pipfile", "Pipfile.lock", "poetry.lock"}:
        return 1
    if base in {"package.json", "pnpm-lock.yaml", "package-lock.json", "yarn.lock", "tsconfig.json"}:
        return 2
    if base in {"CMakeLists.txt", "Makefile", "Dockerfile", "docker-compose.yml", "docker-compose.yaml"}:
        return 3
    if parts and parts[0] in {"src", "app", "apps", "api", "core", "lib", "include"}:
        return 10
    if "tests" in parts or (parts and parts[0] in {"tests", "test"}):
        return 20
    if parts and parts[0] in {"scripts", "tools"}:
        return 30
    if extension in _LANGUAGES:
        return 40
    return 60


def _project_fingerprint(paths: Sequence[str]) -> list[str]:
    available = set(paths)
    pairs = (
        ("pyproject.toml", "python: pyproject.toml"),
        ("requirements.txt", "python: requirements.txt"),
        ("package.json", "node: package.json"),
        ("CMakeLists.txt", "cpp: CMakeLists.txt"),
        ("Makefile", "build: Makefile"),
        ("README.md", "docs: README"),
        ("LICENSE", "docs: LICENSE"),
    )
    return [label for filename, label in pairs if filename in available]


def _entrypoints(paths: Sequence[str]) -> list[str]:
    preferred = (
        "README.md", "pyproject.toml", "requirements.txt", "package.json", "manage.py",
        "CMakeLists.txt", "Makefile", "Dockerfile", "src/main.py", "main.py", "app.py",
        "src/main.ts", "src/main.tsx", "src/index.ts", "src/index.js",
    )
    available = set(paths)
    return [path for path in preferred if path in available][:25]


def _display_text(value: str) -> str:
    output = []
    for character in value:
        codepoint = ord(character)
        if character == "\n":
            output.append("\\n")
        elif character == "\r":
            output.append("\\r")
        elif character == "\t":
            output.append("\\t")
        elif codepoint < 32 or codepoint == 127:
            output.append(f"\\x{codepoint:02x}")
        else:
            output.append(character)
    return "".join(output)


def _html_code(value: str) -> str:
    safe = _display_text(value)
    return f"<code>{html.escape(safe, quote=False)}</code>"


def _fence(content: str) -> str:
    longest = current = 0
    for character in content:
        if character == "`":
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return "`" * max(3, longest + 1)


def _tree(paths: Sequence[str]) -> str:
    root: dict[str, dict] = {}
    for path in paths:
        node = root
        for part in PurePosixPath(path).parts:
            node = node.setdefault(part, {})
    lines = ["."]

    def render(node: dict[str, dict], prefix: str) -> None:
        items = sorted(node.items(), key=lambda item: item[0])
        for index, (name, children) in enumerate(items):
            last = index == len(items) - 1
            lines.append(f"{prefix}{'└──' if last else '├──'} {_display_text(name)}")
            if children:
                render(children, prefix + ("    " if last else "│   "))

    render(root, "")
    return "\n".join(lines)


def _record_meta(record: _FileRecord) -> str:
    bits = [f"lang={record.language or 'unknown'}", f"status={record.status}"]
    bits.append(f"bytes={record.size if record.size is not None else 'unknown'}")
    bits.append(f"sha256={record.sha256 or 'unavailable'}")
    return ", ".join(bits)


def _render(
    output: TextIO,
    root: Path,
    root_identity: tuple[int, int],
    records: Sequence[_FileRecord],
    result: SnapshotResult,
    *,
    max_file_bytes: int,
    max_total_bytes: int,
    use_gitignore: bool,
    use_default_excludes: bool,
    include_sensitive: bool,
    user_exclusion_count: int,
) -> None:
    paths = [record.path for record in records]
    output.write("---\n")
    output.write("snapshot_format: mdrepoatlas/v1\n")
    output.write("generated_by: mdrepoatlas\n")
    output.write(f"tool_version: {__version__}\n")
    output.write('source_root: "."\n')
    output.write("path_style: posix\nencoding: utf-8\nordering: generalized-priority-then-path\n")
    output.write(f"max_file_bytes: {max_file_bytes}\nmax_total_bytes: {max_total_bytes}\n")
    output.write(f"gitignore_enabled: {str(use_gitignore).lower()}\n")
    output.write(f"default_excludes_enabled: {str(use_default_excludes).lower()}\n")
    output.write(f"sensitive_paths_enabled: {str(include_sensitive).lower()}\n")
    output.write(f"user_exclusion_rules: {user_exclusion_count}\nstats:\n")
    output.write(f"  discovered_files: {result.discovered_files}\n")
    output.write(f"  embedded_files: {result.embedded_files}\n")
    output.write(f"  omitted_files: {result.omitted_files}\n")
    output.write(f"  emitted_bytes: {result.emitted_bytes}\n")
    output.write(f"  source_bytes: {result.source_bytes}\n")
    output.write(f"  excluded_paths: {result.excluded_paths}\n")
    output.write(f"  skipped_symlinks: {result.skipped_symlinks}\n")
    output.write(f"  gitignore_files: {result.gitignore_files}\n")
    facts = _project_fingerprint(paths)
    if facts:
        output.write("fingerprint:\n")
        for fact in facts:
            output.write(f"  - {json.dumps(fact, ensure_ascii=False)}\n")
    else:
        output.write("fingerprint: []\n")
    entries = _entrypoints(paths)
    if entries:
        output.write("entrypoints:\n")
        for entry in entries:
            output.write(f"  - {json.dumps(entry, ensure_ascii=False)}\n")
    else:
        output.write("entrypoints: []\n")
    output.write("---\n\n# Repository snapshot\n\n## Directory structure\n\n")
    tree = _tree(paths)
    tree_fence = _fence(tree)
    output.write(f"{tree_fence}text\n{tree}\n{tree_fence}\n\n")
    output.write("## File index by language\n\n")
    groups: dict[str, list[_FileRecord]] = {}
    for record in records:
        groups.setdefault(record.language or "unknown", []).append(record)
    for language in sorted(groups):
        items = groups[language]
        output.write(f"### {language} ({len(items)})\n\n")
        for record in items:
            output.write(f"- {_html_code(record.path)} — {_record_meta(record)}\n")
        output.write("\n")
    output.write("## Source files\n\n")
    for record in records:
        output.write(f"### FILE: {_html_code(record.path)}\n\n{_record_meta(record)}\n\n")
        if record.omitted:
            reasons = {
                "too_large": "per-file content limit exceeded",
                "binary": "binary content",
                "decode_error": "content is not valid UTF-8",
                "minified": "minified or generated single-line content",
                "total_limit": "total content limit reached",
                "unreadable": "file could not be read",
            }
            output.write(f"> OMITTED: {reasons.get(record.status, record.status)}.\n\n")
            continue
        text = _load_verified_text(root, root_identity, record, max_file_bytes)
        fence = _fence(text)
        output.write(f"{fence}{record.language}\n{text}")
        if text and not text.endswith("\n"):
            output.write("\n")
        output.write(f"{fence}\n\n")


def write_snapshot(
    source: os.PathLike[str] | str,
    output: TextIO,
    *,
    exclude: Iterable[str] = (),
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    use_gitignore: bool = True,
    use_default_excludes: bool = True,
    include_sensitive: bool = False,
    output_path: os.PathLike[str] | str | None = None,
) -> SnapshotResult:
    """Write a deterministic Markdown snapshot to a text stream.

    ``max_file_bytes`` limits the raw bytes loaded for any one embedded file.
    Oversized files are streamed only to calculate size and SHA-256 metadata.
    ``max_total_bytes`` limits normalized UTF-8 source content in deterministic
    file order. A value of ``0`` explicitly disables the corresponding limit.

    Git ignore rules, default noise exclusions, and user patterns are applied in
    that order. User negations may re-include ordinary defaults. Sensitive-path
    safeguards remain active unless ``include_sensitive=True`` is explicit.
    Symlinks and Windows reparse points are never followed.
    """
    if max_file_bytes < 0 or max_total_bytes < 0:
        raise ValueError("size limits must be non-negative")
    source_path = Path(source).expanduser()
    if source_path.is_symlink():
        raise SnapshotError("source root must not be a symlink")
    try:
        root = source_path.resolve(strict=True)
    except OSError as exc:
        raise SnapshotError(f"source directory is unavailable: {source}") from exc
    if not root.is_dir():
        raise SnapshotError(f"source is not a directory: {source}")
    try:
        root_info = root.stat(follow_symlinks=False)
    except OSError as exc:
        raise SnapshotError(f"source directory is unavailable: {source}") from exc
    if _is_link_or_reparse(root_info) or not stat.S_ISDIR(root_info.st_mode):
        raise SnapshotError("source root must be a regular directory")
    root_identity = _identity(root_info)
    user_patterns = [exclude] if isinstance(exclude, str) else list(exclude)
    reserved_candidates: list[os.PathLike[str] | str] = []
    if output_path is not None:
        reserved_candidates.append(output_path)
    inferred_name = getattr(output, "name", None)
    if isinstance(inferred_name, (str, os.PathLike)) and not str(inferred_name).startswith("<"):
        reserved_candidates.append(inferred_name)
    reserved_paths = _normalize_reserved(root, reserved_candidates)
    discovery = _discover(
        root,
        user_patterns=user_patterns,
        use_gitignore=use_gitignore,
        use_default_excludes=use_default_excludes,
        include_sensitive=include_sensitive,
        reserved_paths=reserved_paths,
    )
    records, emitted_total = _analyze_files(
        root, root_identity, discovery.files, max_file_bytes, max_total_bytes
    )
    result = SnapshotResult(
        discovered_files=len(records),
        embedded_files=sum(not record.omitted for record in records),
        omitted_files=sum(record.omitted for record in records),
        emitted_bytes=emitted_total,
        source_bytes=sum(record.size or 0 for record in records),
        excluded_paths=discovery.excluded_paths,
        skipped_symlinks=discovery.skipped_symlinks,
        gitignore_files=discovery.gitignore_files,
    )
    with tempfile.SpooledTemporaryFile(
        mode="w+", max_size=1024 * 1024, encoding="utf-8", newline="\n"
    ) as staged:
        _render(
            staged, root, root_identity, records, result,
            max_file_bytes=max_file_bytes,
            max_total_bytes=max_total_bytes,
            use_gitignore=use_gitignore,
            use_default_excludes=use_default_excludes,
            include_sensitive=include_sensitive,
            user_exclusion_count=len(user_patterns),
        )
        staged.seek(0)
        while True:
            chunk = staged.read(_COPY_CHUNK_CHARS)
            if not chunk:
                break
            output.write(chunk)
    return result

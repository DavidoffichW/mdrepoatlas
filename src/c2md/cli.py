#!/usr/bin/env python3
"""
c2md: Codebase to Markdown snapshot generator

Generates an LLM-optimized Markdown snapshot of a repository.

Output includes:
- metadata header
- directory structure
- language index
- deterministic ordering
- embedded source files

Framework-agnostic.

Usage:
    c2md
    c2md <source> -t <target> -o code_base.md

How to use:
1) Run: c2md or optionally python -m c2md.cli
2) Enter Source directory.
3) Enter Target directory (or press Enter for current directory).
4) Enter Exclude paths/patterns (comma-separated, relative to source; supports glob patterns like node_modules/**, *.o).
5) Optionally tune limits when prompted (press Enter to accept defaults).
6) Script generates code_base.md: metadata header + directory tree + language-grouped index + file contents (text only).
"""

import os
import sys
import datetime
import platform
import fnmatch
import hashlib
import argparse
from . import __version__

FRONTEND_EXTENSIONS = {".html", ".js", ".css", ".ts", ".tsx", ".jsx", ".vue", ".svelte"}
CONFIG_EXTENSIONS = {".json", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".xml"}
CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs", ".c", ".h", ".hpp", ".cc", ".cpp",
    ".cs", ".rb", ".php", ".swift", ".kt", ".m", ".mm", ".scala", ".pl", ".lua",
    ".f", ".for", ".f77", ".f90", ".f95", ".f03", ".f08",
    ".sh", ".ps1", ".sql",
}

DEFAULT_EXCLUDE_PATTERNS = [
    ".git/**",
    ".hg/**",
    ".svn/**",
    ".idea/**",
    ".vscode/**",
    "__pycache__/**",
    ".pytest_cache/**",
    ".mypy_cache/**",
    ".ruff_cache/**",
    ".tox/**",
    ".venv/**",
    "venv/**",
    "env/**",
    "dist/**",
    "build/**",
    "out/**",
    ".cache/**",
    "tmp/**",
    "temp/**",
    "*.log",
    "node_modules/**",
    ".next/**",
    ".nuxt/**",
    ".svelte-kit/**",
    ".astro/**",
    "coverage/**",
    "CMakeFiles/**",
    "cmake-build-*/**",
    "*.o",
    "*.obj",
    "*.a",
    "*.so",
    "*.dll",
    "*.dylib",
    "*.exe",
    "*.pdb",
    "*.mod",
    "*.out",
    "*.bin",
    "*.dat",
    "*.db",
    "*.sqlite",
    "*.sqlite3",
    "*.pdf",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.ico",
    "*.webp",
    "*.zip",
    "*.tar",
    "*.gz",
    "*.7z",
    "*.woff",
    "*.woff2",
    "*.ttf",
    "*.otf",
    "*.mp3",
    "*.mp4",
    "*.mov",
    "*.avi",
]

DEFAULT_MAX_FILE_BYTES = 512 * 1024
DEFAULT_MAX_TOTAL_BYTES = 0


def normalize_posix(p):
    p = p.strip().strip('"').strip("'")
    if not p:
        return ""
    p = p.replace("\\", "/")
    while "//" in p:
        p = p.replace("//", "/")
    if p.endswith("/") and p != "/":
        p = p.rstrip("/")
    return p


def is_glob_pattern(p):
    return any(ch in p for ch in ["*", "?", "["])


def ensure_dir_glob(p):
    if not p:
        return p
    if p.endswith("/**") or p.endswith("/*"):
        return p
    if p.endswith("/"):
        return p + "**"
    if "/" in p and not is_glob_pattern(p):
        return p
    return p


def normalize_patterns(patterns):
    out = []
    for raw in patterns:
        p = normalize_posix(raw)
        if not p:
            continue
        if p in [
            ".git", ".hg", ".svn", ".idea", ".vscode", "__pycache__", ".pytest_cache",
            ".mypy_cache", ".ruff_cache", ".tox", ".venv", "venv", "env", "node_modules",
            ".next", ".nuxt", ".svelte-kit", ".astro", "coverage", "CMakeFiles",
            "dist", "build", "out", ".cache", "tmp", "temp"
        ]:
            p = p + "/**"
        out.append(ensure_dir_glob(p))
    dedup = []
    seen = set()
    for p in out:
        if p not in seen:
            seen.add(p)
            dedup.append(p)
    return dedup


def match_any(rel_posix, patterns):
    for pat in patterns:
        if fnmatch.fnmatchcase(rel_posix, pat):
            return True
        if not is_glob_pattern(pat):
            if rel_posix == pat or rel_posix.startswith(pat.rstrip("/") + "/"):
                return True
    return False


def detect_language(filename):
    ext = os.path.splitext(filename)[1].lower()
    return {
        ".py": "python",
        ".md": "markdown",
        ".txt": "text",
        ".yml": "yaml",
        ".yaml": "yaml",
        ".toml": "toml",
        ".json": "json",
        ".xml": "xml",
        ".html": "html",
        ".css": "css",
        ".js": "javascript",
        ".jsx": "jsx",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".ps1": "powershell",
        ".sh": "bash",
        ".ini": "ini",
        ".cfg": "ini",
        ".sql": "sql",
        ".c": "c",
        ".h": "c",
        ".cc": "cpp",
        ".cpp": "cpp",
        ".hpp": "cpp",
        ".f": "fortran",
        ".for": "fortran",
        ".f77": "fortran",
        ".f90": "fortran",
        ".f95": "fortran",
        ".f03": "fortran",
        ".f08": "fortran",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".cs": "csharp",
        ".rb": "ruby",
        ".php": "php",
        ".kt": "kotlin",
        ".swift": "swift",
        ".m": "objectivec",
        ".mm": "objectivec",
        ".vue": "vue",
        ".svelte": "svelte",
    }.get(ext, "")


def normalize_newlines(s):
    return s.replace("\r\n", "\n").replace("\r", "\n")


def is_minified_text(rel_posix, content):
    base = os.path.basename(rel_posix).lower()
    if base.endswith(".min.js") or base.endswith(".min.css"):
        return True
    if content is None:
        return False
    lines = content.splitlines()
    if not lines:
        return False
    long_lines = sum(1 for ln in lines if len(ln) >= 500)
    if long_lines >= 3:
        return True
    if len(lines) <= 3 and any(len(ln) >= 2000 for ln in lines):
        return True
    return False


def sha256_bytes(b):
    h = hashlib.sha256()
    h.update(b)
    return h.hexdigest()


def looks_binary(b):
    if b is None:
        return False
    if b.startswith(b"\xEF\xBB\xBF"):
        b = b[3:]
    if not b:
        return False
    sample = b[:4096]
    if b"\x00" in sample:
        return True
    bad = 0
    for ch in sample:
        if ch in (9, 10, 13):
            continue
        if 32 <= ch <= 126:
            continue
        bad += 1
    return (bad / max(1, len(sample))) > 0.25


def read_text_best_effort(abs_path, max_bytes):
    try:
        with open(abs_path, "rb") as f:
            raw = f.read()
    except Exception:
        return None, None, None, None, "unreadable"

    file_bytes = len(raw)
    file_hash = sha256_bytes(raw)

    if max_bytes > 0 and file_bytes > max_bytes:
        return None, file_bytes, file_hash, True, "too_large"
    if looks_binary(raw):
        return None, file_bytes, file_hash, True, "binary"

    try:
        txt = raw.decode("utf-8")
        txt = normalize_newlines(txt)
        return txt, file_bytes, file_hash, False, "ok"
    except Exception:
        try:
            txt = raw.decode("utf-8", errors="replace")
            txt = normalize_newlines(txt)
            return txt, file_bytes, file_hash, False, "ok_replace"
        except Exception:
            return None, file_bytes, file_hash, True, "decode_fail"


def collect_files(src, exclude_patterns):
    files = []
    for dirpath, _, filenames in os.walk(src):
        rel_dir = os.path.relpath(dirpath, src)
        rel_dir_posix = "." if rel_dir == "." else normalize_posix(rel_dir)
        if rel_dir_posix != "." and match_any(rel_dir_posix, exclude_patterns):
            continue
        for fname in filenames:
            rel_posix = fname if rel_dir_posix == "." else f"{rel_dir_posix}/{fname}"
            rel_posix = normalize_posix(rel_posix)
            if match_any(rel_posix, exclude_patterns):
                continue
            files.append(rel_posix)
    return sorted(files)


def build_tree(src, exclude_patterns):
    lines = []

    def _tree(dir_path, prefix=""):
        try:
            entries = sorted(os.listdir(dir_path))
        except PermissionError:
            return

        visible = []
        for entry in entries:
            full = os.path.join(dir_path, entry)
            rel = os.path.relpath(full, src)
            rel_posix = normalize_posix(rel)
            if match_any(rel_posix, exclude_patterns):
                continue
            visible.append(entry)

        for i, entry in enumerate(visible):
            full = os.path.join(dir_path, entry)
            connector = "└──" if i == len(visible) - 1 else "├──"
            lines.append(f"{prefix}{connector} {entry}")
            if os.path.isdir(full):
                ext = "    " if i == len(visible) - 1 else "│   "
                _tree(full, prefix + ext)

    lines.append(os.path.basename(os.path.abspath(src)))
    _tree(src)
    return "\n".join(lines)


def detect_project_fingerprint(files):
    s = set(files)
    out = []

    def any_glob(glob_pat):
        return any(fnmatch.fnmatchcase(f, glob_pat) for f in s)

    if "package.json" in s:
        out.append("node: package.json")
    if "pnpm-lock.yaml" in s:
        out.append("node: pnpm-lock.yaml")
    if "package-lock.json" in s:
        out.append("node: package-lock.json")
    if "yarn.lock" in s:
        out.append("node: yarn.lock")
    if "tsconfig.json" in s:
        out.append("node: tsconfig.json")
    if any_glob("vite.config.*"):
        out.append("node: vite.config.*")
    if any_glob("next.config.*"):
        out.append("node: next.config.*")

    if "pyproject.toml" in s:
        out.append("python: pyproject.toml")
    if "requirements.txt" in s:
        out.append("python: requirements.txt")
    if "manage.py" in s:
        out.append("python: manage.py")

    if "CMakeLists.txt" in s:
        out.append("cpp: CMakeLists.txt")
    if "Makefile" in s:
        out.append("build: Makefile")

    if any_glob("*.f90") or any_glob("*.f") or any_glob("*.for"):
        out.append("fortran: source files detected")

    if "README.md" in s or "README" in s:
        out.append("docs: README")
    if "LICENSE" in s or "LICENSE.md" in s:
        out.append("docs: LICENSE")

    return out


def find_entrypoints(files):
    candidates = []
    preferred = [
        "README.md", "README",
        "pyproject.toml", "requirements.txt",
        "package.json",
        "manage.py",
        "CMakeLists.txt", "Makefile",
        "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
        "src/main.py", "main.py", "app.py",
        "src/main.ts", "src/main.tsx", "src/index.ts", "src/index.tsx",
        "src/index.js", "src/index.jsx", "index.js", "index.ts",
        "server.js", "server.ts",
        "asgi.py", "wsgi.py",
    ]
    s = set(files)
    for p in preferred:
        if p in s:
            candidates.append(p)
    for f in files:
        base = os.path.basename(f).lower()
        if base in ("main.c", "main.cpp", "main.cc", "main.f90", "main.f", "main.for"):
            candidates.append(f)
    seen = set()
    out = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out[:25]


def group_by_language(file_records):
    groups = {}
    for r in file_records:
        lang = r["lang"] if r["lang"] else "unknown"
        groups.setdefault(lang, []).append(r)
    for k in groups:
        groups[k].sort(key=lambda x: x["path"])
    return dict(sorted(groups.items(), key=lambda kv: kv[0]))


def generalized_priority(path):
    base = os.path.basename(path)
    ext = os.path.splitext(base)[1].lower()
    parts = path.split("/")

    if base in ("README.md", "README", "LICENSE", "LICENSE.md", "NOTICE", "CHANGELOG.md", "CHANGELOG"):
        return 0
    if base in ("pyproject.toml", "requirements.txt", "Pipfile", "Pipfile.lock", "poetry.lock"):
        return 1
    if base in ("package.json", "pnpm-lock.yaml", "package-lock.json", "yarn.lock", "tsconfig.json"):
        return 2
    if base in ("CMakeLists.txt", "Makefile", "configure.ac", "meson.build", "BUILD", "WORKSPACE"):
        return 3
    if base in ("Dockerfile", "docker-compose.yml", "docker-compose.yaml", ".env.example"):
        return 4
    if parts and parts[0] in ("src", "app", "apps", "api", "core", "lib", "include"):
        return 10
    if "services" in parts or "server" in parts:
        return 11
    if "tests" in parts or parts[0] in ("tests", "test"):
        return 20
    if parts and parts[0] in ("scripts", "tools"):
        return 30
    if ext in FRONTEND_EXTENSIONS:
        return 40
    if ext in CONFIG_EXTENSIONS:
        return 45
    if ext in CODE_EXTENSIONS:
        return 50
    return 60


def fmt_file_meta_line(r):
    bits = []
    if r.get("lang"):
        bits.append(f"lang={r['lang']}")
    if r.get("bytes") is not None:
        bits.append(f"bytes={r['bytes']}")
    if r.get("sha256"):
        bits.append(f"sha256={r['sha256']}")
    if r.get("status") and r["status"] != "ok":
        bits.append(f"status={r['status']}")
    return ", ".join(bits)


def build_index_section(file_records):
    groups = group_by_language(file_records)
    md = []
    md.append("## Index (grouped by language)\n")
    for lang, items in groups.items():
        md.append(f"### Index: {lang} ({len(items)} files)\n")
        for r in items:
            flags = []
            if r.get("omitted"):
                flags.append("OMITTED")
            if r.get("minified"):
                flags.append("MINIFIED")
            suffix = f" [{' '.join(flags)}]" if flags else ""
            meta = fmt_file_meta_line(r)
            md.append(f"- `{r['path']}`{suffix} — {meta}")
        md.append("")
    return "\n".join(md).rstrip() + "\n"


def build_header(src, out_path, exclude_patterns, files, sanitize_paths, file_records, total_bytes_emitted, max_file_bytes, max_total_bytes):
    utc = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    fp = detect_project_fingerprint(files)
    entrypoints = find_entrypoints(files)
    
    display_src = "<project_root>" if sanitize_paths else src

    header = []
    header.append("---")
    header.append("snapshot_format: one-file-per-module")
    header.append("generated_by: c2md")
    header.append(f"tool_version: {__version__}")
    header.append(f"generated_at_utc: {utc}")
    header.append(f"source_root: {display_src}")
    header.append(f"output_file: {out_path}")
    header.append(f"os: {platform.system()}")
    header.append("path_style: posix-in-doc")
    header.append("encoding: utf-8 (best effort)")
    header.append("ordering: generalized-priority")
    header.append(f"max_file_bytes: {max_file_bytes}")
    header.append(f"max_total_bytes: {max_total_bytes}")
    header.append("exclude_patterns:")
    if exclude_patterns:
        for e in exclude_patterns:
            header.append(f"  - {e}")
    else:
        header.append("  - []")
        
    embedded = sum(1 for r in file_records if not r["omitted"])
    omitted = sum(1 for r in file_records if r["omitted"])

    header.append("stats:")
    header.append(f"  total_files_discovered: {len(files)}")
    header.append(f"  total_files_listed: {len(file_records)}")
    header.append(f"  total_bytes_emitted: {total_bytes_emitted}")
    header.append(f"  embedded_files: {embedded}")
    header.append(f"  omitted_files: {omitted}")
    header.append("notes:")
    header.append('  - "Each file section begins with: ### FILE: <relative/path> (...metadata...)"')
    header.append('  - "Search for ### FILE: to jump to a file section."')
    if fp:
        header.append("fingerprint:")
        for x in fp:
            header.append(f"  - {x}")
    if entrypoints:
        header.append("entrypoints:")
        for x in entrypoints:
            header.append(f"  - {x}")
    header.append("---\n")

    header.append("# Document Navigation\n")
    header.append("## How to use this snapshot")
    header.append("- Read metadata header, then Directory Structure, then Index.")
    header.append("- Jump to any file by searching for: `### FILE: path/to/file`.")
    header.append("- Treat omitted files as out-of-scope unless explicitly re-exported.\n")

    return "\n".join(header)


def build_file_records(src, sorted_files, max_file_bytes, max_total_bytes):
    file_records = []
    contents_cache = {}
    total_bytes_emitted = 0

    for rel_posix in sorted_files:
        abs_path = os.path.join(src, rel_posix.replace("/", os.sep))
        text, file_bytes, file_hash, omit, status = read_text_best_effort(abs_path, max_file_bytes)

        lang = detect_language(rel_posix)
        minified = is_minified_text(rel_posix, text) if text is not None else False

        if minified:
            omit = True
            if status in ("ok", "ok_replace"):
                status = "minified"

        would_add = 0 if (omit or text is None) else len(text.encode("utf-8", "ignore"))
        if max_total_bytes > 0 and (total_bytes_emitted + would_add) > max_total_bytes:
            omit = True
            status = "total_cap"

        r = {
            "path": rel_posix,
            "lang": lang,
            "bytes": file_bytes,
            "sha256": file_hash,
            "omitted": bool(omit),
            "status": status,
            "minified": bool(minified),
        }
        file_records.append(r)

        if not omit and text is not None:
            contents_cache[rel_posix] = text
            total_bytes_emitted += len(text.encode("utf-8", "ignore"))
        else:
            contents_cache[rel_posix] = None

    return file_records, contents_cache, total_bytes_emitted


def write_markdown(out_path, header, tree, file_records, contents_cache):
    md = []
    md.append(header)
    md.append("# Code Base\n")
    md.append("## Directory Structure\n")
    md.append("```text")
    md.append(tree)
    md.append("```\n")
    md.append(build_index_section(file_records))
    md.append("")

    for r in file_records:
        rel = r["path"]
        meta = fmt_file_meta_line(r)
        md.append(f"### FILE: {rel} ({meta})\n")

        content = contents_cache[rel]
        if content is None:
            md.append("```text")
            if r.get("status") == "too_large":
                md.append(f"OMITTED: too large to embed (bytes={r.get('bytes')}, sha256={r.get('sha256')})")
            elif r.get("status") == "binary":
                md.append(f"OMITTED: binary file (bytes={r.get('bytes')}, sha256={r.get('sha256')})")
            elif r.get("status") == "minified":
                md.append(f"OMITTED: minified file (bytes={r.get('bytes')}, sha256={r.get('sha256')})")
            elif r.get("status") == "total_cap":
                md.append(f"OMITTED: total embed cap reached (bytes={r.get('bytes')}, sha256={r.get('sha256')})")
            elif r.get("status") == "unreadable":
                md.append("Unable to read file from disk")
            elif r.get("status") == "decode_fail":
                md.append(f"Unable to decode contents (bytes={r.get('bytes')}, sha256={r.get('sha256')})")
            else:
                md.append(f"OMITTED/UNAVAILABLE (status={r.get('status')}, bytes={r.get('bytes')}, sha256={r.get('sha256')})")
            md.append("```\n")
            continue

        lang = detect_language(rel)
        md.append(f"```{lang}")
        md.append(content.rstrip())
        md.append("```\n")

    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(md))


def parse_args(argv):
    p = argparse.ArgumentParser(prog="c2md", description="LLM-optimized codebase snapshot generator (codebase to single Markdown).")
    p.add_argument("-o", "--output", default="code_base.md", help="Output filename (default: code_base.md).")
    p.add_argument("source", nargs="?", help="Source directory (repository root).")
    p.add_argument("-t", "--target", default=None, help="Target directory for output (default: current directory).")
    p.add_argument("--sanitize-paths", action="store_true", help="Hide absolute filesystem paths in snapshot header")
    p.add_argument("-x", "--exclude", default="", help="Comma-separated exclude patterns relative to source (supports globs).")
    p.add_argument("--no-default-excludes", action="store_true", help="Disable default excludes for builds/binaries/node_modules/etc.")
    p.add_argument("--max-file-bytes", type=int, default=DEFAULT_MAX_FILE_BYTES, help=f"Max bytes per file to embed (0=unlimited, default={DEFAULT_MAX_FILE_BYTES}).")
    p.add_argument("--max-total-bytes", type=int, default=DEFAULT_MAX_TOTAL_BYTES, help="Max total bytes to embed across all files (0=unlimited).")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p.parse_args(argv)


def interactive_inputs():
    src = input("Source directory: ").strip()
    if not os.path.isdir(src):
        print(f"Error: source '{src}' is not a valid directory.")
        sys.exit(1)

    tgt = input("Target directory (leave empty for current directory): ").strip()
    if not tgt:
        tgt = "."

    excl = input("Exclude paths/patterns (comma-separated, relative to source; supports globs): ").strip()
    use_defaults = input("Use default excludes for binaries/build/node_modules/etc? (Y/n): ").strip().lower()
    no_defaults = not (use_defaults in ("", "y", "yes"))

    max_file_bytes_in = input(f"Max bytes per file to embed (default {DEFAULT_MAX_FILE_BYTES}, 0=unlimited): ").strip()
    max_total_bytes_in = input(f"Max total bytes to embed across all files (default {DEFAULT_MAX_TOTAL_BYTES}, 0=unlimited): ").strip()

    try:
        max_file_bytes = int(max_file_bytes_in) if max_file_bytes_in else DEFAULT_MAX_FILE_BYTES
    except Exception:
        max_file_bytes = DEFAULT_MAX_FILE_BYTES

    try:
        max_total_bytes = int(max_total_bytes_in) if max_total_bytes_in else DEFAULT_MAX_TOTAL_BYTES
    except Exception:
        max_total_bytes = DEFAULT_MAX_TOTAL_BYTES

    out_name = input("Output filename (default code_base.md): ").strip()
    if not out_name:
        out_name = "code_base.md"

    return src, tgt, out_name, excl, no_defaults, max_file_bytes, max_total_bytes


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]

    args = parse_args(argv)
    sanitize_paths = getattr(args, "sanitize_paths", False)

    if not args.source:
        src, tgt, out_name, excl, no_defaults, max_file_bytes, max_total_bytes = interactive_inputs()
    else:
        src = args.source
        if not os.path.isdir(src):
            print(f"Error: source '{src}' is not a valid directory.")
            return 1
        tgt = args.target if args.target else "."
        out_name = args.output
        excl = args.exclude
        no_defaults = bool(args.no_default_excludes)
        max_file_bytes = int(args.max_file_bytes)
        max_total_bytes = int(args.max_total_bytes)

    exclude_patterns = [] if no_defaults else list(DEFAULT_EXCLUDE_PATTERNS)
    if excl:
        for e in excl.split(","):
            e = normalize_posix(e)
            if e:
                exclude_patterns.append(e)
    exclude_patterns = normalize_patterns(exclude_patterns)

    files = collect_files(src, exclude_patterns)
    tree = build_tree(src, exclude_patterns)
    sorted_files = sorted(files, key=lambda p: (generalized_priority(p), p))

    out_dir = tgt
    out_path = os.path.join(out_dir, out_name)

    file_records, contents_cache, total_bytes_emitted = build_file_records(src, sorted_files, max_file_bytes, max_total_bytes)
    header = build_header(src, out_path, exclude_patterns, files, sanitize_paths, file_records, total_bytes_emitted, max_file_bytes, max_total_bytes)
    write_markdown(out_path, header, tree, file_records, contents_cache)

    print(f"Generated '{out_path}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""Command-line interface for mdrepoatlas."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

from . import SnapshotError, __version__, write_snapshot
from .core import DEFAULT_MAX_FILE_BYTES, DEFAULT_MAX_TOTAL_BYTES


def _non_negative(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return number


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export a local repository as deterministic, navigable Markdown."
    )
    parser.add_argument("source", nargs="?", help="Repository or directory to export.")
    parser.add_argument(
        "-o", "--output", default="code_base.md", help="Output path, or '-' for stdout."
    )
    parser.add_argument(
        "-t", "--target", default=None,
        help="Backward-compatible output directory used with a relative --output path.",
    )
    parser.add_argument(
        "-x", "--exclude", action="append", default=[], metavar="PATTERN",
        help="Git-style exclusion rule. Repeat or separate rules with commas; '!' negates.",
    )
    parser.add_argument("--no-gitignore", action="store_true", help="Do not read .gitignore files.")
    parser.add_argument(
        "--no-default-excludes", action="store_true",
        help="Disable ordinary build/cache/noise defaults; sensitive safeguards remain.",
    )
    parser.add_argument(
        "--include-sensitive", action="store_true",
        help="Explicitly allow paths normally blocked as credentials or sensitive configuration.",
    )
    parser.add_argument(
        "--sanitize-paths", action="store_true", help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--max-file-bytes", type=_non_negative, default=DEFAULT_MAX_FILE_BYTES,
        help=f"Raw bytes allowed per embedded file; 0 disables (default: {DEFAULT_MAX_FILE_BYTES}).",
    )
    parser.add_argument(
        "--max-total-bytes", type=_non_negative, default=DEFAULT_MAX_TOTAL_BYTES,
        help=f"Normalized UTF-8 bytes embedded in total; 0 disables (default: {DEFAULT_MAX_TOTAL_BYTES}).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def _patterns(values: list[str]) -> list[str]:
    patterns = []
    for value in values:
        patterns.extend(part.strip() for part in value.split(",") if part.strip())
    return patterns


def _interactive_args() -> argparse.Namespace:
    source = input("Source directory: ").strip()
    target = input("Target directory (leave empty for current directory): ").strip() or "."
    exclusions = input("Exclude rules (comma-separated, optional): ").strip()
    output = input("Output filename (default code_base.md): ").strip() or "code_base.md"
    return argparse.Namespace(
        source=source,
        output=output,
        target=target,
        exclude=[exclusions] if exclusions else [],
        no_gitignore=False,
        no_default_excludes=False,
        include_sensitive=False,
        max_file_bytes=DEFAULT_MAX_FILE_BYTES,
        max_total_bytes=DEFAULT_MAX_TOTAL_BYTES,
    )


def _output_path(args: argparse.Namespace) -> Path:
    path = Path(args.output).expanduser()
    if args.target and not path.is_absolute():
        path = Path(args.target).expanduser() / path
    return path.absolute()


def _write_path(args: argparse.Namespace, patterns: list[str]):
    target = _output_path(args)
    if not target.parent.is_dir():
        raise SnapshotError(f"output directory does not exist: {target.parent}")
    with tempfile.SpooledTemporaryFile(
        mode="w+", max_size=1024 * 1024, encoding="utf-8", newline="\n"
    ) as staged:
        result = write_snapshot(
            args.source,
            staged,
            exclude=patterns,
            max_file_bytes=args.max_file_bytes,
            max_total_bytes=args.max_total_bytes,
            use_gitignore=not args.no_gitignore,
            use_default_excludes=not args.no_default_excludes,
            include_sensitive=args.include_sensitive,
            output_path=target,
        )
        staged.seek(0)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=target.parent,
                prefix=f".{target.name}.", suffix=".tmp", delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                shutil.copyfileobj(staged, temporary)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, target)
        except Exception:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise
    return target, result


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.source is None:
        args = _interactive_args()
    patterns = _patterns(args.exclude)
    if args.include_sensitive:
        print(
            "warning: sensitive-path safeguards are disabled; review the output before sharing",
            file=sys.stderr,
        )
    try:
        if args.output == "-":
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8", newline="\n")
            result = write_snapshot(
                args.source,
                sys.stdout,
                exclude=patterns,
                max_file_bytes=args.max_file_bytes,
                max_total_bytes=args.max_total_bytes,
                use_gitignore=not args.no_gitignore,
                use_default_excludes=not args.no_default_excludes,
                include_sensitive=args.include_sensitive,
            )
            destination = "stdout"
        else:
            target, result = _write_path(args, patterns)
            destination = str(target)
    except (OSError, SnapshotError, ValueError) as exc:
        print(f"mdrepoatlas: error: {exc}", file=sys.stderr)
        return 1
    print(
        f"mdrepoatlas: wrote {destination}; "
        f"{result.embedded_files} embedded, {result.omitted_files} omitted",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

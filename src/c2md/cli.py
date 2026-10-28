"""Compatibility entry point for ``python -m c2md.cli``."""

from mdrepoatlas.cli import main, parse_args

__all__ = ["main", "parse_args"]


if __name__ == "__main__":
    raise SystemExit(main())

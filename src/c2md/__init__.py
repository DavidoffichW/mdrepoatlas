"""Compatibility namespace for mdrepoatlas 0.1.x imports."""

from mdrepoatlas import SnapshotError, SnapshotResult, __version__, write_snapshot

__all__ = ["SnapshotError", "SnapshotResult", "write_snapshot", "__version__"]

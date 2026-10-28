from importlib import metadata

import pytest


def test_installed_distribution_exposes_console_entry_point() -> None:
    try:
        distribution = metadata.distribution("mdrepoatlas")
    except metadata.PackageNotFoundError:
        pytest.skip("distribution is not installed in this source-only environment")

    entries = {
        entry.name: entry.value
        for entry in distribution.entry_points
        if entry.group == "console_scripts"
    }
    assert entries["mdrepoatlas"] == "mdrepoatlas.cli:main"

## [0.2.0] - Unreleased

### Added
- Public `mdrepoatlas.write_snapshot` API with a concise result summary
- Root and nested `.gitignore` handling through `pathspec`
- Stdout output with diagnostics isolated on stderr
- Functional tests for traversal, exclusions, limits, output, CLI, and API behavior

### Changed
- Require Python 3.11 or newer
- Make snapshots deterministic by omitting timestamps, host details, and absolute paths
- Stream hash calculation for oversized files and stage final output before copying
- Use explicit omission records for binary, invalid UTF-8, unreadable, and limited files
- Preserve `c2md` imports and module execution as compatibility wrappers
- Require a manual, identity-validated release workflow before PyPI publication

### Security
- Skip symlinks and Windows reparse points by default
- Validate opened file handles on every content pass to reject concurrent symlink, junction, and outside-root substitutions
- Protect common credential and sensitive configuration paths unless explicitly overridden
- Prevent an output file inside the source tree from ingesting itself

## [0.1.2] - 2025-09-19

### Changed
- Hardened package metadata and classifiers
- Added project URLs section
- Improved setuptools packaging configuration
- Version exposure alignment

### Fixed
- Namespace conflict with previously taken package names
- Distribution metadata cleanup
- argparse added --version

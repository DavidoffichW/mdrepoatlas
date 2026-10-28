# mdrepoatlas

`mdrepoatlas` exports a local software repository as one deterministic,
navigable Markdown document. The snapshot contains compact metadata, a
directory tree, a language-grouped index, and one section for every discovered
file. Binary, oversized, unreadable, and total-limit files remain visible as
explicit omission records.

The tool reads the local filesystem only. It does not access the Git network or
modify the source repository.

## Requirements and installation

mdrepoatlas 0.2.0 requires Python 3.11 or newer.

```bash
python -m pip install mdrepoatlas
```

For development from a clone:

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
```

## Command line

Write a snapshot to a file:

```bash
mdrepoatlas ./repository -o snapshot.md
```

Write only Markdown to stdout. Status and diagnostics go to stderr:

```bash
mdrepoatlas ./repository -o - > snapshot.md
```

The older target-directory form remains supported:

```bash
mdrepoatlas ./repository -t ./exports -o snapshot.md
```

Apply Git-style user exclusions. Repeat `--exclude`, use commas, or use `!` to
negate an earlier ordinary rule:

```bash
mdrepoatlas ./repository \
  --exclude "docs/generated/" \
  --exclude "*.csv,!examples/sample.csv" \
  -o snapshot.md
```

Running `mdrepoatlas` without a source starts the retained interactive prompt.
Interactive mode asks for the source, target directory, exclusions, and output
filename; other options use their safe defaults.

The legacy `--sanitize-paths` flag is still accepted as a no-op. Path
sanitization is always enabled in v0.2.0 output.

## Python API

```python
from mdrepoatlas import write_snapshot

with open("snapshot.md", "w", encoding="utf-8", newline="\n") as output:
    result = write_snapshot("./repository", output)

print(result.embedded_files, result.omitted_files)
```

`write_snapshot` accepts `exclude`, `max_file_bytes`, `max_total_bytes`,
`use_gitignore`, `use_default_excludes`, and `include_sensitive`. It returns an
immutable `SnapshotResult` with discovered, embedded, omitted, byte, exclusion,
symlink, and `.gitignore` counts. The existing `c2md` import and
`python -m c2md.cli` entry point remain compatibility wrappers.

## Discovery and exclusions

By default mdrepoatlas:

- reads root and nested `.gitignore` files using Git wildmatch rules;
- applies ordinary cache, build, VCS, dependency, binary-archive, and media
  exclusions;
- blocks common credential and sensitive configuration paths such as `.env`,
  private-key files, `.ssh/`, `.aws/`, and `.kaggle/` credential files;
- skips file and directory symlinks, including Windows reparse points;
- validates the actual opened file on every content pass so a path changed to a
  symlink, junction, or outside-root target is rejected before its bytes are
  read;
- excludes the requested output file when it is inside the source tree; and
- emits files in a stable generalized-priority, then path, order.

`--no-default-excludes` disables ordinary noise exclusions but not sensitive
safeguards. `--include-sensitive` is the explicit override for those safeguards
and prints a warning to stderr. Automatic exclusions are defensive defaults,
not secrets detection or a confidentiality guarantee. Always review a snapshot
before sharing it.

Ignore files are configuration input, so they use the same verified, no-follow
opening boundary as source content. A `.gitignore` that becomes a symlink,
junction, outside-root target, unreadable file, or non-regular file causes the
export to fail without copying partial Markdown. `--no-gitignore` explicitly
disables this configuration processing.

## Size and reading semantics

Defaults:

- `--max-file-bytes 524288` (512 KiB)
- `--max-total-bytes 10485760` (10 MiB)

Git ignore parsing has separate fixed safety limits: 262144 raw bytes per
`.gitignore` and 1048576 raw bytes in total across root and nested ignore files.
Files are checked and read with a bounded buffer. Exceeding either limit stops
the export with an actionable error rather than continuing with an incomplete
ignore policy. These configuration limits are intentionally independent of the
source-content options above.

The file limit is checked from file metadata before content is loaded. An
oversized file is streamed only to calculate accurate size and SHA-256
metadata, then recorded as omitted. The total limit counts normalized UTF-8
source bytes in deterministic file order. Files that would cross it are listed
as `total_limit` omissions. Passing `0` explicitly disables the corresponding
limit.

Text must be valid UTF-8; Unicode is preserved and newlines are normalized to
LF. Binary or invalid-UTF-8 files are not embedded. The renderer rereads each
embedded file and verifies its size and hash. Secure opens are applied during
both the analysis and rendering passes. On POSIX, each path component is opened
relative to the source-directory descriptor without following symlinks. On
Windows, the final path and identity of the actual opened handle are checked
against the source root. If a path becomes unsafe or a file changes, the
operation fails instead of producing an unauthorized or internally
inconsistent snapshot.

Repository contents are processed one file at a time. The final document is
staged in a spooled temporary stream so failed reads do not copy a partial
snapshot to the caller.

## Output shape

```text
---
snapshot_format: mdrepoatlas/v1
tool_version: 0.2.0
stats:
  discovered_files: ...
  embedded_files: ...
  omitted_files: ...
---

# Repository snapshot
## Directory structure
## File index by language
## Source files
### FILE: <code>src/example.py</code>
```

Code fences expand when source contains backticks. Paths are HTML-escaped in
headings and indexes. Default metadata contains no generation timestamp,
absolute local path, output path, operating-system name, or host identifier, so
unchanged inputs and options produce byte-identical output.

## Development

Run the functional suite:

```bash
python -m pytest
```

Build and check distributions:

```bash
python -m build
python -m twine check dist/*
```

CI runs the tests on Python 3.11 through 3.14 on Linux and Windows, then
builds the wheel and source distribution and smoke-tests an installed wheel.
Publication is a separate, manually dispatched workflow. It requires an
existing `vX.Y.Z` tag and the full expected commit SHA, verifies that the tag,
package version, commit, and `main` history agree, builds and checks the
distributions, and only then reaches the `pypi` GitHub Environment.

The repository must configure that `pypi` Environment with required reviewers
and an environment-scoped `PYPI_API_TOKEN`; the workflow file cannot enforce
those repository settings by itself. PyPI Trusted Publishing is preferable to
a long-lived API token when repository-side OIDC trust is configured, but this
workflow does not assume that configuration exists.

## License

[MIT](LICENSE)

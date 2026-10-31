# mdrepoatlas

> Turn a source tree into one readable Markdown file.

mdrepoatlas walks a local repository and writes a snapshot with a directory
tree, a language-grouped index, and a section for every discovered file. Files
that cannot be embedded are still listed, along with the reason they were
skipped.

It is deterministic by default: the same files and options produce the same
bytes. It does not contact Git remotes or modify the source repository.

## Install

mdrepoatlas requires Python 3.11 or newer.

```bash
python -m pip install mdrepoatlas
```

## Usage

Write a snapshot to a file:

```bash
mdrepoatlas ./repository -o snapshot.md
```

Write Markdown to stdout:

```bash
mdrepoatlas ./repository -o - > snapshot.md
```

Diagnostics go to stderr, so redirected stdout contains only the snapshot.

Exclude files with Git-style patterns:

```bash
mdrepoatlas ./repository \
  --exclude "docs/generated/" \
  --exclude "*.csv,!examples/sample.csv" \
  -o snapshot.md
```

Repeat `--exclude`, separate patterns with commas, or use `!` to negate an
earlier ordinary exclusion.

Running `mdrepoatlas` without a source starts an interactive prompt. The older
target-directory form also remains available:

```bash
mdrepoatlas ./repository -t ./exports -o snapshot.md
```

## Python API

```python
from mdrepoatlas import write_snapshot

with open("snapshot.md", "w", encoding="utf-8", newline="\n") as output:
    result = write_snapshot("./repository", output)

print(result.embedded_files, result.omitted_files)
```

`write_snapshot` accepts custom exclusions, per-file and total size limits,
Git-ignore handling, default exclusions, and the sensitive-file override. It
returns an immutable `SnapshotResult` with useful file, byte, exclusion,
symlink, and `.gitignore` counts.

The previous `c2md` imports and `python -m c2md.cli` command are kept as
compatibility wrappers.

## What gets included

By default, mdrepoatlas:

- follows root and nested `.gitignore` rules;
- leaves out common build output, caches, dependencies, archives, and media;
- blocks common credential paths such as `.env`, private keys, `.ssh/`,
  `.aws/`, and Kaggle credentials;
- skips symlinks and Windows reparse points;
- prevents an output file inside the source tree from ingesting itself; and
- emits files in a stable order.

Sensitive exclusions are separate from ordinary exclusions. Turning off the
ordinary defaults does not turn off the credential safeguards. Use
`--include-sensitive` when you genuinely intend to export those paths; the
command prints a warning when you do.

These checks are guardrails, not secrets detection. Review a snapshot before
sharing it.

## Limits

The defaults are:

- 512 KiB per source file
- 10 MiB of embedded source in total
- 256 KiB per `.gitignore`
- 1 MiB across all loaded `.gitignore` files

Change the source limits with `--max-file-bytes` and `--max-total-bytes`.
Passing `0` disables the corresponding source limit.

Oversized files are streamed for size and SHA-256 metadata, but their content
is not loaded into the snapshot. Binary data, invalid UTF-8, unreadable files,
and files beyond the total limit become explicit omission records.

Ignore files are part of the discovery policy, so mdrepoatlas treats them more
strictly. An unsafe, unreadable, or oversized `.gitignore` stops the export
instead of silently continuing with fewer exclusions. Use `--no-gitignore`
when you intentionally do not want ignore processing.

## Filesystem safety

Every source read validates the file that was actually opened. On POSIX,
mdrepoatlas uses descriptor-relative no-follow traversal. On Windows, it checks
the opened handle's identity and final path against the source root.

If a file changes, becomes a link, or moves outside the root during an export,
the export fails. The finished Markdown is staged before it is copied to the
destination, so a failed run does not leave a plausible partial snapshot.

## Output

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

Code fences grow when embedded source contains backticks. Paths are escaped in
headings and indexes. Default metadata does not include timestamps, absolute
paths, hostnames, or operating-system details.

## Development

```bash
git clone https://github.com/DavidoffichW/mdrepoatlas.git
cd mdrepoatlas
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest
```

Build and check the distributions:

```bash
python -m build
python -m twine check dist/*
```

CI covers Python 3.11 through 3.14 on Windows and Linux. Publishing is a
separate manual workflow that checks the requested tag, package version,
commit, and `main` history before it can reach the protected PyPI environment.

## License

[MIT](LICENSE)

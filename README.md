# mdrepoatlas: Codebase to Markdown (LLM-Optimized Snapshot Generator)

`mdrepoatlas` converts a software project into a **single structured Markdown document** (`code_base.md`) designed for Large Language Models to navigate efficiently.

Instead of uploading repositories, zipping folders, or pasting fragments, `mdrepoatlas` produces a **deterministic, navigable, AI-ready snapshot** of your codebase.

---

## Why mdrepoatlas Exists

LLMs do not understand repositories.

They understand **documents**.

Traditional repo exports create problems:

- ❌ Too many irrelevant files (`node_modules`, binaries)
- ❌ No navigation structure
- ❌ Context fragmentation
- ❌ Token waste
- ❌ Hard for LLMs to reason globally

`mdrepoatlas` solves this by generating a **single authoritative document**:

```

code_base.md

```

containing:

✅ Metadata header  
✅ Project fingerprint detection  
✅ Directory tree  
✅ Language-grouped index  
✅ Deterministic file ordering  
✅ Binary/build exclusion  
✅ Size-safe embedding  
✅ Stable navigation anchors  

The result is a document an LLM can **read, internalize, and navigate efficiently**.

---

## Example Output

```

code_base.md
├── Metadata Header
├── Project Navigation Guide
├── Directory Structure
├── File Index (grouped by language)
└── Full Source Files
└── ### FILE: src/main.py (...)

````

---

## Supported Projects

`mdrepoatlas` is framework-agnostic.

Works with:

- Python / Django / FastAPI
- React / Next.js / Node
- C / C++
- Fortran
- Rust / Go
- Mixed monorepos
- Research repositories
- Scientific computing projects
- Enterprise platforms

---

## Installation

Clone:

```bash
git clone https://github.com/DavidoffichW/mdrepoatlas.git
cd mdrepoatlas
````

## Install (editable)
```bash
pip install -e .
````

## Usage

Interactive mode:

```bash
mdrepoatlas
```

Non-interactive:

```bash
mdrepoatlas /path/to/repo -t /path/to/output -o code_base.md
```

Exclude patterns (comma-separated; supports globs):

```bash
mdrepoatlas /path/to/repo -x "node_modules/**,dist/**,*.pdf"
```

Disable default exclusions:

```bash
mdrepoatlas /path/to/repo --no-default-excludes
```

Size limits:

```bash
mdrepoatlas /path/to/repo --max-file-bytes 1048576 --max-total-bytes 0
```


No dependencies required.

Python 3.8+ recommended.

---


You will be prompted for:

| Prompt           | Description            |
| ---------------- | ---------------------- |
| Source directory | Project root           |
| Target directory | Output location        |
| Excludes         | Optional glob patterns |
| Default excludes | Skip builds/binaries   |
| Size limits      | Prevent huge files     |

Example:

```
Source directory:
~/projects/my_app

Target directory:
~/exports

Exclude:
docs/build/**, *.csv
```

Output:

```
exports/code_base.md
```

---

## Default Smart Exclusions

Automatically removes noise:

* `.git/`
* `node_modules/`
* virtual environments
* build artifacts
* binaries
* media files
* compiled objects
* caches

LLM receives **signal only**.

---

## Why This Works Well For LLMs

The generated document teaches the model how to read it.

Key design principles:

### 1. Deterministic Structure

Every file appears as:

```
### FILE: path/to/file.py (metadata)
```

LLMs can jump instantly.

---

### 2. Navigation Before Content

Models first learn:

* project structure
* entrypoints
* languages
* priorities

before reading implementation.

---

### 3. Context Efficiency

Instead of scanning thousands of irrelevant files:

* binaries are omitted
* minified bundles skipped
* oversized files summarized

---

## Example Prompt for ChatGPT / Claude

After generating `code_base.md`, upload it and start with:

---

### 🔹 Recommended Initialization Prompt

```
You are now analyzing a full project snapshot.

The uploaded file `code_base.md` is an authoritative
LLM-optimized export of the repository.

Instructions:
1. Read the metadata header first.
2. Use the Directory Structure and Index sections to build a mental map.
3. Treat each "### FILE:" section as an independent module.
4. Do NOT assume missing files exist outside the snapshot.
5. Prefer entrypoints and core modules when reasoning.

First task:
Summarize the system architecture and identify primary subsystems.

```

---

### 🔹 Example Follow-up Prompts

Architecture understanding:

```
Explain the project architecture using only the snapshot.
```

Refactoring:

```
Identify architectural weaknesses and propose improvements.
```

Bug investigation:

```
Search for potential concurrency or state-management issues.
```

Feature design:

```
Design a new feature consistent with existing patterns.
```

---

## Recommended LLM Workflow

1. Run `mdrepoatlas`
2. Upload `code_base.md`
3. Initialize model using prompt above
4. Work normally

You now have **full-repo reasoning**.

---

## Design Philosophy

`mdrepoatlas` treats an LLM as:

> a deterministic reader of structured technical documents.

The goal is not compression.

The goal is **cognitive alignment between repository and model**.

---

## Comparison

| Method      | Result                     |
| ----------- | -------------------------- |
| Upload repo | ❌ inconsistent             |
| Paste files | ❌ fragmented               |
| Zip archive | ❌ opaque                   |
| `mdrepoatlas`      | ✅ structured understanding |

---

## Roadmap

Planned improvements:

* pip installable CLI
* gitignore parsing
* incremental snapshots
* diff snapshots
* multi-document mode
* token estimation
* IDE integration
* local LLM pipeline support

---

## Contributing

PRs welcome.

Good areas:

* language detection
* ordering heuristics
* performance
* additional exclusions
* LLM workflow research

---

## License

MIT License.

---

## Author

Created to bridge software engineering and AI reasoning workflows.


---

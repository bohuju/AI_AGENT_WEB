# OSS-Fuzz Harness Generation Toolkit

The **Harness Generation Toolkit** automates the entire workflow of adding
new *libFuzzer* harnesses to existing [OSS-Fuzz] projects, executing the
resulting fuzzers and producing polished vulnerability reports when crashes
are identified.

---

## Contents

```
harness-generator/
├── batch_generate.py          # batch driver (multiple targets)
├── src/                       # Python package with core logic
│   ├── codex_helper.py        # Codex CLI wrapper (sentinel + retry logic)
│   └── harness_generator.py   # single-project orchestrator
└── scripts/                   # triage & reporting utilities
    ├── sort_jobs.py           # classify jobs → ./sorted/[buckets]
    ├── summarize.py           # Markdown summary of findings (no LLM usage)
    ├── generate_reports.py    # create disclosure-style bug_report.md
    └── gather_reports.py      # collect final artifacts into one folder
└── yamls/                     # sample target lists consumed by batch_generate.py
```

---

## 1. Core Workflow Overview

For **day-to-day usage** you will typically launch *batch_generate.py* – it
drives the end-to-end process and drops every run into `./jobs/`.

```bash
# Example: fuzz 40 C projects, eight rounds each, using 32 Codex workers
python batch_generate.py --targets ./yamls/c-projects.yaml \
                       --threads 32 --rounds 8
```

Behind the scenes *batch_generate.py* clones the target repository, prunes
unrelated project folders under `oss-fuzz/projects/`, then invokes
`harness_generator.py` one or more times (**rounds**) for that project.  All
stdout/stderr is tee’d to `harness_round_<n>.log` so nothing is lost if the
main process is interrupted.

`harness_generator.py` itself encapsulates the following high-level steps:

1. **Baseline build** – build the project’s Docker image & existing fuzzers
   (via `infra/helper.py`) to record the current binary set.
2. **Archive extraction** – unpack any source bundles (tar/zip) so Codex can
   edit the real files.
3. **Harness creation** – Codex is instructed to add one new
   `LLVMFuzzerTestOneInput` harness and adjust build scripts accordingly.
4. **Re-package archives** – re-create any bundles touched by Codex.
5. **Rebuild with retries** – rebuild image & fuzzers; compiler errors are
   forwarded to Codex for minimal fixes (configurable retry count).
6. **Seed corpus** – before each *new* fuzzer is executed, Codex populates a
   seed corpus directory with meaningful inputs.
7. **Fuzzer execution** – every new fuzzer is run; crash / OOM / timeout
   artifacts are detected and logged.
8. **Crash analysis** – the first crash is reproduced; the harness source,
   reproducer log and hexdump are combined into *crash_info.md*.
   Codex then writes *crash_analysis.md* explaining root cause, impact and
   patch guidance.  Finally a `crash_reproducer.sh` PoC script is authored.

All Codex interactions are handled by **CodexHelper**.  It runs the Codex CLI
in a pseudo-terminal, watches for a sentinel file (`./done`), retries on
transient errors, and only returns once a *git diff* confirms that edits were
made.

### Running a single project

```bash
python -m src.harness_generator <project> <path/to/oss-fuzz/checkout> <key.env> \
       --sanitizer address --codex-cli codex --max-retries 3
```
---

## 2. Batch Generation

`batch_generate.py` reads a YAML file whose `projects:` list describes
multiple targets (name + fuzz-tooling repo URL + git ref).  For every entry
it clones the repository into **./jobs/**`<project>_<uuid>` and invokes
`harness_generator.py` *n* times ("rounds").  All stdout/stderr is tee’d to
`harness_round_<n>.log` inside the job directory.

The default output tree therefore looks like:

```
jobs/
    libpng_16f7f21a/
        crash_analysis.md
        crash_info.md
        ...
    freetype2_51c9ea11/
        ...
```

---

## 3. Triage & Reporting Utilities (scripts/)

| Script | Purpose |
|--------|---------|
| **sort_jobs.py** | Move each job directory from `./jobs` into `./sorted/<bucket>`:<br>• `crashes/` – real crash files present, *no* `HARNESS ERROR` marker.<br>• `false_positives/` – `HARNESS ERROR` appears in *crash_analysis.md*.<br>• `no_crashes/` – no crash/oom/timeout produced. |
| **generate_reports.py** | For every job that has *crash_info.md* **and** *crash_analysis.md*, ask Codex to create a polished `bug_report.md` following the embedded disclosure template. |
| **gather_reports.py** | Copy `{crash_info,crash_analysis,bug_report}.md` (+ optional PoC scripts) for each job into a flat structure under `./sorted/reports/` for easy export. |
| **summarize.py** | Build a Markdown overview of all jobs (counts, per-project sections embedding analysis & info). |

All helper CLIs expose `--help` with full documentation; defaults are chosen
so running them in order without arguments *just works*:

```
# 1. Sort raw jobs into buckets
python scripts/sort_jobs.py

# 2. Generate bug_report.md for each real crash
python scripts/generate_reports.py --input ./sorted/crashes

# 3. Collect artifacts for disclosure upload
python scripts/gather_reports.py --input ./sorted/crashes --output ./sorted/reports

# 4. Produce a human-readable summary
python scripts/summarize.py --input ./sorted/crashes > triage_summary.md
```

---

## 4. Installation & Requirements

1. **Provide an API key** – either export it directly:

   ```bash
   export OPENAI_API_KEY="sk-your-key"
   ```

   or create a `.env` file (anywhere) with

   ```ini
   OPENAI_API_KEY=sk-your-key
   ```

   and pass the path via `--ai-key-path`.

2. **System packages** – Docker, git, clang/llvm, etc. as required by
   OSS-Fuzz’s `infra/helper.py` build process.


### Codex CLI

The repository relies on the **Codex CLI**.  `setup-env.sh` will detect its
absence and offer to build & install it automatically (requires `go` and
`sudo`).  If you prefer manual installation:

```bash
npm install -g @openai/codex
```

### Python environment

1. Create & activate a virtual environment (recommended):

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. Install the required Python packages:

   ```bash
   pip install -r requirements.txt
   ```

   The toolkit depends on only three third-party libraries – *GitPython*,
   *PyYAML* and *python-dotenv*. They are listed in **requirements.txt** so
   the above command resolves everything in one go.

   **Note:** The codebase uses modern type-hinting features introduced in
   Python 3.9 – please make sure you run it on Python ≥ 3.9.

3. Ensure the **git** command-line tool itself is present.  Several modules
   shell out to `git` for repository operations; missing it will result in
   runtime errors such as `FileNotFoundError: [Errno 2] No such file or directory: 'git'`.

Other prerequisites
-------------------

* Docker + OSS-Fuzz build dependencies
* Codex CLI in `$PATH` (or specify via `--codex-cli`)
* OpenAI-compatible API key (environment variable **OPENAI_API_KEY** or a
  path passed with `--ai-key-path`)

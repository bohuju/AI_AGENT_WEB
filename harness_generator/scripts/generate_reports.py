#!/usr/bin/env python3

#────────────
#
# Copyright 2025 Artificial Intelligence Cyber Challenge
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of 
# this software and associated documentation files (the “Software”), to deal in the 
# Software without restriction, including without limitation the rights to use, 
# copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the 
# Software, and to permit persons to whom the Software is furnished to do so, 
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all 
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, 
# INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A 
# PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT 
# HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION 
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE 
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#
# ────────────

"""
generate_reports.py
────────────
For every harness run directory that contains both `crash_analysis.md` and
`crash_info.md`, invoke the Codex CLI to author a polished disclosure-style
`bug_report.md` using the template supplied by the user.

The script mirrors the Codex interaction pattern used in `harness_generator.py`:

1. Aggregate the contents of `crash_analysis.md` and `crash_info.md` into a
   single context blob that is provided to Codex.
2. Send high-level instructions asking Codex to create **exactly one new file
   called `bug_report.md`** in the same directory, following the required
   section layout verbatim (see `REPORT_TEMPLATE` below).
3. Repeat for every qualifying run directory found under the *input* root
   (default: `./jobs`).

Like the other tooling, the script expects an OpenAI-compatible API key via
`OPENAI_API_KEY` **or** a path to a dotenv file containing it.
"""

from __future__ import annotations

import argparse
import os
import textwrap
from pathlib import Path
from typing import List

from dotenv import load_dotenv

load_dotenv(dotenv_path="./.env")

# Re-use the Codex helper that is already part of this repository
# Add src/ to import path then import CodexHelper
import sys
from pathlib import Path as _Path

_REPO_ROOT = _Path(__file__).resolve().parent.parent
_SRC_DIR = _REPO_ROOT / "src"
sys.path.insert(0, str(_SRC_DIR))

from codex_helper import CodexHelper  # type: ignore


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_INPUT_ROOT = Path("./jobs")

CODEX_ANALYSIS_MODEL = os.environ.get("CODEX_ANALYSIS_MODEL", "o4-mini")
CODEX_APPROVAL_MODE = os.environ.get("CODEX_APPROVAL_MODE", "full-auto")


REPORT_TEMPLATE = textwrap.dedent(
    r"""
    # <Project / Component> – <Short Bug Title>
    _Disclosure date: <YYYY-MM-DD>_ (use the current date)

    ---

    ## 1 Overview
    Brief, one-sentence statement of the flaw and why it matters.

    ## 2 Affected product(s) and version(s)
    * <Product 1> - <version / git SHA / build> (check the git history and use origin/HEAD)

    ## 3 Impact
    Describe what an attacker can do (RCE, DoS, info-leak, privilege escalation, etc.).  
    _Add CVSS v3.1 vector & score here if you have one._

    ## 4 Technical details
    1. **Root cause** – where in the code / design the issue lives.  
    2. **Trigger** – how malformed input or an attacker’s action reaches that code path (include the harness and the crashing input)
    3. **Why it fails safely/unsafely** – memory corruption, missing auth check, etc.  
    4. **Reproduction** – step-by-step commands or minimal PoC (link to file if large).

    ## 5 Mitigation / Patch guidance
    * Short-term workaround (e.g., config flag, WAF rule).  
    * Long-term fix suggestion (code change, input validation, size check).

    ## 6 Timeline
    | Date | Event |
    |------|-------|
    | YYYY-MM-DD | Vulnerability discovered | (use the date when crash_info.md was created)

    ## 7 Credits
    _Reported by SHERPA_

    ## 8 References
    * ISO/IEC 29147 section 5.4 (Disclosure contents)  
    * CERT/CC Vulnerability Note style  
    * CVE entry (reserved) – CVE-YYYY-NNNN
    """
).strip()


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _find_run_dirs(root: Path) -> List[Path]:
    """Return run-directory candidates located one **or two** levels below *root*.

    The original implementation only considered the immediate children of
    *root*::

        output/<target>/crash_analysis.md

    Newer triage layouts introduce an additional *category* layer so the
    structure now looks like::

        output/<category>/<target>/crash_analysis.md

    To stay backward-compatible while supporting the new layout the function
    operates in two steps:

    1.  Collect every *direct* sub-directory of *root*.
    2.  For each direct child that itself is **not** a run directory (i.e.
        lacks the required crash documents), collect its own sub-directories.

    The resulting list therefore contains

        • output/<target>
        • output/<category>/<target>

    leaving the subsequent `_has_crash_docs` filter to decide which candidates
    actually qualify as run directories.
    """

    run_dirs: List[Path] = []

    # First pass — look at immediate children of *root*.
    for first_level in root.iterdir():
        if not first_level.is_dir():
            continue

        if _has_crash_docs(first_level):
            # Classic layout: the run directory sits directly under *root*.
            run_dirs.append(first_level)
            continue

        # Second pass — treat *first_level* as a category and inspect its
        # direct sub-directories.  We intentionally do **not** recurse further
        # to avoid unexpectedly deep walks while still covering the new
        # two-level layout.
        for candidate in first_level.iterdir():
            if candidate.is_dir() and _has_crash_docs(candidate):
                run_dirs.append(candidate)

    return run_dirs


def _has_crash_docs(run_dir: Path) -> bool:
    return (run_dir / "crash_analysis.md").is_file() and (
        run_dir / "crash_info.md"
    ).is_file()


# ---------------------------------------------------------------------------
# Codex interaction per run directory
# ---------------------------------------------------------------------------


def _invoke_codex_for_report(
    run_dir: Path, codex_cli: str, ai_key_path: Path
) -> None:
    """Ask Codex to create *bug_report.md* inside *run_dir*."""

    analysis_path = run_dir / "crash_analysis.md"
    info_path = run_dir / "crash_info.md"

    # Combine the two markdown files into one context blob
    context_blob = textwrap.dedent(
        """
        === crash_analysis.md ===
        {analysis}

        === crash_info.md ===
        {info}
        """
    ).format(
        analysis=analysis_path.read_text(encoding="utf-8", errors="replace"),
        info=info_path.read_text(encoding="utf-8", errors="replace"),
    )

    instructions = textwrap.dedent(
        f"""
        You are an experienced vulnerability disclosure author.

        Using the *context* provided (crash analysis and crash info), write a
        **new file** called `bug_report.md` inside the same directory.

        If `bug_report.md` already exists, just create the ./done file and exit.

        Requirements:
        • Follow the exact section headings and formatting shown below.
        • Where possible, extract details from the analysis/info; otherwise
          leave concise TODO placeholders for a human analyst.
        • Do **not** modify existing files.

        Important: If the bug is caused by a harness error (bad library usage, wrong params, etc.)
          then you must put "HARNESS ERROR" somewhere in your report to flag this false positive.

        ---
        BEGIN TEMPLATE (copy verbatim, then fill)
        {REPORT_TEMPLATE}
        END TEMPLATE
        """
    ).strip()

    patcher = CodexHelper(
        repo_path=run_dir,
        ai_key_path=str(ai_key_path),
        copy_repo=False,
        codex_cli=codex_cli,
        codex_model=CODEX_ANALYSIS_MODEL,
        approval_mode=CODEX_APPROVAL_MODE,
    )

    stdout = patcher.run_codex_command(
        instructions, additional_context=context_blob
    )

    if stdout is None:
        print(f"[!] Codex did not create bug_report.md in {run_dir}")
    else:
        print(f"✓ bug_report.md generated for {run_dir}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate bug_report.md for each crash-containing run directory via Codex.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT_ROOT,
        help="Root directory containing harness run directories (default: ./jobs)",
    )
    parser.add_argument(
        "--ai-key-path",
        type=Path,
        default=Path("./.env"),
        help="Path to .env file holding your OPENAI-compatible API key.",
    )
    parser.add_argument(
        "--codex-cli",
        default="codex",
        help="Codex CLI executable path (default: codex)",
    )

    parser.add_argument(
        "--threads",
        type=int,
        default=1,
        help="Maximum number of parallel Codex report generations.",
    )

    args = parser.parse_args()

    # Ensure API key is exported exactly like harness_generator does
    load_dotenv(dotenv_path=os.path.expanduser(str(args.ai_key_path)))

    root = args.input.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"Output directory not found: {root}")

    run_dirs = _find_run_dirs(root)

    if not run_dirs:
        print("[!] No run directories found – nothing to do.")
        return

    todo = [d for d in run_dirs if _has_crash_docs(d)]

    if not todo:
        print("[!] No crash_analysis.md found under", root)
        return

    print(
        f"[*] Found {len(todo)} run directorie(s) with crashes. Using up to {args.threads} thread(s)."
    )

    import concurrent.futures as _cf

    with _cf.ThreadPoolExecutor(max_workers=args.threads) as pool:
        futures = [
            pool.submit(
                _invoke_codex_for_report,
                run_dir,
                codex_cli=args.codex_cli,
                ai_key_path=args.ai_key_path.expanduser(),
            )
            for run_dir in todo
        ]

        # Wait for completion, surface exceptions early
        for f in _cf.as_completed(futures):
            try:
                f.result()
            except Exception as exc:
                print(f"[!] Worker raised exception: {exc}")


if __name__ == "__main__":
    main()

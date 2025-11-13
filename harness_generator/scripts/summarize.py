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
summarize.py
────────────

Generate a Markdown overview of Codex harness runs stored in an *output*
directory (default: **./jobs**).

The report contains:

• Total run directories processed and count of *unique* OSS-Fuzz projects.  
• Counts of run directories that include `crash_analysis.md`, `crash_info.md`,
  and those flagged as **false positives** (i.e. `crash_analysis.md` contains
  the string *“HARNESS ERROR”*).  
• **Only** projects whose crashes are **not** false positives get a section
  embedding:
    - Full path to every *real* crashing run directory.  
    - Contents of `crash_analysis.md` and `crash_info.md`.

False-positive runs are tallied but *omitted* from the detailed sections.

Usage examples
--------------
    # Print report to stdout
    ./summarize.py

    # Custom output root and write to file
    ./summarize.py --output /tmp/my_runs --report triage_summary.md
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Dict, List, Tuple


# ────────────────────────── helpers ──────────────────────────


def _project_name(run_dir: Path) -> str:
    """Best-effort project name inference from directory layout."""
    build_out = run_dir / "build" / "out"
    if build_out.is_dir():
        subs = [d.name for d in build_out.iterdir() if d.is_dir()]
        if len(subs) == 1:
            return subs[0]
    parts = run_dir.name.rsplit("_", 1)
    return parts[0] if len(parts) == 2 else run_dir.name


def _safe_code(text: str) -> str:
    """Prevent premature closing of code fences in embedded markdown."""
    return text.replace("```", "```​")


def _is_false_positive(analysis_path: Path) -> bool:
    """Return True if crash_analysis.md contains 'HARNESS ERROR' (case-insensitive)."""
    if not analysis_path.is_file():
        return False
    return bool(re.search(r"harness\s+error", analysis_path.read_text(errors="ignore"), re.I))


# ───────────────────────── summariser ─────────────────────────


def build_summary(output_root: Path) -> str:
    run_dirs = [d for d in output_root.iterdir() if d.is_dir()]

    total_runs = len(run_dirs)
    unique_projects = {_project_name(d) for d in run_dirs}

    info_total = 0
    analysis_total = 0
    fp_total = 0  # false positives

    # Stores (run_dir, is_false_positive)
    project_runs: Dict[str, List[Tuple[Path, bool]]] = {}

    for run_dir in run_dirs:
        analysis_path = run_dir / "crash_analysis.md"
        info_path = run_dir / "crash_info.md"

        has_info = info_path.is_file()
        has_analysis = analysis_path.is_file()
        is_fp = _is_false_positive(analysis_path)

        if has_info or has_analysis:
            proj = _project_name(run_dir)
            project_runs.setdefault(proj, []).append((run_dir, is_fp))

        if has_info:
            info_total += 1
        if has_analysis:
            analysis_total += 1
        if is_fp:
            fp_total += 1

    # ───────────────────── build markdown ─────────────────────
    md_lines: List[str] = [
        "# Codex Harness Run Summary",
        f"Scan directory: {output_root}",
        "",
        "## Totals",
        f"- Run directories scanned: {total_runs}",
        f"- Unique projects: {len(unique_projects)}",
        f"- Directories with crash_analysis.md: {analysis_total}",
        f"- Directories with crash_info.md:    {info_total}",
        f"- **False positives (HARNESS ERROR): {fp_total}**",
        "",
    ]

    # Only include detailed sections for *real* crashes
    real_project_sections_written = False

    for proj, runs in sorted(project_runs.items()):
        # Filter out false-positive runs
        real_runs = [r for r, is_fp in runs if not is_fp]
        if not real_runs:
            continue  # nothing real to show for this project

        real_project_sections_written = True
        md_lines.extend([f"## {proj}", ""])

        for run_dir in real_runs:
            md_lines.append(f"### {run_dir}")

            # ---- Crash Analysis --------------------------------------
            analysis_path = run_dir / "crash_analysis.md"
            if analysis_path.is_file():
                md_lines.extend(
                    [
                        "#### Crash Analysis",
                        "```markdown",
                        _safe_code(
                            analysis_path.read_text(
                                encoding="utf-8", errors="replace"
                            )
                        ),
                        "```",
                        "",
                    ]
                )

            # ---- Crash Info -----------------------------------------
            info_path = run_dir / "crash_info.md"
            if info_path.is_file():
                md_lines.extend(
                    [
                        "#### Crash Info",
                        "```markdown",
                        _safe_code(
                            info_path.read_text(
                                encoding="utf-8", errors="replace"
                            )
                        ),
                        "```",
                        "",
                    ]
                )

    if not real_project_sections_written:
        md_lines.append("_All detected crashes are marked as false positives._\n")

    return "\n".join(md_lines).rstrip() + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Produce a Markdown summary of harness run results.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument(
        "--input",
        type=Path,
        default=Path("./jobs"),
        help="Root directory containing harness run directories.",
        dest="jobs",
    )
    ap.add_argument(
        "--report",
        type=Path,
        help="Write report to this file instead of stdout.",
    )
    args = ap.parse_args()

    root = args.jobs.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"Jobs directory not found: {root}")

    md_doc = build_summary(root)

    if args.report:
        args.report.write_text(md_doc, encoding="utf-8")
        print(f"✓ Summary written to {args.report}")
    else:
        print(md_doc)


if __name__ == "__main__":
    main()

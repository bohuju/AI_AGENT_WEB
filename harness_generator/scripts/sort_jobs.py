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
sort_jobs.py
────────────
Classify and move harness-run job directories into three buckets:

    • crashes          - at least one crash/OOM/timeout file produced **and**
                        the crash *does not* appear to be harness-induced.
    • false_positives  - crash_analysis.md contains the marker *"HARNESS ERROR"*.
    • no_crashes       - build/out/** contains no crash, oom or timeout files.

The script replaces the previous trio of helper utilities
(*sort_crashes.py*, *sort_false_positives.py*, *sort_non_crashing.py*) with a
single, more ergonomic command.

Usage examples
──────────────
    # Use defaults (./jobs  →  ./sorted)
    ./sort_jobs.py

    # Custom locations
    ./sort_jobs.py --input batch_runs --output triaged

Directory layout
────────────────
All job directories directly under *input* are inspected.  They are **moved**
to one of the following sub-directories inside *output* (created if absent):

    sorted/
        crashes/
        false_positives/
        no_crashes/

If a target directory already exists a numeric suffix ("_1", "_2", …) is
automatically appended to avoid overwriting previous runs.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Iterable, List, Tuple


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _unique_dest(dest_root: Path, name: str) -> Path:
    """Return a unique destination path inside *dest_root* (adds _N if needed)."""

    candidate = dest_root / name
    idx = 1
    while candidate.exists():
        candidate = dest_root / f"{name}_{idx}"
        idx += 1
    return candidate


def _list_matching(root: Path, prefixes: Iterable[str]) -> List[Path]:
    """Return immediate children of *root* whose names start with any prefix."""

    return [
        p
        for p in root.glob("*")
        if p.is_file() and any(p.name.startswith(pre) for pre in prefixes)
    ]


def _detect_bug_files(run_dir: Path) -> bool:
    """Return *True* if the run directory contains any crash/oom/timeout files."""

    build_out_root = run_dir / "build" / "out"

    # There should be exactly one project sub-directory under build/out/<project>
    subdirs = (
        [d for d in build_out_root.iterdir() if d.is_dir()]
        if build_out_root.is_dir()
        else []
    )

    if len(subdirs) != 1:
        return False

    project_out = subdirs[0]
    bug_files = _list_matching(project_out, ("crash", "oom", "timeout"))
    return bool(bug_files)


def _has_harness_error(run_dir: Path) -> bool:
    """Return *True* if crash_analysis.md mentions a harness error marker."""

    analysis = run_dir / "crash_analysis.md"
    if not analysis.is_file():
        return False

    try:
        content = analysis.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False

    return "harness error" in content.lower()


def classify(run_dir: Path) -> str:
    """Return the classification label for *run_dir* (crashes/false_positives/no_crashes)."""

    # False positives have crash docs *and* the harness error marker.
    if _has_harness_error(run_dir):
        return "false_positives"

    if _detect_bug_files(run_dir):
        return "crashes"

    return "no_crashes"


def sort_jobs(src_root: Path, dst_root: Path) -> Tuple[int, int, int]:
    """Move job directories from *src_root* into bucketed sub-directories under *dst_root*.

    Returns a tuple (crashes, false_positives, no_crashes) with the number of
    directories moved into each bucket.
    """

    if not src_root.is_dir():
        sys.exit(f"Input directory not found: {src_root}")

    # Ensure bucket directories exist.
    crashes_dir = dst_root / "crashes"
    fp_dir = dst_root / "false_positives"
    nc_dir = dst_root / "no_crashes"

    for d in (crashes_dir, fp_dir, nc_dir):
        d.mkdir(parents=True, exist_ok=True)

    counts = {"crashes": 0, "false_positives": 0, "no_crashes": 0}

    for run_dir in sorted(src_root.iterdir()):
        if not run_dir.is_dir():
            continue

        label = classify(run_dir)

        dest_root = {
            "crashes": crashes_dir,
            "false_positives": fp_dir,
            "no_crashes": nc_dir,
        }[label]

        dest = _unique_dest(dest_root, run_dir.name)
        print(f"[+] {run_dir.name}  →  {label}/{dest.name}")
        shutil.move(str(run_dir), dest)
        counts[label] += 1

    return counts["crashes"], counts["false_positives"], counts["no_crashes"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Sort job run directories into crashes/false_positives/no_crashes.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    ap.add_argument(
        "--input",
        type=Path,
        default=Path("./jobs"),
        help="Directory produced by batch_generate.py (default: ./jobs)",
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=Path("./sorted"),
        help="Destination root (buckets will be created here).",
    )

    args = ap.parse_args()

    src = args.input.resolve()
    dst = args.output.resolve()

    crashes, fps, ncs = sort_jobs(src, dst)

    print(
        f"\nFinished.  Crashes: {crashes},  False-positives: {fps},  No-crash: {ncs}."
    )


if __name__ == "__main__":
    main()

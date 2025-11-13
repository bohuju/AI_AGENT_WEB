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
gather_reports.py
────────────
Gather bug-report artifacts from a triage directory.

Usage:
    ./gather_reports [<triage_dir>] [--output-dir <path>]

By default the script scans ./triage for job folders that follow the pattern
"<project>_<uuid>".  If a job folder (anywhere in its subtree) contains the
three markdown files *crash_analysis.md*, *crash_info.md*, and
*bug_report.md*, they are copied into a new directory named after the **uuid**
under ./bug_reports.

The output directory will now contain sub-directories with the same
names as the corresponding job folders found in *triage_dir* (for example
``apache-httpd_1234abcd``), preserving the project name instead of keeping
only the raw UUID.

Directory layout variants
------------------------
Historically, *triage_dir* contained only the per-job folders themselves::

    triage/
        <project>_<uuid>/

With the introduction of *categories* an additional level may be present::

    triage/
        <category>/
            <project>_<uuid>/

`gather_reports` now transparently handles **both** layouts by examining the
immediate children of *triage_dir* **and**, if they are not job directories,
their own direct sub-directories.

If category directories are detected the script replicates this structure
under the output directory so that artifacts remain grouped::

    triage/
        asan/
            project_1111aaaa/

    → bug_reports/
        asan/
            project_1111aaaa/

When a job directory contains a *poc.sh* or *poc.py* file, it is copied along
with the three required markdown files.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# These files must be present for a job directory to be considered complete.
REQUIRED_FILES = {
    "crash_analysis.md",
    "crash_info.md",
    "bug_report.md",
}

# Optional proof-of-concept files that are copied alongside the required
# markdown artifacts when present.  Only the **first** occurrence of each file
# name within a job directory is taken into account.

OPTIONAL_POC_FILES = [
    "poc.sh",
    "poc.py",
]


def extract_uuid(job_dir_name: str) -> str | None:
    """Return the substring after the final underscore in *job_dir_name*.

    Example::

        >>> extract_uuid('apache-httpd_1234abcd')
        '1234abcd'
    """

    if "_" not in job_dir_name:
        return None

    return job_dir_name.split("_")[-1]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _find_first(root: Path, filename: str) -> Path | None:
    """Return the **first** occurrence of *filename* under *root* or *None*."""

    try:
        return next(root.rglob(filename))
    except StopIteration:
        return None


def find_required_files(root: Path) -> dict[str, Path] | None:
    """Search *root* recursively for all REQUIRED_FILES.

    Returns a mapping *filename → Path* for the first occurrence of every
    required file or *None* if any file is missing.
    """

    found: dict[str, Path] = {}

    for name in REQUIRED_FILES:
        path = _find_first(root, name)
        if path is None:
            return None
        found[name] = path

    return found


def gather_reports(triage_dir: Path, output_dir: Path) -> None:
    """Populate *output_dir* with consolidated bug-report artifacts."""

    if not triage_dir.is_dir():
        sys.exit(f"Error: '{triage_dir}' is not a directory")

    output_dir.mkdir(exist_ok=True)

    def _process_job_dir(job_dir: Path, *, category: str | None = None) -> bool:
        """Copy artifacts from *job_dir* to *output_dir*.

        Returns True if the directory was handled successfully, False
        otherwise (e.g. not a job dir or missing files).
        """

        if not job_dir.is_dir():
            return False

        uuid = extract_uuid(job_dir.name)
        if uuid is None:
            return False  # not a job directory

        artifacts = find_required_files(job_dir)
        if artifacts is None:
            return False  # incomplete job – skip

        # Preserve categories in the output directory if requested.
        dest = output_dir / job_dir.name if category is None else output_dir / category / job_dir.name

        if dest.exists():
            print(
                f"[!] Destination '{dest}' already exists – skipping duplicate job from '{job_dir.name}'",
                file=sys.stderr,
            )
            return True  # already processed, treat as handled to avoid deeper fallback

        dest.mkdir(parents=True)


        for name, src in artifacts.items():
            shutil.copy2(src, dest / name)

        # Copy optional PoC files if they exist.
        for poc_name in OPTIONAL_POC_FILES:
            poc_path = _find_first(job_dir, poc_name)
            if poc_path is not None:
                shutil.copy2(poc_path, dest / poc_name)

        print(f"[+] Collected reports for job '{job_dir.name}' → '{dest}'")
        return True

    # Iterate over immediate children; if a child isn't processed try its sub-dirs.
    for child in triage_dir.iterdir():
        if not child.is_dir():
            continue

        handled = _process_job_dir(child)
        if handled:
            continue

        # Treat *child* as category and look one level deeper.
        for grandchild in child.iterdir():
            _process_job_dir(grandchild, category=child.name)


def main(argv: list[str] | None = None) -> None:  # noqa: D401
    parser = argparse.ArgumentParser(
        description=(
            "Collect crash_analysis.md, crash_info.md and bug_report.md files "
            "from each job directory under --input and copy them into "
            "--output (default ./sorted/reports) preserving job folder names."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--input",
        default="./jobs",
        help="Root directory containing job run folders.",
    )
    parser.add_argument(
        "--output",
        default="./sorted/reports",
        help="Destination where consolidated reports will be written.",
    )

    args = parser.parse_args(argv)

    gather_reports(
        Path(args.input).expanduser().resolve(),
        Path(args.output).expanduser().resolve(),
    )


if __name__ == "__main__":
    main()

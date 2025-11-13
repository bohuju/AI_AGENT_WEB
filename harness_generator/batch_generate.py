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
batch_generate.py
─────────────────

Orchestrates batch execution of `harness_generator.py` against multiple
OSS-Fuzz projects.

• Consumes a YAML file whose top-level `projects` list describes the project
  name, git URL and commit/reference of each fuzz-tooling repository.
• For every entry it clones the repository into an *output* directory,
  then invokes HarnessGenerator one or more times ("rounds").
• All stdout/stderr from each invocation is tee'd to
  `harness_round_<n>.log` inside that project's run directory so logs are
  preserved even if the main process is interrupted.

Most HarnessGenerator CLI flags are surfaced so the batch driver can choose
sanitiser, Codex binary, scratch space, etc.  Work is done sequentially and
any clone/build failure simply skips the affected target, keeping the batch
run going.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import logging
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Dict, List

import yaml
from dotenv import load_dotenv
from git import Repo, exc as git_exc

# ---------------------------------------------------------------------------#
# Constants & global state
# ---------------------------------------------------------------------------#
# Default location for all job run directories created by this batch driver.
# The original internal tooling wrote to an NFS mount; we switch to a local
# folder so the released version works out-of-the-box.

OUTPUT_ROOT = Path("./jobs").resolve()
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------#
# YAML helper
# ---------------------------------------------------------------------------#


def load_targets_yaml(path: Path) -> list[dict[str, str]]:
    """Return the list under `projects:` from a YAML file."""
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not data or "projects" not in data:
        raise ValueError("YAML must contain a top-level 'projects' list")
    return data["projects"]


# ---------------------------------------------------------------------------#
# Git helper
# ---------------------------------------------------------------------------#


def clone_and_checkout(url: str, ref: str, dest: Path) -> Repo:
    logging.info("Cloning %s → %s", url, dest)
    repo = Repo.clone_from(url, dest)
    try:
        repo.git.checkout(ref)
    except git_exc.GitCommandError:
        repo.git.fetch("origin", ref)
        repo.git.checkout("FETCH_HEAD")
    logging.info("Checked-out commit %s", repo.head.commit.hexsha)
    return repo


# ---------------------------------------------------------------------------#
# Worker function
# ---------------------------------------------------------------------------#


def process_project(
    target: Dict[str, str],
    *,
    rounds: int,
    ai_key_path: Path,
    sanitizer: str,
    codex_cli: str,
    scratch_space: Path | None,
    copy_repo: bool,
    no_build: bool,
    smoke: bool,
    max_retries: int,
) -> None:
    """
    Clone the project and run HarnessGenerator `rounds` times in sequence.
    All stdout/stderr from each round is captured to a file.
    """
    project = target["project_name"]
    url = target["fuzz_tooling_url"]
    ref = target["fuzz_tooling_ref"]

    run_dir = OUTPUT_ROOT / f"{project}_{uuid.uuid4().hex}"
    run_dir.mkdir(parents=True, exist_ok=True)

    try:
        clone_and_checkout(url, ref, run_dir)
    except Exception as err:
        logging.error("[SKIP] %s - clone/checkout failed: %s", project, err)
        return

    # ── Keep only the target project directory under oss-fuzz/projects/ ──
    projects_root = run_dir / "projects"
    if projects_root.is_dir():
        for sub in projects_root.iterdir():
            if sub.is_dir() and sub.name != project:
                try:
                    shutil.rmtree(sub)
                except Exception as exc:
                    logging.warning(
                        "[%s] Failed to remove directory %s: %s",
                        project,
                        sub,
                        exc,
                    )

    script_path = Path(__file__).parent / "src" / "harness_generator.py"

    for round_idx in range(1, rounds + 1):
        log_path = run_dir / f"harness_round_{round_idx}.log"
        logging.info(
            "[%s] Round %d/%d → %s", project, round_idx, rounds, log_path
        )

        cmd = [
            sys.executable,
            str(script_path),
            project,
            str(run_dir),
            str(ai_key_path),
            "--sanitizer",
            sanitizer,
            "--codex-cli",
            codex_cli,
            "--max-retries",
            str(max_retries),
        ]

        if scratch_space:
            cmd += ["--scratch-space", str(scratch_space)]
        if copy_repo:
            cmd.append("--copy-repo")
        if no_build:
            cmd.append("--no-build")
        if smoke:
            cmd.append("--smoke")

        # Capture combined stdout/stderr into the log file
        with open(log_path, "w", encoding="utf-8") as lf:
            proc = subprocess.run(
                cmd,
                stdout=lf,
                stderr=subprocess.STDOUT,
                text=True,
            )
        if proc.returncode != 0:
            logging.error(
                "[%s] Round %d failed (rc=%d). " "See %s for details.",
                project,
                round_idx,
                proc.returncode,
                log_path,
            )

    logging.info("[%s] All rounds complete → %s", project, run_dir)


# ---------------------------------------------------------------------------#
# Main
# ---------------------------------------------------------------------------#


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch-generate OSS-Fuzz harnesses concurrently.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--targets",
        type=Path,
        required=True,
        help="YAML file listing projects to process.",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=1,
        help="Successive Codex rounds per project.",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=1,
        help="Maximum number of concurrent HarnessGenerator runs.",
    )
    parser.add_argument(
        "--ai-key-path",
        type=Path,
        default="./.env",
        help="Path to .env or file containing OPENAI key.",
    )
    parser.add_argument(
        "--sanitizer",
        default="address",
        help="Sanitizer to use when building fuzzers.",
    )
    parser.add_argument(
        "--codex-cli", default="codex", help="Codex CLI executable."
    )
    parser.add_argument(
        "--scratch-space",
        type=Path,
        help="Directory for HarnessGenerator temp copies.",
    )
    parser.add_argument(
        "--copy-repo",
        action="store_true",
        help="Tell HarnessGenerator to copy the repo before edits.",
    )
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="Skip docker rebuild step (debug only).",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Run a quick smoke test before Codex edits.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Max build-fix rounds inside HarnessGenerator.",
    )
    parser.add_argument(
        "--randomize",
        action="store_true",
        help="Randomize the order of projects before processing.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=True,
        help="Enable DEBUG logging.",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[%(levelname)s] %(message)s",
    )

    load_dotenv(os.path.expanduser(str(args.ai_key_path)))

    targets: List[Dict[str, str]] = load_targets_yaml(args.targets)
    if args.randomize:
        import random

        random.shuffle(targets)
        logging.info("--randomize is set; target list shuffled.")

    logging.info("Loaded %d project(s) from %s", len(targets), args.targets)
    logging.info("Running with up to %d concurrent job(s)…", args.threads)

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=args.threads
    ) as pool:
        futures = [
            pool.submit(
                process_project,
                t,
                rounds=args.rounds,
                ai_key_path=args.ai_key_path.expanduser(),
                sanitizer=args.sanitizer,
                codex_cli=args.codex_cli,
                scratch_space=args.scratch_space,
                copy_repo=args.copy_repo,
                no_build=args.no_build,
                smoke=args.smoke,
                max_retries=args.max_retries,
            )
            for t in targets
        ]

        # wait for all tasks to finish, raising exceptions if any occurred
        for f in concurrent.futures.as_completed(futures):
            try:
                f.result()
            except Exception as exc:
                logging.error("Worker raised: %s", exc)

    logging.info("All work complete.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted - exiting.")

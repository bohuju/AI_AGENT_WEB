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
harness_generator.py
────────────────────

Automates the lifecycle of adding an extra libFuzzer harness to an
OSS-Fuzz project and running it end-to-end.  Operations are performed in a
working copy of the project's oss-fuzz directory and rely on the **Codex CLI**
for all code-writing tasks.

High-level flow
===============
1. Baseline build - compile the docker image and existing fuzzers to record
   the current binary set.
2. Extract archives - unpack any tar/zip bundles so the source can be edited
   directly.
3. Harness creation - ask Codex to write a new `LLVMFuzzerTestOneInput` and
   update build scripts.
4. Repack archives - re-create any bundles that were unpacked and edited.
5. Rebuild with retries - rebuild image & fuzzers; on compiler errors, send
   the diagnostics back to Codex for minimal fixes (configurable retries).
6. Seed corpus - before each new fuzzer is executed, instruct Codex to
   populate the corresponding corpus directory with one or more meaningful
   seed inputs, using the harness source as context.
7. Fuzzer execution - run every newly-built fuzzer, capture stdout/stderr and
   detect any crash / OOM / timeout artifacts.
8. Crash handling - for the first crash found:
      • reproduce it with `infra/helper.py reproduce` and write
        `crash_reproduction.log` (commented with the exact command);
      • gather the reproducer log, harness source and a hexdump of the input
        into `crash_info.md`;
      • pass the same context to Codex and request `crash_analysis.md` that
        summarises bug type, impact and patch guidance.

Command-line flags allow skipping the rebuild, running a smoke test first, or
changing the maximum fix-retry count.  The script requires Python ≥ 3.9,
GitPython, python-dotenv, the Codex CLI, Docker and a functional oss-fuzz
checkout.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import time
import uuid
from dotenv import load_dotenv
from git import Repo, exc as git_exc
from pathlib import Path
from typing import Dict, Sequence

# Make the helper discoverable whether this module is executed as a script
# or imported as part of the *src* package.
try:
    from .codex_helper import CodexHelper  # type: ignore
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path as _Path

    _SRC_DIR = _Path(__file__).resolve().parent
    sys.path.insert(0, str(_SRC_DIR))
    from codex_helper import CodexHelper  # type: ignore

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
DEFAULT_SANITIZER = "address"
MAX_BUILD_RETRIES = 3
CODEX_ANALYSIS_MODEL = os.environ.get("CODEX_ANALYSIS_MODEL", "o3")
CODEX_APPROVAL_MODE = os.environ.get("CODEX_APPROVAL_MODE", "full-auto")


class HarnessGeneratorError(RuntimeError):
    """Raised for any harness-generation failure."""


class HarnessGenerator:
    """Automate Codex-assisted creation of an additional OSS-Fuzz harness."""

    # ───────────────────────── INITIALIZATION ───────────────────────── #

    def __init__(
        self,
        project_name: str,
        oss_fuzz_path: Path,
        *,
        ai_key_path: str,
        sanitizer: str = DEFAULT_SANITIZER,
        codex_cli: str = "codex",
        scratch_space: Path | None = None,
        copy_repo: bool = False,
    ) -> None:
        # Basic fields
        self.project = project_name.strip()
        self.oss_fuzz_path = oss_fuzz_path.expanduser().resolve()
        self.ai_key_path = Path(ai_key_path).expanduser()
        self.sanitizer = sanitizer
        self.codex_cli = codex_cli
        self.scratch_space = scratch_space or Path("/tmp")
        self.copy_repo = copy_repo
        self.logger = logging.getLogger(__name__)

        if not self.oss_fuzz_path.is_dir():
            raise FileNotFoundError(
                f"OSS-Fuzz path not found: {self.oss_fuzz_path}"
            )

        # Optionally copy the oss-fuzz tree so Codex works on a throw-away copy
        self.repo_path = (
            self._copy_repo(self.oss_fuzz_path)
            if copy_repo
            else self.oss_fuzz_path
        )
        self.repo = self._ensure_git_repo(self.repo_path)

        print(f"[*] Ready (project={self.project}, repo={self.repo_path})")

        # Mapping of extracted_dir  →  original_archive_path
        self._archives: Dict[Path, Path] = {}

    # ───────────────────────── PUBLIC ENTRY-POINT ────────────────────── #
    def generate_harness(
        self,
        *,
        build: bool = True,
        run_smoke: bool = False,
        max_iterations: int = MAX_BUILD_RETRIES,
    ) -> None:
        """Run the full workflow end-to-end."""

        # 1. Baseline build (with automatic Codex-assisted fixes)
        print("[*] Building docker image and fuzzers...")
        self._build_with_retries(clean=True, max_iterations=1)

        baseline_fuzzers = self._list_fuzzer_binaries()
        print(
            f"[*] Baseline has {len(baseline_fuzzers)} fuzzer(s):\n{baseline_fuzzers}\n"
        )
        if run_smoke:
            self._run_any_fuzzer_once()

        # 2. Extract any archives
        print("[*] Extracting any project archives...")
        self._extract_archives()

        # 3. Ask Codex to add a harness
        print("[*] Running Codex to generate a new harness...")
        self._invoke_codex_for_harness()

        # 4. Re-pack archives (Codex may have edited files inside them)
        print("[*] Repackaging any project archives...")
        self._repack_archives()

        # 5. Rebuild after harness has been added (again with retries)
        if build:
            print("[*] Attempting image/fuzzer rebuild...")
            self._build_with_retries(clean=False, max_iterations=max_iterations)

        # 6. Detect which fuzzers are new and run them
        final_fuzzers = self._list_fuzzer_binaries()
        new_fuzzers = sorted(final_fuzzers - baseline_fuzzers)

        if not new_fuzzers:
            print("[!] No new fuzzer binaries detected after Codex run.")
            return

        print(
            f"[*] Detected {len(new_fuzzers)} new fuzzer(s): {', '.join(new_fuzzers)}"
        )
        for fuzzer in new_fuzzers:
            # ── Generate seed corpus files before running ──
            try:
                self._invoke_codex_to_generate_seeds(fuzzer)
            except HarnessGeneratorError as err:
                print(f"[!] Failed to generate seeds for {fuzzer}: {err}")

            print(f"[*] ➤ Running {fuzzer} …")
            time.sleep(5)
            try:
                # ── Record existing crash/timeout/oom files before run
                baseline_bug_files = self._find_bug_files()

                output = self._run_fuzzer(fuzzer)

                # ── Detect newly-generated bug files
                new_bug_files = self._find_bug_files() - baseline_bug_files
                if new_bug_files:
                    print(
                        f"[!] Detected {len(new_bug_files)} crash/oom/timeout file(s):"
                    )
                    for p in new_bug_files:
                        print(f"    • {p.relative_to(self.repo_path)}")

                    # Reproduce only the first file (additional files can be handled later)
                    bug_path = sorted(new_bug_files)[0]
                    try:
                        repro_log, repro_cmd = self._reproduce_crash(
                            fuzzer, bug_path
                        )
                        self._generate_bug_report(
                            fuzzer, bug_path, repro_log, repro_cmd
                        )
                    except HarnessGeneratorError as err:
                        print(
                            f"[!] Failed to reproduce or analyse crash: {err}"
                        )

            except HarnessGeneratorError as err:
                print(f"[!] {fuzzer} failed: {err}")

    # ───────────────────────── INTERNAL HELPERS ─────────────────────── #

    # ---- Git helpers -------------------------------------------------- #
    def _copy_repo(self, src: Path) -> Path:
        dst = Path(
            tempfile.mkdtemp(
                prefix="oss-fuzz-harness-", dir=str(self.scratch_space)
            )
        )
        shutil.copytree(src, dst, dirs_exist_ok=True)
        return dst

    def _ensure_git_repo(self, path: Path) -> Repo:
        try:
            repo = Repo(path)
        except git_exc.InvalidGitRepositoryError:
            repo = Repo.init(path)
        repo.git.add(A=True)
        try:
            repo.git.commit(m="Initial commit (baseline)", allow_empty=True)
        except git_exc.GitCommandError:
            pass
        return repo

    # ---- Build helpers ------------------------------------------------- #
    def _build_image_and_fuzzers(self, *, clean: bool) -> None:
        helper = self.repo_path / "infra" / "helper.py"
        if not helper.is_file():
            raise HarnessGeneratorError(
                "infra/helper.py not found - invalid checkout?"
            )

        env = os.environ.copy()
        env.setdefault("OSSFUZZ_SKIP_UNSHALLOW", "1")

        # Build image (auto-confirm y/n prompt)
        self._run_cmd(
            ["python3", str(helper), "build_image", self.project],
            cwd=self.repo_path,
            env=env,
            input="y\n",
        )

        # Build fuzzers
        cmd = [
            "python3",
            str(helper),
            "build_fuzzers",
            self.project,
            "--sanitizer",
            self.sanitizer,
        ]
        if clean:
            cmd.append("--clean")
        self._run_cmd(cmd, cwd=self.repo_path, env=env)

    # ---- Fuzzer discovery -------------------------------------------- #
    def _list_fuzzer_binaries(self) -> set[str]:
        """Return the names of all executable fuzzer binaries for this project."""
        out_dir = self.repo_path / "build" / "out" / self.project
        if not out_dir.is_dir():
            return set()
        return {
            p.name
            for p in out_dir.iterdir()
            if p.is_file()
            and os.access(p, os.X_OK)
            and not p.name.endswith(".dict")
        }

    # ---- Build with retries (Codex-assisted) ------------------------- #
    def _build_with_retries(
        self,
        *,
        clean: bool,
        max_iterations: int = MAX_BUILD_RETRIES,
    ) -> None:
        """Attempt to build image & fuzzers, asking Codex to fix failures.

        This consolidates the repeated logic used for both the initial
        baseline build **and** the post-harness rebuild.  On every failure we
        forward the compiler diagnostics to Codex, let it apply minimal
        patches, optionally re-package any modified archives, and then retry
        the build until it succeeds or *max_iterations* is reached.
        """

        for attempt in range(1, max_iterations + 1):
            try:
                # Only pass the --clean flag on the *first* attempt – subsequent
                # iterations should reuse the prior build cache to save time.
                self._build_image_and_fuzzers(clean=clean and attempt == 1)
                print(f"[*] Fuzzer build succeeded on attempt {attempt}!")
                return
            except HarnessGeneratorError as err:
                if attempt == max_iterations:
                    raise

                print(
                    f"[!] Build failed (attempt {attempt}/{max_iterations}). "
                    "Sending compiler stderr back to Codex..."
                )

                # Ask Codex for a minimal patch based on the compiler output.
                self._invoke_codex_to_fix_build(str(err))

                # If the project uses bundled source archives we may have to
                # regenerate them after Codex edits.
                self._repack_archives()

    # ---- Archive extraction / repack ---------------------------------- #
    ARCHIVE_REGEX = re.compile(r"\.(?:tar\.gz|tgz|tar|zip)$", re.IGNORECASE)

    def _extract_archives(self) -> None:
        proj_dir = self.repo_path / "projects" / self.project
        if not proj_dir.is_dir():
            return

        for arch in proj_dir.rglob("*"):
            if arch.is_file() and self.ARCHIVE_REGEX.search(arch.name):
                if arch.name.endswith(".tar.gz"):
                    extract_root = arch.with_name(
                        arch.stem[:-4]
                    )  # Remove .tar from .tar.gz
                elif arch.name.endswith(".tgz"):
                    extract_root = arch.with_name(arch.stem)
                else:
                    extract_root = arch.with_suffix("")

                if extract_root.exists():
                    continue

                print(f"[*] Extracting {arch.relative_to(self.repo_path)}")

                tmp_dir = tempfile.mkdtemp(dir=self.scratch_space)
                tmp_path = Path(tmp_dir)

                # Extract to temp location
                if arch.name.endswith(".zip"):
                    shutil.unpack_archive(str(arch), str(tmp_path))
                else:
                    with tarfile.open(arch, mode="r:*") as tf:
                        tf.extractall(tmp_path)

                # Move contents into extract_root (flatten, don't preserve temp dir)
                extract_root.mkdir(parents=True, exist_ok=True)
                for item in tmp_path.iterdir():
                    shutil.move(str(item), extract_root / item.name)

                shutil.rmtree(tmp_path, ignore_errors=True)
                self._archives[extract_root] = arch

    def _repack_archives(self) -> None:
        for src_dir, arch in self._archives.items():
            print(f"[*] Re-packing {arch.relative_to(self.repo_path)}")

            # Remove old archive
            arch.unlink(missing_ok=True)

            parent = arch.parent
            base_name = arch.name
            if base_name.endswith(".tar.gz"):
                base = arch.with_suffix("").with_suffix(
                    ""
                )  # Remove .gz then .tar
                mode = "w:gz"
                archive_path = parent / f"{base.name}.tar.gz"
            elif base_name.endswith(".tgz"):
                base = arch.with_suffix("")  # Remove .tgz
                mode = "w:gz"
                archive_path = parent / f"{base.name}.tgz"
            elif base_name.endswith(".tar"):
                base = arch.with_suffix("")
                mode = "w"
                archive_path = parent / f"{base.name}.tar"
            elif base_name.endswith(".zip"):
                base = arch.with_suffix("")
                archive_path = shutil.make_archive(
                    str(base), "zip", root_dir=src_dir
                )
                continue
            else:
                raise HarnessGeneratorError(
                    f"Unsupported archive format: {arch}"
                )

            with tarfile.open(archive_path, mode) as tf:
                for item in sorted(src_dir.rglob("*")):
                    tf.add(item, arcname=item.relative_to(src_dir))

    # ---- Codex interaction -------------------------------------------- #
    def _invoke_codex_for_harness(self) -> None:
        patcher = CodexHelper(
            repo_path=self.repo_path,
            ai_key_path=str(self.ai_key_path),
            copy_repo=False,
            codex_cli=self.codex_cli,
            codex_model=CODEX_ANALYSIS_MODEL,
            approval_mode=CODEX_APPROVAL_MODE,
        )

        # High-level tasks for Codex
        # IMPROVEME: extend prompt for java support
        instructions = textwrap.dedent(
            f"""
            **Objective (high-value fuzz target)**  
            Create a **new libFuzzer harness** for the **{self.project}** OSS-Fuzz project that
            exercises a *public* or *documented* API reachable with **user-supplied input**
            (e.g. files, packets, strings) and therefore has real-world security impact.

            ────────────────────────────────────────
            **Target-selection rules**

            1. **Start at the top**: pick the *highest-level* function that
            *directly* consumes attacker-controlled data.  
            • Good examples: `exif_data_load()`, `freerdp_peer_context_new()`,  
                `curl_url_set()`, `png_read_info()`.  
            • **Avoid** low-level helpers (`*_parse_int()`, `*_read_field()` etc.)
                unless *no higher layer* validates input.

            2. **Document reachability**  
            Add a one-line comment in the harness explaining why the chosen API
            is reachable from untrusted input in real software (file upload,
            network packet, etc.).

            3. **Minimal realistic setup**  
            If the API needs a context/handle, initialise it exactly as a real
            app would (e.g. `exif_data = exif_data_new_from_file(data, size)`).
            Don't stub out internal structs—use official constructors.

            4. **One API per harness**  
            If multiple candidate APIs exist, pick the single best one that is
            *not already fuzzed* (check existing harnesses + binaries).

            5. **ENSURE HARNESS USES THE LIBRARY CORRECTLY**
            Many false positives are the result of the generated harness code failing
            to exercise the library properly (passing a ptr instead of an int, etc.)
            Ensure all calls performed by the harness match the library signatures
            and use the library in the way it was intended to be used. Our goal is to
            only uncover bugs that are true positives with real world implications.

            ────────────────────────────────────────
            **Implementation requirements**

            * Harness signature  
            ```c++
            extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size);
            ````

            * Keep everything in C/C++ (follow project style).
            * **Do not** remove or refactor existing code; just add the harness and
            tweak build scripts so it is compiled.
            * Place the harness source next to similar existing harnesses.

            Extracted archive directories (may be empty if none):
            {', '.join(str(p.relative_to(self.repo_path)) for p in self._archives) or 'None'}

            **NO** build/run commands—just write code + build recipe edits.
            When finished, write the path to the new harness into `./done`

            Notes: 
            - The oss-fuzz project typically contains a Dockerfile, build.sh, and project.yaml.
            - The repo source is typically not included, but can be cloned to assist in analysis.
              - It may be specified in project.yaml as `main_repo`. It may be cloned as part of the docker build.
              - When you clone the repo source, you must clone it within your working directory (don't use /tmp)
            - Carefully analyze the existing build structure to fully understand what is needed to successfully include your new harness in the build.

            VERY IMPORTANT: You must clone the repo so that you can validate the function signatures of every library function you put in the new harness.
                            You must ensure that the library is being used correctly to mitigate false-positive crashes caused by errors in the harness.

            This task is very important! Every bug we trigger will be responsibly disclosed to make the world a safer place.
            Have fun and do your very best!
            """
        ).strip()

        stdout = patcher.run_codex_command(instructions)
        if stdout is None:
            raise HarnessGeneratorError(
                "Codex produced no edits when adding harness."
            )
        print(
            f"[*] Codex stdout (truncated):\n{stdout[:1200]}",
        )

    def _invoke_codex_to_fix_build(self, build_stderr: str) -> None:
        patcher = CodexHelper(
            repo_path=self.repo_path,
            ai_key_path=str(self.ai_key_path),
            copy_repo=False,
            codex_cli=self.codex_cli,
            codex_model=CODEX_ANALYSIS_MODEL,
            approval_mode=CODEX_APPROVAL_MODE,
        )
        instructions = [
            "Compilation failed.  Read the compiler output below and make only "
            "the minimal edits necessary to fix build-blocking errors.  "
            "Do not add features or refactor unrelated code."
            "Do not execute any commands to build or run any fuzzers, just correct the build statically."
        ]
        stdout = patcher.run_codex_command(
            instructions, additional_context=build_stderr
        )
        if stdout is None:
            raise HarnessGeneratorError("Codex failed to resolve build errors.")

    # ---- Run New Fuzzer ------------------------------------------------ #
    def _run_fuzzer(
        self,
        fuzzer_name: str,
        *,
        timeout_seconds: int = 600,
        engine: str = "libfuzzer",
        sanitizer: str | None = None,
        architecture: str = "x86_64",
        rss_limit_mb: int = 16_384,
        max_len: int = 1024,
    ) -> str:
        """
        Run a single fuzzer and return its combined stdout + stderr.

        • Captures raw bytes → decodes with errors='backslashreplace'
        • If stderr is not empty, appends it under "=== STDERR ===" in log.
        • Prints the last ≈200 lines and writes the full log to fuzzer_run_<uuid>.txt
        • Never raises on non-zero exit codes (crash/OOM/timeout are findings).
        """
        helper = self.repo_path / "infra" / "helper.py"
        if not helper.is_file():
            raise HarnessGeneratorError(
                "infra/helper.py not found - invalid checkout?"
            )

        corpus_dir = (
            self.repo_path
            / "build"
            / "out"
            / self.project
            / "corpus"
            / fuzzer_name
        )
        corpus_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env.setdefault("RSS_LIMIT_MB", str(rss_limit_mb))
        env.setdefault("TIMEOUT", "45")

        cmd = [
            "python3",
            str(helper),
            "run_fuzzer",
            "--architecture",
            architecture,
            "--engine",
            engine,
            "--sanitizer",
            sanitizer or self.sanitizer,
            "--corpus-dir",
            str(corpus_dir),
            self.project,
            fuzzer_name,
            "--",
            f"-max_total_time={timeout_seconds}",
            f"-max_len={max_len}",
            "-print_final_stats=1",
        ]

        print(f"[*] ➜  {' '.join(cmd)}")
        proc = subprocess.Popen(
            cmd,
            cwd=self.repo_path,
            env=env,
            stdout=subprocess.PIPE,  # raw bytes
            stderr=subprocess.PIPE,  # keep stderr separate
            text=False,  # important: capture bytes
        )

        try:
            raw_stdout, raw_stderr = proc.communicate(
                timeout=timeout_seconds + 30
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            raw_stdout, raw_stderr = proc.communicate()
            print("[!] Fuzzer process exceeded hard timeout; killed.")
        except Exception:
            import traceback

            traceback.print_exc()
            raw_stdout = b""
            raw_stderr = traceback.format_exc().encode()

        # Decode safely
        stdout_dec = raw_stdout.decode("utf-8", errors="backslashreplace")
        stderr_dec = raw_stderr.decode("utf-8", errors="backslashreplace")

        # Combine, adding labelled section if needed
        if stderr_dec.strip():
            full_output = f"{stdout_dec}\n\n=== STDERR ===\n{stderr_dec}"
        else:
            full_output = stdout_dec

        # Normalise CRs
        full_output = full_output.replace("\r", "\n")

        # Persist full log
        log_path = self.repo_path / f"fuzzer_run_{uuid.uuid4().hex}.txt"
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write(full_output)

        # Pretty-print the last 200 lines
        tail_lines = full_output.splitlines()[-200:]
        print("\n".join(tail_lines))
        print(f"\n[*] Full fuzzer log saved to: {log_path}")

        if proc.returncode != 0:
            print(
                f"[!] Fuzzer exited with rc={proc.returncode} "
                "(non-zero is expected for crash/timeout/OOM)."
            )

        return full_output

    # ────────────────────── Crash-handling helpers ─────────────────────── #

    BUG_PREFIXES = ("crash", "oom", "timeout")

    def _find_bug_files(self) -> set[Path]:
        """Return a *set* of Paths matching crash/oom/timeout files for project."""
        root = self.repo_path / "build" / "out" / self.project
        if not root.is_dir():
            return set()
        return {
            p
            for p in root.rglob("*")
            if p.is_file()
            and any(p.name.startswith(pref) for pref in self.BUG_PREFIXES)
        }

    # ------------------------------------------------------------------ #
    def _reproduce_crash(
        self, fuzzer_name: str, crash_path: Path
    ) -> tuple[str, str]:
        """Run `helper.py reproduce` and persist output → crash_reproduction.log.

        Returns a tuple (full_log, command_line).
        """

        helper = self.repo_path / "infra" / "helper.py"
        if not helper.is_file():
            raise HarnessGeneratorError(
                "infra/helper.py not found - cannot reproduce crash"
            )

        cmd_list = [
            "python3",
            str(helper),
            "reproduce",
            self.project,
            fuzzer_name,
            str(crash_path),
        ]

        cmd_str = " ".join(cmd_list)
        print(f"[*] ➜  {cmd_str} (reproducing crash)")

        proc = subprocess.run(
            cmd_list,
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )

        repro_output = proc.stdout + (
            "\n=== STDERR ===\n" + proc.stderr if proc.stderr else ""
        )

        # ── Strip ANSI colour / control codes for readability ──────────
        repro_output = self._strip_ansi(repro_output)

        # Build comment line with relative paths for readability
        try:
            crash_rel = crash_path.relative_to(self.repo_path)
        except ValueError:
            crash_rel = Path(crash_path).name
        helper_rel = helper.relative_to(self.repo_path)
        command_line = f"python {helper_rel} reproduce {self.project} {fuzzer_name} {crash_rel}"

        comment_line = f"# {command_line}\n"

        log_path = self.repo_path / "crash_reproduction.log"
        with open(log_path, "w", encoding="utf-8", errors="replace") as fh:
            fh.write(comment_line)
            fh.write(repro_output)

        print(
            f"[*] Crash reproduction log written to {log_path.relative_to(self.repo_path)}"
        )

        full_log = comment_line + repro_output
        return full_log, command_line

    # ------------------------------------------------------------------ #
    def _hexdump(self, path: Path, limit_bytes: int = 512) -> str:
        """Return an xxd -g1 style hexdump (≤limit_bytes) of a file."""
        try:
            return subprocess.check_output(
                [
                    "xxd",
                    "-g1",
                    "-l",
                    str(limit_bytes),
                    str(path),
                ],
                text=True,
            )
        except Exception:
            data = path.read_bytes()[:limit_bytes]
            lines = []
            for off in range(0, len(data), 16):
                chunk = data[off : off + 16]
                hex_bytes = " ".join(f"{b:02x}" for b in chunk)
                ascii = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                lines.append(f"{off:08x}: {hex_bytes:<47}  {ascii}")
            return "\n".join(lines)

    # ------------------------------------------------------------------ #
    _ANSI_ESCAPE_RE = re.compile(
        r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", re.MULTILINE
    )

    @classmethod
    def _strip_ansi(cls, text: str) -> str:
        """Return *text* with any ANSI colour/control sequences removed."""

        # A pre-compiled regex is used for efficiency as logs can be large.
        # The pattern aims to match the majority of common ANSI escape
        # sequences produced by oss-fuzz tooling (colour, cursor movement,
        # screen erasing etc.).  If a sequence slips through it will simply
        # render as an innocuous control code in the markdown, which is still
        # preferable to the unreadable colour gibberish.
        return cls._ANSI_ESCAPE_RE.sub("", text)

    # ------------------------------------------------------------------ #
    def _locate_harness_source(self, fuzzer_name: str) -> Path | None:
        """Locate the harness source file, primarily via the ./done marker."""

        done_file = self.repo_path / "done"
        if done_file.is_file():
            try:
                rel_path = (
                    done_file.read_text(encoding="utf-8", errors="replace")
                    .splitlines()[0]
                    .strip()
                )
                if rel_path:
                    abs_path = (self.repo_path / rel_path).resolve()
                    if abs_path.is_file():
                        return abs_path
            except Exception:
                pass
        # First: look for file name containing fuzzer_name with typical C/C++ suffix
        exts = {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp"}
        for p in self.repo_path.rglob("*"):
            if p.suffix.lower() in exts and fuzzer_name in p.name:
                return p

        # Fallback: any file containing LLVMFuzzerTestOneInput token
        for p in self.repo_path.rglob("*"):
            if p.suffix.lower() in exts:
                try:
                    txt = p.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                if "LLVMFuzzerTestOneInput" in txt:
                    return p

        return None

    # ------------------------------------------------------------------ #
    def _generate_bug_report(
        self,
        fuzzer_name: str,
        crash_path: Path,
        reproducer_log: str,
        reproducer_cmd: str,
    ) -> None:
        """Invoke Codex to write crash_analysis.md at repo root."""

        harness_path = self._locate_harness_source(fuzzer_name)
        harness_source = (
            harness_path.read_text(encoding="utf-8", errors="replace")
            if harness_path and harness_path.is_file()
            else "*Harness source not found*"
        )

        hexdump_text = self._hexdump(crash_path)

        # Build context block (text, not markdown) for Codex
        context_parts = [
            "=== Reproducer Log ===\n",
            reproducer_log,
            "\n\n=== Harness Source ===\n",
            harness_source,
            "\n\n=== Crashing Input (hexdump) ===\n",
            hexdump_text,
            "\n",
        ]
        additional_context = "".join(context_parts)

        # ── Write crash_info.md ────────────────────────────────────────
        def _md_safe(text: str) -> str:
            return text.replace("```", "```​")  # no early fence close

        md_lines = [
            "# Crash Info",
            "",
            "## Reproducer command",
            "```bash",
            reproducer_cmd,
            "```",
            "",
            "## Reproducer log",
            "```text",
            _md_safe(reproducer_log),
            "```",
            "",
            "## Harness source",
            "```c",
            _md_safe(harness_source),
            "```",
            "",
            "## Crashing input (hexdump)",
            "```text",
            hexdump_text,
            "```",
            "",
        ]

        (self.repo_path / "crash_info.md").write_text(
            "\n".join(md_lines), encoding="utf-8"
        )
        print("[*] crash_info.md written")

        instructions = textwrap.dedent(
            """
            You are an experienced security researcher.

            Using the context provided, write a **new file** called `crash_analysis.md` in the repository root with the following top-level sections:

            1. Bug Type
            2. Bug Summary
            3. Bug Impact (real world reachability/exploitability/constraints)
            4. How to Patch

            Requirements:
              • Provide concise yet complete analysis (markdown).
              • If the bug could not be reproduced (the reproducer exited cleanly) then indicate this in your analysis. 
              • These harnesses were *just generated*. Carefully consider whether the crash is due to a genuine bug in the target project or a mistake in the harness.  
                If it is harness-induced, explicitly state this in your analysis and use **severity: None** in the *bug impact* section.
                Look out for harness mistakes like erroneous frees, misuse of the target library, incorrect function arguments / types, or anything else indicating this is not a genuine bug in the target library.
                For these cases, you must also include the sentinel "HARNESS ERROR" somewhere in your analysis.
            """
        ).strip()

        print("[*] Calling Codex to generate crash_analysis.md …")

        patcher = CodexHelper(
            repo_path=self.repo_path,
            ai_key_path=str(self.ai_key_path),
            copy_repo=False,
            codex_cli=self.codex_cli,
            codex_model=CODEX_ANALYSIS_MODEL,
            approval_mode=CODEX_APPROVAL_MODE,
        )

        stdout = patcher.run_codex_command(
            instructions,
            additional_context=additional_context,
        )

        if stdout is None:
            print("[!] Codex did not produce crash_analysis.md")
        else:
            print(
                "[*] Codex generated crash_analysis.md (truncated output below):"
            )
            print(stdout[:1000])

        # ── Reproducer script generation ─────────────────────────────
        try:
            self._generate_reproducer_script()
        except HarnessGeneratorError as err:
            print(f"[!] Failed to generate crash_reproducer.sh: {err}")

    # ---- Seed corpus generation ------------------------------------ #
    def _invoke_codex_to_generate_seeds(self, fuzzer_name: str) -> None:
        """Ask Codex to create initial corpus seeds for the new harness."""

        corpus_dir = (
            self.repo_path
            / "build"
            / "out"
            / self.project
            / "corpus"
            / fuzzer_name
        )
        corpus_dir.mkdir(parents=True, exist_ok=True)

        harness_path = self._locate_harness_source(fuzzer_name)
        if not harness_path or not harness_path.is_file():
            raise HarnessGeneratorError(
                f"Unable to locate harness source for {fuzzer_name} when generating seeds"
            )

        harness_source = harness_path.read_text(
            encoding="utf-8", errors="replace"
        )

        instructions = textwrap.dedent(
            f"""
            The directory `{corpus_dir.relative_to(self.repo_path)}` is the **initial corpus** for the newly created libFuzzer harness `{fuzzer_name}`.

            You will receive the *full harness source code* as additional context.

            Task: create one or more **meaningful seed inputs** (at least one, up to five) and write them as **files** inside that corpus directory.

            Guidelines:
              • Inputs should be small yet exercise realistic code paths.  
              • Prefer simple human-readable examples when possible; otherwise use `.bin` files.  
              • Do **NOT** modify any existing source or build scripts.  
              • Use appropriate file extensions if the target expects a specific format.  
              • Binary content can be expressed via hex literals or base64 in the patch - whichever is most convenient.

            Write the files directly - no commentary - using the standard Codex patch instructions.
            """
        ).strip()

        patcher = CodexHelper(
            repo_path=self.repo_path,
            ai_key_path=str(self.ai_key_path),
            copy_repo=False,
            codex_cli=self.codex_cli,
            codex_model=CODEX_ANALYSIS_MODEL,
            approval_mode=CODEX_APPROVAL_MODE,
        )

        stdout = patcher.run_codex_command(
            instructions,
            additional_context=harness_source,
        )

        if stdout is None:
            raise HarnessGeneratorError("Codex did not generate any seed files")

        print("[*] Codex seed-generation output (truncated):")
        print(stdout[:800])

    # ------------------------------------------------------------------ #
    def _generate_reproducer_script(self) -> None:
        """Invoke Codex to create crash_reproducer.sh after crash analysis."""

        info_path = self.repo_path / "crash_info.md"
        analysis_path = self.repo_path / "crash_analysis.md"

        if not info_path.is_file() or not analysis_path.is_file():
            raise HarnessGeneratorError(
                "Required markdown files not found for reproducer script generation."
            )

        context_blob = (
            "=== crash_info.md ===\n"
            + info_path.read_text(encoding="utf-8", errors="replace")
            + "\n\n=== crash_analysis.md ===\n"
            + analysis_path.read_text(encoding="utf-8", errors="replace")
        )

        instructions = textwrap.dedent(
            """
            Using the context provided, create a robust, idempotent Bash script named `crash_reproducer.sh` in the repository root that demonstrates the vulnerability described.

            The script must:
              • Install any required build/runtime dependencies non-interactively (e.g. apt-get -y, pip install) and skip if already present.
              • Build the vulnerable project with AddressSanitizer enabled (or another memory sanitizer that will surface the bug).
              • Fetch or construct the proof-of-concept input that triggers the crash - ideally the same data that appears in the fuzzing crash file, but adapted for a real-world invocation path (command-line tool, library API, etc.).
              • Construct the proof-of-concept script so that it reproduces the harness bug, but do not call the harness directly.
              • Apply reasonable execution limits (timeout, ulimit) so it never hangs.
              • Exit with non-zero status if the bug is reproduced; otherwise exit 0.
              • Contain clear comments for every major section.

            Notes:
              • You can run `git status --porcelain` to discover which harness source file was added or modified.  Use this knowledge to understand the target API.
              • The script should work when executed from the repository root on a clean Ubuntu container.
              • Only create `crash_reproducer.sh`. Do not modify existing files.
            """
        ).strip()

        patcher = CodexHelper(
            repo_path=self.repo_path,
            ai_key_path=str(self.ai_key_path),
            copy_repo=False,
            codex_cli=self.codex_cli,
            codex_model=CODEX_ANALYSIS_MODEL,
            approval_mode=CODEX_APPROVAL_MODE,
        )

        stdout = patcher.run_codex_command(
            instructions,
            additional_context=context_blob,
        )

        if stdout is None:
            raise HarnessGeneratorError(
                "Codex did not create crash_reproducer.sh"
            )

        print("[*] Codex reproduce script output (truncated):")
        print(stdout[:800])

    # ---- Smoke test ---------------------------------------------------- #
    def _run_any_fuzzer_once(self, timeout: int = 60) -> None:
        out_dir = self.repo_path / "build" / "out" / self.project
        fuzzers = [
            p
            for p in out_dir.iterdir()
            if p.is_file()
            and os.access(p, os.X_OK)
            and not p.name.endswith(".dict")
        ]
        if not fuzzers:
            print("[*] No fuzzer binaries found.")
            return
        fuzzer = fuzzers[0].name
        print(
            f"[*] Smoke-testing fuzzer {fuzzer} …",
        )
        helper = self.repo_path / "infra" / "helper.py"
        corpus = out_dir / "corpus" / fuzzer
        corpus.mkdir(parents=True, exist_ok=True)
        self._run_cmd(
            [
                "python3",
                str(helper),
                "run_fuzzer",
                "--engine",
                "libfuzzer",
                "--sanitizer",
                self.sanitizer,
                "--corpus-dir",
                str(corpus),
                self.project,
                fuzzer,
                "--",
                f"-max_total_time={timeout}",
                "-timeout=120",
                "-print_final_stats=1",
            ],
            cwd=self.repo_path,
            env=os.environ.copy(),
        )

    # ---- Shell helper -------------------------------------------------- #
    def _run_cmd(
        self,
        cmd: Sequence[str],
        *,
        cwd: Path,
        env: dict[str, str],
        input: str | None = None,
    ) -> None:
        """Run a subprocess and raise HarnessGeneratorError on failure."""
        cmd_str = " ".join(cmd)
        print(f"[*] ➜  {cmd_str}")
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE if input else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = proc.communicate(input=input, timeout=7200)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise HarnessGeneratorError("Command timed out: " + " ".join(cmd))

        if proc.returncode != 0:
            print(
                f"[*] Command failed (rc={proc.returncode})\nSTDOUT:\n{stdout}\n---\nSTDERR:\n{stderr}"
            )
            raise HarnessGeneratorError(stderr)

        print(f"[*] Command succeeded. Truncated stdout:\n{stdout[:600]}")


# --------------------------------------------------------------------------- #
# CLI entry-point                                                             #
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate and integrate a new OSS-Fuzz harness with Codex."
    )
    parser.add_argument(
        "project_name", help="OSS-Fuzz project name (e.g. freerdp)"
    )
    parser.add_argument(
        "oss_fuzz_path",
        type=Path,
        default="./oss-fuzz",
        help="Path to local oss-fuzz checkout (root directory)",
    )
    parser.add_argument(
        "ai_key_path",
        type=Path,
        default="./.env",
        help="Path to file containing your OpenAI-compatible API key",
    )

    # Optional knobs
    parser.add_argument(
        "--sanitizer",
        default=DEFAULT_SANITIZER,
        help="Sanitizer to use when building fuzzers (default: address)",
    )
    parser.add_argument(
        "--codex-cli",
        default="codex",
        help="Executable name or path for the Codex CLI",
    )
    parser.add_argument(
        "--scratch-space",
        type=Path,
        help="Directory for temp working copies (defaults to /tmp)",
    )
    parser.add_argument(
        "--copy-repo",
        action="store_true",
        help="Work on a temporary copy of the oss-fuzz tree (safer, slower)",
    )
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="Skip rebuilding image/fuzzers after adding the harness",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Run a 60-second smoke test with one fuzzer at the beginning",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=MAX_BUILD_RETRIES,
        help=f"Maximum build-retry attempts (default: {MAX_BUILD_RETRIES})",
    )

    args = parser.parse_args()
    load_dotenv(dotenv_path=os.path.expanduser(args.ai_key_path))

    try:
        hg = HarnessGenerator(
            project_name=args.project_name,
            oss_fuzz_path=args.oss_fuzz_path,
            ai_key_path=args.ai_key_path,
            sanitizer=args.sanitizer,
            codex_cli=args.codex_cli,
            scratch_space=args.scratch_space,
            copy_repo=args.copy_repo,
        )
        hg.generate_harness(
            build=not args.no_build,
            run_smoke=args.smoke,
            max_iterations=args.max_retries,
        )
    except HarnessGeneratorError as e:
        print(f"[harness_generator] ERROR: {e}", file=sys.stderr)
        sys.exit(1)

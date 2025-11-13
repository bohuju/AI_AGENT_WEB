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
codex_helper.py
────────────────
• Runs the Codex CLI inside a pseudo-terminal so coloured output streams live
  to stdout while still being captured for later inspection.
• Watches for the sentinel file `./done` that Codex is instructed to write
  once it has applied all edits.  The session is terminated as soon as the
  file appears.
• Retries the **CLI invocation** on common transient failure strings.
• Retries the **whole patch generation attempt** when no diff was produced.
• Enforces a hard wall-clock timeout and performs a 3-stage
  (SIGINT→SIGTERM→SIGKILL) shutdown sequence.
• Returns *None* if Codex made no edits; otherwise returns full captured
  stdout so callers can inspect or log the conversation.
• Optional *ai_key_path* can point to a file containing the OpenAI key; the
  helper sets the OPENAI_API_KEY environment variable if it was not yet
  defined.
"""

from __future__ import annotations

import errno
import logging
import os
import pty
import select
import shutil
import signal
import subprocess
import tempfile
import textwrap
import time
from pathlib import Path
from typing import List, Sequence

from git import Repo, exc as git_exc

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_git_repo(path: Path) -> Repo:
    """Return a *Repo* object, initialising a new repository if needed."""

    try:
        repo = Repo(path)
    except git_exc.InvalidGitRepositoryError:
        repo = Repo.init(path)

    # Make sure at least one commit exists so `git diff` behaves.
    if not repo.head.is_valid():
        repo.git.add(A=True)
        try:
            repo.git.commit(m="Initial commit", allow_empty=True)
        except git_exc.GitCommandError:
            # Happens when there is literally nothing to commit yet.
            pass
    return repo


# ---------------------------------------------------------------------------
# Core helper class
# ---------------------------------------------------------------------------


class CodexHelper:
    """Light-weight wrapper around the Codex CLI with robust retry logic."""

    def __init__(
        self,
        *,
        repo_path: Path,
        ai_key_path: str | None = None,
        copy_repo: bool = True,
        scratch_space: Path | None = None,
        codex_cli: str = "codex",
        codex_model: str = "o3",
        approval_mode: str = "full-auto",
        dangerous_bypass: bool = False,
        sandbox_mode: str | None = None,
    ) -> None:

        self.repo_path = Path(repo_path).expanduser().resolve()
        if not self.repo_path.is_dir():
            raise FileNotFoundError(f"Repository not found: {self.repo_path}")

        self.scratch_space = scratch_space or Path("/tmp")
        self.codex_cli = str(codex_cli)
        self.codex_model = codex_model
        self.approval_mode = approval_mode

        if sandbox_mode:
            self.sandbox_mode = sandbox_mode
        else:
            self.sandbox_mode = "workspace-write"
        
        if dangerous_bypass:
            self.approval_mode = "never"
            self.sandbox_mode = "danger-full-access"
        

        # Work on an isolated copy when requested so Codex can freely modify.
        if copy_repo:
            self.working_dir = Path(
                tempfile.mkdtemp(prefix="codex-helper-", dir=str(self.scratch_space))
            )
            shutil.copytree(self.repo_path, self.working_dir, dirs_exist_ok=True)
        else:
            self.working_dir = self.repo_path

        self.repo = _ensure_git_repo(self.working_dir)

        # Provide API key via env var if a path was supplied.
        if ai_key_path and "OPENAI_API_KEY" not in os.environ:
            key_path = Path(ai_key_path).expanduser()
            if key_path.is_file():
                key = key_path.read_text(encoding="utf-8", errors="ignore").strip()
                if key:
                    os.environ["OPENAI_API_KEY"] = key

        LOGGER.debug("CodexHelper working directory: %s", self.working_dir)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_codex_command(
        self,
        instructions: str | Sequence[str],
        *,
        additional_context: str | None = None,
        max_attempts: int = 3,
        timeout: int = 1800,
        max_cli_retries: int = 3,
        initial_backoff: float = 3.0,
    ) -> str | None:
        """Execute Codex with robust retry logic and return its stdout or *None*."""

        SENTINEL = "done"
        RETRY_ERRORS = (
            "Connection closed prematurely",
            "internal error",
            "failed to send request",
            "model failed to respond",
            "Network error while contacting OpenAI",
        )

        done_path = self.working_dir / SENTINEL

        # Build prompt body once (mirrors original behaviour).
        if isinstance(instructions, (list, tuple)):
            tasks = "\n".join(str(i) for i in instructions)
        else:
            tasks = str(instructions)

        prompt_parts: List[str] = [
            "You are an expert engineer. Apply the edits below - no refactors.",
            "When ALL tasks are complete, output a summary of your changes,",
            "then populate a file called **done** in the repo root (`./done`).",
            "Write the relative path to the **single** most relevant file you created or modified into `./done`.",
            f"## Tasks\n{tasks}",
        ]

        if additional_context:
            prompt_parts.append(
                textwrap.dedent(
                    f"""
                    ---
                    ### Additional context
                    {additional_context.strip()}
                    ---
                    """
                )
            )

        prompt = "\n".join(prompt_parts).strip()

        # ----------------------------------------------------------------
        # Outer loop – retry full patch attempt if no diff produced.
        # ----------------------------------------------------------------

        for attempt in range(1, max_attempts + 1):
            LOGGER.info("[CodexHelper] patch attempt %d/%d", attempt, max_attempts)

            done_path.unlink(missing_ok=True)

            # ----------------------------------------------------------------
            # Inner loop – retry CLI invocation on transient errors.
            # ----------------------------------------------------------------

            cli_try = 0
            backoff = initial_backoff

            while cli_try < max_cli_retries:
                cli_try += 1
                LOGGER.info("[CodexHelper] launch #%d (backoff=%.1fs)", cli_try, backoff)

                cmd = [
                    self.codex_cli,
                    "exec",
                    "-m",
                    self.codex_model,
                    "-c model_reasoning_effort=high",
                    "-c disable_response_storage=true",
                    "-c sandbox_mode="+self.sandbox_mode,
                    "--full-auto" if self.approval_mode == "full-auto" else "-c approval_policy="+self.approval_mode,
                    prompt,
                ]

                master_fd, slave_fd = pty.openpty()
                proc = subprocess.Popen(
                    cmd,
                    cwd=self.working_dir,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    env=os.environ.copy(),
                    text=False,
                    close_fds=True,
                )
                os.close(slave_fd)

                captured_chunks: List[str] = []
                start_time = time.time()
                saw_retry_error = False

                # Helper to perform 3-stage kill.
                def _kill_proc(grace: float = 4.0) -> None:
                    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
                        if proc.poll() is not None:
                            return
                        try:
                            proc.send_signal(sig)
                            proc.wait(timeout=grace)
                        except subprocess.TimeoutExpired:
                            continue

                # Use non-blocking read with select() similar to original.
                try:
                    with os.fdopen(master_fd, "rb", buffering=0) as stream:
                        while True:
                            # Wall-clock timeout.
                            if time.time() - start_time > timeout:
                                LOGGER.error("[CodexHelper] hard timeout")
                                raise TimeoutError

                            # Sentinel detected?
                            if done_path.exists():
                                LOGGER.info("[CodexHelper] done flag detected")
                                _kill_proc()
                                break

                            ready, *_ = select.select([stream], [], [], 1.0)
                            if ready:
                                try:
                                    chunk = stream.read(4096)
                                except OSError as e:
                                    if e.errno == errno.EIO:  # PTY closed
                                        break
                                    raise

                                if not chunk:
                                    break  # EOF

                                text = chunk.decode("utf-8", errors="replace")
                                print(text, end="")  # live pass-through to caller
                                captured_chunks.append(text)

                                # Check for retryable error messages on the fly.
                                if any(err in text for err in RETRY_ERRORS):
                                    LOGGER.warning("[CodexHelper] retryable error detected → abort")
                                    saw_retry_error = True
                                    _kill_proc()
                                    break

                            if proc.poll() is not None and not ready:
                                break
                except TimeoutError:
                    _kill_proc()
                    saw_retry_error = True
                    LOGGER.warning("[CodexHelper] Codex timeout; will retry")

                # Decide if we should relaunch the CLI.
                if saw_retry_error:
                    time.sleep(backoff)
                    backoff *= 2
                    continue  # restart inner CLI loop

                # CLI completed without retryable error; break inner loop.
                break

            # After inner loop – did Codex create the sentinel and produce diff?

            if not done_path.exists():
                LOGGER.warning("[CodexHelper] sentinel not created; next attempt")
                continue  # outer attempt loop

            # Refresh repo to ensure it sees new changes.
            self.repo.git.add(A=True)

            if self.repo.git.diff('HEAD'):
                LOGGER.info("[CodexHelper] diff produced — success")
                return "".join(captured_chunks)

            LOGGER.info("[CodexHelper] sentinel present but no diff; next attempt")

        LOGGER.warning("[CodexHelper] exhausted attempts — no edits produced")
        return None


# ---------------------------------------------------------------------------
# Backwards-compat alias – internal code may still import CodexPatcher.
# ---------------------------------------------------------------------------


CodexPatcher = CodexHelper

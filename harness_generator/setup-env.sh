#!/usr/bin/env bash

# Simple environment bootstrapper for this repository.
#
# Features
#   • Confirms that basic system tools (git, docker) are available – offering to
#     install them via apt when missing.
#   • Optionally installs the libxapian-dev development headers.
#   • Optionally creates (or re-uses) a virtual-environment in ./.sherpa-venv.
#   • Installs Python dependencies from requirements.txt.
#
# The script is intentionally interactive so it can be re-run safely.

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

set -euo pipefail

#------------------------------------------------------------------------------
# Ensure required command-line tools are present
#------------------------------------------------------------------------------

# ensure_tool <command|""> <apt-package>
#   If <command> is non-empty the function checks for its presence in PATH. If
#   the command is absent (or <command> is empty) it falls back to verifying
#   that the corresponding apt package is installed via dpkg. When missing it
#   offers an interactive prompt to install it.

ensure_tool() {
    local cmd_name="$1"   # may be empty string for header-only libs like libxapian-dev
    local pkg_name="$2"

    local cmd_missing=false
    if [[ -n "$cmd_name" ]]; then
        if ! command -v "$cmd_name" >/dev/null 2>&1; then
            cmd_missing=true
        fi
    fi

    # If we didn't check a command or the command is present, still ensure the
    # package is installed (covers header-only deps).
    if dpkg -s "$pkg_name" >/dev/null 2>&1; then
        # Package present, and command (if any) is present—nothing to do.
        $cmd_missing && echo "'$cmd_name' will become available after reopening the shell." >&2
        return 0
    fi

    echo "The package '$pkg_name' is required${cmd_name:+ (provides '$cmd_name')}." >&2
    read -rp "Install '$pkg_name' now? [y/N]: " _install_pkg
    case "${_install_pkg:-N}" in
        [yY]|[yY][eE][sS])
            echo "Installing $pkg_name (requires sudo)..."
            sudo apt update && sudo apt install -y "$pkg_name"
            ;;
        *)
            echo "Cannot continue without '$pkg_name'. Please install it and re-run the script." >&2
            exit 1
            ;;
    esac
}

# Verify core dependencies
ensure_tool git git
ensure_tool docker docker.io
ensure_tool "" libxapian-dev

# The repository relies on the "codex" command-line tool.
# Detect Codex – offer instructions for installing when missing.
if ! command -v codex >/dev/null 2>&1; then
    echo "Codex CLI not detected in PATH. It is required for harness generation."
    echo "Follow the instructions in the Codex CLI repository for installation: https://github.com/openai/codex"
fi

# libxapian-dev is handled by ensure_tool above.

PROJECT_ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$PROJECT_ROOT"

# Detect python executable
detect_python() {
    if command -v python3 > /dev/null 2>&1; then
        echo python3
    elif command -v python > /dev/null 2>&1; then
        echo python
    else
        echo "Error: Python interpreter not found in PATH." >&2
        exit 1
    fi
}

PYTHON_BIN="$(detect_python)"

VENV_DIR="${PROJECT_ROOT}/.sherpa-venv"

#------------------------------------------------------------------------------
# Virtual-environment handling
#------------------------------------------------------------------------------

activate_venv=false

if [[ -d "$VENV_DIR" ]]; then
    echo "Found existing virtual environment at $VENV_DIR"
    activate_venv=true
else
    read -rp "No virtual environment found. Create one at ./.sherpa-venv? [y/N]: " _create
    case "${_create:-N}" in
        [yY]|[yY][eE][sS])
            echo "Creating virtual environment..."
            "$PYTHON_BIN" -m venv "$VENV_DIR"
            activate_venv=true
            ;;
        *)
            echo "Proceeding without a dedicated virtual environment. Ensure you have the right permissions."
            ;;
    esac
fi

# Determine pip invocation (always via python -m pip to avoid PATH issues)

if $activate_venv; then
    source "$VENV_DIR/bin/activate"
fi

# After potential activation re-detect python so it points to venv interpreter
PYTHON_BIN="$(detect_python)"

PIP_CMD=("$PYTHON_BIN" -m pip)

#------------------------------------------------------------------------------
# Requirements installation
#------------------------------------------------------------------------------

if [[ -f "$PROJECT_ROOT/requirements.txt" ]]; then
    echo "Installing dependencies from requirements.txt..."
    "${PIP_CMD[@]}" install --upgrade -r "$PROJECT_ROOT/requirements.txt"
else
    echo "requirements.txt not found – skipping dependency installation." >&2
fi

echo && echo "Environment setup complete. Ready for harness generation! 🚀"

if $activate_venv && [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    echo -e "\nExecute \`source ./.sherpa-venv/bin/activate\` to enter the virtual environment."
fi

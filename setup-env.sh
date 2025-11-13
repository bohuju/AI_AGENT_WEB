#!/bin/bash
# setup-env.sh for sherpa project
# Usage: source ./setup-env.sh

VENV_DIR=".venv"
REQ_FILE="harness_generator/requirements.txt"
PYTHON_BIN="python3"

# Detect Apple Silicon and recommend Homebrew Python if needed
if [[ $(uname -m) == "arm64" ]]; then
    echo "Detected Apple Silicon (arm64)."
    if ! command -v $PYTHON_BIN &> /dev/null; then
        echo "$PYTHON_BIN not found. Please install Python 3 via Homebrew: brew install python3"
        exit 1
    fi
fi

# Install codex binary if missing
if ! command -v codex &> /dev/null; then
    echo "codex not found. Installing..."
    if [[ "$(uname)" == "Darwin" ]]; then
        # macOS
        if command -v brew &> /dev/null; then
            brew install codex
        else
            echo "Homebrew not found. Please install Homebrew first: https://brew.sh"
            exit 1
        fi
    elif [[ "$(uname)" == "Linux" ]]; then
        # Linux
        if command -v apt &> /dev/null; then
            sudo apt update && sudo apt install -y codex
        else
            echo "apt not found. Please install codex manually."
            exit 1
        fi
    else
        echo "Unsupported OS. Please install codex manually."
        exit 1
    fi
fi

# Create virtual environment if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment in $VENV_DIR..."
    $PYTHON_BIN -m venv $VENV_DIR
fi

# Activate virtual environment
source $VENV_DIR/bin/activate

# Upgrade pip and install dependencies
pip install --upgrade pip
pip install -r $REQ_FILE

echo "Environment setup complete."

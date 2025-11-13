# Makefile for sherpa project
# Usage:
#   make setup      # Set up Python venv and install dependencies
#   make clean      # Remove venv and __pycache__
#   make run-script SCRIPT=script_name.py # Run a script from harness_generator/scripts

VENV_DIR := .venv
JOBS_DIR := ./jobs
PYTHON := python3
REQ_FILE := harness_generator/requirements.txt

setup:
	$(PYTHON) -m venv $(VENV_DIR)
	. $(VENV_DIR)/bin/activate && pip install --upgrade pip && pip install -r $(REQ_FILE)

clean:
	rm -rf $(VENV_DIR)
	rm -rf $(JOBS_DIR)
	find . -type d -name "__pycache__" -exec rm -rf {} +

run-script:
	. $(VENV_DIR)/bin/activate && python harness_generator/scripts/$(SCRIPT)


leveldb:
	@if [ -z "$$OPENAI_API_KEY" ]; then \
	  echo "Error: OPENAI_API_KEY is not set. Please export your OpenAI API key before running make leveldb."; \
	  exit 1; \
	fi
	@docker info > /dev/null 2>&1 || (echo "Error: Docker is not running or not accessible. Please start Docker and try again." && exit 1)
	. $(VENV_DIR)/bin/activate && python harness_generator/batch_generate.py --targets harness_generator/yamls/leveldb.yaml

.PHONY: setup clean run-script leveldb

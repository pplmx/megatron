.PHONY: help ruff
.DEFAULT_GOAL := help

# Init the venv
init: sync
	@uvx pre-commit install --hook-type commit-msg --hook-type pre-push
	@uv run playwright install chromium

# Sync the project with the venv
sync:
	@uv sync

# Run
run:
	@uv run main.py docs/pdf_html output.pdf

# Ruff
ruff:
	@uvx ruff format .
	@uvx ruff check . --fix

# Type check
type:
	@uvx mypy .

# Show help
help:
	@echo ""
	@echo "Usage:"
	@echo "    make [target]"
	@echo ""
	@echo "Targets:"
	@awk '/^[a-zA-Z\-_0-9]+:/ \
	{ \
		helpMessage = match(lastLine, /^# (.*)/); \
		if (helpMessage) { \
			helpCommand = substr($$1, 0, index($$1, ":")-1); \
			helpMessage = substr(lastLine, RSTART + 2, RLENGTH); \
			printf "\033[36m%-22s\033[0m %s\n", helpCommand,helpMessage; \
		} \
	} { lastLine = $$0 }' $(MAKEFILE_LIST)

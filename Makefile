# Makefile to help automate key steps

.DEFAULT_GOAL := help
# Will likely fail on Windows, but Makefiles are in general not Windows
# compatible so we're not too worried
TEMP_FILE := $(shell mktemp)

# A helper script to get short descriptions of each target in the Makefile
define PRINT_HELP_PYSCRIPT
import re, sys

for line in sys.stdin:
	match = re.match(r'^([\$$\(\)a-zA-Z_-]+):.*?## (.*)$$', line)
	if match:
		target, help = match.groups()
		print("%-30s %s" % (target, help))
endef
export PRINT_HELP_PYSCRIPT


help:  ## print short description of each target
	@python3 -c "$$PRINT_HELP_PYSCRIPT" < $(MAKEFILE_LIST)


.PHONY: pre-commit
pre-commit:  ## run pre-commit on all files in the repository
	uv run pre-commit run --all-files

.PHONY: virtual-environment
virtual-environment:  ## update virtual environment, create a new one if it doesn't already exist
	uv sync
	uv run pre-commit install

.PHONY: test
test:  ## run the tests
	uv run pytest tests

.PHONY: accept-layout
accept-layout:  ## accept a deliberate change to how the summary is laid out
	# Rewrites the checked-in copies of the email under
	# tests/regression/test_summary_rendering/ to match what the renderer now
	# produces. Read `git diff` on those files before committing: they are the
	# only review the layout gets.
	uv run pytest tests/regression --force-regen
	git --no-pager diff --stat tests/regression

.PHONY: pin-esmporium
pin-esmporium:  ## point esmporium at another commit, e.g. make pin-esmporium REV=a296d9c
	@test -n "$(REV)" || { \
		echo "Give it a commit, branch or tag: make pin-esmporium REV=<rev>"; \
		exit 1; \
	}
	uv add "esmporium @ git+https://github.com/esmporium/esmporium.git" --rev "$(REV)"
	# The tests are the point of doing it this way round: tests/unit/test_esmporium_contract.py
	# names every bit of esmporium we rely on, so a rename upstream fails here and
	# now, by name, instead of as a missing email at 6am. If it fails, either fix
	# the renderer or `git checkout pyproject.toml uv.lock && uv sync` to go back.
	uv run pytest tests
	@echo
	@echo "esmporium is now pinned at:"
	@grep -o 'esmporium.git?rev=[^"]*' uv.lock | head -1

.PHONY: run-search-and-notifications
run-search-and-notifications:  ## run the whole job: search, update the database, report the changes and email them
	uv run python scripts/drive-search-and-notifications.py

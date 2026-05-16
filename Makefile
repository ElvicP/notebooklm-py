# notebooklm-py developer tasks.
#
# PYTHON is overridable so CI (which pip-installs into the system env) can
# call `make audit PYTHON=python` while local dev uses the uv-managed venv.
PYTHON ?= uv run python

.PHONY: help audit

help:
	@echo "Targets:"
	@echo "  make audit   Run the recurring API parity audit (Spec 0.1),"
	@echo "               regenerating docs/feature-parity.md."

audit:
	$(PYTHON) scripts/parity_audit.py --output docs/feature-parity.md

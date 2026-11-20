PY ?= .venv/bin/python

.PHONY: check hooks compile kinematics selfcheck smoke

check: compile kinematics selfcheck smoke
	@echo "ALL CHECKS PASSED"

hooks:
	chmod +x .githooks/pre-commit
	git config core.hooksPath .githooks
	@echo "pre-commit hook installed"

compile:
	$(PY) -m py_compile arm.py mcp_server.py scripted_pick.py elrobot/ik.py elrobot/check.py

kinematics:
	$(PY) elrobot/check.py

selfcheck:
	$(PY) arm.py selfcheck

smoke:
	$(PY) scripts/smoke.py

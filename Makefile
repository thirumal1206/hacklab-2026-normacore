PY ?= .venv/bin/python

.PHONY: check hooks compile kinematics selfcheck smoke station agent

check: compile kinematics selfcheck smoke station agent
	@echo "ALL CHECKS PASSED"

hooks:
	chmod +x .githooks/pre-commit
	git config core.hooksPath .githooks
	@echo "pre-commit hook installed"

compile:
	$(PY) -m py_compile arm.py mcp_server.py scripted_pick.py elrobot/ik.py elrobot/check.py agent/sorter.py agent/mcp_server.py agent/check.py agent/dashboard.py

kinematics:
	$(PY) elrobot/check.py

selfcheck:
	$(PY) arm.py selfcheck

smoke:
	$(PY) scripts/smoke.py

station:
	$(PY) scripts/station_logic.py

agent:
	$(PY) agent/check.py

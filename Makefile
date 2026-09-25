PY ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
PORT ?= 8765

.PHONY: setup serve demo status test overlay install uninstall hooks clean

setup:            ## venv + editable install with the embedding model
	python3 -m venv .venv
	.venv/bin/python -m pip install -U pip
	.venv/bin/python -m pip install -e '.[embed,test]'
	.venv/bin/neko fetch-model || true

serve:            ## run the daemon (real agents)
	$(PY) -m neko serve --port $(PORT)

demo:             ## run the daemon with fake agents and open the room
	$(PY) -m neko serve --demo --port $(PORT) --open

status:
	$(PY) -m neko status --port $(PORT)

test:
	$(PY) -m pytest -q tests

overlay:          ## desktop overlay (Electron); daemon must be running
	cd overlay && ( [ -d node_modules ] || npm install ) && NEKO_PORT=$(PORT) npm start

install:          ## launchd agent
	$(PY) -m neko install

uninstall:
	$(PY) -m neko uninstall

hooks:            ## opt-in Claude Code hooks
	$(PY) -m neko hooks install

clean:
	rm -rf build dist *.egg-info .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

# The Python suite needs target/release/hsl-config to exist before pytest
# starts: see tests/helpers.py:ensure_helper.
.PHONY: test test-serial build

build:
	cargo build --release --locked

test: build
	python3 -m pytest tests/test_tmux_mouse.py -p no:xdist
	python3 -m pytest --ignore=tests/test_tmux_mouse.py -n auto --dist loadscope

test-serial: build
	python3 -m pytest -p no:xdist

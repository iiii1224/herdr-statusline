# The Python suite needs target/release/hsl-config to exist before pytest
# starts: see tests/helpers.py:ensure_helper.
.PHONY: test test-serial build

build:
	cargo build --release --locked

test: build
	python3 -m pytest -n auto --dist loadscope

test-serial: build
	python3 -m pytest -p no:xdist

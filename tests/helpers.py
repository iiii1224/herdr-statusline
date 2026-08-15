"""Shared fixtures for the herdr-statusline integration tests."""

import json
import os
import pathlib
import re
import stat
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANAGED_MARKER = "# herdr-statusline-managed-launcher:v1"


def make_executable(path: pathlib.Path, text: str) -> None:
    """Write ``text`` to ``path`` and give it the owner execute bit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def base_env(home: pathlib.Path, fakebin: pathlib.Path) -> dict:
    """Build an environment isolated from the developer's real Herdr setup."""
    home.mkdir(parents=True, exist_ok=True)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("HSL_", "HERDR_"))
    }
    env.pop("TMUX", None)
    env.pop("TMUX_PANE", None)
    env["HOME"] = str(home)
    env["PATH"] = f"{fakebin}:{os.environ['PATH']}"
    return env


HELPER = ROOT / "target/release/hsl-config"


def ensure_helper():
    """Return the release helper's path, failing with what to run if absent.

    Deliberately does not build. Under `pytest -n auto` each worker is its own
    process, and a session-scoped fixture is never evaluated in the xdist
    controller, so there is no place inside pytest that runs exactly once.
    Building is a precondition: `make test` does it, and CI has its own step.
    """
    if not os.access(HELPER, os.X_OK):
        raise RuntimeError(
            f"{HELPER} is missing or not executable.\n"
            "Run `make test`, or build it directly, before running pytest."
        )
    return HELPER


def write_protocol(base, pairs, enabled=True, mouse_clicks=False):
    """Write a protocol file by running the shipped writer.

    Tests name tmux options with dashes and state values as they should reach
    tmux; the Rust writer owns the wire format, so no test re-implements it.
    ``pairs`` is a sequence of ``(name, value)``. Returns the file's path.
    """
    helper = ensure_helper()
    lines = [
        f"enabled = {'true' if enabled else 'false'}",
        f"mouse_clicks = {'true' if mouse_clicks else 'false'}",
        "[statusline]",
    ]
    for name, value in pairs:
        # TOML basic strings share JSON's escapes over the printable subset, so
        # json.dumps yields a correct literal for embedded quotes and
        # backslashes. An f-string with bare quotes would corrupt those.
        # `status-format[1]` is written `status_format_1` in config.toml,
        # because a TOML bare key cannot hold brackets. Every other option is
        # the tmux name with hyphens swapped for underscores.
        key = re.sub(r"\[(\d+)\]$", r"_\1", name).replace("-", "_")
        lines.append(f"{key} = {json.dumps(value)}")
    config = base / "protocol-config.toml"
    config.write_text("\n".join(lines) + "\n")
    result = subprocess.run(
        [str(helper), "load", str(config)], check=True, text=True, capture_output=True
    )
    path = base / "options"
    path.write_text(result.stdout)
    return path

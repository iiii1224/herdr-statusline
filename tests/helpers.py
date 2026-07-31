"""Shared fixtures for the herdr-statusline integration tests."""

import json
import os
import pathlib
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
_HELPER_BUILT = False


def ensure_helper():
    """Build the release helper once per process and return its path.

    Called from write_protocol rather than a setUpClass: unittest orders
    classes by dir(module), so RealTmuxSmokeTests runs before TmuxRuntimeTests
    and a per-class build would leave the first class without a binary.
    """
    global _HELPER_BUILT
    if not _HELPER_BUILT:
        subprocess.run(
            ["cargo", "build", "--release", "--locked"], cwd=ROOT, check=True
        )
        _HELPER_BUILT = True
    return HELPER


def write_protocol(base, pairs, enabled=True):
    """Write a protocol file by running the shipped writer.

    Tests name tmux options with dashes and state values as they should reach
    tmux; the Rust writer owns the wire format, so no test re-implements it.
    ``pairs`` is a sequence of ``(name, value)``. Returns the file's path.
    """
    helper = ensure_helper()
    lines = [f"enabled = {'true' if enabled else 'false'}", "[statusline]"]
    for name, value in pairs:
        # TOML basic strings share JSON's escapes over the printable subset, so
        # json.dumps yields a correct literal for embedded quotes and
        # backslashes. An f-string with bare quotes would corrupt those.
        lines.append(f'{name.replace("-", "_")} = {json.dumps(value)}')
    config = base / "protocol-config.toml"
    config.write_text("\n".join(lines) + "\n")
    result = subprocess.run(
        [str(helper), "load", str(config)], check=True, text=True, capture_output=True
    )
    path = base / "options"
    path.write_text(result.stdout)
    return path

"""Drive the real run-in-tmux on a pty and inject SGR mouse events.

tmux only reports the mouse to a client attached to a terminal, so none of
this is reachable through the fake tmux in test_tmux_runtime.py. The existing
RealTmuxSmokeTests uses `script` for the same reason; a pty of our own is the
same idea plus the ability to write into it.

Critically, this starts scripts/run-in-tmux rather than reproducing what it
does. A test that set `mouse on` and the bindings itself would pass even if
the production wiring quoted them wrongly or expanded them at the wrong stage.
"""

import fcntl
import os
import pty
import select
import signal
import struct
import termios
import time

# Inner-app stub standing in for Herdr: raw stdin, mouse tracking and SGR
# encoding, recording exactly what tmux forwards. Exits on `q` so the runtime
# can tear its session down normally.
INNER_APP = r"""
import os, subprocess, sys, time, tty
log = sys.argv[1]
ready = sys.argv[2]
expected = sys.argv[3]
open(log, "w").close()
tty.setraw(0)
sys.stdout.write("\033[?MODEh\033[?1006h")
sys.stdout.flush()

# flush() only guarantees the bytes reached the pty. It says nothing about
# whether the tmux server has read them and updated its own mouse flags, so
# poll until they are actually set before declaring readiness. Without this
# a click can be delivered while tracking is still off.
deadline = time.time() + 20
while time.time() < deadline:
    out = subprocess.run(
        ["tmux", "display-message", "-p",
         "#{mouse_any_flag}#{mouse_all_flag}#{mouse_sgr_flag}"],
        capture_output=True, text=True,
    ).stdout.strip()
    if out == expected:
        with open(ready, "w") as stream:
            stream.write(out + "\n")
        break
    time.sleep(0.05)

end = time.time() + 120
while time.time() < end:
    try:
        data = os.read(0, 4096)
    except OSError:
        break
    if not data:
        time.sleep(0.05)
        continue
    with open(log, "a") as stream:
        stream.write(repr(data) + "\n")
    if b"p" in data:
        # Probe: report server state a test cannot otherwise reach. This runs
        # inside the pane, so $TMUX already points at the disposable server.
        out = subprocess.run(
            ["tmux", "display-message", "-p",
             "PROBE in_mode=#{pane_in_mode} mouse=#{mouse}"],
            capture_output=True, text=True,
        ).stdout.strip()
        keys = subprocess.run(
            ["tmux", "list-keys", "-T", "root"], capture_output=True, text=True,
        ).stdout.strip()
        n = len([line for line in keys.splitlines() if line.strip()])
        with open(log, "a") as stream:
            stream.write(f"{out} rootkeys={n}\n")
    if b"q" in data:
        break
"""


def shell_quote(text):
    return "'" + text.replace("'", "'\\''") + "'"


def inner_app_script(log_path, ready_path, mode=1003):
    """A shell script running the stub, for HSL_HERDR_BIN."""
    program = INNER_APP.replace("MODE", str(mode))
    expected = "111" if mode == 1003 else "101"
    return (
        "#!/bin/sh\n"
        f"exec python3 -c {shell_quote(program)} "
        f"{shell_quote(str(log_path))} {shell_quote(str(ready_path))} {shell_quote(expected)}\n"
    )


class HslPty:
    def __init__(self, runtime, env, ready_path, session="mouse", cols=80,
                 rows=24, ready_timeout=30.0):
        self.runtime = runtime
        self.env = env
        self.ready_path = ready_path
        self.session = session
        self.cols = cols
        self.rows = rows
        # Generous for real sessions; the negative test passes a small value
        # so that exercising the timeout path costs seconds, not half a minute.
        self.ready_timeout = ready_timeout
        self._buffer = bytearray()
        self.pid = None
        self.fd = None

    def __enter__(self):
        # The marker is per-session state, and several tests start more than
        # one session in the same test method. A stale marker from the
        # previous session would make _wait_ready return immediately against
        # a server that has not read the tracking sequence yet.
        try:
            os.unlink(self.ready_path)
        except FileNotFoundError:
            pass
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            for key, value in self.env.items():
                os.environ[key] = value
            # stty before exec: the parent's TIOCSWINSZ races run-in-tmux's own
            # `stty size` read, and a 0x0 size makes it start tmux without one.
            os.execvp("sh", [
                "sh", "-c",
                f"stty rows {self.rows} cols {self.cols}; "
                f"exec sh {shell_quote(str(self.runtime))} "
                f"--session {shell_quote(self.session)}",
            ])
        fcntl.ioctl(
            self.fd, termios.TIOCSWINSZ,
            struct.pack("HHHH", self.rows, self.cols, 0, 0),
        )
        # __exit__ never runs when __enter__ raises, so a failed wait would
        # otherwise leak the child pid and the pty fd into the rest of the run.
        try:
            self._wait_ready(self.ready_timeout)
        except BaseException:
            self._reap()
            raise
        self._buffer.clear()
        return self

    def _wait_ready(self, timeout):
        """Block until the inner app reports tmux has its mouse flags set.

        The marker is written only after the stub has confirmed the flags on
        the server, so its appearance means a click sent next will actually
        be tracked. A fixed sleep here would either be slow or racy.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if os.path.exists(self.ready_path):
                return
            self._drain(0.2)
        drawn = bytes(self._buffer)
        raise AssertionError(
            f"the tmux session never became mouse-ready within {timeout}s.\n"
            f"pid={self.pid} fd={self.fd}\n"
            f"pty buffer ({len(drawn)} bytes): {drawn[-2000:]!r}"
        )

    def __exit__(self, *exc):
        try:
            self._shutdown()
        finally:
            self._reap()

    def _shutdown(self):
        try:
            os.write(self.fd, b"q")
        except OSError:
            return
        deadline = time.time() + 5.0
        while time.time() < deadline:
            try:
                done, _ = os.waitpid(self.pid, os.WNOHANG)
            except ChildProcessError:
                self.pid = None
                return
            if done:
                self.pid = None
                return
            self._drain(0.2)

    def _reap(self):
        if self.pid is not None:
            try:
                os.kill(self.pid, signal.SIGKILL)
                os.waitpid(self.pid, 0)
            except (OSError, ChildProcessError):
                pass
            self.pid = None
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

    def _drain(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            if select.select([self.fd], [], [], 0.1)[0]:
                try:
                    self._buffer.extend(os.read(self.fd, 65536))
                except OSError:
                    break

    def _send(self, sequence, settle=0.2):
        os.write(self.fd, sequence.encode())
        time.sleep(0.05)
        self._drain(settle)

    def click(self, col, row, button=0):
        self._send(f"\033[<{button};{col};{row}M")
        self._send(f"\033[<{button};{col};{row}m")

    def wheel(self, col, row, up=True):
        self._send(f"\033[<{64 if up else 65};{col};{row}M")

    def motion(self, col, row):
        self._send(f"\033[<35;{col};{row}M")

    def drag(self, from_col, from_row, to_col, to_row):
        self._send(f"\033[<0;{from_col};{from_row}M")
        # Button 1 held plus motion is Cb 32, which tmux reports as MouseDrag1.
        self._send(f"\033[<32;{to_col};{to_row}M")
        self._send(f"\033[<0;{to_col};{to_row}m")

    def wait_for_lines(self, path, count, timeout=10.0):
        """Poll until `path` holds `count` lines.

        run-shell -b promises neither completion order nor completion time, so
        a fixed sleep is a flake and an ordered comparison is a false failure.
        """
        deadline = time.time() + timeout
        lines = []
        while time.time() < deadline:
            if os.path.exists(path):
                with open(path) as stream:
                    lines = stream.read().splitlines()
                if len(lines) >= count:
                    return lines
            self._drain(0.2)
        return lines

    def wait_for_text(self, path, needle, timeout=10.0):
        """Poll until `path` contains `needle`.

        Prefer this over wait_for_lines when waiting on the inner app's log:
        a line there is one os.read(), not one event, so two events arriving
        together share a line and a count-based wait sits out its whole
        deadline for output that already arrived.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if os.path.exists(path):
                with open(path) as stream:
                    if needle in stream.read():
                        return True
            self._drain(0.2)
        return False

    def probe(self, log_path, timeout=6.0):
        """Ask the inner stub for live server state and return its report.

        Some invariants are only visible on the server: a hook that exits
        non-zero without `|| true` has no command queue to print to, so tmux
        puts the pane into view-mode instead. Nothing in the client's byte
        stream shows that, and the message it does print wraps at the
        terminal width, so matching on drawn text is not a reliable guard.
        """
        self._send("p", settle=0.3)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if os.path.exists(log_path):
                with open(log_path) as stream:
                    for line in reversed(stream.read().splitlines()):
                        if line.startswith("PROBE "):
                            return line
            self._drain(0.2)
        return ""

    def drawn(self):
        return bytes(self._buffer)

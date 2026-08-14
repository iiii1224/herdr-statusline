import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

from tests.helpers import ROOT, base_env, make_executable, write_protocol
from tests.mouse_pty import HslPty, inner_app_script

RUNTIME = ROOT / "scripts/run-in-tmux"
TMUX = shutil.which("tmux")

# " BTN " occupies x=0..4 and `tail` follows. tmux makes the hit area one
# column wider than the text, so the range covers x=0..5: col 3 lands on x=2
# and col 40 lands outside every range. Measured against a real tmux.
STATUS_FORMAT = "#[align=left]#[range=user|btn] BTN #[norange]tail"
BUTTON_COL = 3
OUTSIDE_COL = 40
STATUS_ROW = 24  # 24 rows, status line at the bottom
PANE_ROW = 5


def tmux_at_least_3_4():
    if not TMUX:
        return False
    out = subprocess.run([TMUX, "-V"], text=True, capture_output=True).stdout
    parts = "".join(c if c.isdigit() or c == "." else " " for c in out).split()
    if not parts:
        return False
    major, _, minor = parts[0].partition(".")
    try:
        return int(major) > 3 or (int(major) == 3 and int(minor or 0) >= 4)
    except ValueError:
        return False


class MouseIntegrationBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = pathlib.Path(self.tmp.name)
        self.fakebin = self.base / "bin"
        self.fakebin.mkdir()
        self.app_log = self.base / "app.log"
        self.ready_marker = self.base / "ready.marker"
        self.hook_log = self.base / "hook.log"
        # A config directory whose name carries a space, a '#' and a quote:
        # all three are hazards for the two-stage expansion run-shell does.
        self.config_dir = self.base / "cfg dir#x'y"
        self.config_dir.mkdir()

    def hook(self, body=None):
        body = body or (
            "#!/bin/sh\n"
            f"printf '%s|%s|%s|%s\\n' \"$1\" \"$2\" \"$3\" \"$4\""
            f" >> '{self.hook_log}'\n"
        )
        make_executable(self.config_dir / "on-click.sh", body)

    def runtime_env(self, status_format=STATUS_FORMAT, mouse=True, mode=1003,
                    extra_options=()):
        stub = self.fakebin / "herdr"
        make_executable(
            stub, inner_app_script(self.app_log, self.ready_marker, mode=mode)
        )
        options = write_protocol(
            self.base,
            [("status-interval", "1"), ("status-format-0", status_format),
             *extra_options],
            mouse_clicks=mouse,
        )
        env = base_env(self.base / "home", self.fakebin)
        env.update({
            "HSL_HERDR_BIN": str(stub),
            "HSL_STATUS_OPTIONS": str(options),
            "HERDR_PLUGIN_CONFIG_DIR": str(self.config_dir),
            "HERDR_SESSION": "mouse",
            "TMPDIR": str(self.base),
        })
        if env.get("TERM", "dumb") == "dumb":
            env["TERM"] = "xterm-256color"
        return env

    def session(self, **kw):
        return HslPty(RUNTIME, self.runtime_env(**kw), self.ready_marker)

    def received(self):
        if not self.app_log.exists():
            return ""
        return self.app_log.read_text()


@unittest.skipUnless(TMUX, "tmux is not installed")
@unittest.skipUnless(tmux_at_least_3_4(), "needs tmux 3.4 or newer")
class StatusClickTests(MouseIntegrationBase):
    def test_delivers_button_range_and_coordinates(self):
        self.hook()
        with self.session() as term:
            term.click(BUTTON_COL, STATUS_ROW)
            term.click(BUTTON_COL, STATUS_ROW, button=2)
            term.wheel(BUTTON_COL, STATUS_ROW, up=True)
            term.wheel(BUTTON_COL, STATUS_ROW, up=False)
            lines = term.wait_for_lines(self.hook_log, 4)
        # run-shell -b does not order its jobs, so compare as a multiset.
        self.assertEqual(
            sorted(lines),
            sorted(["left|btn|2|0", "right|btn|2|0",
                    "wheelup|btn|2|0", "wheeldown|btn|2|0"]),
        )

    def test_clicking_outside_every_range_does_nothing(self):
        self.hook()
        with self.session() as term:
            term.click(OUTSIDE_COL, STATUS_ROW)
            lines = term.wait_for_lines(self.hook_log, 1, timeout=3.0)
        self.assertEqual(lines, [])

    def test_a_range_name_cannot_inject_a_shell_command(self):
        # 15 bytes, exactly tmux's limit, and enough to escape hand-written
        # single quotes: `;>/tmp/zNNN;` truncates a file into existence. It
        # must arrive as one literal argument instead.
        #
        # The limit leaves no room for a temp directory, so the marker has to
        # live at a short absolute path. Claim one that does not exist rather
        # than clearing a fixed name, which would delete a stranger's file and
        # make two concurrent runs report each other's result.
        marker = None
        for suffix in range(1000):
            candidate = pathlib.Path(f"/tmp/z{suffix:03d}")
            try:
                # O_EXCL, not a prior exists() check: two concurrent runs can
                # both see the same name free and then both claim it.
                os.close(os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            except FileExistsError:
                continue
            marker = candidate
            break
        self.assertIsNotNone(marker, "no free /tmp/zNNN marker path")
        # Held only to reserve the name; the injection would recreate it.
        marker.unlink()
        self.addCleanup(marker.unlink, True)
        evil = f"a';>{marker};'"
        self.assertEqual(len(evil), 15)
        self.hook()
        with self.session(
            status_format=f"#[align=left]#[range=user|{evil}] BTN #[norange]tail"
        ) as term:
            term.click(BUTTON_COL, STATUS_ROW)
            lines = term.wait_for_lines(self.hook_log, 1)
        self.assertEqual(lines, [f"left|{evil}|2|0"])
        self.assertFalse(marker.exists(), "the range name executed a command")

    def test_carries_hostile_range_names_verbatim(self):
        # '#' and '#{...}' are the other half of the two-stage expansion
        # hazard: tmux would re-expand them if they were not escaped.
        self.hook()
        for evil in ("a#b", "a#{x}b", "a b", "a$(id)b", "0123456789abcde"):
            with self.subTest(name=evil):
                self.assertLessEqual(len(evil), 15)
                self.hook_log.unlink(missing_ok=True)
                fmt_evil = evil.replace("#", "##")
                with self.session(
                    status_format=(
                        f"#[align=left]#[range=user|{fmt_evil}] BTN #[norange]tail"
                    )
                ) as term:
                    term.click(BUTTON_COL, STATUS_ROW)
                    lines = term.wait_for_lines(self.hook_log, 1, timeout=3.0)
                if evil == "a b":
                    # Space splits tmux style attributes, so range=user|a b has no valid range.
                    self.assertEqual(lines, [])
                else:
                    self.assertEqual(lines, [f"left|{evil}|2|0"])

    def test_a_noisy_failing_hook_draws_nothing(self):
        # The hook records that it ran before making any noise. Without that
        # line the assertions below would also hold for a hook that never
        # started, which is the opposite of what this is checking.
        self.hook(
            "#!/bin/sh\n"
            f"printf 'ran\\n' >> '{self.hook_log}'\n"
            "echo NOISE-MARKER\n"
            "exit 7\n"
        )
        with self.session() as term:
            term.click(BUTTON_COL, STATUS_ROW)
            lines = term.wait_for_lines(self.hook_log, 1)
            drawn = term.drawn()
            probe = term.probe(self.app_log)
        self.assertEqual(lines, ["ran"], "the hook never ran")
        self.assertNotIn(b"NOISE-MARKER", drawn)
        # `|| true` is what keeps this true, and the pane is where it shows.
        # A non-zero run-shell -b has no command queue to report to, so tmux
        # takes the pane into view-mode instead — Herdr loses it. Matching on
        # drawn text cannot guard this: tmux's message embeds the whole
        # command, so it wraps at the terminal width and never appears
        # contiguously in the client's byte stream.
        # An empty probe is itself the symptom: once tmux has taken the pane,
        # the probe keystroke goes to view-mode and never reaches the stub.
        self.assertTrue(
            probe,
            "the pane stopped accepting input after a failing hook, which is "
            "what happens when the binding lacks `|| true`",
        )
        self.assertIn("in_mode=0", probe,
                      "a failing hook put the pane into a tmux mode")

    def test_a_slow_hook_does_not_block_the_command_queue(self):
        # -b is the whole point. The hook announces itself and only then
        # sleeps, so the question is how far apart the two announcements land,
        # not how long the pair takes overall.
        #
        # Backgrounded, both lines appear as fast as the clicks arrive. Run in
        # the foreground the first hook holds tmux's command queue for its
        # whole sleep, so the second click is not even dispatched until three
        # seconds later and the deadline below expires with one line.
        self.hook(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$1\" >> '{self.hook_log}'\n"
            "sleep 3\n"
        )
        with self.session() as term:
            term.click(BUTTON_COL, STATUS_ROW)
            term.click(BUTTON_COL, STATUS_ROW)
            lines = term.wait_for_lines(self.hook_log, 2, timeout=2.0)
        self.assertEqual(lines, ["left", "left"])

    def test_rapid_clicks_all_reach_the_hook(self):
        self.hook()
        with self.session() as term:
            for _ in range(6):
                term.click(BUTTON_COL, STATUS_ROW)
            lines = term.wait_for_lines(self.hook_log, 6)
        self.assertEqual(len(lines), 6)
        self.assertEqual(set(lines), {"left|btn|2|0"})

    def test_the_ready_marker_records_the_flags_tmux_actually_set(self):
        # AC-D2-1. The marker means "tmux has read the tracking sequence",
        # not merely "the inner app wrote it". mode 1003 sets any and all;
        # sgr is set by the 1006 request in both modes. Measured on 3.7b:
        # mode 1000 reports 101 and mode 1003 reports 111.
        self.hook()
        with self.session():
            recorded = self.ready_marker.read_text().strip()
        self.assertEqual(recorded, "111")

    def test_a_stale_marker_is_removed_and_a_stuck_session_reports_the_buffer(self):
        # Covers AC-D2-3 and the stale-marker case in one deterministic test,
        # with no reliance on timing or filesystem timestamp resolution.
        #
        # The stub never writes a marker. A pre-existing marker is therefore
        # the ONLY thing that could satisfy _wait_ready. An implementation
        # that forgets to unlink it returns successfully and this test fails;
        # a correct one unlinks it, waits, and times out with a useful message.
        self.hook()
        self.ready_marker.write_text("STALE\n")

        stub = self.fakebin / "herdr"
        # STUCK is printed into the pane so the assertion below can prove the
        # error message carries the real buffer, not just a label. The sleep
        # must outlive ready_timeout so the session is still up when it fires.
        make_executable(stub, "#!/bin/sh\nprintf 'STUCK\\n'\nsleep 15\n")
        options = write_protocol(
            self.base,
            [("status-interval", "1"), ("status-format-0", STATUS_FORMAT)],
            mouse_clicks=True,
        )
        env = base_env(self.base / "home", self.fakebin)
        env.update({
            "HSL_HERDR_BIN": str(stub),
            "HSL_STATUS_OPTIONS": str(options),
            "HERDR_PLUGIN_CONFIG_DIR": str(self.config_dir),
            "HERDR_SESSION": "mouse",
            "TMPDIR": str(self.base),
        })
        if env.get("TERM", "dumb") == "dumb":
            env["TERM"] = "xterm-256color"

        with self.assertRaises(AssertionError) as caught:
            with HslPty(RUNTIME, env, self.ready_marker, ready_timeout=5.0):
                pass
        message = str(caught.exception)
        self.assertIn("never became mouse-ready", message)
        # Not just the "pty buffer" label: assert the buffer's actual content
        # reached the message. A fixed string carrying the label but omitting
        # the bytes would satisfy a label-only check and tell a debugger
        # nothing. AC-D2-3.
        self.assertIn("STUCK", message)


@unittest.skipUnless(TMUX, "tmux is not installed")
@unittest.skipUnless(tmux_at_least_3_4(), "needs tmux 3.4 or newer")
class RootTableContentsTests(MouseIntegrationBase):
    def test_a_wired_server_holds_exactly_the_four_status_bindings(self):
        # The fake-tmux test can only see the argv run-in-tmux emitted; it
        # cannot observe a key table. This is the same claim on a real server
        # with the mouse actually on, which is when tmux's own defaults would
        # come back if base.conf stopped clearing root.
        self.hook()
        with self.session() as term:
            probe = term.probe(self.app_log)
        # #{mouse} is a flag format: 1, not "on".
        self.assertIn("mouse=1", probe)
        self.assertIn("rootkeys=4", probe)


@unittest.skipUnless(TMUX, "tmux is not installed")
@unittest.skipUnless(tmux_at_least_3_4(), "needs tmux 3.4 or newer")
class PanePassThroughTests(MouseIntegrationBase):
    def test_forwards_clicks_motion_drag_and_wheel_in_1003(self):
        self.hook()
        with self.session(mode=1003) as term:
            term.click(10, PANE_ROW)
            term.motion(12, 6)
            term.drag(10, PANE_ROW, 14, 7)
            term.wheel(10, PANE_ROW)
            # Wait on the last event sent, not on a line count: two events
            # can share one os.read() line in the app log.
            term.wait_for_text(self.app_log, r"\x1b[<64;10;5M", timeout=6.0)
        blob = self.received()
        self.assertIn(r"\x1b[<0;10;5M", blob)
        self.assertIn(r"\x1b[<0;10;5m", blob)
        self.assertIn(r"\x1b[<35;12;6M", blob)   # motion, 1003 only
        self.assertIn(r"\x1b[<32;14;7M", blob)   # drag, button 1 held
        self.assertIn(r"\x1b[<64;10;5M", blob)   # wheel up

    def test_forwards_clicks_and_wheel_in_1000(self):
        self.hook()
        with self.session(mode=1000) as term:
            term.click(10, PANE_ROW)
            term.wheel(10, PANE_ROW)
            term.wait_for_text(self.app_log, r"\x1b[<64;10;5M", timeout=6.0)
        blob = self.received()
        self.assertIn(r"\x1b[<0;10;5M", blob)
        self.assertIn(r"\x1b[<0;10;5m", blob)
        self.assertIn(r"\x1b[<64;10;5M", blob)

    def test_translates_coordinates_to_pane_relative(self):
        # tmux hands the application pane-relative rows, so this is not a
        # byte-for-byte relay. status-position is in the allowlist, so a user
        # really can move the bar to the top; the pane then starts one row
        # down and terminal row 5 must arrive as row 4.
        self.hook()
        with self.session(
            extra_options=[("status-position", "top")]
        ) as term:
            term.click(10, PANE_ROW)
            term.wait_for_text(self.app_log, r"\x1b[<0;10;4M", timeout=6.0)
        self.assertIn(r"\x1b[<0;10;4M", self.received())


@unittest.skipUnless(TMUX, "tmux is not installed")
@unittest.skipUnless(tmux_at_least_3_4(), "needs tmux 3.4 or newer")
class RootTableGuardTests(MouseIntegrationBase):
    def test_pass_through_breaks_without_the_root_table_clear(self):
        # Guards tmux/base.conf's `unbind-key -a -T root`. Removing that line
        # brings tmux's defaults back; M-MouseDown3Pane then opens a menu
        # instead of forwarding, so the application stops seeing what the
        # terminal sent.
        #
        # The discriminator has to exist on every tmux this feature supports
        # and must have no forwarding branch. C-MouseDown1Pane has neither
        # property: it arrived in 3.7, so on 3.4 through 3.6 the event would
        # pass through and the guard would fail. DoubleClick1Pane is worse —
        # it forwards with send-keys -M whenever mouse_any_flag is set, which
        # is exactly this case. M-MouseDown3Pane is present in 3.6b and 3.7b
        # and consumes the event in both, which was measured.
        original = (ROOT / "tmux/base.conf").read_text()
        self.assertIn("unbind-key -a -T root", original,
                      "base.conf must still clear the root table")
        patched = self.base / "base-without-root-clear.conf"
        patched.write_text(original.replace("unbind-key -a -T root\n", ""))

        self.hook()
        env = self.runtime_env()
        env["HSL_TEST_BASE_CONF"] = str(patched)
        event = r"\x1b[<10;10;5M"
        control = r"\x1b[<0;10;5M"
        with HslPty(RUNTIME, env, self.ready_marker) as term:
            # Positive control first, in this same session. Without it a
            # session that never started — a bad HSL_TEST_BASE_CONF, a slow
            # boot — is indistinguishable from tmux swallowing the event, and
            # the assertions below would pass having tested nothing. A plain
            # click survives because the restored MouseDown1Pane forwards it.
            term.click(10, PANE_ROW)
            self.assertTrue(
                term.wait_for_text(self.app_log, control, timeout=6.0),
                "the session with root left populated never forwarded "
                "anything, so its negative result proves nothing",
            )
            term.click(10, PANE_ROW, button=10)   # M-MouseDown3Pane (Alt+right)
            # Expected to time out: the default binding swallows the event.
            broken_arrived = term.wait_for_text(self.app_log, event, timeout=4.0)
            broken = self.received()

        # Sanity: with the clear in place the same event does arrive.
        self.setUp()
        self.hook()
        with self.session() as term:
            term.click(10, PANE_ROW, button=10)
            intact_arrived = term.wait_for_text(self.app_log, event, timeout=4.0)
            intact = self.received()

        self.assertTrue(intact_arrived,
                        "the cleared root table must forward the event")
        self.assertIn(event, intact)
        self.assertFalse(broken_arrived,
                         "without the clear tmux's default binding must "
                         "consume the event")
        self.assertNotIn(event, broken)



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
        make_executable(stub, inner_app_script(self.app_log, mode=mode))
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
        return HslPty(RUNTIME, self.runtime_env(**kw))

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
        # single quotes: `;>/tmp/hslz;` truncates a file into existence. It
        # must arrive as one literal argument instead.
        marker = pathlib.Path("/tmp/hslz")
        marker.unlink(missing_ok=True)
        self.addCleanup(marker.unlink, True)
        evil = "a';>/tmp/hslz;'"
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
        self.hook("#!/bin/sh\necho NOISE-MARKER\nexit 7\n")
        with self.session() as term:
            term.click(BUTTON_COL, STATUS_ROW)
            term.wait_for_lines(self.hook_log, 1, timeout=3.0)
            drawn = term.drawn()
        self.assertNotIn(b"NOISE-MARKER", drawn)
        self.assertNotIn(b"returned 7", drawn)

    def test_a_slow_hook_does_not_block_the_command_queue(self):
        # -b is what keeps this true. Without it the first click would hold the
        # queue for the whole sleep and the second would run only afterwards.
        self.hook(
            "#!/bin/sh\n"
            f"sleep 3\nprintf '%s\\n' \"$1\" >> '{self.hook_log}'\n"
        )
        with self.session() as term:
            term.click(BUTTON_COL, STATUS_ROW)
            term.click(BUTTON_COL, STATUS_ROW)
            # Both must be in flight at once: two sequential three-second
            # sleeps could not produce both lines inside this deadline.
            lines = term.wait_for_lines(self.hook_log, 2, timeout=5.5)
        self.assertEqual(lines, ["left", "left"])

    def test_rapid_clicks_all_reach_the_hook(self):
        self.hook()
        with self.session() as term:
            for _ in range(6):
                term.click(BUTTON_COL, STATUS_ROW)
            lines = term.wait_for_lines(self.hook_log, 6)
        self.assertEqual(len(lines), 6)
        self.assertEqual(set(lines), {"left|btn|2|0"})


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
            term.wait_for_lines(self.app_log, 7, timeout=6.0)
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
            term.wait_for_lines(self.app_log, 3, timeout=6.0)
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
            term.wait_for_lines(self.app_log, 2, timeout=6.0)
        self.assertIn(r"\x1b[<0;10;4M", self.received())


@unittest.skipUnless(TMUX, "tmux is not installed")
@unittest.skipUnless(tmux_at_least_3_4(), "needs tmux 3.4 or newer")
class RootTableGuardTests(MouseIntegrationBase):
    def test_pass_through_breaks_without_the_root_table_clear(self):
        # Guards tmux/base.conf's `unbind-key -a -T root`. Removing that line
        # brings tmux's defaults back; C-MouseDown1Pane then runs swap-pane
        # instead of forwarding, so the application stops seeing what the
        # terminal sent.
        original = (ROOT / "tmux/base.conf").read_text()
        self.assertIn("unbind-key -a -T root", original,
                      "base.conf must still clear the root table")
        patched = self.base / "base-without-root-clear.conf"
        patched.write_text(original.replace("unbind-key -a -T root\n", ""))

        self.hook()
        env = self.runtime_env()
        env["HSL_TEST_BASE_CONF"] = str(patched)
        with HslPty(RUNTIME, env) as term:
            term.click(10, PANE_ROW, button=16)   # C-MouseDown1Pane (Ctrl+Click)
            term.wait_for_lines(self.app_log, 2, timeout=4.0)
            broken = self.received()

        # Sanity: with the clear in place the same events do arrive.
        self.setUp()
        self.hook()
        with self.session() as term:
            term.click(10, PANE_ROW, button=16)
            term.wait_for_lines(self.app_log, 2, timeout=4.0)
            intact = self.received()

        self.assertIn(r"\x1b[<16;10;5M", intact,
                      "the cleared root table must forward the events")
        self.assertNotEqual(
            broken.count(r"\x1b[<16;10;5M"), intact.count(r"\x1b[<16;10;5M"),
            "removing the root-table clear must change what the app receives",
        )



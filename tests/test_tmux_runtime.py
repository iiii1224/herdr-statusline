import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest

from tests.helpers import ROOT, base_env, make_executable, write_protocol

RUNTIME = ROOT / "scripts/run-in-tmux"
DEFAULT_OPTIONS = [("status-interval", "3"), ("status-position", "top")]

FAKE_TMUX = """#!/bin/sh
python3 - "$@" <<'PY_TMUX'
import json, os, pathlib, subprocess, sys
args = sys.argv[1:]
log = pathlib.Path(os.environ['HSL_TEST_TMUX_LOG'])
with log.open('a') as stream:
    stream.write(json.dumps({
        'args': args,
        'tmux': os.environ.get('TMUX', ''),
        'term_present': 'TERM' in os.environ,
        'term': os.environ.get('TERM', ''),
    }) + '\\n')
if args == ['-V']:
    print(os.environ.get('HSL_TEST_TMUX_VERSION', 'tmux 3.7b'))
    raise SystemExit(0)
reject = os.environ.get('HSL_TEST_TMUX_REJECT', '')
if reject and reject in args and ('set-option' in args or 'bind-key' in args):
    print('rejected by the fake tmux', file=sys.stderr)
    raise SystemExit(1)
if 'new-session' in args:
    pathlib.Path(os.environ['HSL_TEST_COMMAND_FILE']).write_text(args[-1])
    raise SystemExit(0)
if 'attach-session' in args:
    command = pathlib.Path(os.environ['HSL_TEST_COMMAND_FILE']).read_text()
    env = os.environ.copy()
    env['TMUX'] = 'inner-socket'
    raise SystemExit(subprocess.run(command, shell=True, env=env).returncode)
raise SystemExit(0)
PY_TMUX
"""

FAKE_HERDR = """#!/bin/sh
python3 - "$@" <<'PY_HERDR'
import json, os, pathlib, sys
pathlib.Path(os.environ['HSL_TEST_HERDR_LOG']).write_text(json.dumps({
    'args': sys.argv[1:],
    'tmux': os.environ.get('TMUX', ''),
    'session_present': 'HERDR_SESSION' in os.environ,
    'session': os.environ.get('HERDR_SESSION', ''),
}))
message = os.environ.get('HSL_TEST_HERDR_STDERR', '')
if message:
    print(message, file=sys.stderr)
raise SystemExit(int(os.environ.get('HSL_TEST_HERDR_EXIT', '0')))
PY_HERDR
"""


# The real terminfo database is whatever the developer's machine happens to
# hold, so both lookup tools are faked and HSL_TEST_KNOWN_TERMS decides which
# entries exist. run-in-tmux calls them as `tput -T <term> longname` and
# `infocmp -- <term>`; nothing else is supported on purpose.
FAKE_TPUT = """#!/bin/sh
term=
while [ $# -gt 0 ]; do
    [ "$1" = -T ] && term=${2:-}
    shift
done
for known in ${HSL_TEST_KNOWN_TERMS:-}; do
    [ "$term" = "$known" ] || continue
    printf '%s\\n' "$known"
    exit 0
done
exit 3
"""

FAKE_INFOCMP = """#!/bin/sh
term=
for arg in "$@"; do
    [ "$arg" = "--" ] || term=$arg
done
for known in ${HSL_TEST_KNOWN_TERMS:-}; do
    [ "$term" = "$known" ] && exit 0
done
exit 1
"""


# Runs inside the pane, so $TMUX points at the disposable server. Waits for the
# status-interval=1 job to fire at least once, then records what the server
# actually holds.
SMOKE_HERDR = """#!/bin/sh
python3 - "$@" <<'PY_SMOKE'
import json, os, pathlib, subprocess, time

def option(*args):
    return subprocess.run(
        ['tmux', *args], text=True, capture_output=True
    ).stdout.strip()

time.sleep(2.5)
pathlib.Path(os.environ['HSL_TEST_HERDR_LOG']).write_text(json.dumps({
    'justify': option('show-options', '-g', 'status-justify'),
    'window_format': option('show-options', '-gw', 'window-status-format'),
    'automatic_rename': option('show-options', '-gw', 'automatic-rename'),
    'set_clipboard': option('show-options', '-s', 'set-clipboard'),
    'window_name': option('display-message', '-p', '#W'),
    'status': option('show-options', '-g', 'status'),
    'status_format_1': option('show-options', '-g', 'status-format[1]'),
    'root_keys': option('list-keys', '-T', 'root'),
}))
raise SystemExit(0)
PY_SMOKE
"""


class TmuxRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = pathlib.Path(self.tmp.name)
        self.fakebin = self.base / "bin"
        self.fakebin.mkdir()
        self.tmux_log = self.base / "tmux.jsonl"
        self.herdr_log = self.base / "herdr.json"
        self.command_file = self.base / "command"
        self.private_tmp = self.base / "tmp"
        self.private_tmp.mkdir()
        make_executable(self.fakebin / "tmux", FAKE_TMUX)
        make_executable(self.fakebin / "herdr", FAKE_HERDR)
        make_executable(self.fakebin / "tput", FAKE_TPUT)
        make_executable(self.fakebin / "infocmp", FAKE_INFOCMP)
        self.env = base_env(self.base / "home", self.fakebin)
        self.env.update(
            {
                "TMPDIR": str(self.private_tmp),
                # Pinned so the developer's own terminal cannot alter a run.
                "TERM": "xterm-256color",
                "HSL_TEST_KNOWN_TERMS": "xterm-256color xterm",
                "HSL_TMUX_BIN": str(self.fakebin / "tmux"),
                "HSL_HERDR_BIN": str(self.fakebin / "herdr"),
                "HSL_TEST_TMUX_LOG": str(self.tmux_log),
                "HSL_TEST_HERDR_LOG": str(self.herdr_log),
                "HSL_TEST_COMMAND_FILE": str(self.command_file),
                "HERDR_PLUGIN_CONFIG_DIR": "/cfg",
                "HERDR_SESSION": "research",
            }
        )

    def run_runtime(self, *args, options=None, mouse=False, **extra_env):
        env = self.env.copy()
        # A None value removes the variable, which no plain update() can do.
        for key, value in extra_env.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        if "HSL_STATUS_OPTIONS" not in extra_env:
            pairs = DEFAULT_OPTIONS if options is None else options
            env["HSL_STATUS_OPTIONS"] = str(
                write_protocol(self.base, pairs, mouse_clicks=mouse)
            )
        return subprocess.run(
            ["sh", str(RUNTIME), *args], cwd=ROOT, env=env, text=True, capture_output=True
        )

    def test_still_applies_status_options_after_the_protocol_shift(self):
        result = self.run_runtime("--session", "x")
        self.assertEqual(result.returncode, 0, result.stderr)
        applied = [
            args[args.index("set-option") + 1 :]
            for args in self.tmux_argv()
            if "set-option" in args
        ]
        self.assertIn(["-g", "status-interval", "3"], applied)
        self.assertIn(["-g", "status-position", "top"], applied)

    def test_rejects_a_protocol_whose_mouse_clicks_line_is_not_boolean(self):
        broken = self.base / "broken-options"
        broken.write_text("true\nmaybe\n0\n")
        result = self.run_runtime("--session", "x", HSL_STATUS_OPTIONS=str(broken))
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid hsl-config output", result.stderr)

    def test_rejects_an_old_writer_protocol(self):
        # Old writer, new runner. Line 2 of the old format is the option count,
        # so the boolean check rejects it before the line count is reached.
        old = self.base / "old-options"
        old.write_text("true\n1\nstatus-interval\n3\n")
        result = self.run_runtime("--session", "x", HSL_STATUS_OPTIONS=str(old))
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid hsl-config output", result.stderr)

    def test_rejects_a_protocol_whose_pairs_sit_at_the_old_offsets(self):
        # The other skew direction: a payload shaped for the old reader must
        # be refused rather than applied one line off.
        skewed = self.base / "skewed-options"
        skewed.write_text("true\nfalse\n1\nstatus-interval\n3\nstray\n")
        result = self.run_runtime("--session", "x", HSL_STATUS_OPTIONS=str(skewed))
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid hsl-config output", result.stderr)

    def tmux_calls(self):
        return [json.loads(line) for line in self.tmux_log.read_text().splitlines()]

    def tmux_argv(self):
        return [call["args"] for call in self.tmux_calls()]

    def herdr_record(self):
        return json.loads(self.herdr_log.read_text())

    def attach_record(self):
        # The client is what reads terminfo, so its environment is the contract.
        # A pane's own TERM comes from tmux's default-terminal, not from here.
        for call in self.tmux_calls():
            if "attach-session" in call["args"]:
                return call
        self.fail("attach-session was never called")

    def sockets(self):
        found = []
        for args in self.tmux_argv():
            if "new-session" in args:
                found.append(args[args.index("-L") + 1])
        return found

    def test_preserves_spaces_quotes_globs_and_trailing_newlines(self):
        args = ["plain", "value with space", "quote'\"", "*?[x]", "trailing\n\n"]
        result = self.run_runtime(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.herdr_record()["args"], args)

    def test_uses_a_unique_socket_and_a_dotless_session_name(self):
        self.run_runtime("--session", "one")
        self.run_runtime("--session", "two")
        sockets = self.sockets()
        self.assertEqual(len(sockets), 2)
        self.assertNotEqual(sockets[0], sockets[1])
        for args in self.tmux_argv():
            if "-s" in args:
                session = args[args.index("-s") + 1]
                self.assertNotIn(".", session, "a dot breaks tmux -t targeting")

    def test_isolates_the_outer_tmux_but_keeps_the_inner_one(self):
        result = self.run_runtime("--session", "x", TMUX="outer-socket,1,2")
        self.assertEqual(result.returncode, 0, result.stderr)
        for call in self.tmux_calls():
            if "new-session" in call["args"] or "attach-session" in call["args"]:
                self.assertEqual(call["tmux"], "", "outer TMUX must be unset")
        self.assertEqual(self.herdr_record()["tmux"], "inner-socket")

    # The symptom being prevented: a tmux client aborts with "missing or
    # unsuitable terminal: $TERM" when the outer TERM has no terminfo entry.
    def test_replaces_an_unknown_term_with_a_known_fallback(self):
        result = self.run_runtime(TERM="xterm-rio")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.attach_record()["term"], "xterm-256color")
        self.assertIn("xterm-rio", result.stderr)
        self.assertIn("xterm-256color", result.stderr)

    def test_keeps_a_term_the_terminfo_database_knows(self):
        result = self.run_runtime(TERM="xterm")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.attach_record()["term"], "xterm")
        self.assertNotIn("no terminfo entry", result.stderr)

    def test_keeps_an_unknown_term_when_no_fallback_is_verifiable(self):
        result = self.run_runtime(TERM="xterm-rio", HSL_TEST_KNOWN_TERMS="")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.attach_record()["term"], "xterm-rio")

    def test_prefers_the_configured_fallback_term(self):
        result = self.run_runtime(
            TERM="xterm-rio",
            HSL_TEST_KNOWN_TERMS="rio xterm-256color",
            HSL_FALLBACK_TERM="rio",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.attach_record()["term"], "rio")

    def test_ignores_a_configured_fallback_the_system_does_not_know(self):
        result = self.run_runtime(TERM="xterm-rio", HSL_FALLBACK_TERM="nonesuch")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.attach_record()["term"], "xterm-256color")

    def test_leaves_an_absent_term_absent(self):
        # No TERM at all is a non-interactive caller's business, not a
        # terminal hsl should invent one for.
        result = self.run_runtime(TERM=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.attach_record()["term_present"])

    def test_returns_the_herdr_exit_status_and_cleans_up(self):
        result = self.run_runtime(HSL_TEST_HERDR_EXIT="37")
        self.assertEqual(result.returncode, 37)
        leftovers = [p.name for p in self.private_tmp.iterdir()]
        self.assertEqual(leftovers, [], f"runtime directories leaked: {leftovers}")

    def test_applies_each_option_with_the_prefix_derived_scope(self):
        result = self.run_runtime(
            options=[
                ("status-justify", "centre"),
                ("status-left", " #(echo hi) "),
                ("window-status-format", " #I: #W "),
                ("status-left-length", "40"),
            ]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        tails = [args[-4:] for args in self.tmux_argv()]
        self.assertIn(["set-option", "-g", "status-justify", "centre"], tails)
        self.assertIn(["set-option", "-g", "status-left", " #(echo hi) "], tails)
        self.assertIn(["set-option", "-g", "status-left-length", "40"], tails)
        self.assertIn(["set-option", "-gw", "window-status-format", " #I: #W "], tails)

    def test_passes_format_characters_through_untouched(self):
        raw = "#[fg=red]#{client_width}#(id)%H:%M$HOME 100%"
        result = self.run_runtime(options=[("status-right", raw)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            ["set-option", "-g", "status-right", raw],
            [args[-4:] for args in self.tmux_argv()],
        )

    def test_applies_the_options_in_protocol_order(self):
        # tmux folds status-bg into status-style; reordering changes the bar.
        self.run_runtime(
            options=[("status-style", "bg=colour238"), ("status-bg", "colour241")]
        )
        names = [args[-2] for args in self.tmux_argv() if args[-4:-3] == ["set-option"]]
        self.assertLess(names.index("status-style"), names.index("status-bg"))

    def test_applies_an_indexed_status_format_to_the_session(self):
        # status-format is a session option, so the name must route to -g even
        # though every other multi-line concern is a window option.
        self.run_runtime(
            options=[("status", "2"), ("status-format[1]", "#[align=left]second")]
        )
        tails = [args[-4:] for args in self.tmux_argv()]
        self.assertIn(["set-option", "-g", "status", "2"], tails)
        self.assertIn(
            ["set-option", "-g", "status-format[1]", "#[align=left]second"], tails
        )

    def test_never_takes_over_the_status_format(self):
        self.run_runtime()
        for args in self.tmux_argv():
            for value in args:
                self.assertNotIn("status-format", value)

    def test_puts_the_config_directory_in_the_tmux_environment(self):
        # `#(...)` jobs read it to find helper scripts.
        result = self.run_runtime()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            ["set-environment", "-g", "HERDR_PLUGIN_CONFIG_DIR", "/cfg"],
            [args[-4:] for args in self.tmux_argv()],
        )

    def test_names_the_window_so_the_window_list_is_stable(self):
        self.run_runtime()
        for args in self.tmux_argv():
            if "new-session" in args:
                self.assertEqual(args[args.index("-n") + 1], "herdr")
                break
        else:
            self.fail("new-session was never called")

    def test_stops_without_starting_herdr_when_tmux_rejects_an_option(self):
        result = self.run_runtime(
            options=[("status-style", "bogus")], HSL_TEST_TMUX_REJECT="status-style"
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot apply statusline option status-style", result.stderr)
        self.assertIn("rejected by the fake tmux", result.stderr)
        self.assertFalse(self.herdr_log.exists())
        self.assertNotIn(
            ["wait-for", "-S", "hsl-start"], [args[-3:] for args in self.tmux_argv()]
        )
        self.assertEqual(list(self.private_tmp.iterdir()), [])

    def test_rejects_a_protocol_whose_count_does_not_match(self):
        options = self.base / "truncated"
        # Hand-written on purpose: this is a format *violation*, so the real
        # writer cannot produce it. Every valid fixture goes through
        # helpers.write_protocol.
        options.write_text("true\n2\nstatus-interval\n1\n")
        result = self.run_runtime(HSL_STATUS_OPTIONS=str(options))
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid hsl-config output", result.stderr)
        self.assertFalse(self.herdr_log.exists())

    def test_removes_the_options_file_on_a_normal_exit(self):
        options = write_protocol(self.base, DEFAULT_OPTIONS)
        self.run_runtime(HSL_STATUS_OPTIONS=str(options))
        self.assertFalse(options.exists())

    def test_removes_the_options_file_when_tmux_is_missing(self):
        options = write_protocol(self.base, DEFAULT_OPTIONS)
        result = self.run_runtime(
            HSL_STATUS_OPTIONS=str(options),
            HSL_TMUX_BIN=str(self.base / "no-such-tmux"),
        )
        self.assertEqual(result.returncode, 127)
        self.assertFalse(options.exists())

    def test_runs_with_no_options_at_all(self):
        result = self.run_runtime(options=[])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.herdr_log.exists())

    def test_forwards_a_named_session_to_the_tmux_environment(self):
        result = self.run_runtime("--session", "research")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            ["set-environment", "-g", "HERDR_SESSION", "research"],
            [args[-4:] for args in self.tmux_argv()],
        )
        self.assertEqual(self.herdr_record()["session"], "research")

    def test_never_hands_herdr_an_empty_session_name(self):
        # An exported HERDR_SESSION="" makes herdr exit 2 with "session name
        # cannot be empty" before it draws anything, so the pane dies and the
        # user sees nothing but tmux's [exited].
        result = self.run_runtime(HERDR_SESSION="")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.herdr_record()["session_present"])
        for args in self.tmux_argv():
            self.assertNotEqual(args[-4:-1], ["set-environment", "-g", "HERDR_SESSION"])

    def test_replays_the_wrapped_stderr_when_herdr_fails(self):
        result = self.run_runtime(
            HSL_TEST_HERDR_EXIT="2",
            HSL_TEST_HERDR_STDERR="error: session name cannot be empty",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("error: session name cannot be empty", result.stderr)

    def test_stays_quiet_when_the_wrapped_command_succeeds(self):
        result = self.run_runtime(HSL_TEST_HERDR_STDERR="harmless chatter")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("harmless chatter", result.stderr)

    def test_releases_the_start_barrier_after_configuration_and_before_attach(self):
        result = self.run_runtime()
        self.assertEqual(result.returncode, 0, result.stderr)
        argv = self.tmux_argv()
        configured = next(
            index
            for index, args in enumerate(argv)
            if args[-4:] == ["set-option", "-g", "status-position", "top"]
        )
        released = next(
            index
            for index, args in enumerate(argv)
            if args[-3:] == ["wait-for", "-S", "hsl-start"]
        )
        attached = next(
            index for index, args in enumerate(argv) if "attach-session" in args
        )
        self.assertLess(configured, released)
        self.assertLess(released, attached)
        self.assertTrue(
            any(args[-2:] == ["wait-for", "hsl-start"] for args in argv),
            "launch.sh must wait on the matching barrier",
        )

    def test_base_conf_sets_the_two_stage_destroy_policy(self):
        base = (ROOT / "tmux/base.conf").read_text()
        self.assertIn("set-option -g destroy-unattached off", base)
        self.assertIn("client-attached", base)
        self.assertNotIn("allow-passthrough", base)
        self.assertIn("set-option -gw automatic-rename off", base)
        self.assertNotIn("status-justify", base)
        self.assertNotIn("status-style", base)

    def test_base_conf_enables_osc52_clipboard(self):
        base = (ROOT / "tmux/base.conf").read_text()
        self.assertIn("set-option -s set-clipboard on", base)

    def test_signal_handlers_prefer_a_recorded_herdr_status(self):
        runtime = RUNTIME.read_text()
        self.assertIn("if final_status=$(recorded_status); then", runtime)
        self.assertIn("trap 'on_signal 129' HUP", runtime)
        self.assertIn("trap 'on_signal 130' INT", runtime)
        self.assertIn("trap 'on_signal 143' TERM", runtime)

    def test_reports_a_clear_error_when_tmux_is_missing(self):
        result = self.run_runtime(HSL_TMUX_BIN=str(self.base / "no-such-tmux"))
        self.assertEqual(result.returncode, 127)
        self.assertIn("tmux is required", result.stderr)
        self.assertFalse(self.herdr_log.exists())

    def test_concurrent_invocations_do_not_collide(self):
        processes = []
        for index in range(3):
            env = self.env.copy()
            env["HSL_TEST_TMUX_LOG"] = str(self.base / f"tmux-{index}.jsonl")
            env["HSL_TEST_HERDR_LOG"] = str(self.base / f"herdr-{index}.json")
            env["HSL_TEST_COMMAND_FILE"] = str(self.base / f"command-{index}")
            processes.append(
                subprocess.Popen(
                    ["sh", str(RUNTIME), "--session", f"s{index}"],
                    cwd=ROOT,
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            )
        for index, process in enumerate(processes):
            process.communicate()
            self.assertEqual(process.returncode, 0)
            record = json.loads((self.base / f"herdr-{index}.json").read_text())
            self.assertEqual(record["args"], ["--session", f"s{index}"])
        self.assertEqual(list(self.private_tmp.iterdir()), [])

    def wire(self, *, hook="#!/bin/sh\nexit 0\n", executable=True,
             config_dir=True, version="tmux 3.7b", reject=None):
        """Run the runtime with mouse_clicks on, returning (result, argv)."""
        cfg = self.base / "cfg"
        if hook is not None:
            cfg.mkdir(parents=True, exist_ok=True)
            path = cfg / "on-click.sh"
            path.write_text(hook)
            path.chmod(0o700 if executable else 0o600)
        env = {"HSL_TEST_TMUX_VERSION": version}
        if reject is not None:
            env["HSL_TEST_TMUX_REJECT"] = reject
        env["HERDR_PLUGIN_CONFIG_DIR"] = str(cfg) if config_dir else ""
        result = self.run_runtime("--session", "x", mouse=True, **env)
        return result, self.tmux_argv()

    def mouse_option_calls(self, argv):
        return [a for a in argv if "set-option" in a and "mouse" in a]

    def bind_calls(self, argv):
        return [a for a in argv if "bind-key" in a]

    def hook_option_calls(self, argv):
        return [a for a in argv if "set-option" in a and "@hsl_on_click" in a]

    def assert_no_mouse_changes(self, argv):
        """Nothing mouse-specific was touched.

        Checking only the mouse option and the bindings would still pass if
        @hsl_on_click were set before the guard that declined, so the user
        option is part of the contract too.
        """
        self.assertEqual(self.bind_calls(argv), [])
        self.assertEqual(self.mouse_option_calls(argv), [])
        self.assertEqual(self.hook_option_calls(argv), [])

    def test_wires_exactly_four_status_bindings(self):
        result, argv = self.wire()
        self.assertEqual(result.returncode, 0, result.stderr)
        binds = self.bind_calls(argv)
        self.assertEqual(len(binds), 4)
        self.assertEqual(
            sorted(a[a.index("bind-key") + 2] for a in binds),
            ["MouseDown1Status", "MouseDown3Status",
             "WheelDownStatus", "WheelUpStatus"],
        )
        self.assertEqual(
            sorted(a[-1].split()[1] for a in binds),
            ["left", "right", "wheeldown", "wheelup"],
        )
        for args in binds:
            command = args[-1]
            self.assertIn("-b", args)
            self.assertIn("#{q:@hsl_on_click}", command)
            self.assertIn("#{q:mouse_status_range}", command)
            self.assertIn("#{q:mouse_x}", command)
            self.assertIn("#{q:mouse_status_line}", command)
            self.assertIn(">/dev/null 2>&1 || true", command)
            # Hand-quoting a format is the injection bug; only #{q:} may appear.
            self.assertNotIn("'#{", command)
        self.assertEqual(len(self.mouse_option_calls(argv)), 1)
        self.assertIn(
            ["set-option", "-g", "@hsl_on_click",
             str(self.base / "cfg" / "on-click.sh")],
            [a[a.index("set-option"):] for a in argv if "set-option" in a],
        )

    def test_leaves_the_mouse_off_when_the_hook_is_missing(self):
        result, argv = self.wire(hook=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("mouse clicks stay off", result.stderr)
        self.assert_no_mouse_changes(argv)

    def test_leaves_the_mouse_off_when_the_hook_is_not_executable(self):
        result, argv = self.wire(executable=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("is not executable", result.stderr)
        self.assert_no_mouse_changes(argv)

    def test_leaves_the_mouse_off_when_the_config_dir_is_unknown(self):
        result, argv = self.wire(config_dir=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("plugin config dir is unknown", result.stderr)
        self.assert_no_mouse_changes(argv)

    def test_leaves_the_mouse_off_below_tmux_3_4(self):
        for version in ("tmux 3.3a", "tmux 3.0", "tmux 2.9", "tmux 1.8"):
            with self.subTest(version=version):
                self.tmux_log.write_text("")
                result, argv = self.wire(version=version)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("tmux 3.4", result.stderr)
                self.assert_no_mouse_changes(argv)
                # Ordinary status options are unrelated and still apply.
                self.assertTrue(
                    [a for a in argv
                     if "set-option" in a and "status-interval" in a]
                )

    def test_enables_the_mouse_on_tmux_3_4_and_newer(self):
        for version in ("tmux 3.4", "tmux 3.7b", "tmux 4.0",
                        "tmux next-3.9", "weird output"):
            with self.subTest(version=version):
                self.tmux_log.write_text("")
                result, argv = self.wire(version=version)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(self.bind_calls(argv)), 4)

    def test_fails_closed_when_the_hook_option_is_rejected(self):
        result, _ = self.wire(reject="@hsl_on_click")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.herdr_log.exists(), "herdr must not start")

    def test_fails_closed_when_the_mouse_option_is_rejected(self):
        result, _ = self.wire(reject="mouse")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.herdr_log.exists(), "herdr must not start")

    def test_fails_closed_when_any_binding_is_rejected(self):
        for key in ("MouseDown1Status", "MouseDown3Status",
                    "WheelUpStatus", "WheelDownStatus"):
            with self.subTest(key=key):
                self.tmux_log.write_text("")
                result, _ = self.wire(reject=key)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(self.herdr_log.exists(),
                                 "herdr must not start")


class RealTmuxSmokeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("tmux"), "tmux is not installed")
    @unittest.skipUnless(shutil.which("script"), "util-linux script is not installed")
    def test_starts_and_exits_a_real_disposable_server(self):
        import shlex

        with tempfile.TemporaryDirectory() as name:
            base = pathlib.Path(name)
            fakebin = base / "bin"
            fakebin.mkdir()
            log = base / "herdr.json"
            options = write_protocol(base, [("status-interval", "1")])
            make_executable(fakebin / "herdr", FAKE_HERDR)
            env = base_env(base / "home", fakebin)
            env.update(
                {
                    "HSL_HERDR_BIN": str(fakebin / "herdr"),
                    "HSL_TEST_HERDR_LOG": str(log),
                    "HSL_TEST_HERDR_EXIT": "41",
                    "HSL_STATUS_OPTIONS": str(options),
                    "HERDR_PLUGIN_CONFIG_DIR": str(base / "cfg"),
                    "HERDR_SESSION": "smoke",
                    "TMPDIR": str(base),
                }
            )
            if env.get("TERM", "dumb") == "dumb":
                env["TERM"] = "xterm-256color"
            inner = shlex.join(["sh", str(RUNTIME), "--session", "smoke"])
            result = subprocess.run(
                ["script", "-qec", f"stty rows 40 cols 120; {inner}", "/dev/null"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 41, result.stdout[-2000:])
            self.assertEqual(json.loads(log.read_text())["args"], ["--session", "smoke"])
            self.assertEqual(
                [p.name for p in base.glob("herdr-statusline.*")],
                [],
                "the runtime directory must be removed",
            )

    @unittest.skipUnless(shutil.which("tmux"), "tmux is not installed")
    @unittest.skipUnless(shutil.which("script"), "util-linux script is not installed")
    def test_a_real_server_applies_options_and_feeds_the_status_job(self):
        import shlex

        with tempfile.TemporaryDirectory() as name:
            base = pathlib.Path(name)
            fakebin = base / "bin"
            fakebin.mkdir()
            log = base / "herdr.json"
            seen = base / "seen"
            job = base / "job.sh"
            # No `%` anywhere: status-left is passed through strftime first.
            make_executable(
                job, f'#!/bin/sh\nprintf "[$HERDR_SESSION]" > "{seen}"\necho hi\n'
            )
            make_executable(fakebin / "herdr", SMOKE_HERDR)

            options = write_protocol(
                base,
                [
                    ("status-interval", "1"),
                    ("status-justify", "centre"),
                    ("status-left", f"#({job})"),
                    ("window-status-format", " #I: #W "),
                ],
            )

            env = base_env(base / "home", fakebin)
            env.update(
                {
                    "HSL_HERDR_BIN": str(fakebin / "herdr"),
                    "HSL_TEST_HERDR_LOG": str(log),
                    "HSL_STATUS_OPTIONS": str(options),
                    "HERDR_PLUGIN_CONFIG_DIR": str(base / "cfg"),
                    "HERDR_SESSION": "smoke",
                    "TMPDIR": str(base),
                }
            )
            if env.get("TERM", "dumb") == "dumb":
                env["TERM"] = "xterm-256color"
            inner = shlex.join(["sh", str(RUNTIME), "--session", "smoke"])
            result = subprocess.run(
                ["script", "-qec", f"stty rows 40 cols 120; {inner}", "/dev/null"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout[-2000:])

            record = json.loads(log.read_text())
            # tmux quotes show-options values that have leading/trailing
            # spaces, so parse with shlex instead of comparing raw strings.
            self.assertEqual(
                shlex.split(record["justify"]), ["status-justify", "centre"]
            )
            self.assertEqual(
                shlex.split(record["window_format"]),
                ["window-status-format", " #I: #W "],
            )
            self.assertEqual(
                shlex.split(record["automatic_rename"]),
                ["automatic-rename", "off"],
            )
            self.assertEqual(
                shlex.split(record["set_clipboard"]),
                ["set-clipboard", "on"],
            )
            self.assertEqual(record["window_name"], "herdr")

            # `unbind-key -a` clears only the prefix table. tmux keeps 24
            # default mouse bindings in root, inert while the mouse is off but
            # ready to take copy-mode, the pane context menu, the kill-pane
            # menu and border resize away from Herdr the moment it goes on.
            self.assertEqual(record["root_keys"], "")

            # The job ran, which means status-format still composes status-left,
            # and it saw the session name from the tmux environment.
            self.assertTrue(seen.exists(), "the status-left job never ran")
            self.assertEqual(seen.read_text(), "[smoke]")

            self.assertFalse(options.exists(), "the options file must be removed")
            self.assertEqual(
                [p.name for p in base.glob("herdr-statusline.*")],
                [],
                "the runtime directory must be removed",
            )

    @unittest.skipUnless(shutil.which("tmux"), "tmux is not installed")
    @unittest.skipUnless(shutil.which("script"), "util-linux script is not installed")
    def test_a_real_server_draws_and_feeds_a_second_status_line(self):
        import shlex

        with tempfile.TemporaryDirectory() as name:
            base = pathlib.Path(name)
            fakebin = base / "bin"
            fakebin.mkdir()
            log = base / "herdr.json"
            seen = base / "seen"
            job = base / "line1.sh"
            # No `%` anywhere: the format is passed through strftime first.
            make_executable(
                job, f'#!/bin/sh\nprintf "[$HERDR_SESSION]" > "{seen}"\necho line1\n'
            )
            make_executable(fakebin / "herdr", SMOKE_HERDR)

            options = write_protocol(
                base,
                [
                    ("status-interval", "1"),
                    ("status", "2"),
                    ("status-format[1]", f"#[align=left]#({job})"),
                ],
            )

            env = base_env(base / "home", fakebin)
            env.update(
                {
                    "HSL_HERDR_BIN": str(fakebin / "herdr"),
                    "HSL_TEST_HERDR_LOG": str(log),
                    "HSL_STATUS_OPTIONS": str(options),
                    "HERDR_PLUGIN_CONFIG_DIR": str(base / "cfg"),
                    "HERDR_SESSION": "smoke",
                    "TMPDIR": str(base),
                }
            )
            if env.get("TERM", "dumb") == "dumb":
                env["TERM"] = "xterm-256color"
            inner = shlex.join(["sh", str(RUNTIME), "--session", "smoke"])
            result = subprocess.run(
                ["script", "-qec", f"stty rows 40 cols 120; {inner}", "/dev/null"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout[-2000:])

            record = json.loads(log.read_text())
            self.assertEqual(shlex.split(record["status"]), ["status", "2"])
            self.assertEqual(
                shlex.split(record["status_format_1"]),
                ["status-format[1]", f"#[align=left]#({job})"],
            )

            # The job ran, which means tmux drew the second line, and it saw
            # the session name from the tmux environment just as a status-left
            # job does.
            self.assertTrue(seen.exists(), "the status-format[1] job never ran")
            self.assertEqual(seen.read_text(), "[smoke]")

            self.assertFalse(options.exists(), "the options file must be removed")
            self.assertEqual(
                [p.name for p in base.glob("herdr-statusline.*")],
                [],
                "the runtime directory must be removed",
            )

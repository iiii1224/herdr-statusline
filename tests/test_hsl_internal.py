import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest

from tests.helpers import ROOT, base_env, ensure_helper, make_executable

INTERACTIVE = [
    (),
    ("--session", "research"),
    ("--session=research",),
    ("--remote", "host"),
    ("--remote=host",),
    ("--no-session",),
    ("session", "attach", "research"),
    ("agent", "attach", "agent-1"),
    ("terminal", "attach", "terminal-1"),
    ("--handoff", "--remote", "host"),
    ("--handoff", "--remote=host"),
    ("--remote-keybindings", "local", "--remote", "host"),
    ("--remote-keybindings=local", "--remote", "host"),
    # The --remote=* arm of the gate: without it these fall through to a
    # pass-through and the status line silently disappears.
    ("--remote-keybindings", "local", "--remote=host"),
    ("--remote-keybindings=local", "--remote=host"),
]
DIRECT = [
    ("--help",),
    ("--version",),
    ("completion", "bash"),
    ("plugin", "list"),
    ("config", "check"),
    ("status",),
    ("workspace", "list"),
    ("pane", "list"),
    ("server",),
    ("api", "call"),
    ("session", "list"),
    ("agent", "list"),
    ("terminal", "list"),
    ("future-command", "value with space"),
    # herdr takes --handoff and --remote-keybindings only alongside --remote:
    # without one it answers `unknown option: --handoff` and
    # `--remote-keybindings requires --remote`. Wrapping these would spend a
    # tmux server on a command that dies immediately.
    ("--remote-keybindings", "local", "--session", "x"),
    ("--remote-keybindings", "local", "--no-session"),
    ("--remote-keybindings=local", "--session", "x"),
    ("--handoff", "--session", "research"),
    ("--handoff", "--session=research"),
    ("--handoff", "--no-session"),
    ("--handoff", "plugin", "list"),
    ("--remote-keybindings", "local", "status"),
    # herdr rejects each of these; hsl must not start a tmux server for them.
    ("--handoff",),
    ("--remote-keybindings", "local"),
    ("--handoff", "session", "attach", "research"),
]

FAKE_HERDR = """#!/bin/sh
python3 - "$@" <<'PY'
import json, os, pathlib, sys
args = sys.argv[1:]
with pathlib.Path(os.environ['HSL_TEST_HERDR_CALLS']).open('a') as stream:
    stream.write(json.dumps(args) + '\\n')
if args[:2] == ['plugin', 'list'] and '--plugin' in args:
    state = pathlib.Path(os.environ['HSL_TEST_STATE_FILE']).read_text().strip()
    plugins = [] if state == 'not-installed' else [{
        'plugin_id': 'herdr-statusline',
        'plugin_root': os.environ['HSL_TEST_PLUGIN_ROOT'],
        'enabled': state == 'enabled',
    }]
    print(json.dumps({'id': 'cli:plugin',
                      'result': {'plugins': plugins, 'type': 'plugin_list'}}))
    raise SystemExit(int(os.environ.get('HSL_TEST_PLUGIN_LIST_EXIT', '0')))
if args == ['plugin', 'config-dir', 'herdr-statusline']:
    print(os.environ['HSL_TEST_CONFIG_DIR'])
    raise SystemExit(0)
pathlib.Path(os.environ['HSL_TEST_HERDR_RUN']).write_text(json.dumps(args))
PY
"""

FAKE_RUNTIME = """#!/bin/sh
python3 - "$@" <<'PY'
import json, os, pathlib, sys
keys = ('HERDR_PLUGIN_CONFIG_DIR', 'HERDR_SESSION', 'HSL_STATUS_OPTIONS')
options = os.environ.get('HSL_STATUS_OPTIONS', '')
pathlib.Path(os.environ['HSL_TEST_RUNTIME_LOG']).write_text(json.dumps({
    'args': sys.argv[1:],
    'env': {key: os.environ.get(key, '') for key in keys},
    'present': [key for key in keys if key in os.environ],
    'options': pathlib.Path(options).read_text() if options else '',
}))
PY
"""


class InternalLauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_helper()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = pathlib.Path(self.tmp.name)
        self.root = self.base / "plugin"
        shutil.copytree(
            ROOT, self.root, ignore=shutil.ignore_patterns(".git", "target", "__pycache__")
        )
        helper = self.root / "target/release/hsl-config"
        helper.parent.mkdir(parents=True)
        shutil.copy2(ROOT / "target/release/hsl-config", helper)
        helper.chmod(0o755)
        make_executable(self.root / "scripts/run-in-tmux", FAKE_RUNTIME)

        self.fakebin = self.base / "bin"
        self.fakebin.mkdir()
        make_executable(self.fakebin / "herdr", FAKE_HERDR)
        self.home = self.base / "home"
        self.config_dir = self.home / ".config/herdr/plugins/config/herdr-statusline"
        self.state_file = self.base / "state"
        self.state_file.write_text("enabled")
        # Named distinctly from the "tmp" directory
        # test_config_disabled_removes_the_options_file manages itself below:
        # that test's leading warmup call doesn't override TMPDIR, so it would
        # leak its options file into this directory, and a same-named
        # directory would make that leak show up in that test's own
        # empty-directory assertion.
        self.private_tmp = self.base / "hsl-test-tmp"
        self.private_tmp.mkdir(exist_ok=True)
        self.env = base_env(self.home, self.fakebin)
        self.env.update(
            {
                "HSL_HERDR_BIN": str(self.fakebin / "herdr"),
                "HSL_TEST_HERDR_CALLS": str(self.base / "calls.jsonl"),
                "HSL_TEST_HERDR_RUN": str(self.base / "run.json"),
                "HSL_TEST_RUNTIME_LOG": str(self.base / "runtime.json"),
                "HSL_TEST_STATE_FILE": str(self.state_file),
                "HSL_TEST_PLUGIN_ROOT": str(self.root),
                "HSL_TEST_CONFIG_DIR": str(self.config_dir),
                "TMPDIR": str(self.private_tmp),
            }
        )

    def run_internal(self, *args, **extra_env):
        env = self.env.copy()
        env.update(extra_env)
        return subprocess.run(
            ["sh", str(self.root / "bin/hsl-internal"), *args],
            env=env,
            text=True,
            capture_output=True,
        )

    def reset_logs(self):
        for name in ("calls.jsonl", "run.json", "runtime.json"):
            (self.base / name).unlink(missing_ok=True)

    def herdr_calls(self):
        path = self.base / "calls.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def herdr_run_args(self):
        return json.loads((self.base / "run.json").read_text())

    def runtime_record(self):
        return json.loads((self.base / "runtime.json").read_text())

    def test_direct_commands_pass_through_without_querying_herdr(self):
        for args in DIRECT:
            with self.subTest(args=args):
                self.reset_logs()
                result = self.run_internal(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.herdr_run_args(), list(args))
                self.assertFalse((self.base / "runtime.json").exists())
                for call in self.herdr_calls():
                    if call == list(args):
                        continue
                    self.assertNotEqual(call[:2], ["plugin", "list"])
                    self.assertNotEqual(call[:2], ["plugin", "config-dir"])

    def test_interactive_commands_reach_the_tmux_runtime(self):
        for args in INTERACTIVE:
            with self.subTest(args=args):
                self.reset_logs()
                result = self.run_internal(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.runtime_record()["args"], list(args))

    def test_a_skipped_global_option_keeps_subcommand_attach_out_of_tmux(self):
        # herdr rejects `--handoff session attach x`, and cli_session reads the
        # subcommand form positionally from the original argv, so classifying
        # it as interactive would start tmux with HERDR_SESSION=default rather
        # than the requested name.
        result = self.run_internal("--handoff", "session", "attach", "research")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.herdr_run_args(), ["--handoff", "session", "attach", "research"]
        )
        self.assertFalse((self.base / "runtime.json").exists())

    def test_first_interactive_run_initializes_the_config_directory(self):
        self.run_internal("--session", "x")
        self.assertTrue((self.config_dir / "config.toml").is_file())
        self.assertTrue((self.config_dir / "herdr-info.sh").is_file())
        self.assertFalse((self.config_dir / "statusline.sh").exists())
        record = self.runtime_record()
        self.assertEqual(record["env"]["HERDR_PLUGIN_CONFIG_DIR"], str(self.config_dir))
        self.assertEqual(record["env"]["HERDR_SESSION"], "x")
        self.assertEqual(
            record["options"],
            "true\n2\nstatus-interval\n1\nstatus-right\n%m/%d %H:%M:%S\n",
        )

    def test_herdr_disabled_bypasses_config_and_tmux(self):
        self.state_file.write_text("disabled")
        result = self.run_internal("--session", "x")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.herdr_run_args(), ["--session", "x"])
        self.assertFalse((self.base / "runtime.json").exists())
        self.assertNotIn(
            ["plugin", "config-dir", "herdr-statusline"], self.herdr_calls()
        )

    def test_config_enabled_false_bypasses_tmux(self):
        self.run_internal("--session", "warmup")
        (self.config_dir / "config.toml").write_text("enabled = false\n")
        self.reset_logs()
        result = self.run_internal("--session", "x")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.herdr_run_args(), ["--session", "x"])
        self.assertFalse((self.base / "runtime.json").exists())

    def test_invalid_config_starts_neither_herdr_nor_tmux(self):
        self.run_internal("--session", "warmup")
        (self.config_dir / "config.toml").write_text("[statusline]\nprefix = \"C-b\"\n")
        self.reset_logs()
        result = self.run_internal()
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.base / "run.json").exists())
        self.assertFalse((self.base / "runtime.json").exists())

    def test_config_disabled_removes_the_options_file(self):
        self.run_internal("--session", "warmup")
        (self.config_dir / "config.toml").write_text("enabled = false\n")
        self.reset_logs()
        private_tmp = self.base / "tmp"
        private_tmp.mkdir(exist_ok=True)
        result = self.run_internal("--session", "x", TMPDIR=str(private_tmp))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.herdr_run_args(), ["--session", "x"])
        self.assertEqual(list(private_tmp.iterdir()), [])

    def test_unknown_key_is_rejected(self):
        self.run_internal("--session", "warmup")
        (self.config_dir / "config.toml").write_text("interval = 2\n")
        self.reset_logs()
        self.assertEqual(self.run_internal().returncode, 2)

    def test_not_installed_is_an_error_not_a_launcher_removal(self):
        self.state_file.write_text("not-installed")
        result = self.run_internal("--session", "x")
        self.assertEqual(result.returncode, 2)
        self.assertIn("not registered", result.stderr)

    def test_nonzero_plugin_list_is_an_error_even_with_valid_json(self):
        result = self.run_internal("--session", "x", HSL_TEST_PLUGIN_LIST_EXIT="19")
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot determine", result.stderr)
        self.assertFalse((self.base / "runtime.json").exists())

    def test_preserves_a_preexisting_herdr_session_value(self):
        self.run_internal("--no-session", HERDR_SESSION="ambient")
        self.assertEqual(self.runtime_record()["env"]["HERDR_SESSION"], "ambient")

    def test_reads_both_forms_of_the_session_flag(self):
        for args in (
            ("--session", "research"),
            ("--session=research",),
        ):
            with self.subTest(args=args):
                self.reset_logs()
                result = self.run_internal(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    self.runtime_record()["env"]["HERDR_SESSION"], "research"
                )

    def test_reads_the_session_attach_subcommand(self):
        result = self.run_internal("session", "attach", "research")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.runtime_record()["env"]["HERDR_SESSION"], "research")

    def test_leaves_herdr_session_unset_for_a_monolithic_run(self):
        # --no-session runs herdr with no persistent session, so there is no
        # name for the status line to show.
        result = self.run_internal("--no-session")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("HERDR_SESSION", self.runtime_record()["present"])

    def test_falls_back_to_the_default_session_name(self):
        for args in (
            (),
            ("agent", "attach", "agent-1"),
            ("terminal", "attach", "terminal-1"),
        ):
            with self.subTest(args=args):
                self.reset_logs()
                result = self.run_internal(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    self.runtime_record()["env"]["HERDR_SESSION"], "default"
                )

    def test_does_not_invent_a_name_for_a_remote_session(self):
        # The session lives on another host. hsl cannot know its name, and
        # guessing risks both a wrong label and steering the attach.
        for args in (
            ("--remote", "host"),
            ("--remote=host",),
            # --remote-keybindings must not satisfy the --remote exclusion by
            # prefix: skips_the_local_session matches --remote exactly and
            # --remote=* with an equals sign, so neither can catch it.
            ("--remote-keybindings", "local", "--remote", "host"),
        ):
            with self.subTest(args=args):
                self.reset_logs()
                result = self.run_internal(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("HERDR_SESSION", self.runtime_record()["present"])

    def test_an_ambient_session_outranks_the_default_fallback(self):
        result = self.run_internal(HERDR_SESSION="ambient")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.runtime_record()["env"]["HERDR_SESSION"], "ambient")

    def test_an_empty_session_flag_is_left_for_herdr_to_reject(self):
        # Substituting "default" here would hide the user's typo, and exporting
        # the empty string would stop herdr from starting at all.
        result = self.run_internal("--session=")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("HERDR_SESSION", self.runtime_record()["present"])

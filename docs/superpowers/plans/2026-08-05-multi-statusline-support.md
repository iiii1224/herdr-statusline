# Multi-line status line support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `config.toml` say what tmux draws on each status line, so `status = 2` becomes useful instead of merely legal.

**Architecture:** tmux draws line *n* from `status-format[n]` and has no per-line `status-left`, so that array is the only lever. `option_name` in `src/config.rs` gains one suffix rule — `status_format_N` becomes `status-format[N]` — and loses its outright rejection of `status-format`. Nothing downstream changes: the protocol writer, `bin/hsl-internal` and `scripts/run-in-tmux` all treat option names as opaque text.

**Tech Stack:** Rust 2021 (`serde`, `toml`), POSIX sh, Python 3 `unittest`, tmux 3.7b.

**Spec:** `docs/superpowers/specs/2026-08-05-multi-statusline-design.md`

## Global Constraints

- **Do not touch the prefix check** in `option_name` (`name == "status" || name.starts_with("status-") || name.starts_with("window-status-")`). It is the whole safety argument: it keeps `prefix`, key bindings, `mouse`, hooks, `destroy-unattached` and `remain-on-exit` unreachable so the disposable session's invariants cannot be configured away.
- **hsl must never set `status-format` on its own** — only when the user writes it. `tests/test_tmux_runtime.py::test_never_takes_over_the_status_format` guards this and must keep passing unchanged.
- **No validation beyond what tmux does.** A bare `status_format`, an index that is never drawn, and a `status` value tmux rejects all pass through unexamined. tmux is the authority on names and values (`src/config.rs:83`).
- **`README.md` must stay concise, not verbose** (`AGENTS.md`).
- **No new dependencies.** `Cargo.toml` is not modified.
- CI gates, all of which must pass: `cargo fmt --check`; `cargo clippy --all-targets --all-features -- -D warnings`; `cargo test`; `cargo build --release --locked`; `python3 -m unittest discover -s tests -v`.
- Python tests run from the repository root as `python3 -m unittest ...` (they import `tests.helpers`).

---

### Task 1: Map `status_format_N` to `status-format[N]`

**Files:**
- Modify: `src/config.rs:84-99` (`option_name`)
- Modify: `src/config.rs:200-204` (delete `rejects_status_format`)
- Test: `src/config.rs` (inline `mod tests`)

**Interfaces:**
- Consumes: nothing.
- Produces: `fn option_name(key: &str) -> Result<String, String>` now returns `status-format[N]` for a key of the form `status_format_N`, and returns `status-format` unchanged for a bare `status_format`. `NormalizedConfig.options` and `write_protocol` are untouched — they carry whatever string this returns.

- [ ] **Step 1: Delete the test that pins the old rejection**

Remove this test from `mod tests` in `src/config.rs` (currently lines 200-204):

```rust
    #[test]
    fn rejects_status_format() {
        let error = load_text("[statusline]\nstatus_format = \"x\"\n").unwrap_err();
        assert!(error.contains("status_format"), "{error}");
    }
```

- [ ] **Step 2: Write the failing tests**

Add these four tests to `mod tests` in `src/config.rs`, where `rejects_status_format` used to be. The existing `options()` helper returns `Vec<(String, String)>` of `(tmux option name, value)`.

```rust
    #[test]
    fn indexes_status_format_for_the_extra_status_lines() {
        assert_eq!(
            options("[statusline]\nstatus_format_1 = \"x\"\n")[0].0,
            "status-format[1]"
        );
    }

    #[test]
    fn carries_the_index_over_verbatim() {
        // tmux normalises `[01]` to `[1]` and rejects an index it cannot
        // parse, so nothing here parses one and nothing here can overflow.
        assert_eq!(
            options("[statusline]\nstatus_format_01 = \"x\"\n")[0].0,
            "status-format[01]"
        );
    }

    #[test]
    fn leaves_an_unindexed_status_format_to_tmux() {
        // tmux collapses the array to a single element, dropping its own
        // status-format[1] and [2]. That is tmux's behaviour to own, not ours
        // to veto.
        assert_eq!(
            options("[statusline]\nstatus_format = \"x\"\n")[0].0,
            "status-format"
        );
    }

    #[test]
    fn does_not_index_names_that_only_look_indexed() {
        // No tmux option in these two families ends in a digit, which is what
        // makes a bare suffix rule safe; these are the near misses.
        for (text, expected) in [
            (
                "[statusline]\nwindow_status_format = \"x\"\n",
                "window-status-format",
            ),
            ("[statusline]\nstatus_left_length = 20\n", "status-left-length"),
            ("[statusline]\nstatus_format_1_2 = \"x\"\n", "status-format-1-2"),
            ("[statusline]\nstatus_format_ = \"x\"\n", "status-format-"),
            ("[statusline]\nstatus_format_abc = \"x\"\n", "status-format-abc"),
        ] {
            assert_eq!(options(text)[0].0, expected, "{text:?}");
        }
    }
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cargo test --lib`

Expected: FAIL. `indexes_status_format_for_the_extra_status_lines`, `carries_the_index_over_verbatim` and `leaves_an_unindexed_status_format_to_tmux` all panic inside `options()` with `called Result::unwrap() on an Err value: "statusline option \"status_format...\" cannot be set: ..."`, because `option_name` still rejects the whole family. `does_not_index_names_that_only_look_indexed` fails the same way on its `status_format_*` rows.

- [ ] **Step 4: Write the implementation**

Replace `option_name` in `src/config.rs` (currently lines 84-99) with this. The doc comment above it (lines 77-83) is correct as it stands and must not change — it argues the boundary in terms of the prefixes alone.

```rust
fn option_name(key: &str) -> Result<String, String> {
    let name = key.replace('_', "-");
    if !(name == "status" || name.starts_with("status-") || name.starts_with("window-status-")) {
        return Err(format!(
            "unknown statusline option {key:?}: only `status`, `status_*` \
             and `window_status_*` can be set"
        ));
    }
    // `status_format_1` is tmux's `status-format[1]`, the format of the second
    // status line; TOML bare keys cannot hold brackets, hence the suffix. A
    // bare suffix rule cannot capture a real option because no name in these
    // two families ends in a digit. The digits are carried over verbatim:
    // tmux normalises `[01]` to `[1]` and rejects an index it cannot parse.
    if let Some(index) = name.strip_prefix("status-format-") {
        if !index.is_empty() && index.bytes().all(|byte| byte.is_ascii_digit()) {
            return Ok(format!("status-format[{index}]"));
        }
    }
    Ok(name)
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cargo test`

Expected: PASS, every test in the crate. In particular `rejects_names_outside_those_forms` must still pass — `prefix`, `mouse`, `statusbar`, `window_size` and `destroy_unattached` are still rejected by the untouched prefix check.

- [ ] **Step 6: Run the formatting and lint gates**

Run: `cargo fmt --check && cargo clippy --all-targets --all-features -- -D warnings`

Expected: both exit 0 with no output. If `cargo fmt --check` reports a diff, run `cargo fmt` and re-run.

- [ ] **Step 7: Commit**

```bash
git add src/config.rs
git commit -m "$(cat <<'EOF'
Map status_format_N to tmux status-format[N]

tmux draws line n of the bar from status-format[n] and offers no per-line
status-left, so the array was the only way to say what an extra line holds.
The old rejection guarded ergonomics, not the prefix-based safety boundary,
which is untouched.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Carry the indexed name through the runtime

**Files:**
- Modify: `tests/helpers.py:66-90` (`write_protocol`, and the module imports at the top)
- Modify: `tests/test_tmux_runtime.py` (add one test to `TmuxRuntimeTests`, after `test_applies_the_options_in_protocol_order`)

**Interfaces:**
- Consumes: `option_name` from Task 1, via the release binary that `tests/helpers.ensure_helper()` builds.
- Produces: `write_protocol(base, pairs, enabled=True)` now accepts a tmux name written with brackets, e.g. `("status-format[1]", "...")`, and spells it `status_format_1` in the generated `config.toml`. Task 3 relies on this.

Why this task exists: `write_protocol` builds a `config.toml` by mechanically swapping hyphens for underscores, so `status-format[1]` would become the key `status_format[1]`, which is not a legal TOML bare key. The helper has to learn the one spelling that is not a straight substitution.

- [ ] **Step 1: Write the failing test**

Add to `TmuxRuntimeTests` in `tests/test_tmux_runtime.py`, directly after `test_applies_the_options_in_protocol_order`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_tmux_runtime.TmuxRuntimeTests.test_applies_an_indexed_status_format_to_the_session -v`

Expected: FAIL with `subprocess.CalledProcessError` raised from `write_protocol`, because `hsl-config load` cannot parse the key `status_format[1] = "..."` and exits 2.

- [ ] **Step 3: Teach the helper the one non-substitution spelling**

In `tests/helpers.py`, add `re` to the imports at the top of the file:

```python
import json
import os
import pathlib
import re
import stat
import subprocess
```

Then, inside `write_protocol`, replace this line:

```python
        lines.append(f'{name.replace("-", "_")} = {json.dumps(value)}')
```

with:

```python
        # `status-format[1]` is written `status_format_1` in config.toml,
        # because a TOML bare key cannot hold brackets. Every other option is
        # the tmux name with hyphens swapped for underscores.
        key = re.sub(r"\[(\d+)\]$", r"_\1", name).replace("-", "_")
        lines.append(f"{key} = {json.dumps(value)}")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m unittest tests.test_tmux_runtime.TmuxRuntimeTests.test_applies_an_indexed_status_format_to_the_session -v`

Expected: PASS.

- [ ] **Step 5: Run the whole suite**

Run: `python3 -m unittest discover -s tests -v`

Expected: PASS. Every other caller of `write_protocol` passes names without brackets, so the regex leaves them untouched — `test_applies_the_options_in_protocol_order` and `test_passes_format_characters_through_untouched` in particular must be unaffected.

- [ ] **Step 6: Commit**

```bash
git add tests/helpers.py tests/test_tmux_runtime.py
git commit -m "$(cat <<'EOF'
Apply an indexed status-format to the tmux session

The protocol and run-in-tmux treat option names as opaque text, so the
bracketed name needs no plumbing; this pins that it arrives intact and takes
-g rather than -gw.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Prove a second line draws and feeds its job on a real server

**Files:**
- Modify: `tests/test_tmux_runtime.py:90-109` (`SMOKE_HERDR`)
- Modify: `tests/test_tmux_runtime.py:463` (`RealTmuxSmokeTests`), adding one test after `test_a_real_server_applies_options_and_feeds_the_status_job` at line 508

**Interfaces:**
- Consumes: `write_protocol` from Task 2, accepting `("status-format[1]", ...)`.
- Produces: nothing other tasks use.

Why this task exists: everything before it runs against a fake tmux. The feature is only worth building if tmux actually runs a `#(...)` job written straight into `status-format[1]` and shows its output; a real server is the only place that can be observed.

- [ ] **Step 1: Record the two new options from inside the pane**

`SMOKE_HERDR` runs as the fake herdr inside the real pane and reports what the server holds. Add two keys to its JSON payload. Replace the `pathlib.Path(...).write_text(json.dumps({...}))` call in `SMOKE_HERDR` (lines 100-106) with:

```python
pathlib.Path(os.environ['HSL_TEST_HERDR_LOG']).write_text(json.dumps({
    'justify': option('show-options', '-g', 'status-justify'),
    'window_format': option('show-options', '-gw', 'window-status-format'),
    'automatic_rename': option('show-options', '-gw', 'automatic-rename'),
    'set_clipboard': option('show-options', '-s', 'set-clipboard'),
    'window_name': option('display-message', '-p', '#W'),
    'status': option('show-options', '-g', 'status'),
    'status_format_1': option('show-options', '-g', 'status-format[1]'),
}))
```

The existing real-server test asserts on named keys only, so the two additions do not affect it.

- [ ] **Step 2: Write the failing test**

Add this method to `RealTmuxSmokeTests`, directly after `test_a_real_server_applies_options_and_feeds_the_status_job`:

```python
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
```

- [ ] **Step 3: Run the test**

Run: `python3 -m unittest tests.test_tmux_runtime -v -k second_status_line`

Expected: PASS. It is written after the implementation exists, so it does not go red first — it is the end-to-end proof, not a driver. If it is skipped, install `tmux` and `util-linux`'s `script` and run it again; a skip is not a pass here.

If it fails on `the status-format[1] job never ran`, the likely cause is the 2.5 s sleep in `SMOKE_HERDR` racing a slow first status draw, not the feature. Confirm by raising the sleep locally before changing anything else.

- [ ] **Step 4: Run the whole suite**

Run: `python3 -m unittest discover -s tests -v`

Expected: PASS, including the pre-existing `test_a_real_server_applies_options_and_feeds_the_status_job`.

- [ ] **Step 5: Commit**

```bash
git add tests/test_tmux_runtime.py
git commit -m "$(cat <<'EOF'
Prove a real server draws and feeds a second status line

A #(...) job written straight into status-format[1] runs, reaches the
terminal, and sees HERDR_SESSION from the tmux environment. The fake tmux
cannot show any of that, and the feature is pointless if it is untrue.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Document the extra lines and their traps

**Files:**
- Modify: `README.md:93` and insert a subsection after `README.md:96`
- Modify: `scripts/default-config.toml`

**Interfaces:**
- Consumes: the config spelling from Task 1.
- Produces: nothing other tasks use.

`scripts/default-config.toml` is embedded into the binary by `src/init.rs:87` with `include_str!` and is what `hsl` writes on first run, so editing it changes what users receive. `scripts/build.sh:33` already ships it; no packaging change is needed.

**Keep `README.md` concise — `AGENTS.md` requires it.** The subsection below is deliberately four bullets and one short example; do not expand it into prose.

- [ ] **Step 1: Drop the `status-format` exception from README.md**

Replace `README.md:93`:

```markdown
- Options under `[statusline]` map directly to tmux status settings (`status`, `status-*`, and `window-status-*`, except `status-format`). Write option names using underscores in `config.toml` (e.g., `status_style`, `window_status_format`), which are automatically converted to hyphens for tmux.
```

with:

```markdown
- Options under `[statusline]` map directly to tmux status settings (`status`, `status-*`, and `window-status-*`). Write option names using underscores in `config.toml` (e.g., `status_style`, `window_status_format`), which are automatically converted to hyphens for tmux.
```

- [ ] **Step 2: Add the multi-line subsection**

Insert this between the `### Statusline Options & Environment Variables` bullets (ending at `README.md:96`) and `### Herdr integration with the tmux status line` (`README.md:98`):

````markdown
### Multiple Status Lines

`status` takes `on`, `off`, or `2`–`5`. Lines past the first are drawn from tmux's `status-format[N]`, written `status_format_N`:

```toml
status = 2
status_format_1 = "#[align=left]#($HERDR_PLUGIN_CONFIG_DIR/herdr-info.sh)"
```

- Line 0 keeps tmux's default format, which is what composes `status_left`, `status_right`, `status_left_length` and the window list. Setting `status_format_0` replaces it, and those options stop having any effect.
- Without a `status_format_N` of your own, line 1 shows tmux's pane list and line 2 its session list — both near-empty in the single-pane session `hsl` creates.
- Each extra line takes a row from the `herdr` pane.
````

- [ ] **Step 3: Add the example to the generated config**

In `scripts/default-config.toml`, add these two lines to the end of the `# Other examples:` block, after the `window_status_current_format` line:

```toml
#   status = 2
#   status_format_1 = "#[align=left]#($HERDR_PLUGIN_CONFIG_DIR/herdr-info.sh)"
```

Then add this entry to the end of the `# Notes:` block, matching the existing indentation:

```toml
#   `status` takes on, off or 2-5. Lines past the first come from
#   status_format_N, tmux's status-format[N]. status_format_0 replaces the
#   default that composes status_left, status_right and the window list.
```

- [ ] **Step 4: Verify the shipped config still parses and still installs**

Run: `cargo test && python3 -m unittest discover -s tests -v`

Expected: PASS. `src/init.rs` embeds the file with `include_str!`, so a TOML syntax error would surface as a failure in the init tests; the added lines are comments and must not change behaviour.

- [ ] **Step 5: Read the README section back**

Run: `sed -n '90,125p' README.md`

Expected: the exception is gone from the bullet, the new subsection reads as four bullets and one example, and it sits between the environment-variable bullets and the `herdr-info.sh` section. Trim anything that grew past that — `AGENTS.md` requires the README stay concise.

- [ ] **Step 6: Commit**

```bash
git add README.md scripts/default-config.toml
git commit -m "$(cat <<'EOF'
Document multi-line status lines and their traps

status_format_0 silently disables status_left, status_right and the window
list, and an extra line left unconfigured shows tmux's near-empty pane list.
Both are surprising enough to name.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Final verification

- [ ] Run the full CI gate locally:

```bash
cargo fmt --check \
  && cargo clippy --all-targets --all-features -- -D warnings \
  && cargo test \
  && cargo build --release --locked \
  && python3 -m unittest discover -s tests -v
```

Expected: all five exit 0. Confirm the output shows `test_a_real_server_draws_and_feeds_a_second_status_line` as **ok**, not **skipped**.

- [ ] Confirm nothing was smuggled past the boundary:

```bash
grep -n 'starts_with("window-status-")' src/config.rs
```

Expected: the prefix check is present and unchanged.

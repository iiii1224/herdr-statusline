# Status line マウスクリック基盤 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** tmux status line 上のクリックを、外部リポジトリが所有する単一フック `on-click.sh` へ安全に配送する opt-in 基盤を追加する。

**Architecture:** `config.toml` のトップレベル `mouse_clicks` を `hsl-config` の行プロトコルに 2 行目として載せ、`run-in-tmux` がそれを読んで tmux を条件付きで配線する。`tmux/base.conf` は root キーテーブルを明示的に空にし、`mouse on` にしたとき tmux 標準のマウス binding が herdr の入力を奪わないようにする。フックへの引数搬送は tmux 自身の `#{q:...}` sh エスケーパのみで行う。

**Tech Stack:** Rust（`hsl-config`、serde/toml）、POSIX sh（`run-in-tmux`、`bin/hsl-internal`）、tmux 3.4+、Python unittest（統合テスト）

**設計仕様:** `docs/superpowers/specs/2026-08-10-statusline-mouse-foundation-design.md`（rev2）

**改訂:** rev2。codex による計画レビュー指摘 15 件を反映。タスク境界を「各コミットで全テストが緑」に引き直し、結合テストを本番の `scripts/run-in-tmux` を通す形へ作り替えた。

## Global Constraints

- **tmux 3.4 以上が必要。** `range=user` / `mouse_status_range` / `mouse_status_line` は `CHANGES FROM 3.3a TO 3.4` で追加（仕様 F15）。
- **フックへ渡す値は必ず tmux の `#{q:...}` を通す。** `run-shell` は文字列全体を tmux format 展開してから `/bin/sh -c` に渡すため、手でシングルクォートすると range 名からコマンドインジェクションが成立する（仕様 F7、15 バイトで実証済み）。`scripts/lib/shell-quote.sh` の `shell_quote` はこの経路では**使わない**。
- **binding は必ず `run-shell -b` + `>/dev/null 2>&1` + `|| true` の 3 点セット**（仕様 F9）。
- **pane 系の binding は追加しない。** root が空なら tmux が転送する（仕様 F4）。
- **`src/config.rs` の `option_name` allowlist は変更しない。**
- **`MouseDown2Status` と `MouseDown1StatusDefault` は配線しない。**
- **`set -e` に頼らない。** `apply_status_options` と `apply_mouse_clicks` は `if ! f; then` の条件文として呼ばれるため暗黙のエラー伝播が効かない。**すべての command substitution に明示的な失敗検査を付ける。**
- **各タスクのコミット時点で全テストが緑であること。** タスクを跨いで赤を残さない。
- **結合テストは本番の `scripts/run-in-tmux` を起動して検証する。** テスト内で `mouse on` や `bind-key` を再現してはならない。再現すると本番配線の誤りを見逃す。
- ベースライン: `cargo test` 44 件、`python3 -m unittest discover -s tests` 106 件が全通過。**pytest は入っていない。** CI は `python3 -m unittest discover -s tests -v`。
- コミットメッセージ末尾に `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` を付ける。

---

## File Structure

| ファイル | 役割 | 変更 |
| --- | --- | --- |
| `src/config.rs` | `mouse_clicks` のパースと行プロトコル出力 | Modify |
| `scripts/run-in-tmux` | プロトコル読み出し、バージョン判定、条件付き配線 | Modify |
| `tmux/base.conf` | root キーテーブルの明示クリア | Modify |
| `tests/helpers.py` | `write_protocol` に `mouse_clicks` を通す | Modify |
| `tests/test_hsl_internal.py` | プロトコル完全一致の期待値 | Modify |
| `tests/test_tmux_runtime.py` | fake tmux による argv 検証、root テーブル検証 | Modify |
| `tests/mouse_pty.py` | **新規** `run-in-tmux` を pty 上で起動し SGR を注入するフィクスチャ | Create |
| `tests/test_tmux_mouse.py` | **新規** 実 tmux でのクリック配送・pane 透過の結合テスト | Create |
| `scripts/default-config.toml` | `mouse_clicks` の説明コメント | Modify |
| `skills/customize-herdr-statusline/SKILL.md` | ボタンの作り方と制約 | Modify |

### タスク境界の根拠

writer と reader を別コミットにすると、`tests/helpers.py` が新プロトコルを書いた瞬間に
旧 `run-in-tmux` が 2 行目の `false` を count として読み、`test_tmux_runtime.py` が
全滅する。同様にバージョン判定だけを先に入れると `mouse on` を期待するテストが赤のまま
残る。よって **Task 1 は writer と reader を、Task 3 は判定と配線を、それぞれ 1 コミットに
まとめる**。

---

## Task 1: `mouse_clicks` をプロトコルの端から端まで通す

**Files:**
- Modify: `src/config.rs`
- Modify: `scripts/run-in-tmux`（`apply_status_options`）
- Modify: `tests/helpers.py`
- Modify: `tests/test_hsl_internal.py:217`
- Modify: `tests/test_tmux_runtime.py`

**Interfaces:**
- Consumes: なし
- Produces:
  - `NormalizedConfig.mouse_clicks: bool`
  - `hsl-config load` の出力順 `enabled\nmouse_clicks\ncount\nname\nvalue...`
  - `scripts/run-in-tmux` のシェル変数 `MOUSE_CLICKS`（`true` / `false`）— Task 3 が読む
  - `tests/helpers.write_protocol(base, pairs, enabled=True, mouse_clicks=False)`
  - `TmuxRuntimeTests.run_runtime(*args, options=None, mouse=False, **extra_env)`

- [ ] **Step 1: Rust の失敗テストを書く**

`src/config.rs` の `mod tests` に追加する。

```rust
    #[test]
    fn defaults_mouse_clicks_to_off_and_parses_both_values() {
        assert!(!load_text("enabled = true\n").unwrap().mouse_clicks);
        assert!(load_text("mouse_clicks = true\n").unwrap().mouse_clicks);
        assert!(!load_text("mouse_clicks = false\n").unwrap().mouse_clicks);
    }

    #[test]
    fn rejects_a_non_boolean_mouse_clicks() {
        assert!(load_text("mouse_clicks = \"on\"\n").is_err());
        assert!(load_text("mouse_clicks = 1\n").is_err());
    }
```

既存の `writes_the_variable_length_protocol` の期待値を書き換える。

```rust
    #[test]
    fn writes_the_variable_length_protocol() {
        let config = load_text(
            "enabled = false\nmouse_clicks = true\n\
             [statusline]\nstatus_interval = 2\nstatus_left = \" a \"\n",
        )
        .unwrap();
        let mut out = Vec::new();
        write_protocol(&config, &mut out).unwrap();
        assert_eq!(
            String::from_utf8(out).unwrap(),
            "false\ntrue\n2\nstatus-interval\n2\nstatus-left\n a \n"
        );
    }
```

- [ ] **Step 2: 失敗を確認**

Run: `cargo test --quiet`
Expected: FAIL。`no field 'mouse_clicks' on type 'NormalizedConfig'`。

- [ ] **Step 3: Rust を実装**

`RawConfig` にフィールドを足す。

```rust
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RawConfig {
    #[serde(default = "default_enabled")]
    enabled: bool,
    /// Opt-in for status-line mouse clicks. Off by default: turning the mouse
    /// on costs the outer terminal its native selection, because tmux then
    /// asks it for 1000/1002/1003/1006 reporting.
    #[serde(default)]
    mouse_clicks: bool,
    #[serde(default)]
    statusline: Statusline,
}
```

`NormalizedConfig` にも足す。

```rust
#[derive(Debug, PartialEq, Eq)]
pub struct NormalizedConfig {
    pub enabled: bool,
    pub mouse_clicks: bool,
    /// `(tmux option name, value)`, in document order.
    pub options: Vec<(String, String)>,
}
```

`normalize` の戻り値。

```rust
    Ok(NormalizedConfig {
        enabled: raw.enabled,
        mouse_clicks: raw.mouse_clicks,
        options,
    })
```

`write_protocol` に 2 行目を挿入。

```rust
pub fn write_protocol(config: &NormalizedConfig, mut out: impl Write) -> Result<(), String> {
    writeln!(out, "{}", config.enabled).map_err(|e| e.to_string())?;
    writeln!(out, "{}", config.mouse_clicks).map_err(|e| e.to_string())?;
    writeln!(out, "{}", config.options.len()).map_err(|e| e.to_string())?;
    for (name, value) in &config.options {
        writeln!(out, "{name}").map_err(|e| e.to_string())?;
        writeln!(out, "{value}").map_err(|e| e.to_string())?;
    }
    Ok(())
}
```

- [ ] **Step 4: Rust テストが通ることを確認**

Run: `cargo test --quiet`
Expected: PASS、44 → 46 件。

- [ ] **Step 5: Python ヘルパーと期待値を更新**

`tests/helpers.py`。

```python
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
```

以降は変更しない。

`tests/test_hsl_internal.py:217` の期待値。

```python
        self.assertEqual(
            record["options"],
            "true\nfalse\n2\nstatus-interval\n1\nstatus-right\n%m/%d %H:%M:%S\n",
        )
```

- [ ] **Step 6: `run_runtime` に `mouse` 引数を足す**

`tests/test_tmux_runtime.py`。

```python
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
```

- [ ] **Step 7: reader 側の失敗テストを書く**

`TmuxRuntimeTests` に追加する。

```python
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
```

- [ ] **Step 8: 失敗を確認**

Run: `python3 -m unittest tests.test_tmux_runtime -v`
Expected: FAIL が多数。`helpers.write_protocol` が新形式を書くのに reader が旧オフセット
のままなので既存テストも落ちる。これが「Task 1 で writer と reader を同時に直す」理由。

- [ ] **Step 9: reader を実装**

`scripts/run-in-tmux` の `STATUS_OPTIONS=${HSL_STATUS_OPTIONS:-}` の直後に既定値を置く。

```sh
# Set before apply_status_options runs so an absent options file leaves the
# feature off rather than unset. apply_mouse_clicks reads this.
MOUSE_CLICKS=false
```

`apply_status_options` を差し替える。**すべての command substitution に失敗検査を付ける**
（この関数は `if ! apply_status_options` から呼ばれ `set -e` は効かない）。

```sh
apply_status_options() {
    [ -n "$STATUS_OPTIONS" ] || return 0
    [ -f "$STATUS_OPTIONS" ] || {
        printf '%s\n' 'hsl: invalid hsl-config output' >&2
        return 2
    }
    # Line 1 is `enabled`, already consumed by bin/hsl-internal; line 2 is
    # `mouse_clicks`; the count is line 3 and the pairs start at line 4.
    MOUSE_CLICKS=$(sed -n '2p' "$STATUS_OPTIONS") || {
        printf '%s\n' 'hsl: invalid hsl-config output' >&2
        return 2
    }
    case $MOUSE_CLICKS in
        true|false) ;;
        *)
            printf '%s\n' 'hsl: invalid hsl-config output' >&2
            return 2
            ;;
    esac
    count=$(sed -n '3p' "$STATUS_OPTIONS") || {
        printf '%s\n' 'hsl: invalid hsl-config output' >&2
        return 2
    }
    case ${count:-x} in
        ''|*[!0-9]*)
            printf '%s\n' 'hsl: invalid hsl-config output' >&2
            return 2
            ;;
    esac
    lines=$(wc -l <"$STATUS_OPTIONS") || {
        printf '%s\n' 'hsl: invalid hsl-config output' >&2
        return 2
    }
    [ "$lines" -eq $((3 + count * 2)) ] || {
        printf '%s\n' 'hsl: invalid hsl-config output' >&2
        return 2
    }
    index=0
    while [ "$index" -lt "$count" ]; do
        name=$(sed -n "$((4 + index * 2))p" "$STATUS_OPTIONS") || return 2
        # Command substitution strips the line terminator and nothing else, so
        # a value's leading and trailing spaces survive.
        value=$(sed -n "$((5 + index * 2))p" "$STATUS_OPTIONS") || return 2
        case $name in
            window-status-*) scope=-gw ;;
            *) scope=-g ;;
        esac
        if ! failure=$("$TMUX_BIN" -L "$socket" set-option "$scope" "$name" "$value" 2>&1); then
            printf 'hsl: cannot apply statusline option %s: %s\n' "$name" "$failure" >&2
            return 2
        fi
        index=$((index + 1))
    done
}
```

- [ ] **Step 10: 全テストが通ることを確認**

Run: `cargo test --quiet && python3 -m unittest discover -s tests`
Expected: Rust 46 件、Python 110 件が PASS。

- [ ] **Step 11: 構文チェックとコミット**

Run: `sh -n scripts/run-in-tmux`

```bash
git add src/config.rs scripts/run-in-tmux tests/helpers.py \
        tests/test_hsl_internal.py tests/test_tmux_runtime.py
git commit -m "$(cat <<'EOF'
feat: carry a mouse_clicks flag through the config protocol

The flag is a top-level key, not a [statusline] option, so the allowlist
keeping mouse and key bindings out of reach from config.toml is untouched.

Writer and reader move together on purpose. The moment the test helper
emits the new layout, an old reader takes line 2's `false` for the option
count, so splitting them would leave a commit with the suite red.

Version skew is now a fail-closed contract rather than something the
design claims cannot happen: an old protocol trips the boolean check, and
one whose pairs sit at the old offsets trips the line count. Both refuse
to start instead of applying options shifted by a line.

Every command substitution in apply_status_options is checked explicitly.
The function is called as `if ! apply_status_options`, so set -e does not
propagate out of it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `base.conf` の root キーテーブル明示クリア

**Files:**
- Modify: `tmux/base.conf`
- Modify: `tests/test_tmux_runtime.py`（`SMOKE_HERDR` と
  `RealTmuxSmokeTests.test_a_real_server_applies_options_and_feeds_the_status_job`）

**Interfaces:**
- Consumes: なし
- Produces: 起動直後の tmux セッションで `list-keys -T root` が空。Task 3 の配線と
  Task 6 の透過テストがこれに依存する。

- [ ] **Step 1: 失敗テストを書く**

`SMOKE_HERDR` の記録辞書に 1 行足す。`'status_format_1': ...` の隣に置く。

```python
    'root_keys': option('list-keys', '-T', 'root'),
```

既存の `test_a_real_server_applies_options_and_feeds_the_status_job` の
`self.assertEqual(record["window_name"], "herdr")` の直後に assertion を足す。
**新しいテストクラスやヘルパーは作らない。** このリポジトリの実 tmux テストは
`RealTmuxSmokeTests` の 2 本だけで、セットアップを共有する仕組みは存在しない。

```python
            # `unbind-key -a` clears only the prefix table. tmux keeps 24
            # default mouse bindings in root, inert while the mouse is off but
            # ready to take copy-mode, the pane context menu, the kill-pane
            # menu and border resize away from Herdr the moment it goes on.
            self.assertEqual(record["root_keys"], "")
```

- [ ] **Step 2: 失敗を確認**

Run: `python3 -m unittest tests.test_tmux_runtime -k real_server -v`
Expected: FAIL。24 本の binding が列挙された文字列と `""` の比較で落ちる。

- [ ] **Step 3: 実装**

`tmux/base.conf` の `unbind-key -a` を置き換える。

```tmux
# `unbind-key -a` clears the prefix table only. tmux keeps its default mouse
# bindings in the root table, where they are inert while the mouse is off but
# would take copy-mode, the pane context menu, the kill-pane menu and border
# resize away from Herdr the moment mouse_clicks turns it on. Clearing root
# here, statically, also removes any ordering hazard: no wiring can run before
# the table is empty.
unbind-key -a
unbind-key -a -T root
```

ファイル冒頭のコメントも更新する。

```tmux
# Minimal, status-line-only tmux for a disposable herdr-statusline server.
# The session owns no keys and takes no input, with one bounded exception:
# `mouse_clicks = true` makes run-in-tmux turn the mouse on and add four fixed
# status-line bindings after this file has been read.
```

- [ ] **Step 4: 通ることを確認**

Run: `python3 -m unittest discover -s tests`
Expected: PASS（110 件）。

- [ ] **Step 5: 手で裏を取る**

```bash
tmux -L planprobe -f tmux/base.conf new-session -d -s x 'sleep 30'
tmux -L planprobe list-keys -T root | wc -l   # 0 であること
tmux -L planprobe kill-server
```

- [ ] **Step 6: コミット**

```bash
git add tmux/base.conf tests/test_tmux_runtime.py
git commit -m "$(cat <<'EOF'
tmux: clear the root key table, not just the prefix table

The file's own comment says this session owns no keys, and that was not
true: unbind-key -a clears the prefix table, leaving tmux's 24 default
mouse bindings in root. They cannot fire while the mouse is off, so this
changes nothing today, but it is what makes turning the mouse on safe.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: バージョン判定と配線

**Files:**
- Modify: `scripts/run-in-tmux`
- Modify: `tests/test_tmux_runtime.py`

**Interfaces:**
- Consumes: `MOUSE_CLICKS`（Task 1）、空の root テーブル（Task 2）
- Produces: tmux 側の user option `@hsl_on_click` と root テーブルの 4 binding
  （`MouseDown1Status` / `MouseDown3Status` / `WheelUpStatus` / `WheelDownStatus`）。
  Task 5・6 の結合テストがこれを叩く。

- [ ] **Step 1: fake tmux に `-V` と bind-key 拒否を足す**

`FAKE_TMUX` を編集する。`-V` の分岐は `new-session` の分岐より**前**に置く。
既存の `if 'set-option' in args and os.environ.get('HSL_TEST_TMUX_REJECT', '') in args:`
の 3 行を次で置き換える。空文字列が全 argv にマッチしないよう `reject and` を必ず付ける。

```python
if args == ['-V']:
    print(os.environ.get('HSL_TEST_TMUX_VERSION', 'tmux 3.7b'))
    raise SystemExit(0)
reject = os.environ.get('HSL_TEST_TMUX_REJECT', '')
if reject and reject in args and ('set-option' in args or 'bind-key' in args):
    print('rejected by the fake tmux', file=sys.stderr)
    raise SystemExit(1)
```

- [ ] **Step 2: 失敗テストを書く**

`TmuxRuntimeTests` に追加する。**`setUp()` を手で呼ばない**（unittest のライフサイクル外で
一時ディレクトリと cleanup が積み上がる）。ループでは argv ログだけを空に戻す。

```python
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
        self.assertEqual(self.bind_calls(argv), [])
        self.assertEqual(self.mouse_option_calls(argv), [])

    def test_leaves_the_mouse_off_when_the_hook_is_not_executable(self):
        result, argv = self.wire(executable=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("is not executable", result.stderr)
        self.assertEqual(self.bind_calls(argv), [])
        self.assertEqual(self.mouse_option_calls(argv), [])

    def test_leaves_the_mouse_off_when_the_config_dir_is_unknown(self):
        result, argv = self.wire(config_dir=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("plugin config dir is unknown", result.stderr)
        self.assertEqual(self.bind_calls(argv), [])
        self.assertEqual(self.mouse_option_calls(argv), [])

    def test_leaves_the_mouse_off_below_tmux_3_4(self):
        for version in ("tmux 3.3a", "tmux 3.0", "tmux 2.9", "tmux 1.8"):
            with self.subTest(version=version):
                self.tmux_log.write_text("")
                result, argv = self.wire(version=version)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("tmux 3.4", result.stderr)
                self.assertEqual(self.bind_calls(argv), [])
                self.assertEqual(self.mouse_option_calls(argv), [])
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
```

- [ ] **Step 3: 失敗を確認**

Run: `python3 -m unittest tests.test_tmux_runtime -k wire -k mouse_off -k tmux_3_4 -k fails_closed -v`
Expected: FAIL。`apply_mouse_clicks` も `mouse_ranges_supported` もまだ無い。

- [ ] **Step 4: 実装**

`scripts/run-in-tmux` の `apply_status_options` の直後に 2 つの関数を足す。

```sh
# Unlike allow-passthrough below, user ranges cannot be detected by setting
# them: tmux validates the `range` style keyword but not its value, so
# `range=user|p` is accepted on 3.3 too, where user ranges do not exist.
# Version parsing is the only test that discriminates, so it is used here and
# nowhere else. Anything unparseable counts as new enough: those are builds
# like `next-3.9`, and refusing them would silently ignore an explicit opt-in.
mouse_ranges_supported() {
    version=$("$TMUX_BIN" -V 2>/dev/null) || return 0
    numbers=$(printf '%s' "$version" |
        sed -n 's/[^0-9]*\([0-9][0-9]*\)\.\([0-9][0-9]*\).*/\1 \2/p') || return 0
    [ -n "$numbers" ] || return 0
    major=${numbers% *}
    minor=${numbers#* }
    [ "$major" -gt 3 ] && return 0
    [ "$major" -eq 3 ] && [ "$minor" -ge 4 ] && return 0
    return 1
}

# Status-line mouse clicks are opt-in and inert without a hook: enabling the
# mouse with nothing to dispatch to would only cost the user the terminal's
# native selection and give nothing back.
#
# Every field travels through tmux's own `#{q:...}` sh-escaper. run-shell
# format-expands the whole string before handing it to /bin/sh -c, so a range
# name is attacker-controlled text landing inside a shell command line: a
# 15-byte name is enough to break out of hand-written single quotes and run
# arbitrary code. The hook path goes the same way, as a user option, so a `#`
# in the config path cannot be re-expanded either. shell_quote is deliberately
# not used here; it escapes for a generated script, not for tmux's two-stage
# expansion.
#
# -b, the redirect and the `|| true` are three separate requirements: without
# -b the hook blocks the command queue, without the redirect its stdout is
# drawn over Herdr, and without `|| true` a non-zero exit still is.
#
# Nothing mouse-specific is touched on any early return. Ordinary status
# options are unrelated to this feature and have already been applied.
apply_mouse_clicks() {
    [ "${MOUSE_CLICKS:-false}" = true ] || return 0

    if ! mouse_ranges_supported; then
        printf '%s\n' \
            'hsl: mouse_clicks needs tmux 3.4 or newer for user ranges;' \
            'hsl: mouse clicks stay off' >&2
        return 0
    fi

    if [ -z "${HERDR_PLUGIN_CONFIG_DIR:-}" ]; then
        printf '%s\n' \
            'hsl: mouse_clicks is on but the plugin config dir is unknown;' \
            'hsl: mouse clicks stay off' >&2
        return 0
    fi

    hook=$HERDR_PLUGIN_CONFIG_DIR/on-click.sh
    if [ ! -x "$hook" ]; then
        printf 'hsl: mouse_clicks is on but %s is not executable\n' "$hook" >&2
        printf '%s\n' 'hsl: mouse clicks stay off' >&2
        return 0
    fi

    "$TMUX_BIN" -L "$socket" set-option -g @hsl_on_click "$hook" || return 2
    "$TMUX_BIN" -L "$socket" set-option -g mouse on || return 2

    for pair in \
        'MouseDown1Status left' \
        'MouseDown3Status right' \
        'WheelUpStatus wheelup' \
        'WheelDownStatus wheeldown'
    do
        key=${pair% *}
        name=${pair#* }
        "$TMUX_BIN" -L "$socket" bind-key -n "$key" run-shell -b \
            "#{q:@hsl_on_click} $name #{q:mouse_status_range} #{q:mouse_x} #{q:mouse_status_line} >/dev/null 2>&1 || true" \
            || return 2
    done
}
```

呼び出しを既存の `apply_status_options` チェックの直後、`wait-for -S hsl-start` より前に足す。

```sh
if ! apply_status_options; then
    exit 2
fi

if ! apply_mouse_clicks; then
    exit 2
fi
```

- [ ] **Step 5: 判定規則を単体で確かめる**

```bash
for v in "tmux 3.4" "tmux 3.7b" "tmux 4.0" "tmux next-3.9" "weird output" \
         "tmux 3.3a" "tmux 3.0" "tmux 2.9"; do
  n=$(printf '%s' "$v" | sed -n 's/[^0-9]*\([0-9][0-9]*\)\.\([0-9][0-9]*\).*/\1 \2/p')
  printf '  %-16s -> [%s]\n' "$v" "$n"
done
```
Expected: `next-3.9` は `3 9`、`weird output` は空、`tmux 3.3a` は `3 3`。

- [ ] **Step 6: 全テストが通ることを確認**

Run: `cargo test --quiet && python3 -m unittest discover -s tests`
Expected: すべて PASS。

- [ ] **Step 7: 構文チェックとコミット**

Run: `sh -n scripts/run-in-tmux`

```bash
git add scripts/run-in-tmux tests/test_tmux_runtime.py
git commit -m "$(cat <<'EOF'
feat: wire status-line clicks to an opt-in on-click.sh hook

Four fixed root bindings, a user option holding the hook path, and
nothing else. Arguments reach the hook only through tmux's #{q:} escaper:
hand-quoting #{mouse_status_range} is a command injection, since run-shell
format-expands into a shell command line and tmux bounds range names only
by length.

The version gate ships with the wiring rather than ahead of it, so no
commit is left expecting a mouse that nothing turns on. It parses tmux -V,
the one place in this repo that does. Probing was tried and does not
discriminate: tmux validates the `range` keyword but not its value, so
`range=user|p` is accepted on 3.3 as well.

A missing hook, a hook without its execute bit, an unknown config dir and
a tmux below 3.4 each leave the mouse untouched with a warning. Ordinary
status options still apply in every one of those cases; this flag governs
input handling, not whether a status line is drawn.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: 本番配線を通す pty フィクスチャ

**Files:**
- Create: `tests/mouse_pty.py`
- Modify: `scripts/run-in-tmux:11`（`BASE_CONF` をテストから差し替え可能にする）

**Interfaces:**
- Consumes: Task 3 が張る配線（`scripts/run-in-tmux` 経由でのみ使う）
- Produces: `tests/mouse_pty.py`
  - `HslPty(runtime, env, session="mouse", cols=80, rows=24)` — コンテキストマネージャ。
    **`sh scripts/run-in-tmux --session <session>` を pty 上で起動する。** socket は
    run-in-tmux が一意に選ぶので固定 socket の衝突は起こらない
  - `.click(col, row, button=0)` / `.wheel(col, row, up=True)` /
    `.motion(col, row)` / `.drag(from_col, from_row, to_col, to_row)`
  - `.wait_for_lines(path, count, timeout=10.0) -> list[str]`
  - `.drawn() -> bytes`
  - `INNER_APP` / `inner_app_script(log_path, mode=1003)` — herdr 役のスタブ
  - `shell_quote(text)`

- [ ] **Step 1: `BASE_CONF` を差し替え可能にする**

`scripts/run-in-tmux:11` を書き換える。Task 6 の negative test が、root クリアを外した
base.conf でサーバを起動して透過が壊れることを示すために必要。

```sh
# Overridable for tests only: the root-table guard has to start a server
# without `unbind-key -a -T root` and show that pass-through breaks.
BASE_CONF=${HSL_TEST_BASE_CONF:-$root/tmux/base.conf}
```

- [ ] **Step 2: フィクスチャを書く**

`tests/mouse_pty.py` を新規作成する。

```python
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
import os, sys, time, tty
log = sys.argv[1]
open(log, "w").close()
tty.setraw(0)
sys.stdout.write("\033[?MODEh\033[?1006h")
sys.stdout.flush()
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
    if b"q" in data:
        break
"""


def shell_quote(text):
    return "'" + text.replace("'", "'\\''") + "'"


def inner_app_script(log_path, mode=1003):
    """A shell script running the stub, for HSL_HERDR_BIN."""
    program = INNER_APP.replace("MODE", str(mode))
    return (
        "#!/bin/sh\n"
        f"exec python3 -c {shell_quote(program)} {shell_quote(str(log_path))}\n"
    )


class HslPty:
    def __init__(self, runtime, env, session="mouse", cols=80, rows=24):
        self.runtime = runtime
        self.env = env
        self.session = session
        self.cols = cols
        self.rows = rows
        self._buffer = bytearray()
        self.pid = None
        self.fd = None

    def __enter__(self):
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
        self._drain(3.0)
        self._buffer.clear()
        return self

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

    def drawn(self):
        return bytes(self._buffer)
```

- [ ] **Step 3: import と mode 置換を確認**

Run:
```bash
python3 -c "
from tests.mouse_pty import inner_app_script
s = inner_app_script('/tmp/a', 1000)
assert s.startswith('#!/bin/sh'), s[:40]
assert '?1000h' in s and '?1006h' in s
print('ok')
"
```
Expected: `ok`

- [ ] **Step 4: 内側スタブが実 tmux ペインで動くことを確認**

```bash
python3 - <<'PY'
import pathlib, subprocess, tempfile, time
from tests.mouse_pty import inner_app_script
with tempfile.TemporaryDirectory() as d:
    base = pathlib.Path(d); log = base / "app.log"
    stub = base / "herdr"; stub.write_text(inner_app_script(log)); stub.chmod(0o700)
    subprocess.run(["tmux","-L","planprobe2","-f","/dev/null","new-session",
                    "-d","-s","x","-x","80","-y","23",str(stub)], check=True)
    time.sleep(1.2)
    flags = subprocess.run(["tmux","-L","planprobe2","display-message","-p",
                            "#{mouse_any_flag}#{mouse_all_flag}#{mouse_sgr_flag}"],
                           text=True, capture_output=True).stdout.strip()
    print("mouse flags (want 111):", flags)
    subprocess.run(["tmux","-L","planprobe2","kill-server"], capture_output=True)
PY
```
Expected: `mouse flags (want 111): 111`

- [ ] **Step 5: 構文チェックと全テスト**

Run: `sh -n scripts/run-in-tmux && python3 -m unittest discover -s tests`
Expected: PASS。

- [ ] **Step 6: コミット**

```bash
git add tests/mouse_pty.py scripts/run-in-tmux
git commit -m "$(cat <<'EOF'
test: add a pty fixture that drives the real run-in-tmux

It starts scripts/run-in-tmux rather than reproducing what it does. A
fixture that set `mouse on` and the four bindings itself would keep
passing if the production wiring quoted a format wrongly or expanded it
at the wrong stage, which is the failure worth testing for.

Because the runtime picks its own socket, nothing here has a fixed one to
collide over. Hook output is polled to a deadline, since run-shell -b
promises neither completion order nor completion time. Children are reaped.

BASE_CONF becomes overridable so the root-table guard can start a server
without the clear and show that pass-through breaks.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: クリック配送の結合テスト

**Files:**
- Create: `tests/test_tmux_mouse.py`

**Interfaces:**
- Consumes: `tests/mouse_pty.py`（Task 4）、Task 3 の配線
- Produces: `MouseIntegrationBase`（Task 6 が継承する）

- [ ] **Step 1: テストを書く**

`tests/test_tmux_mouse.py` を新規作成する。

```python
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
        for evil in ("a#b", "a#{x}b", "a b", "a$(id)b", "0123456789abcde"):
            with self.subTest(name=evil):
                self.assertLessEqual(len(evil), 15)
                self.hook_log.unlink(missing_ok=True)
                with self.session(
                    status_format=(
                        f"#[align=left]#[range=user|{evil}] BTN #[norange]tail"
                    )
                ) as term:
                    term.click(BUTTON_COL, STATUS_ROW)
                    lines = term.wait_for_lines(self.hook_log, 1)
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
```

- [ ] **Step 2: 実行して確認**

Run: `python3 -m unittest tests.test_tmux_mouse -v`
Expected: PASS。落ちた場合はまず 1 本だけ動かし、セッションが起動しているかを見る。

```bash
python3 -m unittest tests.test_tmux_mouse.StatusClickTests.test_delivers_button_range_and_coordinates -v
```

`wait_for_lines` が空を返す場合は、`HslPty.__enter__` の `_drain(3.0)` を伸ばすか、
`runtime_env` の環境変数が `RealTmuxSmokeTests` と揃っているかを確認する。

- [ ] **Step 3: 全テストが通ることを確認**

Run: `python3 -m unittest discover -s tests`
Expected: PASS。

- [ ] **Step 4: コミット**

```bash
git add tests/test_tmux_mouse.py
git commit -m "$(cat <<'EOF'
test: cover status-click dispatch end to end

Everything here goes through scripts/run-in-tmux, so the assertions bind
to the wiring that ships rather than to a copy of it.

The injection case uses a 15-byte range name — exactly tmux's limit — that
truncates /tmp/hslz into existence under hand-written quotes, and asserts
both that the marker is absent and that the name arrived as one literal
argument. The rest of the hostile surface is covered too: '#', '#{...}',
whitespace, command substitution, and a name at the length limit.

Ordering is compared as a multiset and output is polled to a deadline,
because run-shell -b promises neither. The slow-hook test is what actually
pins -b: two three-second hooks have to overlap.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: pane 透過と root クリアの回帰テスト

**Files:**
- Modify: `tests/test_tmux_mouse.py`

**Interfaces:**
- Consumes: `MouseIntegrationBase`（Task 5）、`HSL_TEST_BASE_CONF`（Task 4）
- Produces: なし

- [ ] **Step 1: 透過と座標のテストを書く**

`tests/test_tmux_mouse.py` に追加する。

```python
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
```

- [ ] **Step 2: root クリアの negative test を書く**

binding の存在確認では足りない。**実際にイベントを送り、アプリ側の受信が壊れることを
確認する。**

```python
@unittest.skipUnless(TMUX, "tmux is not installed")
@unittest.skipUnless(tmux_at_least_3_4(), "needs tmux 3.4 or newer")
class RootTableGuardTests(MouseIntegrationBase):
    def test_pass_through_breaks_without_the_root_table_clear(self):
        # Guards tmux/base.conf's `unbind-key -a -T root`. Removing that line
        # brings tmux's defaults back; DoubleClick1Pane then runs copy-mode
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
            term.click(10, PANE_ROW)
            term.click(10, PANE_ROW)   # completes a double click
            term.wait_for_lines(self.app_log, 4, timeout=4.0)
            broken = self.received()

        # Sanity: with the clear in place the same events do arrive.
        self.setUp()
        self.hook()
        with self.session() as term:
            term.click(10, PANE_ROW)
            term.click(10, PANE_ROW)
            term.wait_for_lines(self.app_log, 4, timeout=4.0)
            intact = self.received()

        self.assertIn(r"\x1b[<0;10;5M", intact,
                      "the cleared root table must forward the events")
        self.assertNotEqual(
            broken.count(r"\x1b[<0;10;5M"), intact.count(r"\x1b[<0;10;5M"),
            "removing the root-table clear must change what the app receives",
        )
```

`self.setUp()` をここで呼ぶのは、同一テスト内で 2 つ目の独立したセッションを張るため。
`addCleanup` は積み増しになるが、どちらの一時ディレクトリも最後に片付く。

- [ ] **Step 3: 実行して確認**

Run: `python3 -m unittest tests.test_tmux_mouse -v`
Expected: PASS。

`test_pass_through_breaks_without_the_root_table_clear` が
「差が出ない」で落ちる場合は、tmux のどの既定 binding が実際にイベントを消費するかを
実測してから比較対象を合わせる。

```bash
tmux -L planprobe3 -f /dev/null new-session -d -s x 'sleep 30'
tmux -L planprobe3 list-keys -T root | awk '{print $4}'
tmux -L planprobe3 kill-server
```

- [ ] **Step 4: 全テストが通ることを確認**

Run: `cargo test --quiet && python3 -m unittest discover -s tests`
Expected: すべて PASS。

- [ ] **Step 5: コミット**

```bash
git add tests/test_tmux_mouse.py
git commit -m "$(cat <<'EOF'
test: pin pane pass-through, coordinates and the root-table guard

Turning the mouse on must not cost Herdr its own mouse input, so both 1000
and 1003 are covered, including a real button-held drag rather than motion
standing in for one.

The coordinate test states what the relay actually is: with the status
line on top — which the allowlist permits — terminal row 5 reaches the
application as row 4.

The guard test sends events instead of listing bindings. It starts one
server from a base.conf with the root-table clear removed and another with
it intact, and asserts the application receives different things.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: ドキュメントと安全論拠の更新

**Files:**
- Modify: `scripts/default-config.toml`
- Modify: `skills/customize-herdr-statusline/SKILL.md`
- Modify: `src/config.rs`（`option_name` のコメント）

**Interfaces:**
- Consumes: Task 1〜6 の完成した挙動
- Produces: なし

- [ ] **Step 1: `scripts/default-config.toml` に説明を足す**

`enabled = true` の直後に置く。

```toml
# Status-line mouse clicks. Off by default. Turning this on asks the outer
# terminal for mouse reporting, which costs you its native selection and
# middle-click paste, so it is opt-in.
#
# It needs tmux 3.4 or newer, and it does nothing on its own: clicks are
# dispatched to `on-click.sh` in this directory, which you or another plugin
# provides. It receives four arguments:
#
#   $1  left | right | wheelup | wheeldown
#   $2  the range name under the pointer
#   $3  the mouse column, zero-based
#   $4  the status line number, zero-based
#
# Mark a clickable area in a status format with `#[range=user|NAME]` ...
# `#[norange]`, where NAME is at most 15 bytes. The hook's stdout and exit
# status are discarded, so call `tmux display-message` to say anything. It may
# run several times at once, in no particular order.
#
#   mouse_clicks = true
```

- [ ] **Step 2: `SKILL.md` に節を足す**

`## Validate before reporting` の直前に置く。

```markdown
## Status line buttons

Clickable areas need `mouse_clicks = true` at the top level of `config.toml`,
next to `enabled`, and tmux 3.4 or newer. Clicks go to `on-click.sh` in the
config directory, which the user or another plugin owns; this skill does not
create it. Turning the feature on costs the terminal its native selection and
middle-click paste, so say so before enabling it for someone.

Mark an area with `#[range=user|NAME]` ... `#[norange]`. `NAME` is at most 15
bytes and reaches the hook as its second argument. Shell metacharacters are
carried safely, but keep names to letters, digits and `_` for readability.

Three constraints shape the layout:

- Put user ranges in a `status_format_N` you define yourself. Inside
  `status_left` and `status_right` tmux wraps them in `range=left` and
  `range=right`, which shifts the clickable area one column right of the text.
- The clickable area always extends one column past the last character.
- There is no hover: tmux has no `MouseMove` binding, so a button has to look
  clickable on its own.
```

- [ ] **Step 3: `src/config.rs` の安全論拠コメントを書き換える**

`option_name` のドキュメントコメントを差し替える。

```rust
/// Map a config key to a tmux option name and bound what may be set.
///
/// The prefixes bound the blast radius: no option named `status`, `status-*`
/// or `window-status-*` can reach `prefix`, key bindings, `mouse`, hooks,
/// `destroy-unattached` or `remain-on-exit`, so the disposable session's
/// invariants cannot be configured away. Whether a name exists at all is
/// tmux's business, which keeps this free of an option table to maintain.
///
/// This is no longer the whole safety argument. The top-level `mouse_clicks`
/// key is a bounded exception to "tmux takes no input": it turns the mouse on
/// and installs four fixed root bindings, whose names and bodies are hardcoded
/// in run-in-tmux and cannot be reached from here. Everything else about the
/// session stays as it was.
```

- [ ] **Step 4: 検証**

```bash
cargo test --quiet
python3 -m unittest discover -s tests
```

Expected: すべて PASS。`default-config.toml` は TOML なので `sh -n` の対象外。

このステップで壊しやすいのは次の 2 つで、いずれも **`mouse_clicks` をコメントアウトの
まま置く限り通る**。

- `src/init.rs` の `creates_both_files_and_the_shipped_config_parses` は、既定
  `config.toml` の解析結果が `status-interval = 1` と `status-right` の 2 件だけで
  あることを検査する。コメント行は解析結果を変えない
- `src/init.rs` の `ships_the_repository_copy_of_each_template` は、インストールされた
  ファイルがリポジトリのテンプレートとバイト一致することを検査する。どちらも同じ
  `include_str!` 由来なので、テンプレートだけを編集する限り一致は保たれる

- [ ] **Step 5: コミット**

```bash
git add scripts/default-config.toml skills/customize-herdr-statusline/SKILL.md \
        src/config.rs
git commit -m "$(cat <<'EOF'
docs: describe the click hook and restate the safety argument

The allowlist comment claimed to be the whole safety argument. With a
top-level key that turns the mouse on it is not, so it now names the
exception and how it is bounded: four fixed bindings whose names and
bodies live in run-in-tmux, unreachable from config.toml.

The user-facing docs lead with the price — mouse reporting costs the
terminal its native selection — and cover the tmux 3.4 floor, the
one-column hit-area offset, the absence of hover, and the fact that the
hook can run concurrently with itself.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**Spec coverage**

| 仕様 | タスク |
| --- | --- |
| §5.1 `mouse_clicks` フラグ | Task 1 |
| §5.2 行プロトコル・3 消費者・版ずれ契約（双方向） | Task 1 |
| §5.3 base.conf root クリア | Task 2 |
| §5.4 プロトコル読み出し（明示的失敗検査を含む） | Task 1 |
| §5.5 条件付き配線 | Task 3 |
| §5.6 tmux 3.4 判定 | Task 3 |
| §5.7 default-config.toml | Task 7 |
| §5.8 init.rs 変更なし | 意図的に該当タスクなし |
| §5.9 SKILL.md | Task 7 |
| §6 フック契約（並行性・出力破棄を含む） | Task 3 実装 / Task 5 検証 / Task 7 文書化 |
| §7 安全論拠の再定義 | Task 7 |
| §8 既知の制約 | Task 7 |

| 仕様 §9 のテスト | 実装箇所 |
| --- | --- |
| 1〜5 ユニット | Task 1 Step 1 |
| 6 test_hsl_internal 期待値 | Task 1 Step 5 |
| 7 hsl-internal の enabled=false 経路 | 既存テストが維持（Task 1 Step 10 で確認） |
| 8・9 版ずれ双方向 | Task 1 Step 7（`test_rejects_an_old_writer_protocol`、`test_rejects_a_protocol_whose_pairs_sit_at_the_old_offsets`） |
| 10 非 boolean で exit 2 | Task 1 Step 7 |
| 11 mouse off 時に root 0 本 | Task 2 Step 1 |
| 12 root ちょうど 4 本 | Task 3 Step 2（`test_wires_exactly_four_status_bindings`） |
| 13 フック不在／非実行可能／config dir 不明 | Task 3 Step 2、いずれも mouse 未適用まで検査 |
| 14 各段階の失敗で exit 2 | Task 3 Step 2（`@hsl_on_click`・`mouse`・4 binding を個別に拒否し、herdr 未起動も確認） |
| 15 引数搬送と敵対的入力 | Task 5（注入・`#`・`#{}`・空白・`$( )`・15 バイト境界） |
| 16 出力非描画とキュー非ブロック | Task 5（`test_a_noisy_failing_hook_draws_nothing`、`test_a_slow_hook_does_not_block_the_command_queue`） |
| 17 3.4 未満で mouse off | Task 3 Step 2（返り値・警告・通常オプション適用も検査） |
| 18 1000/1003 の透過（ドラッグ含む） | Task 6 |
| 19 座標のペイン相対変換 | Task 6 |
| 20 root クリアの negative test | Task 6（イベントを送り受信内容の差を確認） |
| 21 連打時の並行起動 | Task 5（`test_rapid_clicks_all_reach_the_hook`） |

**Placeholder scan:** socket の特定方法という未解決分岐は、`status-position` を起動時
options に渡す形へ一本化して解消した。`run_smoke` / `herdr_record` という存在しない
ヘルパーへの依存も、既存の `RealTmuxSmokeTests` の該当テストへ直接 assertion を足す形へ
置き換えた。

**Type consistency:** `mouse_clicks`（Rust フィールド・TOML キー・Python 引数）、
`MOUSE_CLICKS`（sh 変数）、`@hsl_on_click`（tmux user option）、
`mouse_ranges_supported` / `apply_mouse_clicks`（sh 関数）、
`HslPty` / `inner_app_script` / `wait_for_lines` / `MouseIntegrationBase`（Python）で
全タスク一貫。

**各コミットの緑:** Task 1 は writer と reader を、Task 3 は判定と配線を同時に変更する
ため、どのコミット時点でも `cargo test` と `python3 -m unittest discover -s tests` が
通る。

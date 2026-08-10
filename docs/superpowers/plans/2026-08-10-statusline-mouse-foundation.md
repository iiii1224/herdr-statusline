# Status line マウスクリック基盤 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** tmux status line 上のクリックを、外部リポジトリが所有する単一フック `on-click.sh` へ安全に配送する opt-in 基盤を追加する。

**Architecture:** `config.toml` のトップレベル `mouse_clicks` を `hsl-config` の行プロトコルに 2 行目として載せ、`run-in-tmux` がそれを読んで tmux を条件付きで配線する。`tmux/base.conf` は root キーテーブルを明示的に空にし、`mouse on` にしたとき tmux 標準のマウス binding が herdr の入力を奪わないようにする。フックへの引数搬送は tmux 自身の `#{q:...}` sh エスケーパのみで行い、シェル文字列の手組みは一切しない。

**Tech Stack:** Rust（`hsl-config`、serde/toml）、POSIX sh（`run-in-tmux`、`bin/hsl-internal`）、tmux 3.4+、Python unittest（統合テスト）

**設計仕様:** `docs/superpowers/specs/2026-08-10-statusline-mouse-foundation-design.md`（rev2）。本計画の各タスクは仕様の F 番号・§番号を参照する。

## Global Constraints

- **tmux 3.4 以上が必要。** `range=user` / `mouse_status_range` / `mouse_status_line` は `CHANGES FROM 3.3a TO 3.4` で追加された（仕様 F15）。
- **フックへ渡す値は必ず tmux の `#{q:...}` を通す。** `run-shell` は文字列全体を tmux format 展開してから `/bin/sh -c` に渡すため、手でシングルクォートすると range 名からコマンドインジェクションが成立する（仕様 F7、14 バイトで実証済み）。`scripts/lib/shell-quote.sh` の `shell_quote` はこの経路では**使わない**。
- **binding は必ず `run-shell -b` + `>/dev/null 2>&1` + `|| true` の 3 点セット。** `-b` が無いとコマンドキューをブロックし、リダイレクトが無いと stdout が herdr に描画され、`|| true` が無いと非ゼロ終了が描画される（仕様 F9）。
- **pane 系の binding は追加しない。** root が空なら tmux が自動で転送する（仕様 F4）。
- **`src/config.rs` の `option_name` allowlist は変更しない。** `[statusline]` から到達できるのは `status` / `status-*` / `window-status-*` のみを維持する。
- **`MouseDown2Status` と `MouseDown1StatusDefault` は配線しない。**
- 既存のベースライン: `cargo test` は 44 テスト全通過、全 shell スクリプトは `sh -n` 通過。
- コミットメッセージ末尾には `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` を付ける。

---

## File Structure

| ファイル | 役割 | 変更 |
| --- | --- | --- |
| `src/config.rs` | `mouse_clicks` のパースと行プロトコル出力 | Modify |
| `tests/helpers.py` | `write_protocol` ヘルパーに `mouse_clicks` を通す | Modify |
| `tests/test_hsl_internal.py` | プロトコル完全一致の期待値 | Modify |
| `tmux/base.conf` | root キーテーブルの明示クリア | Modify |
| `scripts/run-in-tmux` | プロトコル読み出し、バージョン判定、条件付き配線 | Modify |
| `tests/test_tmux_runtime.py` | fake tmux による argv 検証 | Modify |
| `tests/mouse_pty.py` | **新規** pty へ SGR マウスを注入する再現フィクスチャ | Create |
| `tests/test_tmux_mouse.py` | **新規** 実 tmux でのクリック配送・透過の結合テスト | Create |
| `scripts/default-config.toml` | `mouse_clicks` の説明コメント | Modify |
| `skills/customize-herdr-statusline/SKILL.md` | ボタンの作り方と制約 | Modify |

---

## Task 1: `mouse_clicks` フラグと行プロトコル

**Files:**
- Modify: `src/config.rs`
- Modify: `tests/helpers.py`
- Modify: `tests/test_hsl_internal.py:217`

**Interfaces:**
- Consumes: なし（最初のタスク）
- Produces: `NormalizedConfig.mouse_clicks: bool`。`hsl-config load` の出力が
  `enabled\nmouse_clicks\ncount\nname\nvalue...` の順になる。Task 2 以降が依存する。

- [ ] **Step 1: 失敗するテストを書く**

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

さらに既存の `writes_the_variable_length_protocol` の期待値を書き換える。

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

- [ ] **Step 2: テストが失敗することを確認**

Run: `cargo test --quiet`
Expected: FAIL。`no field `mouse_clicks` on type `NormalizedConfig`` と、
`writes_the_variable_length_protocol` が `unknown field `mouse_clicks`` で失敗する。

- [ ] **Step 3: 最小の実装を書く**

`src/config.rs` の `RawConfig` にフィールドを足す。

```rust
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RawConfig {
    #[serde(default = "default_enabled")]
    enabled: bool,
    /// Opt-in for status-line mouse clicks. Off by default: turning the mouse
    /// on costs the outer terminal its native selection (tmux asks for
    /// 1000/1002/1003/1006), so it must never happen without being asked for.
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

`normalize` で透過させる。

```rust
    Ok(NormalizedConfig {
        enabled: raw.enabled,
        mouse_clicks: raw.mouse_clicks,
        options,
    })
```

`write_protocol` に 2 行目を挿入する。

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
Expected: PASS。テスト数は 44 → 46。

- [ ] **Step 5: Python 側の消費者を更新する**

`tests/helpers.py` の `write_protocol` に引数を足す。docstring も更新すること。

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

以降の行は変更しない。

`tests/test_hsl_internal.py:217` の完全一致期待値を更新する。

```python
        self.assertEqual(
            record["options"],
            "true\nfalse\n2\nstatus-interval\n1\nstatus-right\n%m/%d %H:%M:%S\n",
        )
```

- [ ] **Step 6: Python テストが通ることを確認**

Run: `python3 -m pytest tests/test_hsl_internal.py -q`
Expected: PASS。落ちる場合は期待値の行順を確認する（`enabled` → `mouse_clicks` → count）。

- [ ] **Step 7: コミット**

```bash
git add src/config.rs tests/helpers.py tests/test_hsl_internal.py
git commit -m "$(cat <<'EOF'
feat: carry a mouse_clicks flag through the config protocol

The flag is a top-level key, not a [statusline] option, so the allowlist
that keeps mouse and key bindings out of reach from config.toml stays
exactly as it was.

It goes on line 2 of the wire protocol. bin/hsl-internal reads only line 1
and does not count lines, so it is unaffected; run-in-tmux and the exact
string comparison in test_hsl_internal.py both move by one line.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `run-in-tmux` の新プロトコル読み出し

**Files:**
- Modify: `scripts/run-in-tmux:215-249`
- Modify: `tests/test_tmux_runtime.py`

**Interfaces:**
- Consumes: Task 1 の行プロトコル（`enabled` / `mouse_clicks` / `count` / ペア）
- Produces: シェル変数 `MOUSE_CLICKS`（`true` または `false`）。Task 5 の
  `apply_mouse_clicks` が読む。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_tmux_runtime.py` の `TmuxRuntimeTests` に追加する。

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
        # Old writer, new runner: line 2 holds the option count, so the
        # boolean check rejects it before the line count is even reached.
        # Either way the runtime must refuse to start rather than misapply
        # options shifted by one line.
        old = self.base / "old-options"
        old.write_text("true\n1\nstatus-interval\n3\n")
        result = self.run_runtime("--session", "x", HSL_STATUS_OPTIONS=str(old))
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid hsl-config output", result.stderr)
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python3 -m pytest tests/test_tmux_runtime.py -q -k "protocol"`
Expected: FAIL。旧オフセットのままなので `status-interval` の値がずれ、
`maybe` は count として読まれて別のエラーになる。

- [ ] **Step 3: 実装する**

`scripts/run-in-tmux` の `apply_status_options` を書き換える。既存のコメントは残し、
オフセットの根拠を追記すること。

```sh
apply_status_options() {
    [ -n "$STATUS_OPTIONS" ] || return 0
    [ -f "$STATUS_OPTIONS" ] || {
        printf '%s\n' 'hsl: invalid hsl-config output' >&2
        return 2
    }
    # Line 1 is `enabled`, which bin/hsl-internal has already consumed; line 2
    # is `mouse_clicks`; the pairs start at line 4.
    MOUSE_CLICKS=$(sed -n '2p' "$STATUS_OPTIONS")
    case ${MOUSE_CLICKS:-} in
        true|false) ;;
        *)
            printf '%s\n' 'hsl: invalid hsl-config output' >&2
            return 2
            ;;
    esac
    count=$(sed -n '3p' "$STATUS_OPTIONS")
    case ${count:-x} in
        ''|*[!0-9]*)
            printf '%s\n' 'hsl: invalid hsl-config output' >&2
            return 2
            ;;
    esac
    lines=$(wc -l <"$STATUS_OPTIONS")
    [ "$lines" -eq $((3 + count * 2)) ] || {
        printf '%s\n' 'hsl: invalid hsl-config output' >&2
        return 2
    }
    index=0
    while [ "$index" -lt "$count" ]; do
        name=$(sed -n "$((4 + index * 2))p" "$STATUS_OPTIONS")
        # Command substitution strips the line terminator and nothing else, so
        # a value's leading and trailing spaces survive.
        value=$(sed -n "$((5 + index * 2))p" "$STATUS_OPTIONS")
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

`STATUS_OPTIONS` が空のときに `MOUSE_CLICKS` が未設定のままになるので、関数定義より前で
既定値を置く。`STATUS_OPTIONS=${HSL_STATUS_OPTIONS:-}` の直後（17 行目付近）に足す。

```sh
MOUSE_CLICKS=false
```

- [ ] **Step 4: テストが通ることを確認**

Run: `python3 -m pytest tests/test_tmux_runtime.py -q`
Expected: PASS（全件）。既存テストが落ちる場合はオフセットを見直す。

- [ ] **Step 5: 構文チェック**

Run: `sh -n scripts/run-in-tmux`
Expected: 出力なし・exit 0

- [ ] **Step 6: コミット**

```bash
git add scripts/run-in-tmux tests/test_tmux_runtime.py
git commit -m "$(cat <<'EOF'
feat: read mouse_clicks from the shifted config protocol

Version skew between the writer and this reader is now a fail-closed
contract rather than something the design claims cannot happen: an old
writer trips the line-count check and a new writer trips an old reader's
numeric check, so a half-updated install refuses to start instead of
misapplying options.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `base.conf` の root キーテーブル明示クリア

**Files:**
- Modify: `tmux/base.conf`
- Modify: `tests/test_tmux_runtime.py`

**Interfaces:**
- Consumes: なし
- Produces: 起動直後の tmux セッションで `list-keys -T root` が 0 行であること。
  Task 5 の配線と Task 7 の透過テストがこれに依存する。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_tmux_runtime.py` の実 tmux スモークテストのクラス（`SMOKE_HERDR` を使う方）に
root テーブルの観測を足す。まず `SMOKE_HERDR` の記録項目を増やす。

```python
    'root_keys': option('list-keys', '-T', 'root'),
```

そのうえでテストを追加する。

```python
    def test_the_root_key_table_is_empty(self):
        # `unbind-key -a` only clears the prefix table. tmux keeps 24 default
        # mouse bindings in root, inert only while the mouse is off, and they
        # would hijack herdr's input the moment it goes on.
        record = self.run_smoke()
        self.assertEqual(record["root_keys"], "")
```

`run_smoke` はこのクラスの既存の実行ヘルパーに合わせて呼び分けること。既存テストが
`self.herdr_record()` を使っているならそれに合わせる。

- [ ] **Step 2: テストが失敗することを確認**

Run: `python3 -m pytest tests/test_tmux_runtime.py -q -k "root_key"`
Expected: FAIL。24 本の binding が列挙される。

- [ ] **Step 3: 実装する**

`tmux/base.conf` の 4 行目を書き換える。

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

- [ ] **Step 4: テストが通ることを確認**

Run: `python3 -m pytest tests/test_tmux_runtime.py -q`
Expected: PASS（全件）

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

## Task 4: tmux バージョン判定

**Files:**
- Modify: `scripts/run-in-tmux`
- Modify: `tests/test_tmux_runtime.py`

**Interfaces:**
- Consumes: なし
- Produces: shell 関数 `mouse_ranges_supported()`。exit 0 なら対応、非 0 なら非対応。
  Task 5 の `apply_mouse_clicks` が最初に呼ぶ。

- [ ] **Step 1: fake tmux に `-V` を実装する**

`tests/test_tmux_runtime.py` の `FAKE_TMUX` に分岐を足す。`new-session` の分岐より**前**に置く。

```python
if args == ['-V']:
    print(os.environ.get('HSL_TEST_TMUX_VERSION', 'tmux 3.7b'))
    raise SystemExit(0)
```

- [ ] **Step 2: 失敗するテストを書く**

```python
    SUPPORTED = ["tmux 3.4", "tmux 3.7b", "tmux 4.0", "tmux next-3.9", "weird output"]
    UNSUPPORTED = ["tmux 3.3a", "tmux 3.0", "tmux 2.9", "tmux 1.8"]

    def mouse_argv(self, version):
        hook = self.base / "cfg" / "on-click.sh"
        make_executable(hook, "#!/bin/sh\nexit 0\n")
        self.run_runtime(
            "--session", "x",
            options=[("status-interval", "1")],
            mouse=True,
            HSL_TEST_TMUX_VERSION=version,
            HERDR_PLUGIN_CONFIG_DIR=str(self.base / "cfg"),
        )
        return self.tmux_argv()

    def test_enables_the_mouse_only_on_tmux_3_4_and_newer(self):
        for version in self.SUPPORTED:
            with self.subTest(version=version):
                self.setUp()
                argv = self.mouse_argv(version)
                self.assertIn(["set-option", "-g", "mouse", "on"],
                              [a[a.index("set-option"):] for a in argv
                               if "set-option" in a and "mouse" in a])

    def test_leaves_the_mouse_off_below_tmux_3_4(self):
        for version in self.UNSUPPORTED:
            with self.subTest(version=version):
                self.setUp()
                argv = self.mouse_argv(version)
                self.assertEqual(
                    [a for a in argv if "mouse" in a or "bind-key" in a], []
                )
```

`run_runtime` に `mouse=True` を通すため、`run_runtime` のシグネチャを拡張する。

```python
    def run_runtime(self, *args, options=None, mouse=False, **extra_env):
        ...
        if "HSL_STATUS_OPTIONS" not in extra_env:
            pairs = DEFAULT_OPTIONS if options is None else options
            env["HSL_STATUS_OPTIONS"] = str(
                write_protocol(self.base, pairs, mouse_clicks=mouse)
            )
```

- [ ] **Step 3: テストが失敗することを確認**

Run: `python3 -m pytest tests/test_tmux_runtime.py -q -k "tmux_3_4"`
Expected: FAIL。まだ `mouse` を触るコードが無いので両方とも空 argv になる。

- [ ] **Step 4: 実装する**

`scripts/run-in-tmux` に関数を足す。`apply_status_options` の直後が読みやすい。

```sh
# Unlike allow-passthrough below, user ranges cannot be detected by setting
# them: tmux validates the `range` keyword but not its value, so
# `range=user|p` is accepted on 3.3 too, where user ranges do not exist.
# Version parsing is the only test that discriminates, so it is used here and
# nowhere else. Anything unparseable is treated as new enough: those are
# development builds such as `next-3.9`, and refusing them would silently
# ignore an explicit opt-in.
mouse_ranges_supported() {
    version=$("$TMUX_BIN" -V 2>/dev/null) || return 0
    numbers=$(printf '%s' "$version" |
        sed -n 's/[^0-9]*\([0-9][0-9]*\)\.\([0-9][0-9]*\).*/\1 \2/p')
    [ -n "$numbers" ] || return 0
    major=${numbers% *}
    minor=${numbers#* }
    [ "$major" -gt 3 ] && return 0
    [ "$major" -eq 3 ] && [ "$minor" -ge 4 ] && return 0
    return 1
}
```

`sed` は最初の `MAJOR.MINOR` だけを取り出す。`tmux next-3.9` は `3 9`、`tmux 3.3a` は
`3 3`、解析できない文字列は空になり `return 0`（対応とみなす）へ落ちる。

- [ ] **Step 5: 判定を単体で確かめる**

```bash
for v in "tmux 3.4" "tmux 3.7b" "tmux 4.0" "tmux next-3.9" "weird output" \
         "tmux 3.3a" "tmux 3.0" "tmux 2.9"; do
  printf '%-18s -> %s\n' "$v" \
    "$(printf '%s' "$v" | sed -n 's/[^0-9]*\([0-9][0-9]*\)\.\([0-9][0-9]*\).*/\1 \2/p')"
done
```
Expected: `next-3.9` は `3 9`、`weird output` は空、`tmux 3.3a` は `3 3`。

- [ ] **Step 6: 構文チェックとコミット**

Run: `sh -n scripts/run-in-tmux`

```bash
git add scripts/run-in-tmux tests/test_tmux_runtime.py
git commit -m "$(cat <<'EOF'
feat: gate status-line mouse clicks on tmux 3.4

User ranges and mouse_status_range arrived in tmux 3.4. Below that the
hook could never fire, while `mouse on` would still cost the terminal its
native selection, so the feature must stay off rather than degrade.

This is the one place that parses tmux -V. Probing was tried first and
does not work: tmux validates the `range` style keyword but not its value,
so `range=user|p` is accepted on 3.3 as well.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: 条件付き配線

**Files:**
- Modify: `scripts/run-in-tmux`
- Modify: `tests/test_tmux_runtime.py`

**Interfaces:**
- Consumes: `MOUSE_CLICKS`（Task 2）、`mouse_ranges_supported()`（Task 4）
- Produces: tmux 側に user option `@hsl_on_click` と root テーブルの 4 binding
  （`MouseDown1Status` / `MouseDown3Status` / `WheelUpStatus` / `WheelDownStatus`）。
  Task 6・7 の結合テストがこれを叩く。

- [ ] **Step 1: 失敗するテストを書く**

```python
    def wire(self, **kw):
        cfg = self.base / "cfg"
        env = {"HERDR_PLUGIN_CONFIG_DIR": str(cfg)}
        env.update(kw.pop("env", {}))
        return self.run_runtime("--session", "x", mouse=True, **env, **kw)

    def test_wires_exactly_four_status_bindings(self):
        cfg = self.base / "cfg"
        make_executable(cfg / "on-click.sh", "#!/bin/sh\nexit 0\n")
        result = self.wire()
        self.assertEqual(result.returncode, 0, result.stderr)
        binds = [a for a in self.tmux_argv() if "bind-key" in a]
        self.assertEqual(len(binds), 4)
        keys = [a[a.index("bind-key") + 2] for a in binds]
        self.assertEqual(
            sorted(keys),
            ["MouseDown1Status", "MouseDown3Status", "WheelDownStatus", "WheelUpStatus"],
        )
        for args in binds:
            command = args[-1]
            self.assertIn("#{q:@hsl_on_click}", command)
            self.assertIn("#{q:mouse_status_range}", command)
            self.assertIn("#{q:mouse_x}", command)
            self.assertIn("#{q:mouse_status_line}", command)
            self.assertIn(">/dev/null 2>&1 || true", command)
            self.assertIn("-b", args)
            # Hand-quoting is the injection bug; only #{q:} may appear.
            self.assertNotIn("'#{", command)
        self.assertIn(
            ["set-option", "-g", "@hsl_on_click", str(cfg / "on-click.sh")],
            [a[a.index("set-option"):] for a in self.tmux_argv() if "set-option" in a],
        )

    def test_leaves_the_mouse_off_when_the_hook_is_missing(self):
        result = self.wire()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("mouse clicks stay off", result.stderr)
        self.assertEqual([a for a in self.tmux_argv() if "bind-key" in a], [])
        self.assertEqual(
            [a for a in self.tmux_argv() if "mouse" in a and "set-option" in a], []
        )

    def test_leaves_the_mouse_off_when_the_hook_is_not_executable(self):
        cfg = self.base / "cfg"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "on-click.sh").write_text("#!/bin/sh\nexit 0\n")
        (cfg / "on-click.sh").chmod(0o600)
        result = self.wire()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("is not executable", result.stderr)
        self.assertEqual([a for a in self.tmux_argv() if "bind-key" in a], [])

    def test_leaves_the_mouse_off_when_the_config_dir_is_unknown(self):
        result = self.run_runtime("--session", "x", mouse=True,
                                  HERDR_PLUGIN_CONFIG_DIR="")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("plugin config dir is unknown", result.stderr)
        self.assertEqual([a for a in self.tmux_argv() if "bind-key" in a], [])

    def test_fails_closed_when_a_binding_cannot_be_applied(self):
        cfg = self.base / "cfg"
        make_executable(cfg / "on-click.sh", "#!/bin/sh\nexit 0\n")
        result = self.wire(env={"HSL_TEST_TMUX_REJECT": "mouse"})
        self.assertEqual(result.returncode, 2)
```

`HSL_TEST_TMUX_REJECT` は既存の fake tmux が `set-option` にのみ効くので、
`bind-key` も拒否できるよう `FAKE_TMUX` の該当行を広げる。

```python
if os.environ.get('HSL_TEST_TMUX_REJECT', '') in args and (
    'set-option' in args or 'bind-key' in args
):
    print('rejected by the fake tmux', file=sys.stderr)
    raise SystemExit(1)
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python3 -m pytest tests/test_tmux_runtime.py -q -k "wire or hook or config_dir or fails_closed"`
Expected: FAIL。まだ `apply_mouse_clicks` が無い。

- [ ] **Step 3: 実装する**

`scripts/run-in-tmux` に足す。

```sh
# Status-line mouse clicks are opt-in and inert without a hook: enabling the
# mouse with nothing to dispatch to would only cost the user the terminal's
# native selection (tmux asks the outer terminal for 1000/1002/1003/1006) and
# give nothing back.
#
# Every field travels through tmux's own `#{q:...}` sh-escaper. run-shell
# format-expands the whole string before handing it to /bin/sh -c, so a range
# name is attacker-controlled text landing inside a shell command line: a
# 14-byte name is enough to break out of hand-written single quotes and run
# arbitrary code. The hook path goes the same way, as a user option, so a `#`
# in the config path cannot be re-expanded either. shell_quote is deliberately
# not used here; it escapes for a generated script, not for tmux's two-stage
# expansion.
#
# -b, the redirect and the `|| true` are three separate requirements: without
# -b the hook blocks the command queue, without the redirect its stdout is
# drawn over Herdr, and without `|| true` a non-zero exit still is.
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

呼び出しを既存の `apply_status_options` チェックの直後に足す。

```sh
if ! apply_status_options; then
    exit 2
fi

if ! apply_mouse_clicks; then
    exit 2
fi
```

- [ ] **Step 4: テストが通ることを確認**

Run: `python3 -m pytest tests/test_tmux_runtime.py -q`
Expected: PASS（全件）

- [ ] **Step 5: 構文チェックとコミット**

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

A missing hook, a hook without its execute bit, and an unknown config dir
each leave the mouse off with a warning rather than producing a bar that
swallows clicks. Any failure from tmux itself exits 2 while the launcher
is still blocked in wait-for, so a half-wired session never reaches the
user.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: pty フィクスチャとクリック配送の結合テスト

**Files:**
- Create: `tests/mouse_pty.py`
- Create: `tests/test_tmux_mouse.py`

**Interfaces:**
- Consumes: Task 5 が張る 4 binding
- Produces: `tests/mouse_pty.py` の `TmuxPty` クラス。Task 7 が同じものを使う。
  - `TmuxPty(socket, conf, session, cols=80, rows=24)` — コンテキストマネージャ
  - `.click(col, row, button=0)` — SGR の press/release を注入（1 始まり座標）
  - `.wheel(col, row, up=True)` — ホイールを注入
  - `.motion(col, row)` — ボタン無しモーションを注入
  - `.drawn()` — クライアントへ描画されたバイト列を返す

- [ ] **Step 1: フィクスチャを書く**

`tests/mouse_pty.py` を新規作成する。

```python
"""Drive a real tmux client over a pty and inject SGR mouse events.

tmux only reports the mouse to a client on a terminal, so the runtime
behaviour these tests cover cannot be reached through the fake tmux used in
test_tmux_runtime.py. A pty is the smallest thing that is a terminal.
"""

import fcntl
import os
import pty
import select
import struct
import subprocess
import termios
import time

# Inner-app stub asking for any-event tracking and SGR encoding, the same modes
# Herdr relays on behalf of the applications it hosts. It records raw stdin so a
# test can assert on the exact bytes tmux forwards.
INNER_APP = r"""
import os, sys, time, tty
log = sys.argv[1]
open(log, "w").close()
tty.setraw(0)
sys.stdout.write("\033[?{mode}h\033[?1006h")
sys.stdout.flush()
end = time.time() + 60
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
"""


class TmuxPty:
    def __init__(self, tmux, socket, conf, session, cols=80, rows=24):
        self.tmux = tmux
        self.socket = socket
        self.conf = conf
        self.session = session
        self.cols = cols
        self.rows = rows
        self._buffer = bytearray()

    def __enter__(self):
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.environ["TERM"] = "xterm-256color"
            os.execvp(
                self.tmux,
                [self.tmux, "-L", self.socket, "-f", self.conf,
                 "attach", "-t", self.session],
            )
        fcntl.ioctl(
            self.fd, termios.TIOCSWINSZ,
            struct.pack("HHHH", self.rows, self.cols, 0, 0),
        )
        self._drain(2.0)
        self._buffer.clear()
        return self

    def __exit__(self, *exc):
        subprocess.run(
            [self.tmux, "-L", self.socket, "detach-client"], capture_output=True
        )
        time.sleep(0.3)
        try:
            os.close(self.fd)
        except OSError:
            pass

    def _drain(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            if select.select([self.fd], [], [], 0.1)[0]:
                try:
                    self._buffer.extend(os.read(self.fd, 65536))
                except OSError:
                    break

    def _send(self, sequence, settle=0.5):
        os.write(self.fd, sequence.encode())
        time.sleep(0.15)
        self._drain(settle)

    def click(self, col, row, button=0):
        self._send(f"\033[<{button};{col};{row}M", settle=0.1)
        self._send(f"\033[<{button};{col};{row}m")

    def wheel(self, col, row, up=True):
        self._send(f"\033[<{64 if up else 65};{col};{row}M")

    def motion(self, col, row):
        self._send(f"\033[<35;{col};{row}M")

    def drawn(self):
        return bytes(self._buffer)


def inner_app_command(log_path, mode=1003):
    """Return a shell command that runs the inner-app stub in a tmux pane."""
    program = INNER_APP.replace("{mode}", str(mode))
    return f"python3 -c {shell_quote(program)} {shell_quote(str(log_path))}"


def shell_quote(text):
    return "'" + text.replace("'", "'\\''") + "'"
```

- [ ] **Step 2: 失敗するテストを書く**

`tests/test_tmux_mouse.py` を新規作成する。

```python
import pathlib
import shutil
import subprocess
import tempfile
import unittest

from tests.mouse_pty import TmuxPty

TMUX = shutil.which("tmux")


def tmux_version_at_least_3_4():
    if not TMUX:
        return False
    out = subprocess.run([TMUX, "-V"], text=True, capture_output=True).stdout
    digits = "".join(c if c.isdigit() or c == "." else " " for c in out).split()
    if not digits:
        return False
    major, _, minor = digits[0].partition(".")
    return int(major) > 3 or (int(major) == 3 and int(minor or 0) >= 4)


@unittest.skipUnless(tmux_version_at_least_3_4(), "needs tmux 3.4 or newer")
class StatusClickTests(unittest.TestCase):
    SOCKET = "hsl-mouse-test"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = pathlib.Path(self.tmp.name)
        self.addCleanup(
            lambda: subprocess.run(
                [TMUX, "-L", self.SOCKET, "kill-server"], capture_output=True
            )
        )
        self.hook_log = self.base / "hook.log"

    def start(self, status_format, hook_body, hook_dir="cfg dir"):
        # A directory name with a space, a '#' and a quote: all three are
        # hazards for the two-stage expansion run-shell performs.
        directory = self.base / f"{hook_dir}#x'y"
        directory.mkdir(parents=True, exist_ok=True)
        hook = directory / "on-click.sh"
        hook.write_text(hook_body)
        hook.chmod(0o700)
        conf = self.base / "mouse.conf"
        conf.write_text(
            "set-option -g prefix None\n"
            "unbind-key -a\n"
            "unbind-key -a -T root\n"
            "set-option -g mouse on\n"
            "set-option -g status on\n"
            f'set-option -g status-format[0] "{status_format}"\n'
        )
        subprocess.run(
            [TMUX, "-L", self.SOCKET, "-f", str(conf), "new-session",
             "-d", "-s", "x", "-x", "80", "-y", "23", "sleep 120"],
            check=True, capture_output=True,
        )
        subprocess.run(
            [TMUX, "-L", self.SOCKET, "set-option", "-g", "@hsl_on_click", str(hook)],
            check=True, capture_output=True,
        )
        for key, name in (
            ("MouseDown1Status", "left"),
            ("MouseDown3Status", "right"),
            ("WheelUpStatus", "wheelup"),
            ("WheelDownStatus", "wheeldown"),
        ):
            subprocess.run(
                [TMUX, "-L", self.SOCKET, "bind-key", "-n", key, "run-shell", "-b",
                 f"#{{q:@hsl_on_click}} {name} #{{q:mouse_status_range}} "
                 f"#{{q:mouse_x}} #{{q:mouse_status_line}} >/dev/null 2>&1 || true"],
                check=True, capture_output=True,
            )
        return conf

    RECORDING_HOOK = (
        "#!/bin/sh\n"
        "printf '%s|%s|%s|%s\\n' \"$1\" \"$2\" \"$3\" \"$4\" >> LOG\n"
    )

    def recording_hook(self):
        return self.RECORDING_HOOK.replace("LOG", str(self.hook_log))

    def lines(self):
        if not self.hook_log.exists():
            return []
        return self.hook_log.read_text().splitlines()

    def test_delivers_button_range_and_coordinates(self):
        conf = self.start(
            "#[align=left]#[range=user|btn] BTN #[norange]tail",
            self.recording_hook(),
        )
        with TmuxPty(TMUX, self.SOCKET, str(conf), "x") as term:
            term.click(3, 24)
            term.click(3, 24, button=2)
            term.wheel(3, 24)
        self.assertEqual(
            self.lines(),
            ["left|btn|2|0", "right|btn|2|0", "wheelup|btn|2|0"],
        )

    def test_a_range_name_cannot_inject_a_shell_command(self):
        # 14 bytes, inside tmux's 15-byte limit, and enough to escape
        # hand-written single quotes. It must arrive as one literal argument.
        marker = self.base / "pwned"
        evil = "a';id>/tmp/x;'"
        conf = self.start(
            f"#[align=left]#[range=user|{evil}] BTN #[norange]tail",
            self.recording_hook(),
        )
        with TmuxPty(TMUX, self.SOCKET, str(conf), "x") as term:
            term.click(3, 24)
        self.assertEqual(self.lines(), [f"left|{evil}|2|0"])
        self.assertFalse(marker.exists())

    def test_a_noisy_failing_hook_draws_nothing(self):
        conf = self.start(
            "#[align=left]#[range=user|btn] BTN #[norange]tail",
            "#!/bin/sh\necho NOISE-MARKER\nexit 7\n",
        )
        with TmuxPty(TMUX, self.SOCKET, str(conf), "x") as term:
            term.click(3, 24)
            drawn = term.drawn()
        self.assertNotIn(b"NOISE-MARKER", drawn)
        self.assertNotIn(b"returned 7", drawn)

    def test_clicking_outside_every_range_does_nothing(self):
        conf = self.start(
            "#[align=left]#[range=user|btn] BTN #[norange]tail",
            self.recording_hook(),
        )
        with TmuxPty(TMUX, self.SOCKET, str(conf), "x") as term:
            term.click(40, 24)
        self.assertEqual(self.lines(), [])
```

- [ ] **Step 3: テストが失敗することを確認**

Run: `python3 -m pytest tests/test_tmux_mouse.py -q`
Expected: FAIL。`tests/mouse_pty.py` の import か、フックが呼ばれず空の
`lines()` になる。

- [ ] **Step 4: 座標の裏取り（確定値。変更不要）**

上のテストの座標は実測済みで、`col=3` / `row=24` が `x=2`・`line=0` を生む。
`status-format[0]` が `#[align=left]#[range=user|btn] BTN #[norange]tail` のとき、
描画は `x=0` が空白、`x=1..3` が `BTN`、`x=4` が空白、`x=5` 以降が `tail`。
range の当たり判定は仕様 F14 により `x=0..5`（右端が 1 カラム広い）。`col=40` は
どの range にも入らないため不発になる。

テストが落ちた場合にだけ、次で実際の当たり判定を確かめる。

```bash
tmux -L hsl-mouse-test display-message -p '#{status-format[0]}'
```

- [ ] **Step 5: テストが通ることを確認**

Run: `python3 -m pytest tests/test_tmux_mouse.py -q`
Expected: PASS（4 件）

- [ ] **Step 6: コミット**

```bash
git add tests/mouse_pty.py tests/test_tmux_mouse.py
git commit -m "$(cat <<'EOF'
test: cover status-click dispatch against a real tmux over a pty

tmux only reports the mouse to a client on a terminal, so the fake tmux
cannot reach any of this. The fixture is committed rather than left in a
scratch file because the injection case is the reason the wiring looks the
way it does: a 14-byte range name that escapes hand-written quotes, a hook
path holding a space, a '#' and a quote, and a hook that prints on stdout
and exits non-zero.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: pane 透過の回帰テスト

**Files:**
- Modify: `tests/test_tmux_mouse.py`

**Interfaces:**
- Consumes: `tests/mouse_pty.py` の `TmuxPty` と `inner_app_command`（Task 6）
- Produces: なし（テストのみ）

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_tmux_mouse.py` に追加する。

```python
from tests.mouse_pty import TmuxPty, inner_app_command


@unittest.skipUnless(tmux_version_at_least_3_4(), "needs tmux 3.4 or newer")
class PanePassThroughTests(unittest.TestCase):
    SOCKET = "hsl-mouse-pane-test"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = pathlib.Path(self.tmp.name)
        self.addCleanup(
            lambda: subprocess.run(
                [TMUX, "-L", self.SOCKET, "kill-server"], capture_output=True
            )
        )
        self.app_log = self.base / "app.log"

    def start(self, mode=1003, clear_root=True, position="bottom"):
        conf = self.base / "pane.conf"
        conf.write_text(
            "set-option -g prefix None\n"
            "unbind-key -a\n"
            + ("unbind-key -a -T root\n" if clear_root else "")
            + "set-option -g mouse on\n"
            "set-option -g status on\n"
            f"set-option -g status-position {position}\n"
        )
        subprocess.run(
            [TMUX, "-L", self.SOCKET, "-f", str(conf), "new-session",
             "-d", "-s", "x", "-x", "80", "-y", "23",
             inner_app_command(self.app_log, mode=mode)],
            check=True, capture_output=True,
        )
        time.sleep(1.0)
        return conf

    def received(self):
        if not self.app_log.exists():
            return []
        return self.app_log.read_text().splitlines()

    def test_forwards_clicks_motion_and_wheel_in_both_mouse_modes(self):
        for mode in (1000, 1003):
            with self.subTest(mode=mode):
                self.setUp()
                conf = self.start(mode=mode)
                with TmuxPty(TMUX, self.SOCKET, str(conf), "x") as term:
                    term.click(10, 5)
                    term.motion(12, 6)
                    term.wheel(10, 5)
                blob = "\n".join(self.received())
                self.assertIn(r"\x1b[<0;10;5M", blob)
                self.assertIn(r"\x1b[<0;10;5m", blob)
                self.assertIn(r"\x1b[<64;10;5M", blob)
                if mode == 1003:
                    self.assertIn(r"\x1b[<35;12;6M", blob)

    def test_translates_coordinates_to_pane_relative(self):
        # With the status line at the top the pane starts one row down, so a
        # click on terminal row 5 must reach the application as row 4.
        conf = self.start(position="top")
        with TmuxPty(TMUX, self.SOCKET, str(conf), "x") as term:
            term.click(10, 5)
        self.assertIn(r"\x1b[<0;10;4M", "\n".join(self.received()))

    def test_leaving_the_root_table_populated_breaks_pass_through(self):
        # Guards tmux/base.conf's `unbind-key -a -T root`. Without it tmux's
        # default MouseDown1Pane binding runs instead, so the bytes the
        # application sees are no longer the ones the terminal sent.
        conf = self.start(clear_root=False)
        keys = subprocess.run(
            [TMUX, "-L", self.SOCKET, "list-keys", "-T", "root"],
            text=True, capture_output=True,
        ).stdout
        self.assertIn("DoubleClick1Pane", keys)
        self.assertIn("MouseDown1Control9", keys)
```

`import time` を先頭に足すこと。

- [ ] **Step 2: テストが失敗することを確認**

Run: `python3 -m pytest tests/test_tmux_mouse.py -q -k "PanePassThrough"`
Expected: 最初は `inner_app_command` の import エラーか、`received()` が空になる。

- [ ] **Step 3: フィクスチャの不足を埋める**

`INNER_APP` が `{mode}` を `str.replace` で埋める作りなので、`1000` を渡したときに
`\033[?1000h` になることを確認する。ならない場合は `inner_app_command` を直す。

```bash
python3 -c "
from tests.mouse_pty import INNER_APP
print(INNER_APP.replace('{mode}','1000')[:200])
"
```

- [ ] **Step 4: テストが通ることを確認**

Run: `python3 -m pytest tests/test_tmux_mouse.py -q`
Expected: PASS（全件）

- [ ] **Step 5: コミット**

```bash
git add tests/test_tmux_mouse.py
git commit -m "$(cat <<'EOF'
test: pin pane mouse pass-through and coordinate translation

Turning the mouse on must not cost Herdr its own mouse input. These cover
both 1000 and 1003, including motion, and pin the fact that tmux hands the
application pane-relative rows: with the status line on top, terminal row
5 arrives as row 4.

The last test guards base.conf's `unbind-key -a -T root` by showing what
comes back without it, including the kill-pane menu binding.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: ドキュメントと安全論拠の更新

**Files:**
- Modify: `scripts/default-config.toml`
- Modify: `skills/customize-herdr-statusline/SKILL.md`
- Modify: `src/config.rs`（`option_name` のコメント）
- Modify: `tmux/base.conf`（コメント）

**Interfaces:**
- Consumes: Task 1〜7 の完成した挙動
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
# `#[norange]`, where NAME is at most 15 bytes. Its stdout and exit status are
# discarded, so call `tmux display-message` if you need to say something.
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
create it.

Mark an area with `#[range=user|NAME]` ... `#[norange]`. `NAME` is at most 15
bytes and reaches the hook as its second argument. Shell metacharacters are
carried safely, but keep names to letters, digits and `_` for readability.

Two constraints shape the layout:

- Put user ranges in a `status_format_N` you define yourself. Inside
  `status_left` and `status_right` tmux wraps them in `range=left` and
  `range=right`, which shifts the clickable area one column to the right of
  the text.
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

- [ ] **Step 4: `tmux/base.conf` の先頭コメントを更新する**

1 行目を書き換える。

```tmux
# Minimal, status-line-only tmux for a disposable herdr-statusline server.
# The session owns no keys and takes no input, with one bounded exception:
# `mouse_clicks = true` makes run-in-tmux turn the mouse on and add four fixed
# status-line bindings after this file has been read.
```

- [ ] **Step 5: 検証**

```bash
cargo test --quiet
python3 -m pytest tests/ -q
```

Expected: すべて PASS。`default-config.toml` は TOML なので `sh -n` の対象外。

このステップで壊しやすいのは次の 2 つで、いずれも **`mouse_clicks` をコメントアウトの
まま置く限り通る**。

- `src/init.rs` の `creates_both_files_and_the_shipped_config_parses` は、既定
  `config.toml` の解析結果が `status-interval = 1` と `status-right` の 2 件だけで
  あることを検査する。コメント行は解析結果を変えない
- `src/init.rs` の `ships_the_repository_copy_of_each_template` は、インストールされた
  ファイルがリポジトリのテンプレートと**バイト一致**することを検査する。どちらも同じ
  `include_str!` 由来なので、テンプレートだけを編集する限り一致は保たれる

`tests/test_consistency.py` はプラグイン ID・バージョン・ヘルパーパス・managed marker の
整合だけを見ており、本タスクの変更対象と重ならない。

- [ ] **Step 6: コミット**

```bash
git add scripts/default-config.toml skills/customize-herdr-statusline/SKILL.md \
        src/config.rs tmux/base.conf
git commit -m "$(cat <<'EOF'
docs: describe the click hook and restate the safety argument

The allowlist comment claimed to be the whole safety argument. With a
top-level key that turns the mouse on it is not, so it now says what the
exception is and how it is bounded: four fixed bindings whose names and
bodies live in run-in-tmux, unreachable from config.toml.

The user-facing docs state the price of the feature up front — mouse
reporting costs the terminal its native selection — along with the tmux
3.4 floor, the one-column hit-area offset, and the absence of hover.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**Spec coverage:**

| 仕様 | タスク |
| --- | --- |
| §5.1 `mouse_clicks` フラグ | Task 1 |
| §5.2 行プロトコル・3 消費者・版ずれ契約 | Task 1（writer と test_hsl_internal）、Task 2（runner と skew） |
| §5.3 base.conf root クリア | Task 3 |
| §5.4 プロトコル読み出し | Task 2 |
| §5.5 条件付き配線 | Task 5 |
| §5.6 tmux 3.4 判定 | Task 4 |
| §5.7 default-config.toml | Task 8 |
| §5.8 init.rs 変更なし | 該当タスクなし（意図的） |
| §5.9 SKILL.md | Task 8 |
| §6 フック契約 | Task 5（実装）、Task 6（検証）、Task 8（文書化） |
| §7 安全論拠の再定義 | Task 8 |
| §8 既知の制約 | Task 8 |
| §9 テスト 1〜5 | Task 1 |
| §9 テスト 6〜10 | Task 1・Task 2 |
| §9 テスト 11〜14 | Task 3・Task 5 |
| §9 テスト 15〜16 | Task 6 |
| §9 テスト 17 | Task 4 |
| §9 テスト 18〜21 | Task 7 |

**Placeholder scan:** 実施済み。Task 6 Step 4 と Task 7 Step 3 は「実測で座標を合わせる」
手順だが、確かめ方のコマンドと期待値を明記しているため作業指示として完結している。

**Type consistency:** `mouse_clicks`（Rust フィールド・TOML キー・Python 引数）、
`MOUSE_CLICKS`（sh 変数）、`@hsl_on_click`（tmux user option）、
`mouse_ranges_supported`・`apply_mouse_clicks`（sh 関数）、
`TmuxPty`・`inner_app_command`（Python）で全タスク一貫。

# リポジトリ健全化 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 中核ロジックの振る舞いを一切変えずに、配布物の体裁・CI の検出力・テスト実行時間・出荷スクリプトの副作用という周辺の欠落を 15 項目まとめて解消する。

**Architecture:** 6 ワークストリーム（A: 出荷コード修正 / B: 配布 / C: CI / D: テスト基盤 / E: ドキュメントと契約 / F: リポジトリ衛生）を 13 タスクに分解する。`.github/workflows/ci.yml` を触るタスクが 6 件あるため、それらは 1 本の直列系列として並べる。テストは既存の規律に従い、本番の配線そのものを起動して検証する。

**Tech Stack:** Rust (edition 2021, MSRV 1.85) / POSIX sh / Python 3.12 + pytest 9 + pytest-xdist 3 / tmux 3.4+ / GitHub Actions

**Spec:** `docs/superpowers/specs/2026-08-15-repo-hardening-design.md`（rev4、codex ACCEPT 済み）

## Global Constraints

仕様から転記した、全タスクに暗黙に適用される要求。値は仕様のまま。

- **中核ロジックの振る舞いを変えない。** `bin/hsl-internal` の `is_interactive()` / `cli_session()` / `skips_the_local_session()` の判定結果、`src/config.rs` の `option_name()` 許可リストと `write_protocol()` の行プロトコル、`src/purge.rs` の検証則、`scripts/run-in-tmux` の tmux 配線、`tmux/base.conf` の全内容。機械的置換は可、振る舞いの変更は不可。
- **出荷プラグインのランタイム依存をゼロに保つ。** pytest / pytest-xdist は dev 依存であり、`scripts/build.sh` はテストを一切参照しない。
- **MSRV は `1.85`。** これは「保証する下限」であって「証明された最小」ではない。
- **シェルは POSIX sh。** bash 固有機能を使わない。CI の `sh -n` と shellcheck `-s sh` の対象は同一の 7 ファイル: `bin/hsl-internal`、`scripts/build.sh`、`scripts/install-launcher.sh`、`scripts/lib/shell-quote.sh`、`scripts/launcher-body.sh`、`scripts/run-in-tmux`、`scripts/default-herdr-info.sh`。
- **タグは 2 本のみ。** `v0.1.0` → `0622df4`、`v0.1.2` → `16bd1b7`。`0.1.1` は存在しないので作らない。
- **黙った skip を作らない。** 新設する検証も同じ規律に従う。既定集合から外すものは skip ではなく deselect にする。
- **著作権表記は `Copyright (c) 2026 IIAD Yusuke`。**
- **`herdr --help` は全 top-level subcommand を列挙しない**（`plugin` が現れない）。help 解析から網羅性を主張しない。

## ファイル構成

| ファイル | 責務 | タスク |
| --- | --- | --- |
| `LICENSE` | MIT 全文 | 1 |
| `CHANGELOG.md` | 版と変更の対応 | 1 |
| `.gitignore` | worktree ディレクトリの除外 | 1 |
| `scripts/default-herdr-info.sh` | git lock 回避 | 2 |
| `tests/test_herdr_info.py` | git shim による環境検証 | 2 |
| `scripts/build.sh` | PATH 警告 | 3 |
| `tests/test_build.py` | PATH 警告の検証 | 3 |
| `.github/workflows/ci.yml` | 権限・キャッシュ・重複解消・setup-python | 4, 5, 6, 7, 12, 13 |
| 7 つのシェルファイル | shellcheck 警告の書き換え | 5 |
| `requirements-dev.txt` / `pyproject.toml` / `Makefile` | pytest 実行機構 | 6 |
| `tests/helpers.py` | `ensure_helper` の縮退 | 6 |
| `tests/conftest.py` | skip 禁止フックと live マーカー | 7 |
| `tests/mouse_pty.py` | ready marker による同期 | 8 |
| `scripts/launcher-body.sh` | `--hsl-check-config` | 9 |
| `tests/test_launcher.py` | CLI 契約の検証 | 9 |
| `tests/fixtures/herdr-0.8.0/*.txt` | CLI snapshot | 10 |
| `tests/test_herdr_cli_contract.py` | snapshot と live 比較 | 10 |
| `tests/test_hsl_internal.py` | argv 行列の拡張 | 10 |
| `README.md` | mouse_clicks 節 | 11 |
| `Cargo.toml` | `rust-version` | 12 |
| `tests/test_consistency.py` | タグとバージョンの対応 | 13 |

---

## Task 1: リポジトリメタデータ（LICENSE / CHANGELOG / .gitignore）

**Files:**
- Create: `LICENSE`
- Create: `CHANGELOG.md`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: なし
- Produces: なし（後続タスクはこれらのファイルを参照しない）

3 ファイルとも新規または 1 行追加で、テストコードを伴わない。レビュアが個別に却下する意味が薄いため 1 タスクにまとめる。検証は AC-B1-1 / AC-B1-2 / AC-B2-1 / AC-F-1 を手で確認する。

- [ ] **Step 1: LICENSE を作る**

MIT の全文を書く。1 行目は `MIT License`、3 行目に著作権行。

```
MIT License

Copyright (c) 2026 IIAD Yusuke

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 2: LICENSE が Cargo.toml と矛盾しないことを確認**

Run: `grep '^license' Cargo.toml`
Expected: `license = "MIT"`

- [ ] **Step 3: CHANGELOG.md を作る**

`0.1.1` の節を**作らないこと**。存在しない版である。

```markdown
# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.2] - 2026-08-10

### Added

- Status line mouse clicks, opt-in through `mouse_clicks` in `config.toml`.
  Clicks on a `#[range=user|NAME]` area are dispatched to an `on-click.sh`
  hook that the user or another plugin owns. Needs tmux 3.4 or newer.
- `herdr-info.sh`, a generated helper showing the focused Herdr pane, its
  working directory and its Git state.
- A bundled customization skill, installed into the plugin config directory
  for both `.agents/skills/` and `.claude/skills/`.

### Changed

- `config.toml` now configures tmux status options directly instead of
  running a user-supplied status line script.

## [0.1.0] - 2026-08-01

### Added

- Initial release. Wraps interactive Herdr sessions in a disposable,
  status-line-only tmux session; passes utility commands straight through.
- `hsl` launcher installed to `~/.local/bin`, with `hsl uninstall [--purge]`.
```

> 日付は各コミットの author date に合わせて実装時に確認する:
> `git show -s --format=%as 0622df4` と `git show -s --format=%as 16bd1b7`。

- [ ] **Step 4: .gitignore に worktree ディレクトリを足す**

`.claude/` 全体ではなく `worktrees/` のみ。既存の `/target` と同じく直下限定を意図して `/` を前置する。

```
/target
/.claude/worktrees/
__pycache__/
*.pyc
```

- [ ] **Step 5: 無視規則を確認**

Run: `git check-ignore -v .claude/worktrees && git check-ignore .claude/settings.json; echo "settings exit=$?"`
Expected: `.claude/worktrees` は一致する。`.claude/settings.json` は一致せず `exit=1`。

- [ ] **Step 6: Commit**

```bash
git add LICENSE CHANGELOG.md .gitignore
git commit -m "docs: add LICENSE and CHANGELOG, and ignore the worktree directory"
```

---

## Task 2: 出荷スクリプトの git lock 回避

**Files:**
- Modify: `scripts/default-herdr-info.sh`（`set -u` の直後）
- Test: `tests/test_herdr_info.py`

**Interfaces:**
- Consumes: 既存の `self.make_repo()` / `self.run_template()` / `self.fakebin` / `self.env`
- Produces: なし

**⚠️ 罠**: `test_herdr_info.py` の `self.git()` は `env=self.env` で実行され、`base_env` は `fakebin` を PATH 先頭に置く。したがって **git shim を `setUp` に置くとフィクスチャ構築の git 呼び出しまでログに混入する**。shim は `make_repo()` を呼んだ**後**にテスト本体で設置すること。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_herdr_info.py` の import に `shutil` を足し、モジュール定数と テストを追加する。

```python
# module level, near FAKE_HERDR
GIT_ENV_SHIM = """#!/bin/sh
printf '%s\\n' "${GIT_OPTIONAL_LOCKS:-<unset>}" >> "$HSL_TEST_GIT_ENV_LOG"
exec REAL_GIT "$@"
"""
```

```python
    def test_every_git_call_disables_optional_locks(self):
        # `git status` takes index.lock by default, and this template is
        # advertised for status_interval = 1, so it would contend with the
        # user's own git commands once a second.
        repo = self.make_repo()

        # The shim goes in only AFTER make_repo: self.git() resolves `git`
        # through the same PATH, so installing it earlier would log the
        # fixture's own calls.
        log = self.base / "git-env.log"
        real_git = shutil.which("git")
        self.assertIsNotNone(real_git, "these tests need a real git")
        make_executable(
            self.fakebin / "git", GIT_ENV_SHIM.replace("REAL_GIT", real_git)
        )

        self.run_template(
            pane_json(foreground_cwd=str(repo)),
            HSL_TEST_GIT_ENV_LOG=str(log),
            # Force the ambient value to 1 so this test is red without the
            # export, even on a machine that already exports 0.
            GIT_OPTIONAL_LOCKS="1",
        )

        recorded = log.read_text().split()
        self.assertTrue(recorded, "the template must invoke git at least once")
        self.assertEqual(
            set(recorded), {"0"}, f"every git call must see 0, got {recorded}"
        )
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python3 -m unittest tests.test_herdr_info.HerdrInfoTemplateTests.test_every_git_call_disables_optional_locks -v`
Expected: FAIL。`{'1'}` が得られて `{'0'}` と一致しない。

- [ ] **Step 3: 最小の実装**

`scripts/default-herdr-info.sh` の `set -u` の直後に追加する。

```sh
#!/bin/sh
# Print the focused herdr pane, its working directory and its git state as
# coloured tmux status-line segments.
set -u

# `git status` refreshes the index and takes index.lock to write it back.
# This runs once per status-interval -- every second with the shipped
# config -- so without this it would contend with the user's own git
# commands. GIT_OPTIONAL_LOCKS=0 tells git not to take the lock at all;
# it is not a fallback for when the lock is unavailable. Set once at the
# top so that any git call added to this template later is covered too.
export GIT_OPTIONAL_LOCKS=0

PANE_STYLE='#[fg=#ffffff,bg=#5a45a5]'
```

- [ ] **Step 4: テストが通ることを確認**

Run: `python3 -m unittest tests.test_herdr_info -v`
Expected: 17 件すべて PASS（既存 16 + 新規 1）。

- [ ] **Step 5: Commit**

```bash
git add scripts/default-herdr-info.sh tests/test_herdr_info.py
git commit -m "fix: stop the herdr-info template from taking git's index lock"
```

---

## Task 3: build.sh の PATH 警告

**Files:**
- Modify: `scripts/build.sh`（末尾）
- Test: `tests/test_build.py`

**Interfaces:**
- Consumes: 既存の `self.run_build()` / `self.env` / `self.bindir`
- Produces: なし

- [ ] **Step 1: 失敗するテストを書く**

```python
    def test_warns_when_the_launcher_directory_is_not_on_path(self):
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(self.bindir), result.stderr)
        self.assertIn("PATH", result.stderr)

    def test_stays_quiet_when_the_launcher_directory_is_on_path(self):
        self.env["PATH"] = f"{self.bindir}:{self.env['PATH']}"
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("PATH", result.stderr)
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python3 -m unittest tests.test_build -v`
Expected: `test_warns_when_the_launcher_directory_is_not_on_path` が FAIL（stderr が空）。

- [ ] **Step 3: 最小の実装**

`scripts/build.sh` の末尾、`sh "$install_launcher" "$root" "$launcher"` の後に追加する。

```sh
sh "$install_launcher" "$root" "$launcher"

# The launcher is installed, so the build succeeded; PATH is the user's shell
# configuration and not something to fail an install over. Colons on both
# sides so a directory cannot match a longer neighbour by prefix.
case ":$PATH:" in
    *":$bindir:"*) ;;
    *)
        printf 'build.sh: %s is not on PATH; `hsl` will not be found\n' "$bindir" >&2
        printf '%s\n' 'build.sh: add it to PATH in your shell configuration' >&2
        ;;
esac
```

- [ ] **Step 4: テストが通ることを確認**

Run: `python3 -m unittest tests.test_build -v`
Expected: 12 件すべて PASS（既存 10 + 新規 2）。

- [ ] **Step 5: Commit**

```bash
git add scripts/build.sh tests/test_build.py
git commit -m "feat: warn when the launcher directory is not on PATH"
```

---

## Task 4: CI ワークフローの整備

**Files:**
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: なし
- Produces: 後続の Task 5 / 6 / 7 / 12 / 13 がこのワークフローにステップとジョブを足す。ジョブ名は `test`。

このタスクは検証を CI 上でしか行えない。ローカルでは YAML の妥当性のみ確認する。

- [ ] **Step 1: ワークフローを書き換える**

```yaml
name: ci

on:
  push:
    branches: [master]
  pull_request:

permissions:
  contents: read

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
        with:
          components: rustfmt, clippy
      - uses: Swatinem/rust-cache@v2
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - name: Install tmux
        run: sudo apt-get update && sudo apt-get install -y tmux
      - name: Format
        run: cargo fmt --check
      - name: Clippy
        run: cargo clippy --all-targets --all-features -- -D warnings
      - name: Rust tests
        run: cargo test --locked
      - name: Build the release helper
        run: cargo build --release --locked
      - name: Python integration tests
        run: python3 -m unittest discover -s tests -v
      - name: Shell syntax
        run: |
          sh -n bin/hsl-internal
          sh -n scripts/build.sh
          sh -n scripts/install-launcher.sh
          sh -n scripts/lib/shell-quote.sh
          sh -n scripts/launcher-body.sh
          sh -n scripts/run-in-tmux
          sh -n scripts/default-herdr-info.sh
```

> `on: push` を `branches: [master]` に絞ったことで、PR ブランチへの push は
> `pull_request` イベントだけを起こす。`cargo test` に `--locked` を足したのは
> 依存条件を build と揃えるため（仕様 §5.B-4）。

- [ ] **Step 2: YAML が読めることを確認**

Run: `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo OK`
Expected: `OK`

> `yaml` が無ければ `python3 -m pip install --user pyyaml` するか、この確認は飛ばして
> push 後の GitHub 側の解釈に委ねてよい。

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: declare read-only permissions, cache cargo, and stop double runs"
```

---

## Task 5: shellcheck の書き換えと CI 導入

**Files:**
- Modify: `bin/hsl-internal:10`、`scripts/build.sh:5`、`scripts/install-launcher.sh:51,56`、`scripts/launcher-body.sh:12`、`scripts/run-in-tmux:10,130,135`、`scripts/default-herdr-info.sh`（`pane= cwd=`、`branch= state=`、`@{upstream}`）
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: Task 4 のワークフロー
- Produces: なし

**方針**: 抑制ではなく**書き換え**で警告を消す。抑制するのは回避不能な SC2329 のみ。

- [ ] **Step 1: 変更前の shellcheck 出力を記録する**

Run: `shellcheck -s sh bin/hsl-internal scripts/build.sh scripts/install-launcher.sh scripts/lib/shell-quote.sh scripts/launcher-body.sh scripts/run-in-tmux scripts/default-herdr-info.sh; echo "exit=$?"`
Expected: 非ゼロ終了。SC1007 が 8 件、SC1090 が 2 件、SC1083 が 1 件、SC2329 が 3 件。

- [ ] **Step 2: SC1007 を書き換える（`CDPATH=` の 6 箇所）**

`CDPATH= cd` は「空文字を代入」ではなく「後続 `cd` への一時的な環境変数指定」である。`CDPATH=''` と書けば意図が明示され警告も消える。振る舞いは同一。

対象:
- `bin/hsl-internal:10`
- `scripts/build.sh:5`
- `scripts/install-launcher.sh:51`
- `scripts/launcher-body.sh:12`
- `scripts/run-in-tmux:10`
- `scripts/run-in-tmux:130`

いずれも `CDPATH= cd -P` を `CDPATH='' cd -P` にする。例:

```sh
root=$(CDPATH='' cd -P "$(dirname "$0")/.." && pwd)
```

- [ ] **Step 3: SC1007 を書き換える（`default-herdr-info.sh` の 2 箇所）**

複数変数の同時初期化をやめ、1 行ずつにする。

```sh
pane=
cwd=
if pane_json=$(herdr pane current 2>/dev/null); then
```

```sh
branch=
state=
if [ -n "$cwd" ] && [ -d "$cwd" ]; then
```

- [ ] **Step 4: SC1083 を書き換える**

`@{upstream}` の `{` `}` はリテラルであることを quote で明示する。git への引数は同一。

```sh
    if counts=$(git -C "$cwd" rev-list --left-right --count "HEAD...@{upstream}" \
        2>/dev/null)
```

- [ ] **Step 5: SC1090 に source 指示を足す（2 箇所）**

抑制ではなく、shellcheck に解析させる。`scripts/run-in-tmux:135` と `scripts/install-launcher.sh:56` の `. "$..."` の直前に置く。

```sh
# shellcheck source=scripts/lib/shell-quote.sh
. "$QUOTE_LIB"
```

```sh
# shellcheck source=scripts/lib/shell-quote.sh
. "$quote_lib"
```

- [ ] **Step 6: SC2329 を抑制する（3 箇所、理由付き）**

`scripts/run-in-tmux` の `remove_status_options` / `cleanup` / `on_signal` は trap からのみ呼ばれる。shellcheck は trap 経由の呼び出しを追えないため、これは回避不能な false positive である。各関数定義の直前に置く。

```sh
# Invoked through `trap` below, which shellcheck cannot follow.
# shellcheck disable=SC2329
remove_status_options() {
```

同じ 2 行を `cleanup()` と `on_signal()` の直前にも置く。

- [ ] **Step 7: shellcheck が通ることを確認**

Run: `shellcheck -s sh bin/hsl-internal scripts/build.sh scripts/install-launcher.sh scripts/lib/shell-quote.sh scripts/launcher-body.sh scripts/run-in-tmux scripts/default-herdr-info.sh; echo "exit=$?"`
Expected: `exit=0`、出力なし。

- [ ] **Step 8: 抑制が SC2329 のみであることを確認（AC-C1-2）**

Run: `grep -n 'shellcheck disable' bin/hsl-internal scripts/build.sh scripts/install-launcher.sh scripts/lib/shell-quote.sh scripts/launcher-body.sh scripts/run-in-tmux scripts/default-herdr-info.sh`
Expected: 3 行、すべて `SC2329`。

- [ ] **Step 9: 振る舞いが変わっていないことを確認（AC-C1-3）**

Run: `python3 -m unittest tests.test_hsl_internal tests.test_tmux_runtime tests.test_herdr_info tests.test_build tests.test_launcher -v`
Expected: すべて PASS。失敗が 1 件でもあれば書き換えが振る舞いを変えている。

- [ ] **Step 10: CI に shellcheck ステップを足す**

`Shell syntax` ステップの直後に追加する。対象集合は `sh -n` と同一に保つ。

```yaml
      - name: Shellcheck
        run: |
          shellcheck -s sh \
            bin/hsl-internal \
            scripts/build.sh \
            scripts/install-launcher.sh \
            scripts/lib/shell-quote.sh \
            scripts/launcher-body.sh \
            scripts/run-in-tmux \
            scripts/default-herdr-info.sh
```

> `ubuntu-latest` には shellcheck が既に入っている。入っていなければ
> `sudo apt-get install -y shellcheck` を tmux のインストール行に足す。

- [ ] **Step 11: Commit**

```bash
git add bin/hsl-internal scripts/ .github/workflows/ci.yml
git commit -m "ci: run shellcheck, rewriting the warnings rather than suppressing them"
```

---

## Task 6: pytest + xdist への移行

**Files:**
- Create: `requirements-dev.txt`
- Create: `pyproject.toml`
- Create: `Makefile`
- Modify: `tests/helpers.py:36-53`
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`（開発者向けの実行手順）

**Interfaces:**
- Consumes: Task 4 のワークフロー
- Produces: `ensure_helper()` は引数なしで helper のパスを返す。ビルドはしない。Task 7 が `tests/conftest.py` を新設する。

- [ ] **Step 1: dev 依存を宣言する**

`requirements-dev.txt`:

```
pytest==9.1.1
pytest-xdist==3.8.0
```

- [ ] **Step 2: pytest を設定する**

`pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
# tests/ is a package and the suite imports `from tests.helpers import ...`,
# so the repository root has to be on sys.path.
pythonpath = ["."]
```

- [ ] **Step 3: ensure_helper を縮退させる失敗テストを書く**

`tests/test_build.py` に追加する（helper の契約を検査する場所として最も近い）。

```python
    def test_ensure_helper_does_not_build(self):
        # Under `pytest -n auto` every worker is a separate process, and a
        # session-scoped fixture never runs in the xdist controller at all,
        # so there is no in-pytest place to build exactly once. The build is
        # a precondition instead; see the Makefile and the CI build step.
        from tests import helpers

        self.assertFalse(hasattr(helpers, "_HELPER_BUILT"))
        source = (ROOT / "tests/helpers.py").read_text()
        self.assertNotIn("cargo", source)
```

- [ ] **Step 4: テストが失敗することを確認**

Run: `python3 -m unittest tests.test_build.BuildTests.test_ensure_helper_does_not_build -v`
Expected: FAIL。`_HELPER_BUILT` が存在する。

- [ ] **Step 5: helpers.ensure_helper を書き換える**

`tests/helpers.py` の `HELPER` 定義以降を差し替える。`import subprocess` は `write_protocol` が使い続けるので残す。

```python
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
            "Run `make test`, or `cargo build --release --locked` first."
        )
    return HELPER
```

`import os` は既にファイル先頭にある。

- [ ] **Step 6: テストが通ることを確認**

Run: `cargo build --release --locked && python3 -m unittest tests.test_build -v`
Expected: すべて PASS。

- [ ] **Step 7: ローカル実行の入口を作る（AC-D1-4）**

`Makefile`:

```makefile
# The Python suite needs target/release/hsl-config to exist before pytest
# starts: see tests/helpers.py:ensure_helper.
.PHONY: test test-serial build

build:
	cargo build --release --locked

test: build
	python3 -m pytest -n auto --dist loadscope

test-serial: build
	python3 -m pytest -p no:xdist
```

> `Makefile` のレシピ行は**タブ**でインデントすること。スペースでは動かない。

- [ ] **Step 8: 並列実行が通ることを確認**

Run: `python3 -m pip install --user -r requirements-dev.txt && make test`
Expected: 132 件以上が PASS。

- [ ] **Step 9: 直列と並列の所要時間を測る（AC-G-5 の下準備）**

Run:
```bash
for i in 1 2 3 4 5; do /usr/bin/time -f "%e" make test 2>&1 | tail -1; done
for i in 1 2 3 4 5; do /usr/bin/time -f "%e" make test-serial 2>&1 | tail -1; done
```
Expected: 並列の中央値が直列の中央値の 75% 以下。満たさない場合は数値を記録し、
仕様 §8-5 の通り閾値ではなく実測値で再判断する（Task 8 の後に再測定する）。

- [ ] **Step 10: CI を pytest に切り替える**

`Python integration tests` ステップを差し替え、`Install tmux` の後に依存インストールを足す。

```yaml
      - name: Install Python dev dependencies
        run: python3 -m pip install -r requirements-dev.txt
```

```yaml
      - name: Python integration tests
        run: python3 -m pytest -n auto --dist loadscope
```

- [ ] **Step 11: README に開発者向けの手順を足す**

README の末尾に節を追加する。

```markdown
## Development

```sh
make test          # Build the release helper, then run the suite in parallel
make test-serial   # Same suite, one process
```

The Python suite requires `target/release/hsl-config` to exist before pytest
starts, which is why `make test` builds first. Install the test dependencies
with `python3 -m pip install -r requirements-dev.txt`.
```

- [ ] **Step 12: Commit**

```bash
git add requirements-dev.txt pyproject.toml Makefile tests/helpers.py tests/test_build.py .github/workflows/ci.yml README.md
git commit -m "test: run the suite under pytest-xdist and build the helper up front"
```

---

## Task 7: 黙った skip の禁止

**Files:**
- Create: `tests/conftest.py`
- Modify: `pyproject.toml`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: Task 6 の `pyproject.toml` と CI の pytest ステップ
- Produces: `live_herdr` マーカー。Task 10 の live 比較テストがこれを付ける。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_build.py` に追加する。

```python
    def test_ci_fails_the_session_when_a_test_is_skipped(self):
        # CI must not go green because a precondition silently vanished.
        script = self.base / "skippy.py"
        script.write_text(
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            "    @unittest.skip('deliberate')\n"
            "    def test_x(self):\n"
            "        pass\n"
        )
        env = dict(os.environ)
        env["CI"] = "true"
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(script), "-p", "no:xdist", "-q"],
            cwd=ROOT, env=env, text=True, capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("skip", result.stdout.lower())
```

`import sys` を `tests/test_build.py` の import に足す。

- [ ] **Step 2: テストが失敗することを確認**

Run: `python3 -m unittest tests.test_build.BuildTests.test_ci_fails_the_session_when_a_test_is_skipped -v`
Expected: FAIL。pytest は skip があっても exit 0 を返す。

- [ ] **Step 3: conftest.py を作る**

```python
"""Session-wide policy: CI must never go green on a skipped test.

A skip means a precondition vanished -- tmux missing, a version too old --
and that is exactly the failure this suite must not hide. Tests that are
deliberately not part of the default run are *deselected* by marker instead,
which is a different thing from being skipped.
"""

import os


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live_herdr: compares the committed CLI snapshot against the real "
        "herdr binary. Deselected by default; select with -m live_herdr.",
    )


def pytest_sessionfinish(session, exitstatus):
    if not os.environ.get("CI"):
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        return
    skipped = reporter.stats.get("skipped", [])
    if skipped and session.exitstatus == 0:
        reporter.write_line(
            f"ERROR: {len(skipped)} test(s) skipped; CI forbids silent skips",
            red=True,
        )
        session.exitstatus = 1
```

- [ ] **Step 4: テストが通ることを確認**

Run: `python3 -m unittest tests.test_build.BuildTests.test_ci_fails_the_session_when_a_test_is_skipped -v`
Expected: PASS。

- [ ] **Step 5: live マーカーを既定から外す**

`pyproject.toml` の `[tool.pytest.ini_options]` に足す。skip ではなく deselect にすることが要点。

```toml
addopts = "-m 'not live_herdr'"
```

- [ ] **Step 6: CI に前提検査ステップを足す**

`Install tmux` の直後に置く。

```yaml
      - name: Assert test preconditions
        run: |
          command -v tmux >/dev/null || { echo "tmux is required"; exit 1; }
          command -v script >/dev/null || { echo "util-linux script is required"; exit 1; }
          tmux -V
          tmux -V | awk '{
            gsub(/[^0-9.]/, "", $2)
            split($2, v, ".")
            if (v[1] < 3 || (v[1] == 3 && v[2] < 4)) {
              print "tmux 3.4 or newer is required, found " $2
              exit 1
            }
          }'
```

- [ ] **Step 7: 全体が通ることを確認**

Run: `make test`
Expected: すべて PASS、skip 0 件。

- [ ] **Step 8: Commit**

```bash
git add tests/conftest.py tests/test_build.py pyproject.toml .github/workflows/ci.yml
git commit -m "test: fail CI on any skipped test and assert the preconditions"
```

---

## Task 8: マウステストの readiness marker

**Files:**
- Modify: `tests/mouse_pty.py`（`INNER_APP`、`HslPty.__init__`、`HslPty.__enter__`）
- Modify: `tests/test_tmux_mouse.py`（`runtime_env` / `session`）

**Interfaces:**
- Consumes: 既存の `inner_app_script(log_path, mode=1003)` / `HslPty(runtime, env, ...)`
- Produces: `inner_app_script(log_path, ready_path, mode=1003)`、`HslPty(runtime, env, ready_path, ...)`

**⚠️ 核心**: `flush()` はバイト列が PTY の kernel buffer に届いたことしか保証しない。**tmux server がそれを読んで mouse flag を更新したことは保証しない。** marker はフラグの反映を確認してから作る。

- [ ] **Step 1: INNER_APP にフラグ確認と marker 作成を足す**

`tests/mouse_pty.py` の `INNER_APP` を差し替える。`sys.argv[2]` が marker のパス。

```python
INNER_APP = r"""
import os, subprocess, sys, time, tty
log = sys.argv[1]
ready = sys.argv[2]
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
    if out and out.endswith("1") and "1" in out[:-1]:
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
```

> `mouse_sgr_flag` は 1006 を要求したので必ず 1 になる。`mouse_any_flag` と
> `mouse_all_flag` は `mode` が 1003 なら双方 1、1000 なら `any` のみ 1 になる。
> 判定は「sgr が立ち、かつ any か all のどちらかが立つ」で両モードを許す。

- [ ] **Step 2: inner_app_script に marker のパスを渡す**

```python
def inner_app_script(log_path, ready_path, mode=1003):
    """A shell script running the stub, for HSL_HERDR_BIN."""
    program = INNER_APP.replace("MODE", str(mode))
    return (
        "#!/bin/sh\n"
        f"exec python3 -c {shell_quote(program)} "
        f"{shell_quote(str(log_path))} {shell_quote(str(ready_path))}\n"
    )
```

- [ ] **Step 3: HslPty を marker 待ちにする**

`__init__` に `ready_path` を足し、`__enter__` の `self._drain(3.0)` を置換する。

```python
class HslPty:
    def __init__(self, runtime, env, ready_path, session="mouse", cols=80, rows=24):
        self.runtime = runtime
        self.env = env
        self.ready_path = ready_path
        self.session = session
        self.cols = cols
        self.rows = rows
        self._buffer = bytearray()
        self.pid = None
        self.fd = None
```

```python
        fcntl.ioctl(
            self.fd, termios.TIOCSWINSZ,
            struct.pack("HHHH", self.rows, self.cols, 0, 0),
        )
        self._wait_ready(30.0)
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
```

- [ ] **Step 4: 呼び出し側を更新する**

`tests/test_tmux_mouse.py` の `MouseIntegrationBase`:

```python
        self.app_log = self.base / "app.log"
        self.ready_marker = self.base / "ready.marker"
        self.hook_log = self.base / "hook.log"
```

```python
    def runtime_env(self, status_format=STATUS_FORMAT, mouse=True, mode=1003,
                    extra_options=()):
        stub = self.fakebin / "herdr"
        make_executable(
            stub, inner_app_script(self.app_log, self.ready_marker, mode=mode)
        )
```

```python
    def session(self, **kw):
        return HslPty(RUNTIME, self.runtime_env(**kw), self.ready_marker)
```

`HslPty(` の構築箇所はリポジトリ全体で 2 つだけである。もう 1 つは
`test_tmux_mouse.py:324`、`RootTableGuardTests` 内の直接構築なので、そこも直す。

```python
        with HslPty(RUNTIME, env, self.ready_marker) as term:
```

> このテストは `HSL_TEST_BASE_CONF` から `unbind-key -a -T root` を消すだけで、
> セッション自体は正常に起動する（同テストが冒頭で positive control のクリックを
> 通していることがその証拠）。したがって ready marker は通常どおり現れ、
> `_wait_ready` は待たされない。待機の追加によってこのテストが壊れることはない。

- [ ] **Step 5: マウステストが通ることを確認**

Run: `python3 -m pytest tests/test_tmux_mouse.py -p no:xdist -q`
Expected: 12 件 PASS。

- [ ] **Step 6: flake が入っていないことを確認（AC-D2-4）**

Run: `for i in 1 2 3 4 5; do python3 -m pytest tests/test_tmux_mouse.py -p no:xdist -q || echo "RUN $i FAILED"; done`
Expected: 5 回とも PASS、`RUN n FAILED` が出ない。

- [ ] **Step 7: 所要時間を再測定する**

Run: `for i in 1 2 3 4 5; do /usr/bin/time -f "%e" make test 2>&1 | tail -1; done`
Expected: Task 6 Step 9 の値より短い。AC-G-5 の判定はここで行う。

- [ ] **Step 8: Commit**

```bash
git add tests/mouse_pty.py tests/test_tmux_mouse.py
git commit -m "test: wait for tmux to report its mouse flags instead of sleeping 3s"
```

---

## Task 9: `--hsl-check-config`

**Files:**
- Modify: `scripts/launcher-body.sh`（`mode`/`purge` の解析部）
- Modify: `skills/customize-herdr-statusline/SKILL.md:89-105`
- Test: `tests/test_launcher.py`

**Interfaces:**
- Consumes: `$PLUGIN_ROOT/target/release/hsl-config`、`$HERDR_BIN plugin config-dir`
- Produces: `hsl --hsl-check-config [<path>]`。exit 0 = 妥当、exit 2 = それ以外。

**CLI 契約**（仕様 §5.E-2 の表を実装する）:

| 入力 | 挙動 |
| --- | --- |
| 引数 0 個 | `herdr plugin config-dir` を解決し、その `config.toml` を検査 |
| 引数 1 個 | そのパスを検査。herdr を必要としない |
| 引数 2 個以上 | usage を stderr、exit 2 |
| 成功 | stdout 空、exit 0 |
| 検査失敗 | `hsl-config` のエラーを stderr、exit 2 |
| config 不在 | stderr にメッセージ、exit 2、ファイルを作らない |

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_launcher.py` の `LauncherTests` に追加する。同クラスは
`run_launcher(self, *args, **extra_env)` を持つ（100 行目）。**環境変数は `env=` ではなく
キーワード引数で渡す**こと。

```python
    def test_check_config_accepts_a_valid_file(self):
        config = self.base / "config.toml"
        config.write_text("enabled = true\n[statusline]\nstatus_interval = 1\n")
        result = self.run_launcher("--hsl-check-config", str(config))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_check_config_rejects_a_broken_file(self):
        config = self.base / "config.toml"
        config.write_text("[statusline]\nprefix = \"C-b\"\n")
        result = self.run_launcher("--hsl-check-config", str(config))
        self.assertEqual(result.returncode, 2)
        self.assertNotEqual(result.stderr, "")

    def test_check_config_reports_a_missing_file(self):
        missing = self.base / "absent.toml"
        result = self.run_launcher("--hsl-check-config", str(missing))
        self.assertEqual(result.returncode, 2)
        self.assertFalse(missing.exists(), "must not create the file")

    def test_check_config_rejects_extra_arguments(self):
        result = self.run_launcher("--hsl-check-config", "a", "b")
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage", result.stderr.lower())

    def test_check_config_with_an_explicit_path_does_not_need_herdr(self):
        config = self.base / "config.toml"
        config.write_text("enabled = true\n")
        empty = self.base / "empty-bin"
        empty.mkdir(exist_ok=True)
        # run_launcher takes environment overrides as keyword arguments.
        # A PATH with no herdr proves the explicit-path form never calls it.
        result = self.run_launcher(
            "--hsl-check-config", str(config), PATH=str(empty)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python3 -m pytest tests/test_launcher.py -k check_config -q`
Expected: 5 件とも FAIL。現状は herdr へ pass-through され、herdr が未知オプションとして拒否する。

- [ ] **Step 3: launcher-body.sh に分岐を足す**

`mode=run` / `purge=false` の初期化ブロックを拡張する。`uninstall` と同じ位置、すなわち
`root_is_complete` による再解決の**前**に引数だけ解析し、実行は再解決の**後**に行う。

```sh
mode=run
purge=false
check_config_path=
if [ "${1:-}" = uninstall ]; then
    mode=uninstall
    if [ "$#" -eq 1 ]; then
        purge=false
    elif [ "$#" -eq 2 ] && [ "$2" = --purge ]; then
        purge=true
    else
        printf '%s\n' 'usage: hsl uninstall [--purge]' >&2
        exit 2
    fi
elif [ "${1:-}" = --hsl-check-config ]; then
    mode=check-config
    if [ "$#" -eq 1 ]; then
        check_config_path=
    elif [ "$#" -eq 2 ]; then
        check_config_path=$2
    else
        printf '%s\n' 'usage: hsl --hsl-check-config [<config.toml>]' >&2
        exit 2
    fi
fi
```

`root_is_complete` のブロックで `rewrite_self` を走らせる条件は `mode = run` のままにする
（契約表の「stale root の rewrite は行わない」）。

`HELPER` / `INTERNAL` の定義の後、`if [ "$mode" = uninstall ]` の**前**に置く:

```sh
if [ "$mode" = check-config ]; then
    if [ -z "$check_config_path" ]; then
        require_herdr
        config_dir=$("$HERDR_BIN" plugin config-dir "$PLUGIN_ID") || {
            printf '%s\n' 'hsl: failed to resolve the plugin config directory' >&2
            exit 2
        }
        check_config_path=$config_dir/config.toml
    fi
    [ -f "$check_config_path" ] || {
        printf 'hsl: no such configuration file: %s\n' "$check_config_path" >&2
        exit 2
    }
    "$HELPER" load "$check_config_path" >/dev/null || exit 2
    exit 0
fi
```

- [ ] **Step 4: テストが通ることを確認**

Run: `python3 -m pytest tests/test_launcher.py -q`
Expected: 27 件 PASS（既存 22 + 新規 5）。

- [ ] **Step 5: SKILL.md の手順を差し替える**

`skills/customize-herdr-statusline/SKILL.md` の "Validate before reporting" 節、
`herdr plugin list --json` を parse させている部分を置き換える。

```markdown
## Validate before reporting

Run `sh -n` on every shell helper you changed.

Validate `config.toml` with the plugin's own parser:

```sh
hsl --hsl-check-config
```

It exits 0 when the configuration is valid and 2 with an explanation otherwise.
Pass a path to check a file other than the installed one.

Fix every parse or normalization error. Do not claim visual verification unless a fresh `hsl` session was actually inspected. Tell the user to exit and restart `hsl` to load the new configuration, and summarize the changed segments and files.
```

- [ ] **Step 6: SKILL.md に plugin_root の parse が残っていないことを確認（AC-E2-6）**

Run: `grep -n 'plugin_root\|plugin list --plugin' skills/customize-herdr-statusline/SKILL.md; echo "exit=$?"`
Expected: `exit=1`（一致なし）。

- [ ] **Step 7: shellcheck と構文を確認**

Run: `sh -n scripts/launcher-body.sh && shellcheck -s sh scripts/launcher-body.sh && echo OK`
Expected: `OK`

- [ ] **Step 8: Commit**

```bash
git add scripts/launcher-body.sh tests/test_launcher.py skills/customize-herdr-statusline/SKILL.md
git commit -m "feat: add hsl --hsl-check-config and point the skill at it"
```

---

## Task 10: herdr CLI 契約テスト

**Files:**
- Modify: `tests/test_hsl_internal.py`（`DIRECT` 行列）
- Create: `tests/fixtures/herdr-0.8.0/root.txt` ほかサブコマンド分
- Create: `tests/test_herdr_cli_contract.py`

**Interfaces:**
- Consumes: Task 7 の `live_herdr` マーカー
- Produces: なし

**3 層の役割**（取り違えないこと）:

| 層 | 検出する | **検出しない** |
| --- | --- | --- |
| 1 | ローカル分類器の契約回帰 | **上流 herdr の変化は一切観測しない** |
| 2 | コードが記録済み 0.8.0 CLI と整合していること | snapshot 自体の陳腐化、`attach` 集合の網羅性 |
| 3 | 実 herdr と snapshot の乖離 | — |

- [ ] **Step 1: 層 1 — 既存の argv 行列を拡張する**

`tests/test_hsl_internal.py` の `DIRECT` に 2 行足す。**`is_interactive()` を抽出・source してはならない。** 既存の仕組みはスクリプト全体を実行しており、それが本番の配線そのものを起動するという原則に合致している。

```python
    ("future-command", "value with space"),
    # herdr 0.8.0 added --skill; --default-config predates it. Both are
    # terminating globals, so hsl must not spend a tmux server on them.
    ("--skill",),
    ("--default-config",),
```

- [ ] **Step 2: 層 1 が通ることを確認**

Run: `python3 -m pytest tests/test_hsl_internal.py -q`
Expected: すべて PASS。

- [ ] **Step 3: 層 2 — snapshot fixture を作る**

正規化スクリプトを一時的に走らせて fixture を生成する。root help から発見できる全
top-level subcommand と、そこに現れない既知の例外 `plugin`（F29）を含めること。

```bash
mkdir -p tests/fixtures/herdr-0.8.0
normalize() { sed -e "s#$HOME#\$HOME#g" -e 's/[[:space:]]*$//'; }
herdr --help 2>&1 | normalize > tests/fixtures/herdr-0.8.0/root.txt
for sub in api channel config workspace worktree tab notification agent \
           pane session integration terminal completion plugin; do
    herdr "$sub" --help 2>&1 | normalize > "tests/fixtures/herdr-0.8.0/$sub.txt"
done
```

- [ ] **Step 4: fixture に絶対パスが残っていないことを確認（AC-E3-2）**

Run: `grep -rn "$HOME" tests/fixtures/herdr-0.8.0/; echo "exit=$?"`
Expected: `exit=1`（一致なし）。

- [ ] **Step 5: 層 2 と層 3 のテストを書く**

`tests/test_herdr_cli_contract.py`:

```python
"""What the hsl classifier assumes about herdr's CLI, and how far it is checked.

Layer 2 (this file, always run) checks the committed 0.8.0 snapshot. It cannot
detect that the snapshot itself has gone stale, and it cannot prove which
subcommands carry `attach`: `herdr --help` does not list `plugin` at all, so a
set derived from help output is not exhaustive. Layer 3 compares the snapshot
against a real binary and is deselected by default.
"""

import os
import pathlib
import re
import shutil
import subprocess
import unittest

import pytest

from tests.helpers import ROOT

FIXTURES = ROOT / "tests/fixtures/herdr-0.8.0"
ATTACH_SUBCOMMANDS = {"session", "agent", "terminal"}
GLOBAL_OPTIONS = {
    "--no-session", "--session", "--remote", "--remote-keybindings",
    "--handoff", "--default-config", "--skill", "--version", "-V",
    "--help", "-h",
}


def normalize(text):
    home = os.environ.get("HOME", "")
    if home:
        text = text.replace(home, "$HOME")
    return "\n".join(line.rstrip() for line in text.splitlines())


def global_options(root_help):
    """Options from the `Options:` block of the root help."""
    block = root_help.split("Options:", 1)
    if len(block) < 2:
        return set()
    return set(re.findall(r"(?<![\w-])(--?[A-Za-z][\w-]*)", block[1]))


def has_attach(subcommand_help):
    return bool(re.search(r"^\s+attach\s", subcommand_help, re.M))


class SnapshotTests(unittest.TestCase):
    """Layer 2. Runs everywhere; needs no herdr binary."""

    def setUp(self):
        self.root = (FIXTURES / "root.txt").read_text()

    def test_the_snapshot_carries_no_absolute_paths(self):
        for path in FIXTURES.glob("*.txt"):
            with self.subTest(fixture=path.name):
                self.assertNotIn(os.environ.get("HOME", "\0"), path.read_text())

    def test_global_options_match_exactly(self):
        # The root help's Options: block is complete, so this one can be exact.
        self.assertEqual(global_options(self.root), GLOBAL_OPTIONS)

    def test_the_three_known_attach_forms_are_present(self):
        for name in sorted(ATTACH_SUBCOMMANDS):
            with self.subTest(subcommand=name):
                self.assertTrue(has_attach((FIXTURES / f"{name}.txt").read_text()))

    def test_no_other_captured_subcommand_offers_attach(self):
        # Best effort, NOT exhaustive: herdr --help does not list `plugin`, so
        # a set built from help output cannot prove which commands exist.
        captured = {p.stem for p in FIXTURES.glob("*.txt")} - {"root"}
        for name in sorted(captured - ATTACH_SUBCOMMANDS):
            with self.subTest(subcommand=name):
                self.assertFalse(has_attach((FIXTURES / f"{name}.txt").read_text()))

    def test_the_fixture_captured_more_than_the_attach_three(self):
        # Without this the negative test above would pass vacuously.
        captured = {p.stem for p in FIXTURES.glob("*.txt")} - {"root"}
        self.assertGreater(len(captured - ATTACH_SUBCOMMANDS), 3)
        self.assertIn("plugin", captured)


@pytest.mark.live_herdr
class LiveComparisonTests(unittest.TestCase):
    """Layer 3. Deselected by default; select with `-m live_herdr`.

    Fails rather than skips when herdr is absent: this test is only ever run
    because someone asked for it, and answering "skipped" to that request is
    the silent-green failure the suite forbids.
    """

    def setUp(self):
        self.herdr = shutil.which("herdr")
        self.assertIsNotNone(
            self.herdr,
            "herdr is not installed; this test was explicitly selected so it "
            "fails rather than skipping",
        )

    def capture(self, *args):
        return normalize(
            subprocess.run(
                [self.herdr, *args], text=True, capture_output=True
            ).stdout
        )

    def test_root_help_matches_the_snapshot(self):
        self.assertEqual(
            self.capture("--help"), normalize((FIXTURES / "root.txt").read_text())
        )

    def test_each_captured_subcommand_matches_the_snapshot(self):
        for path in sorted(FIXTURES.glob("*.txt")):
            if path.stem == "root":
                continue
            with self.subTest(subcommand=path.stem):
                self.assertEqual(
                    self.capture(path.stem, "--help"),
                    normalize(path.read_text()),
                )
```

- [ ] **Step 6: 層 2 が herdr 不在で通ることを確認（AC-E3-1）**

Run: `env PATH=/usr/bin:/bin python3 -m pytest tests/test_herdr_cli_contract.py -q`
Expected: `SnapshotTests` の 5 件 PASS。`LiveComparisonTests` は deselect され、skip 0 件。

- [ ] **Step 7: 層 3 が herdr 有りで通ることを確認（AC-E3-5）**

Run: `python3 -m pytest tests/test_herdr_cli_contract.py -m live_herdr -q`
Expected: 2 件 PASS。

- [ ] **Step 8: 差分を注入して層 3 が落ちることを確認（AC-E3-6）**

```bash
cp tests/fixtures/herdr-0.8.0/root.txt /tmp/root.bak
printf 'INJECTED DIVERGENCE\n' >> tests/fixtures/herdr-0.8.0/root.txt
python3 -m pytest tests/test_herdr_cli_contract.py -m live_herdr -q; echo "exit=$?"
cp /tmp/root.bak tests/fixtures/herdr-0.8.0/root.txt
```
Expected: 注入時に非ゼロ終了。復元後は再び PASS。

- [ ] **Step 9: 明示選択かつ herdr 不在で fail することを確認（AC-E3-7）**

Run: `env PATH=/usr/bin:/bin python3 -m pytest tests/test_herdr_cli_contract.py -m live_herdr -q; echo "exit=$?"`
Expected: 非ゼロ終了。skip ではなく fail。

- [ ] **Step 10: Commit**

```bash
git add tests/test_hsl_internal.py tests/fixtures tests/test_herdr_cli_contract.py
git commit -m "test: pin the herdr CLI contract the hsl classifier depends on"
```

---

## Task 11: README の mouse_clicks 節

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: なし
- Produces: なし

- [ ] **Step 1: 節を追加する**

`## How It Works` の後、`## Troubleshooting` の前に置く。

```markdown
## Status Line Buttons

Clicks on the status line can be dispatched to a hook. This is **off by
default**: turning it on asks the outer terminal for mouse reporting, which
costs you its native selection and middle-click paste.

It needs tmux 3.4 or newer. Enable it in `config.toml`:

```toml
mouse_clicks = true
```

**The hook is not shipped with this plugin.** `hsl` only routes clicks; you or
another plugin provide `on-click.sh` in the configuration directory, and it
must be executable. It receives four arguments:

| | |
|---|---|
| `$1` | `left`, `right`, `wheelup` or `wheeldown` |
| `$2` | the range name under the pointer |
| `$3` | the mouse column, zero-based |
| `$4` | the status line number, zero-based |

Mark a clickable area in a status format with `#[range=user|NAME]` ...
`#[norange]`, where `NAME` is at most 15 bytes. tmux dispatches its own
`window`, `session` and `pane` ranges through the same hook, so treat those
three names as reserved.

The hook's stdout and exit status are discarded; call `tmux display-message`
to say anything. See the comments in `config.toml` for the full details.
```

- [ ] **Step 2: 節が存在することを確認（AC-E1-1）**

Run: `grep -n 'mouse_clicks\|3.4 or newer\|on-click.sh' README.md`
Expected: 3 つとも一致する。

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document the status line mouse clicks in the README"
```

---

## Task 12: MSRV の宣言

**Files:**
- Modify: `Cargo.toml`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: Task 4 のワークフロー
- Produces: なし

`1.85` は「保証する下限」であって「証明された最小」ではない。1.85 未満は未検証である。

- [ ] **Step 1: Cargo.toml に rust-version を足す**

```toml
[package]
name = "hsl-config"
version = "0.1.2"
edition = "2021"
rust-version = "1.85"
description = "Config, init and purge helper for the herdr-statusline plugin"
license = "MIT"
```

- [ ] **Step 2: 宣言した版でビルドとテストが通ることを確認**

Run: `cargo +1.85.0 build --release --locked && cargo +1.85.0 test --locked`
Expected: 双方 exit 0。

> `rustup toolchain install 1.85.0` が必要な場合がある。

- [ ] **Step 3: Cargo.lock が変わっていないことを確認**

Run: `git diff --stat Cargo.lock`
Expected: 差分なし。`rust-version` の追加は lock に影響しない。

- [ ] **Step 4: CI に MSRV ジョブを足す**

`jobs:` の下、`test:` と並べる。

```yaml
  msrv:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      # Pinned to the rust-version declared in Cargo.toml. If this job fails
      # after a dependency update, the declared floor is no longer true:
      # raise it deliberately rather than loosening the pin.
      - uses: dtolnay/rust-toolchain@1.85.0
      - uses: Swatinem/rust-cache@v2
      - name: Build and test at the declared MSRV
        run: |
          cargo build --release --locked
          cargo test --locked
```

- [ ] **Step 5: Commit**

```bash
git add Cargo.toml .github/workflows/ci.yml
git commit -m "chore: declare rust-version 1.85 and pin a CI job to it"
```

---

## Task 13: 遡及タグ

**Files:**
- Modify: `tests/test_consistency.py`
- Modify: `.github/workflows/ci.yml`
- タグ 2 本の作成（ワーキングツリーの変更ではない）

**Interfaces:**
- Consumes: Task 4 のワークフロー
- Produces: なし

**⚠️ タグは公開後に動かせない。** 対象コミットは以下で確定であり、`16de6df` ではない。

| タグ | コミット | 理由 |
| --- | --- | --- |
| `v0.1.0` | `0622df4` | 歴史的なリリース点 |
| `v0.1.2` | `16bd1b7` | `--locked` が通る最初の 0.1.2。`eb38ecb` は Cargo.lock が 0.1.0 のままで**ビルドが落ちる** |

- [ ] **Step 1: 対象コミットを再確認する**

Run:
```bash
git show 0622df4:herdr-plugin.toml | grep '^version'
git show 16bd1b7:herdr-plugin.toml | grep '^version'
git show 16bd1b7:Cargo.lock | grep -A1 'name = "hsl-config"' | grep version
```
Expected: `0.1.0` / `0.1.2` / `0.1.2`。

- [ ] **Step 2: 失敗するテストを書く**

`tests/test_consistency.py` に追加する。既存の import に `subprocess` を足す。

```python
class TagTests(unittest.TestCase):
    """Tags are a release contract: `herdr plugin install --ref vX.Y.Z` builds
    from them, and scripts/build.sh runs `cargo build --release --locked`, so a
    tag pointing at a commit whose Cargo.lock disagrees is uninstallable."""

    EXPECTED = {"v0.1.0": "0622df4", "v0.1.2": "16bd1b7"}

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(ROOT), *args],
            text=True, capture_output=True, check=True,
        ).stdout.strip()

    def test_only_the_two_real_versions_are_tagged(self):
        # 0.1.1 never existed: only 0622df4 and eb38ecb ever touched the
        # version, and it went 0.1.0 -> 0.1.2.
        tags = set(self.git("tag").splitlines())
        self.assertEqual(tags, set(self.EXPECTED))

    def test_each_tag_points_at_the_intended_commit(self):
        # Version agreement alone is not enough: 16de6df also declares 0.1.0
        # and also builds, so the expected object id has to be pinned.
        for tag, short in self.EXPECTED.items():
            with self.subTest(tag=tag):
                actual = self.git("rev-parse", f"{tag}^{{commit}}")
                expected = self.git("rev-parse", f"{short}^{{commit}}")
                self.assertEqual(actual, expected)

    def test_each_tag_has_agreeing_versions(self):
        for tag in self.EXPECTED:
            with self.subTest(tag=tag):
                manifest = self.git("show", f"{tag}:herdr-plugin.toml")
                cargo = self.git("show", f"{tag}:Cargo.toml")
                lock = self.git("show", f"{tag}:Cargo.lock")
                version = re.search(r'^version = "([^"]+)"', manifest, re.M).group(1)
                self.assertEqual(
                    version,
                    re.search(r'^version = "([^"]+)"', cargo, re.M).group(1),
                )
                locked = re.search(
                    r'name = "hsl-config"\nversion = "([^"]+)"', lock
                ).group(1)
                self.assertEqual(version, locked)
```

- [ ] **Step 3: テストが失敗することを確認**

Run: `python3 -m pytest tests/test_consistency.py -q`
Expected: `TagTests` の 3 件が FAIL。タグが存在しない。

- [ ] **Step 4: タグを作る**

```bash
git tag -a v0.1.0 0622df4 -m "herdr-statusline 0.1.0"
git tag -a v0.1.2 16bd1b7 -m "herdr-statusline 0.1.2"
```

- [ ] **Step 5: テストが通ることを確認**

Run: `python3 -m pytest tests/test_consistency.py -q`
Expected: 7 件 PASS（既存 4 + 新規 3）。

- [ ] **Step 6: 各タグが --locked でビルドできることを確認（AC-B3-2）**

```bash
for tag in v0.1.0 v0.1.2; do
    rm -rf /tmp/tagcheck && mkdir -p /tmp/tagcheck
    git archive "$tag" | tar -x -C /tmp/tagcheck
    (cd /tmp/tagcheck && CARGO_TARGET_DIR=/tmp/tagcheck/target \
        cargo build --release --locked) && echo "$tag OK" || echo "$tag FAILED"
done
```
Expected: 両方 `OK`。

- [ ] **Step 7: CI の checkout に fetch-depth を足す**

`test` ジョブの `actions/checkout` を差し替える。既定の深さ 1 ではタグが取得されず、
`TagTests` が何も見えないまま失敗する。

```yaml
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
```

- [ ] **Step 8: タグを push する**

⚠️ これは外向きの操作である。実行前に確認を取ること。

```bash
git push origin v0.1.0 v0.1.2
```

- [ ] **Step 9: Commit**

```bash
git add tests/test_consistency.py .github/workflows/ci.yml
git commit -m "test: pin the release tags and their version agreement"
```

---

## 完了時の全体検証

すべてのタスク完了後に実行する。

- [ ] **AC-G-1 〜 AC-G-4**

```bash
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --locked
make test
```
Expected: すべて exit 0。pytest の出力に `skipped` が 0 件。

- [ ] **AC-G-5: 並列が直列の 75% 以下**

```bash
for i in 1 2 3 4 5; do /usr/bin/time -f "par %e" make test 2>&1 | tail -1; done
for i in 1 2 3 4 5; do /usr/bin/time -f "ser %e" make test-serial 2>&1 | tail -1; done
```
Expected: 並列の中央値 ≤ 直列の中央値 × 0.75。達成できない場合は実測値を記録し、
仕様 §8-5 の通り閾値ではなく実測値で再判断する（未達それ自体は実装のやり直しを意味しない）。

- [ ] **中核ロジックの振る舞いが変わっていないこと**

```bash
git diff master --stat -- src/ tmux/base.conf
python3 -m pytest tests/test_hsl_internal.py tests/test_tmux_runtime.py -q
```
Expected: `src/` と `tmux/base.conf` に差分なし。両テストとも PASS。

- [ ] **shellcheck の抑制が SC2329 のみ**

Run: `grep -c 'shellcheck disable' bin/hsl-internal scripts/*.sh scripts/lib/*.sh scripts/run-in-tmux`
Expected: `run-in-tmux` が 3、他は 0。

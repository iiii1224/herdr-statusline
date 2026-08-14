# リポジトリ健全化 — 設計仕様

- 日付: 2026-08-15
- 対象リポジトリ: `herdr-statusline`
- 検証環境: tmux 3.7b / Linux WSL2 / herdr 0.8.0 / rustc 1.97.1 / shellcheck 0.11.0 / Python 3.12
- 改訂: rev1（初版）

---

## 1. 背景と目的

`herdr-statusline` の中核ロジック（`src/`・`bin/hsl-internal`・`scripts/run-in-tmux`）は
既に高い水準にある。`cargo fmt --check` と `cargo clippy -D warnings` は無指摘、Rust 単体
46 件と Python 統合 132 件が全通過し、シェルスクリプトは shellcheck の観点でも実質無指摘
である（F7）。

本仕様が扱うのは中核ロジックではなく、その**周辺**である。すなわち配布物としての体裁、
CI の検出力、テスト実行時間、そして出荷コードが外部プロセスへ与える副作用。いずれも
「動いているが、放置すると静かに損失を出す」種類の欠落であり、機能追加ではない。

例外的に 1 件だけ、出荷コードの実バグを含む（§5.A、F1）。

### 解決する課題

1. 出荷スクリプトが git の index lock を奪い、ユーザー自身の git 操作を失敗させうる
2. MIT を主張しながら LICENSE ファイルが無く、バージョンを固定する手段（タグ）も無い
3. CI が 1200 行の POSIX シェルを構文チェックしかしておらず、機能テストが黙って skip されうる
4. テストが 112 秒かかり、その 69% が 1 モジュールの直列実行に起因する
5. 主要機能（`mouse_clicks`）が README に存在せず、設定検証手順が内部実装に依存している

---

## 2. スコープ

### 対象

6 ワークストリーム 15 項目。内訳は §5 の各節に対応する。

| WS | 項目 | 件数 |
| --- | --- | --- |
| **A** | 出荷スクリプトの git lock 回避 | 1 |
| **B** | LICENSE / CHANGELOG / タグ / MSRV / PATH 警告 | 5 |
| **C** | shellcheck / ワークフロー整備 / 黙った skip の禁止 | 3 |
| **D** | pytest + xdist 移行 / 固定待機の除去 | 2 |
| **E** | README / `--hsl-check-config` / CLI ドリフト検出 | 3 |
| **F** | `.gitignore` に `.claude/worktrees/` | 1 |
| | | **15** |

> 検討開始時の概算は「12 項目」だったが、実際に数えると 13 であり（当初の列挙で
> ワークストリーム A の 1 件が採番から漏れていた）、さらに B のタグと CHANGELOG を
> 独立した実装単位として分離したため 15 となった。数え方の差であって、範囲は
> 合意時点から変わっていない。

### 非対象

- **中核ロジックの変更**。`is_interactive()` の分類規則、`option_name()` の許可リスト、
  `run-in-tmux` の二段階起動、purge の検証則は一切触らない。F14・F15 の通り、これらは
  herdr 0.8.0 に対しても現に正しい。
- **バージョン bump の自動化**。`scripts/bump-version.sh` 相当は検討したが、利用者判断に
  より非対象（§9-1）。
- **GitHub Release ワークフロー**。同上。
- **macOS 対応**。`herdr-plugin.toml` の `platforms = ["linux"]` は据え置く。
- **`toml` クレートの 0.9 系への更新**、および依存削減。挙動変更を伴うため別件とする。
- **`status_interval` 既定値の変更**。§5.A で扱うのは lock 回避のみで、ポーリング頻度の
  設計是非には踏み込まない。

---

## 3. 前提となる実測事実

すべて §先頭の検証環境で実測した。推測は F 番号を与えず、本文中で明示的に「未測定」と書く。

| # | 事実 | 設計への影響 |
| --- | --- | --- |
| F1 | `scripts/default-herdr-info.sh:57` は `git -C "$cwd" status --porcelain=v1` を `GIT_OPTIONAL_LOCKS` 無指定で呼ぶ。`git status` は index の stat 情報を更新するため `index.lock` を取得しうる | §5.A の根拠。既定 `status_interval = 1` で毎秒発火する構成を `scripts/default-config.toml` が明示的に案内しているため、ユーザーの `git add` / `commit` / `rebase` と競合しうる |
| F2 | 同スクリプトの他の git 呼び出し（`symbolic-ref` / `rev-parse` / `rev-list` / `stash list`）は index lock を取らない | 現時点で実害があるのは `status` のみ。ただし §5.A は個別前置ではなく先頭一括 export を採る（理由は §5.A） |
| F3 | `tests/test_herdr_info.py` は fake git を使わず、**実 git を temp リポジトリに対して**実行する（`git init` / `commit` / `stash` / `branch --set-upstream-to`）。PATH 隔離の仕組みは既にあり、`test_survives_a_missing_git` が利用している | §5.A のテストは「環境変数を記録して実 git へ exec する shim」を PATH 先頭に置く方式を採る。ソース文字列の grep は採らない |
| F4 | `LICENSE` / `COPYING` / `CHANGELOG` / `CONTRIBUTING` のいずれも存在しない。一方 `Cargo.toml` は `license = "MIT"` を宣言し、README は MIT バッジを掲げる | §5.B-1、§5.B-2 |
| F5 | `git tag` の出力は空。コミット数は 34 | §5.B-3。遡及タグ付けが必要 |
| F6 | `herdr plugin install` は `--ref <REF>` オプションを持つ（`herdr plugin install --help` で確認） | F5 と併せて §5.B-3 の価値を確定させる。タグが無い限りこのオプションは利用者にとって死んでいる |
| F7 | shellcheck 0.11.0 を全 7 スクリプトに `-s sh` で適用した結果、指摘は 4 種のみ。SC1007（`CDPATH= cd` イディオム、5 箇所）、SC1090（動的 source、2 箇所）、SC1083（`@{upstream}`、1 箇所）、SC2329（trap 経由でのみ呼ばれる関数、3 箇所）。**いずれも意図的な記述で、真の欠陥は 0 件** | §5.C-1。導入コストが現時点で最小であり、これ以上先延ばしする理由が無い |
| F8 | `.github/workflows/ci.yml` は `sh -n` による構文チェックのみを行う。cargo キャッシュ無し、`permissions` 宣言無し、`on: push`（全ブランチ）と `on: pull_request` の併記により PR ブランチで二重実行される | §5.C-1、§5.C-2 |
| F9 | `tests/test_tmux_mouse.py` の 4 クラス全てに `@unittest.skipUnless(tmux_at_least_3_4(), ...)` が付く。tmux 不在または 3.4 未満の環境では 12 件が**失敗ではなく skip** となり、CI は緑のままとなる | §5.C-3。機能まるごとの検証が無言で消える経路 |
| F10 | モジュール別実測時間: `test_tmux_mouse` 77.5s / `test_tmux_runtime` 19.5s / `test_launcher` 3.6s / `test_hsl_internal` 3.6s / `test_herdr_info` 0.3s / `test_build` 0.1s / `test_consistency` 0.0s。全体で 132 件 111.7s | §5.D。総時間の 69% が 1 モジュールに集中 |
| F11 | `test_tmux_mouse` の 4 クラスを 4 プロセスで同時実行したところ、**全クラス OK で 47.5s**（直列 77.5s）。tmux ソケット名は `mktemp` 由来で呼び出しごとに一意、各テストは専用 tempdir と専用 `TMPDIR` を持つため、共有状態の衝突は無い | §5.D-1。並列化は安全に成立する |
| F12 | `tests/mouse_pty.py:103`、`HslPty.__enter__` は `self._drain(3.0)` を**無条件**で実行する。同ファイルの `INNER_APP` は起動直後に `open(log, "w").close()` でログファイルを作成する | §5.D-2。ログファイルの出現が readiness シグナルとして利用可能で、テスト側は既にそのパスを保持している |
| F13 | `tests/helpers.py:40-53`、`ensure_helper()` はモジュールグローバル `_HELPER_BUILT` により「1 プロセスにつき 1 回」`cargo build --release --locked` を実行する | §5.D-1 の最重要論点。xdist のワーカーは別プロセスであり、N ワーカーが同一 `target/` に対して N 回のビルドを起動する |
| F14 | herdr 0.8.0 は 0.7.5 に無かったグローバルオプション `--skill` を持つ。`is_interactive()` に通すと、`--remote` 不在のため skip ループに入らず、`case $1` のどの分岐にも該当せず `return 1` となり、`exec herdr --skill` へ pass-through される（＝正しい挙動） | 分類器の default-deny 設計が新規グローバルに対して機能していることの確認。**中核ロジックの変更は不要** |
| F15 | herdr 0.8.0 の `attach` を持つサブコマンドは `session` / `agent` / `terminal` の 3 つのみ。`pane` / `workspace` / `tab` / `integration` に `attach` は存在しない | 分類器の `session\|agent\|terminal` 網羅は 0.8.0 に対しても正確。**中核ロジックの変更は不要** |
| F16 | `bin/hsl-internal:22` のコメントは分類規則を "Measured against herdr 0.7.5" と記す。この照合を再実行する仕組みはリポジトリ内に存在しない | §5.E-3。F14・F15 は今回手動で確認したが、その検証はどこにも残っていない |
| F17 | `skills/customize-herdr-statusline/SKILL.md:93-103` は設定検証手順として、`herdr plugin list --json` を parse して `plugin_root` を取り出し、`$plugin_root/target/release/hsl-config load` を直接実行することをエージェントに指示している | §5.E-2。ビルド成果物の内部パスが公開契約に漏れている |
| F18 | README に `mouse` を含む行は 0 件。`mouse_clicks` の記述は `scripts/default-config.toml` のコメントと SKILL.md にのみ存在する | §5.E-1 |
| F19 | `.gitignore` の内容は `/target`・`__pycache__/`・`*.pyc` の 3 行のみ。`.claude` は `git check-ignore` に一致しない | §5.F。`EnterWorktree` はリポジトリ内の `.claude/worktrees/` に作業ツリーを作成する |
| F20 | `Cargo.toml` に `rust-version` の宣言が無い。依存は 35 クレート（`serde` / `serde_json` / `toml` / `tempfile` とその推移閉包） | §5.B-4。MSRV の具体値は**未測定**であり、実装時に CI で確定させる |
| F21 | ローカル `master` と `origin/master` は同一コミット `16bd1b7`（0 ahead / 0 behind） | 遡及タグ付け（§5.B-3）が push 済み履歴に対して安全に行えることの確認 |
| F22 | master 最新コミットは `16bd1b7 fix: sync Cargo.lock with the 0.1.2 version bump` である | バージョン同期漏れが実際に発生した記録。§9-1 で bump 自動化を非対象とした判断の背景 |

---

## 4. 設計方針

3 点を貫く。

1. **中核ロジックを触らない。** F14・F15 が示すとおり、分類器も許可リストも herdr 0.8.0
   に対して現に正しい。本仕様の変更は周辺に限定し、`src/config.rs`・`src/purge.rs`・
   `bin/hsl-internal` の `is_interactive()`・`scripts/run-in-tmux` の tmux 配線には
   変更を加えない。
2. **黙る失敗を作らない。** §5.C-3 で skip の黙認を禁じる以上、本仕様が新設する検証も
   同じ規律に従う。§5.E-3 がこの制約と正面衝突するため、そこだけ特別な設計を採る。
3. **既存のテスト規律を守る。** このリポジトリのテストは「本番の配線そのものを起動する」
   ことを原則としている（`tests/mouse_pty.py` 冒頭の記述）。新規テストも実物を動かす。

---

## 5. ワークストリーム別設計

### 5.A 出荷スクリプトの git lock 回避

**変更**: `scripts/default-herdr-info.sh` の先頭付近（`set -u` の直後、スタイル定数の前）に
`export GIT_OPTIONAL_LOCKS=0` を 1 行追加する。

**個別前置ではなく先頭一括 export を採る理由**: F2 のとおり実害があるのは `status` の
1 箇所のみで、そこだけ前置しても現時点の正しさは同じである。しかし本スクリプトは
`config.toml` のコメントがユーザーへ改造を明示的に勧める「テンプレート」であり、将来
追記される git 呼び出しが同じ穴を再び開ける。先頭一括なら追記が自動的に保護される。

**`GIT_OPTIONAL_LOCKS=0` の意味**: git 2.15 以降、この環境変数は「lock を取れないなら
取らずに済ませる」ことを git に指示する。`git status` は index を書き戻さなくなり、
報告内容は変わらない（stat 情報の再利用が効かないぶん、大規模リポジトリでは僅かに遅く
なりうる）。プロンプト／ステータスライン統合における標準的な対処である。

**テスト設計**: F3 のとおり既存テストは実 git を使う。したがって、

- `tests/test_herdr_info.py` に、PATH 先頭へ `git` shim を置くテストを 1 件追加する。
- shim は `printf '%s\n' "${GIT_OPTIONAL_LOCKS:-<unset>}" >> "$log"` で環境を記録した後、
  実 git へ `exec` する（記録専用にせず実行も通すことで、既存フィクスチャの構築が壊れない）。
- 表明は「記録された全行が `0` であること」および「1 行以上記録されたこと」の 2 点。
  後者が無いと、git が一度も呼ばれない経路で偽陽性の合格になる。

**受け入れ条件**: 上記テストが追加前のスクリプトに対して**失敗**することを実装時に確認する
（テストが本当に対象を捉えていることの証明）。

---

### 5.B 配布物の体裁

#### 5.B-1 LICENSE

リポジトリ直下に `LICENSE` を追加。MIT の全文とし、著作権表記は
`Copyright (c) 2026 IIAD Yusuke` とする。

> **前提**: 著作権者名は git のコミット identity（`IIAD Yusuke`）を採用した。GitHub の
> owner 名は `iiii1224` であり、法人名や別表記を希望する場合はここだけ差し替えれば足りる。
> F4 のとおり `Cargo.toml` に `authors` フィールドが無いため、リポジトリ内に確定的な
> 典拠が存在しない。

#### 5.B-2 CHANGELOG

`CHANGELOG.md` を Keep a Changelog 形式で追加。`## [Unreleased]` に続き、`0.1.2` /
`0.1.1` / `0.1.0` を git log から遡及記載する。遡及分は網羅的な列挙ではなく、各版で
利用者から見て何が変わったかの要約に留める（履歴の再構成ではなく、以後の運用開始点を
作ることが目的）。

#### 5.B-3 タグ

F5・F6・F21 より、`v0.1.0` / `v0.1.1` / `v0.1.2` を該当コミットへ遡及付与する。
対応コミットは `herdr-plugin.toml` の `version` を変更したコミットを典拠として実装時に
特定する（`Cargo.toml` 側は F22 の同期漏れがあるため典拠にしない）。

タグ形式は `v` 接頭辞付き。`herdr plugin install iiii1224/herdr-statusline --ref v0.1.2`
が成立することを受け入れ条件とする。

#### 5.B-4 MSRV

`Cargo.toml` の `[package]` に `rust-version` を追加する。**値は推測しない。**

確定手順:

1. 候補バージョンを 1 つ選び、そのバージョンに pin した CI ジョブで
   `cargo build --release --locked` と `cargo test` を実行する。
2. 通れば候補を下げ、落ちれば上げる。通る最小値を `rust-version` に採用する。
3. 採用値に pin した CI ジョブを恒久的に残す。これにより宣言値は構成的に正しくなり、
   依存更新で MSRV が上がった場合に CI が検知する。

`rust-version` を宣言すれば cargo 自身が古い toolchain に対して明快なエラーを出すため、
`scripts/build.sh` に追加のバージョン判定は書かない。F20 のとおり本プラグインは全利用者が
インストール時にコンパイルするため、この宣言の実用的価値は通常のクレートより高い。

#### 5.B-5 PATH 警告

`scripts/build.sh` の末尾、`install-launcher.sh` の実行成功後に、`$bindir` が `PATH` の
要素として含まれるかを検査し、含まれなければ stderr へ警告を出す。ビルドは失敗させない
（PATH はユーザーのシェル設定の問題であり、インストール自体は成功しているため）。

判定は `case ":$PATH:" in *":$bindir:"*)` の形を採り、部分一致による誤判定を避ける。

---

### 5.C CI の検出力

#### 5.C-1 shellcheck の導入

CI に shellcheck ステップを追加し、`.github/workflows/ci.yml` の既存 `sh -n` ステップは
残す（shellcheck は構文チェックの上位互換ではあるが、両者の失敗メッセージの質が異なる）。

**抑制方針**: F7 の 4 種について、**グローバル除外（`-e SC1007,...`）ではなく、該当行への
個別 `# shellcheck disable=` 指示を採る。** グローバル除外にすると、同じコードの新しい
*本物の* 違反が今後黙って通る。SC1007 は 5 箇所あり指示行も 5 行に増えるが、ここは
冗長性より検出力を優先する。

各抑制には理由を 1 行添える（例: SC1007 には「`CDPATH=` は空代入ではなく、後続 `cd` への
一時的な環境変数指定である」）。理由の無い抑制は置かない。

対象は CI の `sh -n` が現在列挙している 7 ファイルと同一とし、両者の対象集合が食い違わ
ないようにする。

#### 5.C-2 ワークフローの整備

F8 に対して 4 点:

- `permissions: contents: read` をワークフロー既定として宣言する。
- cargo キャッシュを導入する（`Swatinem/rust-cache`）。
- `on: push` を既定ブランチ限定にし、`on: pull_request` と併せて PR ブランチでの
  二重実行を解消する。
- `concurrency` グループを設定し、同一 ref への連続 push で古い実行を打ち切る。

#### 5.C-3 黙った skip の禁止

F9 に対して、テスト実行の**前**に前提条件を表明するステップを置く。

- tmux が存在し、かつバージョンが 3.4 以上であることを検査し、満たさなければ CI を落とす。
- 同様に `script`（util-linux）の存在も検査する。`tests/test_tmux_runtime.py` の
  `RealTmuxSmokeTests` が `skipUnless(shutil.which("script"))` で同じ黙った skip の
  経路を持つため。

この検査は「テストが skip されなかったこと」を保証するものではなく「skip の原因となる
前提の欠落が CI で起きないこと」を保証する。前者を直接保証する手段（skip 件数の表明）は
pytest 移行後に `-p no:randomly --strict-markers` 等と併せて再検討しうるが、本仕様では
前提検査に留める。

---

### 5.D テスト基盤

#### 5.D-1 pytest + pytest-xdist への移行

**方針**: 既存の `unittest.TestCase` 派生クラスは pytest がそのまま収集・実行するため、
**テスト本体のコードは書き換えない**。追加するのは実行機構のみ。

- `requirements-dev.txt` に `pytest` と `pytest-xdist` を固定バージョンで記載する。
- `pyproject.toml` に `[tool.pytest.ini_options]` を置き、`testpaths = ["tests"]` と
  必要な `pythonpath` 設定を行う。`tests/__init__.py` と `from tests.helpers import ...`
  という既存のパッケージ構成は維持する（rootdir 相対の import が壊れないため）。
- CI の実行を `pytest -n auto --dist loadscope` に置き換える。`loadscope` を選ぶ理由は
  F11 の測定がクラス単位の分割で成立していること、および各クラスの `setUp` が
  重い（tempdir + fake bin 構築）ためテスト単位分割の利得が相対的に小さいこと。

**最重要論点 — F13 のビルド競合**:

`ensure_helper()` の `_HELPER_BUILT` は「1 プロセス 1 回」の保証しか与えない。xdist の
ワーカーは独立プロセスであるため、N ワーカーが同時に `cargo build --release --locked` を
起動する。cargo は `target/` ディレクトリロックを取るので**破損はしない**が、N-1 個の
ワーカーが起動時にロック待ちで直列化し、並列化の利得を起動時に食い潰す。

**設計**: ヘルパーのビルドをテスト実行の**前段**へ移し、ワーカーからは一切ビルドしない。

- CI には既に「Build the release helper」ステップが存在する。これを pytest 実行の
  前提と位置づける。
- `tests/conftest.py` を新設し、session スコープの autouse フィクスチャでビルドを 1 度だけ
  実行する。xdist 下では各ワーカーもこのフィクスチャを評価するため、`request.config` に
  `workerinput` 属性が**無い**プロセス（＝親）でのみ実際のビルドを行い、ワーカーは成果物の
  存在検証のみを行う。
- `helpers.ensure_helper()` は「ビルドする」関数から「存在を検証し、無ければ何を実行すべきか
  を示すエラーで落ちる」関数へ縮退させる。モジュールグローバル `_HELPER_BUILT` は削除する。

> xdist の親プロセスがフィクスチャを評価しない構成（`-n` 指定時に collection のみ親で
> 走る等）が判明した場合は、ファイルロックを用いた「最初に到達したワーカーがビルドし、
> 他は待つ」方式へ切り替える。切り替えても受け入れ基準は変わらない（§9-3）。

> この論点は実装中に初めて顕在化すると、原因が「なぜか並列化しても速くならない」という
> 形でしか見えない。仕様段階で固定しておく価値が高い。

**受け入れ条件**: 移行後の全 132 件が通ること、および実行時間が直列実行より有意に短い
ことを実測で示すこと。目標値は設定しない（F11 の 47.5s は 4 クラス並列時の値であり、
5.D-2 適用後の値は未測定のため）。

#### 5.D-2 固定待機の除去

F12 に対して、`tests/mouse_pty.py` の `HslPty.__enter__` の `self._drain(3.0)` を
readiness ポーリングへ置換する。

- **シグナル**: `INNER_APP` が起動直後に作成するログファイルの出現。パスは
  `HslPty` の呼び出し側（`MouseIntegrationBase.runtime_env`）が既に `self.app_log` として
  保持しているため、`HslPty` へ渡すだけでよい。
- **実装**: 既存の `wait_for_*` と同じ deadline ループ形式を採り、上限は現行の固定値
  より十分大きく取る（3.0s は「十分待つ」ための値であって上限ではないため、上限としては
  短すぎる可能性がある）。上限超過時は明示的に失敗させる。
- **`_send` の settle は据え置く。** `test_clicking_outside_every_range_does_nothing` の
  ように「反応が*無い*こと」を確認するテストが存在し、これらは本質的に時間で待つ以外の
  方法が無い。ここを削ると偽陽性を生む。

削減効果の大半は起動時の 3 秒側にある。この点は誇張せず仕様に明記する。

---

### 5.E ドキュメントと契約

#### 5.E-1 README への mouse_clicks 節

F18 に対して、README に節を追加する。含める内容:

- opt-in であること、および代償（外側端末のネイティブ選択とミドルクリック貼り付けを失う）
- tmux 3.4 以上が必要であること
- `on-click.sh` が本プラグインの提供物では**ない**こと。フックはユーザーまたは別プラグイン
  が所有する
- フックの 4 引数の契約
- 詳細は `config.toml` のコメントと同梱スキルにあること

`scripts/default-config.toml` のコメントと重複する記述になるが、README は「導入前の利用者」、
config コメントは「導入後の編集者」を読者とするため、要約と詳細という関係で共存させる。

#### 5.E-2 `--hsl-check-config`

F17 に対して、`hsl` に設定検証の導線を追加する。

**形式**: `hsl --hsl-check-config [<config.toml のパス>]`

**`--hsl-` 接頭辞を採る理由**: herdr 0.8.0 のグローバルオプションは `--session` /
`--remote` / `--no-session` / `--handoff` / `--remote-keybindings` / `--default-config` /
`--skill` / `--version` / `-V` / `--help` / `-h` である。`--hsl-` 接頭辞は herdr 側の
命名規則と構造的に交わらないため、将来 herdr が何を追加しても衝突しない。サブコマンド形式
（`hsl doctor` 等）は人間にとって読みやすいが、herdr のサブコマンド名前空間を 1 語占有する
リスクを負う。エージェント向けの検証導線であり、衝突不能性を可読性より優先する。

**実装位置**: `scripts/launcher-body.sh` が `uninstall` を横取りしているのと同じ位置。
launcher は `PLUGIN_ROOT` を既に解決済みであるため、`$PLUGIN_ROOT/target/release/hsl-config`
へ委譲できる。引数を省略した場合は `herdr plugin config-dir herdr-statusline` を解決して
その `config.toml` を対象とする。

**`root_is_complete` との関係**: 既存の launcher は不完全なインストールを検出して再解決
する経路を持つ。`--hsl-check-config` もこの経路の**後**で処理する（`uninstall` と同じ）。

**SKILL.md の更新**: F17 の 3 ステップ手順を `hsl --hsl-check-config` の 1 行へ差し替える。

**テスト**: `tests/test_launcher.py` に、正常な config で成功し、壊れた config で非ゼロ終了
することを確認するテストを追加する。既存の fake herdr / fake bin 機構を使う。

#### 5.E-3 herdr CLI ドリフト検出

F16 に対する対処だが、**§4-2 の「黙る失敗を作らない」と正面衝突する**。herdr は CI に
存在しないため、実バイナリを叩くテストは CI で必ず skip され、§5.C-3 で禁じたばかりの
経路を再導入してしまう。

**設計 — 2 段構え**:

1. **記録フィクスチャに対するテスト（CI で常時実行）**: `herdr --help` および
   関連サブコマンドの `--help` 出力を `tests/fixtures/` へコミットする。分類器が前提と
   している性質——「`attach` を持つサブコマンドは `session` / `agent` / `terminal` の 3 つ」
   「グローバルオプションの集合」——を、この記録に対して表明する。herdr の有無に依存しない
   ため CI で常に走り、skip されない。
2. **実バイナリとの差分テスト（opt-in）**: 記録フィクスチャと実 `herdr --help` の出力を
   比較する。herdr が無ければ skip されるが、これは**明示的な opt-in**（環境変数または
   pytest マーカーで選択）とし、既定の実行集合には含めない。「既定で走るが黙って skip される」
   という状態を作らないことが要点である。

この構造により、CI は「分類器が記録された CLI と整合していること」を常に検証し、開発者は
herdr 更新時に「記録が実物と乖離していないこと」を意図的に検証できる。フィクスチャ更新の
手順は CONTRIBUTING 相当の記述として README または `docs/` に残す。

**フィクスチャの版**: herdr 0.8.0 の出力を記録する。F14・F15 は本仕様策定時に手動確認済み
であり、その確認内容がフィクスチャとして固定される。

---

### 5.F リポジトリ衛生

F19 に対して、`.gitignore` に `/.claude/worktrees/` を追加する。

`EnterWorktree` はリポジトリ内の `.claude/worktrees/<name>` に作業ツリーを作成する。
`.claude/` が無視対象でないため、親チェックアウトでこのディレクトリが未追跡として現れ、
不用意な `git add -A` の対象になりうる。

**`.claude/` 全体ではなく `worktrees/` のみに限定する。** `.claude/settings.json` など、
将来リポジトリで共有したくなる設定が同じ階層に置かれうるため、それらは追跡可能なまま
残す。既存の `/target` と同じくリポジトリ直下限定を意図するため `/` を前置する。

---

## 6. 依存順序

```
F (.gitignore)  ──┐
                  │
A (git lock)  ────┼──▶ 独立、順不同
B (配布物)     ────┤
E-1 (README)  ────┘

C-1 (shellcheck) ──┐
C-2 (workflow)   ──┼──▶ 同一ファイル (.github/workflows/ci.yml) を編集するため直列化
C-3 (前提検査)    ──┤
D-1 (pytest)     ──┘   ※ D-1 は CI の実行コマンドを差し替えるため C と同じファイルに触る

D-2 (固定待機)  ──▶ D-1 と独立（tests/mouse_pty.py のみ）だが、効果測定は D-1 の後

E-2 (--hsl-check-config) ──▶ 独立
E-3 (ドリフト検出)        ──▶ C-3 の方針確定後（skip 方針が前提）
```

**直列化が必要な唯一の集合は C-1 / C-2 / C-3 / D-1** である。いずれも
`.github/workflows/ci.yml` を編集するため、並行実装すると衝突する。実装計画ではこの 4 件を
1 本の系列として扱う。

B-4（MSRV）は C-2（CI 整備）の後に置くと、追加する pin ジョブが整備済みのワークフロー上に
乗るため手戻りが少ない。

---

## 7. テスト戦略の総括

新規に追加するテストは以下。既存 132 件は 1 件も削除しない。

| 対象 | テスト | 実行環境 |
| --- | --- | --- |
| §5.A | git shim による `GIT_OPTIONAL_LOCKS=0` の記録検証 | CI（実 git のみ必要） |
| §5.B-3 | タグの存在と `herdr-plugin.toml` の version との対応（`test_consistency.py` を拡張） | CI |
| §5.B-4 | MSRV に pin した build + test ジョブ | CI（別ジョブ） |
| §5.E-2 | `--hsl-check-config` の正常系・異常系 | CI |
| §5.E-3 | 記録フィクスチャに対する分類器の性質表明 | CI（常時） |
| §5.E-3 | 実 herdr との差分 | opt-in のみ |

`test_consistency.py` の既存 4 件が担っている「物理的に単一化できない定数の一致」という
役割は維持する。タグ検証を同ファイルに置くのが妥当かは実装時に判断する（タグは
ワーキングツリーの内容ではないため、別ファイルが適切な可能性がある）。

---

## 8. 受け入れ基準

1. `cargo fmt --check`・`cargo clippy --all-targets --all-features -- -D warnings`・
   `cargo test` が無指摘で通る。
2. Python テストが全件通り、実行時間が移行前（111.7s）より有意に短い。
3. shellcheck が CI で通り、抑制指示はすべて理由付きで、対象は `sh -n` の対象集合と一致する。
4. tmux または `script` が欠けた環境で CI が**失敗する**（skip して緑にならない）。
5. `LICENSE` が存在し、`Cargo.toml` の `license` と README のバッジと矛盾しない。
6. `git tag` が `v0.1.0` / `v0.1.1` / `v0.1.2` を含み、`--ref v0.1.2` での取得が成立する。
7. `rust-version` が宣言され、その値に pin した CI ジョブが通る。
8. `hsl --hsl-check-config` が壊れた `config.toml` に対して非ゼロ終了する。
9. SKILL.md に `plugin_root` を parse させる記述が残っていない。
10. README に `mouse_clicks` の節が存在する。
11. 中核ロジック（§2 非対象に列挙したファイル・関数）に差分が無い。

---

## 9. 判断の記録と未解決点

1. **バージョン bump 自動化を非対象とした。** F22 のとおり同期漏れは実際に発生しており、
   `scripts/bump-version.sh` を作る根拠はあった。利用者判断により「タグ + CHANGELOG のみ」
   を採用。`test_consistency.py` が同期の**検査**を担い続けるため、漏れは検知はされる
   （実行されるのは CI 上であり、bump 時点ではない）。
2. **MSRV の値が未確定。** F20 のとおり未測定であり、§5.B-4 の手順で実装時に確定させる。
   確定値が現行の CI ランナーの stable より大きく古い場合、pin ジョブの追加が CI 時間を
   押し上げる点は許容する。
3. **`ensure_helper` のビルド前段化は、xdist の親子プロセス判定に依存する。** §5.D-1 では
   `workerinput` の有無による親判定を採用したが、この属性の有無は xdist の実行構成に
   依存する実装詳細である。実装時に想定どおり動かない場合はファイルロック方式へ
   切り替える。いずれの方式でも受け入れ基準 2 は変わらない。
4. **`_send` の settle を残す判断。** §5.D-2 のとおり「反応が無いこと」を待つテストが
   存在するため据え置いた。将来、否定的表明を別の機構（tmux 側のイベントカウンタ等）で
   置き換えられれば削減余地があるが、本仕様では扱わない。
5. **`status_interval = 1` の既定値そのものは触らない。** §5.A は lock 競合のみを解く。
   毎秒 `git status` を実行する構成の是非（大規模リポジトリでの負荷）は残る課題であり、
   §5.E-1 / config コメントでの注意喚起に留めるか、別件で扱う。

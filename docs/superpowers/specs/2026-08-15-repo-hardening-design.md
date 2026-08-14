# リポジトリ健全化 — 設計仕様

- 日付: 2026-08-15
- 対象リポジトリ: `herdr-statusline`
- 検証環境: tmux 3.7b / Linux WSL2 / herdr 0.8.0 / rustc 1.97.1 / shellcheck 0.11.0 /
  Python 3.12 / pytest 9.1.1 / pytest-xdist 3.8.0
- 改訂: rev4（codex レビュー 3 巡目の指摘 1 件を検証。**修正案は前提が偽であることを実測で
  示したうえで代替案を採用**。1 巡目 10 件・2 巡目 6 件は全件採用）

### rev3 → rev4 の変更

| 指摘 | 変更 |
| --- | --- |
| Medium | AC-E3-4 の `attach` 集合の厳密一致は、fixture が 3 サブコマンドの help しか持たないため正例しか検査していなかった。**指摘は正しい。** ただし提示された修正案「root command graph から全 top-level subcommand を列挙して厳密集合を検査」は前提が偽である——F29 の通り `herdr --help` に `plugin` は現れないが `herdr plugin` は実在し、しかも `bin/hsl-internal` が最も依存するサブコマンドである。この案に従うと `plugin` を欠いた集合で「厳密一致」に合格し、網羅性について偽の確信を与える。よって代替案（正例 + best-effort な負例、網羅性を主張しない）を採用し、§8-7 に限界と、それが許容できる理由（default-deny のため取りこぼしは機能低下であって破壊ではない）を記録した |

### rev2 → rev3 の変更

| 指摘 | 変更 |
| --- | --- |
| High | `v0.1.0` のタグ位置を `16de6df` → **`0622df4`** に変更。F27 の実測（`0622df4..16de6df` が +3743 行、`16de6df..16bd1b7` が +3 行）により、前者だと機能のほぼ全部が 0.1.0 に属し 0.1.2 が bump だけになる。AC-B3-1 に期待 OID を固定 |
| High | §5.D-2 の readiness に**さらに深いレースが残っていた**。`flush()` は tmux server が tracking を読んだことを保証しない。marker 作成前に tmux の mouse flag を poll する設計へ変更 |
| High | §5.E-3 の 3 層について、各層が**何を検出しないか**を明示。層 1 は上流 drift を一切観測しない旨を明記し、rev2 の「主たる防御」という記述を撤回。層 2 にサブコマンド help を追加（root help だけでは `agent attach` を抽出できない）。AC を 3 件から 8 件へ |
| Medium | 層 1 を「`is_interactive()` の直接実行」から「既存 `test_hsl_internal.py` の argv 行列の拡張」へ変更（F28）。関数の抽出・source を明示的に禁止 |
| Medium | AC-C1-2 が自己言及で必ず失敗する問題を修正。検査範囲を CI 対象の 7 ファイルに限定 |
| Low | AC-D1-4 を追加（ローカル統一入口の検査） |

### rev1 → rev2 の変更

| 指摘 | 変更 |
| --- | --- |
| Critical | §5.D-1 の `workerinput` × session fixture 設計は**動かない**（実測）。ビルドを pytest の外へ出す設計へ全面変更 |
| High | §5.D-2 の readiness signal がレース含み。専用 ready marker へ変更 |
| High | §5.C-1 の SC1007 は 5 件ではなく **8 件**。抑制中心から**書き換え中心**へ方針転換 |
| High | §5.E-3 の fixture は drift 検出になっていない。契約テスト + 正規化 fixture へ変更 |
| High | 受入基準（rev2 では §7）に AC ID を付与し、全 15 項目を客観判定可能にした |
| — | F1 / F5 / F20 の数値誤り、`GIT_OPTIONAL_LOCKS` の意味の誤り、`--ref` の効用の誇張を訂正 |
| — | タグ対象を **2 本**（`v0.1.1` は存在しない）に訂正し、打てるコミットを実測で確定 |

---

## 1. 背景と目的

`herdr-statusline` の中核ロジック（`src/`・`bin/hsl-internal`・`scripts/run-in-tmux`）は
既に高い水準にある。`cargo fmt --check` と `cargo clippy -D warnings` は無指摘、Rust 単体
46 件と Python 統合 132 件が全通過する。

本仕様が扱うのは中核ロジックではなく、その**周辺**である。すなわち配布物としての体裁、
CI の検出力、テスト実行時間、そして出荷コードが外部プロセスへ与える副作用。いずれも
「動いているが、放置すると静かに損失を出す」種類の欠落であり、機能追加ではない。

例外的に 1 件だけ、出荷コードの実バグを含む（§5.A、F1）。

### 解決する課題

1. 出荷スクリプトが git の index lock を取り、ユーザー自身の git 操作と競合しうる
2. MIT を主張しながら LICENSE ファイルが無く、版を固定する手段（タグ）も無い
3. CI が 1200 行の POSIX シェルを構文チェックしかしておらず、機能テストが黙って skip されうる
4. テストが 112 秒かかり、その 69% が 1 モジュールの直列実行に起因する
5. 主要機能（`mouse_clicks`）が README に存在せず、設定検証手順が内部実装に依存している

---

## 2. スコープ

### 対象

6 ワークストリーム 15 項目。

| WS | 項目 | 件数 |
| --- | --- | --- |
| **A** | 出荷スクリプトの git lock 回避 | 1 |
| **B** | LICENSE / CHANGELOG / タグ / MSRV / PATH 警告 | 5 |
| **C** | shellcheck / ワークフロー整備 / 黙った skip の禁止 | 3 |
| **D** | pytest + xdist 移行 / 固定待機の除去 | 2 |
| **E** | README / `--hsl-check-config` / herdr CLI 契約テスト | 3 |
| **F** | `.gitignore` に `.claude/worktrees/` | 1 |
| | | **15** |

### 非対象

**「非対象」は*振る舞いの変更*を指し、ファイルに一切触れないことを指さない。** rev1 は
この区別を曖昧にしたまま rev1 の受入基準 11 に「非対象ファイルに差分が無いこと」と書き、§5.C-1 が
`bin/hsl-internal` と `scripts/run-in-tmux` を編集することと矛盾していた（codex 指摘 6）。
以下は**観測可能な振る舞いを変えない**という意味での非対象である。

- `bin/hsl-internal` の `is_interactive()` / `cli_session()` / `skips_the_local_session()`
  の**判定結果**。F14・F15 の通り herdr 0.8.0 に対して現に正しい。
- `src/config.rs` の `option_name()` 許可リストと `write_protocol()` の行プロトコル。
- `src/purge.rs` の検証則。
- `scripts/run-in-tmux` の tmux 配線（二段階起動、`wait-for` の解放順序、mouse binding）。
- `tmux/base.conf` の全内容。

§5.C-1 が行う `CDPATH= cd` → `CDPATH='' cd` 等の書き換えは、シェルの評価結果が同一である
ことを前提とした**機械的置換**であり、上記の「振る舞いを変えない」に適合する。適合の確認
方法は AC-C1-3 に定める。

そのほか、以下は本仕様の対象外とする。

- バージョン bump の自動化（§8-1）。
- GitHub Release ワークフロー。
- macOS 対応。`herdr-plugin.toml` の `platforms = ["linux"]` は据え置く。
- `toml` クレートの 0.9 系への更新、および依存削減。
- `status_interval` 既定値の変更（§8-4）。

---

## 3. 前提となる実測事実

すべて §先頭の検証環境で実測した。**推論には F 番号を与えない。** rev1 は F13 として
未実測の推論を実測事実の表に並記していた。これは本仕様自身の規律違反であり撤回した。

| # | 事実 | 設計への影響 |
| --- | --- | --- |
| F1 | `scripts/default-herdr-info.sh:52` は `git -C "$cwd" status --porcelain=v1` を `GIT_OPTIONAL_LOCKS` 無指定で呼ぶ。`git status` は既定で index の stat 情報を更新し、そのために `index.lock` を取得する | §5.A の根拠。既定 `status_interval = 1` で毎秒発火する構成を `scripts/default-config.toml` が明示的に案内しているため、ユーザーの `git add` / `commit` / `rebase` と競合しうる |
| F2 | 同スクリプトの他の git 呼び出し（`symbolic-ref` / `rev-parse` / `rev-list` / `stash list`）は index lock を取らない | 現時点で実害があるのは `status` のみ。§5.A が先頭一括 export を採る理由は将来の追記の保護であって、現在の必要性ではない |
| F3 | `tests/test_herdr_info.py` は fake git を使わず、**実 git を temp リポジトリに対して**実行する。PATH 隔離の仕組みは既にあり `test_survives_a_missing_git` が利用している | §5.A のテストは PATH 先頭の記録 shim 方式を採る |
| F4 | `LICENSE` / `COPYING` / `CHANGELOG` / `CONTRIBUTING` のいずれも存在しない。一方 `Cargo.toml` は `license = "MIT"` を宣言し、README は MIT バッジを掲げる | §5.B-1、§5.B-2 |
| F5 | `git tag` の出力は空。`git rev-list --count HEAD` は 37（本仕様のコミットを含む。master は 36） | §5.B-3 |
| F6 | `herdr plugin install` は `--ref <REF>` を持つ。ref はタグに限らずブランチ名や SHA も受け付ける | §5.B-3。**タグが無くても `--ref` は使える。**タグの価値は「安定した版参照を提供すること」であって、`--ref` を有効化することではない（rev1 の記述を訂正） |
| F7 | shellcheck 0.11.0 を全 7 スクリプトに `-s sh` で適用した結果、**SC1007 が 8 件**（`CDPATH= cd` が 6 件、`pane= cwd=` と `branch= state=` が 2 件）、SC1090 が 2 件、SC1083 が 1 件、SC2329 が 3 件。真の欠陥は 0 件 | §5.C-1。rev1 の「SC1007 は 5 件」は誤り |
| F8 | `.github/workflows/ci.yml` は `sh -n` のみ。cargo キャッシュ無し、`permissions` 宣言無し、`on: push`（全ブランチ）と `on: pull_request` の併記により PR ブランチで二重実行される。`actions/setup-python` も pytest のインストール手順も無い | §5.C-1、§5.C-2、§5.D-1 |
| F9 | `tests/test_tmux_mouse.py` の 4 クラス全てに `skipUnless(tmux_at_least_3_4(), ...)` が付く。`tests/test_tmux_runtime.py` の実 tmux テストは `skipUnless(shutil.which("script"))` を持つ。いずれも前提欠如時は**失敗ではなく skip** | §5.C-3 |
| F10 | モジュール別実測: `test_tmux_mouse` 77.5s / `test_tmux_runtime` 19.5s / `test_launcher` 3.6s / `test_hsl_internal` 3.6s / `test_herdr_info` 0.3s / `test_build` 0.1s / `test_consistency` 0.0s。全体 132 件 111.7s | §5.D |
| F11 | `test_tmux_mouse` の 4 クラスを 4 プロセスで同時実行して**全クラス OK、47.5s**（直列 77.5s） | §5.D-1。並列化は安全に成立する |
| F12 | `tests/mouse_pty.py:103` の `HslPty.__enter__` は `_drain(3.0)` を無条件実行する。同ファイルの `INNER_APP` は `open(log,"w").close()` → `tty.setraw(0)` → mouse tracking 出力 → `flush()` の順で起動する | §5.D-2。**ログファイルの出現は raw mode と tracking の有効化に先行するため、readiness signal として使えない** |
| F13 | `pytest -n 4 --dist loadscope`（pytest 9.1.1 / xdist 3.8.0）で `pytest_configure` は 5 プロセス（親 1・worker 4）で実行されるが、**session スコープの autouse fixture は worker 4 プロセスでのみ実行され、親では実行されない**。「`workerinput` を持たないプロセスでのみ実行」する分岐は 0 回しか通らない。直列実行では 1 回通る | §5.D-1。rev1 の設計は**ビルドが一度も走らない**。直列では動くため、書いた本人が気づきにくい |
| F14 | herdr 0.8.0 は 0.7.5 に無いグローバル `--skill` を持つ。`is_interactive()` に通すと `--remote` 不在で skip ループに入らず、`case $1` のどの分岐にも該当せず `return 1` となり pass-through される | 分類器の default-deny が新規グローバルに対して機能している。**中核ロジックの変更は不要** |
| F15 | herdr 0.8.0 で `attach` を持つサブコマンドは `session` / `agent` / `terminal` の 3 つのみ | 分類器の網羅は 0.8.0 に対しても正確。**中核ロジックの変更は不要** |
| F16 | `bin/hsl-internal:22` のコメントは分類規則を "Measured against herdr 0.7.5" と記す。この照合を再実行する仕組みは存在しない | §5.E-3 |
| F17 | `SKILL.md:93-103` は設定検証手順として `herdr plugin list --json` の parse と `$plugin_root/target/release/hsl-config load` の直接実行を指示している | §5.E-2 |
| F18 | README に `mouse` を含む行は 0 件 | §5.E-1 |
| F19 | `.gitignore` は `/target`・`__pycache__/`・`*.pyc` の 3 行のみ。`.claude` は `git check-ignore` に一致しない | §5.F |
| F20 | `Cargo.toml` に `rust-version` の宣言が無い。`Cargo.lock` の `[[package]]` は 34（root 込み。依存は 33） | §5.B-4 |
| F21 | ローカル `master` と `origin/master` は同一コミット `16bd1b7`（0 ahead / 0 behind） | §5.B-3 の遡及タグ付けが安全に行える |
| F22 | `herdr-plugin.toml` と `Cargo.toml` を変更したコミットは `0622df4`（Initial commit、`0.1.0`）と `eb38ecb`（`0.1.2`）の 2 つのみ。**`0.1.1` はどちらの履歴にも存在しない** | §5.B-3。打つタグは 2 本であって 3 本ではない |
| F23 | `eb38ecb` の `Cargo.lock` は `hsl-config` を `0.1.0` と記録しており、`cargo build --release --locked` が `error: cannot update the lock file ... because --locked was passed` で**失敗する**。`16bd1b7` では成功する | §5.B-3。`scripts/build.sh` は `--locked` を使うため、`eb38ecb` にタグを打つと**インストール不能な ref** を公開することになる |
| F24 | `0622df4` と `16de6df`（`eb38ecb` の親、`0.1.0` を宣言する最後のコミット）は、いずれも `cargo build --release --locked` に成功する | §5.B-3。`v0.1.0` はどちらにも打てる。どちらを選ぶかは F27 が決める |
| F27 | `git diff --shortstat 0622df4 16de6df` は 19 files / +3743 / -206（mouse support を含む）。`git diff --shortstat 16de6df 16bd1b7` は 3 files / +3 / -3（version と lock のみ） | §5.B-3。`v0.1.0` を `16de6df` に打つと機能のほぼ全部が 0.1.0 に属し、0.1.2 が 3 行の bump だけになる。リリースの記述として成立しないため `0622df4` を採る |
| F28 | `tests/test_hsl_internal.py` は既に `INTERACTIVE` / `DIRECT` の argv 行列を持ち、`bin/hsl-internal` を**スクリプト全体として実行**して fake runtime と fake herdr のどちらへ到達したかを検査している。現行の行列に `--skill` と `--default-config` は含まれていない | §5.E-3。層 1 は新機構ではなく**既存行列の拡張**である |
| F29 | `herdr --help` の出力に文字列 `plugin` は **1 度も現れない**が、`herdr plugin --help` は実在し `install` / `list` / `config-dir` 等を持つ。`bin/hsl-internal` はこの `plugin` サブコマンドに依存している | §5.E-3。**root help は全 top-level subcommand を列挙していない。**したがって help 解析から「`attach` を持つのはこの 3 つだけ」を証明することはできない |
| F25 | rustc 1.85.0 / 1.88.0 / 1.95.0 / 1.96.0 / 1.97.1 のすべてで `cargo build --release --locked` と `cargo test` が成功する。1.85.0 より古い toolchain は当環境に未導入で、**未検証** | §5.B-4。1.85 は「通ることを確認した最古」であって「最小」ではない |
| F26 | `herdr --help` の出力は `Config: /home/iida/.config/herdr/config.toml` と `Logs: ...` の 2 行に**絶対パスを含む** | §5.E-3。raw 出力の完全一致 fixture は別ユーザー環境で必ず失敗する |

---

## 4. 設計方針

1. **中核ロジックの振る舞いを変えない。** F14・F15 の通り分類器も許可リストも herdr 0.8.0
   に対して現に正しい。§2 の非対象定義に従う。
2. **黙る失敗を作らない。** §5.C-3 で skip の黙認を禁じる以上、本仕様が新設する検証も同じ
   規律に従う。§5.E-3 がこの制約と衝突するため、そこは「常時走る契約テスト」と
   「明示選択時に前提不足なら *fail* する live テスト」に分ける。
3. **既存のテスト規律を守る。** 本番の配線そのものを起動する（`tests/mouse_pty.py` 冒頭）。
4. **実測と推論を混ぜない。** F 番号は実測にのみ与える。推論は本文で「未測定」と明記する。

---

## 5. ワークストリーム別設計

### 5.A 出荷スクリプトの git lock 回避

**変更**: `scripts/default-herdr-info.sh` の `set -u` 直後に `export GIT_OPTIONAL_LOCKS=0`
を追加する。

**`GIT_OPTIONAL_LOCKS=0` の意味**: git 2.15 以降、この変数は git に対し
**lock の取得を要する任意の副作用を最初から行わない**よう指示する。「lock を取れなければ
諦める」というフォールバックではない（rev1 の記述は誤りで、codex 指摘 10 により訂正）。
`git status` は refresh 結果を index へ書き戻さなくなり、報告内容は変わらない。stat 情報の
再利用が効かないぶん大規模リポジトリでは僅かに遅くなりうる。

**先頭一括 export を採る理由**: F2 の通り現時点で実害があるのは `status` の 1 箇所のみ。
それでも一括にするのは、本スクリプトが `config.toml` のコメントでユーザーへ改造を勧める
**テンプレート**であり、将来追記される git 呼び出しを自動的に保護するためである。

**テスト設計**（AC-A1-2）:

- PATH 先頭に `git` shim を置く。shim は `GIT_OPTIONAL_LOCKS` の値をログへ追記した後、
  **事前解決した実 git の絶対パス**へ `exec` する（PATH 先頭に自分がいるため、`git` の
  名前で再帰しない）。
- テスト起動時の環境に**明示的に `GIT_OPTIONAL_LOCKS=1` を入れる**。開発者の ambient 環境が
  既に `0` を持つ場合でも red test が確実に赤くなるようにするため（codex 指摘 10）。
- 表明は 2 点: 記録行が 1 行以上あること、記録された全行が `0` であること。前者が無いと
  git が一度も呼ばれない経路で偽陽性の合格になる。

---

### 5.B 配布物の体裁

#### 5.B-1 LICENSE

`LICENSE` を追加。MIT 全文、著作権表記は `Copyright (c) 2026 IIAD Yusuke`。

> **前提**: 著作権者名は git のコミット identity を採用した。GitHub owner は `iiii1224`。
> F4 の通り `Cargo.toml` に `authors` が無く、リポジトリ内に確定的な典拠が存在しない。
> 別表記を望む場合はこの 1 行の差し替えで足りる。

#### 5.B-2 CHANGELOG

`CHANGELOG.md` を Keep a Changelog 形式で追加。`## [Unreleased]` に続き **`0.1.2` と
`0.1.0` の 2 版**を記載する。F22 の通り `0.1.1` は存在しないため、**書かない**。

版と履歴の対応は §5.B-3 のタグ位置と一致させる。

| 版 | 対応する範囲 | 内容 |
| --- | --- | --- |
| `0.1.0` | `0622df4` | 初回公開。tmux ラッパー、`config.toml`、launcher、purge |
| `0.1.2` | `0622df4..16bd1b7` | mouse クリック基盤（`mouse_clicks`、`on-click.sh` 契約）ほか F27 の 3743 行 |

遡及分は網羅的な列挙ではなく、利用者から見て何が変わったかの要約に留める。

#### 5.B-3 タグ

F22・F23・F24・F27 より、打つタグは 2 本。

| タグ | コミット | 根拠 |
| --- | --- | --- |
| `v0.1.0` | `0622df4` | 歴史的なリリース点。`0.1.0` を宣言して公開された最初の状態。`--locked` ビルド成功（F24） |
| `v0.1.2` | `16bd1b7` | `--locked` ビルドが成功する最初の `0.1.2` コミット（F23）。master HEAD |

**`v0.1.0` を `16de6df`（`0.1.0` を宣言する最後のコミット）に打ってはならない。** F27 の通り
`0622df4..16de6df` には mouse support を含む 3743 行の追加があり、`16de6df..16bd1b7` は
version と lock の 3 行だけである。`16de6df` を選ぶと機能のほぼ全部が `0.1.0` に属し、
`0.1.2` が 3 行の bump だけになる。これは §5.B-2 の CHANGELOG 再構成と食い違う。

**タグは公開後に動かせない。** 実装前にこの表で確定させ、AC-B3-1 で期待 OID を固定する。

**`v0.1.2` を `eb38ecb`（bump コミット）に打ってはならない。** F23 の通り、そこでは
`cargo build --release --locked` が失敗し、`scripts/build.sh` がそれを実行するため、
`--ref v0.1.2` でインストールしたユーザーのビルドが落ちる。

**タグ付与の不変条件**（AC-B3-2）: 任意のタグ `vX.Y.Z` について、
`herdr-plugin.toml` / `Cargo.toml` / `Cargo.lock` の 3 者が `X.Y.Z` で一致し、かつ
その ref で `cargo build --release --locked` が成功すること。

**CI での検証には `actions/checkout` の `fetch-depth: 0` が必要**（codex 指摘 1）。既定は
1 コミットのみでタグを取得しないため、タグ検証ジョブは何も見えないまま緑になる。

#### 5.B-4 MSRV

`Cargo.toml` に `rust-version = "1.85"` を追加する。

**「最小」ではなく「サポートする最小 stable」と定義する**（codex 指摘 9）。F25 の通り
1.85.0 は通ることを確認した最古の toolchain だが、1.85.0 未満は当環境に未導入で未検証で
あり、数学的な最小値である証明はない。値を下げたい場合は、より古い toolchain を導入して
「採用版が通り、その直前 stable が落ちる」証拠を取得したうえで改訂する。

- CI に `rust-version` の値へ pin したジョブを追加し、`cargo build --release --locked` と
  `cargo test --locked` を実行する。**`cargo test` にも `--locked` を付ける**（依存条件を
  build と一致させるため。codex 指摘 9）。
- 宣言があれば cargo 自身が古い toolchain に明快なエラーを出すため、`scripts/build.sh` に
  追加のバージョン判定は書かない。

#### 5.B-5 PATH 警告

`scripts/build.sh` 末尾、`install-launcher.sh` の成功後に `$bindir` が `PATH` の要素かを
`case ":$PATH:" in *":$bindir:"*)` で検査し、無ければ stderr へ警告する。ビルドは
失敗させない（PATH はユーザーのシェル設定の問題であり、インストール自体は成功している）。

---

### 5.C CI の検出力

#### 5.C-1 shellcheck の導入

**方針転換（codex 指摘 5）**: rev1 は F7 の 4 種すべてを per-site 抑制する方針だった。
しかし SC1007・SC1083・SC1090 は**等価な書き換えで警告そのものを消せる**。抑制は
解析精度を捨てる行為であり、書き換えで済むなら書き換える。

| 診断 | 件数 | 対処 |
| --- | --- | --- |
| SC1007 | 8 | `CDPATH= cd` → `CDPATH='' cd`（6 件）、`pane= cwd=` / `branch= state=` → 各変数を個別行で初期化（2 件）。**抑制しない** |
| SC1083 | 1 | `HEAD...@{upstream}` を `"HEAD...@{upstream}"` と quote する。**抑制しない** |
| SC1090 | 2 | `# shellcheck source=scripts/lib/shell-quote.sh` 指示を置き、解析を通す。**抑制しない** |
| SC2329 | 3 | trap 経由でのみ呼ばれる関数。回避不能な false positive のため **per-site 抑制**（理由コメント付き） |

per-site 抑制という方針自体は維持するが、**適用対象は回避不能な false positive に限る**。

書き換えは `bin/hsl-internal` と `scripts/run-in-tmux` にも及ぶ。§2 の通りこれは
「振る舞いを変えない機械的置換」として許容し、AC-C1-3 で同一性を確認する。

CI の対象ファイル集合は既存 `sh -n` ステップと**同一**とし、両者が食い違わないようにする。
`sh -n` ステップは残す。

#### 5.C-2 ワークフローの整備

F8 に対して:

- `permissions: contents: read` をワークフロー既定として宣言。
- cargo キャッシュを導入（`Swatinem/rust-cache`）。
- `on: push` を既定ブランチ限定にし、PR ブランチでの二重実行を解消。
- `concurrency` グループを設定。
- `actions/setup-python` を追加し、`python -m pip install -r requirements-dev.txt` を
  明示する（§5.D-1 の前提。codex 指摘 2）。
- タグ検証ジョブには `fetch-depth: 0`（§5.B-3）。

#### 5.C-3 黙った skip の禁止

F9 に対して**二層**で対処する。rev1 は前提検査のみで、仕様自身が「skip されなかったことは
保証しない」と認めていた（codex 指摘 7）。

1. **前提検査**: テスト実行前に tmux（3.4 以上）と `script` の存在を検査し、欠ければ CI を
   落とす。
2. **skip の直接禁止**: `conftest.py` に、CI 実行時に限り「skip されたテストが 1 件でも
   あればセッションの終了ステータスを失敗にする」フックを追加する。CI かどうかは環境変数
   （`CI`）で判定する。

opt-in のテスト（§5.E-3 の live 比較）は **skip ではなく deselect** で既定集合から外す。
deselect は「実行対象に選ばれていない」であり、「選ばれたが前提不足で飛ばした」とは
区別される。明示選択された live テストが前提不足に遭遇した場合は **fail** させる。

---

### 5.D テスト基盤

#### 5.D-1 pytest + pytest-xdist への移行

**方針**: 既存の `unittest.TestCase` 派生クラスは pytest がそのまま収集・実行するため、
**テスト本体は書き換えない**。追加するのは実行機構のみ。

- `requirements-dev.txt` に `pytest` と `pytest-xdist` を固定バージョンで記載。
- `pyproject.toml` に `[tool.pytest.ini_options]`（`testpaths = ["tests"]` ほか）。
  `tests/__init__.py` と `from tests.helpers import ...` の既存構成は維持する。
- CI の実行を `python3 -m pytest -n auto --dist loadscope` に置き換える。`loadscope` は
  F11 がクラス単位分割で成立していること、および各クラスの `setUp` が重いことによる。

**ヘルパーのビルド（rev1 から全面変更）**

rev1 は「session スコープ autouse fixture の中で `workerinput` の有無を見て親だけビルド」
という設計だった。**F13 の実測によりこれは動かない。** xdist の controller はテストを
収集・実行せず session fixture も評価しない。評価するのは各 worker であり、全プロセスが
worker 側の分岐へ入るため、clean checkout では誰もビルドしない。

あわせて rev1 が F13 として掲げていた「N worker が実ビルドで起動時に直列化し並列化利益を
食い潰す」という主張を**撤回する**。これは未実測の推論だった。実際には
`ensure_helper()` を呼ぶのは helper を使うクラスが配られた worker だけであり、CI では
事前ビルド済みのため各 `cargo build` は通常 freshness check に留まる。

**採用する設計**（最も単純な形）:

- ビルドは **pytest の外**で行う。CI は `cargo build --release --locked` の既存ステップを
  pytest 実行の前提とする。
- `helpers.ensure_helper()` は「存在と実行可能性を検証し、無ければ何を実行すべきかを示す
  エラーで落ちる」関数へ縮退させる。モジュールグローバル `_HELPER_BUILT` は削除する。
- ローカル開発向けに、ビルドとテストを続けて実行する入口（`make test` 相当）を用意し、
  README または `CONTRIBUTING` に記載する。
- pytest 単独で自動ビルドする要件が将来生じた場合に限り、xdist 公式が案内する
  **worker 間ファイルロック**方式を採る。本仕様では採らない。

#### 5.D-2 固定待機の除去

F12 に対して `HslPty.__enter__` の `_drain(3.0)` を置換する。

**rev1 の設計は誤り**（codex 指摘 4）。`INNER_APP` は `open(log,"w").close()` を
`tty.setraw(0)` と mouse tracking 出力の**前**に実行する。したがってログファイルの出現を
readiness signal にすると、raw mode と tracking がまだ有効でない時点でクリック送出が
始まりうる。固定 3 秒を消した代償に flake を導入することになる。

**`flush()` の後に marker を置くだけでは足りない。** `sys.stdout.flush()` が保証するのは
tracking sequence が Python から PTY の kernel buffer へ書かれたことまでで、**tmux server が
そのバイト列を読んで `mouse_any_flag` / `mouse_all_flag` / `mouse_sgr_flag` を更新したことは
保証しない**。marker を見た親が直ちにクリックを送ると、tmux が tracking sequence より先に
client input を処理する余地が残る（codex 指摘 2）。同じ種類のレースが一段深いところに残る。

**採用する設計**:

- `INNER_APP` は tracking sequence を `flush()` した後、**tmux に自身の mouse flag を
  問い合わせる**（`tmux display-message -p` で `#{mouse_any_flag}#{mouse_all_flag}#{mouse_sgr_flag}`）。
  要求した mode が server 側に反映されるまで deadline 付きで poll し、**反映を確認してから**
  ready marker を作成する。marker のパスは既存のログとは別に渡す。
- `HslPty` は marker の出現を deadline 付きで待つ。marker の存在は「tmux が tracking を
  認識済み」を意味するため、直後のクリック送出が安全になる。
- timeout 時は、PTY バッファの内容と子プロセスの状態をエラーメッセージに含めて失敗させる
  （黙って進まない）。deadline は現行の 3.0s より十分大きく取る。3.0s は「十分待つ」ための
  値であって上限ではないため、上限としては短すぎる可能性がある。
- **`_send` の settle は据え置く。** `test_clicking_outside_every_range_does_nothing` の
  ように「反応が*無い*こと」を確認するテストは、本質的に時間で待つ以外の方法がない。

削減効果の大半は起動時の 3 秒側にある。この点は誇張しない。

---

### 5.E ドキュメントと契約

#### 5.E-1 README への mouse_clicks 節

F18 に対して README に節を追加する。内容: opt-in であることと代償（外側端末のネイティブ
選択とミドルクリック貼り付けを失う）、tmux 3.4 以上、`on-click.sh` が本プラグインの
提供物では**ない**こと、フックの 4 引数契約、詳細の所在。

`scripts/default-config.toml` のコメントと重複するが、README は「導入前の利用者」、
config コメントは「導入後の編集者」を読者とするため、要約と詳細として共存させる。

#### 5.E-2 `--hsl-check-config`

**形式**: `hsl --hsl-check-config [<config.toml のパス>]`

**接頭辞の根拠（rev1 から表現を弱める。codex 指摘 8）**: herdr 0.8.0 のグローバルは
`--session` / `--remote` / `--no-session` / `--handoff` / `--remote-keybindings` /
`--default-config` / `--skill` / `--version` / `-V` / `--help` / `-h`。`--hsl-` 接頭辞は
これらと交わらず、herdr が同じ接頭辞を採る動機も薄い。ただし
**「何を追加しても衝突しない」とは言えない**——それは herdr 側の将来の命名に対する保証で
あり、本リポジトリが与えられるものではない。正しくは「衝突可能性を大きく下げる予約接頭辞」
である。サブコマンド形式（`hsl doctor` 等）より衝突可能性が低いことが採用理由。

**CLI 契約**（rev1 は未定義。AC-E2-1〜5 で検証）:

| 項目 | 仕様 |
| --- | --- |
| 引数 0 個 | `herdr plugin config-dir herdr-statusline` を解決し、その `config.toml` を検査する |
| 引数 1 個 | そのパスを検査する。herdr の呼び出しを要さない |
| 引数 2 個以上 | usage を stderr へ出し、exit 2 |
| 成功時 | stdout へ何も出さない。exit 0 |
| 検査失敗時 | `hsl-config` のエラーを stderr へ通し、exit 2 |
| config 不在時 | 「見つからない」旨を stderr へ出し、exit 2（作成はしない） |
| 実装位置 | `scripts/launcher-body.sh` の `uninstall` と同じ位置。`root_is_complete` による再解決の**後** |
| stale root の rewrite | 行わない（`mode = run` のときのみ rewrite する既存挙動を変えない） |

**SKILL.md の更新**: F17 の 3 ステップ手順をこの 1 行へ差し替える。

#### 5.E-3 herdr CLI 契約テスト

**rev1 の設計は問題を解決していない**（codex 指摘 3）。固定 fixture は「コードが既知の
0.8.0 出力と整合すること」しか検査せず、fixture 自体が古くなったことは検出できない。
live 比較を既定集合から外せば、上流の drift は永遠に CI を失敗させない。さらに F26 の通り
`herdr --help` は絶対パスを含むため、raw 出力の完全一致は別ユーザー環境で必ず失敗する。
「選んだ help だけから attach は 3 種類だけと証明する」のも循環的である。

**採用する設計 — 3 層。各層が何を検出し、何を検出しないかを明示する**（codex 指摘 3）。

| 層 | 名称 | 検出するもの | **検出しないもの** | 実行 |
| --- | --- | --- | --- | --- |
| 1 | ローカル分類器の契約回帰 | 分類器または期待行列が意図せず変わったこと | **上流 herdr の変化は一切観測しない** | CI 常時 |
| 2 | 0.8.0 compatibility snapshot | コードが記録済み 0.8.0 CLI と整合していること | snapshot 自体が古くなったこと。**`attach` を持つ集合の網羅性**（F29） | CI 常時 |
| 3 | live 比較 | 実 herdr と snapshot の乖離 | — | opt-in のみ |

1. **層 1 — ローカル分類器の契約回帰（herdr 不要）**: F28 の通り
   `tests/test_hsl_internal.py` には既に `INTERACTIVE` / `DIRECT` の argv 行列があり、
   `bin/hsl-internal` を**スクリプト全体として**実行して到達先を検査している。層 1 は
   この**既存行列の拡張**であり、新機構ではない。追加するのは現行行列に無い
   `--skill` と `--default-config`（いずれも `DIRECT` 側）。
   **`is_interactive()` を抽出・source してはならない。** `bin/hsl-internal` は
   source 用ライブラリではなく関数定義の後に main path を実行するため、関数だけを
   取り出す実装は脆く、§4-3 の「本番の配線そのものを起動する」にも反する。
   この層は **drift に対する防御ではない**（rev2 の記述を訂正）。
2. **層 2 — 0.8.0 compatibility snapshot（herdr 不要）**: `herdr --help` に加え、
   root help から発見できる全 top-level subcommand と、そこに現れない既知のもの
   （F29 の `plugin`）の `--help` 出力を fixture としてコミットする。**root help 単体からは
   `agent attach` と `terminal attach` を抽出できない**ため、サブコマンドの help が必要である。
   絶対パス（F26）・末尾空白・折返しを正規化したうえで記録する。

   表明は 2 種類に分け、**強さを取り違えない**。

   - **グローバルオプション集合**: 厳密一致。root help の `Options:` 節は完結しているため。
   - **`attach` を持つサブコマンド**: `session` / `agent` / `terminal` の 3 つが `attach` を
     **持つこと**（正例）と、fixture に収めた他のどのサブコマンドも `attach` を
     **持たないこと**（best-effort な負例）。**これは網羅性の証明ではない。**

   **網羅性を主張しない理由**（codex 2 巡目指摘への反論）: 「root command graph から全
   top-level subcommand を列挙して厳密集合を検査せよ」という案は、root help が全
   subcommand を列挙しているという前提に立つ。F29 の通りこれは**偽**である——`herdr --help`
   に `plugin` は現れないが、`herdr plugin` は実在し、しかも `bin/hsl-internal` が最も
   依存しているサブコマンドである。この前提で「厳密一致」を書くと、`plugin` を欠いた集合に
   対して合格し、**網羅性について偽の確信**を与える。help 解析で到達できないものを
   到達したふりをするより、到達できないと書くほうが正しい。

   名称は "compatibility snapshot" であり "drift detection" とは呼ばない。
3. **層 3 — live 比較（opt-in）**: 実 `herdr` の出力を同じ正規化にかけ、snapshot と
   比較する。既定集合からは **deselect** する（skip ではない）。明示選択されたうえで
   herdr が不在なら **fail** させる（§5.C-3 の規律）。

> 未解決として §8-6 に記す: **上流 herdr の振る舞い変化を CI は自動検知しない。** 層 1 は
> ローカル契約の回帰しか見ず、層 2 は snapshot の陳腐化を見ない。自動検知には scheduled
> または release ワークフローで herdr を取得し層 3 を必須実行する必要があり、本仕様では
> 対象外とする。この限界を承知のうえで受け入れる。

---

### 5.F リポジトリ衛生

F19 に対して `.gitignore` に `/.claude/worktrees/` を追加する。

`EnterWorktree` はリポジトリ内の `.claude/worktrees/<name>` に作業ツリーを作成する。
`.claude/` が無視対象でないため、親チェックアウトでこのディレクトリが未追跡として現れる。

**`.claude/` 全体ではなく `worktrees/` のみに限定する。** `.claude/settings.json` など将来
リポジトリで共有したくなる設定を追跡可能なまま残すため。既存の `/target` と同じく直下限定
を意図して `/` を前置する。

---

## 6. 依存順序

```
F (.gitignore)  ──┐
A (git lock)  ────┼──▶ 独立、順不同
B-1 LICENSE   ────┤
B-2 CHANGELOG ────┤
E-1 README    ────┘

C-2 (workflow 整備) ──▶ C-1 (shellcheck) ──▶ C-3 (skip 禁止) ──▶ D-1 (pytest)
                    └──▶ B-3 (タグ + fetch-depth) ──▶ B-4 (MSRV pin ジョブ)

D-2 (ready marker) ──▶ D-1 と独立（tests/mouse_pty.py のみ）。効果測定は D-1 の後
E-2 (--hsl-check-config) ──▶ 独立
E-3 (契約テスト) ──▶ C-3 の後（deselect / fail 方針が前提）
```

`.github/workflows/ci.yml` を編集するのは C-1 / C-2 / C-3 / D-1 / B-3 / B-4 の 6 件。
並行実装すると衝突するため、**1 本の系列**として扱う。C-2 を先頭に置くのは、以降の追加が
整備済みのワークフロー上に乗り手戻りが減るため。

---

## 7. 受入基準

各項目に AC ID を与える。すべて「コマンド / 期待される結果」で客観判定できる形にした
（codex 指摘 6）。

### 全体

| AC | 内容 |
| --- | --- |
| AC-G-1 | `cargo fmt --check` が exit 0 |
| AC-G-2 | `cargo clippy --all-targets --all-features -- -D warnings` が exit 0 |
| AC-G-3 | `cargo test --locked` が exit 0、失敗 0 件 |
| AC-G-4 | `python3 -m pytest -n auto --dist loadscope` が exit 0、132 件以上が pass、skip 0 件 |
| AC-G-5 | 並列実行の所要時間の**中央値（5 回測定、同一マシン、他の負荷なし）が、直列実行 `python3 -m pytest -p no:xdist` の中央値の 75% 以下** |

### ワークストリーム別

| AC | 内容 |
| --- | --- |
| AC-A1-1 | `scripts/default-herdr-info.sh` が `GIT_OPTIONAL_LOCKS=0` を export している |
| AC-A1-2 | 記録 shim テストが pass。ambient に `GIT_OPTIONAL_LOCKS=1` を置いた状態で、記録行が 1 行以上あり全行が `0` |
| AC-A1-3 | 同テストを `export` 行を除いたスクリプトに対して実行すると**失敗する**（red 確認） |
| AC-B1-1 | `LICENSE` が存在し、1 行目に `MIT License`、著作権行を含む |
| AC-B1-2 | `Cargo.toml` の `license` が `MIT` であり、README のバッジ URL が MIT を指す |
| AC-B2-1 | `CHANGELOG.md` が存在し、`[Unreleased]` / `0.1.2` / `0.1.0` の 3 見出しを持ち、`0.1.1` を**含まない** |
| AC-B3-1 | `git tag` が `v0.1.0` と `v0.1.2` の 2 本のみを出力し、`git rev-parse v0.1.0^{commit}` が **`0622df4`**、`git rev-parse v0.1.2^{commit}` が **`16bd1b7`** に解決する（期待 OID を固定する。版一致だけでは `16de6df` も通ってしまうため） |
| AC-B3-2 | 各タグの ref で `herdr-plugin.toml` / `Cargo.toml` / `Cargo.lock` の版が一致し、`cargo build --release --locked` が exit 0 |
| AC-B3-3 | タグ検証を行う CI ジョブの `actions/checkout` に `fetch-depth: 0` が指定されている |
| AC-B4-1 | `Cargo.toml` に `rust-version = "1.85"` がある |
| AC-B4-2 | 1.85 に pin した CI ジョブで `cargo build --release --locked` と `cargo test --locked` が exit 0 |
| AC-B5-1 | `$bindir` が `PATH` に無い状態で `build.sh` を実行すると stderr に警告が出て、**exit 0** のまま |
| AC-B5-2 | `$bindir` が `PATH` にある状態では警告が出ない |
| AC-C1-1 | CI の shellcheck ステップが exit 0 |
| AC-C1-2 | **CI が shellcheck にかける 7 つのシェルファイルに限って**、抑制指示が SC2329 のものだけであること。検査範囲をこの 7 ファイルに限定する（「リポジトリ内に存在しない」とすると本仕様書自身がこの AC の説明文に当該文字列を含むため、素直な検索が必ず失敗する） |
| AC-C1-3 | 書き換え後、`bin/hsl-internal` と `scripts/run-in-tmux` に対する既存 Python テスト（`test_hsl_internal.py` / `test_tmux_runtime.py` の全件）が pass。これをもって振る舞い同一とみなす |
| AC-C1-4 | shellcheck の対象ファイル集合が `sh -n` ステップの集合と文字列一致 |
| AC-C2-1 | ワークフローに `permissions: contents: read` がある |
| AC-C2-2 | PR ブランチへの push で起動するワークフロー実行が 1 件（二重実行しない） |
| AC-C2-3 | cargo キャッシュと `actions/setup-python` + `pip install -r requirements-dev.txt` がある |
| AC-C3-1 | tmux を PATH から外した状態で CI 相当のステップを実行すると**非ゼロ終了**する |
| AC-C3-2 | 任意のテストに `@unittest.skip` を一時的に付けて `CI=1` で実行すると、セッションが**失敗**する |
| AC-C3-3 | live-herdr テストは既定実行で **deselect** され、skip として計上されない |
| AC-D1-1 | `helpers.py` に `_HELPER_BUILT` が存在せず、`ensure_helper()` が `cargo` を呼ばない |
| AC-D1-2 | helper 未ビルドの状態で pytest を実行すると、何を実行すべきかを示すエラーで落ちる |
| AC-D1-3 | `requirements-dev.txt` と `pyproject.toml` の `[tool.pytest.ini_options]` が存在する |
| AC-D1-4 | ローカル用の統一入口（`make test` 相当）が存在し、release build を実行してから pytest を実行する。README または `CONTRIBUTING` にその入口が記載されている |
| AC-D2-1 | ready marker の出現時点で、tmux 側の `mouse_any_flag` / `mouse_all_flag` / `mouse_sgr_flag` が要求した mode を反映している。**ソース上の記述順の検査では代用しない**（順序は必要条件だが十分条件ではない） |
| AC-D2-2 | `HslPty.__enter__` に `_drain(3.0)` 相当の無条件固定待機が無い |
| AC-D2-3 | marker が現れない状況で `__enter__` が timeout し、PTY バッファ内容を含むエラーで失敗する |
| AC-D2-4 | `test_tmux_mouse.py` 全 12 件が 5 回連続で pass（flake が入っていないことの確認） |
| AC-E1-1 | README に `mouse_clicks` の節があり、tmux 3.4 要件・代償・`on-click.sh` が非提供物であることの 3 点を含む |
| AC-E2-1 | `hsl --hsl-check-config` が正常な config に対し exit 0、stdout 空 |
| AC-E2-2 | 壊れた config に対し exit 2、stderr にエラー |
| AC-E2-3 | 明示パス指定時、herdr を PATH から外しても動作する |
| AC-E2-4 | 引数 2 個以上で usage を出し exit 2 |
| AC-E2-5 | config 不在時 exit 2 で、ファイルを作成しない |
| AC-E2-6 | `SKILL.md` に `plugin_root` を parse させる記述が残っていない |
| AC-E3-1 | 層 1: 拡張後の `test_hsl_internal.py` の argv 行列に `--skill` と `--default-config` が `DIRECT` として含まれ、herdr 不在環境で pass する（skip しない）。`is_interactive()` を抽出・source する実装が存在しない |
| AC-E3-2 | 層 2: snapshot fixture が `herdr --help` に加え、**root help から発見できる全 top-level subcommand と既知の例外 `plugin`（F29）** の各 `--help` を含み、正規化済みで絶対パスを含まない。これにより AC-E3-4 の負例検査が空集合に対して vacuous に合格する抜け道を塞ぐ |
| AC-E3-3 | 層 2: fixture から抽出したグローバルオプション集合が期待集合と**厳密一致**する（`--session` `--remote` `--no-session` `--handoff` `--remote-keybindings` `--default-config` `--skill` `--version` `-V` `--help` `-h`） |
| AC-E3-4 | 層 2: fixture 上で `session` / `agent` / `terminal` が `attach` を**持つ**こと、かつ fixture に収めた他のどのサブコマンドも `attach` を**持たない**こと。**「全 herdr サブコマンドのうち attach を持つのはこの 3 つだけ」という網羅性は主張しない**（F29 により help 解析では到達不能） |
| AC-E3-5 | 層 3: herdr 0.8.0 が存在する環境で live 比較を明示選択すると **pass** する |
| AC-E3-6 | 層 3: fixture に意図的な差分を注入すると live 比較が **fail** する（red 確認。無条件 fail する偽実装との区別） |
| AC-E3-7 | 層 3: 明示選択したうえで herdr が不在なら **fail** する（skip しない） |
| AC-E3-8 | 層 3 が既定実行から **deselect** され、skip として計上されない（AC-G-4 の skip 0 件と両立する） |
| AC-F-1 | `git check-ignore .claude/worktrees` が一致し、`git check-ignore .claude/settings.json` が一致しない |

---

## 8. 判断の記録と未解決点

1. **バージョン bump 自動化は非対象。** 同期漏れは実際に発生している（`16bd1b7`）が、
   利用者判断により「タグ + CHANGELOG のみ」を採用。`test_consistency.py` が検査を担い
   続けるため漏れは CI で検知される（bump 時点ではない）。
2. **MSRV は「最小」ではなく「サポートする最小 stable」。** F25 の通り 1.85 未満は未検証。
   下げるには古い toolchain の導入と「直前 stable が落ちる」証拠が必要（§5.B-4）。
3. **`_send` の settle を残した。** §5.D-2 の通り「反応が無いこと」を待つテストがあるため。
   将来 tmux 側のイベントカウンタ等で否定的表明を置き換えられれば削減余地がある。
4. **`status_interval = 1` 既定値は触らない。** §5.A は lock 競合のみを解く。毎秒
   `git status` を実行する構成の是非は残る課題であり、注意喚起に留めるか別件で扱う。
5. **AC-G-5 の 75% という閾値は目標値であって実測の裏付けはない。** F11（4 クラス並列で
   77.5s → 47.5s、61%）から外挿した値。5.D-2 適用後の全体値は未測定。実装時に達成できない
   場合は、閾値ではなく実測値を記録して再判断する。
6. **上流 herdr の振る舞い変化を CI は自動検知しない。** §5.E-3 の 3 層のうち、層 1 は
   ローカル分類器の契約回帰しか観測せず（上流の変化は一切見ない）、層 2 は snapshot 自体の
   陳腐化を見ない。自動検知には scheduled / release ワークフローで herdr を取得して層 3 を
   必須実行する必要があり、本仕様の対象外とした。**したがって herdr が新しい `attach` 形式や
   グローバルオプションを追加しても、誰かが層 3 を手動で実行するまで CI は緑のままである。**
   この限界を承知のうえで受け入れる。rev2 は層 1 を「drift に対する主たる防御」と書いていたが
   これは誤りであり、rev3 で撤回した。

7. **`attach` を持つサブコマンドの網羅性は、どの層でも証明されない。** F29 の通り
   `herdr --help` は `plugin` を列挙しないため、help 解析から全 top-level subcommand を
   得ることができず、「`attach` を持つのは 3 つだけ」は best-effort な負例検査に留まる。

   **この限界が許容できる理由**: 分類器は default-deny である（F14）。未知の `attach` 形式を
   取りこぼした場合の結果は「tmux で包まれず pass-through され、ステータスラインが出ない」
   という**機能低下**であって、コマンドの破壊ではない。逆向きの誤り——herdr が拒否する
   コマンドを包んで tmux server を無駄に起動する——のほうが害が大きく、そちらは
   §5.E-3 層 1 の `DIRECT` 行列が守っている。したがって網羅性の欠如は許容する。

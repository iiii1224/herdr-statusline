# Status line マウスクリック基盤 — 設計仕様

- 日付: 2026-08-10
- 対象リポジトリ: `herdr-statusline`
- 検証環境: tmux 3.7b / Linux WSL2 / herdr 0.8.0
- 改訂: rev2（codex によるレビュー指摘 10 件を実測で検証し、5 件の設計欠陥を修正）

---

## 1. 背景と目的

`herdr-statusline` は tmux を「ステータスライン専用のクローム」として使い、その中で herdr
本体を全画面 TUI として動かす。`tmux/base.conf` は `prefix None` / `unbind-key -a` /
`mouse off` を設定し、tmux が入力を一切奪わないことを不変条件としている。

この不変条件を**明示的に境界づけたうえで緩和し**、status line 上のクリックを外部が
所有する単一フックへ配送する基盤を追加する。個々のボタン機能は本リポジトリの対象外と
し、別リポジトリが `on-click.sh` を提供して実現する。

### 解決する課題

herdr の外側で常時可視な領域は status line だけであり、現在は完全に read-only である。
クリックを受け取れるようにすることで、外部リポジトリが status line をエージェント制御・
Git 操作・情報展開などの操作面として利用できるようになる。

---

## 2. スコープ

### 対象

- `config.toml` のトップレベル opt-in キー `mouse_clicks`
- `hsl-config load` の行プロトコルへの当該フラグの追加
- `tmux/base.conf` の root キーテーブル明示クリア
- `run-in-tmux` による条件付きの tmux 配線（`mouse on` + status クリックの binding 4 本）
- `on-click.sh` 拡張点の契約定義とドキュメント化
- 上記を検証する**コミット済み**の再現フィクスチャ

### 非対象

- 個別のボタン機能（pane id コピー、cwd コピー、lazygit 起動、エージェント起動、
  btm 起動、メディア操作など）。**別リポジトリが `on-click.sh` として実装する。**
- `#[range=user|X]` をステータスラインへ書くこと自体。これは既存の
  `status_format_N` / `status_left` / `status_right` で既に可能。
- tmux 側の当たり判定オフセット（§8）の補正。

---

## 3. 前提となる実測事実

すべて tmux 3.7b で実測した。「出典」欄が URL のものは一次情報による確認。

| # | 事実 | 設計への影響 |
| --- | --- | --- |
| F1 | `#[range=user\|X]` のクリックは `MouseDown1Status` を発火し、`#{mouse_status_range}` に `X` が入る | 任意領域をボタン化できる。ディスパッチのキーは range 名 |
| F2 | `run-shell` は文字列全体をまず tmux format 展開し、その結果を `/bin/sh -c` に渡す（**二段階展開**） | 引数搬送は tmux 側のエスケープで行う必要がある。F7 参照 |
| F3 | **`unbind-key -a` は prefix テーブルしか消さない。** base.conf 実行後も root テーブルに tmux 標準のマウス binding が 24 本残る（`list-keys -T root` が 24 行） | `mouse off` の現在は不活性。`mouse on` にした瞬間すべて有効化される。§5.3 の根拠 |
| F4 | root を `unbind-key -a -T root` で空（0 本）にしたうえで `mouse on` にすると、転送 binding が無くても pane のマウスイベントはアプリへ届く | pane 転送 binding は不要。**root が空であることが前提** |
| F5 | F4 は 1000・1003 の双方で成立し、モーション・ドラッグ・ホイールも届く。ただし **tmux は座標をペイン相対へ再エンコードする**。`status-position top` では端末行 5 のクリックがアプリに行 4 として届く | 「バイト単位で透過」は**誤り**。正しくはペイン相対への変換を伴う転送。`status-position` は allowlist を通るのでユーザーが top にできる |
| F6 | `mouse on` にすると tmux は外側端末に `1000` `1002` `1003` `1006` を要求する（pty で観測） | 端末ネイティブの選択・ミドルクリック貼り付けは binding の有無に関係なく失われる（§8） |
| F7 | **コマンドインジェクションが成立する。** 14 バイトの range 名 `a';id>/tmp/z;'` を `'#{mouse_status_range}'` としてシングルクォートで囲む配線に食わせると、クォートを脱出して `id` が実行された。tmux が range 名に課す制約は 15 バイト上限のみ | シングルクォート囲みは**使用禁止**。§5.5 の設計根拠 |
| F8 | `#{q:...}` は sh(1) 特殊文字をエスケープする（`#{qh:...}` は `#` → `##`）。`#{q:mouse_status_range}` に変えると F7 のペイロードは単一引数 `[a';id>/tmp/z;']` として渡り、インジェクションは起きない | 採用する搬送方式 |
| F9 | `run-shell` は `-b` なしだとコマンドキューをブロックし、**フックの stdout と非ゼロ終了をクライアントに描画する**（`HOOK-STDOUT-MARKER` と `returned 7` の描画を観測）。`-b` と `>/dev/null 2>&1` を足しても**非ゼロ終了の表示は残る**。`\|\| true` まで足して初めて何も描画されない | §5.5 の 3 点セットが必須 |
| F10 | 修正後の配線は、空白・`#`・`'` を含むフックパスでも `ARGC=4` で正しい 4 引数を渡す | user option + `#{q:}` で搬送する設計の実証 |
| F11 | range 外（`MouseDown1StatusDefault`）の左クリックは、当該 binding を張らない限り何も起こさない | フックに空の range は渡らない（§6） |
| F12 | 右クリック・ホイールも user range 上で range 名付きで発火する | left / right / wheel の 4 系統を配線する根拠 |
| F13 | tmux に `MouseMove` binding は存在しない | hover は実装できない（§8） |
| F14 | user range の当たり判定は表示テキストより右端が 1 カラム広い。`range=left` / `range=right` の内側にネストすると左端も 1 カラムずれる | ドキュメント化のみ（§8） |
| F15 | `range=user`・`mouse_status_range`・`mouse_status_line` は **tmux 3.4** で追加された（`CHANGES FROM 3.3a TO 3.4`: "Add a session, pane and user mouse range types for the status line and add format variables for mouse_status_line and mouse_status_range"）。汎用の `range=` は 2.9 | 最低要求バージョンは 3.4。§5.6 |

### F3 の重要性

tmux 標準の root マウス binding には、`mouse on` の下で herdr の操作を奪うものが含まれる。

- `DoubleClick1Pane` / `TripleClick1Pane` → `copy-mode` で単語・行選択
- `MouseDown3Pane` → tmux のコンテキストメニュー
- `WheelUpPane` → `copy-mode -e`
- `MouseDown1Control9` → **kill-pane 確認メニュー**
- `MouseDrag1Border` → ペインリサイズ
- `MouseDown1ScrollbarUp` / `Down` / `MouseDrag1ScrollbarSlider`

いずれも `#{mouse_any_flag}` が 0 のとき（herdr がマウスモードを要求していない瞬間）に
発火する分岐を持つ。root を空にしない限り、`mouse on` は herdr の入力を壊す。

### F4 の成立条件

「未 binding なら転送される」は単純化しすぎである。tmux 3.7b で自動転送が起きるのは、
実効キーテーブルと root テーブルの双方に一致する binding が無く、イベントが tmux の
mode・overlay・prompt に消費されず、対象ペインが可視で、ペイン側が該当するマウスモードを
有効化している場合に限る。ペインが tmux の mode に入っている場合、root へフォールバック
した後に未処理イベントを破棄する経路がある。

本プラグインの構成ではペインは常に 1 枚で herdr が占有し、tmux 側の mode・overlay は
使わないため条件は満たされる。**ただしフックが overlay（`display-popup` 等）を出した
場合、その間のイベントは overlay に消費される**。これはフック側の責務として §6 に記す。

---

## 4. アーキテクチャ

### データフロー

```
status line クリック
  │
  ├─ tmux が range を解決                  → #{mouse_status_range}
  │
  ├─ bind -n MouseDown1Status run-shell -b
  │     '#{q:@hsl_on_click} left #{q:mouse_status_range} ... >/dev/null 2>&1 || true'
  │        ↑ tmux format 展開で sh エスケープ済みの引数列が組み立てられる
  │
  └─ on-click.sh が case 分岐              （別リポジトリ所有）
        └─ tmux display-popup / herdr CLI / クリップボード など
```

pane クリックは配線しない（F4）。root が空であることは §5.3 で base.conf が保証する。

### 三層構造

| 層 | 所有者 | 内容 |
| --- | --- | --- |
| 有効化 | ユーザー | `config.toml` の `mouse_clicks = true` |
| 配線 | 本プラグイン | `run-in-tmux` が `mouse on`、user option、binding 4 本を適用 |
| 拡張点 | 別リポジトリ | `$HERDR_PLUGIN_CONFIG_DIR/on-click.sh` |

---

## 5. コンポーネント別の変更

### 5.1 `src/config.rs` — opt-in フラグ

`RawConfig` に既定 `false` のフィールドを追加する。`#[serde(deny_unknown_fields)]` が
効いているため、追加しない限り `mouse_clicks` は未知キーとして拒否される。

```rust
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RawConfig {
    #[serde(default = "default_enabled")]
    enabled: bool,
    #[serde(default)]
    mouse_clicks: bool,
    #[serde(default)]
    statusline: Statusline,
}
```

`NormalizedConfig` にも `pub mouse_clicks: bool` を追加し、`normalize` で透過させる。
非 boolean は serde が型エラーを返すため、追加のバリデーションは不要。

`option_name` の allowlist は変更しない。ただしこれが「安全論拠のすべて」ではなくなる
ことについては §7 を参照。

### 5.2 `src/config.rs` — `write_protocol` の行プロトコル

```
現行                        変更後
1: enabled                  1: enabled
2: option count             2: mouse_clicks     ← 挿入
3: name[0]                  3: option count
4: value[0]                 4: name[0]
...                         5: value[0]
```

`mouse_clicks` は `enabled` と同じく Rust の `bool` の `Display`（`true` / `false`）。

**プロトコルの消費者は 3 つある。**

| 消費者 | 読む内容 | 対応 |
| --- | --- | --- |
| `bin/hsl-internal:146` | 1 行目の `enabled` のみ。`true\|false` を検証。行数検証はしない | 変更不要。1 行目の意味を変えないことをテストで担保 |
| `scripts/run-in-tmux:215-249` | 2 行目の count と以降のペア。行数を厳密に検証 | §5.4 のとおり更新 |
| `tests/test_hsl_internal.py:217` | プロトコル文字列を**完全一致**で検証（`"true\n2\nstatus-interval\n1\n..."`） | **期待値の更新が必須。** 見落とすとテストが落ちる |

#### 版ずれの契約

producer（`hsl-config` バイナリ）と consumer（`run-in-tmux` / `hsl-internal`）は通常起動では
どちらも `$PLUGIN_ROOT` から解決されるため、整合したインストールでは版ずれしない
（`launcher-body.sh:118` の staging コピーは `uninstall --purge` 専用であり、この保証とは
無関係）。

しかし**更新途中には新旧の混在が起こりうる**。そのとき何が起きるかを契約として定める。

- 新 writer × 旧 runner: 旧 runner は 2 行目を count として読み `mouse_clicks` の文字列を
  得るため、数値検証に失敗して `exit 2`
- 旧 writer × 新 runner: 行数検証 `3 + count*2` に合わず `exit 2`

いずれも **fail-closed**（黙って誤動作せず、起動を拒否する）。バージョンネゴシエーション
は導入しない。「版ずれは起こりえない」のではなく「版ずれ時は安全に拒否し、更新完了まで
起動できないことを許容する」が本設計の立場である。両方向の混在をテストで固定する（§9）。

### 5.3 `tmux/base.conf` — root キーテーブルを空にする

`set-option -g mouse off` は残したうえで、**`unbind-key -a -T root` を 1 行追加する。**

```tmux
unbind-key -a
unbind-key -a -T root
```

理由は F3。既存の `unbind-key -a` は prefix テーブルしか消しておらず、root には標準の
マウス binding が 24 本残っている。

**条件分岐側ではなく base.conf に置く理由:**

1. base.conf のコメントが表明している「tmux はキーを一切持たない」という意図を実際に
   成立させる。現状その表明は事実に反している
2. 24 本はすべてマウス binding であり `mouse off` の下では発火しえないため、
   `mouse_clicks = false` の既存経路に挙動変化はない
3. 条件分岐側に置くと「クリア前に `mouse on` が適用される」順序ハザードが生じる

### 5.4 `scripts/run-in-tmux` — プロトコル読み出しの更新

`apply_status_options` のオフセットを 1 行ずらす。

- `count`: `sed -n '2p'` → `sed -n '3p'`
- 行数検証: `2 + count * 2` → `3 + count * 2`
- `name`: `$((3 + index * 2))` → `$((4 + index * 2))`
- `value`: `$((4 + index * 2))` → `$((5 + index * 2))`

加えて `mouse_clicks` を 2 行目から読む。

```sh
MOUSE_CLICKS=$(sed -n '2p' "$STATUS_OPTIONS") || return 2
case $MOUSE_CLICKS in
    true|false) ;;
    *)
        printf '%s\n' 'hsl: invalid hsl-config output' >&2
        return 2
        ;;
esac
```

`STATUS_OPTIONS` 未設定時は早期 return し、`MOUSE_CLICKS` は `false` として扱う。

### 5.5 `scripts/run-in-tmux` — 条件付き配線

`apply_status_options` の成功後、`wait-for -S hsl-start` の**前**に実行する。launcher は
まだ `wait-for` でブロックされているため、失敗しても herdr には波及しない。

```sh
# Status-line mouse clicks are opt-in and inert without a hook: enabling the
# mouse with nothing to dispatch to would only cost the user their terminal's
# native selection (tmux asks the outer terminal for 1000/1002/1003/1006) and
# give nothing back.
#
# Every field is carried through tmux's own `#{q:...}` sh-escaper rather than
# hand-quoting. run-shell format-expands the whole string before handing it to
# /bin/sh -c, so a range name is attacker-controlled text landing inside a
# shell command line; a 14-byte name is enough to break out of single quotes
# and execute arbitrary code. The hook path travels the same way, as a user
# option, so a `#` or `#{` in the config path cannot be re-expanded either.
#
# -b, the redirect and the `|| true` are three separate requirements: without
# -b the hook blocks the command queue, without the redirect its stdout is
# drawn over herdr, and without `|| true` a non-zero exit still is.
apply_mouse_clicks() {
    [ "${MOUSE_CLICKS:-false}" = true ] || return 0

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

呼び出し側:

```sh
if ! apply_status_options; then
    exit 2
fi
if ! apply_mouse_clicks; then
    exit 2
fi
```

決定事項:

- **`shell_quote` は使わない。** `scripts/lib/shell-quote.sh` は生成シェルスクリプトに値を
  焼き込むためのもので、tmux の二段階展開（F2）を通る文字列には不十分である。
  フックパスは tmux user option `@hsl_on_click` に置き、`#{q:...}` で取り出す。
  値は argv で `set-option` に渡すのでクォート規則が一切かからない
- **中クリック（`MouseDown2Status`）は配線しない。** ただし「端末のペーストを温存する
  ため」ではない。`mouse on` の時点で端末ネイティブのペーストは失われる（F6）。
  配線しないのは基盤として最小限に留めるためであり、失われる挙動は §8 に明記する
- **`MouseDown1StatusDefault` も配線しない**（F11）
- **pane 系の binding は追加しない**（F4）
- 失敗時は `return 2` → 呼び出し側で `exit 2`。フック不在・config dir 不明は失敗では
  なく `return 0`（警告のみ、起動継続）
- **各 command substitution の失敗を明示的に検査する。** 関数は `if !` の条件文として
  呼ばれるため `set -e` の暗黙伝播は効かない
- 警告メッセージは行継続バックスラッシュを使わず、`printf` に複数の引数を渡して組み立てる。
  シングルクォート内の `\` + 改行は行継続にならず、そのまま出力される

### 5.6 tmux 3.4 未満での挙動

`range=user` と `mouse_status_range` は tmux 3.4 で追加された（F15）。3.4 未満では
range が解決されないため**フックは決して発火しない**一方、`mouse on` だけは適用され、
端末ネイティブの選択・ペーストが失われる（F6）。得るものが無く失うものがある状態になる。

本リポジトリは `scripts/run-in-tmux:257` に「`allow-passthrough` は `tmux -V` を parse せず
設定して検出する」という作法を持つ。**この機能に限り、その作法を採れないことを実測で
確認した。**

| 試した検出手段 | 結果 |
| --- | --- |
| `#{mouse_status_range}` を `display-message -p` で展開 | 未知の書式変数は空文字列・exit 0 に展開される。既知だが空の場合と区別できない |
| `display-message -p '#[notakeyword=1]ok'` | style は解釈されず文字列のまま出力・exit 0。判別不能 |
| style 型オプションへ `range=user\|p` を設定 | exit 0。ただし **`range=bogus` も exit 0** ＝ tmux は range の**値を検証しない**。`range` キーワード自体は 2.9 からあるため、3.4 未満でも成功してしまう**偽陽性** |

したがって残る手段は `tmux -V` の parse のみである。**この 1 箇所に限って採用し、
なぜ「設定して検出する」が使えないのかを上表の要点とともにコメントへ残す。**

```sh
# Unlike allow-passthrough, user ranges cannot be detected by setting them:
# tmux validates the `range` keyword but not its value, so `range=user|p`
# succeeds on 3.3 too, where user ranges do not exist. Version parsing is the
# only discriminating test available.
```

判定規則を確定する。

1. `"$TMUX_BIN" -V` の出力から最初に現れる `MAJOR.MINOR` を取り出す
   （`tmux 3.7b` → `3` と `7`。末尾の英字は無視する）
2. `MAJOR > 3`、または `MAJOR = 3` かつ `MINOR >= 4` なら**対応**
3. `MAJOR.MINOR` を取り出せない場合は**対応とみなし、警告を出さずに続行する**。
   判別不能な形式は新しい版であることが多く、opt-in したユーザーの意図を無為に
   しないため。なお `tmux next-3.9` のような開発ビルドは規則 1 で `3` と `9` が
   取り出せるので、この fallback ではなく通常の比較で対応と判定される
4. 非対応と判定したときは `mouse on` を適用せず、必要な版を述べる警告を stderr に出して
   `return 0` する。フック不在時と同じ扱いで、起動は継続する

判定は `apply_mouse_clicks` の最初に行う。非対応なら **mouse 固有の変更を一切行わない**
——`mouse` オプション、`@hsl_on_click`、4 本の binding のいずれにも触れない。
`[statusline]` 由来の通常の status オプションはこの判定と無関係に適用される。
`mouse_clicks` は status line の見た目ではなく入力の扱いだけを制御する opt-in であり、
それが使えないことと status line を描かないことは別問題だからである。

### 5.7 `scripts/default-config.toml` — 契約の説明

`enabled` の近くに `mouse_clicks` のコメントを追加し、`on-click.sh` の契約（§6）、
`#[range=user|X]` の書き方、tmux 3.4 以上が要ること、§8 の制約を簡潔に記す。
既定値は変更せず、コメントアウトのまま置く。

### 5.8 `init.rs` — テンプレートは作らない

`on-click.sh` は別リポジトリの所有物である。`create_if_missing` は既存ファイルを
上書きしないため、プラグインが先に空テンプレートを置くと別リポジトリの本体が入らなく
なる。よって `init.rs` は変更しない。

### 5.9 `skills/customize-herdr-statusline/SKILL.md`

追記する内容:

- `mouse_clicks` の有効化方法と、`on-click.sh` が別リポジトリ所有である旨
- `#[range=user|X]` の書き方。**`X` は最大 15 バイトで、シェルのメタ文字を含めても
  安全に渡るが、可読性のため英数字と `_` に留めることを推奨**
- `range=left` / `range=right` に包まれる `status_left` / `status_right` の内側に user
  range を置くとずれる（F14）ため、`status_format_N` を自前定義する構成を推奨する旨
- hover は実装できない旨（F13）
- tmux 3.4 以上が必要な旨

---

## 6. `on-click.sh` の契約

| 項目 | 内容 |
| --- | --- |
| パス | `$HERDR_PLUGIN_CONFIG_DIR/on-click.sh`（固定）。別リポジトリまたはユーザーが所有 |
| 起動条件 | `mouse_clicks = true` かつ実行可能（`[ -x ]`）かつ tmux が user range をサポート |
| `$1` button | `left` / `right` / `wheelup` / `wheeldown` |
| `$2` range | `#{mouse_status_range}` の値。user range なら宣言した `X`、window list なら `window`。**空にはならない**（F11） |
| `$3` x | `#{mouse_x}`（0 始まり） |
| `$4` line | `#{mouse_status_line}`（0 始まりの status 行番号） |
| 引数の安全性 | すべて `#{q:...}` で sh エスケープ済み。range 名にシェルメタ文字が含まれても単一引数として届く（F8） |
| 環境 | `$TMUX` が設定されているため `tmux display-popup` 等が使える。tmux グローバル環境の `HERDR_SESSION` / `HERDR_PLUGIN_CONFIG_DIR` も利用可能 |
| stdout / stderr | **基盤側が `/dev/null` へ捨てる。** フックからの出力は一切ユーザーに見えない。ユーザーへの通知が要るなら `tmux display-message` 等を明示的に呼ぶこと |
| 終了コード | **基盤側が `\|\| true` で正規化する。** 非ゼロで終了しても tmux は何も表示しない |
| 並行性 | `run-shell -b` で起動されるため、**連打すると複数インスタンスが並行実行され、完了順序は保証されない**。フックは再入可能であること。排他が要るならフック自身がロックを取ること |
| overlay | フックが `display-popup` / `display-menu` を出している間、pane へのマウスイベントは overlay に消費される（§3 F4 の成立条件） |
| 責務 | 未知の `range` では何もせず終了すること。長時間ブロックしないこと |

---

## 7. 安全論拠の再定義

`src/config.rs:79` のドキュメントコメントは、`[statusline]` の allowlist を
「the whole safety argument」と表現している。**本変更でその表現は正確でなくなる。**
allowlist は引き続き任意の tmux オプション・コマンドを config から到達不能に保つが、
「tmux は入力を一切奪わない」という旧不変条件には明示的な例外が入る。

新しい安全論拠を「**境界づけられた capability**」として定義し、`option_name` の
コメントと base.conf のコメントを書き換える。

### 保証されること

- `[statusline]` から到達できるのは `status`・`status-*`・`window-status-*` のみで不変
- `mouse_clicks` は boolean 1 つ。任意のコマンド文字列を受け取らない
- 有効化しても tmux が持つキーは **root テーブルの固定 4 本のみ**。名前も動作も
  ハードコードされ、config からは変更できない
- 実行されるのは固定パスの 1 ファイルのみ。探索も glob もしない
- フックへ渡る引数はすべて tmux の `#{q:}` を通り、シェル注入を起こさない（F7・F8）
- 二段階 teardown（`destroy-unattached` と `client-attached` フック）に影響なし
- `mouse_clicks = false`（既定）では tmux の設定は現行と同一。root が空になる点だけが
  差分で、それは `mouse off` の下で観測不能

### 明示的に手放すこと

- 「tmux は入力を一切奪わない」は「**status 行のマウスイベントだけは tmux が処理する**」に
  緩和される
- 端末ネイティブの選択・ミドルクリック貼り付け（F6）

### 部分適用時の扱い

`apply_mouse_clicks` は複数の tmux コマンドを順に発行するため、途中で失敗すると
「`mouse on` だけ適用され binding が無い」状態がありうる。この状態は status クリックが
無反応になるだけだが、**中途半端な状態のまま起動を続けない**。どの段階の失敗でも
`return 2` → `exit 2` とし、launcher が `wait-for` でブロックされているうちにサーバごと
落とす。ロールバックは行わない（セッションごと破棄されるため不要）。

---

## 8. 既知の制約（本基盤では解決しない）

| 制約 | 内容 | 扱い |
| --- | --- | --- |
| 端末ネイティブの選択・ペーストの喪失 | `mouse on` で tmux が `1000/1002/1003/1006` を要求するため（F6）。`MouseDown2Status` を配線しないことでは回避できない | `mouse_clicks` を有効にする代償として `default-config.toml` と SKILL.md に明記 |
| hover 不可 | tmux に `MouseMove` binding が無い（F13） | 押せることは静的な見た目で示す |
| range 名 15 バイト上限 | tmux の仕様 | ドキュメント化 |
| 当たり判定の 1 カラムずれ | 右端が 1 カラム広い。`range=left` / `range=right` の内側では左端も +1（F14） | SKILL.md で回避策を案内 |
| tmux 3.4 未満では機能しない | F15 | §5.6 のプローブで `mouse on` 自体を見送る |
| 設定変更に再起動が必要 | 既存の全設定と同じ | 挙動変化なし |
| `status_interval` 由来の表示遅延 | クリック直後の再描画は最大 `status_interval` 秒遅れる | 基盤の対象外 |

---

## 9. テスト戦略

### 再現フィクスチャをコミットする

§9 の tmux 結合テストは pty へ SGR マウスシーケンスを注入する必要がある。この仕組みを
**コミット済みのヘルパーとして** `tests/` に置く（`tmp/` の調査メモは背景資料であり、
テストの依存先にしない）。

**フィクスチャは `scripts/run-in-tmux` を実際に起動しなければならない。** 既存の
`RealTmuxSmokeTests` が `script -qec` で行っているのと同じく、本番の起動経路を通す。
テスト側で `mouse on` や `bind-key` を再現してはならない。再現してしまうと、本番の
配線がクォートや展開順を間違えていてもテストが通る。`script` の代わりに自前の pty を
使うのは、クリックを注入する必要があるためである。

ヘルパーが提供するもの:

- 自前の pty 上で `sh scripts/run-in-tmux --session <name>` を起動する。socket は
  run-in-tmux が毎回一意に選ぶので、固定 socket による衝突は起こらない
- `(col, row, button)` の列を SGR シーケンスとして注入する
- `HSL_HERDR_BIN` に差し込む内側アプリ役として、生モードで stdin を記録し
  `1000` / `1003` / `1006` を要求するスタブを提供する
- クライアントへ描画されたバイト列を回収する（overlay 描画の検出用）
- 非同期フックの完了を**期限付きで待つ**（`run-shell -b` は完了順も完了時刻も保証
  しないため、固定の待ち時間や順序付き比較は偽陰性・偽陽性を生む）
- 子プロセスを `waitpid` で回収する

### ユニット（`src/config.rs`）

1. `mouse_clicks` の既定が `false`
2. `true` / `false` が正しくパースされる
3. 非 boolean（文字列・整数）が拒否される
4. `[statusline]` 内の `mouse` は従来どおり拒否される（既存テストの維持）
5. `write_protocol` が新しい行順で出力する

### プロトコル

6. `tests/test_hsl_internal.py:217` の完全一致期待値を新形式へ更新
7. `bin/hsl-internal` の `enabled = false` 経路が新プロトコルでも従来どおり動く
8. 新 writer × 旧 runner が `exit 2` する（fail-closed）
9. 旧 writer × 新 runner が `exit 2` する（fail-closed）
10. `mouse_clicks` が `true` / `false` 以外なら `run-in-tmux` が `exit 2` する

### 統合（`tests/test_tmux_runtime.py`）

11. `mouse_clicks = false`: `mouse` が `off`、**root テーブルが 0 本**（回帰）
12. `mouse_clicks = true` + 実行可能フック: `mouse` が `on`、**root がちょうど 4 本**。
    `DoubleClick1Pane` や `MouseDown1Control9` が復活していないことを明示的に検証
13. `mouse_clicks = true` + フック不在／非実行可能／config dir 未設定:
    `mouse` は `off`、root 0 本、stderr に警告、起動は成功
14. `bind-key` / `set-option` の各段階で失敗させたとき `exit 2` する

### 実 tmux 結合

15. フックが `(button, range, x, line)` を受け取る。left / right / wheel を網羅。
    **引数の悪意ある内容を必ず含める**: `'`、`#`、`#{...}`、`;`、空白、15 バイト上限。
    フックのパス側にも空白・`#`・`'` を含める（F7・F8・F10）
16. フックが stdout へ出力し非ゼロで終了しても、クライアントに何も描画されない（F9）。
    さらにフックが長時間実行してもコマンドキューがブロックされない（`-b` の検証）
17. バージョン判定（§5.6）を単体で検証する。`tmux 3.3a` / `tmux 3.4` / `tmux 3.7b` /
    `tmux 4.0` / `tmux next-3.9` / 解析不能な文字列をそれぞれ与え、
    3.4 未満のみ非対応と判定されること。判定に偽の `tmux -V` を食わせるため、
    `TMUX_BIN` を差し替えたスタブで駆動する。非対応時は `mouse` が `off` のままで、
    tmux へ `set-option` も `bind-key` も一切発行されていないこと
18. **回帰**: `mouse on` の状態で、pane 内アプリが 1000・1003 の双方でクリック・
    モーション・ドラッグ・ホイールを受信する
19. **座標**: `status-position top` でも `bottom` でも、アプリが受け取る行番号が
    ペイン相対として正しい（F5）
20. **negative test**: `unbind-key -a -T root` を外すと 18 が壊れることを確認し、
    §5.3 の 1 行を回帰から守る
21. 連打時にフックが並行起動しても基盤が壊れない（§6 の並行性契約）

---

## 10. 受け入れ条件

- 上記テスト 1〜21 がすべて通る
- `cargo build --release --locked` と `cargo test` が通る（変更前のベースラインは
  44 テスト全通過）
- 変更した shell スクリプトすべてに `sh -n` が通る
- `mouse_clicks` 未指定の既存 `config.toml` が挙動を変えずに読み込める
- `src/config.rs` の `option_name` コメントと `tmux/base.conf` のコメントが §7 の
  新しい安全論拠を反映している
- 実機の `hsl` セッションで `mouse_clicks = true` にしても、herdr のマウス操作
  （ペイン選択・スクロール・ドラッグ）が劣化しないことを確認する

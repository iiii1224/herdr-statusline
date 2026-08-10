# Status line マウスクリック基盤 — 設計仕様

- 日付: 2026-08-10
- 対象リポジトリ: `herdr-statusline`
- 検証環境: tmux 3.7b / Linux WSL2 / herdr 0.8.0
- 調査記録: `tmp/tmux-statusline-mouse-support.md`

---

## 1. 背景と目的

`herdr-statusline` は tmux を「ステータスライン専用のクローム」として使い、その中で herdr
本体を全画面 TUI として動かす。`tmux/base.conf` は `prefix None` / `unbind-key -a` /
`mouse off` を設定し、tmux が入力を一切奪わないことを不変条件としている。

この不変条件を保ったまま、**status line 上のクリックを外部が所有する単一フックへ配送する
基盤**を追加する。個々のボタン機能は本リポジトリの対象外とし、別リポジトリが
`on-click.sh` を提供して実現する。

### 解決する課題

herdr の外側で常時可視な領域は status line だけであり、現在は完全に read-only である。
クリックを受け取れるようにすることで、外部リポジトリが status line をエージェント制御・
Git 操作・情報展開などの操作面として利用できるようになる。

---

## 2. スコープ

### 対象

- `config.toml` のトップレベル opt-in キー `mouse_clicks`
- `hsl-config load` の行プロトコルへの当該フラグの追加
- `run-in-tmux` による条件付きの tmux 配線（`mouse on` + status クリックの binding 4 本）
- `on-click.sh` 拡張点の契約定義とドキュメント化

### 非対象

- 個別のボタン機能（pane id コピー、cwd コピー、lazygit 起動、エージェント起動、
  btm 起動、メディア操作など）。**別リポジトリが `on-click.sh` として実装する。**
- `#[range=user|X]` をステータスラインへ書くこと自体。これは既存の
  `status_format_N` / `status_left` / `status_right` で既に可能であり、基盤の変更を要しない。
- pane 領域のマウス処理。tmux が自動で素通しするため配線不要（§7 で実測を示す）。
- tmux 側の当たり判定オフセット（§8）の補正。

---

## 3. 前提となる実測事実

本設計は以下を tmux 3.7b で実測した結果に基づく。再現手順は
`tmp/tmux-statusline-mouse-support.md` の付録 B にある。

| # | 事実 | 設計への影響 |
| --- | --- | --- |
| F1 | `#[range=user\|X]` のクリックは `MouseDown1Status` を発火し、`#{mouse_status_range}` に `X` が入る | 任意領域をボタン化できる。ディスパッチのキーは range 名 |
| F2 | `run-shell` はフォーマットを展開する（`#{mouse_x}` 等がそのまま使える） | フックへ引数を渡せる |
| F3 | `mouse on` + `unbind-key -a` で、**転送 binding が無くても** pane のマウスイベントはアプリへ素通しされる | pane 転送 binding は不要 |
| F4 | F3 は 1000（クリックのみ）・1003（全イベント追跡）の双方で成立し、モーション `\x1b[<35;12;6M`・ドラッグ・ホイールもバイト単位で透過する | herdr が使う 1002/1003 が壊れない |
| F5 | herdr バイナリは `?1002h` / `?1003h` を含む（モーション追跡を使う） | F4 の確認が必須だった。結果は良好 |
| F6 | 右クリック（`MouseDown3Status`）とホイールも user range 上で range 名付きで発火する | left / right / wheel の 4 系統を配線する根拠 |
| F7 | tmux に `MouseMove` binding は存在しない | status line では hover を実装できない（§8） |
| F8 | user range の当たり判定は表示テキストより右端が 1 カラム広い。`range=left` / `range=right` の内側にネストすると左端も 1 カラムずれる | ドキュメント化のみ（§8） |

---

## 4. アーキテクチャ

### データフロー

```
status line クリック
  │
  ├─ tmux が range を解決            → #{mouse_status_range}
  │
  ├─ bind -n MouseDown1Status run-shell "<hook> left <range> <x> <line>"
  │
  └─ on-click.sh が case 分岐        （別リポジトリ所有）
        └─ tmux display-popup / herdr CLI / クリップボード など
```

pane クリックは配線しない。tmux がキーテーブルに一致を見つけられず、かつアプリが
マウスモードを有効化している場合、自動的に素通しする（F3・F4）。

### 三層構造

| 層 | 所有者 | 内容 |
| --- | --- | --- |
| 有効化 | ユーザー | `config.toml` の `mouse_clicks = true` |
| 配線 | 本プラグイン | `run-in-tmux` が `mouse on` と binding 4 本を適用 |
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

**`option_name` の allowlist は変更しない。** `[statusline]` から `mouse` や
key binding へ到達できないという既存の安全論拠（`src/config.rs` の `option_name`
ドキュメントコメント）はそのまま維持される。`mouse_clicks` は `[statusline]` ではなく
トップレベルの別名前空間であり、値は boolean 一つで、任意のコマンド文字列を受け取らない。

非 boolean が与えられた場合は serde が型エラーを返す。追加のバリデーションは不要。

### 5.2 `src/config.rs` — `write_protocol` の行プロトコル

```
現行                        変更後
1: enabled                  1: enabled
2: option count             2: mouse_clicks     ← 挿入
3: name[0]                  3: option count
4: value[0]                 4: name[0]
...                         5: value[0]
                            ...
```

`mouse_clicks` は `enabled` と同じく Rust の `bool` の `Display`（`true` / `false`）で書く。

producer（`hsl-config`）と consumer（`run-in-tmux`）は常に同一インストールから供給される。
`scripts/launcher-body.sh` が `$PLUGIN_ROOT/target/release/hsl-config` を staging に
コピーし、`run-in-tmux` も同じ `$root` から読まれるため、版ずれは起こらない。よって
バージョンネゴシエーションは導入せず、両者を同時に変更する。

**プロトコルの消費者は 2 つある。**

| 消費者 | 読む内容 | 本変更の影響 |
| --- | --- | --- |
| `bin/hsl-internal:146` | 1 行目の `enabled` のみ。`true\|false` を検証し、`false` ならファイルを捨てて herdr へ直行する。行数の検証はしない | **影響なし。** `enabled` は 1 行目のままで、行数検証も行っていない |
| `scripts/run-in-tmux:215-249` | 2 行目の count と以降のペア。行数を厳密に検証する | §5.4 のとおり更新する |

`bin/hsl-internal` を変更する必要はないが、行 1 の意味を変えていないことを回帰テストで
担保する（§9 テスト 10）。

### 5.3 `tmux/base.conf` — 変更しない

`set-option -g mouse off` を残す。「マウス無効が既定」という不変条件を静的ファイルに
保持し、有効化は `run-in-tmux` による動的な上書きとして扱う。これにより
`mouse_clicks = false`（既定）の経路は現行とバイト単位で同一の tmux 設定になる。

### 5.4 `scripts/run-in-tmux` — プロトコル読み出しの更新

`apply_status_options` のオフセットを 1 行ずらす。

- `count` の読み出し: `sed -n '2p'` → `sed -n '3p'`
- 行数検証: `lines -eq $((2 + count * 2))` → `lines -eq $((3 + count * 2))`
- `name` の読み出し: `$((3 + index * 2))` → `$((4 + index * 2))`
- `value` の読み出し: `$((4 + index * 2))` → `$((5 + index * 2))`

加えて `mouse_clicks` を 2 行目から読み、`true` / `false` 以外なら既存の
`'hsl: invalid hsl-config output'` と同じ扱いで `return 2` する。

```sh
MOUSE_CLICKS=$(sed -n '2p' "$STATUS_OPTIONS")
case $MOUSE_CLICKS in
    true|false) ;;
    *)
        printf '%s\n' 'hsl: invalid hsl-config output' >&2
        return 2
        ;;
esac
```

`STATUS_OPTIONS` が未設定のときは `apply_status_options` と同じく早期 return し、
`MOUSE_CLICKS` は `false` として扱う。

### 5.5 `scripts/run-in-tmux` — 条件付き配線

`apply_status_options` の成功後、`wait-for -S hsl-start` の**前**に実行する。
launcher はまだ `wait-for` でブロックされているため、失敗しても herdr には波及しない。

```sh
# Status-line mouse clicks are opt-in and inert without a hook: enabling the
# mouse with nothing to dispatch to would only produce a bar that swallows
# clicks. The hook path is baked in shell-quoted rather than left to
# $HERDR_PLUGIN_CONFIG_DIR, because run-shell's environment is not part of the
# contract this plugin relies on anywhere else.
apply_mouse_clicks() {
    [ "${MOUSE_CLICKS:-false}" = true ] || return 0
    [ -n "${HERDR_PLUGIN_CONFIG_DIR:-}" ] || {
        printf '%s\n' 'hsl: mouse_clicks is on but the plugin config dir is \
unknown; mouse clicks stay off' >&2
        return 0
    }
    hook=$HERDR_PLUGIN_CONFIG_DIR/on-click.sh
    if [ ! -x "$hook" ]; then
        printf 'hsl: mouse_clicks is on but %s is not executable; \
mouse clicks stay off\n' "$hook" >&2
        return 0
    fi
    quoted=$(shell_quote "$hook")
    "$TMUX_BIN" -L "$socket" set-option -g mouse on || return 2
    for pair in \
        'MouseDown1Status left' \
        'MouseDown3Status right' \
        'WheelUpStatus wheelup' \
        'WheelDownStatus wheeldown'
    do
        key=${pair% *}
        name=${pair#* }
        "$TMUX_BIN" -L "$socket" bind-key -n "$key" run-shell \
            "$quoted $name '#{mouse_status_range}' '#{mouse_x}' '#{mouse_status_line}'" \
            || return 2
    done
}
```

決定事項:

- **フックのパスは `shell_quote` で埋め込む。** `scripts/lib/shell-quote.sh` の既存関数を
  使う。`run-in-tmux` は 125 行目でこれを読み込み、`shell_quote` が定義されているかを
  確認済みなので、追加の読み込みは不要。`run-shell` の実行環境に
  `$HERDR_PLUGIN_CONFIG_DIR` が渡るかどうかに依存しない。
- **中クリック（`MouseDown2Status`）は配線しない。** 端末では伝統的にペーストに
  割り当てられており、奪うと利用者を驚かせる。
- **pane 系の binding は一切追加しない**（F3・F4）。
- 失敗時は `return 2` とし、呼び出し側は `apply_status_options` と同じく `exit 2` する。
  フック不在・config dir 不明は失敗ではなく `return 0`（警告のみ、起動継続）。

呼び出し側は既存の `apply_status_options` の直後に置く。

```sh
if ! apply_status_options; then
    exit 2
fi
if ! apply_mouse_clicks; then
    exit 2
fi
```

### 5.6 `scripts/default-config.toml` — 契約の説明

`enabled` の近くに `mouse_clicks` のコメントを追加し、`on-click.sh` の契約（§6）と
`#[range=user|X]` の書き方、および §8 の制約を簡潔に記す。既定値は変更しない
（コメントアウトのまま置き、有効化はユーザーの明示的な操作とする）。

### 5.7 `init.rs` — テンプレートは作らない

`on-click.sh` は別リポジトリの所有物である。プラグインが空のテンプレートを
`create_if_missing` で置くと、別リポジトリのインストールと衝突する
（`create_if_missing` は既存ファイルを上書きしないため、プラグインが先に空ファイルを
作ると別リポジトリの本体が入らなくなる）。よって `init.rs` は変更しない。

### 5.8 `skills/customize-herdr-statusline/SKILL.md`

以下を追記する。

- `mouse_clicks` の有効化方法と、`on-click.sh` が別リポジトリ所有である旨
- `#[range=user|X]` の書き方（`X` は最大 15 バイト）
- **`range=left` / `range=right` で包まれる `status_left` / `status_right` の内側に
  user range を置くとずれる**ため、`status_format_N` を自前定義して包まない構成を推奨する旨
- hover は実装できない旨

---

## 6. `on-click.sh` の契約

| 項目 | 内容 |
| --- | --- |
| パス | `$HERDR_PLUGIN_CONFIG_DIR/on-click.sh`（固定）。別リポジトリまたはユーザーが所有 |
| 起動条件 | `mouse_clicks = true` かつ当該パスが実行可能（`[ -x ]`） |
| `$1` button | `left` / `right` / `wheelup` / `wheeldown` のいずれか |
| `$2` range | `#{mouse_status_range}` の値。user range なら宣言した `X`、window list なら `window`、範囲外なら空文字列 |
| `$3` x | `#{mouse_x}`（0 始まり） |
| `$4` line | `#{mouse_status_line}`（0 始まりの status 行番号） |
| 環境 | `$TMUX` が設定されているため `tmux display-popup` 等がそのまま使える。tmux グローバル環境の `HERDR_SESSION` / `HERDR_PLUGIN_CONFIG_DIR` も利用可能 |
| 終了コード | 無視される（`run-shell` は非同期） |
| stdout / stderr | tmux が破棄する。`#(...)` と同様、エラーは自前で処理すること |
| 責務 | 未知の `range` では何もせず正常終了すること。長時間ブロックしないこと |

引数は常に 4 つ渡される。range が空文字列でも位置引数はずれない
（binding 側でシングルクォートしているため）。

---

## 7. 不変条件

本変更で維持されるもの:

- `[statusline]` allowlist は不変。`prefix`・key bindings・`mouse`・hooks・
  `destroy-unattached`・`remain-on-exit` は引き続き config から到達できない
- 追加されるトップレベルキーは boolean 一つのみで、任意のコマンド文字列を受け取らない
- 実行されるのは固定パスの 1 ファイルのみ。探索も glob もしない
- 二段階 teardown（`destroy-unattached` と `client-attached` フック）に影響なし
- `mouse_clicks = false`（既定）の場合、生成される tmux 設定は現行と同一

新たに導入されるリスクと緩和:

| リスク | 緩和 |
| --- | --- |
| `mouse on` が herdr のマウス入力を壊す | F3・F4 で 1000/1003・モーション・ドラッグ・ホイールの透過を実測確認。加えて回帰テストを課す（§9） |
| status 行のクリックが herdr に届かなくなる | status 行は pane の外であり、現状でも herdr には届いていない。挙動変化なし |
| フック不在で無反応なバーになる | フックが実行可能でなければ `mouse on` 自体を適用しない |

---

## 8. 既知の制約（本基盤では解決しない）

| 制約 | 内容 | 扱い |
| --- | --- | --- |
| hover 不可 | tmux に `MouseMove` binding が無い（F7） | 押せることは静的な見た目で示す。ドキュメント化 |
| range 名 15 バイト上限 | tmux の仕様 | ドキュメント化 |
| 当たり判定の 1 カラムずれ | 右端が 1 カラム広い。`range=left` / `range=right` の内側では左端も +1（F8） | tmux 側の挙動。SKILL.md で回避策（`status_format_N` を自前定義）を案内 |
| 設定変更に再起動が必要 | `hsl` セッションの再起動が要る | 既存の全設定と同じ。挙動変化なし |
| `status_interval` 由来の表示遅延 | クリック直後の再描画は最大 `status_interval` 秒遅れる | 基盤の対象外 |

---

## 9. テスト戦略

既存の Python 統合テスト群に追加する。`tests/helpers.py` の `write_protocol` は
Rust の writer を実行してワイヤ形式を生成しているため、プロトコル変更は自動的に
追随する（`enabled` と並ぶ `mouse_clicks` 引数の追加のみ必要）。

### ユニット（`src/config.rs`）

1. `mouse_clicks` の既定が `false`
2. `mouse_clicks = true` / `= false` が正しくパースされる
3. 非 boolean（文字列・整数）が拒否される
4. `[statusline]` 内の `mouse` は従来どおり拒否される（既存テストの維持を確認）
5. `write_protocol` が新しい行順で出力する

### 統合（`tests/test_tmux_runtime.py`）

6. `mouse_clicks = false`: `mouse` が `off` のまま、status 系 binding が 0 本（回帰）
7. `mouse_clicks = true` + 実行可能な `on-click.sh`: `mouse` が `on`、
   `MouseDown1Status` / `MouseDown3Status` / `WheelUpStatus` / `WheelDownStatus` の 4 本が存在
8. `mouse_clicks = true` + フック不在: `mouse` は `off`、binding 0 本、stderr に警告、起動は成功
9. `mouse_clicks = true` + フックが非実行可能（実行ビット無し）: 8 と同じ
10. `apply_status_options` が新しいオフセットで正しく動く（既存テストの維持を確認）
11. `bin/hsl-internal` の `enabled = false` 経路が新プロトコルでも従来どおり動く
    （1 行目の意味を変えていないことの回帰）
12. `mouse_clicks` が `true` / `false` 以外の場合に `run-in-tmux` が `exit 2` する

### 実 tmux 結合

13. 疑似 pty に SGR クリックを注入し、`on-click.sh` が
    `(button, range, x, line)` を期待どおり受け取る。left / right / wheel を網羅
14. **回帰**: `mouse on` の状態で、pane 内アプリが 1000 モード・1003 モードの双方で
    クリック・モーション・ドラッグ・ホイールをバイト単位で受信する

テスト 13・14 の pty 注入手法は `tmp/tmux-statusline-mouse-support.md` 付録 B の
スクリプトを流用する。フックのパスに空白を含むケースを 13 に含め、`shell_quote` の
埋め込みを検証する。

---

## 10. 受け入れ条件

- 上記テスト 1〜14 がすべて通る
- `cargo build --release --locked` が通る
- 変更した shell スクリプトすべてに `sh -n` が通る
- `mouse_clicks` 未指定の既存 `config.toml` が挙動を変えずに読み込める
- 実機の `hsl` セッションで、`mouse_clicks = true` にしても herdr のマウス操作
  （ペイン選択・スクロール）が劣化しないことを確認する

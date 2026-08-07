#!/bin/sh
# Build hsl-config and install the managed ~/.local/bin/hsl launcher.
set -eu

root=$(CDPATH= cd -P "$(dirname "$0")/.." && pwd)
: "${HOME:?build.sh: HOME is not set}"
cargo_bin=${HSL_TEST_CARGO_BIN:-cargo}
bindir=${HSL_TEST_BIN_DIR:-$HOME/.local/bin}
launcher=$bindir/hsl
install_launcher=$root/scripts/install-launcher.sh

command -v "$cargo_bin" >/dev/null 2>&1 || {
    printf '%s\n' 'build.sh: Cargo is required to install herdr-statusline' >&2
    printf '%s\n' 'build.sh: install a recent stable Rust toolchain and reinstall' >&2
    exit 1
}
[ -f "$root/Cargo.lock" ] || {
    printf '%s\n' 'build.sh: Cargo.lock is required for a reproducible build' >&2
    exit 1
}

# Some checkout methods drop the execute bit; restore it rather than refusing
# to install, which the user could not act on.
for required in bin/hsl-internal scripts/run-in-tmux; do
    [ -f "$root/$required" ] || {
        printf '%s\n' "build.sh: missing required file: $required" >&2
        exit 1
    }
    [ -x "$root/$required" ] || chmod +x "$root/$required"
done
for readable in tmux/base.conf scripts/launcher-body.sh scripts/install-launcher.sh \
                scripts/lib/shell-quote.sh \
                scripts/default-config.toml scripts/default-herdr-info.sh \
                skills/customize-herdr-statusline/SKILL.md \
                skills/customize-herdr-statusline/agents/openai.yaml; do
    [ -r "$root/$readable" ] || {
        printf '%s\n' "build.sh: missing required file: $readable" >&2
        exit 1
    }
done

# Refuse an unusable destination before spending minutes in the compiler.
sh "$install_launcher" --check "$root" "$launcher"

"$cargo_bin" build --release --locked

if [ "${HSL_TEST_SKIP_BINARY_CHECK:-0}" != 1 ] && [ ! -x "$root/target/release/hsl-config" ]; then
    printf '%s\n' 'build.sh: target/release/hsl-config was not produced' >&2
    exit 1
fi

sh "$install_launcher" "$root" "$launcher"

set -u

PLUGIN_ID=herdr-statusline
HERDR_BIN=${HSL_HERDR_BIN:-herdr}
: "${HOME:?hsl: HOME is not set}"

resolve_self() {
    case $0 in
        */*) candidate=$0 ;;
        *) candidate=$(command -v "$0" 2>/dev/null || printf '%s' "$0") ;;
    esac
    dir=$(CDPATH='' cd -P "$(dirname "$candidate")" 2>/dev/null && pwd) || return 1
    printf '%s/%s\n' "$dir" "$(basename "$candidate")"
}

is_managed_launcher() {
    # The marker is split so this file never contains it as a literal: this
    # script is concatenated verbatim into the generated launcher by
    # install-launcher.sh, and marker scans must find it only on the line
    # install-launcher.sh writes, never in this function's own source. The
    # split shape is load-bearing for tests/test_consistency.py.
    expected="# herdr-statusline-managed-launcher:""v1"
    [ -f "$1" ] && [ ! -L "$1" ] && [ "${MARKER:-}" = "$expected" ] && grep -Fqx "$expected" "$1"
}

remove_self() {
    self=$(resolve_self) || return 1
    if ! is_managed_launcher "$self"; then
        printf '%s\n' "hsl: refusing to remove an unmanaged launcher: $self" >&2
        return 1
    fi
    rm -f "$self" || return 1
    printf '%s\n' "hsl: removed stale launcher: $self" >&2
}

require_herdr() {
    command -v "$HERDR_BIN" >/dev/null 2>&1 || {
        printf '%s\n' 'hsl: herdr is not available in PATH' >&2
        exit 127
    }
}

root_is_complete() {
    [ -n "${1:-}" ] || return 1
    [ -f "$1/herdr-plugin.toml" ] || return 1
    [ -x "$1/target/release/hsl-config" ] || return 1
    [ -x "$1/bin/hsl-internal" ] || return 1
    grep -Fqx "id = \"$PLUGIN_ID\"" "$1/herdr-plugin.toml"
}

# Herdr builds a plugin in a staging directory and moves the checkout once the
# build succeeds, so the path recorded at build time is routinely gone. A
# mangled or absent path yields nothing here and is caught by root_is_complete.
registered_root() {
    listing=$("$HERDR_BIN" plugin list --plugin "$PLUGIN_ID" --json 2>/dev/null) || return 1
    printf '%s\n' "$listing" |
        sed -n 's/.*"plugin_root"[[:space:]]*:[[:space:]]*"\([^"\\]*\)".*/\1/p'
}

# Best effort: point this launcher at the new root so the next run skips the
# lookup. Every failure here is survivable, so none of them are reported.
rewrite_self() {
    target=$(resolve_self) || return 1
    is_managed_launcher "$target" || return 1
    sh "$1/scripts/install-launcher.sh" "$1" "$target" >/dev/null 2>&1
}

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
    check_config_explicit=false
    if [ "$#" -eq 1 ]; then
        check_config_path=
    elif [ "$#" -eq 2 ]; then
        check_config_explicit=true
        check_config_path=$2
    else
        printf '%s\n' 'usage: hsl --hsl-check-config [<config.toml>]' >&2
        exit 2
    fi
fi

if ! root_is_complete "$PLUGIN_ROOT"; then
    require_herdr
    resolved=$(registered_root) || {
        printf '%s\n' 'hsl: cannot determine the Herdr plugin state' >&2
        exit 2
    }
    if [ -z "$resolved" ]; then
        printf '%s\n' "hsl: $PLUGIN_ID is no longer installed" >&2
        remove_self || exit 3
        exit 3
    fi
    if ! root_is_complete "$resolved"; then
        printf '%s\n' "hsl: $PLUGIN_ID is registered but incomplete: $resolved" >&2
        printf '%s\n' "hsl: reinstall it with '$HERDR_BIN plugin install iiii1224/herdr-statusline'" >&2
        exit 2
    fi
    PLUGIN_ROOT=$resolved
    if [ "$mode" = run ]; then
        rewrite_self "$PLUGIN_ROOT" || :
    fi
fi

HELPER=$PLUGIN_ROOT/target/release/hsl-config
INTERNAL=$PLUGIN_ROOT/bin/hsl-internal

if [ "$mode" = check-config ]; then
    if [ "$check_config_explicit" = false ]; then
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

if [ "$mode" = uninstall ]; then
    require_herdr
    if [ "$purge" = true ]; then
        config_dir=$("$HERDR_BIN" plugin config-dir "$PLUGIN_ID") || {
            printf '%s\n' 'hsl: failed to resolve the plugin config directory' >&2
            exit 2
        }
        umask 077
        staging=$(mktemp -d "${TMPDIR:-/tmp}/hsl-uninstall.XXXXXX") || exit 1
        trap 'rm -rf "$staging"' EXIT HUP INT TERM
        # Herdr deletes the checkout, so keep a copy of the helper alive.
        cp "$HELPER" "$staging/hsl-config" || exit 1
        chmod 700 "$staging/hsl-config"
        validated=$("$staging/hsl-config" validate-purge "$config_dir" "$HOME") || exit 2
        case $validated in
            missing|/*) ;;
            *)
                printf '%s\n' 'hsl: invalid validate-purge output' >&2
                exit 2
                ;;
        esac
    fi

    "$HERDR_BIN" plugin uninstall "$PLUGIN_ID" || {
        printf '%s\n' 'hsl: herdr failed to uninstall the plugin; nothing was removed' >&2
        exit 2
    }

    if [ "$purge" = true ] && [ "$validated" != missing ]; then
        "$staging/hsl-config" purge-config "$validated" "$HOME" || {
            printf '%s\n' 'hsl: the plugin was removed but its config could not be deleted' >&2
            exit 2
        }
    fi

    remove_self || {
        printf '%s\n' 'hsl: the plugin was removed but the launcher could not be deleted' >&2
        exit 1
    }
    exit 0
fi

exec "$INTERNAL" "$@"

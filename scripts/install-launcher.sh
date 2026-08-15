#!/bin/sh
# Write the managed hsl launcher for a plugin root. `--check` validates the
# destination without writing, so a build can refuse before it compiles.
set -eu

MARKER='# herdr-statusline-managed-launcher:v1'

check_only=false
if [ "${1:-}" = --check ]; then
    check_only=true
    shift
fi
[ "$#" -eq 2 ] || {
    printf '%s\n' 'install-launcher.sh: usage: [--check] <plugin-root> <launcher>' >&2
    exit 2
}
root=$1
launcher=$2
bindir=$(dirname "$launcher")

newline='
'
carriage_return=$(printf '\r')
case $root in
    *"$newline"*|*"$carriage_return"*)
        printf '%s\n' 'install-launcher.sh: the plugin root contains a line break' >&2
        exit 1
        ;;
esac

if [ -L "$launcher" ]; then
    printf '%s\n' "install-launcher.sh: refusing to replace a symlink: $launcher" >&2
    printf '%s\n' 'install-launcher.sh: remove or rename it, then reinstall' >&2
    exit 1
elif [ -e "$launcher" ]; then
    if [ ! -f "$launcher" ] || ! grep -Fqx "$MARKER" "$launcher"; then
        printf '%s\n' "install-launcher.sh: refusing to replace an unmanaged path: $launcher" >&2
        printf '%s\n' 'install-launcher.sh: remove or rename it, then reinstall' >&2
        exit 1
    fi
fi

[ "$check_only" = false ] || exit 0

body=$root/scripts/launcher-body.sh
[ -r "$body" ] || {
    printf '%s\n' "install-launcher.sh: missing required file: $body" >&2
    exit 1
}

quote_lib=$(CDPATH='' cd -P "$(dirname "$0")" && pwd)/lib/shell-quote.sh
[ -r "$quote_lib" ] || {
    printf '%s\n' "install-launcher.sh: missing required file: $quote_lib" >&2
    exit 1
}
# shellcheck source=scripts/lib/shell-quote.sh
. "$quote_lib"
# A missing or unparsable library aborts before here, but an empty or truncated
# one is sourced successfully and defines nothing. That would bake MARKER= and
# PLUGIN_ROOT= as empty strings into a launcher this script then reports as
# installed, and launcher-body.sh's rewrite_self trusts that exit status -- so a
# working launcher would be silently replaced by one that can never recognise
# itself as managed, leaving `hsl uninstall` unable to remove it.
command -v shell_quote >/dev/null 2>&1 || {
    printf '%s\n' "install-launcher.sh: $quote_lib does not define shell_quote" >&2
    exit 1
}

umask 077
mkdir -p "$bindir"
tmp=$(mktemp "$bindir/.hsl.XXXXXX")
trap 'rm -f "$tmp"' EXIT HUP INT TERM

{
    printf '#!/bin/sh\n'
    printf '%s\n' "$MARKER"
    printf 'MARKER=%s\n' "$(shell_quote "$MARKER")"
    printf 'PLUGIN_ROOT=%s\n' "$(shell_quote "$root")"
    cat "$body"
} >"$tmp"

chmod 755 "$tmp"
mv -f "$tmp" "$launcher"
trap - EXIT HUP INT TERM

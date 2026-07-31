# POSIX sh single-quote escaping, for baking a value into a generated shell
# script. Shared by run-in-tmux (runtime) and install-launcher.sh (build time),
# which is why it lives here rather than in either caller.
#
# Sourced, never executed: it defines a function and does nothing else.
shell_quote() {
    printf "'"
    printf '%s' "$1" | sed "s/'/'\\\\''/g"
    printf "'"
}

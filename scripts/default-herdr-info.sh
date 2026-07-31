#!/bin/sh
# Print the focused herdr pane, its working directory and its git state as
# coloured tmux status-line segments.
set -u

PANE_STYLE='#[fg=#ffffff,bg=#5a45a5]'
CWD_STYLE='#[fg=#ffffff,bg=#2b6cb0]'
GIT_STYLE='#[fg=#ffffff,bg=#2f855a]'

# One flat string field out of herdr's single-line JSON. The wanted values are
# ids and paths, so this is enough; a literal `"` inside one would defeat it.
field() {
    printf '%s\n' "$2" | sed -n "s/.*\"$1\":\"\([^\"]*\)\".*/\1/p"
}

# `herdr pane current` returns the focused pane when no --pane is given, and it
# resolves the server from $HERDR_SESSION, so no socket path is needed here.
pane= cwd=
if pane_json=$(herdr pane current 2>/dev/null); then
    pane=$(field pane_id "$pane_json")
    cwd=$(field foreground_cwd "$pane_json")
    [ -n "$cwd" ] || cwd=$(field cwd "$pane_json")
fi

# Home-relative, but only on a component boundary: /home/ida-old must not
# collapse to ~-old.
short_cwd=$cwd
case ${HOME:-} in '') ;; *)
    case $cwd in
        "$HOME") short_cwd='~' ;;
        "$HOME"/*) short_cwd="~${cwd#"$HOME"}" ;;
    esac ;;
esac

branch= state=
if [ -n "$cwd" ] && [ -d "$cwd" ]; then
    branch=$(git -C "$cwd" symbolic-ref --short HEAD 2>/dev/null) ||
        branch=$(git -C "$cwd" rev-parse --short HEAD 2>/dev/null) ||
        branch=
fi

if [ -n "$branch" ]; then
    if counts=$(git -C "$cwd" rev-list --left-right --count HEAD...@{upstream} \
        2>/dev/null)
    then
        ahead=$(printf '%s' "$counts" | cut -f1)
        behind=$(printf '%s' "$counts" | cut -f2)
        [ "$ahead" -gt 0 ] 2>/dev/null && state="$state⇡$ahead"
        [ "$behind" -gt 0 ] 2>/dev/null && state="$state⇣$behind"
    fi

    porcelain=$(git -C "$cwd" status --porcelain=v1 2>/dev/null)
    dirty() { printf '%s\n' "$porcelain" | grep -qE "$1"; }

    flags=
    dirty '^(DD|AU|UD|UA|DU|AA|UU)' && flags="$flags="   # conflicted
    [ -n "$(git -C "$cwd" stash list 2>/dev/null)" ] &&
        flags="$flags\$"                                # stashed
    dirty '^[MARC]' && flags="$flags+"                  # staged
    dirty '^.[MD]'  && flags="$flags!"                  # modified
    dirty '^(R|.R)' && flags="$flags»"                  # renamed
    dirty '^(D|.D)' && flags="$flags✘"                  # deleted
    dirty '^\?\?'   && flags="$flags?"                  # untracked

    if [ -n "$flags" ]; then
        state="${state:+$state }$flags"
    elif [ -z "$state" ] && [ -z "$porcelain" ]; then
        state='✔'                                       # clean
    fi
fi

out="$PANE_STYLE ${pane:-no pane} "
[ -n "$short_cwd" ] && out="$out$CWD_STYLE $short_cwd "
[ -n "$branch" ] && out="$out$GIT_STYLE $branch${state:+ $state} "
printf '%s#[default]\n' "$out"

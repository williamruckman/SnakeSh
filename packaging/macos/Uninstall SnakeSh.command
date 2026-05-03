#!/bin/sh
set -eu

TOOL_LAUNCHER_DIR="${HOME}/Applications/SnakeSh Tools"

shell_quote() {
    printf "%s" "$1" | sed "s/'/'\\\\''/g"
}

remove_path() {
    target="$1"
    if [ ! -e "$target" ]; then
        return 0
    fi
    if rm -rf "$target" 2>/dev/null; then
        return 0
    fi
    if command -v osascript >/dev/null 2>&1; then
        quoted_target="$(shell_quote "$target")"
        osascript -e "do shell script \"rm -rf '$quoted_target'\" with administrator privileges"
        return 0
    fi
    printf "Unable to remove %s\n" "$target" >&2
    return 1
}

remove_path "$TOOL_LAUNCHER_DIR"
remove_path "/Applications/SnakeSh.app"
remove_path "${HOME}/Applications/SnakeSh.app"

if command -v osascript >/dev/null 2>&1; then
    osascript -e 'display dialog "SnakeSh and managed tool launchers were removed." buttons {"OK"} default button "OK" with title "SnakeSh Uninstall"' >/dev/null 2>&1 || true
else
    printf "SnakeSh and managed tool launchers were removed.\n"
fi

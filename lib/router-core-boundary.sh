#!/usr/bin/env bash

router_core_boundary_parse() {
    local log="$1" values count marker
    [[ -f "$log" ]] || return 2
    values="$(awk -F= '$1=="CORE_CHANGE_COMPLETE" && ($2=="true" || $2=="false"){print $2}' "$log")"
    count="$(printf '%s\n' "$values" | awk 'NF{n++} END{print n+0}')"
    [[ "$count" -ge 1 ]] || return 3
    marker="$(printf '%s\n' "$values" | awk 'NF{v=$0} END{print v}')"
    if printf '%s\n' "$values" | awk -v last="$marker" 'NF && $0!=last{bad=1} END{exit bad?0:1}'; then
        return 4
    fi
    ROUTER_CORE_CHANGE_COMPLETE="$marker"
    export ROUTER_CORE_CHANGE_COMPLETE
}

router_core_boundary_run() {
    local log="$1"
    shift
    [[ "${1:-}" == -- ]] || return 2
    shift
    local restore_errexit=false rc parse_rc
    [[ $- == *e* ]] && restore_errexit=true
    set +e
    "$@" 2>&1 | tee "$log"
    rc="${PIPESTATUS[0]}"
    [[ "$restore_errexit" == true ]] && set -e
    ROUTER_CORE_CHILD_RC="$rc"
    export ROUTER_CORE_CHILD_RC
    set +e
    router_core_boundary_parse "$log"
    parse_rc=$?
    [[ "$restore_errexit" == true ]] && set -e
    [[ "$parse_rc" -eq 0 ]] || return "$parse_rc"
    return 0
}

router_core_rollback_allowed() {
    [[ "${ROUTER_CORE_CHANGE_COMPLETE:-false}" != true ]]
}

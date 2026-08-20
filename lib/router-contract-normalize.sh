#!/usr/bin/env bash

router_contract_bool_normalize() {
    case "${1,,}" in
      true|1|yes|y|on|pass) printf 'true
' ;;
      false|0|no|n|off|fail|'') printf 'false
' ;;
      *) return 2 ;;
    esac
}

router_contract_bool_is_true() {
    [[ "$(router_contract_bool_normalize "${1:-}" 2>/dev/null || true)" == true ]]
}

router_contract_require_kind() {
    [[ "${1:-}" == "${2:-}" ]]
}

router_contract_require_readable_python() {
    [[ -f "$1" && -r "$1" ]]
}

router_contract_require_executable_shell() {
    [[ -f "$1" && -x "$1" ]]
}

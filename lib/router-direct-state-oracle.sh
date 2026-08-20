#!/usr/bin/env bash

router_direct_state_oracle() {
    local file="$1" schema active mode
    if [[ ! -e "$file" ]]; then
        echo DIRECT_ORACLE=PASS
        echo DIRECT_STATE=absent
        return 0
    fi
    [[ -f "$file" ]] || {
        echo DIRECT_ORACLE=FAIL
        echo DIRECT_REASON=not_regular_file
        return 2
    }
    schema="$(awk -F= '$1=="schema"{v=$2} END{print v}' "$file")"
    active="$(awk -F= '$1=="active"{v=$2} END{print v}' "$file")"
    mode="$(awk -F= '$1=="mode"{v=$2} END{print v}' "$file")"
    if [[ "$schema" == router-wgpay-direct-mode-state-v1 && "$active" == false && "$mode" == NORMAL ]]; then
        echo DIRECT_ORACLE=PASS
        echo DIRECT_STATE=normal_inactive
        echo DIRECT_SCHEMA="$schema"
        return 0
    fi
    echo DIRECT_ORACLE=FAIL
    echo DIRECT_STATE=unsafe_or_invalid
    echo DIRECT_SCHEMA="$schema"
    echo DIRECT_ACTIVE="$active"
    echo DIRECT_MODE="$mode"
    return 1
}

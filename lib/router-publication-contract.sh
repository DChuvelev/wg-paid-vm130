#!/usr/bin/env bash

source "${ROUTER_PUBLICATION_POLICY_LIB:-${ROUTER_OPS_BASE:-/opt/router-ops}/lib/router-publication-policy.sh}"
source "${ROUTER_CONTRACT_NORMALIZE_LIB:-${ROUTER_OPS_BASE:-/opt/router-ops}/lib/router-contract-normalize.sh}"

router_publication_field() {
    local file="$1" key="$2"
    awk -F= -v key="$key" '
        $1 == key {
            print substr($0, index($0, "=") + 1)
        }
    ' "$file" | tail -n 1
}

router_publication_is_hex40() {
    [[ "${1:-}" =~ ^[0-9a-f]{40}$ ]]
}

router_publication_is_uint() {
    [[ "${1:-}" =~ ^[0-9]+$ ]]
}

router_publication_contract_load() {
    local file="$1" expected_kind="$2"

    RPC_RESULT="$(router_publication_field "$file" RESULT)"
    RPC_PUBLISHED_DIR="$(router_publication_field "$file" PUBLISHED_DIR)"
    RPC_PUBLISHED_RELATIVE_PATH="$(router_publication_field "$file" PUBLISHED_RELATIVE_PATH)"
    RPC_PUBLIC_OBJECT_KIND="$(router_publication_field "$file" PUBLIC_OBJECT_KIND)"
    RPC_PREVIOUS_MOVED_COUNT="$(router_publication_field "$file" PREVIOUS_MOVED_COUNT)"
    RPC_LOCAL_POSTCHECK="$(router_publication_field "$file" LOCAL_POSTCHECK)"
    RPC_LATEST_INDEX_CONTAINS_FOLDER="$(router_publication_field "$file" LATEST_INDEX_CONTAINS_FOLDER)"
    RPC_PUBLICATION_COMMITTED="$(router_publication_field "$file" PUBLICATION_COMMITTED)"
    RPC_PUBLICATION_REUSED="$(router_publication_field "$file" PUBLICATION_REUSED)"
    RPC_COLLISION_DEDUPLICATED_COUNT="$(router_publication_field "$file" COLLISION_DEDUPLICATED_COUNT)"
    RPC_PUBLIC_URL="$(router_publication_field "$file" PUBLIC_URL)"
    RPC_TRANSACTION_ID="$(router_publication_field "$file" PUBLICATION_TRANSACTION_ID)"

    [[ "$RPC_RESULT" == "PASS_ROUTER_PUBLISH_REPORT" ]] || return 1
    [[ -n "$RPC_PUBLISHED_DIR" && -d "$RPC_PUBLISHED_DIR" ]] || return 1
    [[ "$RPC_PUBLISHED_RELATIVE_PATH" == latest/* ]] || return 1
    [[ "$RPC_PUBLIC_OBJECT_KIND" == "$expected_kind" ]] || return 1
    router_publication_is_uint "$RPC_PREVIOUS_MOVED_COUNT" || return 1
    [[ "$RPC_LOCAL_POSTCHECK" == PASS ]] || return 1
    [[ "$RPC_LATEST_INDEX_CONTAINS_FOLDER" == true ]] || return 1
    [[ "$RPC_PUBLICATION_COMMITTED" == true ]] || return 1
    [[ "$RPC_PUBLICATION_REUSED" == true || "$RPC_PUBLICATION_REUSED" == false ]] || return 1
    router_publication_is_uint "$RPC_COLLISION_DEDUPLICATED_COUNT" || return 1
    [[ "$RPC_PUBLIC_URL" == https://*/*/ ]] || return 1
    [[ -n "$RPC_TRANSACTION_ID" ]] || return 1
}

router_publication_verify_file() {
    local file="$1" expected_sha="$2"
    [[ -f "$file" ]] || return 1
    [[ "$(sha256sum "$file" | awk '{print $1}')" == "$expected_sha" ]]
}

router_publication_atomic_write_from_stdin() {
    local destination="$1" mode="${2:-600}" tmp
    tmp="${destination}.tmp.$$"
    mkdir -p "$(dirname "$destination")"
    cat > "$tmp"
    chmod "$mode" "$tmp"
    mv -f "$tmp" "$destination"
}

router_publication_contract_load_for_label() {
    local file="$1" label="$2" classified expected_kind expected_match
    classified="$(router_publication_classify "$label")" || return 1
    IFS=$'	' read -r expected_kind expected_match <<< "$classified"
    router_publication_contract_load "$file" "$expected_kind"
}

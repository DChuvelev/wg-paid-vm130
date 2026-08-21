#!/usr/bin/env bash

router_machine_close_contract_classify() {
    local mode="$1" machine="$2" backend_rc="$3" log_file="$4" upper
    case "$machine" in vm100|vm101|vm103|vm121|vm130) ;; *) ROUTER_MACHINE_CLOSE_CLASSIFICATION=STOP_INVALID_MACHINE; return 1 ;; esac
    upper="${machine^^}"

    if [[ "$backend_rc" -eq 124 || "$backend_rc" -ge 128 ]]; then
        ROUTER_MACHINE_CLOSE_CLASSIFICATION=STOP_BACKEND_SIGNAL_OR_TIMEOUT
        return 1
    fi
    if grep -Eq '^(RESULT=STOP_|STOP=)' "$log_file"; then
        ROUTER_MACHINE_CLOSE_CLASSIFICATION=STOP_BACKEND_OR_PUBLICATION_MARKER
        return 1
    fi

    if [[ "$mode" == --check ]]; then
        if grep -qx "RESULT=PASS_VM130_${upper}_GIT_SOURCE_CHECK" "$log_file"; then
            ROUTER_MACHINE_CLOSE_CLASSIFICATION=PASS_AUTHORITATIVE_CHECK
            return 0
        fi
        ROUTER_MACHINE_CLOSE_CLASSIFICATION=STOP_INCOMPLETE_CHECK_PASS_MARKERS
        return 1
    fi
    [[ "$mode" == --publish ]] || { ROUTER_MACHINE_CLOSE_CLASSIFICATION=STOP_INVALID_MODE; return 1; }

    local target_pass=false outer_pass=false clean_clone=false publisher_pass=false
    local committed=false local_postcheck=false latest_index=false http_postcheck=false
    local permanent_https=false state_updated=false public_url=false commit=false tree=false file_count=false

    grep -Eq "^RESULT=PASS_${machine}_GIT_PUBLISH$" "$log_file" && target_pass=true
    grep -qx "RESULT=PASS_VM130_${upper}_GIT_SOURCE_PUBLISH" "$log_file" && outer_pass=true
    grep -qx 'VM130_STATUS=CLEAN' "$log_file" && clean_clone=true
    grep -qx 'PUBLISHER_RESULT=PASS_ROUTER_PUBLISH_REPORT' "$log_file" && publisher_pass=true
    grep -qx 'PUBLICATION_COMMITTED=true' "$log_file" && committed=true
    grep -qx 'LOCAL_POSTCHECK=PASS' "$log_file" && local_postcheck=true
    grep -qx 'LATEST_INDEX_CONTAINS_FOLDER=true' "$log_file" && latest_index=true
    grep -qx 'HTTP_POSTCHECK=PASS' "$log_file" && http_postcheck=true
    grep -qx 'PERMANENT_HTTPS_PUBLISHED=true' "$log_file" && permanent_https=true
    grep -qx 'SOURCE_STATE_UPDATED=true' "$log_file" && state_updated=true
    grep -Eq "^${upper}_(GIT_SOURCE_)?URL=https://reports\\.secret-studio\\.ru/latest/.+/$" "$log_file" && public_url=true
    grep -Eq "^${upper}_GIT_SOURCE_COMMIT=[0-9a-f]{40}$" "$log_file" && commit=true
    grep -Eq "^${upper}_GIT_SOURCE_TREE=[0-9a-f]{40}$" "$log_file" && tree=true
    grep -Eq "^${upper}_GIT_SOURCE_FILE_COUNT=[0-9]+$" "$log_file" && file_count=true

    if [[ ( "$target_pass" == true || "$outer_pass" == true ) &&
          "$clean_clone" == true && "$publisher_pass" == true &&
          "$committed" == true && "$local_postcheck" == true &&
          "$latest_index" == true && "$http_postcheck" == true &&
          "$permanent_https" == true && "$state_updated" == true &&
          "$public_url" == true && "$commit" == true && "$tree" == true && "$file_count" == true ]]; then
        if [[ "$backend_rc" -eq 0 ]]; then
            ROUTER_MACHINE_CLOSE_CLASSIFICATION=PASS_AUTHORITATIVE_BACKEND_AND_PUBLICATION
        else
            ROUTER_MACHINE_CLOSE_CLASSIFICATION=PASS_AUTHORITATIVE_EVIDENCE_DESPITE_BACKEND_RC
        fi
        return 0
    fi

    if [[ "$backend_rc" -ne 0 ]]; then
        ROUTER_MACHINE_CLOSE_CLASSIFICATION=STOP_BACKEND_NONZERO_WITHOUT_COMPLETE_PASS
    else
        ROUTER_MACHINE_CLOSE_CLASSIFICATION=STOP_INCOMPLETE_AUTHORITATIVE_PASS_MARKERS
    fi
    return 1
}

#!/usr/bin/env bash

router_step_workflow_init() {
    local current_file="$1" events_file="$2"
    shift 2
    local phase tmp
    ROUTER_STEP_CURRENT_FILE="$current_file"
    ROUTER_STEP_EVENTS_FILE="$events_file"
    ROUTER_STEP_LOCK_FILE="${current_file}.lock"
    mkdir -p "$(dirname "$current_file")" "$(dirname "$events_file")"
    tmp="${current_file}.tmp.$$"
    : > "$tmp"
    for phase in "$@"; do
        [[ "$phase" =~ ^[a-zA-Z0-9_.-]+$ ]] || return 2
        printf '%s=pending\n' "$phase" >> "$tmp"
    done
    printf 'PUBLICATION_BARRIER=none\n' >> "$tmp"
    printf 'WORKFLOW_RESULT=running\n' >> "$tmp"
    chmod 600 "$tmp"
    mv -f "$tmp" "$current_file"
    : > "$events_file"
    chmod 600 "$events_file"
}

router_step_workflow_attach() {
    ROUTER_STEP_CURRENT_FILE="$1"
    ROUTER_STEP_EVENTS_FILE="$2"
    ROUTER_STEP_LOCK_FILE="${1}.lock"
    [[ -f "$ROUTER_STEP_CURRENT_FILE" && -f "$ROUTER_STEP_EVENTS_FILE" ]]
}

router_step_phase_status() {
    local phase="$1"
    awk -F= -v phase="$phase" '$1 == phase { value=$2 } END { print value }' "$ROUTER_STEP_CURRENT_FILE"
}

router_step_phase_set() {
    local phase="$1" status="$2" detail="${3:-none}" tmp
    [[ "$phase" =~ ^[a-zA-Z0-9_.-]+$ ]] || return 2
    [[ "$status" =~ ^(pending|started|complete|skipped|failed)$ ]] || return 2
    grep -q "^${phase}=" "$ROUTER_STEP_CURRENT_FILE" || return 3
    exec 8>"$ROUTER_STEP_LOCK_FILE"
    flock 8
    tmp="${ROUTER_STEP_CURRENT_FILE}.tmp.$$"
    awk -F= -v phase="$phase" -v status="$status" '
        $1 == phase { print phase "=" status; next }
        { print }
    ' "$ROUTER_STEP_CURRENT_FILE" > "$tmp"
    chmod 600 "$tmp"
    mv -f "$tmp" "$ROUTER_STEP_CURRENT_FILE"
    printf '%s\t%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$phase" "$status" "$detail" >> "$ROUTER_STEP_EVENTS_FILE"
    flock -u 8
    exec 8>&-
}

router_step_phase_begin() { router_step_phase_set "$1" started "${2:-none}"; }
router_step_phase_complete() { router_step_phase_set "$1" complete "${2:-none}"; }
router_step_phase_skip() { router_step_phase_set "$1" skipped "${2:-none}"; }
router_step_phase_fail() { router_step_phase_set "$1" failed "${2:-none}"; }

router_step_first_incomplete() {
    awk -F= '
        $1 == "WORKFLOW_RESULT" || $1 == "PUBLICATION_BARRIER" { next }
        $2 != "complete" && $2 != "skipped" { print $1; exit }
    ' "$ROUTER_STEP_CURRENT_FILE"
}

router_step_workflow_mark_complete() {
    local incomplete tmp
    incomplete="$(router_step_first_incomplete)"
    [[ -z "$incomplete" ]] || { printf 'INCOMPLETE_PHASE=%s\n' "$incomplete" >&2; return 1; }
    exec 8>"$ROUTER_STEP_LOCK_FILE"
    flock 8
    tmp="${ROUTER_STEP_CURRENT_FILE}.tmp.$$"
    awk -F= '$1 == "WORKFLOW_RESULT" { print "WORKFLOW_RESULT=complete"; next } { print }' "$ROUTER_STEP_CURRENT_FILE" > "$tmp"
    chmod 600 "$tmp"
    mv -f "$tmp" "$ROUTER_STEP_CURRENT_FILE"
    printf '%s\t%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" workflow complete all_phases_complete >> "$ROUTER_STEP_EVENTS_FILE"
    flock -u 8
    exec 8>&-
}

router_step_workflow_is_complete() { grep -qx 'WORKFLOW_RESULT=complete' "$ROUTER_STEP_CURRENT_FILE"; }

router_step_emit_stop_evidence() {
    local result="$1" phase="$2" line="$3" rc="$4"
    local command="${5:-unknown}" owner="${6:-VM130}" core="${7:-false}"
    local rollback="${8:-unknown}" latest="${9:-unknown}" next="${10:-inspect_run_log}"
    printf 'RESULT=%s\n' "$result"
    printf 'STOP_PHASE=%s\n' "$phase"
    printf 'STOP_LINE=%s\n' "$line"
    printf 'STOP_RC=%s\n' "$rc"
    printf 'STOP_MACHINE_OWNER=%s\n' "$owner"
    printf 'STOP_COMMAND=%q\n' "$command"
    printf 'CORE_CHANGE_COMPLETE=%s\n' "$core"
    printf 'ROLLBACK_STATUS=%s\n' "$rollback"
    printf 'LATEST_ARCHIVE=%s\n' "$latest"
    printf 'NEXT_DIAGNOSTIC_ACTION=%s\n' "$next"
}

router_step_error_classification() {
    local default_result="$1" phase="$2" line="$3" rc="$4"
    local command="${5:-unknown}" owner="${6:-VM130}" core="${7:-false}"
    local rollback="${8:-unknown}" latest="${9:-unknown}" next="${10:-inspect_run_log}"
    if router_step_workflow_is_complete || router_step_publication_is_verified; then
        printf 'RESULT=PASS_WORKFLOW_ALREADY_COMPLETE\n'
        printf 'LATE_ERROR_AFTER_COMPLETE=true\n'
        printf 'NO_SECOND_STOP_REPORT=true\n'
        printf 'LATE_ERROR_PHASE=%s\n' "$phase"
        printf 'LATE_ERROR_LINE=%s\n' "$line"
        printf 'LATE_ERROR_RC=%s\n' "$rc"
        printf 'LATE_ERROR_COMMAND=%q\n' "$command"
        return 0
    fi
    router_step_emit_stop_evidence "$default_result" "$phase" "$line" "$rc" "$command" "$owner" "$core" "$rollback" "$latest" "$next"
    return 1
}

router_step_publication_barrier_set() {
    local status="$1" detail="${2:-none}" tmp
    [[ "$status" =~ ^(none|committed|verified)$ ]] || return 2
    exec 8>"$ROUTER_STEP_LOCK_FILE"
    flock 8
    tmp="${ROUTER_STEP_CURRENT_FILE}.tmp.$$"
    awk -F= -v status="$status" '$1 == "PUBLICATION_BARRIER" { print "PUBLICATION_BARRIER=" status; next } { print }' "$ROUTER_STEP_CURRENT_FILE" > "$tmp"
    chmod 600 "$tmp"
    mv -f "$tmp" "$ROUTER_STEP_CURRENT_FILE"
    printf '%s\t%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" publication_barrier "$status" "$detail" >> "$ROUTER_STEP_EVENTS_FILE"
    flock -u 8
    exec 8>&-
}

router_step_publication_barrier_mark_committed() { router_step_publication_barrier_set committed "${1:-publisher_committed}"; }
router_step_publication_barrier_mark_verified() { router_step_publication_barrier_set verified "${1:-http_verified}"; }
router_step_publication_barrier_status() { awk -F= '$1=="PUBLICATION_BARRIER"{v=$2} END{print v}' "$ROUTER_STEP_CURRENT_FILE"; }
router_step_publication_is_verified() { [[ "$(router_step_publication_barrier_status)" == verified ]]; }
router_step_should_publish_stop() { ! router_step_publication_is_verified; }

router_step_archive_latest_python() {
    local base="${1:-${ROUTER_OPS_BASE:-/opt/router-ops}}"
    local incoming="${2:-/home/ops/incoming}"
    local timestamp="${3:-$(date -u +%Y%m%d-%H%M%S)}"
    local token_file="${ROUTER_PUBLIC_TOKEN_FILE:-$base/public/.router-public-token}"
    local token latest archive archive_sha discovered count
    latest=''
    if [[ -n "${ROUTER_PUBLIC_ROOT:-}" && -d "${ROUTER_PUBLIC_ROOT}/latest" ]]; then
        latest="${ROUTER_PUBLIC_ROOT}/latest"
    else
        token="$(cat "$token_file" 2>/dev/null || true)"
        if [[ -n "$token" && -d "$base/public/r/$token/latest" ]]; then
            latest="$base/public/r/$token/latest"
        else
            discovered="$(find "$base/public/r" -mindepth 2 -maxdepth 2 -type d -name latest -print 2>/dev/null || true)"
            count="$(printf '%s\n' "$discovered" | awk 'NF{n++} END{print n+0}')"
            [[ "$count" -eq 1 ]] || { echo RESULT=STOP_ROUTER_STEP_ARCHIVE_LATEST_UNRESOLVED; echo "LATEST_CANDIDATE_COUNT=$count"; return 71; }
            latest="$(printf '%s\n' "$discovered" | awk 'NF{print; exit}')"
        fi
    fi
    [[ -d "$latest" ]] || { echo RESULT=STOP_ROUTER_STEP_ARCHIVE_LATEST_MISSING; echo "LATEST=$latest"; return 72; }
    archive="$incoming/WG_PAID_MGTS_LATEST_${timestamp}.zip"
    mkdir -p "$incoming"
    python3 - "$latest" "$archive" <<'PYARCHIVE'
from pathlib import Path
import sys, zipfile
src=Path(sys.argv[1]); out=Path(sys.argv[2]); tmp=out.with_suffix(out.suffix+'.tmp')
with zipfile.ZipFile(tmp,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for p in sorted(src.rglob('*')):
        if p.is_file(): z.write(p,Path('latest')/p.relative_to(src))
tmp.replace(out)
PYARCHIVE
    archive_sha="$(sha256sum "$archive" | awk '{print $1}')"
    echo RESULT=PASS_ROUTER_STEP_ARCHIVE_LATEST
    echo "LATEST_ARCHIVE=$archive"
    echo "LATEST_ARCHIVE_SHA256=$archive_sha"
}

router_step_publish_payload() {
    local label="$1" report_dir="$2" payload_dir="$3" current="$4" events="$5" output_env="$6"
    local base="${ROUTER_OPS_BASE:-/opt/router-ops}"
    shift 6
    [[ -d "$report_dir" && -f "$current" && -f "$events" ]] || return 73
    "$base/bin/router-public-safe-payload" "$payload_dir" "$@"
    "$base/bin/router-step-finalize" \
        --label "$label" \
        --payload-dir "$payload_dir" \
        --workflow-current "$current" \
        --workflow-events "$events" \
        --output-env "$output_env" \
        --expect-kind step-report
}

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

router_step_zip_integrity_check() {
    local path="$1"
    [[ -f "$path" ]] || return 1
    python3 - "$path" <<'PYZIPCHECK'
import sys, zipfile
p=sys.argv[1]
try:
    with zipfile.ZipFile(p) as z:
        bad=z.testzip()
        if bad is not None:
            raise SystemExit(2)
except (zipfile.BadZipFile, OSError):
    raise SystemExit(3)
PYZIPCHECK
}

router_step_retain_bundle() {
    local bundle="$1" expected_sha="${2:-}" base="${3:-${ROUTER_OPS_BASE:-/opt/router-ops}}" incoming="${4:-/home/ops/incoming}"
    local actual step retained_root dest tmp bundle_real incoming_real removed=false
    [[ -f "$bundle" ]] || { echo RESULT=STOP_ROUTER_STEP_RETAIN_BUNDLE_MISSING; echo "BUNDLE=$bundle"; return 81; }
    router_step_zip_integrity_check "$bundle" || { echo RESULT=STOP_ROUTER_STEP_RETAIN_BUNDLE_CORRUPT; echo "BUNDLE=$bundle"; return 82; }
    actual="$(sha256sum "$bundle" | awk '{print $1}')"
    if [[ -n "$expected_sha" && "$actual" != "$expected_sha" ]]; then
        echo RESULT=STOP_ROUTER_STEP_RETAIN_BUNDLE_SHA_MISMATCH
        echo "EXPECTED_SHA256=$expected_sha"
        echo "ACTUAL_SHA256=$actual"
        return 83
    fi
    step="$(basename "$bundle" .zip)"
    [[ "$step" =~ ^[A-Za-z0-9_.-]+$ ]] || { echo RESULT=STOP_ROUTER_STEP_RETAIN_BUNDLE_STEP_NAME; echo "STEP=$step"; return 84; }
    retained_root="${ROUTER_RETAINED_STEP_ZIP_ROOT:-$base/state/retained-step-zips}/$step"
    mkdir -p "$retained_root"; chmod 700 "$retained_root"
    dest="$retained_root/${actual}.zip"
    if [[ -f "$dest" ]]; then
        echo "$actual  $dest" | sha256sum -c - >/dev/null || { echo RESULT=STOP_ROUTER_STEP_RETAIN_BUNDLE_EXISTING_SHA; echo "RETAINED_STEP_ZIP=$dest"; return 85; }
        router_step_zip_integrity_check "$dest" || { echo RESULT=STOP_ROUTER_STEP_RETAIN_BUNDLE_EXISTING_CORRUPT; echo "RETAINED_STEP_ZIP=$dest"; return 86; }
    else
        tmp="${dest}.tmp.$$"
        cp -- "$bundle" "$tmp"
        chmod 600 "$tmp"
        echo "$actual  $tmp" | sha256sum -c - >/dev/null
        router_step_zip_integrity_check "$tmp"
        mv -f "$tmp" "$dest"
    fi
    bundle_real="$(readlink -f "$bundle")"
    incoming_real="$(readlink -f "$incoming")"
    if [[ "$(dirname "$bundle_real")" == "$incoming_real" && "$bundle_real" != "$(readlink -f "$dest")" ]]; then
        rm -f -- "$bundle"
        removed=true
    fi
    echo RESULT=PASS_ROUTER_STEP_RETAIN_BUNDLE
    echo "STEP=$step"
    echo "RETAINED_STEP_ZIP=$dest"
    echo "RETAINED_STEP_ZIP_SHA256=$actual"
    echo "INCOMING_SOURCE_REMOVED=$removed"
}

router_step_retain_latest_file() {
    local file="$1" base="${2:-${ROUTER_OPS_BASE:-/opt/router-ops}}" incoming="${3:-/home/ops/incoming}"
    local actual root dest tmp file_real incoming_real removed=false
    [[ -f "$file" ]] || return 0
    router_step_zip_integrity_check "$file" || { echo RESULT=STOP_ROUTER_STEP_RETAIN_LATEST_CORRUPT; echo "LATEST_FILE=$file"; return 87; }
    actual="$(sha256sum "$file" | awk '{print $1}')"
    root="${ROUTER_RETAINED_LATEST_ROOT:-$base/state/retained-latest}/$actual"
    mkdir -p "$root"; chmod 700 "$root"
    dest="$root/$(basename "$file")"
    if [[ -f "$dest" ]]; then
        echo "$actual  $dest" | sha256sum -c - >/dev/null || { echo RESULT=STOP_ROUTER_STEP_RETAIN_LATEST_EXISTING_SHA; echo "RETAINED_LATEST=$dest"; return 88; }
        router_step_zip_integrity_check "$dest" || { echo RESULT=STOP_ROUTER_STEP_RETAIN_LATEST_EXISTING_CORRUPT; echo "RETAINED_LATEST=$dest"; return 89; }
    else
        tmp="${dest}.tmp.$$"
        cp -- "$file" "$tmp"
        chmod 600 "$tmp"
        echo "$actual  $tmp" | sha256sum -c - >/dev/null
        router_step_zip_integrity_check "$tmp"
        mv -f "$tmp" "$dest"
    fi
    file_real="$(readlink -f "$file")"
    incoming_real="$(readlink -f "$incoming")"
    if [[ "$(dirname "$file_real")" == "$incoming_real" && "$file_real" != "$(readlink -f "$dest")" ]]; then
        rm -f -- "$file"
        removed=true
    fi
    echo RESULT=PASS_ROUTER_STEP_RETAIN_LATEST
    echo "RETAINED_LATEST=$dest"
    echo "RETAINED_LATEST_SHA256=$actual"
    echo "INCOMING_SOURCE_REMOVED=$removed"
}

router_step_prune_incoming_latest() {
    local base="${1:-${ROUTER_OPS_BASE:-/opt/router-ops}}" incoming="${2:-/home/ops/incoming}" keep="$3"
    local file keep_real retained=0 final_count
    [[ -f "$keep" ]] || { echo RESULT=STOP_ROUTER_STEP_PRUNE_LATEST_KEEP_MISSING; echo "KEEP=$keep"; return 90; }
    keep_real="$(readlink -f "$keep")"
    while IFS= read -r -d '' file; do
        [[ "$(readlink -f "$file")" == "$keep_real" ]] && continue
        router_step_retain_latest_file "$file" "$base" "$incoming" >/dev/null
        retained=$((retained+1))
    done < <(find "$incoming" -maxdepth 1 -type f -name 'WG_PAID_MGTS_LATEST_*.zip' -print0 2>/dev/null)
    final_count="$(find "$incoming" -maxdepth 1 -type f -name 'WG_PAID_MGTS_LATEST_*.zip' -printf . 2>/dev/null | wc -c)"
    [[ "$final_count" -eq 1 ]] || { echo RESULT=STOP_ROUTER_STEP_PRUNE_LATEST_COUNT; echo "INCOMING_LATEST_COUNT=$final_count"; return 91; }
    echo RESULT=PASS_ROUTER_STEP_PRUNE_INCOMING_LATEST
    echo "PREVIOUS_LATEST_RETAINED_COUNT=$retained"
    echo "INCOMING_LATEST_COUNT=$final_count"
}

router_step_cleanup_stale_unpacks() {
    local base="${1:-${ROUTER_OPS_BASE:-/opt/router-ops}}" incoming="${2:-/home/ops/incoming}" current="${3:-}"
    local retained_root="${ROUTER_RETAINED_STEP_ZIP_ROOT:-$base/state/retained-step-zips}"
    local dir name rest step suffix removed=0 preserved=0 current_real=''
    [[ -n "$current" && -e "$current" ]] && current_real="$(readlink -f "$current")" || true
    shopt -s nullglob
    for dir in "$incoming"/.unpack-*; do
        [[ -d "$dir" ]] || continue
        if [[ -n "$current_real" && "$(readlink -f "$dir")" == "$current_real" ]]; then
            continue
        fi
        name="$(basename "$dir")"; rest="${name#.unpack-}"; suffix="${rest##*-}"; step="${rest%-*}"
        if [[ "$suffix" =~ ^[0-9]+$ && -d "$retained_root/$step" ]] && find "$retained_root/$step" -maxdepth 1 -type f -name '*.zip' -print -quit | grep -q .; then
            rm -rf -- "$dir"; removed=$((removed+1))
        else
            preserved=$((preserved+1))
        fi
    done
    shopt -u nullglob
    echo RESULT=PASS_ROUTER_STEP_CLEANUP_STALE_UNPACKS
    echo "STALE_UNPACK_REMOVED_COUNT=$removed"
    echo "STALE_UNPACK_PRESERVED_COUNT=$preserved"
}

router_step_archive_latest_python() {
    local base="${1:-${ROUTER_OPS_BASE:-/opt/router-ops}}"
    local incoming="${2:-/home/ops/incoming}"
    local timestamp="${3:-$(date -u +%Y%m%d-%H%M%S)}"
    local token_file="${ROUTER_PUBLIC_TOKEN_FILE:-$base/public/.router-public-token}"
    local token latest archive archive_sha discovered count prune_log
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
    mkdir -p "$incoming"
    archive="$incoming/WG_PAID_MGTS_LATEST_${timestamp}.zip"
    [[ ! -e "$archive" ]] || archive="$incoming/WG_PAID_MGTS_LATEST_${timestamp}-$$.zip"
    python3 - "$latest" "$archive" <<'PYARCHIVE'
from pathlib import Path
import sys, zipfile
src=Path(sys.argv[1]); out=Path(sys.argv[2]); tmp=Path(str(out)+'.tmp')
if tmp.exists(): tmp.unlink()
with zipfile.ZipFile(tmp,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for p in sorted(src.rglob('*')):
        if p.is_file(): z.write(p,Path('latest')/p.relative_to(src))
with zipfile.ZipFile(tmp) as z:
    bad=z.testzip()
    if bad is not None: raise SystemExit(f'CORRUPT_MEMBER={bad}')
tmp.replace(out)
PYARCHIVE
    router_step_zip_integrity_check "$archive" || { echo RESULT=STOP_ROUTER_STEP_ARCHIVE_LATEST_CORRUPT; echo "LATEST_ARCHIVE=$archive"; return 92; }
    archive_sha="$(sha256sum "$archive" | awk '{print $1}')"
    prune_log="${archive}.prune.$$"
    if ! router_step_prune_incoming_latest "$base" "$incoming" "$archive" > "$prune_log" 2>&1; then
        cat "$prune_log"; rm -f "$prune_log"; return 93
    fi
    cat "$prune_log"; rm -f "$prune_log"
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

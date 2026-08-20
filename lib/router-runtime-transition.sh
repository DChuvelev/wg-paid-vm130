#!/usr/bin/env bash
router_runtime_transition_init(){ local file="${1:?}"; install -d -m 0700 "$(dirname "$file")"; cat >"$file" <<'EOF'
TRANSFER_VERIFIED=false
PREFLIGHT_COMPLETE=false
BACKUP_READY=false
FILES_INSTALLED=false
CORE_CHANGE_COMPLETE=false
ACTIVATION_COMPLETE=false
EOF
chmod 0600 "$file"; }
router_runtime_transition_set(){ local file="${1:?}" key="${2:?}" value="${3:?}" tmp; case "$key" in TRANSFER_VERIFIED|PREFLIGHT_COMPLETE|BACKUP_READY|FILES_INSTALLED|CORE_CHANGE_COMPLETE|ACTIVATION_COMPLETE) ;; *) return 2;; esac; case "$value" in true|false) ;; *) return 2;; esac; tmp="${file}.tmp.$$"; awk -F= -v k="$key" -v v="$value" 'BEGIN{OFS="="} $1==k{$2=v;found=1}{print} END{if(!found) print k,v}' "$file" >"$tmp"; chmod 0600 "$tmp"; mv "$tmp" "$file"; }
router_runtime_transition_get(){ local file="${1:?}" key="${2:?}"; awk -F= -v k="$key" '$1==k{v=$2} END{print v}' "$file"; }
router_runtime_transition_rollback_allowed(){ local file="${1:?}"; [[ "$(router_runtime_transition_get "$file" BACKUP_READY)" == true ]] || return 1; [[ "$(router_runtime_transition_get "$file" CORE_CHANGE_COMPLETE)" != true ]] || return 1; }

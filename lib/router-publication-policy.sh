#!/usr/bin/env bash

ROUTER_PUBLIC_REFUSE_RE='raw|\.env|secret|secrets|private|Private|id_rsa|id_ed25519|authorized_keys|wg[0-9]?.conf|amnezia|hmn.*code|access.*code'

router_publication_label_sanitize() {
    printf '%s' "$1" | tr -c 'A-Za-z0-9_.-' '_' | sed 's/__*/_/g; s/^_//; s/_$//'
}

router_publication_classify() {
    local label="${1,,}"
    case "$label" in
      vm100_git_source_*) printf '%s	%s
' vm100 '*_vm100_git_source_*' ;;
      vm101_git_source_*) printf '%s	%s
' vm101 '*_vm101_git_source_*' ;;
      vm121_git_source_*) printf '%s	%s
' vm121 '*_vm121_git_source_*' ;;
      vm130_git_source_*) printf '%s	%s
' vm130-source '*_vm130_git_source_*' ;;
      local_architecture_plan_vm101_autonomous_hmn_recovery*) printf '%s	%s
' local-m07-plan '*_local_architecture_plan_vm101_autonomous_hmn_recovery*' ;;
      global_project_plan_wg_paid*) printf '%s	%s
' global-project-plan '*_global_project_plan_wg_paid*' ;;
      xs_map_*|access_map_*) printf '%s	%s
' access-map access-map-special ;;
      vm101_model_*) printf '%s	%s
' vm101-model '*_vm101_model_*' ;;
      vm101_methods_*) printf '%s	%s
' vm101-methods '*_vm101_methods_*' ;;
      vm130_router_ops_source_snapshot*) printf '%s	%s
' vm130-source '*_vm130_router_ops_source_snapshot*' ;;
      project_source_post_*) printf '%s	%s
' project-source-post '*_project_source_post_*' ;;
      step*|*_step*) printf '%s	%s
' step-report '*_step*' ;;
      *) printf 'label:%s	*_%s
' "$1" "$1" ;;
    esac
}

router_publication_name_allowed() {
    local value="$1"
    ! printf '%s
' "$value" | grep -Eiq "$ROUTER_PUBLIC_REFUSE_RE"
}

router_publication_direct_http_allowed() {
    case "$1" in
      index.html|*.html|*.htm) return 0 ;;
      *) return 1 ;;
    esac
}

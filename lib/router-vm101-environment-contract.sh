#!/usr/bin/env bash
router_vm101_machine_git_count(){ git -C / --git-dir=/root/.vm101-source.git --work-tree=/ ls-tree -r --full-tree --name-only HEAD | wc -l | tr -d ' '; }
router_real_path(){ readlink -f "${1:?}"; }
router_path_is_within(){ local root path; root="$(readlink -f "${1:?}")" || return 1; path="$(readlink -f "${2:?}")" || return 1; case "$path" in "$root"/*|"$root") return 0;; *) return 1;; esac; }
router_busybox_static_check(){ local file="${1:?}"; ! grep -Eq 'find[^\n]*(-printf|-quit)|grep[[:space:]]+-P|\[\[|\bmapfile\b|<\(' "$file"; }

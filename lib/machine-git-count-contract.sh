#!/usr/bin/env bash
# Canonical Machine Git count contract v1.
# All full-tree counts are independent of current working directory.

router_machine_git_tree_file_count() {
  local git_dir="$1" work_tree="$2" commit="$3"
  git --git-dir="$git_dir" --work-tree="$work_tree" \
    ls-tree -r --full-tree --name-only "$commit" |
    awk 'END { print NR + 0 }'
}

router_machine_git_archive_file_count() {
  local git_dir="$1" work_tree="$2" commit="$3"
  git --git-dir="$git_dir" --work-tree="$work_tree" \
    archive --format=tar "$commit" |
    tar -tf - |
    awk '!/\/$/ { n++ } END { print n + 0 }'
}

router_machine_git_changed_files() {
  local git_dir="$1" work_tree="$2" commit="$3"
  git --git-dir="$git_dir" --work-tree="$work_tree" \
    diff-tree --root --no-commit-id --name-only -r "$commit"
}

router_machine_git_changed_file_count() {
  local git_dir="$1" work_tree="$2" commit="$3"
  router_machine_git_changed_files "$git_dir" "$work_tree" "$commit" |
    awk 'NF { n++ } END { print n + 0 }'
}

router_machine_git_assert_count_contract() {
  local git_dir="$1" work_tree="$2" commit="$3"
  local tree_count archive_count
  tree_count="$(router_machine_git_tree_file_count "$git_dir" "$work_tree" "$commit")"
  archive_count="$(router_machine_git_archive_file_count "$git_dir" "$work_tree" "$commit")"
  [ "$tree_count" = "$archive_count" ] || {
    printf 'ERROR: git tree count %s != git archive count %s\n' "$tree_count" "$archive_count" >&2
    return 1
  }
  printf '%s\n' "$tree_count"
}

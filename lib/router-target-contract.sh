#!/usr/bin/env bash

router_target_matrix_validate() {
    local file="$1"
    [[ -f "$file" ]] || return 2
    awk -F '\t' '
      BEGIN{ok=1}
      /^[[:space:]]*($|#)/{next}
      NF!=4{ok=0; next}
      $1!~/^(VM130|PVE|VM100|VM101)$/{ok=0}
      $2!~/^(bash|posix-sh)$/{ok=0}
      seen[$1]++
      END{
        for(k in seen) if(seen[k]!=1) ok=0
        exit(ok?0:1)
      }
    ' "$file"
}

router_path_ownership_validate() {
    local file="$1"
    [[ -f "$file" ]] || return 2
    awk -F '\t' '
      BEGIN{ok=1}
      /^[[:space:]]*($|#)/{next}
      NF!=4{ok=0; next}
      $1!~/^(VM130|PVE|VM100|VM101)$/{ok=0}
      $2!~/^(durable|runtime|optional)$/{ok=0}
      $3!~/^(true|false)$/{ok=0}
      $4!~/^\//{ok=0}
      $2=="optional" && $3=="true"{ok=0}
      seen[$1 SUBSEP $4]++
      END{
        for(k in seen) if(seen[k]!=1) ok=0
        exit(ok?0:1)
      }
    ' "$file"
}

router_path_owner_assert() {
    local file="$1" execution_owner="$2" path="$3" declared
    router_path_ownership_validate "$file" || return 2
    declared="$(awk -F '\t' -v p="$path" '$0!~/^[[:space:]]*($|#)/ && $4==p{print $1; exit}' "$file")"
    [[ -n "$declared" ]] || {
      printf 'PATH_OWNER_RESULT=UNKNOWN\nPATH=%s\n' "$path"
      return 3
    }
    [[ "$declared" == "$execution_owner" ]] || {
      printf 'PATH_OWNER_RESULT=MISMATCH\nPATH=%s\nDECLARED_OWNER=%s\nEXECUTION_OWNER=%s\n' "$path" "$declared" "$execution_owner"
      return 4
    }
    printf 'PATH_OWNER_RESULT=PASS\nPATH=%s\nOWNER=%s\n' "$path" "$execution_owner"
}

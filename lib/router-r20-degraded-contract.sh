#!/usr/bin/env bash
R20_CONTRACT_VERSION='r20b-v1'
R20_STATE_SCHEMA_VERSION='1'
R20_DEFAULT_RETRY_INTERVAL_SEC='1800'
R20_RETRY_TICK_INTERVAL_SEC='60'
R20_MIN_HEALTHY_SLOTS_FOR_DEGRADED='1'
R20_REQUIRED_STATE_KEYS=(
  mode degraded_reason degraded_since_epoch failed_attempt_id
  last_refresh_result last_refresh_epoch next_refresh_epoch
  refresh_retry_count last_retry_epoch last_retry_result
  active_generation_id healthy_slot_count_at_failure
)
R20_LOCK_ORDER=(
  /var/lib/router-egress-recovery/locks/recovery-coordinator.lock
  /var/lib/router-egress-recovery/locks/local-repair.lock_or_full-pool-refresh.lock
  /var/lib/router-egress-recovery/generations/.builder.lock
)
R20_REQUIRED_FUNCTIONAL_FIXTURES=(
  degraded_failure_preserves_active.sh
  retry_before_due_rejected.sh
  retry_at_due_allowed.sh
  retry_lock_concurrency.sh
  degraded_state_survives_boot.sh
  successful_retry_returns_normal.sh
  counter_reset_after_activation_only.sh
  direct_failopen_disabled.sh
)
r20_mode_valid() { case "${1:-}" in NORMAL|LOCAL_REPAIR|FULL_POOL_REFRESH|DEGRADED_POOL) return 0;; *) return 1;; esac; }
r20_retry_due() {
  local now
  local next
  now="${1:?}"
  next="${2:?}"
  [[ "$now" =~ ^[0-9]+$ && "$next" =~ ^[0-9]+$ ]] || return 2
  (( now >= next ))
}
r20_snapshot_name_valid() { local name; name="${1:?}"; (( ${#name} <= 32 )); }

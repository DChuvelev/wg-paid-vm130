#!/usr/bin/env bash
set -Eeuo pipefail

IFACE="wg-paid-e2e"
CLIENT_IP="10.253.254.$((RANDOM % 180 + 40))"
SERVER_WG_IP="10.253.1.1"
SERVER_ENDPOINT="10.71.100.1:51830"

RU_IP="77.88.8.8"
NONRU_IP1="1.1.1.1"
NONRU_IP2="9.9.9.9"

LOG_DIR="/opt/router-ops/state/wg-paid/logs"
LOG="$LOG_DIR/wg-paid-e2e-smoke-$(date +%Y%m%d-%H%M%S).log"
TMP="$(mktemp -d)"

mkdir -p "$LOG_DIR"

CLIENT_PUB=""

run_vm100() {
  ssh pve-mgts "ssh root@10.71.100.1 '$*'"
}

cleanup() {
  set +e

  echo
  echo "== cleanup =="

  sudo ip route del "$SERVER_WG_IP/32" dev "$IFACE" 2>/dev/null || true
  sudo ip route del "$RU_IP/32" dev "$IFACE" 2>/dev/null || true
  sudo ip route del "$NONRU_IP1/32" dev "$IFACE" 2>/dev/null || true
  sudo ip route del "$NONRU_IP2/32" dev "$IFACE" 2>/dev/null || true

  if [ -n "${ECHO_IP:-}" ]; then
    sudo ip route del "$ECHO_IP/32" dev "$IFACE" 2>/dev/null || true
  fi

  sudo ip link del "$IFACE" 2>/dev/null || true

  if [ -n "${CLIENT_PUB:-}" ]; then
    echo "removing server peer: $CLIENT_PUB"
    ssh pve-mgts "ssh root@10.71.100.1 'wg set wg_paid peer \"$CLIENT_PUB\" remove'" || true
  fi

  rm -rf "$TMP"

  echo "cleanup done"
}
trap cleanup EXIT INT TERM

{
  echo "== wg_paid E2E smoke =="
  date
  echo "client_ip=$CLIENT_IP"
  echo "server_endpoint=$SERVER_ENDPOINT"
  echo

  echo "== prerequisites =="
  command -v wg
  command -v ip
  command -v ping
  command -v curl || true
  echo

  echo "== fetch server public key from MGTS VM100 wg_paid =="
  SERVER_PUB="$(ssh pve-mgts "ssh root@10.71.100.1 'wg show wg_paid public-key'")"
  echo "server_pub_loaded=yes"
  echo

  echo "== generate temporary client key =="
  umask 077
  PRIV="$TMP/client.key"
  PUB="$TMP/client.pub"
  wg genkey | tee "$PRIV" | wg pubkey > "$PUB"
  CLIENT_PUB="$(cat "$PUB")"
  echo "client_pub=$CLIENT_PUB"
  echo

  echo "== clean old local iface if any =="
  sudo ip link del "$IFACE" 2>/dev/null || true
  echo

  echo "== add runtime peer on MGTS VM100 via wg set =="
  ssh pve-mgts "ssh root@10.71.100.1 wg set wg_paid peer '$CLIENT_PUB' allowed-ips '$CLIENT_IP/32'"
  echo "server_peer_added=yes"
  echo

  echo "== create local temporary WG client interface =="
  sudo ip link add dev "$IFACE" type wireguard
  sudo ip addr add "$CLIENT_IP/32" dev "$IFACE"
  sudo wg set "$IFACE" \
    private-key "$PRIV" \
    peer "$SERVER_PUB" \
    endpoint "$SERVER_ENDPOINT" \
    allowed-ips 0.0.0.0/0 \
    persistent-keepalive 25
  sudo ip link set mtu 1280 up dev "$IFACE"
  echo

  echo "== add only test host routes through temporary wg_paid client =="
  sudo ip route replace "$SERVER_WG_IP/32" dev "$IFACE"
  sudo ip route replace "$RU_IP/32" dev "$IFACE"
  sudo ip route replace "$NONRU_IP1/32" dev "$IFACE"
  sudo ip route replace "$NONRU_IP2/32" dev "$IFACE"
  ip -br addr show "$IFACE"
  echo

  echo "== VM100 PBR route decision for wg_paid source =="
  ssh pve-mgts "ssh root@10.71.100.1 '
    echo \"non-RU $NONRU_IP1 from $CLIENT_IP mark VPN:\"
    ip route get $NONRU_IP1 from $CLIENT_IP iif wg_paid mark 0x20000 2>&1 | head -1

    echo
    echo \"RU $RU_IP from $CLIENT_IP mark DIRECT:\"
    ip route get $RU_IP from $CLIENT_IP iif wg_paid mark 0x10000 2>&1 | head -1
  '"
  echo

  echo "== optional tcpdump capture on VM100 if tcpdump exists =="
  ssh pve-mgts "ssh root@10.71.100.1 '
    if command -v tcpdump >/dev/null 2>&1; then
      rm -f /tmp/wgpaid-smoke-eth0.log /tmp/wgpaid-smoke-eth2.log
      timeout 15 tcpdump -ni eth0 -c 20 \"icmp and (host $RU_IP or host $NONRU_IP1 or host $NONRU_IP2)\" >/tmp/wgpaid-smoke-eth0.log 2>&1 &
      timeout 15 tcpdump -ni eth2 -c 20 \"icmp and (host $RU_IP or host $NONRU_IP1 or host $NONRU_IP2)\" >/tmp/wgpaid-smoke-eth2.log 2>&1 &
      echo tcpdump_started
    else
      echo no_tcpdump_on_VM100
    fi
  '"
  sleep 1
  echo

  echo "== handshake with wg_paid server =="
  ping -4 -I "$IFACE" -c 3 -W 2 "$SERVER_WG_IP"
  echo
  ssh pve-mgts "ssh root@10.71.100.1 'wg show wg_paid | sed -n \"1,80p\"'"
  echo

  echo "== actual ping tests through wg_paid client =="
  echo "-- RU/direct candidate: $RU_IP --"
  ping -4 -I "$IFACE" -c 4 -W 2 "$RU_IP"
  echo

  echo "-- non-RU/VPN candidate: $NONRU_IP1 --"
  ping -4 -I "$IFACE" -c 4 -W 2 "$NONRU_IP1"
  echo

  echo "-- non-RU/VPN candidate: $NONRU_IP2 --"
  ping -4 -I "$IFACE" -c 4 -W 2 "$NONRU_IP2"
  echo

  echo "== optional external IP via wg_paid non-RU path =="
  ECHO_HOST="ifconfig.co"
  ECHO_IP="$(getent ahostsv4 "$ECHO_HOST" | awk '{print $1; exit}' || true)"
  echo "echo_host=$ECHO_HOST"
  echo "echo_ip=${ECHO_IP:-none}"

  if [ -n "${ECHO_IP:-}" ]; then
    sudo ip route replace "$ECHO_IP/32" dev "$IFACE"
    curl -4 --interface "$IFACE" --resolve "$ECHO_HOST:443:$ECHO_IP" \
      -sS --connect-timeout 8 --max-time 20 "https://$ECHO_HOST/json" || true
    echo
  fi

  echo "== VM100 tcpdump result if available =="
  ssh pve-mgts "ssh root@10.71.100.1 '
    echo \"--- eth0 / transit_direct candidate ---\"
    sed -n \"1,80p\" /tmp/wgpaid-smoke-eth0.log 2>/dev/null || true
    echo
    echo \"--- eth2 / transit_vpn candidate ---\"
    sed -n \"1,80p\" /tmp/wgpaid-smoke-eth2.log 2>/dev/null || true
  '"
  echo

  echo "== final PASS conditions =="
  echo "PASS if:"
  echo "  1. ping $SERVER_WG_IP via $IFACE succeeds"
  echo "  2. ping $RU_IP via $IFACE succeeds"
  echo "  3. ping $NONRU_IP1 / $NONRU_IP2 via $IFACE succeeds"
  echo "  4. VM100 route decision says RU -> transit_direct and non-RU -> transit_vpn"
  echo "  5. optional tcpdump, if present, shows RU on eth0 and non-RU on eth2"
  echo
  echo "SMOKE_DONE"

} 2>&1 | tee "$LOG"

echo
echo "log saved: $LOG"

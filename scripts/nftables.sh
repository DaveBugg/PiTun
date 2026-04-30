#!/usr/bin/env bash
# Manual nftables management for PiTun
# Usage: ./nftables.sh [apply|flush|status|bypass-mac <mac>]

set -euo pipefail

TABLE="pitun"
TPROXY_TCP="${TPROXY_TCP:-7893}"
TPROXY_UDP="${TPROXY_UDP:-7894}"
DNS_PORT="${DNS_PORT:-5353}"

cmd="${1:-status}"

case "$cmd" in
    apply)
        echo "Applying nftables TPROXY rules…"
        nft -f - <<EOF
table inet $TABLE {
    set bypass_mac {
        type ether_addr
    }
    set bypass_dst4 {
        type ipv4_addr
        flags interval
        elements = {
            0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10,
            127.0.0.0/8, 169.254.0.0/16, 172.16.0.0/12,
            192.168.0.0/16, 224.0.0.0/4, 240.0.0.0/4,
            255.255.255.255/32
        }
    }

    chain prerouting {
        type filter hook prerouting priority mangle - 1; policy accept;
        meta mark 255 return
        ether saddr @bypass_mac return
        ip daddr @bypass_dst4 return
        meta l4proto { tcp, udp } th dport 53 tproxy to 127.0.0.1:$DNS_PORT meta mark set 1
        ip protocol tcp tproxy to 127.0.0.1:$TPROXY_TCP meta mark set 1
        ip protocol udp tproxy to 127.0.0.1:$TPROXY_UDP meta mark set 1
    }

    chain output {
        type route hook output priority mangle; policy accept;
        meta mark 255 return
        ip daddr @bypass_dst4 return
        ip protocol tcp meta mark set 1
        ip protocol udp meta mark set 1
    }
}
EOF
        ip rule del fwmark 1 lookup 100 2>/dev/null || true
        ip rule add fwmark 1 lookup 100
        ip route replace local 0.0.0.0/0 dev lo table 100 2>/dev/null || true
        echo "Done."
        ;;

    flush)
        echo "Flushing nftables TPROXY rules…"
        nft delete table inet $TABLE 2>/dev/null || echo "Table not found"
        ip rule del fwmark 1 lookup 100 2>/dev/null || true
        echo "Done."
        ;;

    status)
        echo "=== nftables table ==="
        nft list table inet $TABLE 2>/dev/null || echo "(not active)"
        echo ""
        echo "=== ip rules ==="
        ip rule list | grep -E "fwmark|100" || echo "(none)"
        echo ""
        echo "=== ip route table 100 ==="
        ip route list table 100 2>/dev/null || echo "(empty)"
        ;;

    bypass-mac)
        mac="${2:-}"
        [[ -z "$mac" ]] && { echo "Usage: $0 bypass-mac <mac-address>"; exit 1; }
        echo "Adding $mac to bypass_mac set…"
        nft add element inet $TABLE bypass_mac "{ $mac }"
        echo "Done."
        ;;

    *)
        echo "Usage: $0 [apply|flush|status|bypass-mac <mac>]"
        exit 1
        ;;
esac

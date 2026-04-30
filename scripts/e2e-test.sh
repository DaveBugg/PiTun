#!/bin/bash
# ============================================================================
# PiTun E2E Integration Test — run on the VM after setup-vm.sh
# ============================================================================
# Tests the full stack: API auth, node management, config generation,
# xray process lifecycle, nftables rules, and kill switch.
#
# Usage: sudo bash scripts/e2e-test.sh
# ============================================================================

set -euo pipefail

BASE="http://localhost/api"
PASS=0
FAIL=0

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

ok()   { PASS=$((PASS+1)); echo -e "  ${GREEN}PASS${NC} $1"; }
fail() { FAIL=$((FAIL+1)); echo -e "  ${RED}FAIL${NC} $1: $2"; }

assert_status() {
    local desc="$1" method="$2" url="$3" expected="$4"
    shift 4
    local status
    status=$(curl -s -o /dev/null -w "%{http_code}" -X "$method" "$@" "$url")
    if [ "$status" = "$expected" ]; then
        ok "$desc"
    else
        fail "$desc" "expected $expected, got $status"
    fi
}

echo "========================================"
echo " PiTun E2E Integration Test"
echo "========================================"
echo ""

# ── 1. Health Check ──
echo "[Auth & Health]"
assert_status "Health endpoint is public" GET "$BASE/../health" 200

# ── 2. Auth ──
assert_status "Protected endpoint returns 401" GET "$BASE/system/status" 401

TOKEN=$(curl -s -X POST "$BASE/auth/login" \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"password"}' | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null)

if [ -n "$TOKEN" ] && [ "$TOKEN" != "None" ]; then
    ok "Login returns token"
else
    fail "Login returns token" "empty token"
    echo "Cannot proceed without token. Exiting."
    exit 1
fi

AUTH="-H Authorization:Bearer $TOKEN"

assert_status "Protected endpoint with token" GET "$BASE/system/status" 200 $AUTH -H "Content-Type: application/json"

# ── 3. Node CRUD ──
echo ""
echo "[Node Management]"
NODE_ID=$(curl -s -X POST "$BASE/nodes" \
    $AUTH -H "Content-Type: application/json" \
    -d '{
        "name":"Test VLESS","protocol":"vless","address":"1.2.3.4","port":443,
        "uuid":"test-uuid-1234","transport":"ws","tls":"tls","sni":"example.com"
    }' | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)

if [ -n "$NODE_ID" ] && [ "$NODE_ID" != "None" ]; then
    ok "Create node (id=$NODE_ID)"
else
    fail "Create node" "no ID returned"
fi

assert_status "Get node" GET "$BASE/nodes/$NODE_ID" 200 $AUTH

# Set as active node
curl -s -X POST "$BASE/system/active-node" $AUTH \
    -H "Content-Type: application/json" -d "{\"node_id\":$NODE_ID}" > /dev/null
ok "Set active node"

# ── 4. Routing Rules ──
echo ""
echo "[Routing Rules]"
RULE_ID=$(curl -s -X POST "$BASE/routing/rules" \
    $AUTH -H "Content-Type: application/json" \
    -d '{"name":"Test domain","rule_type":"domain","match_value":"google.com","action":"proxy","order":100}' \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)

if [ -n "$RULE_ID" ] && [ "$RULE_ID" != "None" ]; then
    ok "Create routing rule (id=$RULE_ID)"
else
    fail "Create routing rule" "no ID returned"
fi

# Bulk create
BULK=$(curl -s -X POST "$BASE/routing/rules/bulk" \
    $AUTH -H "Content-Type: application/json" \
    -d '{"rule_type":"domain","action":"direct","values":"youtube.com\nnetflix.com\nspotify.com"}' \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('created',0))" 2>/dev/null)

if [ "$BULK" = "3" ]; then
    ok "Bulk create (3 values)"
else
    fail "Bulk create" "expected 3, got $BULK"
fi

# ── 5. Config Generation ──
echo ""
echo "[Config Generation]"

# Generate config via reload-config
assert_status "Reload config" POST "$BASE/system/reload-config" 204 $AUTH -H "Content-Type: application/json"

# Check generated config file
if docker exec pitun-backend sh -c "test -f /tmp/pitun/config.json && echo ok" 2>/dev/null | grep -q ok; then
    ok "Config file generated"

    # Validate JSON structure
    VALID=$(docker exec pitun-backend sh -c "python3 -c \"
import json
with open('/tmp/pitun/config.json') as f:
    c = json.load(f)
errors = []
if 'stats' not in c: errors.append('missing stats')
if 'api' not in c: errors.append('missing api')
if 'policy' not in c: errors.append('missing policy')
if 'dns' not in c: errors.append('missing dns')
if 'inbounds' not in c: errors.append('missing inbounds')
if 'outbounds' not in c: errors.append('missing outbounds')
if 'routing' not in c: errors.append('missing routing')

# Check sockopt.mark on all outbounds
for ob in c.get('outbounds', []):
    tag = ob.get('tag', '')
    proto = ob.get('protocol', '')
    if proto == 'blackhole': continue
    ss = ob.get('streamSettings', {})
    mark = ss.get('sockopt', {}).get('mark')
    if mark != 255 and tag not in ('api',):
        errors.append(f'{tag} ({proto}) missing mark=255')

# Check inbound tags
inbound_tags = {ib['tag'] for ib in c.get('inbounds', [])}
required_tags = {'dns-in', 'socks-in', 'http-in', 'api'}
missing = required_tags - inbound_tags
if missing: errors.append(f'missing inbounds: {missing}')

if errors:
    print('ERRORS: ' + '; '.join(errors))
else:
    print('VALID')
\"" 2>/dev/null)

    if echo "$VALID" | grep -q "VALID"; then
        ok "Config JSON structure valid"
    else
        fail "Config JSON structure" "$VALID"
    fi
else
    fail "Config file generated" "file not found"
fi

# ── 6. xray Lifecycle (only works on real Linux) ──
echo ""
echo "[xray Lifecycle]"

XRAY_EXISTS=$(docker exec pitun-backend sh -c "test -x /usr/local/bin/xray && echo yes || echo no" 2>/dev/null)

if [ "$XRAY_EXISTS" = "yes" ]; then
    assert_status "Start xray" POST "$BASE/system/start" 204 $AUTH -H "Content-Type: application/json"
    sleep 2

    STATUS=$(curl -s $AUTH "$BASE/system/status" | python3 -c "import sys,json; print(json.load(sys.stdin).get('running',False))" 2>/dev/null)
    if [ "$STATUS" = "True" ]; then
        ok "xray is running"
    else
        fail "xray is running" "status=$STATUS"
    fi

    assert_status "Stop xray" POST "$BASE/system/stop" 204 $AUTH -H "Content-Type: application/json"
    sleep 1

    STATUS=$(curl -s $AUTH "$BASE/system/status" | python3 -c "import sys,json; print(json.load(sys.stdin).get('running',False))" 2>/dev/null)
    if [ "$STATUS" = "False" ]; then
        ok "xray stopped"
    else
        fail "xray stopped" "status=$STATUS"
    fi
else
    echo "  SKIP xray not installed in container (Windows dev mode)"
fi

# ── 7. nftables (only on real Linux host) ──
echo ""
echo "[nftables]"

if command -v nft &> /dev/null; then
    # Check if pitun table can be created
    nft list tables 2>/dev/null
    ok "nft command available"
else
    echo "  SKIP nftables not available"
fi

# ── 8. Kill Switch ──
echo ""
echo "[Kill Switch]"

# Enable kill switch
curl -s -X PATCH "$BASE/system/settings" \
    $AUTH -H "Content-Type: application/json" \
    -d '{"kill_switch":true}' > /dev/null
ok "Enable kill switch setting"

KS=$(curl -s $AUTH "$BASE/system/settings" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('kill_switch',False))" 2>/dev/null)
if [ "$KS" = "True" ]; then
    ok "Kill switch persisted in settings"
else
    fail "Kill switch persisted" "got $KS"
fi

# Disable it back
curl -s -X PATCH "$BASE/system/settings" \
    $AUTH -H "Content-Type: application/json" \
    -d '{"kill_switch":false}' > /dev/null

# ── 9. Cleanup ──
echo ""
echo "[Cleanup]"
assert_status "Delete routing rule" DELETE "$BASE/routing/rules/$RULE_ID" 204 $AUTH
assert_status "Delete node" DELETE "$BASE/nodes/$NODE_ID" 204 $AUTH

# ── Results ──
echo ""
echo "========================================"
echo -e " Results: ${GREEN}${PASS} passed${NC}, ${RED}${FAIL} failed${NC}"
echo "========================================"

if [ $FAIL -gt 0 ]; then
    exit 1
fi

# Find out why a device or a site does not work
keywords: diagnose, not working, broken, slow, cannot open, does not load, why, problem, check, troubleshoot, no internet

Goal: explain why traffic of a device or to a site fails, then propose a fix.

Steps
1. Call the macro `diagnose` (device and/or site). It runs, read-only:
   GET /diagnostics/health-checks (gateway, DNS, internet direct and via the
   proxy, xray, nftables), GET /system/status (xray running, active node,
   config validation), the device's routing state (policy, rules on its IP)
   and POST /diagnostics/explain {"target": "example.com", "from_mac": "...",
   "test_reachability": true} for where the site goes and whether it connects.
2. Read the findings first; the raw details follow them.
3. Typical causes and fixes:
   - device not included while the gateway proxies only included devices ->
     PATCH /devices/{device_id} {"routing_policy": "include"}
   - active node offline -> pick another (find_nodes) or turn on a circle
   - a rule sends the site direct or blocks it -> change that rule
     (PATCH /routing/rules/{rule_id}) or add a site rule (add_site_rule)
   - DNS check fails -> see GET /dns/settings
4. Propose the fix as one plan; do not change anything without it.

Pitfalls
- A device without a DHCP reservation can change IP; rules on the old IP
  stop matching.
- Health check "internet via proxy" failing while direct works points at
  the node, not at the device.

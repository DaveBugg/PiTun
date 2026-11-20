# Add a routing rule for a website or service
keywords: site, website, domain, service, rule, routing rule, direct, proxy, block, bypass, unblock, through node

Goal: traffic to a site goes direct, through the proxy, through a specific
node, or is blocked.

Steps
1. Work out the domains. Use what you know about the service (main domain,
   CDN, API, auth and media domains), plus what the LAN really asked for:
   GET /domain-stats?q=<word> when domain stats are on. Prefer suffix
   entries ("example.com" matches all subdomains). A geosite tag
   ("geosite:netflix") is better when one exists for the service; search
   for it in the geodata tags only if unsure.
2. Decide the action: "direct" | "proxy" (active node / circle) | "block" |
   "node:<id>" (use `find_nodes`) | "balancer:<id>".
3. Read GET /routing/rules: is there already a domain rule for these domains?
   Extend or update it instead of duplicating. Note the `order` of rules that
   could catch the same traffic first (lower order wins).
4. act: POST /routing/rules {"name": "<service>", "rule_type": "domain",
   "match_value": "example.com,cdn.example.net,geosite:example",
   "action": "...", "order": 50, "enabled": true}
   For a single device only: put the rule in that device's routing set
   (routing_set_id), creating the set if needed.

Pitfalls
- List the domains in the plan summary so the user can see them before saving.
- Bare entries become suffix matches ("domain:"), "full:" for an exact host,
  "keyword:" for a substring.
- QUIC/HTTP3 may bypass a domain rule on some clients; mention it if the user
  reports the rule "not working" for video sites.

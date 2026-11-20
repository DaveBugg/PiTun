# Build a node circle from a subscription, excluding countries
keywords: circle, nodecircle, rotate, rotation, subscription, exclude country, countries, best, fastest, auto switch

Goal: a circle that rotates the active node among a subscription's nodes,
optionally without some countries, in a chosen mode.

Steps
1. Find the subscription: GET /subscriptions (match by name; ask if unclear).
2. Get its nodes: `find_nodes` with `subscription` set to the subscription name
   (k up to 100), or GET /nodes?... Note each node's `country`.
3. Build node_ids = enabled nodes of that subscription whose country is not
   excluded. The user names countries in words ("no Russia, no Belarus") ->
   ISO codes ["RU", "BY"].
4. Check GET /nodecircle for a circle with the same name or linked to the same
   subscription; update it (PATCH /nodecircle/{id}) rather than creating a twin.
5. act: POST /nodecircle with
   {"name": "...", "enabled": true, "mode": "best" | "random" | "sequential",
    "node_ids": [...], "interval_min": 10, "interval_max": 25,
    "max_latency_ms": 1500, "min_speed_mbps": 50,
    "subscription_ids": [<sub id>], "subscription_id": <sub id>,
    "excluded_countries": ["RU"], "excluded_node_ids": [],
    "rotate_when_healthy": false}
   Modes: "best" = pick the best by latency/speed (filters apply only here),
   "random", "sequential". Linking the subscription keeps membership in sync on
   every refresh; excluded_countries keeps those countries out for good.

Pitfalls
- A circle needs at least 2 nodes.
- Only one circle should normally be enabled: an enabled circle drives the
  active node. If another enabled circle exists, say so and offer to disable it
  in the same plan (PATCH /nodecircle/{id} {"enabled": false}).
- "rotate_when_healthy": false keeps a healthy active node on scheduled ticks;
  set true only if the user wants a change every interval.

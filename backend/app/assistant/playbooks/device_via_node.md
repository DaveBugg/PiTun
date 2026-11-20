# Route a device through a specific node
keywords: device, phone, tv, laptop, computer, through node, via node, use node, route device, client, src_ip

Goal: all traffic of one LAN device leaves through one chosen node.

How PiTun does it: a routing rule `rule_type: "src_ip"` with the device's LAN IP
and `action: "node:<node_id>"`. MAC rules cannot do this (MAC only works for
"direct" bypass in nftables).

Steps
1. `find_devices` with the user's description -> device id, ip, mac, online.
   Ambiguous -> ask. No IP -> the device has not been seen; ask the user to
   connect it, or to run a scan (POST /devices/scan).
2. `find_nodes` with the description of the node -> node id. Prefer online
   nodes; mention latency/speed in your answer.
3. Read the current rules: GET /routing/rules. Look for an existing src_ip rule
   for the same IP; update it (PATCH /routing/rules/{id}) instead of adding a
   duplicate.
4. Read GET /system/settings -> `device_routing_mode`:
   - "all": fine.
   - "include_only": the device must have routing_policy "include"
     (PATCH /devices/{id} {"routing_policy": "include"}), else it is not proxied at all.
   - "exclude_list": if the device has routing_policy "exclude" it bypasses the
     proxy entirely; set it to "default".
5. act, one plan:
   - POST /routing/rules {"name": "<device> via <node>", "rule_type": "src_ip",
     "match_value": "<device ip>", "action": "node:<id>", "order": 10, "enabled": true}
   - plus the device policy change from step 4 if needed.
   Low `order` = higher priority; 10 keeps it above typical domain rules. If the
   user wants only some sites through the node, use rule_type "domain" with
   those domains instead, scoped to the device via a routing set.

Pitfalls
- The rule follows the IP. If the device has no DHCP reservation
  (`dhcp_reserved_ip` empty) its IP can change; say so and offer to reserve it
  when router mode is on (PATCH /devices/{id} {"dhcp_reserved_ip": "<ip>"}).
- xray reloads automatically after the rule is saved.

Verify after execution: GET /routing/rules shows the rule; tell the user to
check the device's traffic on the Dashboard / Domains page.

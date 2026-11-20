# Install x-ui on a VPS and issue nodes from it
keywords: vps, server, x-ui, 3x-ui, xui, panel, deploy, install, inbound, client, issue nodes, new nodes, reality

Goal: a fresh VPS becomes an x-ui panel managed by PiTun, and its clients
become PiTun nodes.

Steps
1. The VPS must be on the Servers page with working SSH (the user adds it
   there; never ask for passwords or keys in the chat). GET /servers lists
   them; POST /servers/{server_id}/test checks SSH.
2. Prefer the macro `deploy_server` (server, panel "xui", optional domain).
   Without a domain the panel is bare 3x-ui for Reality; with a domain that
   already points at the VPS it is x-ui-pro (nginx + Let's Encrypt).
   Underneath: POST /servers/{server_id}/deploy {"protocol": "xui",
   "config": {"domain": ""}} -> 202 with a job id. Progress:
   GET /server-tasks/{job_id}. The chat follows the job after approval.
3. After the install the panel shows up in GET /xui/servers.
4. Prefer the macro `issue_nodes` (server, count, optional inbound or
   preset). Underneath, per node:
   POST /xui/servers/{xui_server_id}/inbounds {"preset_id": "vless-reality-vision",
   "values": {"port": 8443}} (only when no inbound fits; presets:
   GET /xui/presets; free ports: GET /xui/servers/{xui_server_id}/ports),
   POST /xui/servers/{xui_server_id}/inbounds/{inbound_id}/clients {"label": "pi-xxxx"},
   POST /xui/servers/{xui_server_id}/inbounds/{inbound_id}/clients/{client_id}/export-node
   with client_id = the label.

Pitfalls
- One x-ui per server: deploying again over a deployed panel is refused;
  uninstall on the Servers page first.
- Domain presets (vless-ws, vless-xhttp, trojan-grpc) need a panel installed
  with a domain. On x-ui-pro, port 443 belongs to nginx.
- Issued nodes are not removed by Undo; clients are removed on the x-ui page.

"""Mock upstream tasks-mcp (contrat serveur) pour smoke local tasks-gateway-rs.

- Sans auth (comme le Python : isolation systemd), enregistre les en-tetes
  recus (acteur/mode/versions) dans /tmp/mock-tasks-headers.json.
- Sert initialize / tools/list (16 outils) / tools/call tasks_today /
  resources/list / prompts/list.
- Usage: python mock_upstream.py <port>
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 18791

TOOLS = [
    {"name": n, "description": "d", "inputSchema": {"type": "object", "properties": {}}}
    for n in [
        "tasks_get", "tasks_history", "tasks_inbox", "tasks_list", "tasks_overdue",
        "tasks_recently_changed", "tasks_search", "tasks_today", "tasks_unscheduled",
        "tasks_upcoming", "tasks_complete", "tasks_create", "tasks_delete",
        "tasks_move", "tasks_reopen", "tasks_update",
    ]
]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.send_header("mcp-session-id", "mock-tasks-session")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/mcp":
            self.send_response(404)
            self.end_headers()
            return
        ln = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(ln) or b"{}")
        except ValueError:
            self._send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse"}})
            return
        with open("/tmp/mock-tasks-headers.json", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "acteur": self.headers.get("x-tasks-mcp-acteur"),
                "mode": self.headers.get("x-tasks-mcp-mode"),
                "version": self.headers.get("mcp-protocol-version"),
                "method": (data.get("method") if isinstance(data, dict) else None),
            }) + "\n")
        rid = data.get("id")
        method = data.get("method")
        if method == "initialize":
            self._send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": "2025-11-25",
                "serverInfo": {"name": "tasks-mcp", "version": "test"},
                "capabilities": {"tools": {}}}})
        elif method == "tools/list":
            self._send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        elif method == "tools/call" and (data.get("params") or {}).get("name") == "tasks_today":
            self._send({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": "aucune tache"}]}})
        elif method in ("resources/list",):
            self._send({"jsonrpc": "2.0", "id": rid, "result": {"resources": []}})
        elif method in ("prompts/list",):
            self._send({"jsonrpc": "2.0", "id": rid, "result": {"prompts": []}})
        else:
            self._send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "mock: non gere"}})


HTTPServer(("127.0.0.1", PORT), H).serve_forever()

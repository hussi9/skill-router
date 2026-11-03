# skill-router over MCP

A dependency-free stdio MCP server (`mcp/server.py`, Python stdlib only). Any MCP client can ask
skill-router to route a task, find a skill or read the audit. Every tool is read-only: nothing is
written, installed, applied or measured, and the router runs outside hook mode (no pending state,
no outcome or feature logging, no holdout). Results carry skill names, reasons and numbers only.

| Tool | Input | Returns |
|---|---|---|
| `route` | `task` | `card`, `shortlist`, `path`, `mode` (`jev` when `TYPESAFE_API_KEY` is available and answered, else `keyless`), `text` |
| `find_skill` | `capability`, `limit` (1-20, default 5) | installed matches (index ranking) and skills.sh cache matches with installs, audit and install command; no network |
| `audit_skills` | none | score, counts and top actions (no descriptions) |
| `listing` | `project` (optional path) | the cached `/context` measurement line, or `not measured` |

The server is not registered anywhere for you. Copy the snippet for your client and replace `<repo>`.

**Codex** (`~/.codex/config.toml`)

```toml
[mcp_servers.skill-router]
command = "python3"
args = ["<repo>/mcp/server.py"]
```

**Claude Code**

```bash
claude mcp add skill-router -- python3 <repo>/mcp/server.py
```

**Cursor** (`.cursor/mcp.json`)

```json
{ "mcpServers": { "skill-router": { "command": "python3", "args": ["<repo>/mcp/server.py"] } } }
```

## Recorded round trip

Synthetic one-skill index, no Jev key, input is newline-delimited JSON-RPC on stdin:

```
> {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}
< {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "skill-router", "version": "2.0.3"}}}
> {"jsonrpc":"2.0","method":"notifications/initialized"}
> {"jsonrpc":"2.0","id":2,"method":"tools/list"}
< {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "route", ...}, {"name": "find_skill", ...}, {"name": "audit_skills", ...}, {"name": "listing", ...}]}}
> {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"route","arguments":{"task":"plot a chart of monthly sales"}}}
< {"jsonrpc": "2.0", "id": 3, "result": {"content": [{"type": "text", "text": "{\"card\": [], \"shortlist\": [], \"path\": \"SKIP\", \"mode\": \"keyless\", \"text\": \"\"}"}], "isError": false}}
```

The notification gets no reply. Malformed lines return a JSON-RPC error and the server keeps running;
an unknown tool or a failing tool returns a result with `isError: true`.

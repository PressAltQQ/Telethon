<!--
ARCHIVED 2026-04-19 — moved from repo root (original name: 03_why_stdio_only.md).
Content is general MCP transport reasoning and remains sound.
The canonical MCP bridge runbook is `docs/mcp_bridge/README.md`; this doc is retained for historical context.
-->

# Why stdio-Only MCP Transport Is Critical for Security

## The Two Transport Options

MCP servers can communicate via two transport mechanisms:

1. **stdio** — The MCP client spawns the server as a child process. Communication happens over the process's stdin/stdout pipes.
2. **HTTP (Streamable HTTP / SSE)** — The server opens a network port (e.g., `localhost:8080`) and the client connects over HTTP.

For a Telegram file download server that holds session credentials, **stdio is the only safe choice**. Here is why.

---

## Problem 1: HTTP Exposes a Network Port Any Local Process Can Access

When an MCP server runs over HTTP on `localhost:3000`, **every process on the machine** can connect to it. That includes:

- Other user accounts on a shared server
- Malware or compromised software running under your user
- VS Code extensions, browser extensions, or other dev tools with network access
- Docker containers with host network access

With stdio, the pipe file descriptors are only inherited by the parent process that spawned the server. No other process on the system can read from or write to those pipes.

**HTTP**: any local process -> `localhost:3000` -> full access to your Telegram session
**stdio**: only the parent MCP client -> pipe fd -> access controlled by process hierarchy

## Problem 2: Localhost Attacks from the Browser

Modern browsers allow JavaScript on any website to make requests to `localhost`. This enables a class of attacks:

1. You visit `evil-site.com` in your browser.
2. The site's JavaScript sends `fetch("http://localhost:3000/mcp/call", { method: "POST", body: ... })` to your MCP server.
3. If CORS is misconfigured (or the server doesn't check `Origin`), the request succeeds.
4. The attacker can now list your Telegram channels and download files through your session.

This is not theoretical — localhost attacks against developer tools (Redis, Docker, Jupyter, Elasticsearch) are well-documented and actively exploited.

**stdio is immune to this entirely.** There is no port to connect to. A browser cannot talk to a Unix pipe.

## Problem 3: HTTP Requires Auth/TLS That's Easy to Get Wrong

To secure an HTTP MCP server, you would need to:

- Generate TLS certificates (self-signed for localhost, which then requires cert pinning)
- Implement authentication (API keys, tokens, mTLS)
- Configure CORS headers correctly
- Handle rate limiting at the HTTP layer
- Prevent request smuggling, header injection, etc.

Each of these is a potential failure point. Most developers skip several of them for "localhost-only" servers, assuming localhost is safe (it is not — see Problem 2).

With stdio, **none of this is needed**. The transport is inherently authenticated: only the process that holds the pipe file descriptors can communicate. There is no network layer to misconfigure.

## Problem 4: HTTP MCP Servers Persist After the Client Exits

If your MCP client crashes or is killed, an HTTP MCP server keeps running and listening on its port. The Telegram session remains exposed on the network indefinitely until someone notices and kills the process.

With stdio, when the parent process exits, the pipe is closed. The server receives EOF on stdin and shuts down. The Telegram session is no longer accessible.

---

## Summary

| Concern | HTTP Transport | stdio Transport |
|---------|---------------|-----------------|
| Accessible to other local processes | Yes (any process can connect) | No (pipe FDs are private) |
| Vulnerable to browser localhost attacks | Yes | No |
| Requires TLS/auth configuration | Yes (easy to get wrong) | No (inherently authenticated) |
| Persists after client crash | Yes (orphaned server) | No (pipe EOF triggers shutdown) |
| Network attack surface | Full TCP/HTTP stack | None |
| Credential exposure window | While port is open | Only during active pipe session |

**For a server that holds Telegram session credentials, stdio is not a preference — it is a requirement.**

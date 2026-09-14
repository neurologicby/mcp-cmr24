# CMR24 MCP Server

Public release **0.0.1** of an MCP server for working with the CMR24 cargo API.

The server provides two MCP interfaces over one operation registry:

- `/mcp/single` — a compact catalogue for clients with limited context;
- `/mcp/native` — individual typed MCP tools.

The application source, deployment instructions, security notes and test
commands are in [`mcp-cmr24/README.md`](mcp-cmr24/README.md).

## Quick start

```powershell
cd mcp-cmr24
Copy-Item .env.example .env
python -m pip install -r requirements.in
python start_server.py
```

Keep `.env` private. The repository contains only `.env.example` with empty
credential fields.

## License

MIT — see [LICENSE](LICENSE).

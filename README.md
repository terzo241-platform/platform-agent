# platform-agent

AI agent that orchestrates CI/CD and infrastructure via natural language.

## Structure

```
agent/
  tools.py       # GitHub + GCP tool functions
  mcp_server.py  # MCP server exposing tools
  chat.py        # LLM-powered natural language interface
```

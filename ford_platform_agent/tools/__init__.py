"""Platform tools — provider-agnostic functions exposed to the ADK agent.

Each function becomes an ADK FunctionTool. The agent calls these;
they route through the ProviderRegistry to the correct backend.
"""

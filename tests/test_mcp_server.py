"""Tests for MCP server — tool registration, annotations, and transport."""

from __future__ import annotations

import pytest

from ford_platform_agent.mcp_server import mcp


class TestMCPServerMetadata:
    def test_server_name(self):
        assert mcp.name == "ford-platform-agent"

    def test_server_version(self):
        assert mcp.version == "0.1.0"


class TestToolRegistration:
    """All 17 ADK tools must be exposed as MCP tools."""

    @pytest.fixture
    def tool_names(self):
        return [t.name for t in mcp._tool_manager.list_tools()]

    def test_cicd_tools_registered(self, tool_names):
        assert "list_pipeline_runs" in tool_names
        assert "get_pipeline_status" in tool_names
        assert "trigger_pipeline" in tool_names
        assert "cancel_pipeline" in tool_names

    def test_scm_tools_registered(self, tool_names):
        assert "list_repositories" in tool_names
        assert "get_repository_info" in tool_names
        assert "list_pull_requests" in tool_names
        assert "create_pull_request" in tool_names

    def test_gitops_tools_registered(self, tool_names):
        assert "list_gitops_applications" in tool_names
        assert "get_application_status" in tool_names
        assert "sync_application" in tool_names
        assert "rollback_application" in tool_names
        assert "get_deployment_history" in tool_names

    def test_infra_tools_registered(self, tool_names):
        assert "list_environments" in tool_names
        assert "get_plan_output" in tool_names
        assert "create_service_pr" in tool_names
        assert "approve_and_merge" in tool_names

    def test_total_tool_count(self, tool_names):
        assert len(tool_names) == 17


class TestToolAnnotations:
    """Read-only vs write vs destructive tools must be annotated correctly."""

    @pytest.fixture
    def tools_by_name(self):
        return {t.name: t for t in mcp._tool_manager.list_tools()}

    def test_read_tools_marked_readonly(self, tools_by_name):
        read_tools = [
            "list_pipeline_runs",
            "get_pipeline_status",
            "list_repositories",
            "get_repository_info",
            "list_pull_requests",
            "list_gitops_applications",
            "get_application_status",
            "get_deployment_history",
            "list_environments",
            "get_plan_output",
        ]
        for name in read_tools:
            tool = tools_by_name[name]
            assert tool.annotations.read_only_hint is True, f"{name} should be readOnly"
            assert tool.annotations.destructive_hint is False, f"{name} should not be destructive"

    def test_destructive_tools_marked(self, tools_by_name):
        destructive_tools = [
            "cancel_pipeline",
            "sync_application",
            "rollback_application",
            "approve_and_merge",
        ]
        for name in destructive_tools:
            tool = tools_by_name[name]
            assert tool.annotations.destructive_hint is True, f"{name} should be destructive"
            assert tool.annotations.read_only_hint is False, f"{name} should not be readOnly"

    def test_write_safe_tools_not_destructive(self, tools_by_name):
        safe_write_tools = [
            "trigger_pipeline",
            "create_pull_request",
            "create_service_pr",
        ]
        for name in safe_write_tools:
            tool = tools_by_name[name]
            assert tool.annotations.read_only_hint is False, f"{name} should not be readOnly"
            assert tool.annotations.destructive_hint is False, f"{name} should not be destructive"


class TestToolSchemas:
    """Each tool must have proper input schemas from type hints."""

    @pytest.fixture
    def tools_by_name(self):
        return {t.name: t for t in mcp._tool_manager.list_tools()}

    def test_create_service_pr_has_required_params(self, tools_by_name):
        schema = tools_by_name["create_service_pr"].parameters
        required = schema.get("required", [])
        assert "service_name" in required
        assert "team" in required
        assert "cost_center" in required

    def test_approve_and_merge_has_pr_number(self, tools_by_name):
        schema = tools_by_name["approve_and_merge"].parameters
        props = schema.get("properties", {})
        assert "pr_number" in props
        assert "approver" in props

    def test_trigger_pipeline_has_workflow(self, tools_by_name):
        schema = tools_by_name["trigger_pipeline"].parameters
        required = schema.get("required", [])
        assert "repo" in required
        assert "workflow" in required

    def test_list_environments_no_required_params(self, tools_by_name):
        schema = tools_by_name["list_environments"].parameters
        required = schema.get("required", [])
        assert len(required) == 0


class TestCLIIntegration:
    def test_mcp_subcommand_exists(self):
        import sys

        from ford_platform_agent.__main__ import parse_args

        old_argv = sys.argv
        sys.argv = ["ford-agent", "mcp"]
        try:
            args = parse_args()
            assert args.command == "mcp"
            assert args.transport == "stdio"
        finally:
            sys.argv = old_argv

    def test_mcp_http_transport(self):
        import sys

        from ford_platform_agent.__main__ import parse_args

        old_argv = sys.argv
        sys.argv = ["ford-agent", "mcp", "--transport", "streamable-http", "--port", "9090"]
        try:
            args = parse_args()
            assert args.transport == "streamable-http"
            assert args.port == 9090
        finally:
            sys.argv = old_argv

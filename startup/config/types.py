"""设置类型定义。

定义配置系统中使用的核心数据结构。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class PermissionRule:
    """权限规则。

    用于定义工具的允许/拒绝/询问策略。
    """

    rule_type: Literal["allow", "deny", "ask"]
    tool_pattern: str
    input_pattern: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_type": self.rule_type,
            "tool_pattern": self.tool_pattern,
            "input_pattern": self.input_pattern,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PermissionRule:
        return cls(
            rule_type=data.get("rule_type", "ask"),
            tool_pattern=data.get("tool_pattern", ""),
            input_pattern=data.get("input_pattern", ""),
        )


@dataclass
class Permissions:
    """权限配置。"""

    allow: list[PermissionRule] = field(default_factory=list)
    deny: list[PermissionRule] = field(default_factory=list)
    ask: list[PermissionRule] = field(default_factory=list)
    default_mode: str | None = None
    additional_directories: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.allow:
            result["allow"] = [r.to_dict() for r in self.allow]
        if self.deny:
            result["deny"] = [r.to_dict() for r in self.deny]
        if self.ask:
            result["ask"] = [r.to_dict() for r in self.ask]
        if self.default_mode:
            result["defaultMode"] = self.default_mode
        if self.additional_directories:
            result["additionalDirectories"] = self.additional_directories
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Permissions:
        allow = [PermissionRule.from_dict(r) for r in data.get("allow", [])]
        deny = [PermissionRule.from_dict(r) for r in data.get("deny", [])]
        ask = [PermissionRule.from_dict(r) for r in data.get("ask", [])]
        return cls(
            allow=allow,
            deny=deny,
            ask=ask,
            default_mode=data.get("defaultMode"),
            additional_directories=data.get("additionalDirectories", []),
        )


@dataclass
class Settings:
    """完整设置结构。

    对应 .claude/settings.json 的结构。
    """

    permissions: Permissions = field(default_factory=Permissions)
    model: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    auto_compact: bool = True
    context_collapse: bool = False
    verbose: bool = False
    theme: str = "dark"
    output_style: str = ""
    hooks: dict[str, Any] = field(default_factory=dict)
    mcp_servers: dict[str, Any] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    api_key_helper: str | None = None
    enable_all_project_mcp_servers: bool = False
    enabled_mcpjson_servers: list[str] = field(default_factory=list)
    disabled_mcpjson_servers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.permissions.allow or self.permissions.deny or self.permissions.ask:
            result["permissions"] = self.permissions.to_dict()
        if self.model is not None:
            result["model"] = self.model
        if self.llm_base_url is not None:
            result["llm_base_url"] = self.llm_base_url
        if self.llm_api_key is not None:
            result["llm_api_key"] = self.llm_api_key
        if not self.auto_compact:
            result["auto_compact"] = self.auto_compact
        if self.context_collapse:
            result["context_collapse"] = self.context_collapse
        if self.verbose:
            result["verbose"] = self.verbose
        if self.theme != "dark":
            result["theme"] = self.theme
        if self.output_style:
            result["output_style"] = self.output_style
        if self.hooks:
            result["hooks"] = self.hooks
        if self.mcp_servers:
            result["mcpServers"] = self.mcp_servers
        if self.env:
            result["env"] = self.env
        if self.api_key_helper is not None:
            result["apiKeyHelper"] = self.api_key_helper
        if self.enable_all_project_mcp_servers:
            result["enableAllProjectMcpServers"] = self.enable_all_project_mcp_servers
        if self.enabled_mcpjson_servers:
            result["enabledMcpjsonServers"] = self.enabled_mcpjson_servers
        if self.disabled_mcpjson_servers:
            result["disabledMcpjsonServers"] = self.disabled_mcpjson_servers
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Settings:
        permissions_data = data.get("permissions", {})
        permissions = Permissions.from_dict(permissions_data)
        return cls(
            permissions=permissions,
            model=data.get("model"),
            llm_base_url=data.get("llm_base_url"),
            llm_api_key=data.get("llm_api_key"),
            auto_compact=data.get("auto_compact", True),
            context_collapse=data.get("context_collapse", False),
            verbose=data.get("verbose", False),
            theme=data.get("theme", "dark"),
            output_style=data.get("output_style", ""),
            hooks=data.get("hooks", {}),
            mcp_servers=data.get("mcpServers", {}),
            env=data.get("env", {}),
            api_key_helper=data.get("apiKeyHelper"),
            enable_all_project_mcp_servers=data.get(
                "enableAllProjectMcpServers", False
            ),
            enabled_mcpjson_servers=data.get("enabledMcpjsonServers", []),
            disabled_mcpjson_servers=data.get("disabledMcpjsonServers", []),
        )

"""工具协议定义 — 声明式工具描述符设计（参考 ZCode 内置工具规格）。

每个工具除了名称/schema/执行函数外，还携带一组描述符：
元数据（风险等级/副作用范围）、权限规格、结果预算、超时策略、取消策略。
执行管线（executor）与权限管线按描述符统一治理，不再散落硬编码。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# 描述符常量
# ---------------------------------------------------------------------------

# 风险等级
RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"

# 副作用范围
SCOPE_NONE = "none"            # 无副作用（只读查询）
SCOPE_WORKSPACE = "workspace"  # 影响工作区文件
SCOPE_SYSTEM = "system"        # 影响系统（子进程/命令）
SCOPE_NETWORK = "network"      # 影响网络
SCOPE_SESSION = "session"      # 仅影响会话上下文

# 结果预算截断方向
DIRECTION_HEAD = "head"  # 保留开头
DIRECTION_TAIL = "tail"  # 保留末尾


# ---------------------------------------------------------------------------
# 描述符数据类
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolMetadata:
    """工具元数据 — 权限分级与调度依据。

    Attributes:
        risk_level: 风险等级（low/medium/high）
        read_only: 是否只读（无副作用）
        destructive: 是否有破坏性（删文件/覆盖不可恢复）
        concurrent_safe: 是否可并发执行
        side_effect_scope: 副作用范围（none/workspace/system/network/session）
        needs_approval: default 模式下是否默认需要用户确认
    """

    risk_level: str = RISK_MEDIUM
    read_only: bool = False
    destructive: bool = False
    concurrent_safe: bool = False
    side_effect_scope: str = SCOPE_WORKSPACE
    needs_approval: bool = True


@dataclass(frozen=True)
class ToolPermissionSpec:
    """权限规格 — 声明工具的权限类别与规则匹配字段。

    Attributes:
        permission: 权限类别（如 read/edit/bash），空串表示沿用工具名
        reason: 该权限分级的理由（供弹窗展示）
        pattern_sources: 权限规则匹配的输入字段名列表（如 Bash 的 ["command"]）
    """

    permission: str = ""
    reason: str = ""
    pattern_sources: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ResultBudget:
    """结果预算 — 控制工具输出进入模型上下文的体量。

    Attributes:
        max_model_chars: 给模型的最大字符数
        strategy: 超预算策略（truncate 截断）
        preview_direction: 截断保留方向（head 开头 / tail 末尾）
    """

    max_model_chars: int = 30000
    strategy: str = "truncate"
    preview_direction: str = DIRECTION_HEAD


@dataclass(frozen=True)
class TimeoutPolicy:
    """超时策略 — 默认/上限/是否允许调用覆盖。

    Attributes:
        default_ms: 默认超时（毫秒）
        max_ms: 超时上限（毫秒），输入覆盖值不得超过
        allow_call_override: 是否允许工具输入覆盖超时
    """

    default_ms: int = 120000
    max_ms: int = 120000
    allow_call_override: bool = False

    def resolve_ms(self, requested_ms: int | None) -> int:
        """解析实际生效的超时：不允许覆盖用默认值，否则钳制到 [1ms, max_ms]。"""
        if not self.allow_call_override or requested_ms is None or requested_ms <= 0:
            return self.default_ms
        return min(requested_ms, self.max_ms)


@dataclass(frozen=True)
class CancellationPolicy:
    """取消策略 — 是否支持取消及清理语义。

    Attributes:
        supported: 是否支持取消
        cleanup: 清理语义（none 无需清理 / best_effort 尽力终止副作用）
        user_visible_message: 取消时展示给用户的文案
    """

    supported: bool = True
    cleanup: str = "best_effort"
    user_visible_message: str = "工具执行已取消"


# ---------------------------------------------------------------------------
# ToolResult — 工具执行结果
# ---------------------------------------------------------------------------

@dataclass
class ToolResult:
    """工具执行结果。

    Attributes:
        content: 结果文本
        is_error: 是否为错误
        metadata: 元数据
        new_messages: 需要注入对话的额外消息（如 Skill 正文、Agent 子代理中间结果）。
            为 None 时不注入。非 None 时，这些消息会在 tool_result 之后追加到对话。
        context_modifier: 上下文修改指令（如 allowed_tools 权限注入、model 覆盖）。
            为 None 时不修改。
    """

    content: str
    is_error: bool = False
    metadata: dict = field(default_factory=dict)
    new_messages: list[dict] | None = None
    context_modifier: dict | None = None


# ---------------------------------------------------------------------------
# ToolUseContext — 工具执行上下文
# ---------------------------------------------------------------------------

@dataclass
class ToolUseContext:
    """工具执行上下文。

    Attributes:
        tool_use_id: 工具执行标识；子代理场景携带 agent_id（agent_ 前缀），
            供 is_subagent_context 判定
        session_id: 所属引擎会话标识（聊天会话 id），供子代理注册表做父会话关联
    """

    permission_decision: str | None = None
    messages: list = field(default_factory=list)
    file_state_cache: dict = field(default_factory=dict)
    abort_controller: Any = None
    tool_use_id: str = ""
    # 提问回调：AskUserQuestion 工具用它挂起等待用户回答，
    # 签名 async (question: str, options: list[dict]) -> str，None 表示无前端可问
    question_callback: Any = None
    session_id: str = ""


# ---------------------------------------------------------------------------
# Tool — 工具协议定义（胖接口）
# ---------------------------------------------------------------------------

@dataclass
class Tool:
    """工具协议定义 — 声明式描述符 + 执行函数。"""

    # --- 必填字段 ---
    name: str
    description: str
    input_schema: type[BaseModel]
    execute: Callable  # async def execute(input, context) -> ToolResult
    prompt: str

    # --- 可选渲染 / 验证 / 权限回调 ---
    render: Callable | None = None
    validate_input: Callable | None = None
    get_tool_permission: Callable | None = None
    render_tool_use: Callable | None = None
    render_tool_result: Callable | None = None
    destructure_function: Callable | None = None

    # --- 行为标志（兼容字段，新代码优先读 metadata） ---
    user_visible: bool = True
    is_concurrent: bool = False
    is_read_only: bool = False
    requires_permission: bool = True
    can_be_replaced_by_mcp: bool = False

    # --- 别名 ---
    aliases: list[str] = field(default_factory=list)

    # --- 声明式描述符（未显式提供时由 build_tool 从行为标志推导默认值） ---
    metadata: ToolMetadata | None = None
    permission_spec: ToolPermissionSpec | None = None
    result_budget: ResultBudget | None = None
    timeout_policy: TimeoutPolicy | None = None
    cancellation: CancellationPolicy | None = None
    format_model_content: Callable | None = None  # 结构化结果 → 给模型的文本

    def get_metadata(self) -> ToolMetadata:
        """获取元数据描述符（None 时回退保守默认值）。"""
        return self.metadata if self.metadata is not None else ToolMetadata()

    def get_result_budget(self) -> ResultBudget:
        """获取结果预算（None 时回退默认预算）。"""
        return self.result_budget if self.result_budget is not None else ResultBudget()

    def get_timeout_policy(self) -> TimeoutPolicy:
        """获取超时策略（None 时回退默认策略）。"""
        return self.timeout_policy if self.timeout_policy is not None else TimeoutPolicy()

    def get_cancellation(self) -> CancellationPolicy:
        """获取取消策略（None 时回退默认策略）。"""
        return self.cancellation if self.cancellation is not None else CancellationPolicy()


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------

async def _default_execute(_input: Any, _context: ToolUseContext) -> ToolResult:
    """execute 的默认实现 — 返回未实现错误。"""
    return ToolResult(content="Tool execute not implemented", is_error=True)


def _default_render(_input: Any, _context: ToolUseContext) -> str:
    """render 的默认实现 — 返回空字符串。"""
    return ""


def build_tool(**kwargs: Any) -> Tool:
    """工厂函数，提供 execute/render 的默认实现。

    用法：
        tool = build_tool(
            name="Bash",
            description="执行 shell 命令",
            input_schema=BashInput,
            execute=my_execute_fn,
            prompt="运行 bash 命令",
        )

    未显式提供 metadata 时，从行为标志（is_read_only/is_concurrent/requires_permission）
    推导出保守的元数据描述符，保证既有工具零改动兼容。
    """
    kwargs.setdefault("execute", _default_execute)
    kwargs.setdefault("render", _default_render)
    kwargs.setdefault("aliases", [])

    # 未声明描述符时从行为标志推导默认元数据
    if kwargs.get("metadata") is None:
        read_only = kwargs.get("is_read_only", False)
        kwargs["metadata"] = ToolMetadata(
            risk_level=RISK_LOW if read_only else RISK_MEDIUM,
            read_only=read_only,
            concurrent_safe=kwargs.get("is_concurrent", False),
            side_effect_scope=SCOPE_NONE if read_only else SCOPE_WORKSPACE,
            needs_approval=kwargs.get("requires_permission", True) and not read_only,
        )

    return Tool(**kwargs)


# ---------------------------------------------------------------------------
# 工具名匹配
# ---------------------------------------------------------------------------

def tool_matches_name(tool: Tool, name: str) -> bool:
    """检查工具名或别名是否匹配给定名称。"""
    return tool.name == name or name in tool.aliases

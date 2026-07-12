"""原生记忆工具 - 非 MCP 的记忆操作工具。

当 memory-palace 插件激活时，自动注册到工具池。
Agent 可通过这些工具搜索/写入记忆、查询知识图谱、摄取文件。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from tools.protocol import Tool, ToolResult, ToolUseContext, build_tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _get_memory_provider() -> Any | None:
    """获取当前激活的记忆后端。"""
    from query.services.memory.registry import get_active_memory
    return get_active_memory()


def _format_results(results: list[dict]) -> str:
    """格式化搜索结果为可读文本。"""
    if not results:
        return "未找到相关记忆。"
    lines = []
    for i, r in enumerate(results, 1):
        score = r.get("score", 0)
        content = r.get("content", "")[:500]
        wing = r.get("wing", "")
        room = r.get("room", "")
        source = r.get("source_file", "")
        lines.append(f"### 结果 {i} (score: {score:.3f})")
        lines.append(f"Wing: {wing} | Room: {room} | Source: {source}")
        lines.append(f"---\n{content}\n")
    return "\n".join(lines)


def _format_triples(triples: list[dict]) -> str:
    """格式化知识图谱三元组列表为可读文本。"""
    if not triples:
        return "未找到相关事实。"
    lines = []
    for t in triples:
        subject = t.get("subject", "")
        predicate = t.get("predicate", "")
        obj = t.get("object", "")
        valid_from = t.get("valid_from", "")
        valid_to = t.get("valid_to")
        valid_to_str = valid_to if valid_to else "present"
        lines.append(
            f"[{valid_from} ~ {valid_to_str}] {subject} --{predicate}--> {obj}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Input Schemas (Pydantic Models)
# ---------------------------------------------------------------------------

class MemorySearchInput(BaseModel):
    query: str = Field(..., description="搜索查询")
    wing: str | None = Field(None, description="限定 Wing（项目/领域）")
    room: str | None = Field(None, description="限定 Room（主题）")
    source_file: str | None = Field(None, description="限定来源文件路径")
    limit: int = Field(5, description="最多返回条数", ge=1, le=20)

class MemoryAddInput(BaseModel):
    wing: str = Field(..., description="Wing 名称（项目/人/领域）")
    room: str = Field(..., description="Room 名称（主题/子分类）")
    content: str = Field(..., description="要存储的逐字内容")
    source_file: str = Field("", description="来源文件路径")
    importance: float = Field(0.5, description="重要性评分 0.0-1.0", ge=0, le=1)

class MemoryStatusInput(BaseModel):
    pass

class MemoryListWingsInput(BaseModel):
    pass

class MemoryListRoomsInput(BaseModel):
    wing: str = Field(..., description="Wing 名称")

class MemoryGetDrawerInput(BaseModel):
    drawer_id: str = Field(..., description="抽屉 ID")

class MemoryGetBySourceInput(BaseModel):
    source_file: str = Field(..., description="来源文件路径")

class MemoryDeleteInput(BaseModel):
    drawer_id: str = Field(..., description="要删除的抽屉 ID")

class MemoryDeleteBySourceInput(BaseModel):
    source_file: str = Field(..., description="来源文件路径")

class MemoryKGAddInput(BaseModel):
    subject: str = Field(..., description="主体实体")
    predicate: str = Field(..., description="关系类型（如 works_on, child_of, has_issue）")
    object: str = Field(..., description="客体实体")
    valid_from: str | None = Field(None, description="生效时间（ISO 8601），默认当前时间")
    drawer_refs: str = Field("", description="关联抽屉 ID（逗号分隔）")

class MemoryKGQueryInput(BaseModel):
    entity: str = Field(..., description="要查询的实体名")
    as_of: str | None = Field(None, description="时间点过滤（ISO 8601）")

class MemoryKGTimelineInput(BaseModel):
    entity: str = Field(..., description="实体名")

class MemoryKGInvalidateInput(BaseModel):
    subject: str = Field(..., description="主体实体")
    predicate: str = Field(..., description="关系类型")
    object: str = Field(..., description="客体实体")
    ended: str | None = Field(None, description="失效时间（ISO 8601），默认当前时间")

class MemoryWakeUpInput(BaseModel):
    wing: str | None = Field(None, description="Wing 名称，None 返回全局记忆")


class MemoryKGSupersedeInput(BaseModel):
    subject: str = Field(..., description="主体实体")
    predicate: str = Field(..., description="关系类型")
    old_object: str = Field(..., description="旧客体")
    new_object: str = Field(..., description="新客体")
    at: str | None = Field(None, description="边界时间（ISO 8601），默认当前时间")


class MemoryRepairInput(BaseModel):
    action: str = Field("repair_fts", description="操作类型：repair_fts / cleanup_closets / cleanup_triples / all")


# ---------------------------------------------------------------------------
# Tool execute functions
# ---------------------------------------------------------------------------

async def _execute_memory_search(input_model: MemorySearchInput, context: ToolUseContext) -> ToolResult:
    """搜索记忆宫殿中的内容。"""
    memory = _get_memory_provider()
    if memory is None:
        return ToolResult(content="错误：无激活的记忆后端", is_error=True)
    try:
        results = memory.search_memory(
            query=input_model.query,
            wing=input_model.wing,
            room=input_model.room,
            source_file=input_model.source_file,
            limit=input_model.limit,
        )
        return ToolResult(content=_format_results(results))
    except Exception as e:
        return ToolResult(content=f"搜索失败: {e}", is_error=True)


async def _execute_memory_add(input_model: MemoryAddInput, context: ToolUseContext) -> ToolResult:
    """写入一条记忆到抽屉。"""
    memory = _get_memory_provider()
    if memory is None:
        return ToolResult(content="错误：无激活的记忆后端", is_error=True)
    try:
        result = memory.add_drawer(
            wing=input_model.wing,
            room=input_model.room,
            content=input_model.content,
            source_file=input_model.source_file,
            importance=input_model.importance,
        )
        drawer_id = result.get("id", "")
        return ToolResult(
            content=f"已存储记忆 [{drawer_id}] 到 {input_model.wing}/{input_model.room}"
        )
    except Exception as e:
        return ToolResult(content=f"写入失败: {e}", is_error=True)


async def _execute_memory_status(input_model: MemoryStatusInput, context: ToolUseContext) -> ToolResult:
    """获取记忆宫殿状态。"""
    memory = _get_memory_provider()
    if memory is None:
        return ToolResult(content="错误：无激活的记忆后端", is_error=True)
    try:
        status = memory.get_status()
        return ToolResult(content=json.dumps(status, ensure_ascii=False, indent=2))
    except Exception as e:
        return ToolResult(content=f"获取状态失败: {e}", is_error=True)


async def _execute_memory_list_wings(input_model: MemoryListWingsInput, context: ToolUseContext) -> ToolResult:
    """列出所有 Wing。"""
    memory = _get_memory_provider()
    if memory is None:
        return ToolResult(content="错误：无激活的记忆后端", is_error=True)
    try:
        wings = memory.list_wings()
        if not wings:
            return ToolResult(content="记忆宫殿为空，尚无 Wing。")
        lines = [
            f"Wing: {w['name']} ({w.get('drawer_count', 0)} drawers)"
            for w in wings
        ]
        return ToolResult(content="\n".join(lines))
    except Exception as e:
        return ToolResult(content=f"列出 Wing 失败: {e}", is_error=True)


async def _execute_memory_list_rooms(input_model: MemoryListRoomsInput, context: ToolUseContext) -> ToolResult:
    """列出指定 Wing 下的 Room。"""
    memory = _get_memory_provider()
    if memory is None:
        return ToolResult(content="错误：无激活的记忆后端", is_error=True)
    try:
        rooms = memory.list_rooms(input_model.wing)
        if not rooms:
            return ToolResult(content=f"Wing '{input_model.wing}' 下无 Room。")
        lines = [
            f"Room: {r['name']} ({r.get('drawer_count', 0)} drawers)"
            for r in rooms
        ]
        return ToolResult(content="\n".join(lines))
    except Exception as e:
        return ToolResult(content=f"列出 Room 失败: {e}", is_error=True)


async def _execute_memory_get_drawer(input_model: MemoryGetDrawerInput, context: ToolUseContext) -> ToolResult:
    """按 ID 获取抽屉。"""
    memory = _get_memory_provider()
    if memory is None:
        return ToolResult(content="错误：无激活的记忆后端", is_error=True)
    try:
        drawer = memory.get_drawer(input_model.drawer_id)
        if drawer is None:
            return ToolResult(content=f"抽屉 {input_model.drawer_id} 不存在", is_error=True)
        lines = [
            f"ID: {drawer.get('id', '')}",
            f"Wing: {drawer.get('wing', '')} | Room: {drawer.get('room', '')}",
            f"Source: {drawer.get('source_file', '')}",
            f"Importance: {drawer.get('importance', 0)}",
            f"Filed At: {drawer.get('filed_at', '')}",
            f"Authored At: {drawer.get('authored_at', '')}",
            f"---\n{drawer.get('content', '')}",
        ]
        return ToolResult(content="\n".join(lines))
    except Exception as e:
        return ToolResult(content=f"获取抽屉失败: {e}", is_error=True)


async def _execute_memory_get_by_source(input_model: MemoryGetBySourceInput, context: ToolUseContext) -> ToolResult:
    """按来源文件获取抽屉列表。"""
    memory = _get_memory_provider()
    if memory is None:
        return ToolResult(content="错误：无激活的记忆后端", is_error=True)
    try:
        drawers = memory.get_drawers_by_source(input_model.source_file)
        if not drawers:
            return ToolResult(content=f"来源 '{input_model.source_file}' 下无抽屉。")
        lines = []
        for i, d in enumerate(drawers, 1):
            lines.append(
                f"### 抽屉 {i}: {d.get('id', '')}"
            )
            lines.append(
                f"Wing: {d.get('wing', '')} | Room: {d.get('room', '')} | "
                f"Filed: {d.get('filed_at', '')}"
            )
            lines.append(f"---\n{d.get('content', '')[:500]}\n")
        return ToolResult(content="\n".join(lines))
    except Exception as e:
        return ToolResult(content=f"获取抽屉失败: {e}", is_error=True)


async def _execute_memory_delete(input_model: MemoryDeleteInput, context: ToolUseContext) -> ToolResult:
    """按 ID 删除抽屉。"""
    memory = _get_memory_provider()
    if memory is None:
        return ToolResult(content="错误：无激活的记忆后端", is_error=True)
    try:
        deleted = memory.delete_drawer(input_model.drawer_id)
        if deleted:
            return ToolResult(content=f"已删除抽屉 [{input_model.drawer_id}]")
        return ToolResult(content=f"抽屉 {input_model.drawer_id} 不存在", is_error=True)
    except Exception as e:
        return ToolResult(content=f"删除失败: {e}", is_error=True)


async def _execute_memory_delete_by_source(input_model: MemoryDeleteBySourceInput, context: ToolUseContext) -> ToolResult:
    """按来源文件删除所有抽屉。"""
    memory = _get_memory_provider()
    if memory is None:
        return ToolResult(content="错误：无激活的记忆后端", is_error=True)
    try:
        count = memory.delete_by_source(input_model.source_file)
        return ToolResult(content=f"已删除 {count} 个抽屉")
    except Exception as e:
        return ToolResult(content=f"删除失败: {e}", is_error=True)


async def _execute_memory_kg_add(input_model: MemoryKGAddInput, context: ToolUseContext) -> ToolResult:
    """添加知识图谱三元组。"""
    memory = _get_memory_provider()
    if memory is None:
        return ToolResult(content="错误：无激活的记忆后端", is_error=True)
    try:
        result = memory.kg_add(
            subject=input_model.subject,
            predicate=input_model.predicate,
            object=input_model.object,
            valid_from=input_model.valid_from,
            drawer_refs=input_model.drawer_refs,
        )
        triple_id = result.get("id", "")
        return ToolResult(
            content=(
                f"已添加三元组 [{triple_id}]: "
                f"{input_model.subject} --{input_model.predicate}--> {input_model.object}"
            )
        )
    except Exception as e:
        return ToolResult(content=f"添加三元组失败: {e}", is_error=True)


async def _execute_memory_kg_query(input_model: MemoryKGQueryInput, context: ToolUseContext) -> ToolResult:
    """查询实体关系。"""
    memory = _get_memory_provider()
    if memory is None:
        return ToolResult(content="错误：无激活的记忆后端", is_error=True)
    try:
        triples = memory.kg_query(
            entity=input_model.entity,
            as_of=input_model.as_of,
        )
        return ToolResult(content=_format_triples(triples))
    except Exception as e:
        return ToolResult(content=f"查询失败: {e}", is_error=True)


async def _execute_memory_kg_timeline(input_model: MemoryKGTimelineInput, context: ToolUseContext) -> ToolResult:
    """查询实体时间线。"""
    memory = _get_memory_provider()
    if memory is None:
        return ToolResult(content="错误：无激活的记忆后端", is_error=True)
    try:
        triples = memory.kg_timeline(input_model.entity)
        return ToolResult(content=_format_triples(triples))
    except Exception as e:
        return ToolResult(content=f"查询时间线失败: {e}", is_error=True)


async def _execute_memory_kg_invalidate(input_model: MemoryKGInvalidateInput, context: ToolUseContext) -> ToolResult:
    """使知识图谱三元组失效。"""
    memory = _get_memory_provider()
    if memory is None:
        return ToolResult(content="错误：无激活的记忆后端", is_error=True)
    try:
        count = memory.kg_invalidate(
            subject=input_model.subject,
            predicate=input_model.predicate,
            object=input_model.object,
            ended=input_model.ended,
        )
        return ToolResult(content=f"已使 {count} 条事实失效")
    except Exception as e:
        return ToolResult(content=f"失效操作失败: {e}", is_error=True)


async def _execute_memory_wake_up(input_model: MemoryWakeUpInput, context: ToolUseContext) -> ToolResult:
    """获取 L0+L1 唤醒上下文。"""
    memory = _get_memory_provider()
    if memory is None:
        return ToolResult(content="错误：无激活的记忆后端", is_error=True)
    try:
        text = memory.wake_up(input_model.wing)
        return ToolResult(content=text)
    except Exception as e:
        return ToolResult(content=f"唤醒失败: {e}", is_error=True)


async def _execute_memory_kg_supersede(input_model: MemoryKGSupersedeInput, context: ToolUseContext) -> ToolResult:
    """原子替换事实 - 关闭旧事实 + 打开新事实。"""
    memory = _get_memory_provider()
    if memory is None:
        return ToolResult(content="错误：无激活的记忆后端", is_error=True)
    if not hasattr(memory, 'kg_supersede'):
        return ToolResult(content="错误：当前记忆后端不支持事实替换", is_error=True)
    try:
        result = memory.kg_supersede(
            subject=input_model.subject,
            predicate=input_model.predicate,
            old_object=input_model.old_object,
            new_object=input_model.new_object,
            at=input_model.at,
        )
        invalidated = result.get("invalidated", 0)
        added = result.get("added", {})
        new_id = added.get("id", "") if isinstance(added, dict) else ""
        return ToolResult(
            content=(
                f"已原子替换事实：失效 {invalidated} 条旧事实，"
                f"新增 [{new_id}]: "
                f"{input_model.subject} --{input_model.predicate}--> {input_model.new_object}"
            )
        )
    except Exception as e:
        return ToolResult(content=f"事实替换失败: {e}", is_error=True)


async def _execute_memory_repair(input_model: MemoryRepairInput, context: ToolUseContext) -> ToolResult:
    """修复索引或清理孤立记录。"""
    memory = _get_memory_provider()
    if memory is None:
        return ToolResult(content="错误：无激活的记忆后端", is_error=True)
    if not hasattr(memory, 'repair_index'):
        return ToolResult(content="错误：当前记忆后端不支持索引修复", is_error=True)
    try:
        action = input_model.action
        if action == "repair_fts":
            result = memory.repair_index()
            return ToolResult(content=json.dumps(result, ensure_ascii=False, indent=2))
        elif action == "cleanup_closets":
            result = memory.cleanup_orphans()
            closets = result.get("closets", {})
            return ToolResult(content=json.dumps(closets, ensure_ascii=False, indent=2))
        elif action == "cleanup_triples":
            result = memory.cleanup_orphans()
            triples = result.get("triples", {})
            return ToolResult(content=json.dumps(triples, ensure_ascii=False, indent=2))
        elif action == "all":
            repair = memory.repair_index()
            cleanup = memory.cleanup_orphans()
            result = {"repair": repair, "cleanup": cleanup}
            return ToolResult(content=json.dumps(result, ensure_ascii=False, indent=2))
        else:
            return ToolResult(
                content=f"未知操作: {action}。支持: repair_fts / cleanup_closets / cleanup_triples / all",
                is_error=True,
            )
    except Exception as e:
        return ToolResult(content=f"修复失败: {e}", is_error=True)


# ---------------------------------------------------------------------------
# Build all tools
# ---------------------------------------------------------------------------

def get_memory_tools() -> list[Tool]:
    """获取所有记忆工具。

    当 memory-palace 插件激活时调用此函数，返回 16 个记忆工具。
    插件未激活时返回空列表。
    """
    memory = _get_memory_provider()
    if memory is None:
        return []

    # Check if the provider has the extended API (is a MemoryPalaceProvider)
    if not hasattr(memory, 'wake_up'):
        return []  # Not a MemoryPalaceProvider, don't register tools

    tools = [
        build_tool(
            name="memory_search",
            description="搜索记忆宫殿中的内容。支持语义搜索和关键词过滤。",
            input_schema=MemorySearchInput,
            execute=_execute_memory_search,
            prompt="搜索记忆：按含义或关键词查找存储的记忆片段",
            is_read_only=True,
            requires_permission=False,
            can_be_replaced_by_mcp=False,
        ),
        build_tool(
            name="memory_add",
            description="写入一条记忆到记忆宫殿的抽屉中。",
            input_schema=MemoryAddInput,
            execute=_execute_memory_add,
            prompt="写入记忆：将内容存入指定 Wing/Room",
            is_read_only=False,
            requires_permission=True,
            can_be_replaced_by_mcp=False,
        ),
        build_tool(
            name="memory_status",
            description="获取记忆宫殿的整体状态（抽屉数、Wing 数等）。",
            input_schema=MemoryStatusInput,
            execute=_execute_memory_status,
            prompt="记忆状态：查看记忆宫殿的整体概况",
            is_read_only=True,
            requires_permission=False,
            can_be_replaced_by_mcp=False,
        ),
        build_tool(
            name="memory_list_wings",
            description="列出记忆宫殿中所有 Wing（顶层分类）。",
            input_schema=MemoryListWingsInput,
            execute=_execute_memory_list_wings,
            prompt="列出 Wing：查看所有顶层记忆分类",
            is_read_only=True,
            requires_permission=False,
            can_be_replaced_by_mcp=False,
        ),
        build_tool(
            name="memory_list_rooms",
            description="列出指定 Wing 下的所有 Room（子分类）。",
            input_schema=MemoryListRoomsInput,
            execute=_execute_memory_list_rooms,
            prompt="列出 Room：查看指定 Wing 下的子分类",
            is_read_only=True,
            requires_permission=False,
            can_be_replaced_by_mcp=False,
        ),
        build_tool(
            name="memory_get_drawer",
            description="按 ID 获取单个抽屉的完整内容和元数据。",
            input_schema=MemoryGetDrawerInput,
            execute=_execute_memory_get_drawer,
            prompt="获取抽屉：按 ID 读取单条记忆",
            is_read_only=True,
            requires_permission=False,
            can_be_replaced_by_mcp=False,
        ),
        build_tool(
            name="memory_get_by_source",
            description="按来源文件路径获取所有相关抽屉。",
            input_schema=MemoryGetBySourceInput,
            execute=_execute_memory_get_by_source,
            prompt="按来源查找：获取某文件的所有记忆片段",
            is_read_only=True,
            requires_permission=False,
            can_be_replaced_by_mcp=False,
        ),
        build_tool(
            name="memory_delete",
            description="按 ID 删除单个抽屉。",
            input_schema=MemoryDeleteInput,
            execute=_execute_memory_delete,
            prompt="删除抽屉：按 ID 删除单条记忆",
            is_read_only=False,
            requires_permission=True,
            can_be_replaced_by_mcp=False,
        ),
        build_tool(
            name="memory_delete_by_source",
            description="按来源文件删除所有相关抽屉。",
            input_schema=MemoryDeleteBySourceInput,
            execute=_execute_memory_delete_by_source,
            prompt="按来源删除：删除某文件的所有记忆片段",
            is_read_only=False,
            requires_permission=True,
            can_be_replaced_by_mcp=False,
        ),
        build_tool(
            name="memory_kg_add",
            description="向知识图谱添加一条三元组（主体-关系-客体）。",
            input_schema=MemoryKGAddInput,
            execute=_execute_memory_kg_add,
            prompt="添加知识：记录实体间的关系",
            is_read_only=False,
            requires_permission=True,
            can_be_replaced_by_mcp=False,
        ),
        build_tool(
            name="memory_kg_query",
            description="查询某实体的当前有效关系。",
            input_schema=MemoryKGQueryInput,
            execute=_execute_memory_kg_query,
            prompt="查询知识：查看实体的当前关系",
            is_read_only=True,
            requires_permission=False,
            can_be_replaced_by_mcp=False,
        ),
        build_tool(
            name="memory_kg_timeline",
            description="查询某实体的完整时间线（含历史事实）。",
            input_schema=MemoryKGTimelineInput,
            execute=_execute_memory_kg_timeline,
            prompt="知识时间线：查看实体的历史关系变更",
            is_read_only=True,
            requires_permission=False,
            can_be_replaced_by_mcp=False,
        ),
        build_tool(
            name="memory_kg_invalidate",
            description="使某条三元组失效（标记关系结束）。",
            input_schema=MemoryKGInvalidateInput,
            execute=_execute_memory_kg_invalidate,
            prompt="失效知识：标记某关系已结束",
            is_read_only=False,
            requires_permission=True,
            can_be_replaced_by_mcp=False,
        ),
        build_tool(
            name="memory_wake_up",
            description="获取 L0+L1 唤醒上下文，快速恢复记忆。",
            input_schema=MemoryWakeUpInput,
            execute=_execute_memory_wake_up,
            prompt="唤醒记忆：获取上下文以恢复工作状态",
            is_read_only=True,
            requires_permission=False,
            can_be_replaced_by_mcp=False,
        ),
        build_tool(
            name="memory_kg_supersede",
            description="原子替换事实 - 关闭旧事实并打开新事实（时序知识图谱）。",
            input_schema=MemoryKGSupersedeInput,
            execute=_execute_memory_kg_supersede,
            prompt="替换事实：原子性地更新实体关系",
            is_read_only=False,
            requires_permission=True,
            can_be_replaced_by_mcp=False,
        ),
        build_tool(
            name="memory_repair",
            description="修复 FTS5 索引或清理孤立记录（Closet/KG 三元组）。",
            input_schema=MemoryRepairInput,
            execute=_execute_memory_repair,
            prompt="修复索引：重建搜索索引或清理孤立记录",
            is_read_only=False,
            requires_permission=True,
            can_be_replaced_by_mcp=False,
        ),
    ]
    return tools

"""记忆路由：后端管理、搜索、知识图谱等 API。"""

from fastapi import APIRouter

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /api/memory/providers - 列出记忆后端 + 当前激活
# ---------------------------------------------------------------------------


@router.get("/api/memory/providers")
async def list_memory_providers() -> dict:
    """返回已注册的记忆后端列表和当前激活的后端名。"""
    from query.services.memory.registry import get_registry

    registry = get_registry()
    return {
        "providers": [{"name": name} for name in registry.list_providers()],
        "active": registry.get_active_name(),
    }


# ---------------------------------------------------------------------------
# POST /api/memory/switch - 切换激活记忆后端（持久化）
# ---------------------------------------------------------------------------


@router.post("/api/memory/switch")
async def switch_memory_provider(body: dict) -> dict:
    """切换激活记忆后端。请求体：{"name": "..."}"""
    from query.services.memory.registry import get_registry

    name = body.get("name", "")
    registry = get_registry()
    ok = registry.set_active(name)
    if not ok:
        return {"ok": False, "error": f"Memory provider not found: {name}"}
    return {"ok": True, "active": name}


# ---------------------------------------------------------------------------
# POST /api/memory/clear - 清空指定会话记忆
# ---------------------------------------------------------------------------


@router.post("/api/memory/clear")
async def clear_memory(body: dict) -> dict:
    """清空指定会话的记忆。请求体：{"session_id": "..."}"""
    from query.services.memory.registry import get_registry

    session_id = body.get("session_id", "default")
    registry = get_registry()
    provider = registry.get_active()
    if provider is None:
        return {"ok": False, "error": "无激活的记忆后端"}
    try:
        await provider.clear(session_id)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": f"清空记忆失败: {e}"}


# ---------------------------------------------------------------------------
# Memory Palace 扩展 API
# ---------------------------------------------------------------------------

@router.post("/api/memory/search")
async def memory_search(body: dict) -> dict:
    """搜索记忆。请求体：{"query": "...", "wing": "...", "room": "...", "limit": 5}"""
    from query.services.memory.registry import get_active_memory
    memory = get_active_memory()
    if memory is None:
        return {"ok": False, "error": "无激活的记忆后端"}
    if not hasattr(memory, 'search_memory'):
        return {"ok": False, "error": "当前记忆后端不支持搜索"}
    try:
        results = memory.search_memory(
            query=body.get("query", ""),
            wing=body.get("wing"),
            room=body.get("room"),
            limit=body.get("limit", 5),
        )
        return {"ok": True, "results": results}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/api/memory/add")
async def memory_add(body: dict) -> dict:
    """添加记忆。请求体：{"wing": "...", "room": "...", "content": "...", "source_file": "...", "importance": 0.5}"""
    from query.services.memory.registry import get_active_memory
    memory = get_active_memory()
    if memory is None:
        return {"ok": False, "error": "无激活的记忆后端"}
    if not hasattr(memory, 'add_drawer'):
        return {"ok": False, "error": "当前记忆后端不支持添加"}
    try:
        drawer = memory.add_drawer(
            wing=body.get("wing", ""),
            room=body.get("room", ""),
            content=body.get("content", ""),
            source_file=body.get("source_file", ""),
            importance=body.get("importance", 0.5),
        )
        return {"ok": True, "drawer": drawer}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/api/memory/status")
async def memory_status() -> dict:
    """获取 Palace 状态。"""
    from query.services.memory.registry import get_active_memory
    memory = get_active_memory()
    if memory is None:
        return {"ok": False, "error": "无激活的记忆后端"}
    if not hasattr(memory, 'get_status'):
        return {"ok": False, "error": "当前记忆后端不支持状态查询"}
    try:
        status = memory.get_status()
        return {"ok": True, "status": status}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/api/memory/wings")
async def memory_wings() -> dict:
    """列出所有 Wing。"""
    from query.services.memory.registry import get_active_memory
    memory = get_active_memory()
    if memory is None:
        return {"ok": False, "error": "无激活的记忆后端"}
    if not hasattr(memory, 'list_wings'):
        return {"ok": False, "error": "当前记忆后端不支持此操作"}
    try:
        wings = memory.list_wings()
        return {"ok": True, "wings": wings}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/api/memory/rooms")
async def memory_rooms(body: dict) -> dict:
    """列出 Wing 下的 Room。请求体：{"wing": "..."}"""
    from query.services.memory.registry import get_active_memory
    memory = get_active_memory()
    if memory is None:
        return {"ok": False, "error": "无激活的记忆后端"}
    if not hasattr(memory, 'list_rooms'):
        return {"ok": False, "error": "当前记忆后端不支持此操作"}
    try:
        rooms = memory.list_rooms(body.get("wing", ""))
        return {"ok": True, "rooms": rooms}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/api/memory/kg/add")
async def memory_kg_add(body: dict) -> dict:
    """添加知识图谱三元组。"""
    from query.services.memory.registry import get_active_memory
    memory = get_active_memory()
    if memory is None:
        return {"ok": False, "error": "无激活的记忆后端"}
    if not hasattr(memory, 'kg_add'):
        return {"ok": False, "error": "当前记忆后端不支持知识图谱"}
    try:
        triple = memory.kg_add(
            subject=body.get("subject", ""),
            predicate=body.get("predicate", ""),
            object=body.get("object", ""),
            valid_from=body.get("valid_from"),
            drawer_refs=body.get("drawer_refs", ""),
        )
        return {"ok": True, "triple": triple}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/api/memory/kg/query")
async def memory_kg_query(body: dict) -> dict:
    """查询实体关系。请求体：{"entity": "...", "as_of": "..."}"""
    from query.services.memory.registry import get_active_memory
    memory = get_active_memory()
    if memory is None:
        return {"ok": False, "error": "无激活的记忆后端"}
    if not hasattr(memory, 'kg_query'):
        return {"ok": False, "error": "当前记忆后端不支持知识图谱"}
    try:
        triples = memory.kg_query(
            entity=body.get("entity", ""),
            as_of=body.get("as_of"),
        )
        return {"ok": True, "triples": triples}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/api/memory/kg/timeline")
async def memory_kg_timeline(body: dict) -> dict:
    """查询实体时间线。请求体：{"entity": "..."}"""
    from query.services.memory.registry import get_active_memory
    memory = get_active_memory()
    if memory is None:
        return {"ok": False, "error": "无激活的记忆后端"}
    if not hasattr(memory, 'kg_timeline'):
        return {"ok": False, "error": "当前记忆后端不支持知识图谱"}
    try:
        triples = memory.kg_timeline(body.get("entity", ""))
        return {"ok": True, "triples": triples}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/api/memory/kg/invalidate")
async def memory_kg_invalidate(body: dict) -> dict:
    """使三元组失效。"""
    from query.services.memory.registry import get_active_memory
    memory = get_active_memory()
    if memory is None:
        return {"ok": False, "error": "无激活的记忆后端"}
    if not hasattr(memory, 'kg_invalidate'):
        return {"ok": False, "error": "当前记忆后端不支持知识图谱"}
    try:
        count = memory.kg_invalidate(
            subject=body.get("subject", ""),
            predicate=body.get("predicate", ""),
            object=body.get("object", ""),
            ended=body.get("as_of"),
        )
        return {"ok": True, "count": count}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/api/memory/kg/entities")
async def memory_kg_entities() -> dict:
    """列出知识图谱中的所有实体。"""
    from query.services.memory.registry import get_active_memory
    memory = get_active_memory()
    if memory is None:
        return {"ok": False, "error": "无激活的记忆后端"}
    if not hasattr(memory, 'kg_entities'):
        return {"ok": False, "error": "当前记忆后端不支持知识图谱"}
    try:
        entities = memory.kg_entities()
        return {"ok": True, "entities": entities}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/api/memory/kg/supersede")
async def memory_kg_supersede(body: dict) -> dict:
    """原子替换事实。"""
    from query.services.memory.registry import get_active_memory
    memory = get_active_memory()
    if memory is None:
        return {"ok": False, "error": "无激活的记忆后端"}
    if not hasattr(memory, 'kg_supersede'):
        return {"ok": False, "error": "当前记忆后端不支持此操作"}
    try:
        result = memory.kg_supersede(
            subject=body.get("subject", ""),
            predicate=body.get("predicate", ""),
            old_object=body.get("old_object", ""),
            new_object=body.get("new_object", ""),
            at=body.get("at"),
        )
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/api/memory/repair")
async def memory_repair(body: dict) -> dict:
    """修复索引或清理孤立记录。"""
    from query.services.memory.registry import get_active_memory
    memory = get_active_memory()
    if memory is None:
        return {"ok": False, "error": "无激活的记忆后端"}
    if not hasattr(memory, 'repair_index'):
        return {"ok": False, "error": "当前记忆后端不支持此操作"}
    try:
        action = body.get("action", "all")
        if action == "repair_fts":
            result = memory.repair_index()
        elif action == "cleanup_closets":
            result = memory.cleanup_orphans()
        elif action == "all":
            repair = memory.repair_index()
            cleanup = memory.cleanup_orphans()
            result = {"repair": repair, "cleanup": cleanup}
        else:
            return {"ok": False, "error": f"未知操作: {action}"}
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}

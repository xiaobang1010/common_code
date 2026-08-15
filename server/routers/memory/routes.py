"""记忆路由：后端管理、搜索、知识图谱等 API。"""

import asyncio

from fastapi import APIRouter

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /api/memory/feature - 记忆功能开关与向量模型加载状态（纯查询）
# ---------------------------------------------------------------------------


@router.get("/api/memory/feature")
async def memory_feature_get() -> dict:
    """返回记忆功能开关与向量模型加载状态 {enabled, loading, available}。

    纯状态查询：enabled=False 时短路返回不触碰 provider（否则轮询会重新
    触发模型加载）；有后端时经 embedding_status() 只读快照读取，不调用
    会触发加载的 available 属性。
    """
    from query.services.memory.registry import get_active_memory
    from startup.config import get_global_config

    enabled = get_global_config().memory_enabled
    if not enabled:
        return {"enabled": False, "loading": False, "available": False}
    memory = get_active_memory()
    if memory is None or not hasattr(memory, "embedding_status"):
        return {"enabled": True, "loading": False, "available": False}
    status = memory.embedding_status()
    return {
        "enabled": True,
        "loading": status.get("loading", False),
        "available": status.get("available", False),
    }


# ---------------------------------------------------------------------------
# POST /api/memory/feature - 切换记忆功能开关（持久化 + 即时生效）
# ---------------------------------------------------------------------------


@router.post("/api/memory/feature")
async def memory_feature_set(body: dict) -> dict:
    """切换记忆功能开关。请求体：{"enabled": true/false}

    先整体读-改-存全局配置（禁止只传部分字段 dict——save_global_config 的
    dict 分支会全量覆写 config.json，清空 llm_base_url 等既有配置），再
    await to_thread 执行生效逻辑（插件构造含 ChromaStore/SQLite 初始化，
    同步执行会阻塞事件循环）。start_loading 只启动后台线程立即返回，模型
    加载仍异步——POST 返回时注册表已是新状态。
    """
    from query.services.memory.registry import get_registry, load_memory_plugins
    from startup.config import get_global_config, save_global_config

    enabled = bool(body.get("enabled", False))

    # 持久化（同步更新内存缓存，保证 POST 后 GET 立即一致）
    config = get_global_config()
    config.memory_enabled = enabled
    save_global_config(config)

    def apply() -> None:
        # 快速连续切换保护：生效段以最后请求为准，配置已不是本次目标则跳过
        if get_global_config().memory_enabled != enabled:
            return
        registry = get_registry()
        if enabled:
            # 开启：加载记忆插件（幂等）+ 触发 embedding 后台异步加载
            load_memory_plugins()
            memory = registry.get_active()
            if memory is not None and hasattr(memory, "start_loading"):
                memory.start_loading()
        else:
            # 关闭：先释放模型（第三方后端无 unload 则跳过）再注销后端
            memory = registry.get_active()
            if memory is not None and hasattr(memory, "unload"):
                memory.unload()
            for name in registry.list_providers():
                registry.unregister(name)

    await asyncio.to_thread(apply)
    return {"ok": True}


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

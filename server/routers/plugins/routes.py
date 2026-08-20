"""插件相关路由：列表、启停、供应商切换。

从 app.py 提取，原路由装饰器 @app.* 改为 @router.*。
各函数内部保留惰性导入，避免模块加载时拉起完整插件体系。
"""

from __future__ import annotations

from fastapi import APIRouter

import server.state

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /api/plugins - 获取已安装插件列表
# ---------------------------------------------------------------------------


@router.get("/api/plugins")
def list_plugins() -> dict:
    """返回已安装插件列表。

    返回：{"plugins": [{"name", "version", "kind", "enabled", "description",
                        "source", "skills_count", "hooks_count",
                        "commands_count", "mcp_servers_count"}]}
    """
    from startup.plugins.manager import PluginManager

    plugins = PluginManager.get_all_plugins()
    return {
        "plugins": [
            {
                "name": p.manifest.name,
                "version": p.manifest.version,
                "kind": p.manifest.kind,
                "enabled": p.enabled,
                "description": p.manifest.description,
                "source": p.manifest.source,
                "skills_count": len(p.skills_registered),
                "hooks_count": len(p.hooks_registered),
                "commands_count": len(p.commands_registered),
                "mcp_servers_count": len(p.mcp_servers_registered),
            }
            for p in plugins
        ]
    }


# ---------------------------------------------------------------------------
# POST /api/plugins/enable - 启用插件
# ---------------------------------------------------------------------------


@router.post("/api/plugins/enable")
def enable_plugin(body: dict) -> dict:
    """启用插件。请求体：{"name": "..."}"""
    from startup.plugins.manager import PluginManager

    name = body.get("name", "")
    ok = PluginManager.enable_plugin(name)
    if not ok:
        return {"ok": False, "error": f"Plugin not found: {name}"}
    return {"ok": True}


# ---------------------------------------------------------------------------
# POST /api/plugins/disable - 禁用插件
# ---------------------------------------------------------------------------


@router.post("/api/plugins/disable")
def disable_plugin(body: dict) -> dict:
    """禁用插件。请求体：{"name": "..."}"""
    from startup.plugins.manager import PluginManager

    name = body.get("name", "")
    ok = PluginManager.disable_plugin(name)
    if not ok:
        return {"ok": False, "error": f"Plugin not found: {name}"}
    return {"ok": True}


# ---------------------------------------------------------------------------
# POST /api/plugins/llm-provider/switch - 切换 LLM 供应商
# ---------------------------------------------------------------------------


@router.post("/api/plugins/llm-provider/switch")
def switch_llm_provider(body: dict) -> dict:
    """切换 LLM 供应商。请求体：{"provider": "..."}"""
    from query.services.api.providers import get_registry

    provider_name = body.get("provider", "")
    registry = get_registry()
    ok = registry.set_active(provider_name)
    if not ok:
        return {"ok": False, "error": f"LLM provider not found: {provider_name}"}

    # 更新 AppState model
    provider = registry.get_active_provider()
    if provider and server.state.app_state:
        state = server.state.app_state.get_state()
        state.model = provider.get("model", "")

    return {"ok": True, "active_provider": provider_name}


# ---------------------------------------------------------------------------
# GET /api/plugins/llm-providers - 获取可用 LLM 供应商列表
# ---------------------------------------------------------------------------


@router.get("/api/plugins/llm-providers")
def list_llm_providers() -> dict:
    """返回可用 LLM 供应商列表和当前激活的供应商。"""
    from query.services.api.providers import get_registry

    registry = get_registry()
    return {
        "providers": registry.list_providers(),
        "active": registry.get_active_name(),
    }

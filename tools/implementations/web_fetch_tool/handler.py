"""WebFetch 实现：抓取 URL → HTML 转 markdown → 带重定向提示与 TTL 缓存。"""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as md_to_markdown

from tools.implementations.runtime.errors import ToolExecutionError
from tools.implementations.web_fetch_tool.schema import WebFetchInput

# 抓取与转换限制
FETCH_TIMEOUT_S = 30
MAX_BODY_BYTES = 5_000_000  # 5MB 上限，超出直接拒绝，避免整站 dump 灌进内存
MAX_CONTENT_CHARS = 100_000  # 转换后正文截断上限
CACHE_TTL_S = 900  # 15 分钟

# 进程内缓存：url -> (过期时间戳, 已转换的 markdown)
_fetch_cache: dict[str, tuple[float, str]] = {}

_USER_AGENT = "Mozilla/5.0 (compatible; CommonCode/1.0)"


def validate_url(url: str) -> None:
    """URL 基本校验：只允许 http/https，且必须有主机名。"""
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise ToolExecutionError("url_invalid", f"URL 无法解析：{url}（{exc}）") from exc
    if parsed.scheme not in ("http", "https"):
        raise ToolExecutionError(
            "invalid_scheme",
            f"只支持 http/https URL，收到 scheme={parsed.scheme or '（空）'}：{url}。"
            "本地文件请用 Read 工具读取",
        )
    if not parsed.netloc:
        raise ToolExecutionError("url_invalid", f"URL 缺少主机名：{url}")


def _host(url: str) -> str:
    return urlparse(url).netloc.lower()


def _cross_host_redirect(history: list[httpx.Response], final_url: str) -> str | None:
    """判断是否发生过跨主机重定向，返回首个跳转到的主机对描述（供提示模型重发）。"""
    if not history:
        return None
    origin = _host(str(history[0].request.url))
    target = _host(final_url)
    if origin != target:
        return f"{origin} → {target}"
    return None


async def handle_web_fetch(inp: WebFetchInput) -> dict[str, Any]:
    """抓取并转换 URL 内容。

    返回字段：
        url: 最终地址；from_cache: 是否命中缓存；
        redirect_note: 跨主机重定向提示（无则空串）；content: markdown 正文。
    """
    validate_url(inp.url)

    now = time.time()
    cached = _fetch_cache.get(inp.url)
    if cached and cached[0] > now:
        return {
            "url": inp.url,
            "from_cache": True,
            "redirect_note": "",
            "content": cached[1],
        }

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=FETCH_TIMEOUT_S,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            resp = await client.get(inp.url)
    except httpx.TimeoutException as exc:
        raise ToolExecutionError(
            "fetch_timeout",
            f"抓取超时（{FETCH_TIMEOUT_S}s）：{inp.url}。"
            "若该站点较慢，可改用 Bash 重试或寻找其他来源",
        ) from exc
    except httpx.HTTPError as exc:
        raise ToolExecutionError(
            "fetch_failed", f"抓取失败：{inp.url}（{type(exc).__name__}: {exc}）"
        ) from exc

    final_url = str(resp.url)
    cross = _cross_host_redirect(resp.history, final_url)
    redirect_note = ""
    if cross:
        redirect_note = (
            f"内容因跨主机重定向（{cross}）被截断处理，请用最终 URL {final_url} 重新发起 WebFetch 以获取完整内容"
        )

    status = resp.status_code
    if status >= 400:
        raise ToolExecutionError(
            "http_error",
            f"抓取返回 HTTP {status}：{final_url}。"
            "若是需要登录或反爬的站点，换其他来源，不要反复重试同一地址",
        )

    content_type = resp.headers.get("content-type", "")
    raw = resp.content
    if len(raw) > MAX_BODY_BYTES:
        raise ToolExecutionError(
            "body_too_large",
            f"响应体超过 {MAX_BODY_BYTES // 1_000_000}MB 上限（{final_url}）。"
            "请用更精确的页面或 API 端点，不要抓整站资源",
        )

    if "html" in content_type.lower():
        soup = BeautifulSoup(raw, "html.parser")
        for noise in soup(["script", "style", "noscript", "svg"]):
            noise.decompose()
        markdown = md_to_markdown(str(soup), heading_style="ATX")
        markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()
    elif "json" in content_type.lower() or "text" in content_type.lower() or not content_type:
        markdown = raw.decode(resp.encoding or "utf-8", errors="replace").strip()
    else:
        markdown = (
            f"（不支持的内容类型 {content_type}，无法转换；"
            f"如需该文件本体，请改用 Bash 下载到工作区后用 Read 查看）"
        )

    if len(markdown) > MAX_CONTENT_CHARS:
        markdown = markdown[:MAX_CONTENT_CHARS] + "\n（内容过长已截断）"

    _fetch_cache[inp.url] = (now + CACHE_TTL_S, markdown)
    # 顺带清掉过期项，避免缓存只增不减
    expired = [u for u, (exp, _) in _fetch_cache.items() if exp <= now]
    for u in expired:
        _fetch_cache.pop(u, None)

    return {
        "url": final_url,
        "from_cache": False,
        "redirect_note": redirect_note,
        "content": markdown,
    }


def format_model_content(structured: dict[str, Any]) -> str:
    """把结构化结果拼成给模型的文本。"""
    parts = [f"URL: {structured['url']}"]
    if structured.get("from_cache"):
        parts.append("（命中 15 分钟缓存，未重新请求）")
    if structured.get("redirect_note"):
        parts.append(structured["redirect_note"])
    parts.append("")
    parts.append(structured["content"])
    return "\n".join(parts)

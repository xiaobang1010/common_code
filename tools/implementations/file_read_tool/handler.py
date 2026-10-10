"""Read 工具执行逻辑 — 返回结构化结果。"""

from __future__ import annotations

import asyncio
import base64
import os

from tools.implementations.file_read_tool.schema import FileReadInput
from tools.implementations.runtime.errors import (
    ToolExecutionError,
    file_not_found_error,
    not_a_file_error,
)
from tools.implementations.runtime.file_baseline import record_baseline
from tools.implementations.runtime.paths import resolve_workspace_path
from tools.protocol import ToolUseContext

# 不带 offset/limit 时默认读取的最大行数，避免整文件灌进上下文
DEFAULT_READ_LINES = 2000

# 图片扩展名 → 格式标识（占位文案用后者）；命中走视觉分支，绝不再按文本解码
_IMAGE_SUFFIXES = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".gif": "gif", ".webp": "webp"}

# 图片大小上限：与聊天附件同口径（按解码后字节计）；常量在工具层自定义，
# 避免 tools 反向依赖 server 层
_MAX_IMAGE_DECODED_BYTES = 5 * 1024 * 1024


async def handle_read(inp: FileReadInput, context: ToolUseContext) -> dict:
    """读取文件内容，支持按行号范围分段读取。

    Returns:
        结构化结果字典：
        {
            "file_path": 绝对路径,
            "content": cat -n 格式的带行号文本（可能带分段提示）,
            "start_line": 起始行号, "end_line": 结束行号,
            "total_lines": 文件总行数,
            "mtime": 整数秒, "size": 字节数,
        }

    Raises:
        ToolExecutionError: 路径越界 / 文件不存在 / 不是文件
    """
    # 磁盘 IO 丢线程池执行，读大文件时不阻塞事件循环（心跳、权限桥都在上面）
    return await asyncio.to_thread(_read_sync, inp)


def _read_sync(inp: FileReadInput) -> dict:
    """同步读文件内核：由 handle_read 放入线程池执行。"""
    # 路径沙箱：解析并校验工作区边界
    file_path = resolve_workspace_path(inp.file_path)

    if not file_path.exists():
        raise file_not_found_error(inp.file_path)
    if not file_path.is_file():
        raise not_a_file_error(inp.file_path)

    st = file_path.stat()

    # 图片分支：能力/大小闸门通过后返回 data URL，由 tool.py 转成视觉注入
    suffix = file_path.suffix.lower()
    if suffix in _IMAGE_SUFFIXES:
        return _read_image_sync(file_path, suffix, st)

    # 分段读取：offset 为 1 起始行号；不带 limit 时默认只读前 DEFAULT_READ_LINES 行
    offset = max(1, inp.offset if inp.offset is not None else 1)
    has_explicit_limit = inp.limit is not None
    limit = inp.limit if has_explicit_limit else DEFAULT_READ_LINES
    end_line = offset + limit - 1

    # 按行流式读取：只保留目标行区间，不整文件载入内存
    selected: list[str] = []
    total_lines = 0
    with file_path.open(encoding="utf-8", errors="replace") as f:
        for raw in f:
            total_lines += 1
            if offset <= total_lines <= end_line:
                selected.append(raw.rstrip("\r\n"))

    num_width = len(str(max(total_lines, 1)))
    numbered = [
        f"{i:>{num_width}}→{line}"
        for i, line in enumerate(selected, start=offset)
    ]
    content = "\n".join(numbered)

    # 默认读取被截断时提示分段读
    if not has_explicit_limit and total_lines > end_line:
        content += f"\n（文件共 {total_lines} 行，仅显示前 {end_line} 行，请用 offset/limit 分段读取）"

    # 登记基线：后续 Write/Edit 覆盖该文件时系统自动采用（模型无需回传参数）
    mtime = int(st.st_mtime)
    record_baseline(str(file_path), mtime, st.st_size)

    return {
        "file_path": str(file_path),
        "content": content,
        "start_line": offset,
        "end_line": min(end_line, total_lines),
        "total_lines": total_lines,
        "mtime": mtime,
        "size": st.st_size,
    }


def _read_image_sync(file_path, suffix: str, st) -> dict:
    """图片读取内核：视觉能力与大小闸门 → base64 data URL。

    工具结果正文只放占位文案，图片块经 new_messages 注入（见 tool.py）；
    图片不参与行号基线登记（Edit 对其无意义）。
    """
    from query.services.api.client import get_default_model
    from startup.model.config import get_model_config

    model = os.environ.get("COMMON_CODE_MODEL") or get_default_model()
    if not get_model_config(model).supports_vision:
        raise ToolExecutionError(
            code="vision_unsupported",
            message="这是图片文件，当前模型不支持图片输入，无法呈现其内容。",
        )
    if st.st_size > _MAX_IMAGE_DECODED_BYTES:
        raise ToolExecutionError(
            code="image_too_large",
            message=(
                f"图片大小 {st.st_size} 字节超过上限 {_MAX_IMAGE_DECODED_BYTES} 字节，"
                "无法读取；请先压缩或裁剪后重试。"
            ),
        )
    fmt = _IMAGE_SUFFIXES[suffix]
    data_url = f"data:image/{fmt};base64," + base64.b64encode(file_path.read_bytes()).decode("ascii")
    return {
        "kind": "image",
        "file_path": str(file_path),
        "fmt": fmt,
        "size": st.st_size,
        "mtime": int(st.st_mtime),
        "data_url": data_url,
    }


def format_model_content(structured: dict) -> str:
    """结构化结果 → 给模型的文本。

    一致性基线（mtime/size）置于开头，避免被结果预算按头部保留截断。
    基线已由系统自动登记，此处展示仅供模型知悉文件状态。
    """
    if structured.get("kind") == "image":
        return f"[Attached image/{structured.get('fmt', 'png')}: Read image]"
    mtime = structured.get("mtime")
    size = structured.get("size")
    header = ""
    if mtime is not None and size is not None:
        header = f"[文件基线] mtime={mtime} size={size}\n\n"
    return header + structured.get("content", "")

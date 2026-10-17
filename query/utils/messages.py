"""消息切片与清洗辅助函数。

提供 compact boundary 查找和切片功能，让 query loop 和 REPL 共用同一套
"从最后一个压缩边界开始取活跃窗口"的语义；
提供悬空 tool_calls 清洗，保证发给模型与写入 DB 的历史序列合法。
"""

from __future__ import annotations

import time


# ---------------------------------------------------------------------------
# boundary 识别
# ---------------------------------------------------------------------------

# compact_conversation() 生成的 boundary marker content 前缀
# 形如: "[Compact Boundary — auto — pre-compact tokens: 12345]"
_COMPACT_BOUNDARY_PREFIX = "[Compact Boundary"


def is_compact_boundary_message(message: dict) -> bool:
    """判断消息是否为 compact boundary marker。

    boundary marker 的 role 为 system，content 以 "[Compact Boundary" 开头。
    """
    if not isinstance(message, dict):
        return False
    if message.get("role") != "system":
        return False
    content = message.get("content", "")
    if not isinstance(content, str):
        return False
    return content.startswith(_COMPACT_BOUNDARY_PREFIX)


def extract_text_from_content(content, image_placeholder: str = "") -> str:
    """从消息 content（字符串或 parts 数组）提取纯文本。

    parts 形态拼接全部 text 块；image_placeholder 非空时图片块以该占位符计入
    （供 hook 入参、标题提取等纯文本消费方使用）。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif btype in ("image", "image_url", "input_image", "image_ref") and image_placeholder:
                parts.append(image_placeholder)
        return "\n".join(parts)
    return ""


# ---------------------------------------------------------------------------
# 查找与切片
# ---------------------------------------------------------------------------


def find_last_compact_boundary_index(messages: list[dict]) -> int:
    """从后往前查找最后一个 compact boundary marker 的索引。

    Args:
        messages: 消息列表

    Returns:
        最后一个 boundary 的索引；未找到返回 -1
    """
    for i in range(len(messages) - 1, -1, -1):
        if is_compact_boundary_message(messages[i]):
            return i
    return -1


def get_messages_after_compact_boundary(messages: list[dict]) -> list[dict]:
    """从最后一个 compact boundary 开始切片（含 boundary）。

    无 boundary 时返回全部消息。

    Args:
        messages: 完整消息列表（可能含历史 boundary marker）

    Returns:
        最后一个 boundary 起的切片；无 boundary 则返回原列表
    """
    boundary_idx = find_last_compact_boundary_index(messages)
    if boundary_idx == -1:
        return messages
    return messages[boundary_idx:]


# ---------------------------------------------------------------------------
# assistant 轮组分组与压缩 pivot
# ---------------------------------------------------------------------------


def group_rounds(messages: list[dict]) -> list[list[dict]]:
    """按 assistant 轮次分组（压缩保留切点用）。

    规则：每条 assistant 消息开启新组并归入其前 pending 的 user 消息为组首；
    tool 消息跟随当前组；组前悬空 tool 结果自成一组；末尾无 assistant 的
    user 消息自成一组。分组不剔除 skill 正文，由调用方决定取舍。
    """
    groups: list[list[dict]] = []
    pending: list[dict] = []
    for msg in messages:
        role = msg.get("role", "")
        if role == "assistant":
            groups.append(pending + [msg])
            pending = []
        elif role == "tool":
            if groups:
                groups[-1].append(msg)
            else:
                groups.append([msg])
        elif role == "user":
            pending.append(msg)
        else:
            # 其他角色（system 边界等）不应进分组：原样挂 pending，
            # 由调用方保证传入的列表已过滤
            pending.append(msg)
    if pending:
        groups.append(pending)
    return groups


def compact_pivot_index(messages: list[dict], keep_groups: int) -> int:
    """计算压缩 pivot（插入边界的全局下标）。

    在活跃窗口（最后一个压缩边界起）内按 assistant 轮组分组，保留最近
    keep_groups 组原文；pivot 落在首个保留组的第一条消息处，pivot 之前的
    活跃消息进入被压缩区。keep_groups ≤ 0 时 pivot 落在活跃窗口首条
    非 system 消息处（全量摘要，仅旧边界留在原位）。

    Raises:
        RuntimeError: 活跃窗口内无可压缩消息（保留组覆盖全部或历史过短）
    """
    boundary_idx = find_last_compact_boundary_index(messages)
    active_offset = boundary_idx + 1  # 无 boundary 时为 0
    active = messages[active_offset:]

    # 活跃窗口内非 system 消息参与分组（旧摘要 role=user 正常入组）
    non_system = [m for m in active if m.get("role") != "system"]
    if not non_system:
        raise RuntimeError("Not enough messages to compact.")

    groups = group_rounds(non_system)
    if keep_groups <= 0:
        # 全量摘要：保留 0 组，被压缩区覆盖全部非 system 消息
        first_kept_role_idx = len(active)
    else:
        keep_groups = min(keep_groups, len(groups))
        if keep_groups >= len(groups):
            raise RuntimeError("Not enough messages to compact.")
        first_kept_msg = groups[-keep_groups][0]
        # 按对象身份定位（内容相同的重复消息用相等比较会错指到最早一条）
        first_kept_role_idx = next(
            i for i, m in enumerate(active) if m is first_kept_msg
        )

    # pivot 必须落在非 system 消息处（插入点之前的消息保持原序）
    return active_offset + first_kept_role_idx

# ---------------------------------------------------------------------------
# skill 正文消息识别
# ---------------------------------------------------------------------------

# skill 正文消息用 <system-reminder> 包裹
_SYSTEM_REMINDER_PREFIX = "<system-reminder>"


def is_skill_message(message: dict) -> bool:
    """判断消息是否为 skill 正文（不参与压缩，需原样保留）。

    skill 正文由 SkillTool 注入，格式为 role=user、content 以
    <system-reminder> 开头。user_context 虽然也用此格式，但它是临时
    注入不写回引擎，因此引擎持久化消息中的此类消息只有 skill 正文。
    """
    if not isinstance(message, dict):
        return False
    if message.get("role") != "user":
        return False
    content = message.get("content", "")
    if not isinstance(content, str):
        return False
    return content.strip().startswith(_SYSTEM_REMINDER_PREFIX)


# ---------------------------------------------------------------------------
# 悬空 tool_calls 清洗（两遍全表集合运算，不依赖行位置）
# ---------------------------------------------------------------------------

# 缺失工具结果补入的合成文本：中断/崩溃导致的收尾不全是事实，如实告知模型
ABORTED_TOOL_RESULT_CONTENT = "[执行被中断，无结果]"


def _synthetic_tool_result(tool_call_id: str) -> dict:
    """构造一条合成 tool 结果消息（形状与 tool_result_to_openai_message 对齐）。

    _synthetic 标记供择优判定（真实结果优先于合成），随下划线剥离不发模型。
    """
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": ABORTED_TOOL_RESULT_CONTENT,
        "_ts": time.time() * 1000,
        "_synthetic": True,
    }


def _is_synthetic_result(msg: dict) -> bool:
    """识别合成中断结果行：新代码带 _synthetic 标记，存量行以占位正文判定。"""
    return (
        msg.get("_synthetic") is True
        or msg.get("content") == ABORTED_TOOL_RESULT_CONTENT
    )


def _tool_call_ids(msg: dict) -> list[str]:
    """提取 assistant 消息的调用 id 列表（保持声明序）。"""
    return [
        tc.get("id") for tc in msg.get("tool_calls") or []
        if isinstance(tc, dict) and tc.get("id")
    ]


def expand_tool_attachments(messages: list[dict]) -> list[dict]:
    """持久形态 → 协议形态投影：把 tool 行的 _attachments 展开为紧随其后的
    图片 parts user 行（OpenAI 形态；Anthropic 转换器的合并窗口会把它并入
    同一 user turn）。

    纯函数、幂等：输出的宿主行剥离 _attachments（投影形态），对输出重跑
    同输出；展开行时间戳透传宿主 _ts（宿主缺省不带，避免两次构建漂移）；
    缺文件附件（data_url 为空）跳过——结果行占位正文已说明。
    不修改传入列表。
    """
    out: list[dict] = []
    for msg in messages:
        if not isinstance(msg, dict):
            out.append(msg)
            continue
        atts = msg.get("_attachments")
        if not isinstance(atts, list) or not atts:
            out.append(msg)
            continue
        # 宿主行投影：剥内部附件字段，其余原样（含 _ts/_synthetic 等约定）
        host = {k: v for k, v in msg.items() if k != "_attachments"}
        out.append(host)
        placeholder = str(msg.get("content") or "")
        blocks: list[dict] = []
        for att in atts:
            url = att.get("data_url") if isinstance(att, dict) else None
            if url:
                blocks.append({"type": "text", "text": placeholder})
                blocks.append({"type": "image_url", "image_url": {"url": url}})
        if blocks:
            expanded: dict = {"role": "user", "content": blocks}
            if msg.get("_ts") is not None:
                expanded["_ts"] = msg["_ts"]
            out.append(expanded)
    return out


def sanitize_dangling_tool_calls(messages: list[dict]) -> list[dict]:
    """修复异常历史序列，返回新的消息列表（两遍全表集合运算，不依赖行位置）。

    任务被中断（/api/abort 的 task.cancel 落在工具执行阶段）、输出超限
    恢复（finish_reason=length 丢弃当轮工具结果）或历史经旧版位置式清洗
    落库时，序列可能存在四类残缺形态：
      a) assistant 带 tool_calls 但部分 id 没有对应 tool 结果；
      b) 同一 tool_call_id 存在多条结果（历史合成与真实双份）；
      c) tool 结果行找不到任何调用归属（孤儿）；
      d) 历史以悬空调用结尾。

    调用组 = assistant(tool_calls) 起、至下一个 assistant(tool_calls) 前的
    全部行。修复规则：
      - 同 id 多条结果择优保留：真实结果（正文非中断占位、无合成标记）
        优先于合成，同优先级保首条，落选行剔除；
      - 缺失 id 的合成结果插在组内最后一条结果行之后（无结果行则紧跟
        调用行）——补齐位置不依赖「结果行是否被注入行隔开」，注入行
        夹在结果之间不再诱发错位或双份；
      - 无主结果行（id 不属于任何调用组）保持原样（沿用旧版保守语义）。
    对合法历史逐条等价（不重排、不补写、不剔除）；幂等（对输出再跑一遍
    结果相同）。不修改传入列表。
    """
    n = len(messages)
    group_starts: list[int] = []
    group_ids: dict[int, list[str]] = {}
    for i, m in enumerate(messages):
        if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls"):
            group_starts.append(i)
            group_ids[i] = _tool_call_ids(m)

    out: list[dict] = []
    cursor = 0  # 已消费的输入游标
    for k, start in enumerate(group_starts):
        group_end = group_starts[k + 1] if k + 1 < len(group_starts) else n
        # 组起点前的独立行透传（无主结果行同样保留，见组内分支）
        while cursor < start:
            out.append(messages[cursor])
            cursor += 1
        out.append(messages[start])
        # 组内择优：每个本组 id 保留最优结果行（真实优先、同优先级保首条）
        best: dict[str, dict] = {}
        for j in range(start + 1, group_end):
            inner = messages[j]
            if not (isinstance(inner, dict) and inner.get("role") == "tool"):
                continue
            cid = inner.get("tool_call_id")
            if cid not in group_ids[start]:
                continue  # 无主结果行不参与本组择优，重放时原样保留
            cur = best.get(cid)
            if cur is None or (_is_synthetic_result(cur) and not _is_synthetic_result(inner)):
                best[cid] = inner
        # 组内重放固定两段：结果行段（本组择优命中 + 无主结果行，原相对序）
        # 在前，注入行段随后（原相对序），缺失合成补在结果行段尾。新写入方
        # 的合法形态（结果行连续、注入行置组后）经此恒等；存量「注入行夹在
        # 结果行之间」的错序形态被理顺——夹心不再可见，也不产生合成+真实双份
        emitted: set[str] = set()
        for j in range(start + 1, group_end):
            inner = messages[j]
            if not (isinstance(inner, dict) and inner.get("role") == "tool"):
                continue
            cid = inner.get("tool_call_id")
            if cid in group_ids[start]:
                if best.get(cid) is inner and cid not in emitted:
                    out.append(inner)
                    emitted.add(cid)
            else:
                out.append(inner)  # 无主结果行留在结果区（旧版保守语义）
        out.extend(
            _synthetic_tool_result(cid)
            for cid in group_ids[start]
            if cid not in best
        )
        for j in range(start + 1, group_end):
            inner = messages[j]
            if isinstance(inner, dict) and inner.get("role") == "tool" \
                    and inner.get("tool_call_id") in group_ids[start]:
                continue  # 本组结果行已在结果行段决策（择优落选者剔除）
            out.append(inner)
        cursor = group_end
    # 尾段透传（无主结果行同样保留——与组内保守语义一致，任何输入不抛错）
    while cursor < n:
        out.append(messages[cursor])
        cursor += 1
    return out

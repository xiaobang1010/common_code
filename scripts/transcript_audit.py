"""转录审计：四项异常形态直查存储，供修复验收与发布后判定门取数。

背景：会话转录曾以整表快照+平铺列表存储，并行读图轮次的注入块与位置式
清洗器叠加会产生同 tool_call_id 双份结果、工具组中夹游离消息块、尾部悬空
调用；队列转正与落库分离还会留下 promoted 无消息的吞记录终态。

四项读数（口径与修复类 spec 判定门一致，目标全部为 0）：
  ① 重复 tool_call_id 组数 —— 同一 tool_call_id 对应多条 tool 结果行
  ② 夹心会话数 —— 调用组未收齐前遇到非 tool 行（依赖行序，仅测会话库与
     主转录导出——两者为清洗后的规范序；子代理 sidechain 是定序旁路，不适用）
  ③ 悬空会话数 —— 存在调用组缺结果（集合匹配，位置无关）
  ④ promoted 未落库行数 —— session_input 已转正但消息体不在库

存储自适应：行式 message 表存在且会话已迁移时读行表，否则读 sessions.messages
旧快照列——修复前后用同一把尺子量。

用法：
  python scripts/transcript_audit.py [--db PATH] [--transcripts DIR] [--subagents DIR] [--json]
默认路径 ~/.agent/sessions.db、~/.agent/transcripts、~/.agent/subagents。
退出码：四项读数为 0 → 0，任一非 0 → 1（sidechain ①③ 仅信息展示，不计退出码）。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any


def _load_session_messages(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """按会话加载消息列表：优先行式 message 表（已迁移），回退旧快照列。"""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    out: dict[str, list[dict]] = {}
    if "message" in tables:
        rows = conn.execute(
            "SELECT m.session_id, m.data FROM message m ORDER BY m.session_id, m.sequence"
        ).fetchall()
        for sid, data in rows:
            try:
                out.setdefault(sid, []).append(json.loads(data))
            except json.JSONDecodeError:
                continue
    # 旧快照列：仅取尚无行数据的会话（行式优先）
    migrated_sessions = set(out)
    for sid, blob in conn.execute("SELECT id, messages FROM sessions").fetchall():
        if sid in migrated_sessions:
            continue
        try:
            msgs = json.loads(blob or "[]")
        except (json.JSONDecodeError, TypeError):
            msgs = []
        # 已迁移但无行的空会话不覆盖；未迁移会话以快照列为准
        out[sid] = [m for m in msgs if isinstance(m, dict)]
    return out


def _is_content_image(msg: dict) -> bool:
    content = msg.get("content")
    if not isinstance(content, list):
        return False
    return any(
        isinstance(b, dict) and b.get("type") in ("image_url", "image", "image_ref")
        for b in content
    )


def audit_message_list(msgs: list[dict]) -> dict[str, Any]:
    """对单个会话消息列表算四项（④除外）形态读数。"""
    dup_ids: list[str] = []
    seen_results: dict[str, int] = {}
    for m in msgs:
        if m.get("role") == "tool" and m.get("tool_call_id"):
            seen_results[m["tool_call_id"]] = seen_results.get(m["tool_call_id"], 0) + 1
    dup_ids = sorted(cid for cid, n in seen_results.items() if n > 1)

    sandwich = False
    pending: set[str] = set()
    for m in msgs:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            pending = {tc.get("id") for tc in m["tool_calls"] if isinstance(tc, dict) and tc.get("id")}
        elif role == "tool":
            pending.discard(m.get("tool_call_id"))
        else:
            if pending:
                sandwich = True
            pending = set()
    # 组尾未齐：悬空（末尾 pending 非空）
    tail_dangling = bool(pending)

    # ③ 集合口径（位置无关）：每个调用组的 id 与全表 tool 行 id 求差
    all_tool_ids = {m.get("tool_call_id") for m in msgs if m.get("role") == "tool"}
    set_dangling = any(
        {tc.get("id") for tc in m.get("tool_calls") or [] if isinstance(tc, dict) and tc.get("id")} - all_tool_ids
        for m in msgs
        if m.get("role") == "assistant" and m.get("tool_calls")
    )
    return {
        "dup_groups": len(dup_ids),
        "dup_ids": dup_ids,
        "sandwich": sandwich,
        "tail_dangling": tail_dangling,
        "set_dangling": set_dangling,
    }


def audit_db(db_path: Path) -> dict[str, Any]:
    """会话库四项读数。"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        sessions = _load_session_messages(conn)
        dup_total = 0
        sandwich_sessions: list[str] = []
        dangling_sessions: list[str] = []
        promoted_lost: list[dict] = []

        for sid, msgs in sessions.items():
            r = audit_message_list(msgs)
            dup_total += r["dup_groups"]
            if r["sandwich"]:
                sandwich_sessions.append(sid)
            if r["set_dangling"]:
                dangling_sessions.append(sid)
            # ④ promoted 对账：新格式挂 _input_id，唤醒合并挂 _merged_input_ids，旧格式挂 _ts
            tables = {x[0] for x in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "session_input" not in tables:
                continue
            ts_set = {str(m.get("_ts")) for m in msgs if isinstance(m, dict)}
            input_ids = set()
            for m in msgs:
                if not isinstance(m, dict):
                    continue
                if m.get("_input_id"):
                    input_ids.add(str(m["_input_id"]))
                for mid in m.get("_merged_input_ids") or []:
                    input_ids.add(str(mid))
            for row in conn.execute(
                "SELECT id, kind, promoted_message_id FROM session_input "
                "WHERE session_id = ? AND status = 'promoted'",
                (sid,),
            ).fetchall():
                pmid = str(row["promoted_message_id"] or "")
                if row["id"] in input_ids or pmid in input_ids or pmid in ts_set:
                    continue
                promoted_lost.append(
                    {"session": sid, "row": row["id"], "kind": row["kind"], "promoted_message_id": pmid}
                )
        return {
            "dup_groups": dup_total,
            "sandwich_sessions": sandwich_sessions,
            "dangling_sessions": dangling_sessions,
            "promoted_lost": promoted_lost,
        }
    finally:
        conn.close()


def _iter_jsonl(path: Path):
    bad_tail = False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return [], True
    msgs = []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            msgs.append(json.loads(line))
        except json.JSONDecodeError:
            if i == len(lines) - 1:
                bad_tail = True
            continue
    return msgs, bad_tail


def audit_transcripts(dir_path: Path) -> dict[str, Any]:
    """主转录导出四项（与库同口径：①重复 ②夹心 ③悬空；④不适用）。"""
    if not dir_path.is_dir():
        return {"files": 0, "dup_groups": 0, "sandwich": [], "dangling": [], "bad_tail": []}
    dup_total = 0
    sandwich: list[str] = []
    dangling: list[str] = []
    bad_tail: list[str] = []
    for f in sorted(dir_path.glob("*.jsonl")):
        msgs, truncated = _iter_jsonl(f)
        if truncated:
            bad_tail.append(f.name)
        if not msgs:
            continue
        r = audit_message_list([m for m in msgs if isinstance(m, dict)])
        dup_total += r["dup_groups"]
        if r["sandwich"]:
            sandwich.append(f.stem)
        if r["set_dangling"]:
            dangling.append(f.stem)
    return {"files": len(list(dir_path.glob("*.jsonl"))), "dup_groups": dup_total,
            "sandwich": sandwich, "dangling": dangling, "bad_tail": bad_tail}


def audit_sidechains(dir_path: Path) -> dict[str, Any]:
    """子代理 sidechain：定序旁路不适用 ②③（结果行先于调用行是文件形态本身），
    仅信息性核 ① 重复 tool_call_id；不计入判定门退出码。"""
    if not dir_path.is_dir():
        return {"files": 0, "dup_groups": 0}
    dup_total = 0
    files = 0
    for agent_dir in sorted(p for p in dir_path.iterdir() if p.is_dir()):
        tpath = agent_dir / "transcript.jsonl"
        if not tpath.is_file():
            continue
        files += 1
        msgs, _ = _iter_jsonl(tpath)
        r = audit_message_list([m for m in msgs if isinstance(m, dict)])
        dup_total += r["dup_groups"]
    return {"files": files, "dup_groups": dup_total}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    base = Path(os.path.expanduser("~")) / ".agent"
    parser.add_argument("--db", type=Path, default=base / "sessions.db")
    parser.add_argument("--transcripts", type=Path, default=base / "transcripts")
    parser.add_argument("--subagents", type=Path, default=base / "subagents")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出全部读数")
    args = parser.parse_args(argv)

    db = audit_db(args.db) if args.db.is_file() else {}
    tr = audit_transcripts(args.transcripts)
    sc = audit_sidechains(args.subagents)

    readings = {
        "dup_groups": int(db.get("dup_groups", 0)),
        "sandwich_sessions": len(db.get("sandwich_sessions", [])),
        "dangling_sessions": len(db.get("dangling_sessions", [])),
        "promoted_lost_rows": len(db.get("promoted_lost", [])),
    }

    if args.json:
        payload = {
            "readings": readings,
            "db": db,
            "transcripts": tr,
            "sidechain_info": sc,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("== 会话库（判定门主读数，目标全 0）==")
        print(f"① 重复 tool_call_id 组数 : {readings['dup_groups']}")
        for sid in db.get("sandwich_sessions", []):
            print(f"② 夹心会话 : {sid}")
        print(f"② 夹心会话数 : {readings['sandwich_sessions']}")
        for sid in db.get("dangling_sessions", []):
            print(f"③ 悬空会话 : {sid}")
        print(f"③ 悬空会话数 : {readings['dangling_sessions']}")
        for row in db.get("promoted_lost", []):
            print(f"④ promoted 未落库 : {row['session'][:8]}…/{row['row']} kind={row['kind']}")
        print(f"④ promoted 未落库行数 : {readings['promoted_lost_rows']}")
        print("== 主转录导出（库的投影，应同步为 0）==")
        print(f"文件数 {tr['files']}｜重复 {tr['dup_groups']}｜夹心 {len(tr['sandwich'])}｜悬空 {len(tr['dangling'])}｜尾部截断 {len(tr['bad_tail'])}")
        print("== sidechain（信息项，不计退出码）==")
        print(f"文件数 {sc['files']}｜重复 {sc['dup_groups']}")

    clean = all(v == 0 for v in readings.values())
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())

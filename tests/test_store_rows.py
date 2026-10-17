"""行式转录存储单测（session-store-rebuild 阶段一）。

覆盖：行读写往返与幂等、sequence 密集、附件外部化与缺文件降级、
转正事务（含竞态剔除与失败回滚）、截断翻 discarded、存量迁移幂等、
启动对账自愈（新格式/旧 _ts 命中规范化/真丢失补写）。
"""

from __future__ import annotations

import base64
import json
import sqlite3

import pytest

from session.store import SessionStore

# 1x1 PNG 的最小 base64（合法 padding）
PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
PNG_URL = f"data:image/png;base64,{PNG_B64}"


@pytest.fixture
def store(workspace, monkeypatch):
    import server.state

    s = SessionStore(db_path=workspace / "sessions.db")
    s.add_workspace(str(workspace))
    monkeypatch.setattr(server.state, "session_store", s)
    monkeypatch.setattr(server.state, "running_runs", {})
    return s


def test_replace_load_roundtrip_and_idempotent(workspace, store):
    """整批重建：行读写往返保形；同一列表连调两次行数内容不变。"""
    sid = store.create_session(str(workspace)).id
    msgs = [
        {"role": "user", "content": "q", "_ts": 1.0},
        {"role": "user", "content": [
            {"type": "text", "text": "看图"},
            {"type": "image_url", "image_url": {"url": PNG_URL}},
        ], "_ts": 2.0},
    ]
    store.replace_messages_tx(sid, msgs)
    first = store.load_messages(sid)
    assert first[0] == msgs[0]
    assert first[1]["content"][1]["type"] == "image_url"
    assert first[1]["content"][1]["image_url"]["url"] == PNG_URL

    store.replace_messages_tx(sid, msgs)
    conn = sqlite3.connect(str(workspace / "sessions.db"))
    n = conn.execute("SELECT COUNT(*) FROM message WHERE session_id=?", (sid,)).fetchone()[0]
    files = list((workspace / "attachments").glob("*"))
    conn.close()
    assert n == 2, "幂等：同一列表两次重建行数不变"
    assert len(files) == 1, "同图内容寻址只落一份文件"


def test_sequence_dense_and_parts_rows(workspace, store):
    """sequence 0..n-1 密集无洞；图片部件落 part 行并指向附件。"""
    sid = store.create_session(str(workspace)).id
    msgs = [
        {"role": "user", "content": "q", "_ts": 1.0},
        {"role": "assistant", "content": "a", "_ts": 2.0},
        {"role": "user", "content": [
            {"type": "text", "text": "x"},
            {"type": "image_url", "image_url": {"url": PNG_URL}},
        ], "_ts": 3.0},
    ]
    store.replace_messages_tx(sid, msgs)
    conn = sqlite3.connect(str(workspace / "sessions.db"))
    seqs = [r[0] for r in conn.execute(
        "SELECT sequence FROM message WHERE session_id=? ORDER BY sequence", (sid,)
    )]
    parts = [dict(kind=r[0], data=json.loads(r[1])) for r in conn.execute(
        "SELECT kind, data FROM part WHERE session_id=?", (sid,)
    )]
    conn.close()
    assert seqs == [0, 1, 2]
    assert len(parts) == 1 and parts[0]["kind"] == "image"
    assert parts[0]["data"]["slot"] == "content" and parts[0]["data"]["mime"] == "image/png"


def test_missing_attachment_file_degrades(workspace, store):
    """附件文件缺失：加载降级为占位文本，不抛错不丢消息。"""
    sid = store.create_session(str(workspace)).id
    store.replace_messages_tx(sid, [
        {"role": "user", "content": [
            {"type": "text", "text": "看图"},
            {"type": "image_url", "image_url": {"url": PNG_URL}},
        ]}
    ])
    for f in (workspace / "attachments").glob("*"):
        f.unlink()
    msgs = store.load_messages(sid)
    assert msgs[0]["content"][1] == {"type": "text", "text": "[图片文件缺失]"}


def test_tool_attachments_externalized_and_roundtrip(workspace, store):
    """图片宿主绑定（_attachments）外部化：附件文件 + part 行 + 加载还原 data URL。"""
    sid = store.create_session(str(workspace)).id
    tool_row = {
        "role": "tool", "tool_call_id": "c1",
        "content": "[Attached image/png: Read image]",
        "_attachments": [{"mime": "image/png", "data_url": PNG_URL}],
    }
    store.replace_messages_tx(sid, [
        {"role": "user", "content": "读图"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "Read", "arguments": "{}"}},
        ]},
        tool_row,
    ])

    conn = sqlite3.connect(str(workspace / "sessions.db"))
    parts = [json.loads(r[0]) for r in conn.execute(
        "SELECT data FROM part WHERE session_id=? AND kind='image'", (sid,)
    ).fetchall()]
    files = list((workspace / "attachments").glob("*"))
    conn.close()
    assert len(parts) == 1 and parts[0]["slot"] == "attachments"
    assert len(files) == 1, "附件字节落盘一份"

    loaded = store.load_messages(sid)
    atts = loaded[2]["_attachments"]
    assert atts[0]["mime"] == "image/png"
    assert atts[0]["data_url"] == PNG_URL, "宿主附件加载还原 data URL"
    assert loaded[2]["content"] == "[Attached image/png: Read image]"


def test_promote_tx_keeps_history_promoted_rows(workspace, store):
    """含既往转正行的会话再次起轮（完整列表重入事务）：
    历史已 promoted 行不得被误判为竞态输家而剔除——历史无损。"""
    sid = store.create_session(str(workspace)).id
    q1 = store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "队列A"})
    store.promote_inputs_tx(sid, [{"role": "user", "content": "初始"}, *store.peek_queued_inputs(sid)])

    q2 = store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "队列B"})
    # 第二次起轮：完整目标列表（含 q1 的历史转正消息 + 新 peek 的 q2）
    store.promote_inputs_tx(sid, [*store.load_messages(sid), *store.peek_queued_inputs(sid)])

    contents = [m.get("content") for m in store.get_session(sid).messages]
    assert "队列A" in contents, "历史转正行不得被误剔（曾回归为每次起轮丢历史）"
    assert "队列B" in contents
    assert store.get_promoted_input(q1)["status"] == "promoted"
    assert store.get_promoted_input(q2)["status"] == "promoted"


def test_promote_tx_history_lossless_and_discard(workspace, store):
    """含历史会话转正：行数=前缀+转正行、promoted_message_id=_input_id；
    截断把被删区间 promoted 行翻 discarded/truncated 且消息移除。"""
    sid = store.create_session(str(workspace)).id
    store.replace_messages_tx(sid, [{"role": "user", "content": "旧问题"}])

    q1 = store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "补充"})
    store.promote_inputs_tx(sid, [*store.load_messages(sid), *store.peek_queued_inputs(sid)])
    st = store.get_promoted_input(q1)
    assert st["status"] == "promoted" and st["promoted_message_id"] == q1
    assert len(store.get_session(sid).messages) == 2

    q2 = store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "待删"})
    store.promote_inputs_tx(sid, [*store.load_messages(sid), *store.peek_queued_inputs(sid)])
    trimmed = [m for m in store.load_messages(sid) if m.get("_input_id") != q2]
    store.promote_inputs_tx(sid, trimmed, discarded_row_ids=[q2])
    assert store.get_promoted_input(q2)["status"] == "discarded"
    assert store.get_promoted_input(q2)["status_reason"] == "truncated"
    assert all(m.get("_input_id") != q2 for m in store.get_session(sid).messages)


def test_promote_tx_race_loser_message_dropped(workspace, store):
    """撤回与转正竞态：输家（已被撤回）的消息不得混进重建列表。"""
    sid = store.create_session(str(workspace)).id
    q = store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "竞争行"})
    peeked = store.peek_queued_inputs(sid)
    assert store.withdraw_queued_input(q) is not None  # 竞态赢家：撤回
    store.promote_inputs_tx(sid, [{"role": "user", "content": "新提问"}, *peeked], promoted_row_ids=[q])
    contents = [m.get("content") for m in store.get_session(sid).messages]
    assert "竞争行" not in contents, "已撤回行的消息不得注入"
    assert store.get_promoted_input(q)["status"] == "cancelled"


def test_promote_tx_rollback_keeps_queued(workspace, store, monkeypatch):
    """消息写入失败：整体回滚，行保持 queued，无 promoted-无消息终态。"""
    sid = store.create_session(str(workspace)).id
    q = store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "x"})
    peeked = store.peek_queued_inputs(sid)

    def boom(*a, **kw):
        raise RuntimeError("写入失败")

    monkeypatch.setattr(store, "_replace_within", boom)
    with pytest.raises(RuntimeError):
        store.promote_inputs_tx(sid, [{"role": "user", "content": "n"}, *peeked])
    assert store.get_promoted_input(q)["status"] == "queued"


def test_migrate_legacy_blob_idempotent(workspace, store):
    """存量快照迁移：清洗+行化+备份转存；跑两遍行数不变、无重复行；
    原生已有行会话（行式真相源）不被快照列重建覆盖。"""
    # 原生行会话：行表已有数据（守卫保护对象）
    sid_native = store.create_session(str(workspace)).id
    store.replace_messages_tx(sid_native, [{"role": "user", "content": "原生行消息", "_ts": 9.0}])

    # 待迁移会话：仅快照列有数据
    sid = store.create_session(str(workspace)).id
    legacy = [
        {"role": "user", "content": "旧问题", "_ts": 1.0},
        {"role": "assistant", "content": "", "_ts": 2.0, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "T", "arguments": "{}"}},
        ]},
        {"role": "user", "content": "恢复", "_ts": 3.0},
    ]
    conn = sqlite3.connect(str(workspace / "sessions.db"))
    conn.execute("UPDATE sessions SET messages=? WHERE id=?", (json.dumps(legacy), sid))
    conn.commit()
    conn.close()

    assert store.migrate_legacy_blobs() >= 1
    # 原生行不被快照列覆盖：真相源仍是行表
    assert store.get_session(sid_native).messages[0]["content"] == "原生行消息"
    first = store.get_session(sid)
    # 1.4 集合化清洗：悬空调用补合成，行数=清洗后列表长度
    assert [m.get("role") for m in first.messages] == ["user", "assistant", "tool", "user"]
    assert first.messages[2]["tool_call_id"] == "c1"

    n_before = store.migrate_legacy_blobs()
    assert n_before == 0, "已迁移会话不再重跑"
    again = store.get_session(sid)
    assert again.messages == first.messages, "二次迁移不改变行内容"

    conn = sqlite3.connect(str(workspace / "sessions.db"))
    row = conn.execute(
        "SELECT messages, messages_backup, migrated_at FROM sessions WHERE id=?", (sid,)
    ).fetchone()
    conn.close()
    backup = json.loads(row[1])
    assert row[0] == "[]" and backup[0]["content"] == "旧问题" and row[2] > 0


def test_reconcile_legacy_ts_hit_normalizes_without_reinject(workspace, store):
    """旧口径 promoted（_ts 命中）：消息在库——只补挂 _input_id，不重复注入。"""
    sid = store.create_session(str(workspace)).id
    q = store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "在库消息"})
    conn = sqlite3.connect(str(workspace / "sessions.db"))
    conn.execute(
        "UPDATE session_input SET status='promoted', promoted_message_id='1791299620716.9915' WHERE id=?",
        (q,),
    )
    conn.commit()
    conn.close()
    # 手工放置一条 _ts 与旧口径值一致的消息（存量命中形态）
    store.replace_messages_tx(sid, [
        {"role": "user", "content": "在库消息", "_ts": 1791299620716.9915},
    ])

    assert store.reconcile_promoted_inputs() == 0, "命中行不是丢失，不补写"
    msgs = store.get_session(sid).messages
    assert len(msgs) == 1 and msgs[0]["_input_id"] == q, "命中消息补挂业务键"
    assert store.get_promoted_input(q)["promoted_message_id"] == q
    # 幂等：复跑零动作
    assert store.reconcile_promoted_inputs() == 0
    assert len(store.get_session(sid).messages) == 1


def test_reconcile_true_loss_backfills_and_is_idempotent(workspace, store):
    """真丢失（66a192ac 形态）：按 payload 补写并挂 _input_id；复跑零补写。"""
    sid = store.create_session(str(workspace)).id
    store.replace_messages_tx(sid, [{"role": "user", "content": "旧问题"}])
    q = store.admit_session_input(sid, "backgroundNotification", "queue", {"role": "user", "content": "丢失通知"})
    conn = sqlite3.connect(str(workspace / "sessions.db"))
    conn.execute(
        "UPDATE session_input SET status='promoted', promoted_message_id='1791299620716.9915' WHERE id=?",
        (q,),
    )
    conn.commit()
    conn.close()

    assert store.reconcile_promoted_inputs() == 1
    msgs = store.get_session(sid).messages
    hit = [m for m in msgs if m.get("content") == "丢失通知"]
    assert len(hit) == 1 and hit[0]["_input_id"] == q and hit[0]["_reconciled"] is True
    row = store.get_promoted_input(q)
    assert row["promoted_message_id"] == q and row["status_reason"] == "reconciled"
    # 整批重建（模拟后续回合落库）后再对账：判据挂 _input_id，抗行重建
    store.replace_messages_tx(sid, store.load_messages(sid))
    assert store.reconcile_promoted_inputs() == 0
    assert len(store.get_session(sid).messages) == 2


def test_edit_truncated_promoted_rows_not_resurrected(workspace, store):
    """编辑重发截断的转正行翻 discarded 后，对账不补写不回拨（主动删除不复活）。"""
    sid = store.create_session(str(workspace)).id
    q = store.admit_session_input(sid, "sendText", "queue", {"role": "user", "content": "将被截掉"})
    store.promote_inputs_tx(sid, [{"role": "user", "content": "旧"}, *store.peek_queued_inputs(sid)])
    # 截断：目标列表不含该行，同事务翻 discarded
    store.promote_inputs_tx(
        sid,
        [m for m in store.load_messages(sid) if m.get("_input_id") != q],
        discarded_row_ids=[q],
    )
    assert store.reconcile_promoted_inputs() == 0, "discarded 不在对账扫描面"
    assert all(m.get("content") != "将被截掉" for m in store.get_session(sid).messages)

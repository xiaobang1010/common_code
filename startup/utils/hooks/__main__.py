"""Hook 系统包入口，支持 python -m startup.utils.hooks 运行测试。"""

from startup.utils.hooks import *  # noqa: F401,F403

if __name__ == "__main__":
    import asyncio
    import json
    import os
    import sys
    import tempfile

    from startup.utils.hooks import (
        HookConfig,
        HookDefinition,
        HookEntry,
        HookResult,
        capture_hooks_config_snapshot,
        run_post_tool_use_hooks,
        run_pre_tool_use_hooks,
    )

    print("=" * 60)
    print("Hook 系统测试")
    print("=" * 60)

    # 测试 1: HookEntry.matches
    print("\n--- 测试 1: HookEntry.matches ---")
    entry_all = HookEntry(matcher=None, hooks=[])
    entry_bash = HookEntry(matcher="Bash", hooks=[])
    entry_glob = HookEntry(matcher="Bash*", hooks=[])
    entry_read = HookEntry(matcher="Read", hooks=[])

    assert entry_all.matches("Bash"), "None matcher 应匹配所有"
    assert entry_all.matches("Read"), "None matcher 应匹配所有"
    assert entry_bash.matches("Bash"), "精确匹配"
    assert not entry_bash.matches("Read"), "不匹配"
    assert entry_glob.matches("Bash"), "通配符匹配 Bash"
    assert entry_glob.matches("BashTool"), "通配符匹配 BashTool"
    assert not entry_glob.matches("Read"), "通配符不匹配 Read"
    assert entry_read.matches("Read"), "精确匹配 Read"
    print("  [PASS] HookEntry.matches")

    # 测试 2: HookConfig 序列化/反序列化
    print("\n--- 测试 2: HookConfig 序列化/反序列化 ---")
    config = HookConfig(
        pre_tool_use=[
            HookEntry(
                matcher="Bash",
                hooks=[HookDefinition(type="command", command="echo test")],
            )
        ],
        post_tool_use=[],
        notification=[],
        stop=[],
    )
    d = config.to_dict()
    assert "PreToolUse" in d
    assert len(d["PreToolUse"]) == 1
    assert d["PreToolUse"][0]["matcher"] == "Bash"

    restored = HookConfig.from_dict(d)
    assert len(restored.pre_tool_use) == 1
    assert restored.pre_tool_use[0].matcher == "Bash"
    assert len(restored.pre_tool_use[0].hooks) == 1
    assert restored.pre_tool_use[0].hooks[0].command == "echo test"
    print("  [PASS] HookConfig 序列化/反序列化")

    # 测试 3: capture_hooks_config_snapshot
    print("\n--- 测试 3: capture_hooks_config_snapshot ---")
    snapshot = capture_hooks_config_snapshot()
    assert isinstance(snapshot, HookConfig)
    print(f"  pre_tool_use: {len(snapshot.pre_tool_use)} entries")
    print(f"  post_tool_use: {len(snapshot.post_tool_use)} entries")
    print("  [PASS] capture_hooks_config_snapshot")

    # 创建临时 Python 脚本用于跨平台 JSON 输出
    def _make_json_script(data: dict) -> str:
        """创建一个临时 Python 脚本，输出指定 JSON 并返回其路径。"""
        fd, path = tempfile.mkstemp(suffix=".py", prefix="hook_test_")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"import sys, json\nprint(json.dumps({data!r}))\n")
        return path

    # 测试 4: run_pre_tool_use_hooks (异步)
    print("\n--- 测试 4: run_pre_tool_use_hooks ---")

    async def test_pre_hook():
        # 空 hook 配置 — 不 deny
        empty_config = HookConfig()
        result = await run_pre_tool_use_hooks(empty_config, "Bash", {"command": "ls"})
        assert not result.decided, "空配置不应 deny"
        print("  空 config: decided=False")

        # 匹配但退出码为 0 的 hook — 不 deny
        config_ok = HookConfig(
            pre_tool_use=[
                HookEntry(
                    matcher="Bash",
                    hooks=[HookDefinition(type="command", command="echo ok")],
                )
            ]
        )
        result = await run_pre_tool_use_hooks(config_ok, "Bash", {"command": "ls"})
        assert not result.decided, "退出码 0 不应 deny"
        print("  exit 0: decided=False")

        # 匹配但退出码非 0 的 hook — deny
        config_deny = HookConfig(
            pre_tool_use=[
                HookEntry(
                    matcher="Bash",
                    hooks=[HookDefinition(type="command", command="exit 1")],
                )
            ]
        )
        result = await run_pre_tool_use_hooks(config_deny, "Bash", {"command": "rm -rf /"})
        assert result.decided, "退出码非 0 应 deny"
        print(f"  exit 1: decided=True, reason={result.reason!r}")

        # 不匹配的 hook — 不 deny
        result = await run_pre_tool_use_hooks(config_deny, "Read", {"path": "/tmp/test"})
        assert not result.decided, "不匹配的 hook 不应 deny"
        print("  不匹配: decided=False")

        # JSON 输出 with decision=block（跨平台：使用临时脚本）
        block_script = _make_json_script({"decision": "block", "reason": "dangerous"})
        try:
            config_block = HookConfig(
                pre_tool_use=[
                    HookEntry(
                        matcher="Bash",
                        hooks=[
                            HookDefinition(
                                type="command",
                                command=f'"{sys.executable}" "{block_script}"',
                            )
                        ],
                    )
                ]
            )
            result = await run_pre_tool_use_hooks(config_block, "Bash", {"command": "rm -rf /"})
            assert result.decided, f"decision=block 应 deny, got decided={result.decided}"
            assert "dangerous" in result.reason, f"reason 应包含 'dangerous', got: {result.reason}"
            print(f"  decision=block: decided=True, reason={result.reason!r}")
        finally:
            os.unlink(block_script)

        # JSON 输出 with updatedInput（跨平台：使用临时脚本）
        update_script = _make_json_script({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "updatedInput": {"command": "ls -la"},
            }
        })
        try:
            config_update = HookConfig(
                pre_tool_use=[
                    HookEntry(
                        matcher="Bash",
                        hooks=[
                            HookDefinition(
                                type="command",
                                command=f'"{sys.executable}" "{update_script}"',
                            )
                        ],
                    )
                ]
            )
            result = await run_pre_tool_use_hooks(config_update, "Bash", {"command": "ls"})
            assert not result.decided, "updatedInput 不应 deny"
            assert result.updated_input is not None, "应有 updated_input"
            assert result.updated_input.get("command") == "ls -la", f"updated_input 不正确: {result.updated_input}"
            print(f"  updatedInput: {result.updated_input}")
        finally:
            os.unlink(update_script)

    asyncio.run(test_pre_hook())
    print("  [PASS] run_pre_tool_use_hooks")

    # 测试 5: run_post_tool_use_hooks (异步)
    print("\n--- 测试 5: run_post_tool_use_hooks ---")

    async def test_post_hook():
        config = HookConfig(
            post_tool_use=[
                HookEntry(
                    matcher="Bash",
                    hooks=[HookDefinition(type="command", command="echo post")],
                )
            ]
        )
        # 不应抛出异常
        await run_post_tool_use_hooks(config, "Bash", {"command": "ls"}, "file1\nfile2")
        print("  PostToolUse hook 执行成功（无异常）")

    asyncio.run(test_post_hook())
    print("  [PASS] run_post_tool_use_hooks")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)

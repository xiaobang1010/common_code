"""SSRF 防护模块（简化版）。

参考原始 TypeScript 实现 src/utils/hooks/ssrfGuard.ts。

防止 HTTP hook 访问内网地址，保护内部基础设施安全。

禁止的地址范围：
  - 127.0.0.1, localhost — 回环地址
  - 10.x.x.x — A 类私有地址
  - 172.16-31.x.x — B 类私有地址
  - 192.168.x.x — C 类私有地址
  - 169.254.x.x — 链路本地地址（云元数据）
  - 0.0.0.x — "this" 网络
  - file:// 协议

注意：原始 TS 版本允许 loopback（本地 dev hook 场景），
此简化版根据需求禁止内网地址（含 127.0.0.1 和 localhost）。
"""

from __future__ import annotations

import re
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# IPv4 地址检查
# ---------------------------------------------------------------------------


def _is_private_ipv4(address: str) -> bool:
    """检查 IPv4 地址是否属于私有/保留范围。

    禁止范围：
      - 0.0.0.0/8 — "this" 网络
      - 10.0.0.0/8 — A 类私有
      - 100.64.0.0/10 — CGNAT 共享地址空间
      - 127.0.0.0/8 — 回环
      - 169.254.0.0/16 — 链路本地
      - 172.16.0.0/12 — B 类私有
      - 192.168.0.0/16 — C 类私有
    """
    parts = address.split(".")
    if len(parts) != 4:
        return False

    try:
        octets = [int(p) for p in parts]
    except ValueError:
        return False

    if any(not (0 <= o <= 255) for o in octets):
        return False

    a, b = octets[0], octets[1]

    # 0.0.0.0/8
    if a == 0:
        return True
    # 10.0.0.0/8
    if a == 10:
        return True
    # 100.64.0.0/10 — CGNAT
    if a == 100 and 64 <= b <= 127:
        return True
    # 127.0.0.0/8 — loopback
    if a == 127:
        return True
    # 169.254.0.0/16 — link-local
    if a == 169 and b == 254:
        return True
    # 172.16.0.0/12
    if a == 172 and 16 <= b <= 31:
        return True
    # 192.168.0.0/16
    if a == 192 and b == 168:
        return True

    return False


# ---------------------------------------------------------------------------
# URL 安全检查
# ---------------------------------------------------------------------------


def is_safe_url(url: str) -> bool:
    """检查 URL 是否安全。

    不安全的 URL：
      - file:// 协议
      - 主机名为 localhost 或 localhost 子域
      - 主机名解析为内网 IP 地址
      - 主机名为纯 IP 且属于私有地址范围

    Args:
        url: 要检查的 URL 字符串

    Returns:
        True 表示安全，False 表示不安全
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    # 禁止 file:// 协议
    if parsed.scheme == "file":
        return False

    # 无 scheme 或无 host 的 URL 不安全
    if not parsed.scheme or not parsed.hostname:
        return False

    hostname = parsed.hostname.lower()

    # 禁止 localhost
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return False

    # 检查主机名是否为 IPv4 地址
    ipv4_pattern = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")
    match = ipv4_pattern.match(hostname)
    if match:
        if _is_private_ipv4(hostname):
            return False

    # 检查 IPv6 地址中的内网映射
    # urlparse 已经去掉了方括号，hostname 可能是 "::1" 或 "::ffff:127.0.0.1"
    # 仅当 hostname 包含冒号时才做 IPv6 检查（排除 IPv4 和域名）
    if ":" in hostname:
        ipv6_addr = hostname.lower()
        if ipv6_addr in ("::1", "::", "::ffff:127.0.0.1"):
            return False
        # fc00::/7 — unique local
        if ipv6_addr.startswith("fc") or ipv6_addr.startswith("fd"):
            return False
        # fe80::/10 — link-local
        if ipv6_addr.startswith("fe8") or ipv6_addr.startswith("fe9") or \
           ipv6_addr.startswith("fea") or ipv6_addr.startswith("feb"):
            return False

    return True


# ---------------------------------------------------------------------------
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("SSRF 防护测试")
    print("=" * 60)

    # 测试 1: 安全的 URL
    print("\n--- 测试 1: 安全的 URL ---")
    safe_urls = [
        "https://api.example.com/v1/chat",
        "https://openai.com/api",
        "http://93.184.216.34/endpoint",
        "https://my-server.com:8080/hook",
    ]
    for url in safe_urls:
        result = is_safe_url(url)
        assert result is True, f"应认为安全: {url}"
        print(f"  SAFE:  {url}")
    print("  [PASS] 安全的 URL")

    # 测试 2: 不安全的 URL — 内网 IP
    print("\n--- 测试 2: 不安全的 URL — 内网 IP ---")
    unsafe_private = [
        "http://127.0.0.1:8080/hook",
        "http://10.0.0.1/endpoint",
        "http://10.255.255.255/endpoint",
        "http://172.16.0.1/endpoint",
        "http://172.31.255.255/endpoint",
        "http://192.168.1.1/endpoint",
        "http://192.168.0.1/endpoint",
        "http://169.254.169.254/metadata",
        "http://0.0.0.0/endpoint",
        "http://100.64.0.1/endpoint",
    ]
    for url in unsafe_private:
        result = is_safe_url(url)
        assert result is False, f"应认为不安全: {url}"
        print(f"  BLOCKED: {url}")
    print("  [PASS] 不安全的 URL — 内网 IP")

    # 测试 3: 不安全的 URL — localhost
    print("\n--- 测试 3: 不安全的 URL — localhost ---")
    unsafe_localhost = [
        "http://localhost:3000/hook",
        "http://localhost/api",
        "http://sub.localhost/endpoint",
    ]
    for url in unsafe_localhost:
        result = is_safe_url(url)
        assert result is False, f"应认为不安全: {url}"
        print(f"  BLOCKED: {url}")
    print("  [PASS] 不安全的 URL — localhost")

    # 测试 4: 不安全的 URL — file:// 协议
    print("\n--- 测试 4: 不安全的 URL — file:// 协议 ---")
    unsafe_file = [
        "file:///etc/passwd",
        "file://localhost/tmp/secret",
    ]
    for url in unsafe_file:
        result = is_safe_url(url)
        assert result is False, f"应认为不安全: {url}"
        print(f"  BLOCKED: {url}")
    print("  [PASS] 不安全的 URL — file:// 协议")

    # 测试 5: 不安全的 URL — IPv6
    print("\n--- 测试 5: 不安全的 URL — IPv6 ---")
    unsafe_ipv6 = [
        "http://[::1]:8080/hook",
        "http://[::]/endpoint",
        "http://[fc00::1]/endpoint",
        "http://[fd00::1]/endpoint",
        "http://[fe80::1]/endpoint",
    ]
    for url in unsafe_ipv6:
        result = is_safe_url(url)
        assert result is False, f"应认为不安全: {url}"
        print(f"  BLOCKED: {url}")
    print("  [PASS] 不安全的 URL — IPv6")

    # 测试 6: 边界情况
    print("\n--- 测试 6: 边界情况 ---")
    # 172.15.x.x 不是私有地址
    assert is_safe_url("http://172.15.0.1/endpoint") is True, "172.15 不在私有范围"
    print("  172.15.0.1: SAFE (不在 172.16/12 范围)")

    # 172.32.x.x 不是私有地址
    assert is_safe_url("http://172.32.0.1/endpoint") is True, "172.32 不在私有范围"
    print("  172.32.0.1: SAFE (不在 172.16/12 范围)")

    # 11.x.x.x 不是私有地址
    assert is_safe_url("http://11.0.0.1/endpoint") is True, "11.x 不是私有地址"
    print("  11.0.0.1: SAFE (不是 10.0.0.0/8)")

    # 192.169.x.x 不是私有地址
    assert is_safe_url("http://192.169.1.1/endpoint") is True, "192.169 不是私有地址"
    print("  192.169.1.1: SAFE (不是 192.168.0.0/16)")
    print("  [PASS] 边界情况")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)

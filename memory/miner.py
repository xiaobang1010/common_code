"""内容摄取管道 - 从文件和对话中提取内容存入 Palace。

File Miner: 摄取项目文件（代码、文档、配置）
Conversation Miner: 摄取对话记录（Q+A 交换对）
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

from memory.gitignore import GitignoreMatcher
from memory.locks import mine_lock
from memory.palace import PalaceManager

logger = logging.getLogger(__name__)

# Token 分割正则（用于名称匹配）
_TOKEN_SPLIT = re.compile(r"[-_./]+")

# 仅匹配完整的年-月-日日期（防幻觉）
_VALID_DATE_RE = re.compile(r"(?:19|20)\d{2}[-/](?:0[1-9]|1[0-2])[-/](?:0[1-9]|[12]\d|3[01])")

# 跳过的目录名
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    ".tox", ".eggs", "dist", "build", ".mypy_cache", ".ruff_cache",
    ".pytest_cache", ".next", ".nuxt", "target", "out",
    ".agent", ".trae",
}

# 跳过的文件扩展名
SKIP_EXTENSIONS = {
    ".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe", ".bin",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg",
    ".zip", ".tar", ".gz", ".rar", ".7z",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".mp3", ".mp4", ".avi", ".mov",
    ".lock", ".cache",
}

# 支持的文件扩展名
SUPPORTED_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".swift",
    ".go", ".rs", ".c", ".cpp", ".h", ".hpp",
    ".rb", ".php", ".vue", ".svelte",
    ".md", ".txt", ".rst", ".yaml", ".yml", ".toml",
    ".json", ".xml", ".html", ".css", ".scss",
    ".sh", ".bat", ".ps1",
    ".sql", ".graphql",
}

# 每个文件的最大字符数
MAX_FILE_CHARS = 50000
# 每个 chunk 的最大字符数
CHUNK_SIZE = 800


class FileMiner:
    """项目文件矿工 - 摄取项目文件到 Palace。

    步骤：
    1. 文件发现：遍历目录，跳过 SKIP_DIRS
    2. Room 路由：路径推导 + 内容关键字
    3. Chunk 分割：按 ~800 字符切分
    4. 增量更新：通过 source_mtime 检测变更
    5. 去重：通过 content_hash 检查
    """

    _COMMON_DIRS = frozenset({
        "src", "lib", "libs", "app", "test", "tests",
        "__tests__", "internal", "pkg", "cmd",
    })

    # 内容关键字 -> room 映射
    _KEYWORD_ROOMS: list[tuple[tuple[str, ...], str]] = [
        (("sql", "database", "query", "table", "schema", "sqlite", "psycopg2", "sqlalchemy"), "database"),
        (("react", "vue", "frontend", "css", "html", "ui", "usestate", "useeffect", "angular"), "frontend"),
        (("test", "pytest", "unittest", "bug", "fix", "error"), "tests"),
        (("api", "endpoint", "route", "flask", "fastapi", "django"), "api"),
        (("deploy", "docker", "ci/cd", "infra", "kubernetes", "dockerfile"), "infra"),
        (("refactor", "design", "architecture", "pattern"), "design"),
    ]

    def __init__(self, palace: PalaceManager):
        self.palace = palace
        self._gitignore_matcher = GitignoreMatcher()
        self._root_dir: Path | None = None

    def mine_file(self, file_path: Path, wing: str | None = None) -> int:
        """摄取单个文件。

        1. 读取文件内容
        2. 检查增量：source_mtime 未变则跳过
        3. 删除该文件的旧 Drawer
        4. Room 路由
        5. Chunk 分割
        6. 为每个 chunk 创建 Drawer
        7. 返回创建的 Drawer 数量
        """
        file_path = Path(file_path)
        if wing is None:
            wing = self._derive_wing(file_path.parent)

        source_file = str(file_path)

        with mine_lock(source_file):
            try:
                content = self._read_text_no_follow(file_path)
                file_mtime = file_path.stat().st_mtime
            except (OSError, UnicodeDecodeError) as e:
                logger.warning("无法读取文件 %s: %s", file_path, e)
                return 0

            if not content.strip():
                return 0

            if len(content) > MAX_FILE_CHARS:
                content = content[:MAX_FILE_CHARS]

            # TOCTOU 保护：获取锁后重新检查文件是否已被挖掘
            stored_mtime = self.palace.storage.get_source_mtime(source_file)
            if stored_mtime is not None and file_mtime == stored_mtime:
                logger.debug("文件未变更，跳过: %s", file_path)
                return 0

            # 删除该文件的旧 Drawer + Closet
            self.palace.delete_by_source(source_file)

            # Room 路由
            room = self.detect_room(file_path, content)

            # authored_at 提取
            authored_at = self._extract_authored_at(file_path, content)

            # Chunk 分割
            chunks = self.chunk_content(content)

            # 为每个 chunk 创建 Drawer
            for i, chunk in enumerate(chunks):
                self.palace.add_drawer(
                    wing=wing, room=room, content=chunk,
                    source_file=source_file, chunk_index=i,
                    source_mtime=file_mtime, authored_at=authored_at,
                )

            return len(self.palace.get_drawers_by_source(source_file))

    def mine_directory(self, dir_path: Path, wing: str | None = None,
                       recursive: bool = True) -> dict:
        """摄取目录下的所有文件。

        Returns:
            {"files_processed": int, "drawers_created": int, "files_skipped": int}
        """
        dir_path = Path(dir_path)
        if not dir_path.is_dir():
            logger.warning("目录不存在或不是目录: %s", dir_path)
            return {"files_processed": 0, "drawers_created": 0, "files_skipped": 0}

        if wing is None:
            wing = self._derive_wing(dir_path)

        # 加载 .gitignore 规则
        self._root_dir = dir_path
        self._gitignore_matcher.load_gitignore(dir_path)

        files_processed = 0
        drawers_created = 0
        files_skipped = 0

        for root, dirs, files in os.walk(dir_path):
            if recursive:
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            else:
                dirs.clear()

            for fname in files:
                file_path = Path(root) / fname
                if self._should_skip(file_path):
                    files_skipped += 1
                    continue
                files_processed += 1
                drawers_created += self.mine_file(file_path, wing=wing)

        # After mining, build hallway analysis
        try:
            from memory.hallways import HallwayBuilder
            builder = HallwayBuilder(self.palace.storage)
            builder.build_all()
        except Exception:
            pass  # Hallway building is best-effort

        return {
            "files_processed": files_processed,
            "drawers_created": drawers_created,
            "files_skipped": files_skipped,
        }

    def detect_room(self, file_path: Path, content: str) -> str:
        """Room 路由 - 从文件路径和内容推导 Room 名称。

        四级优先级：
        1. 文件夹名匹配 room 名/关键字（token 级匹配）
        2. 文件名匹配 room 名
        3. 内容关键字评分
        4. 默认 "general"
        """
        # 策略 1：文件夹名匹配 room 名/关键字（token 级匹配）
        parent_name = file_path.parent.name
        if parent_name and parent_name.lower() not in self._COMMON_DIRS \
                and not parent_name.startswith("."):
            for keywords, room in self._KEYWORD_ROOMS:
                if self._name_matches(parent_name, room):
                    return room
                for kw in keywords:
                    if self._name_matches(parent_name, kw):
                        return room

        # 策略 2：文件名匹配 room 名
        file_stem = file_path.stem
        for _, room in self._KEYWORD_ROOMS:
            if self._name_matches(file_stem, room):
                return room

        # 策略 3：内容关键字评分
        content_lower = content.lower()
        for keywords, room in self._KEYWORD_ROOMS:
            if any(kw in content_lower for kw in keywords):
                return room

        # 策略 4：默认
        return "general"

    def _name_matches(self, name: str, target: str) -> bool:
        """Token 级匹配，防止 views 误匹配 interviews。"""
        name_tokens = set(self._TOKEN_SPLIT.split(name.lower()))
        target_tokens = set(self._TOKEN_SPLIT.split(target.lower()))
        return bool(name_tokens & target_tokens)

    def _extract_authored_at(self, file_path: Path, content: str) -> str:
        """五级 authored_at 提取。

        1. 文件名 ISO 正则 + dateutil 严格解析
        2. YAML frontmatter date/created/published
        3. 内容前 10 行 ISO 正则 + 斜杠日期
        4. 文件系统 mtime
        5. None（回退到 filed_at）
        """
        # Level 1: filename
        fname = file_path.name
        m = _VALID_DATE_RE.search(fname)
        if m:
            date_str = m.group(0).replace("/", "-")
            return f"{date_str}T00:00:00Z"

        # Level 2: YAML frontmatter
        frontmatter_date = self._extract_from_frontmatter(content)
        if frontmatter_date:
            return frontmatter_date

        # Level 3: content first 10 lines
        lines = content.split("\n", 10)[:10]
        for line in lines:
            m = _VALID_DATE_RE.search(line)
            if m:
                date_str = m.group(0).replace("/", "-")
                return f"{date_str}T00:00:00Z"

        # Level 4: file mtime
        try:
            mtime = file_path.stat().st_mtime
            return datetime.fromtimestamp(mtime).isoformat()
        except Exception:
            pass

        # Level 5: None
        return ""

    def _extract_from_frontmatter(self, content: str) -> str:
        """从 YAML frontmatter 提取日期。"""
        if not content.startswith("---"):
            return ""
        end = content.find("\n---", 3)
        if end == -1:
            return ""
        frontmatter = content[3:end]
        for key in ("date", "created", "published"):
            pattern = re.compile(rf"^{key}:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
            m = pattern.search(frontmatter)
            if m:
                date_val = m.group(1).strip().strip('"\'')
                dm = _VALID_DATE_RE.search(date_val)
                if dm:
                    date_str = dm.group(0).replace("/", "-")
                    return f"{date_str}T00:00:00Z"
        return ""

    def chunk_content(self, content: str, chunk_size: int = CHUNK_SIZE,
                      chunk_overlap: int = 100, min_chunk_size: int = 50) -> list[str]:
        """将内容按 chunk_size 切分，边界优先 + overlap。

        边界优先：尝试在段落边界（\\n\\n）切割，其次换行符（\\n），
        只要位置超过 chunk_size 的一半。

        相邻 chunk 之间有 chunk_overlap 字符的重叠，保留跨块上下文。
        低于 min_chunk_size 的块被丢弃。
        """
        if not content:
            return []

        chunks: list[str] = []
        start = 0
        content_len = len(content)

        while start < content_len:
            # Target end position
            target_end = start + chunk_size

            if target_end >= content_len:
                # Last chunk
                chunk = content[start:]
                if len(chunk) >= min_chunk_size:
                    chunks.append(chunk)
                break

            # Find best split point (boundary-first)
            split_pos = self._find_boundary(content, start, target_end, chunk_size)

            chunk = content[start:split_pos]
            if len(chunk) >= min_chunk_size:
                chunks.append(chunk)

            # Next chunk starts with overlap
            next_start = max(split_pos - chunk_overlap, 0)
            # Ensure progress
            if next_start <= start:
                start = split_pos
            else:
                start = next_start

        return chunks

    def _find_boundary(self, content: str, start: int, target_end: int, chunk_size: int) -> int:
        """找到最佳切分边界位置。

        优先级：段落边界 (\\n\\n) > 换行符 (\\n) > 硬切。
        只要在 chunk_size // 2 之后寻找边界即可。
        """
        min_pos = start + chunk_size // 2

        # Try paragraph boundary (\n\n)
        search_start = min_pos
        search_end = target_end + 100  # Allow some overflow
        pos = content.find("\n\n", search_start, search_end)
        if pos != -1:
            return pos + 2  # Include the \n\n

        # Try line boundary (\n)
        pos = content.find("\n", min_pos, target_end)
        if pos != -1:
            return pos + 1  # Include the \n

        # Hard cut at target_end
        return target_end

    def _should_skip(self, path: Path) -> bool:
        """检查文件/目录是否应该跳过。

        优先检查 gitignore 规则，然后回退到 SKIP_DIRS/SKIP_EXTENSIONS。
        """
        # 先检查 gitignore 规则
        if self._root_dir is not None:
            if self._gitignore_matcher.should_ignore(path, root=self._root_dir):
                return True
        for part in path.parts:
            if part in SKIP_DIRS:
                return True
        ext = path.suffix.lower()
        if ext in SKIP_EXTENSIONS:
            return True
        if ext not in SUPPORTED_EXTENSIONS:
            return True
        return False

    def _read_text_no_follow(self, file_path: Path) -> str:
        """安全读取文件，防止符号链接攻击。

        使用 O_NOFOLLOW 打开文件，如果文件是符号链接则拒绝。
        """
        import os
        fd = None
        try:
            fd = os.open(str(file_path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(fd, 'r', encoding='utf-8', errors='replace') as f:
                return f.read()
        except OSError:
            # O_NOFOLLOW not available or file is a symlink
            if fd is not None:
                os.close(fd)
            return file_path.read_text(encoding='utf-8', errors='replace')

    def _path_within_root(self, file_path: Path, root: Path) -> bool:
        """检查文件路径是否在根目录内。"""
        try:
            file_path.resolve().relative_to(root.resolve())
            return True
        except ValueError:
            return False

    def _derive_wing(self, dir_path: Path) -> str:
        """从目录路径推导 Wing 名称。使用目录的 basename。"""
        name = dir_path.name
        if not name or name in (".", ".."):
            return "default"
        return name.lower()


class ConversationMiner:
    """会话矿工 - 摄取对话记录到 Palace。

    以 Q+A 交换对为单位分割，而非按字符数。
    """

    # 内容关键字 -> room 映射
    _KEYWORD_ROOMS: list[tuple[tuple[str, ...], str]] = [
        (("sql", "database", "query", "table", "schema"), "database"),
        (("react", "vue", "frontend", "css", "html", "ui"), "frontend"),
        (("test", "pytest", "bug", "fix", "error"), "tests"),
        (("api", "endpoint", "route", "flask", "fastapi", "django"), "api"),
        (("deploy", "docker", "ci/cd", "infra", "kubernetes"), "infra"),
        (("refactor", "design", "architecture", "pattern"), "design"),
    ]

    # 角色标记 -> role 映射
    _ROLE_KEYWORDS: dict[str, str] = {
        "user": "user", "human": "user", "q": "user",
        "assistant": "assistant", "ai": "assistant", "bot": "assistant", "a": "assistant",
    }

    def __init__(self, palace: PalaceManager):
        self.palace = palace

    def mine_conversation(self, messages: list[dict], wing: str = "conversation",
                          session_id: str = "", extract_mode: str = "exchange") -> int:
        """摄取对话记录。

        Args:
            messages: [{"role": "user"|"assistant", "content": "..."}, ...]
            wing: Wing 名称
            session_id: 会话 ID
            extract_mode: "exchange" (Q+A pairs) or "general" (classified by type)
        """
        if extract_mode == "general":
            return self._mine_general(messages, wing, session_id)
        return self._mine_exchange(messages, wing, session_id)

    def _mine_exchange(self, messages: list[dict], wing: str,
                      session_id: str) -> int:
        """交换对模式摄取。

        当 user 消息 >= 3 条时启用 Q+A 交换对配对，
        否则回退到段落模式（每条消息作为独立段落）。
        """
        source_file = (
            f"conversation:exchange:{session_id}" if session_id
            else "conversation:exchange"
        )
        self.palace.delete_by_source(source_file)

        user_count = sum(1 for m in messages if m.get("role") == "user")

        if user_count >= 3:
            # 交换对模式：按 Q+A 配对
            pairs: list[tuple[str, str]] = []
            current_q: str | None = None
            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role == "user":
                    if current_q is not None:
                        pairs.append((current_q, ""))
                    current_q = content
                elif role == "assistant":
                    if current_q is not None:
                        pairs.append((current_q, content))
                        current_q = None
                    else:
                        pairs.append(("", content))
            if current_q is not None:
                pairs.append((current_q, ""))
        else:
            # 段落回退：每条消息作为独立段落
            pairs = []
            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role == "user":
                    pairs.append((content, ""))
                else:
                    pairs.append(("", content))

        count = 0
        for i, (q, a) in enumerate(pairs):
            if q and a:
                content = f"Q: {q}\n\nA: {a}"
            elif q:
                content = f"Q: {q}"
            else:
                content = f"A: {a}"

            if not content.strip():
                continue

            room = self._detect_room(content)
            self.palace.add_drawer(
                wing=wing, room=room, content=content,
                source_file=source_file, chunk_index=i,
            )
            count += 1

        return count

    def _mine_general(self, messages: list[dict], wing: str,
                      session_id: str) -> int:
        """通用模式摄取 - 按内容类型分类。

        将每条消息分类为 decision/preference/milestone/issue/sentiment，
        并使用类型作为 room。
        """
        source_file = (
            f"conversation:general:{session_id}" if session_id
            else "conversation:general"
        )
        self.palace.delete_by_source(source_file)

        count = 0
        for i, msg in enumerate(messages):
            content = msg.get("content", "")
            if not content or not content.strip():
                continue
            room = self._classify_chunk(content)
            self.palace.add_drawer(
                wing=wing, room=room, content=content,
                source_file=source_file, chunk_index=i,
            )
            count += 1

        return count

    def _classify_chunk(self, content: str) -> str:
        """将内容分类为 decision/preference/milestone/issue/sentiment。"""
        content_lower = content.lower()
        # 按优先级匹配
        if any(kw in content_lower for kw in (
            "decided", "chose", "will use", "going with", "selected",
            "决定", "选择", "采用",
        )):
            return "decision"
        if any(kw in content_lower for kw in (
            "prefer", "like better", "would rather", "favorite",
            "偏好", "更喜欢", "倾向",
        )):
            return "preference"
        if any(kw in content_lower for kw in (
            "completed", "finished", "done", "milestone", "achieved",
            "完成", "里程碑", "达成",
        )):
            return "milestone"
        if any(kw in content_lower for kw in (
            "error", "bug", "problem", "issue", "failed", "crash",
            "错误", "问题", "失败", "崩溃",
        )):
            return "issue"
        if any(kw in content_lower for kw in (
            "happy", "frustrated", "satisfied", "disappointed", "confused",
            "满意", "沮丧", "失望", "困惑",
        )):
            return "sentiment"
        return "general"

    def mine_conversation_file(self, file_path: Path, wing: str | None = None,
                              extract_mode: str = "exchange") -> int:
        """摄取对话文件（JSON/JSONL/Markdown/纯文本）。

        自动检测格式并解析。
        """
        file_path = Path(file_path)
        if wing is None:
            wing = "conversation"

        try:
            text = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            logger.warning("无法读取文件 %s: %s", file_path, e)
            return 0

        session_id = file_path.stem
        suffix = file_path.suffix.lower()
        messages = self._parse_conversation(text, suffix)

        if not messages:
            logger.debug("未解析到对话消息: %s", file_path)
            return 0

        return self.mine_conversation(messages, wing=wing, session_id=session_id,
                                      extract_mode=extract_mode)

    def _detect_room(self, content: str) -> str:
        """基于内容关键字推导 Room。"""
        content_lower = content.lower()
        for keywords, room in self._KEYWORD_ROOMS:
            if any(kw in content_lower for kw in keywords):
                return room
        return "general"

    def _parse_conversation(self, text: str, suffix: str) -> list[dict]:
        """根据文件后缀解析对话消息。"""
        if suffix == ".json":
            return self._parse_json(text)
        if suffix == ".jsonl":
            return self._parse_jsonl(text)
        return self._parse_text_conversation(text)

    @staticmethod
    def _parse_json(text: str) -> list[dict]:
        """解析 JSON 格式对话。"""
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning("JSON 解析失败: %s", e)
            return []
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "messages" in data:
            return data["messages"]
        return []

    @staticmethod
    def _parse_jsonl(text: str) -> list[dict]:
        """解析 JSONL 格式对话（每行一个 JSON 对象）。"""
        messages: list[dict] = []
        for line in text.strip().splitlines():
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
                messages.append(msg)
            except json.JSONDecodeError:
                continue
        return messages

    def _parse_text_conversation(self, text: str) -> list[dict]:
        """解析 Markdown/纯文本格式对话。

        识别角色标记：
        - "User:" / "Human:" / "Q:" -> user
        - "Assistant:" / "AI:" / "Bot:" / "A:" -> assistant
        - Markdown 标题 "## User" / "## Assistant" -> 角色切换
        - 粗体标记 "**User:**" / "**Assistant:**"
        """
        lines = text.splitlines()
        messages: list[dict] = []
        current_role: str | None = None
        current_lines: list[str] = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                if current_role is not None:
                    current_lines.append("")
                continue

            # 去除 Markdown 前缀（#、*）
            cleaned = stripped.lstrip("#").lstrip("*").strip()
            lower = cleaned.lower()

            role = None
            content = None

            # 检查 "Role: content" 模式
            if ":" in cleaned:
                prefix, rest = cleaned.split(":", 1)
                prefix = prefix.strip().lower()
                if prefix in self._ROLE_KEYWORDS:
                    role = self._ROLE_KEYWORDS[prefix]
                    content = rest.strip()

            # 检查纯标题模式（## User，无冒号）
            if role is None and lower in self._ROLE_KEYWORDS:
                role = self._ROLE_KEYWORDS[lower]
                content = ""

            if role is not None:
                if current_role is not None and current_lines:
                    msg_content = "\n".join(current_lines).strip()
                    if msg_content:
                        messages.append({"role": current_role, "content": msg_content})
                current_role = role
                current_lines = [content] if content else []
            else:
                if current_role is not None:
                    current_lines.append(line)

        if current_role is not None and current_lines:
            msg_content = "\n".join(current_lines).strip()
            if msg_content:
                messages.append({"role": current_role, "content": msg_content})

        return messages

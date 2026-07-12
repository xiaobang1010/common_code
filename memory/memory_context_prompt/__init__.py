"""四层记忆栈 - 分层加载策略。

L0 Identity   (~100 tokens)   - 始终加载，身份信息
L1 Essential  (~500-800 tokens) - 始终加载，Top-N 关键故事
L2 On-Demand  (~200-500 tokens) - 按需触发，Wing/Room 过滤检索
L3 Deep       (无限)            - 按需触发，语义搜索
"""

from memory.memory_context_prompt.stack import MemoryContextPromptStack
from memory.memory_context_prompt.layer0 import Layer0
from memory.memory_context_prompt.layer1 import Layer1
from memory.memory_context_prompt.layer2 import Layer2
from memory.memory_context_prompt.layer3 import Layer3

__all__ = [
    "MemoryContextPromptStack",
    "Layer0",
    "Layer1",
    "Layer2",
    "Layer3",
]

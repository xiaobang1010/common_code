"""WebFetch 工具输入模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class WebFetchInput(BaseModel):
    """WebFetch 工具输入。

    Attributes:
        url: 要抓取的 http/https 地址
        prompt: 抓取后要对内容执行的任务
    """

    url: str = Field(
        description="要抓取的 URL，只支持 http/https。"
        "不确定确切地址时先用 WebSearch 类信息源定位，不要瞎猜 URL",
    )
    prompt: str = Field(
        description="对抓取到的页面内容要执行的任务（例如提取某接口用法、总结要点）。"
        "结果按此任务组织回答，不是把整页原文回传",
    )

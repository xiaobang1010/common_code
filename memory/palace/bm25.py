"""自实现 Okapi-BM25 算法 - 独立于 FTS5 内置 bm25() 函数。

Lucene/BM25+ 平滑 IDF 公式，k1=1.5/b=0.75 可调参数。
在候选集内计算 IDF（适合小候选集重排）。
"""

from __future__ import annotations

import math
import re
from collections import Counter

# Unicode-aware tokenizer: \w{2,} matches 2+ char words including CJK
_TOKEN_RE = re.compile(r"\w{2,}", re.UNICODE)


def tokenize(text: str | None) -> list[str]:
    """将文本分词为 token 列表。

    使用 \\w{2,} Unicode 正则，支持中文。
    None 输入返回空列表（ChromaDB 可能返回 None 文档）。

    Args:
        text: 输入文本，可为 None

    Returns:
        小写 token 列表
    """
    if not text:
        return []
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def bm25_scores(
    query: str,
    documents: list[str],
    k1: float = 1.5,
    b: float = 0.75,
) -> list[float]:
    """计算查询对每个文档的 Okapi-BM25 分数。

    IDF 使用 Lucene/BM25+ 平滑公式: log((N - df + 0.5) / (df + 0.5) + 1)
    保证非负（标准 BM25 的 IDF 可能为负，Lucene 平滑修复了这个问题）。

    IDF 在候选集（documents 参数）上计算，而非全局语料库。
    这对小候选集重排是正确的做法。

    Args:
        query: 搜索查询文本
        documents: 候选文档列表
        k1: 词频饱和参数（默认 1.5）
        b: 长度归一化参数（默认 0.75）

    Returns:
        每个文档的 BM25 分数列表（非负浮点数）
    """
    if not documents:
        return []

    # Tokenize query and documents
    query_terms = tokenize(query)
    if not query_terms:
        return [0.0] * len(documents)

    doc_tokens = [tokenize(doc) for doc in documents]

    N = len(documents)

    # Document frequencies: for each query term, how many docs contain it
    df: dict[str, int] = Counter()
    for tokens in doc_tokens:
        unique_terms = set(tokens)
        for term in unique_terms:
            if term in query_terms:
                df[term] += 1

    # Document lengths
    doc_lengths = [len(tokens) for tokens in doc_tokens]
    avgdl = sum(doc_lengths) / N if N > 0 else 0

    # Calculate BM25 score for each document
    scores = []
    for i, tokens in enumerate(doc_tokens):
        score = 0.0
        doc_len = doc_lengths[i]
        tf = Counter(tokens)  # term frequency in this doc

        for term in query_terms:
            if term not in tf:
                continue

            term_df = df.get(term, 0)
            # Lucene/BM25+ smoothed IDF (always non-negative)
            idf = math.log((N - term_df + 0.5) / (term_df + 0.5) + 1)

            term_freq = tf[term]
            # BM25 term frequency saturation
            tf_component = (term_freq * (k1 + 1)) / (
                term_freq + k1 * (1 - b + b * (doc_len / avgdl if avgdl > 0 else 0))
            )

            score += idf * tf_component

        scores.append(score)

    return scores

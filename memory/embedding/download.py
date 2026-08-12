"""Jasper embedding 模型下载脚本。

在需要语义检索时下载 Jasper 模型到 memory/embedding/jasper-model/ 目录。
下载是显式触发（CLI 命令），启动时只探测不下载。

下载方式就是 git clone，简单直接。
默认用 hf-mirror.com 镜像加速。

用法：
  python -m memory.embedding.download           # 直接运行
  download-embedding-model                        # 通过 pyproject.toml 入口点运行
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# HuggingFace 仓库地址
_REPO_URL = "https://huggingface.co/infgrad/Jasper-Token-Compression-600M"
# 镜像站地址
_MIRROR_URL = "https://hf-mirror.com/infgrad/Jasper-Token-Compression-600M"

# 模型缓存目录（memory/embedding/）
_CACHE_DIR = Path(__file__).parent

# 本地模型目录名
_LOCAL_MODEL_DIR = _CACHE_DIR / "jasper-model"


def is_model_downloaded() -> bool:
    """检查 Jasper 模型是否已下载到本地。

    通过检查本地模型目录下是否有 model.safetensors 文件来判断。
    """
    return (_LOCAL_MODEL_DIR / "model.safetensors").exists()


def download_model() -> bool:
    """用 git clone 下载 Jasper 模型。

    默认用 hf-mirror.com 镜像加速，设置 HF_ENDPOINT 环境变量可覆盖。

    Returns:
        True 下载成功或已存在，False 下载失败
    """
    if is_model_downloaded():
        logger.info("Jasper 模型已存在，跳过下载")
        return True

    # 选镜像站
    use_mirror = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com")
    clone_url = use_mirror.rstrip("/") + "/infgrad/Jasper-Token-Compression-600M"

    logger.info("开始下载 Jasper 模型: %s", clone_url)
    logger.info("本地目录: %s", _LOCAL_MODEL_DIR)
    print(f"正在 git clone Jasper embedding 模型到 {_LOCAL_MODEL_DIR} ...")
    print(f"镜像站: {use_mirror}")
    print("模型约 1.2GB，首次下载需要一些时间，请耐心等待。")

    try:
        # 确保父目录存在
        _LOCAL_MODEL_DIR.parent.mkdir(parents=True, exist_ok=True)

        # git clone 到本地目录
        result = subprocess.run(
            ["git", "clone", clone_url, str(_LOCAL_MODEL_DIR)],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            # 镜像失败就试官方源
            if "hf-mirror.com" in clone_url:
                logger.warning("镜像站下载失败，尝试官方源: %s", _REPO_URL)
                print("镜像站失败，尝试官方源...")
                # 清理可能残留的目录
                if _LOCAL_MODEL_DIR.exists():
                    import shutil
                    shutil.rmtree(_LOCAL_MODEL_DIR, ignore_errors=True)

                result = subprocess.run(
                    ["git", "clone", _REPO_URL, str(_LOCAL_MODEL_DIR)],
                    capture_output=True,
                    text=True,
                )

            if result.returncode != 0:
                logger.error("git clone 失败: %s", result.stderr)
                print(f"下载失败: {result.stderr}", file=sys.stderr)
                return False

        # 验证模型文件存在
        if not is_model_downloaded():
            logger.error("下载完成但未找到 model.safetensors 文件")
            return False

        # 验证模型可加载
        logger.info("模型下载完成，验证加载...")
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(str(_LOCAL_MODEL_DIR), device="cpu", trust_remote_code=True)
        test_vec = model.encode("test")
        if test_vec is not None and len(test_vec) > 0:
            print(f"模型下载成功！维度: {len(test_vec)}")
            logger.info("Jasper 模型下载成功，维度: %d", len(test_vec))
            return True
        else:
            logger.error("模型加载验证失败")
            return False

    except Exception as e:
        logger.error("Jasper 模型下载失败: %s", e)
        print(f"模型下载失败: {e}", file=sys.stderr)
        return False


def main():
    """CLI 入口点：下载 Jasper embedding 模型。"""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    success = download_model()
    if success:
        print("Jasper embedding 模型已就绪。")
        sys.exit(0)
    else:
        print("模型下载失败，请检查网络连接和 git 是否安装。", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

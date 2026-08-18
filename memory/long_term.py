"""
长期记忆：基于 Mem0 的跨会话记忆管理

功能：
- 使用 Mem0 API 存储会话总结
- 向量相似度搜索历史会话
- 支持多用户和多 Agent

Mem0 优势：
- 自动向量化和相似度搜索
- 云端或本地 Qdrant 存储
- 内置去重和摘要
"""
from typing import Dict, List, Any, Optional
from datetime import datetime
from loguru import logger
import os
import ssl
import sys
from contextlib import contextmanager


@contextmanager
def _tls12_fallback_for_mem0():
    """
    临时将 SSL 默认上下文的最高版本限制为 TLS 1.2。

    某些 Windows 环境下 OpenSSL 3.x 与 Mem0 服务端在 TLS 1.3 握手/重协商时
    会报 [SSL: UNEXPECTED_EOF_WHILE_READING]，降级到 TLS 1.2 可规避该问题。
    仅在创建 MemoryClient（会一次性建好底层 httpx 连接池）期间生效，
    不影响进程内其他网络请求。
    """
    original_create_default_context = ssl.create_default_context

    def _patched(*args, **kwargs):
        ctx = original_create_default_context(*args, **kwargs)
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        return ctx

    ssl.create_default_context = _patched
    try:
        yield
    finally:
        ssl.create_default_context = original_create_default_context

# Harness Engineering: 熵管理
try:
    from .entropy_manager import MemoryEntropyManager
    ENTROPY_ENABLED = True
except ImportError:
    logger.warning("EntropyManager not found, running without entropy management")
    ENTROPY_ENABLED = False

# 加载上层目录的config.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
try:
    from config import MEM0_CONFIG
except ImportError:
    MEM0_CONFIG = None

try:
    from mem0 import MemoryClient
    MEM0_AVAILABLE = True
except ImportError:
    MEM0_AVAILABLE = False
    logger.warning("mem0ai not installed. Long-term memory disabled. Install with: pip install mem0ai")


class LongTermMemory:
    """
    长期记忆管理器（基于 Mem0）

    功能：
    1. 存储会话总结到 Mem0
    2. 搜索相似历史会话（向量相似度）

    使用场景：
    - 会话结束后保存总结
    - 会话开始前检索相似案例
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        初始化长期记忆管理器

        Args:
            config: Mem0 配置字典，包含 api_key 等配置
        """
        if not MEM0_AVAILABLE:
            self.enabled = False
            logger.warning("Mem0 not available. Long-term memory disabled.")
            return

        self.enabled = True

        # Harness Engineering: 熵管理器
        self.entropy_manager = MemoryEntropyManager() if ENTROPY_ENABLED else None
        if ENTROPY_ENABLED:
            logger.debug("✅ Entropy management enabled for long-term memory")

        try:
            # 获取 API key（优先级：config参数 > 全局config.py > 环境变量）
            mem0_api_key = None

            # 1. 从参数config获取
            if config and "api_key" in config:
                mem0_api_key = config["api_key"]
            # 2. 从全局config.py获取
            elif MEM0_CONFIG and "api_key" in MEM0_CONFIG:
                mem0_api_key = MEM0_CONFIG["api_key"]
            # 3. 从环境变量获取
            else:
                mem0_api_key = os.getenv("MEM0_API_KEY")

            if not mem0_api_key:
                raise ValueError("MEM0_API_KEY not found. Set it in config.py (project root's parent directory) or the MEM0_API_KEY environment variable")

            # 初始化 Mem0 云服务客户端（临时降级 TLS 以规避 Windows 上的握手问题）
            with _tls12_fallback_for_mem0():
                self.mem0 = MemoryClient(api_key=mem0_api_key)
            logger.info("LongTermMemory initialized with Mem0 cloud service")

        except Exception as e:
            logger.warning(f"Failed to initialize Mem0: {e}")
            logger.warning("Long-term memory disabled. System will work without Mem0.")
            logger.info("To enable Mem0: set MEM0_CONFIG['api_key'] in config.py (project root's parent directory)")
            self.enabled = False

    def add_session_summary(
        self,
        session_id: str,
        question: str,
        answer: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        添加会话总结到 Mem0

        Args:
            session_id: 会话ID
            question: 用户问题
            answer: 系统回答
            metadata: 额外元数据（复杂度、时间等）

        Returns:
            记忆ID，失败返回 None
        """
        if not self.enabled:
            return None

        try:
            # 构建记忆文本（包含问题和答案摘要）
            memory_text = f"问题：{question}\n回答：{answer[:500]}..."

            # 添加到 Mem0
            result = self.mem0.add(
                messages=[{"role": "user", "content": memory_text}],
                user_id="medix_user",  # 固定用户ID（可扩展为多用户）
                metadata={
                    "type": "session_summary",
                    "session_id": session_id,
                    "timestamp": datetime.now().isoformat(),
                    **(metadata or {})
                }
            )

            # 提取记忆ID
            if isinstance(result, dict):
                memory_id = result.get("id", result.get("results", [{}])[0].get("id"))
            else:
                memory_id = str(result)

            logger.info(f"Added session summary to Mem0: {memory_id}")
            return memory_id

        except Exception as e:
            logger.error(f"Failed to add session summary to Mem0: {e}")
            return None

    def search_similar_sessions(
        self,
        query: str,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        搜索相似的历史会话（向量相似度搜索，自动去重）

        Args:
            query: 查询文本（通常是用户问题）
            limit: 返回结果数量

        Returns:
            相似会话列表，每个包含 memory_id、content、score、metadata
        """
        if not self.enabled:
            return []

        try:
            results = self.mem0.search(
                query=query,
                user_id="medix_user",
                limit=limit * 2  # 多获取一些以便去重后有足够结果
            )

            # 添加调试日志
            logger.debug(f"Mem0 search called with limit={limit * 2}")

            # 格式化结果
            formatted_results = []

            # 处理不同格式的 Mem0 返回值
            if isinstance(results, dict):
                results_list = results.get("results", [])
                logger.debug(f"Mem0 returned dict with {len(results_list)} results (requested limit={limit * 2})")
            elif isinstance(results, list):
                results_list = results
                logger.debug(f"Mem0 returned list with {len(results_list)} results (requested limit={limit * 2})")
            else:
                results_list = []

            for result in results_list:
                formatted_results.append({
                    "memory_id": result.get("id", "unknown"),
                    "content": result.get("memory", result.get("text", "")),
                    "score": result.get("score", 0.0),
                    "metadata": result.get("metadata", {}),
                    "timestamp": result.get("metadata", {}).get("timestamp")
                })

            # Harness Engineering: 熵管理 - 去重相似会话
            if self.entropy_manager and len(formatted_results) > 0:
                formatted_results = self.entropy_manager.deduplicate_sessions(formatted_results)
                

            # 限制返回数量
            formatted_results = formatted_results[:limit]

            logger.info(f"Found {len(formatted_results)} similar sessions for query: {query[:50]}...")
            return formatted_results

        except Exception as e:
            logger.error(f"Failed to search similar sessions: {e}")
            return []




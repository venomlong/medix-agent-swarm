"""
短期记忆：会话级对话历史管理

功能：
- 管理会话级的对话历史（messages）
- 默认 Redis 存储；连不上时降级到内存
- 自动过期机制（Redis 1小时）
- 熵管理：自动去重和压缩（Harness Engineering）
"""
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any
import json
from loguru import logger

# 与 long_term.py 一致：加载仓库父目录 config.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def get_redis_config() -> Dict[str, Any]:
    """
    解析 Redis 连接信息：环境变量 > config.REDIS_CONFIG > 默认值。
    不硬编码密码；REDIS_PASSWORD 仅从环境变量读取（若 config 未提供）。
    """
    cfg: Dict[str, Any] = {
        "host": "127.0.0.1",
        "port": 6379,
        "db": 0,
        "password": None,
    }
    try:
        from config import REDIS_CONFIG
        if isinstance(REDIS_CONFIG, dict):
            for key in ("host", "port", "db", "password"):
                if key in REDIS_CONFIG and REDIS_CONFIG[key] not in (None, ""):
                    cfg[key] = REDIS_CONFIG[key]
    except ImportError:
        pass

    if os.environ.get("REDIS_HOST"):
        cfg["host"] = os.environ["REDIS_HOST"]
    if os.environ.get("REDIS_PORT"):
        cfg["port"] = int(os.environ["REDIS_PORT"])
    if os.environ.get("REDIS_DB"):
        cfg["db"] = int(os.environ["REDIS_DB"])
    if "REDIS_PASSWORD" in os.environ:
        cfg["password"] = os.environ.get("REDIS_PASSWORD") or None

    return cfg

# Harness Engineering: 熵管理
try:
    from .entropy_manager import MemoryEntropyManager
    ENTROPY_ENABLED = True
except ImportError:
    logger.warning("EntropyManager not found, running without entropy management")
    ENTROPY_ENABLED = False


@dataclass
class ConversationHistory:
    """对话历史数据类"""
    session_id: str
    messages: List[Dict[str, str]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    last_updated: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_message(self, role: str, content: str):
        """添加消息"""
        self.messages.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        })
        self.last_updated = datetime.now()

    def get_recent_messages(self, limit: int = 50) -> List[Dict[str, str]]:
        """获取最近的消息"""
        return self.messages[-limit:]

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于 Redis 存储）"""
        return {
            "session_id": self.session_id,
            "messages": self.messages,
            "created_at": self.created_at.isoformat(),
            "last_updated": self.last_updated.isoformat(),
            "metadata": self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConversationHistory":
        """从字典创建（从 Redis 加载）"""
        return cls(
            session_id=data["session_id"],
            messages=data["messages"],
            created_at=datetime.fromisoformat(data["created_at"]),
            last_updated=datetime.fromisoformat(data["last_updated"]),
            metadata=data.get("metadata", {})
        )


class ShortTermMemory:
    """
    短期记忆管理器（单例模式）

    支持两种存储后端：
    1. redis：Redis 存储（默认；TTL 1 小时，key 为 session:{session_id}）
    2. memory：纯内存存储（Redis 连不上时自动降级）

    使用场景：
    - 管理单次会话的对话历史
    - Agent Loop 中的消息记录
    - 会话结束后转换为长期记忆
    """

    _instance = None  # 单例实例
    _lock = None  # 用于线程安全（如果需要）

    def __new__(cls, *args, **kwargs):
        """单例模式：确保只有一个 ShortTermMemory 实例"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        storage_type: str = "redis",
        redis_config: Optional[Dict[str, Any]] = None
    ):
        """
        初始化短期记忆管理器

        Args:
            storage_type: 存储类型，"redis"（默认）或 "memory"
            redis_config: Redis 配置；缺省时从环境变量 / REDIS_CONFIG 读取
        """
        # 单例只初始化一次。无参/默认调用视为获取单例（search-history 等）；
        # 仅在后续显式传入不同后端或不同 redis_config 时告警。
        if getattr(self, "_initialized", False):
            requested_differs = (
                (storage_type != "redis" and storage_type != self.storage_type)
                or (
                    redis_config is not None
                    and redis_config != getattr(self, "_init_redis_config", None)
                )
            )
            if requested_differs:
                logger.warning(
                    "ShortTermMemory is a singleton already initialized with "
                    f"storage_type={self.storage_type!r}; ignoring subsequent "
                    f"arguments (storage_type={storage_type!r})"
                )
            return

        self.storage_type = storage_type
        self._init_redis_config = redis_config
        self.sessions: Dict[str, ConversationHistory] = {}
        self.redis_client = None
        self._initialized = True

        # Harness Engineering: 熵管理器
        self.entropy_manager = MemoryEntropyManager() if ENTROPY_ENABLED else None
        if ENTROPY_ENABLED:
            logger.debug("✅ Entropy management enabled for short-term memory")

        if storage_type == "redis":
            config = dict(get_redis_config())
            if redis_config:
                config.update({k: v for k, v in redis_config.items() if v is not None})
            self._init_redis_config = config
            host = config.get("host", "127.0.0.1")
            port = int(config.get("port", 6379))
            db = int(config.get("db", 0))
            try:
                import redis
                self.redis_client = redis.Redis(
                    host=host,
                    port=port,
                    db=db,
                    password=config.get("password"),
                    decode_responses=True,
                    socket_connect_timeout=2,
                    socket_timeout=2,
                )
                # 测试连接
                self.redis_client.ping()
                logger.info(
                    f"ShortTermMemory initialized with Redis at {host}:{port} db={db} "
                    f"(key=session:{{id}}, TTL=3600s)"
                )
            except Exception as e:
                logger.error(
                    f"Failed to connect to Redis at {host}:{port} db={db}: {e}. "
                    "Falling back to in-memory short-term storage "
                    "(sessions will not survive process restart)."
                )
                self.storage_type = "memory"
                self.redis_client = None
        else:
            logger.info("ShortTermMemory initialized with in-memory storage")

    def create_session(
        self,
        session_id: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> ConversationHistory:
        """
        创建新会话

        Args:
            session_id: 会话ID
            metadata: 会话元数据

        Returns:
            ConversationHistory 对象
        """
        history = ConversationHistory(
            session_id=session_id,
            metadata=metadata or {}
        )

        if self.storage_type == "memory":
            self.sessions[session_id] = history
        elif self.storage_type == "redis" and self.redis_client:
            self._save_to_redis(history)

        logger.debug(f"Created session: {session_id}")
        return history

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str
    ):
        """
        添加消息到会话历史

        Args:
            session_id: 会话ID
            role: 消息角色（user/assistant/tool）
            content: 消息内容
        """
        history = self.get_session(session_id)

        if history is None:
            history = self.create_session(session_id)

        history.add_message(role, content)

        # 保存到存储
        if self.storage_type == "redis" and self.redis_client:
            self._save_to_redis(history)

        logger.debug(f"Added {role} message to session {session_id}")

    def get_session(self, session_id: str) -> Optional[ConversationHistory]:
        """
        获取会话历史

        Args:
            session_id: 会话ID

        Returns:
            ConversationHistory 对象，如果不存在返回 None
        """
        if self.storage_type == "memory":
            return self.sessions.get(session_id)
        elif self.storage_type == "redis" and self.redis_client:
            return self._load_from_redis(session_id)
        return None

    def get_recent_messages(
        self,
        session_id: str,
        limit: int = 50
    ) -> List[Dict[str, str]]:
        """
        获取最近的消息（自动熵管理）

        Args:
            session_id: 会话ID
            limit: 最大消息数

        Returns:
            消息列表（去重和压缩后）
        """
        history = self.get_session(session_id)
        if history:
            messages = history.get_recent_messages(limit)

            # Harness Engineering: 统一熵管理
            if self.entropy_manager and len(messages) > 0:
                # 估算熵（用于监控系统健康）
                if len(messages) >= 10:
                    entropy_info = self.entropy_manager.estimate_entropy(messages)
                    if entropy_info["entropy_level"] == "high":
                        logger.warning(
                            f"📊 会话 {session_id} 熵等级: {entropy_info['entropy_level']} "
                            f"(消息数: {entropy_info['total_messages']}, "
                            f"重复率: {entropy_info['duplicate_rate']:.1%})"
                        )

                # 统一使用 auto_clean: 自动去重+压缩
                messages = self.entropy_manager.auto_clean(
                    messages,
                    enable_deduplication=True,
                    enable_compression=True,
                    max_messages=limit
                )

            return messages
        return []

    def get_history(
        self,
        session_id: str,
        limit: int = 10
    ) -> List[Dict[str, str]]:
        """
        获取历史对话（OpenAI 格式，用于 Agent Loop）

        Args:
            session_id: 会话ID
            limit: 最大轮数（一轮 = user + assistant）

        Returns:
            消息列表（OpenAI 格式: [{"role": "user", "content": "..."}, ...]）
        """
        # get_recent_messages 已处理熵管理，这里只做格式转换
        messages = self.get_recent_messages(session_id, limit * 2)  # 每轮2条消息

        # 转换为 OpenAI 格式（只保留 user 和 assistant 消息）
        openai_messages = [
            {"role": msg["role"], "content": msg["content"]}
            for msg in messages
            if msg["role"] in ["user", "assistant"]
        ]

        return openai_messages

    def clear_session(self, session_id: str):
        """
        清空会话

        Args:
            session_id: 会话ID
        """
        if self.storage_type == "memory":
            self.sessions.pop(session_id, None)
        elif self.storage_type == "redis" and self.redis_client:
            key = f"session:{session_id}"
            self.redis_client.delete(key)

        logger.debug(f"Cleared session: {session_id}")

    def _save_to_redis(self, history: ConversationHistory):
        """保存到 Redis（内部方法）"""
        if not self.redis_client:
            return

        try:
            key = f"session:{history.session_id}"
            value = json.dumps(history.to_dict())
            # 设置过期时间：1小时（3600秒）
            self.redis_client.setex(key, 3600, value)
        except Exception as e:
            logger.error(f"Failed to save to Redis: {e}")

    def _load_from_redis(self, session_id: str) -> Optional[ConversationHistory]:
        """从 Redis 加载（内部方法）"""
        if not self.redis_client:
            return None

        try:
            key = f"session:{session_id}"
            value = self.redis_client.get(key)

            if value:
                data = json.loads(value)
                return ConversationHistory.from_dict(data)
        except Exception as e:
            logger.error(f"Failed to load from Redis: {e}")

        return None

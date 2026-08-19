"""
医学知识库（Milvus）

功能：
1. 文档向量化和存储
2. 混合检索（向量 + BM25，RRF 融合；可回退纯向量）
3. 知识库管理

"""
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from loguru import logger

from pymilvus import MilvusClient
from sentence_transformers import SentenceTransformer

from knowledge.hybrid_retriever import (
    BM25_TOP_N_DEFAULT,
    RRF_K_DEFAULT,
    VECTOR_TOP_N_DEFAULT,
    BM25Index,
    annotate_collection,
    fuse_or_fallback,
)


class MedicalKnowledgeBase:
    """医学知识库"""

    _instance = None

    def __new__(cls, *args, **kwargs):
        """实现单例模式"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        db_path: str = "./knowledge/data/milvus_lite.db",
        collection_name: str = "medical_knowledge",
        embedding_model: str = "BAAI/bge-small-zh-v1.5",
        retrieval_mode: str = "hybrid",
        rrf_k: int = RRF_K_DEFAULT,
        bm25_top_n: int = BM25_TOP_N_DEFAULT,
        vector_top_n: int = VECTOR_TOP_N_DEFAULT,
    ):
        """
        初始化医学知识库

        Args:
            db_path: Milvus Lite 数据库文件路径
            collection_name: Collection 名称
            embedding_model: Embedding 模型名称或本地路径
            retrieval_mode: 默认检索模式 hybrid / vector / bm25（search 可覆盖）
            rrf_k: RRF 常数 k，默认 60
            bm25_top_n: 融合前 BM25 召回数
            vector_top_n: 融合前向量召回数
        """
        # 防止重复初始化
        if hasattr(self, '_initialized'):
            return

        self.db_path = db_path
        self.collection_name = collection_name
        self.retrieval_mode = retrieval_mode or "hybrid"
        self.rrf_k = int(rrf_k) if rrf_k else RRF_K_DEFAULT
        self.bm25_top_n = int(bm25_top_n) if bm25_top_n else BM25_TOP_N_DEFAULT
        self.vector_top_n = int(vector_top_n) if vector_top_n else VECTOR_TOP_N_DEFAULT
        self._bm25_index = BM25Index()

        # 确保数据目录存在
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # 初始化 Embedding 模型（支持本地路径）
        # 优先检查本地缓存路径
        local_model_path = Path.home() / ".cache" / "huggingface" / "hub" / "models--BAAI--bge-small-zh-v1.5" / "snapshots"

        if local_model_path.exists():
            # 找到最新的 snapshot
            snapshots = sorted(local_model_path.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
            if snapshots:
                model_path = str(snapshots[0])
                logger.info(f"Loading embedding model from local cache: {model_path}")
                self.embedding_model = SentenceTransformer(model_path, device='cpu')
            else:
                logger.info(f"Loading embedding model: {embedding_model}")
                self.embedding_model = SentenceTransformer(embedding_model, device='cpu')
        else:
            logger.info(f"Loading embedding model: {embedding_model}")
            self.embedding_model = SentenceTransformer(embedding_model, device='cpu')

        self.embedding_dim = self.embedding_model.get_sentence_embedding_dimension()
        logger.info(f"Embedding model loaded (dimension={self.embedding_dim})")

        # 初始化 Milvus Lite
        logger.info(f"Connecting to Milvus Lite: {db_path}")
        self.milvus_client = MilvusClient(db_path)

        # 创建 collection（如果不存在）
        if not self.milvus_client.has_collection(collection_name):
            logger.info(f"Creating collection: {collection_name}")
            self.milvus_client.create_collection(
                collection_name=collection_name,
                dimension=self.embedding_dim,
                metric_type="COSINE",  # 余弦相似度
                auto_id=True  # 自动生成整数ID
            )
        else:
            logger.info(f"Collection already exists: {collection_name}")

        # 显式加载 collection 到内存，避免 Milvus Lite 空闲释放后首次检索失败
        try:
            self.milvus_client.load_collection(collection_name)
        except Exception as e:
            logger.warning(f"Failed to load collection at init: {e}")

        self._rebuild_bm25_from_store()
        self._initialized = True

    def _chunk_text(self, text: str, chunk_size: int = 1024, overlap: int = 100) -> List[str]:
        """
        分块文本

        Args:
            text: 原始文本
            chunk_size: 块大小（字符数）
            overlap: 重叠字符数

        Returns:
            文本块列表
        """
        if len(text) <= chunk_size:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunk = text[start:end]
            chunks.append(chunk)
            start = end - overlap  # 重叠

        return chunks

    def add_documents(self, documents: List[Dict[str, Any]], chunk_size: int = 1024) -> int:
        """
        添加文档到知识库（支持分块）

        Args:
            documents: 文档列表，每个文档包含 id, content, metadata
            chunk_size: 分块大小（字符数），默认 1024

        Returns:
            成功添加的文档块数量
        """
        if not documents:
            logger.warning("No documents to add")
            return 0

        logger.info(f"Adding {len(documents)} documents to knowledge base (chunk_size={chunk_size})...")

        # 分块并向量化
        all_chunks = []
        for doc in documents:
            chunks = self._chunk_text(doc["content"], chunk_size=chunk_size)
            for i, chunk in enumerate(chunks):
                metadata = doc.get("metadata", {}).copy()
                metadata["doc_id"] = doc["id"]
                metadata["chunk_id"] = i
                metadata["total_chunks"] = len(chunks)

                all_chunks.append({
                    "content": chunk,
                    "metadata": metadata
                })

        logger.info(f"Split into {len(all_chunks)} chunks")

        # 向量化
        contents = [chunk["content"] for chunk in all_chunks]
        vectors = self.embedding_model.encode(contents, show_progress_bar=True)

        # 准备数据
        data = []
        for i, chunk in enumerate(all_chunks):
            data.append({
                "vector": vectors[i].tolist(),
                "content": chunk["content"],
                "metadata": json.dumps(chunk["metadata"], ensure_ascii=False)
            })

        # 插入
        insert_result = self.milvus_client.insert(self.collection_name, data)
        logger.info(f"Successfully added {len(data)} chunks")
        self._rebuild_bm25_from_store()
        if self._bm25_index.is_empty() and all_chunks:
            ids = []
            if isinstance(insert_result, dict):
                ids = list(insert_result.get("ids") or [])
            fallback_docs = []
            for i, chunk in enumerate(all_chunks):
                fallback_docs.append({
                    "id": ids[i] if i < len(ids) else f"local-{i}",
                    "content": chunk["content"],
                    "metadata": chunk["metadata"],
                })
            self._bm25_index.rebuild(fallback_docs)
            logger.warning(
                f"BM25 rebuilt from insert payload ({len(self._bm25_index)} chunks); "
                "store query returned no rows"
            )

        return len(data)

    def _milvus_retry(self, op_name: str, fn):
        """Milvus Lite 空闲 release 后自动 load 再试一次。"""
        try:
            return fn()
        except Exception as e:
            if "released" in str(e).lower() or "not loaded" in str(e).lower():
                try:
                    logger.warning(f"Collection released, reloading: {e}")
                    self.milvus_client.load_collection(self.collection_name)
                    return fn()
                except Exception as retry_e:
                    logger.error(f"{op_name} failed after reload retry: {retry_e}")
                    return None
            logger.error(f"{op_name} failed: {e}")
            return None

    def _parse_chunk_row(self, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            entity = row.get("entity") if isinstance(row.get("entity"), dict) else row
            content = entity.get("content") or row.get("content") or ""
            meta_raw = entity.get("metadata") if "metadata" in entity else row.get("metadata")
            if isinstance(meta_raw, str):
                metadata = json.loads(meta_raw)
            else:
                metadata = meta_raw or {}
            if not isinstance(metadata, dict):
                metadata = {}
            return {
                "id": row.get("id", entity.get("id")),
                "content": content,
                "metadata": metadata,
            }
        except Exception as e:
            logger.warning(f"Failed to parse chunk row: {e}")
            return None

    def _query_all_chunks(self) -> List[Dict[str, Any]]:
        rows = None
        for filter_expr in ("id >= 0", "id > -1"):
            def _do_query(expr=filter_expr):
                return self.milvus_client.query(
                    collection_name=self.collection_name,
                    filter=expr,
                    output_fields=["content", "metadata"],
                    limit=16384,
                )

            rows = self._milvus_retry("Query chunks for BM25", _do_query)
            if rows:
                break
        if not rows:
            return []
        docs: List[Dict[str, Any]] = []
        for row in rows:
            parsed = self._parse_chunk_row(row if isinstance(row, dict) else {})
            if parsed and parsed.get("id") is not None:
                docs.append(parsed)
        return docs

    def _rebuild_bm25_from_store(self) -> None:
        """按当前 Milvus collection 全量重建 BM25，与向量库 chunk id 对齐。"""
        try:
            docs = self._query_all_chunks()
            self._bm25_index.rebuild(docs)
            logger.info(f"BM25 index rebuilt ({len(self._bm25_index)} chunks)")
        except Exception as e:
            logger.warning(f"BM25 rebuild failed, vector-only fallback will be used: {e}")
            self._bm25_index.rebuild([])

    def _vector_search(
        self,
        query: str,
        top_k: int,
        filter_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if not query or top_k <= 0:
            return []

        query_vector = self.embedding_model.encode([query])[0]
        filter_expr = None
        if filter_type:
            filter_expr = f'metadata like "%\\"type\\": \\"{filter_type}\\"%"'

        def _do_search():
            return self.milvus_client.search(
                collection_name=self.collection_name,
                data=[query_vector.tolist()],
                limit=top_k,
                filter=filter_expr,
                output_fields=["content", "metadata"],
            )

        results = self._milvus_retry("Vector search", _do_search)
        if not results:
            return []

        documents = []
        for hits in results:
            for hit in hits:
                try:
                    documents.append({
                        "id": hit["id"],
                        "content": hit["entity"]["content"],
                        "metadata": json.loads(hit["entity"]["metadata"]),
                        # MilvusClient 在 metric_type="COSINE" 下返回的 distance 本身就是
                        # 余弦相似度（越大越相关），直接作为 score，不能做 1-x 反转
                        "score": hit["distance"],
                        "vector_score": hit["distance"],
                        "collection": self.collection_name,
                    })
                except Exception as e:
                    logger.warning(f"Failed to parse result: {e}")
                    continue
        return documents

    def _bm25_search(
        self,
        query: str,
        top_n: int,
        filter_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if self._bm25_index.is_empty():
            return []
        try:
            hits = self._bm25_index.search(query, top_n=top_n, filter_type=filter_type)
        except Exception as e:
            logger.warning(f"BM25 search failed: {e}")
            return []
        return annotate_collection(hits, self.collection_name)

    def search(
        self,
        query: str,
        top_k: int = 5,
        filter_type: Optional[str] = None,
        mode: Optional[str] = None,
        rrf_k: Optional[int] = None,
        bm25_top_n: Optional[int] = None,
        vector_top_n: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        检索相关文档。默认 hybrid：向量与 BM25 各取 top-N，再按 RRF 融合。

        Args:
            query: 查询文本
            top_k: 最终返回条数
            filter_type: 可选的类型过滤（如 "lifestyle", "disease_classification"）
            mode: hybrid / vector / bm25，默认实例的 retrieval_mode（hybrid）
            rrf_k: RRF 常数，默认 60
            bm25_top_n: 融合前 BM25 召回数
            vector_top_n: 融合前向量召回数

        Returns:
            文档列表，字段 id / content / metadata / score / collection。
            hybrid 时 score 为归一化 RRF（0–1），原始融合分在 rrf_score；
            另附 vector_score / bm25_score（若该路召回过）。
        """
        resolved_mode = mode or self.retrieval_mode or "hybrid"
        if resolved_mode not in ("hybrid", "vector", "bm25"):
            resolved_mode = "hybrid"
        fusion_k = int(rrf_k) if rrf_k else self.rrf_k
        v_n = max(int(vector_top_n) if vector_top_n else self.vector_top_n, top_k)
        b_n = max(int(bm25_top_n) if bm25_top_n else self.bm25_top_n, top_k)

        logger.debug(
            f"Searching for: {query} (top_k={top_k}, filter_type={filter_type}, mode={resolved_mode})"
        )

        if resolved_mode == "vector":
            documents = self._vector_search(query, top_k=top_k, filter_type=filter_type)
            logger.debug(f"Found {len(documents)} documents")
            return documents

        bm25_hits = self._bm25_search(query, top_n=b_n, filter_type=filter_type)
        if resolved_mode == "bm25" and bm25_hits:
            logger.debug(f"Found {len(bm25_hits[:top_k])} documents")
            return bm25_hits[:top_k]
        if not bm25_hits:
            documents = self._vector_search(query, top_k=top_k, filter_type=filter_type)
            logger.debug(f"Found {len(documents)} documents (vector fallback)")
            return documents

        vector_hits = self._vector_search(query, top_k=v_n, filter_type=filter_type)
        documents = fuse_or_fallback(
            vector_hits,
            bm25_hits,
            mode="hybrid",
            rrf_k=fusion_k,
            top_k=top_k,
        )
        logger.debug(f"Found {len(documents)} documents")
        return documents

    def get(self, chunk_id: Any) -> Optional[Dict[str, Any]]:
        """按 Milvus 主键读取单个 chunk 全文。"""
        try:
            pk: Any = int(str(chunk_id).strip())
        except (TypeError, ValueError):
            pk = chunk_id

        def _fetch() -> List[Dict[str, Any]]:
            return self.milvus_client.get(
                collection_name=self.collection_name,
                ids=[pk],
                output_fields=["content", "metadata"],
            )

        try:
            rows = _fetch()
        except Exception as e:
            if "released" in str(e).lower() or "not loaded" in str(e).lower():
                try:
                    logger.warning(f"Collection released, reloading: {e}")
                    self.milvus_client.load_collection(self.collection_name)
                    rows = _fetch()
                except Exception as retry_e:
                    logger.error(f"Get chunk failed after reload retry: {retry_e}")
                    return None
            else:
                logger.error(f"Get chunk failed: {e}")
                return None

        if not rows:
            return None
        row = rows[0] if isinstance(rows, list) else rows
        entity = row.get("entity") if isinstance(row.get("entity"), dict) else row
        content = entity.get("content") or row.get("content") or ""
        meta_raw = entity.get("metadata") if "metadata" in entity else row.get("metadata")
        try:
            metadata = json.loads(meta_raw) if isinstance(meta_raw, str) else (meta_raw or {})
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        return {
            "id": row.get("id", pk),
            "content": content,
            "metadata": metadata if isinstance(metadata, dict) else {},
            "score": 0.0,
            "collection": self.collection_name,
        }

    def delete_collection(self):
        """删除 collection（用于测试）"""
        if self.milvus_client.has_collection(self.collection_name):
            self.milvus_client.drop_collection(self.collection_name)
            logger.info(f"Deleted collection: {self.collection_name}")
        self._bm25_index.rebuild([])

    def bm25_size(self) -> int:
        """当前 BM25 索引中的 chunk 数。"""
        return len(self._bm25_index)

    def count_documents(self) -> int:
        """统计文档数量"""
        try:
            stats = self.milvus_client.describe_collection(self.collection_name)
            # Note: Milvus Lite may not return accurate count, this is a best-effort
            return stats.get("num_entities", 0)
        except Exception as e:
            logger.warning(f"Failed to count documents: {e}")
            return 0

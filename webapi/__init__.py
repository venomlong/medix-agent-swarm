"""
MediX 薄 HTTP 服务层：FastAPI + SSE，不改 CLI / 核心协作算法。
"""
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

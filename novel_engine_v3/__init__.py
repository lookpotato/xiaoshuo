"""作者驱动的长篇小说生成内核（实验版）。"""

from .engine import AuthorEngine, V3ValidationError

__all__ = ["AuthorEngine", "V3ValidationError"]

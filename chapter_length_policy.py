"""Per-book and per-chapter writing length guidance."""

import json
from pathlib import Path


def chapter_length_limited(book: dict, chapter_number: int) -> bool:
    exempt = book.get("word_count_exempt_chapters", [])
    return not (
        book.get("word_count_limit_enabled", True) is False
        or isinstance(exempt, list) and chapter_number in exempt
    )


def chapter_length_instruction(book: dict, chapter_number: int) -> str:
    """Return the authoritative word-count instruction for one chapter."""
    if not chapter_length_limited(book, chapter_number):
        return (
            f"第 {chapter_number} 章已关闭字数限制：忽略本书 novel_config.md、作者档案、"
            "共享写作规范及历史提示中的单章目标字数、最低字数、最高字数、硬上限和拆章字数要求。"
            "根据情节完整度自然决定篇幅，不为凑字扩写，也不为压字删减必要内容。"
            "此设置只豁免字数规则，不豁免读者验收、连续性、人物线和其他质量门禁。"
        )
    book_length = book.get("chapter_length")
    if isinstance(book_length, dict):
        minimum = book_length.get("target_min_hanzi")
        target = book_length.get("target_hanzi")
        maximum = book_length.get("target_max_hanzi")
        review = book_length.get("overlong_review_hanzi")
        if all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in (minimum, target, maximum, review)
        ) and 0 < minimum <= target <= maximum <= review:
            return (
                f"第 {chapter_number} 章执行本书专属篇幅：正文常规 {minimum}—{maximum} 汉字，"
                f"以约 {target} 汉字为中心；超过 {review} 汉字先复核是否能在真实阶段胜负处拆章。"
                "字数按正文实际汉字（Unicode U+4E00—U+9FFF）计，不含标题和 Metadata；"
                "不得用非空白字符数冒充汉字数。完成后按这一口径复核，常规章尽量靠近中心目标；"
                "偏短先检查现场动作、人物关系、因果桥梁和结果余波是否写足。"
                "本书设置优先于共享规范的通用字数建议；不为凑字扩写，也不为压字删减必要内容。"
            )
    return (
        f"第 {chapter_number} 章继续执行本书 novel_config.md 及写作规范中的字数要求；"
        "不得把单章字数限制扩展为整本书的篇幅限制。"
    )


def project_chapter_length_limited(
    project: Path, chapter_number: int, root: Path
) -> bool:
    """Resolve a project folder's setting; unknown folders keep the safe default."""
    config_path = root / "manager_config.json"
    if not config_path.is_file():
        return True
    try:
        data = json.loads(config_path.read_text(encoding="utf-8-sig"))
        target = project.resolve()
        for book in data.get("books", []):
            candidate = (root / str(book.get("path", ""))).resolve()
            if candidate == target:
                return chapter_length_limited(book, chapter_number)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return True
    return True

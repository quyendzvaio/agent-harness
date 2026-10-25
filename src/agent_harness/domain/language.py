"""Language signals shared by intent parsing and answer checks."""

from __future__ import annotations

import re

VIETNAMESE_CHARS = re.compile(r"[ăâđêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ]", re.I)
_LETTER = re.compile(r"[^\W\d_]")
_CODE = re.compile(r"```.*?(```|$)|`[^`\n]*`", re.S)


def lacks_vietnamese(text: str, *, min_letters: int = 12) -> bool:
    """Prose (code removed) long enough to judge, with not a single Vietnamese diacritic."""
    prose = _CODE.sub(" ", text)
    return len(_LETTER.findall(prose)) >= min_letters and not VIETNAMESE_CHARS.search(prose)

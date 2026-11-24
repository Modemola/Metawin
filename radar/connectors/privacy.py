"""Remove contact details from free text before it is written to disk.

The tool never stores player identities, so email addresses and phone numbers
inside message bodies are replaced with `[email]` / `[phone]`.
"""

from __future__ import annotations

import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")
# Candidate phone: optional "+", digits with space/hyphen/parenthesis separators.
# Dots are not separators so crypto amounts like 0.00012345 survive.
_PHONE = re.compile(r"(?<![\w.])\+?\(?\d[\d ()-]{7,}\d(?!\w|\.\d)")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _phone_or_keep(m: re.Match[str]) -> str:
    s = m.group(0)
    digits = sum(ch.isdigit() for ch in s)
    if not 9 <= digits <= 15 or _ISO_DATE.search(s):
        return s
    return "[phone]"


def redact(text: str | None) -> str:
    """Replace email addresses and phone numbers in `text`."""
    if not text:
        return ""
    return _PHONE.sub(_phone_or_keep, _EMAIL.sub("[email]", text))

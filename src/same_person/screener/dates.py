"""Partial dates in the formats the development set uses."""

from __future__ import annotations

import re
from dataclasses import dataclass

MONTHS = {
    name: index
    for index, names in enumerate(
        [
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ],
        start=1,
    )
    for name in names
}
MONTH_RE = (
    r"(January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?"
)

# Order matters: longer forms first.
DATE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("iso", r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)"),
    ("dmy_slash", r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4})(?!\d)"),
    ("mdy_words", MONTH_RE + r" (\d{1,2}),? (\d{4})(?!\d)"),
    ("dmy_words", r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)? " + MONTH_RE + r",? (\d{4})(?!\d)"),
    ("ym_iso", r"(?<!\d)(\d{4})-(\d{1,2})(?![\d-])"),
    ("my_words", MONTH_RE + r",? (\d{4})(?!\d)"),
    ("year", r"(?<![\d/-])(1[89]\d\d|20\d\d)(?![\d/-])"),
)
COMPILED = tuple((kind, re.compile(pattern, re.IGNORECASE)) for kind, pattern in DATE_PATTERNS)


@dataclass(frozen=True, slots=True, order=True)
class PartialDate:
    """A date where month and day may be unknown."""

    year: int
    month: int | None = None
    day: int | None = None

    def __str__(self) -> str:
        if self.month is None:
            return f"{self.year:04d}"
        if self.day is None:
            return f"{self.year:04d}-{self.month:02d}"
        return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"

    @property
    def full(self) -> bool:
        return self.month is not None and self.day is not None

    def key(self) -> tuple[int, int, int]:
        return (self.year, self.month or 0, self.day or 0)


def _valid(year: int, month: int | None, day: int | None) -> PartialDate | None:
    if not 1800 <= year <= 2100:
        return None
    if month is not None and not 1 <= month <= 12:
        return None
    if day is not None and not 1 <= day <= 31:
        return None
    return PartialDate(year, month, day)


def _from_match(kind: str, match: re.Match[str]) -> PartialDate | None:
    groups = match.groups()
    if kind == "iso":
        return _valid(int(groups[0]), int(groups[1]), int(groups[2]))
    if kind == "dmy_slash":
        # The data uses day/month/year: the first part goes above 12, the second never does.
        return _valid(int(groups[2]), int(groups[1]), int(groups[0]))
    if kind == "mdy_words":
        return _valid(int(groups[2]), MONTHS[groups[0].lower()], int(groups[1]))
    if kind == "dmy_words":
        return _valid(int(groups[2]), MONTHS[groups[1].lower()], int(groups[0]))
    if kind == "ym_iso":
        return _valid(int(groups[0]), int(groups[1]), None)
    if kind == "my_words":
        return _valid(int(groups[1]), MONTHS[groups[0].lower()], None)
    return _valid(int(groups[0]), None, None)


def find_dates(text: str) -> list[tuple[int, int, PartialDate]]:
    """Every date in ``text`` with its span, most precise reading first, without overlaps."""
    taken: list[tuple[int, int]] = []
    found: list[tuple[int, int, PartialDate]] = []
    for kind, pattern in COMPILED:
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(start < b and a < end for a, b in taken):
                continue
            value = _from_match(kind, match)
            if value is None:
                continue
            taken.append((start, end))
            found.append((start, end, value))
    found.sort()
    return found


def parse_date(text: str) -> PartialDate | None:
    """Parse one date cell. Returns ``None`` for blanks and unreadable values."""
    text = text.strip()
    if not text:
        return None
    dates = find_dates(text)
    return dates[0][2] if dates else None


def parse_dates(text: str) -> list[PartialDate]:
    """Parse a semicolon-separated list of dates."""
    out: list[PartialDate] = []
    for part in text.split(";"):
        value = parse_date(part)
        if value is not None and value not in out:
            out.append(value)
    return out


def dob_agrees(customer: PartialDate, hit: PartialDate) -> bool:
    """SOP 7: same year, and every part both give is equal."""
    if customer.year != hit.year:
        return False
    if customer.month is not None and hit.month is not None and customer.month != hit.month:
        return False
    if customer.day is not None and hit.day is not None and customer.day != hit.day:
        return False
    return True


def dob_contradicts(customer: PartialDate, hit: PartialDate) -> bool:
    """SOP 6: the year is more than one year out, or same year and month or day differ."""
    if abs(customer.year - hit.year) > 1:
        return True
    if customer.year == hit.year:
        if customer.month is not None and hit.month is not None:
            if customer.month != hit.month:
                return True
            if customer.day is not None and hit.day is not None and customer.day != hit.day:
                return True
    return False


def same_full_date(a: PartialDate | None, b: PartialDate | None) -> bool:
    return a is not None and b is not None and a.full and b.full and a == b


def compatible(a: PartialDate | None, b: PartialDate | None) -> bool:
    """True when ``a`` and ``b`` are equal in every part both give, or either is missing."""
    if a is None or b is None:
        return True
    return dob_agrees(a, b)


def before(a: PartialDate, b: PartialDate) -> bool:
    """True when ``a`` is certainly earlier than ``b`` at the precision both give."""
    if a.year != b.year:
        return a.year < b.year
    if a.month is None or b.month is None:
        return False
    if a.month != b.month:
        return a.month < b.month
    if a.day is None or b.day is None:
        return False
    return a.day < b.day


def max_age_at(dob: PartialDate, when: PartialDate) -> int:
    """The oldest the person can have been at ``when``."""
    age = when.year - dob.year
    if dob.month is None or when.month is None:
        return age
    if (when.month, when.day or 31) < (dob.month, dob.day or 1):
        age -= 1
    return age

"""Gather a hit's facts: names, dates of birth, countries, dated reports, death (SOP 5)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from same_person.screener.countries import countries_in_list, countries_in_text
from same_person.screener.dates import MONTH_RE, PartialDate, find_dates, parse_date, parse_dates
from same_person.screener.names import clean, hit_names

LISTING_PHRASES = {
    "sanctions": "a sanctioned individual",
    "pep": "a politically exposed person",
    "enforcement": "the subject of enforcement action",
    "adverse media": "named in adverse media",
    "soe": "a state-owned enterprise official",
}
OTHER_LISTING = "named on a watchlist"

REPORT_RE = re.compile(
    r"^\s*According to [^;\n]*;\s*(" + MONTH_RE + r" \d{1,2}, \d{4})\s*:", re.MULTILINE
)
AGE_RES = (
    re.compile(r"(?<!\d)(\d{1,3})[- ]years?[- ]old", re.I),
    re.compile(r"\baged (\d{1,3})\b", re.I),
    re.compile(r"\bage of (\d{1,3})\b", re.I),
    re.compile(r"(?<!\d)(\d{1,3}) years of age", re.I),
    re.compile(r"\bwas (\d{1,3})\b(?! years? old)", re.I),
)
BORN_RE = re.compile(r"\bborn (?:on |in )?", re.I)


@dataclass(slots=True)
class HitDob:
    """One possible date of birth and where it came from."""

    value: PartialDate
    source: str


@dataclass(slots=True)
class HitFacts:
    hit_id: str
    entity_type: str
    names: list[str]
    dobs: list[HitDob] = field(default_factory=list)
    dob_source: str = ""
    countries: set[str] = field(default_factory=set)
    earliest_report: PartialDate | None = None
    deceased: PartialDate | None = None
    search_ids: list[str] = field(default_factory=list)

    @property
    def company(self) -> bool:
        return self.entity_type.strip().lower() == "company"


def listing_phrase(primary: str, categories: str) -> str:
    category = primary.strip() or categories.split(";")[0].strip()
    return LISTING_PHRASES.get(category.lower(), OTHER_LISTING)


def _age_years(text: str) -> list[PartialDate]:
    """Two possible birth years from an age at a dated event."""
    when = None
    for _, _, value in find_dates(text):
        if value.month is not None or when is None:
            when = value
    if when is None:
        return []
    for pattern in AGE_RES:
        match = pattern.search(text)
        if match:
            age = int(match.group(1))
            if 0 < age < 120:
                return [PartialDate(when.year - age - 1), PartialDate(when.year - age)]
    return []


def dobs_from_profile(notes: str) -> list[PartialDate]:
    """A birth date, or an age at a date, in the first paragraph of the profile notes."""
    first = notes.strip().split("\n\n")[0].split("\n")[0]
    if not first or REPORT_RE.match(first):
        return []
    born = BORN_RE.search(first)
    if born:
        value = parse_date(first[born.end() : born.end() + 30])
        if value is not None:
            return [value]
    if any(pattern.search(first) for pattern in AGE_RES):
        return _age_years(first)
    return []


def search_query(name: str) -> str:
    return " ".join(sorted(clean(name).split()))


def _search_age(snippet: str) -> list[PartialDate]:
    """Age and month-year in a search result snippet."""
    dates = [d for _, _, d in find_dates(snippet)]
    if not dates:
        return []
    when = dates[0]
    stripped = re.sub(MONTH_RE + r",? \d{4}", " ", snippet)
    stripped = re.sub(r"\b\d{4}\b", " ", stripped)
    ages = [int(x) for x in re.findall(r"(?<![\d.])(\d{1,3})(?![\d.])", stripped)]
    ages = [a for a in ages if 0 < a < 120]
    if len(ages) != 1:
        return []
    return [PartialDate(when.year - ages[0] - 1), PartialDate(when.year - ages[0])]


def dobs_from_search(
    hit: dict[str, str], countries: set[str], search_index: dict[str, list[dict[str, str]]]
) -> tuple[list[PartialDate], list[str]]:
    phrase = listing_phrase(hit["primary_category"], hit["categories"])
    out: list[PartialDate] = []
    ids: list[str] = []
    for result in search_index.get(search_query(hit["name"]), []):
        snippet = result["snippet"]
        if phrase not in snippet.lower():
            continue
        if not countries & countries_in_text(snippet):
            continue
        years = _search_age(snippet)
        if years:
            ids.append(result["result_id"])
            out.extend(y for y in years if y not in out)
    return out, ids


def hit_countries(hit: dict[str, str]) -> set[str]:
    out = countries_in_list(hit["countries"])
    out |= countries_in_list(hit["nationality"], last=True)
    out |= countries_in_list(hit["citizenship"], last=True)
    out |= countries_in_list(hit["addresses"], last=True)
    out |= countries_in_list(hit["place_of_birth"], last=True)
    return out


def earliest_report(notes: str) -> PartialDate | None:
    dates = [parse_date(m.group(1)) for m in REPORT_RE.finditer(notes)]
    dates = [d for d in dates if d is not None]
    return min(dates, key=PartialDate.key) if dates else None


def gather(hit: dict[str, str], search_index: dict[str, list[dict[str, str]]]) -> HitFacts:
    facts = HitFacts(
        hit_id=hit["hit_id"],
        entity_type=hit["entity_type"],
        names=hit_names(hit["name"], hit["aliases"]),
    )
    facts.countries = hit_countries(hit)
    facts.earliest_report = earliest_report(hit["profile_notes"])
    if hit["deceased_date"].strip():
        facts.deceased = parse_date(hit["deceased_date"])
    listed = parse_dates(hit["dobs"])
    if listed:
        facts.dobs = [HitDob(d, "listed date of birth") for d in listed]
        facts.dob_source = "dobs"
    elif not facts.company:
        profile = dobs_from_profile(hit["profile_notes"])
        if profile:
            facts.dobs = [HitDob(d, "profile notes") for d in profile]
            facts.dob_source = "profile notes"
        else:
            found, ids = dobs_from_search(hit, facts.countries, search_index)
            if found:
                facts.dobs = [HitDob(d, "search results") for d in found]
                facts.dob_source = "search results"
                facts.search_ids = ids
    return facts

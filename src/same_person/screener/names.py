"""Name comparison from SOP principle 8."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

PLACEHOLDERS = {"na", "n/a", "test", "unknown", "xxx", "none", "null", "nil", "tbd", "nn"}
DEFAULT_LEGAL_FORMS = (
    "ab ag as asa bv co company corp corporation gmbh inc kg limited llc llp lp ltd na nv oy "
    "oyj plc sa sarl sas se sf sl spa srl"
).split()


def fold(text: str) -> str:
    """Lowercase and strip accents."""
    text = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def clean(text: str) -> str:
    """Fold, drop bracketed text and apostrophes, turn other punctuation into spaces."""
    text = fold(text)
    text = re.sub(r"\([^)]*\)|\[[^\]]*\]|\{[^}]*\}", " ", text)
    text = re.sub(r"['’`]", "", text)
    text = re.sub(r"[^\w\s]|_", " ", text)
    return " ".join(text.split())


@dataclass(slots=True)
class NameRules:
    """Variant groups and legal forms read from the policy sheet."""

    groups: dict[str, set[int]] = field(default_factory=dict)
    legal_forms: set[str] = field(default_factory=lambda: set(DEFAULT_LEGAL_FORMS))

    @classmethod
    def from_policy(cls, lines: list[str]) -> NameRules:
        rules = cls()
        start = None
        for index, line in enumerate(lines):
            if line.strip().lower() == "name_variants.json":
                start = index + 1
        if start is None:
            return rules
        group_id = 0
        for line in lines[start:]:
            text = line.strip()
            if not text:
                continue
            if text.lower().startswith("company legal forms:"):
                forms = text.split(":", 1)[1]
                rules.legal_forms = {clean(item) for item in forms.split(",") if clean(item)}
                continue
            if "," not in text or text.lower().startswith("groups of"):
                continue
            for item in text.split(","):
                token = clean(item)
                if token:
                    rules.groups.setdefault(token, set()).add(group_id)
            group_id += 1
        return rules

    def variants(self, a: str, b: str) -> bool:
        ga, gb = self.groups.get(a), self.groups.get(b)
        return bool(ga and gb and ga & gb)


def is_placeholder(token: str) -> bool:
    return token in PLACEHOLDERS or token.isdigit() or bool(re.fullmatch(r"x+", token))


def name_parts(name: str, rules: NameRules, *, company: bool = False) -> list[str]:
    """Distinct real name parts, in order. Initials and placeholders are not parts."""
    parts: list[str] = []
    for token in clean(name).split():
        if len(token) < 2 or is_placeholder(token):
            continue
        if company and token in rules.legal_forms:
            continue
        if token not in parts:
            parts.append(token)
    return parts


def levenshtein(a: str, b: str, limit: int) -> int:
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        if min(current) > limit:
            return limit + 1
        previous = current
    return previous[-1]


def parts_match(a: str, b: str, rules: NameRules) -> bool:
    if a == b or rules.variants(a, b):
        return True
    shorter = min(len(a), len(b))
    if shorter < 4:
        return False
    limit = 1 if shorter <= 6 else 2
    return levenshtein(a, b, limit) <= limit


def _units(parts: list[str]) -> list[tuple[str, frozenset[int]]]:
    """Single parts plus adjacent pairs written together (Abdul Salam -> abdulsalam)."""
    units = [(part, frozenset({i})) for i, part in enumerate(parts)]
    units += [(parts[i] + parts[i + 1], frozenset({i, i + 1})) for i in range(len(parts) - 1)]
    return units


def match_count(a: list[str], b: list[str], rules: NameRules) -> tuple[int, int, int]:
    """Greedy matching. Returns (matched units, covered parts of a, covered parts of b)."""
    used_a: set[int] = set()
    used_b: set[int] = set()
    matches = 0
    # Prefer exact single-part matches, then the rest.
    candidates: list[tuple[int, frozenset[int], frozenset[int]]] = []
    for ua, ia in _units(a):
        for ub, ib in _units(b):
            if len(ia) == 2 and len(ib) == 2:
                continue
            if parts_match(ua, ub, rules):
                rank = 0 if ua == ub else 1
                candidates.append((rank + len(ia) + len(ib), ia, ib))
    for _, ia, ib in sorted(candidates, key=lambda item: item[0]):
        if ia & used_a or ib & used_b:
            continue
        used_a |= ia
        used_b |= ib
        matches += 1
    return matches, len(used_a), len(used_b)


def names_agree(a: str, b: str, rules: NameRules, *, company: bool = False) -> bool:
    """SOP 8: at least two parts match, or every part of the shorter name matches."""
    pa = name_parts(a, rules, company=company)
    pb = name_parts(b, rules, company=company)
    if not pa or not pb:
        return False
    matches, covered_a, covered_b = match_count(pa, pb, rules)
    if matches >= 2:
        return True
    if len(pa) <= len(pb):
        return covered_a == len(pa)
    return covered_b == len(pb)


def any_agree(
    customer_names: list[str], hit_names: list[str], rules: NameRules, *, company: bool
) -> tuple[bool, str]:
    """Return whether any pair agrees, and the hit name that agreed."""
    for c in customer_names:
        for h in hit_names:
            if names_agree(c, h, rules, company=company):
                return True, h
    return False, ""


def hit_names(name: str, aliases: str) -> list[str]:
    names = [name] if name.strip() else []
    for alias in aliases.split(";"):
        alias = re.sub(r"\([^)]*\)", "", alias).strip()
        if alias and alias not in names:
            names.append(alias)
    return names

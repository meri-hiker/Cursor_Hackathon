"""Run the six pipeline steps over every hit and decide each one."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from types import ModuleType

from same_person.decisions import Decision, ReasonCode
from same_person.paths import repo_root
from same_person.screener.customer import (
    Flag,
    Person,
    Resolution,
    build_person,
    group_people,
    resolve,
)
from same_person.screener.decide import Verdict, decide, view_of
from same_person.screener.hitfacts import HitFacts, gather
from same_person.screener.store import Store

BASELINE = Path("Identity_matching_results 2") / "score_matches.py"

# Red flags that make a clearance unsafe: the fact that cleared the hit is itself in doubt.
UNSAFE_TO_CLEAR = {
    ReasonCode.DOB_MISMATCH: {
        "DOB_CHANGES_ACROSS_CHECKS",
        "LINKED_RECORDS_DIFFERENT_DOB",
        "VERIFIED_DOB_DIFFERS_FROM_RECORD",
    },
    ReasonCode.TEMPORAL_IMPOSSIBILITY: {
        "DOB_CHANGES_ACROSS_CHECKS",
        "LINKED_RECORDS_DIFFERENT_DOB",
        "VERIFIED_DOB_DIFFERS_FROM_RECORD",
    },
    ReasonCode.NAME_MISMATCH_VERIFIED: {"NAME_CHANGES_ACROSS_CHECKS"},
    ReasonCode.LOCATION_MISMATCH: set(),
}
ALWAYS_UNSAFE = {"NO_CUSTOMER_FOUND"}


@dataclass(slots=True)
class HitResult:
    hit_id: str
    alert_id: str
    customer_name: str
    hit_name: str
    entity_type: str
    hit_category: str
    link: str
    decision: str
    reason_codes: list[str]
    customer_record_id: str
    rating: int
    rating_band: str
    intrinsic_score: float | None
    explanation: str
    evidence: list[str]
    path: list[str]
    flags: list[Flag] = field(default_factory=list)
    sop_decision: str = ""
    label_decision: str = ""
    label_reasons: str = ""

    @property
    def high_flags(self) -> int:
        return sum(f.severity == "high" for f in self.flags)

    def to_row(self) -> dict[str, object]:
        return {
            "hit_id": self.hit_id,
            "alert_id": self.alert_id,
            "decision": self.decision,
            "reason_codes": "; ".join(self.reason_codes),
            "customer_record_id": self.customer_record_id,
            "rating": self.rating,
            "rating_band": self.rating_band,
            "intrinsic_score": "" if self.intrinsic_score is None else self.intrinsic_score,
            "red_flags_high": self.high_flags,
            "red_flags": " | ".join(str(f) for f in self.flags if f.severity != "info"),
            "info_flags": " | ".join(str(f) for f in self.flags if f.severity == "info"),
            "customer_name": self.customer_name,
            "hit_name": self.hit_name,
            "entity_type": self.entity_type,
            "hit_category": self.hit_category,
            "link": self.link,
            "explanation": self.explanation,
            "evidence": "; ".join(self.evidence),
            "path": " -> ".join(self.path),
            "sop_decision": self.sop_decision,
            "label_decision": self.label_decision,
            "label_reasons": self.label_reasons,
        }


# ---------------------------------------------------------------- step 2: existing baseline


@cache
def baseline() -> ModuleType | None:
    """Load ``score_matches.py`` so step 2 reuses its comparisons instead of copying them."""
    try:
        path = repo_root() / BASELINE
    except FileNotFoundError:
        return None
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("score_matches", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def intrinsic_score(alert: dict[str, str], hit: dict[str, str]) -> tuple[float | None, list[Flag]]:
    """Step 2: screened name, date of birth, country and city against the hit (0 to 100)."""
    sm = baseline()
    if sm is None:
        return None, []
    flags: list[Flag] = []
    names = [hit["name"]] + [a.strip() for a in hit["aliases"].split(";") if a.strip()]
    values: dict[str, float | None] = {}
    values["name"] = max(
        (sm.name_similarity(alert["customer_name"], n) or 0 for n in names), default=None
    )
    company = alert["entity_type"].strip().lower() == "company"
    weights = {k: w for k, w in sm.WEIGHTS.items() if not (company and k == "dob")}
    values["dob"] = None if company else sm.dob_comparison(alert["dob"], hit["dobs"])[0]
    location = "; ".join(x for x in (hit["countries"], hit["addresses"]) if x)
    values["country"] = sm.location(alert["country"], location)[0]
    values["city"] = sm.location(alert["city"], hit["addresses"])[0]
    available = sum(w for k, w in weights.items() if values.get(k) is not None)
    if not available:
        return None, flags
    similarity = sum(w * values[k] for k, w in weights.items() if values.get(k) is not None)
    similarity /= available
    coverage = available / sum(weights.values())
    score = 100 * similarity * (0.5 + 0.5 * coverage)
    if values["dob"] == 0.0:
        flags.append(
            Flag(
                "2 Intrinsic",
                "medium",
                "SCREENED_DOB_CONFLICT",
                f"screened {alert['dob']} vs hit {hit['dobs']}",
            )
        )
    if values["country"] == 0.25:
        flags.append(Flag("2 Intrinsic", "info", "SCREENED_COUNTRY_NOT_IN_HIT", alert["country"]))
    return round(score, 1), flags


# ---------------------------------------------------------------- step 1: alert against hit


def link_flags(alert: dict[str, str] | None, hit: dict[str, str]) -> list[Flag]:
    if alert is None:
        return [Flag("1 Alert", "high", "ALERT_NOT_FOUND", f"{hit['alert_id']} has no alert row")]
    flags = []
    listed = [h.strip() for h in alert["hit_ids"].split(";") if h.strip()]
    if hit["hit_id"] not in listed:
        flags.append(
            Flag(
                "1 Alert",
                "high",
                "HIT_NOT_ON_ALERT",
                f"{hit['hit_id']} is not in {alert['alert_id']} hit_ids",
            )
        )
    if alert["hit_count"].strip().isdigit() and int(alert["hit_count"]) != len(listed):
        flags.append(
            Flag(
                "1 Alert",
                "medium",
                "HIT_COUNT_MISMATCH",
                f"hit_count {alert['hit_count']} but {len(listed)} ids",
            )
        )
    return flags


# ---------------------------------------------------------------- deciding and rating


def combine(verdicts: list[Verdict]) -> tuple[Decision, list[ReasonCode]]:
    """SOP 3: if every person leads to the same decision, give it; otherwise REVIEW."""
    decisions = {v.decision for v in verdicts}
    if len(decisions) == 1:
        codes: list[ReasonCode] = []
        for v in verdicts:
            codes += [c for c in v.codes if c not in codes]
        if len(verdicts) > 1:
            codes = [c for c in codes if all(c in v.codes for v in verdicts)] or verdicts[0].codes
        return verdicts[0].decision, codes
    return Decision.REVIEW, [ReasonCode.AMBIGUOUS_CUSTOMER_RECORD]


def caution(
    decision: Decision, codes: list[ReasonCode], flags: list[Flag]
) -> tuple[Decision, list[ReasonCode], str]:
    """Lean to REVIEW when a red flag casts doubt on the fact that cleared the hit."""
    if decision != Decision.DIFFERENT_ENTITY:
        return decision, codes, ""
    raised = {f.code for f in flags if f.severity == "high"}
    blocking = raised & ALWAYS_UNSAFE
    safe_codes = [c for c in codes if not (UNSAFE_TO_CLEAR.get(c, set()) & raised)]
    if blocking:
        return (
            Decision.REVIEW,
            [ReasonCode.INSUFFICIENT_DATA],
            ("Held for review: " + ", ".join(sorted(blocking))),
        )
    if not safe_codes:
        doubts = sorted(set().union(*(UNSAFE_TO_CLEAR.get(c, set()) for c in codes)) & raised)
        return (
            Decision.REVIEW,
            [ReasonCode.INSUFFICIENT_DATA],
            ("Held for review: the clearing fact is in doubt (" + ", ".join(doubts) + ")"),
        )
    return decision, safe_codes, ""


def shadow_identities(
    store: Store, alert: dict[str, str], facts: HitFacts, flags: list[Flag]
) -> str:
    """Step 3 red flag: another login on the same phone, opened the same day.

    If that other identity would not clear the hit, the clearance is unsafe.
    """
    related = sorted(
        {
            rid
            for f in flags
            if f.code == "SAME_PHONE_SAME_OPENING_DATE"
            for rid in f.related
            if rid in store.customers
        }
    )
    if not related:
        return ""
    for group in group_people(store, related):
        person = build_person(store, group, alert)
        verdict = decide(store, view_of(person, alert), facts, alert)
        if verdict.decision != Decision.DIFFERENT_ENTITY:
            return (
                f"{', '.join(group)} shares this customer's phone and opening date and would be "
                f"{verdict.decision.value} for this hit"
            )
    return ""


def rate(
    decision: Decision, intrinsic: float | None, verdicts: list[Verdict], flags: list[Flag]
) -> tuple[int, str]:
    """A 0-100 same-entity rating. The decision sets the band; evidence places it inside."""
    agreements = 0
    for v in verdicts:
        agreements = max(
            agreements,
            sum(1 for x in (v.name_agree, v.dob_agree, v.location_agree) if x),
        )
    evidence = (intrinsic or 0) / 100 * 0.5 + agreements / 3 * 0.5
    high = sum(f.severity == "high" for f in flags)
    if decision == Decision.SAME_ENTITY:
        return round(80 + 20 * evidence), "High"
    if decision == Decision.REVIEW:
        return min(79, round(40 + 30 * evidence + 3 * high)), "Medium"
    return min(39, round(5 + 25 * evidence + 3 * high)), "Low"


def screen_hit(
    store: Store,
    alert: dict[str, str] | None,
    hit: dict[str, str],
    resolutions: dict[str, Resolution],
    *,
    cautious: bool = True,
) -> HitResult:
    flags = link_flags(alert, hit)
    facts: HitFacts = gather(hit, store.search_index)
    label = store.labels.get(hit["hit_id"], {})
    if alert is None:
        return HitResult(
            hit["hit_id"],
            hit["alert_id"],
            "",
            hit["name"],
            hit["entity_type"],
            hit["primary_category"] or hit["categories"],
            "",
            Decision.REVIEW.value,
            [ReasonCode.INSUFFICIENT_DATA.value],
            "",
            50,
            "Medium",
            None,
            "The alert for this hit was not found.",
            [hit["hit_id"]],
            [],
            flags,
            Decision.REVIEW.value,
            label.get("decision", ""),
            label.get("reason_codes", ""),
        )
    score, step2 = intrinsic_score(alert, hit)
    flags += step2
    if alert["alert_id"] not in resolutions:
        resolutions[alert["alert_id"]] = resolve(store, alert)
    res = resolutions[alert["alert_id"]]
    flags += res.flags
    persons: list[Person | None] = list(res.persons) or [None]
    for person in res.persons:
        flags += person.flags
    verdicts = [decide(store, view_of(p, alert), facts, alert) for p in persons]
    decision, codes = combine(verdicts)
    sop_decision = decision
    note = ""
    if cautious:
        decision, codes, note = caution(decision, codes, flags)
        if decision == Decision.DIFFERENT_ENTITY:
            shadow = shadow_identities(store, alert, facts, flags)
            if shadow:
                decision, codes = Decision.REVIEW, [ReasonCode.INSUFFICIENT_DATA]
                note = "Held for review: " + shadow
    if len(res.persons) > 1:
        record = "AMBIGUOUS"
    elif res.persons:
        record = res.persons[0].primary
    else:
        record = ""
    explanation = " ".join(dict.fromkeys(r for v in verdicts for r in v.reasons))
    if len(verdicts) > 1:
        explanation = (
            f"{len(verdicts)} different people fit; each decided separately. " + explanation
        )
    if note:
        explanation = f"{note}. {explanation}"
    evidence = list(dict.fromkeys(e for v in verdicts for e in v.evidence if e))
    rating, band = rate(decision, score, verdicts, flags)
    return HitResult(
        hit_id=hit["hit_id"],
        alert_id=alert["alert_id"],
        customer_name=alert["customer_name"],
        hit_name=hit["name"],
        entity_type=alert["entity_type"],
        hit_category=hit["primary_category"] or hit["categories"],
        link=res.link,
        decision=decision.value,
        reason_codes=[c.value for c in codes],
        customer_record_id=record,
        rating=rating,
        rating_band=band,
        intrinsic_score=score,
        explanation=explanation.strip(),
        evidence=evidence,
        path=res.path,
        flags=_dedupe(flags),
        sop_decision=sop_decision.value,
        label_decision=label.get("decision", ""),
        label_reasons=label.get("reason_codes", ""),
    )


def _dedupe(flags: list[Flag]) -> list[Flag]:
    seen: set[tuple[str, str]] = set()
    out = []
    for f in flags:
        if (f.code, f.detail) in seen:
            continue
        seen.add((f.code, f.detail))
        out.append(f)
    order = {"high": 0, "medium": 1, "info": 2}
    return sorted(out, key=lambda f: (order.get(f.severity, 3), f.step))


def run_screener(
    store: Store | None = None, *, cautious: bool = True, hit_ids: set[str] | None = None
) -> list[HitResult]:
    store = store or Store.load()
    alerts = {a["alert_id"]: a for a in store.alerts}
    resolutions: dict[str, Resolution] = {}
    results = []
    for hit in store.hits:
        if hit_ids is not None and hit["hit_id"] not in hit_ids:
            continue
        results.append(
            screen_hit(store, alerts.get(hit["alert_id"]), hit, resolutions, cautious=cautious)
        )
    return results


# ---------------------------------------------------------------- scoring


def score_against_labels(results: list[HitResult]) -> dict[str, object]:
    labelled = [r for r in results if r.label_decision]
    if not labelled:
        return {"labelled": 0}
    correct = [r for r in labelled if r.decision == r.label_decision]
    reason_ok = [
        r for r in correct if set(r.reason_codes) & {c.strip() for c in r.label_reasons.split(";")}
    ]
    matrix: dict[str, dict[str, int]] = {}
    for r in labelled:
        matrix.setdefault(r.label_decision, {}).setdefault(r.decision, 0)
        matrix[r.label_decision][r.decision] += 1
    worst = [
        r.hit_id
        for r in labelled
        if r.decision == "DIFFERENT_ENTITY" and r.label_decision != "DIFFERENT_ENTITY"
    ]
    missed_same = [
        r.hit_id
        for r in labelled
        if r.label_decision == "SAME_ENTITY" and r.decision != "SAME_ENTITY"
    ]
    over_review = [
        r.hit_id for r in labelled if r.decision == "REVIEW" and r.label_decision != "REVIEW"
    ]
    return {
        "labelled": len(labelled),
        "correct": len(correct),
        "accuracy": round(len(correct) / len(labelled), 3),
        "correct_with_accepted_reason": len(reason_ok),
        "wrongly_cleared": worst,
        "missed_same_entity": missed_same,
        "reviewed_but_decidable": over_review,
        "wrong": [
            {
                "hit_id": r.hit_id,
                "label": r.label_decision,
                "got": r.decision,
                "label_reasons": r.label_reasons,
                "got_reasons": "; ".join(r.reason_codes),
            }
            for r in labelled
            if r.decision != r.label_decision
        ],
        "confusion": matrix,
    }

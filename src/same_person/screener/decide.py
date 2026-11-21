"""SOP principles 6 and 7: clear only on a contradiction, confirm only on agreement."""

from __future__ import annotations

from dataclasses import dataclass, field

from same_person.decisions import Decision, ReasonCode
from same_person.screener.countries import canonical
from same_person.screener.customer import Person
from same_person.screener.dates import (
    PartialDate,
    before,
    dob_agrees,
    dob_contradicts,
    max_age_at,
    parse_date,
)
from same_person.screener.hitfacts import HitFacts
from same_person.screener.names import any_agree, name_parts
from same_person.screener.store import Store


@dataclass(slots=True)
class Verdict:
    decision: Decision
    codes: list[ReasonCode]
    reasons: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    name_agree: bool | None = None
    dob_agree: bool | None = None
    location_agree: bool | None = None


@dataclass(slots=True)
class CustomerView:
    """What the decision compares: a resolved person, or only the screened details."""

    entity_type: str
    names: list[str]
    verified_name: str
    dob: PartialDate | None
    dob_source: str
    countries: set[str]
    evidence: list[str]

    @property
    def company(self) -> bool:
        return self.entity_type.strip().lower() == "company"


def view_of(person: Person | None, alert: dict[str, str]) -> CustomerView:
    if person is None:
        country = canonical(alert["country"]) if alert["country"].strip() else None
        return CustomerView(
            entity_type=alert["entity_type"],
            names=[alert["customer_name"]],
            verified_name="",
            dob=parse_date(alert["dob"]),
            dob_source="screened",
            countries={country} if country else set(),
            evidence=[],
        )
    evidence = [person.primary] if person.primary else []
    if person.verified is not None:
        evidence.append(person.verified.source_id)
    verified = person.verified.name if person.verified is not None else ""
    return CustomerView(
        entity_type=person.entity_type or alert["entity_type"],
        names=person.compare_names() or [alert["customer_name"]],
        verified_name=verified,
        dob=person.dob,
        dob_source=person.dob_source,
        countries=set(person.countries),
        evidence=evidence,
    )


def decide(store: Store, view: CustomerView, hit: HitFacts, alert: dict[str, str]) -> Verdict:
    reasons: list[str] = []
    evidence = list(view.evidence)
    company = view.company
    contradictions: list[ReasonCode] = []

    # Type.
    if view.entity_type.strip() and hit.entity_type.strip():
        if view.entity_type.strip().lower() != hit.entity_type.strip().lower():
            return Verdict(
                Decision.DIFFERENT_ENTITY,
                [ReasonCode.ENTITY_TYPE_MISMATCH],
                [f"The customer is a {view.entity_type} but the hit is a {hit.entity_type}."],
                evidence,
            )

    # Death before screening.
    screened = parse_date(alert["screened_at"])
    if hit.deceased is not None and screened is not None and before(hit.deceased, screened):
        contradictions.append(ReasonCode.DECEASED)
        reasons.append(f"The hit died on {hit.deceased}, before the screening on {screened}.")

    # Time.
    if view.dob is not None and hit.earliest_report is not None:
        if company and before(hit.earliest_report, view.dob):
            contradictions.append(ReasonCode.TEMPORAL_IMPOSSIBILITY)
            reasons.append(
                f"The company was incorporated {view.dob} ({view.dob_source}), after the hit's "
                f"report of {hit.earliest_report}."
            )
        elif not company and max_age_at(view.dob, hit.earliest_report) < 16:
            contradictions.append(ReasonCode.TEMPORAL_IMPOSSIBILITY)
            reasons.append(
                f"The customer was born {view.dob} ({view.dob_source}), so they were at most "
                f"{max(max_age_at(view.dob, hit.earliest_report), 0)} at the hit's report of "
                f"{hit.earliest_report}."
            )

    # Name.
    real_parts = max(
        (len(name_parts(n, store.rules, company=company)) for n in view.names), default=0
    )
    name_usable = company or real_parts >= 2
    name_agree, matched = any_agree(view.names, hit.names, store.rules, company=company)
    if not name_usable:
        name_agree_value: bool | None = None
        reasons.append("The customer's name has fewer than two real parts.")
    else:
        name_agree_value = name_agree
    if name_usable and not name_agree and view.verified_name:
        contradictions.append(ReasonCode.NAME_MISMATCH_VERIFIED)
        reasons.append(
            f"The customer's verified name is {view.verified_name}, which does not match the "
            f"hit's name {hit.names[0] if hit.names else ''}."
        )

    # Date of birth (incorporation date for companies).
    dob_agree: bool | None = None
    dob_contra = False
    if view.dob is not None and hit.dobs:
        values = [d.value for d in hit.dobs]
        dob_agree = any(dob_agrees(view.dob, h) for h in values)
        dob_contra = all(dob_contradicts(view.dob, h) for h in values)
        shown = ", ".join(str(v) for v in values)
        if dob_contra:
            contradictions.append(ReasonCode.DOB_MISMATCH)
            reasons.append(
                f"The customer's date of birth is {view.dob} ({view.dob_source}) but the hit's "
                f"{hit.dob_source} give {shown}."
            )
            evidence += hit.search_ids
        elif dob_agree:
            reasons.append(
                f"The customer's date of birth {view.dob} ({view.dob_source}) agrees with the "
                f"hit's {shown} ({hit.dob_source})."
            )
            evidence += hit.search_ids

    # Location.
    location_agree: bool | None = None
    location_contra = False
    if view.countries and hit.countries:
        shared = view.countries & hit.countries
        location_agree = bool(shared)
        location_contra = not shared
        if shared:
            reasons.append(f"Both are linked to {', '.join(sorted(shared))}.")
        else:
            reasons.append(
                f"The customer is linked only to {', '.join(sorted(view.countries))} but the hit "
                f"only to {', '.join(sorted(hit.countries))}."
            )
    if name_agree_value:
        reasons.append(f"The customer's name agrees with the hit's name {matched}.")

    verdict = Verdict(
        Decision.REVIEW,
        [ReasonCode.INSUFFICIENT_DATA],
        reasons,
        evidence,
        name_agree_value,
        dob_agree,
        location_agree,
    )

    # Confirm only on agreement.
    hard = [c for c in contradictions if c != ReasonCode.LOCATION_MISMATCH]
    if name_agree_value and not hard:
        if not company and dob_agree:
            if location_contra:
                reasons.append("A country difference does not clear a name and DOB match.")
                return verdict
            verdict.decision = Decision.SAME_ENTITY
            verdict.codes = (
                [ReasonCode.NAME_DOB_LOCATION_MATCH, ReasonCode.NAME_DOB_MATCH]
                if location_agree
                else [ReasonCode.NAME_DOB_MATCH]
            )
            return verdict
        if company and location_agree and not dob_contra:
            verdict.decision = Decision.SAME_ENTITY
            verdict.codes = [ReasonCode.NAME_LOCATION_MATCH]
            return verdict

    # Clear only on a contradiction.
    if location_contra:
        contradictions.append(ReasonCode.LOCATION_MISMATCH)
    if contradictions:
        verdict.decision = Decision.DIFFERENT_ENTITY
        verdict.codes = list(dict.fromkeys(contradictions))
        return verdict
    reasons.append("Nothing contradicts the hit and there is not enough agreement to confirm it.")
    return verdict

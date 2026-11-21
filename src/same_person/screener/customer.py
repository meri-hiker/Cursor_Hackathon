"""Steps 3 to 6: find the customer behind an alert and build one picture per person."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from same_person.screener.countries import canonical
from same_person.screener.dates import PartialDate, compatible, parse_date, same_full_date
from same_person.screener.names import clean, fold, name_parts, names_agree
from same_person.screener.store import Check, Store, _phone


@dataclass(slots=True)
class Flag:
    step: str
    severity: str  # "high", "medium", "info"
    code: str
    detail: str
    related: tuple[str, ...] = ()

    def __str__(self) -> str:
        return f"[{self.step}] {self.code}: {self.detail}"


@dataclass(slots=True)
class Person:
    """Everything the bank knows about one person, possibly over several records."""

    record_ids: list[str]
    entity_type: str
    record_names: list[str] = field(default_factory=list)
    verified: Check | None = None
    dob: PartialDate | None = None
    dob_source: str = ""
    countries: set[str] = field(default_factory=set)
    country_sources: list[str] = field(default_factory=list)
    accounts: list[str] = field(default_factory=list)
    applicants: list[str] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)

    @property
    def company(self) -> bool:
        return self.entity_type.strip().lower() == "company"

    @property
    def primary(self) -> str:
        return self.record_ids[0] if self.record_ids else ""

    def compare_names(self) -> list[str]:
        """The verified legal name outranks the record and screened names (SOP 4)."""
        if self.verified is not None and self.verified.name.strip():
            return [self.verified.name]
        return self.record_names


@dataclass(slots=True)
class Resolution:
    link: str
    candidates: list[str]
    persons: list[Person]
    flags: list[Flag] = field(default_factory=list)
    path: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- matching the screened details


def fits_screened(store: Store, rid: str, alert: dict[str, str], *, use_name: bool = True) -> bool:
    """SOP 2: name, date of birth, country and city fit the screened details."""
    row = store.customers[rid]
    company = row["entity_type"].lower() == "company"
    if use_name:
        names = [row["full_name"], row["preferred_name"], row["login_name"]]
        screened = alert["customer_name"]
        if len(name_parts(screened, store.rules, company=company)) == 0:
            pass
        elif not any(n and names_agree(screened, n, store.rules, company=company) for n in names):
            return False
    screened_dob = parse_date(alert["dob"])
    if screened_dob is not None and not compatible(store.dob.get(rid), screened_dob):
        return False
    if alert["country"].strip():
        wanted = canonical(alert["country"])
        have = {canonical(row["residence_country"]), canonical(row["nationality"])}
        if wanted and wanted not in have:
            return False
    if alert["city"].strip():
        city = clean(alert["city"])
        if city and city not in clean(row["city"]) and city not in clean(row["address"]):
            return False
    return True


def search_customers(store: Store, alert: dict[str, str]) -> list[str]:
    """No link on the alert: find the records that fit what was screened."""
    company = alert["entity_type"].lower() == "company"
    parts = name_parts(alert["customer_name"], store.rules, company=company)
    pool: set[str] = set()
    for part in parts:
        pool |= store.customers_by_part.get(part, set())
        for other, groups in store.rules.groups.items():
            if groups & store.rules.groups.get(part, set()):
                pool |= store.customers_by_part.get(other, set())
    screened_dob = parse_date(alert["dob"])
    if screened_dob is not None and screened_dob.full:
        pool |= store.customers_by_dob.get(screened_dob, set())
    return sorted(rid for rid in pool if fits_screened(store, rid, alert))


# ---------------------------------------------------------------- one person, one picture


def same_person_records(store: Store, rid: str) -> set[str]:
    """SOP 3: same phone and full date of birth, or same document on approved checks."""
    group = {rid}
    queue = [rid]
    while queue:
        current = queue.pop()
        row = store.customers[current]
        linked: set[str] = set()
        dob = store.dob.get(current)
        if dob is not None and dob.full and row["phone"]:
            linked |= store.customers_by_phone_dob.get((_phone(row["phone"]), dob), set())
        for _, applicant in store.applicants_by_customer.get(current, []):
            for check in store.checks_by_applicant.get(applicant, []):
                if check.approved and check.document_number:
                    for other_app in store.applicants_by_document.get(
                        check.document_number.upper(), set()
                    ):
                        linked |= store.customers_by_applicant.get(other_app, set())
        for other in linked - group:
            if other in store.customers:
                group.add(other)
                queue.append(other)
    return group


def group_people(store: Store, candidates: list[str]) -> list[list[str]]:
    groups: list[set[str]] = []
    for rid in candidates:
        if any(rid in g for g in groups):
            continue
        groups.append(same_person_records(store, rid))
    merged: list[set[str]] = []
    for g in groups:
        for m in merged:
            if g & m:
                m |= g
                break
        else:
            merged.append(set(g))
    ordered = []
    for g in merged:
        first = [rid for rid in candidates if rid in g]
        rest = sorted(g - set(first))
        ordered.append(first + rest)
    return ordered


# ---------------------------------------------------------------- the picture and red flags


def _checks_for(store: Store, record_ids: list[str]) -> tuple[list[Check], list[str], list[str]]:
    checks: list[Check] = []
    applicants: list[str] = []
    accounts: list[str] = []
    for rid in record_ids:
        for account_id, applicant in store.applicants_by_customer.get(rid, []):
            if applicant not in applicants:
                applicants.append(applicant)
                checks.extend(store.checks_by_applicant.get(applicant, []))
            if account_id not in accounts:
                accounts.append(account_id)
        for row in store.accounts_by_customer.get(rid, []):
            if row["account_id"] not in accounts:
                accounts.append(row["account_id"])
        email = store.customers[rid]["email"].strip().lower()
        if email:
            checks.extend(store.kyc_notes_by_email.get(email, []))
    return checks, applicants, accounts


def build_person(store: Store, record_ids: list[str], alert: dict[str, str]) -> Person:
    rows = [store.customers[rid] for rid in record_ids]
    person = Person(record_ids=record_ids, entity_type=rows[0]["entity_type"])
    for row in rows:
        for name in (row["full_name"], row["preferred_name"]):
            if name and name not in person.record_names:
                person.record_names.append(name)
    checks, person.applicants, person.accounts = _checks_for(store, record_ids)
    approved = [c for c in checks if c.approved]
    if approved:
        person.verified = max(
            approved,
            key=lambda c: ((c.checked.key() if c.checked else (0, 0, 0)), c.kind == "kyc"),
        )

    # Date of birth: the latest approved check, else the record.
    if person.verified is not None and person.verified.dob is not None:
        person.dob = person.verified.dob
        person.dob_source = person.verified.source_id
    else:
        for rid in record_ids:
            if store.dob.get(rid) is not None:
                person.dob, person.dob_source = store.dob[rid], rid
                break

    # Countries: residence and nationality on every record, approved documents, notes.
    for rid, row in zip(record_ids, rows, strict=True):
        for value in (row["residence_country"], row["nationality"]):
            country = canonical(value)
            if country:
                person.countries.add(country)
                if rid not in person.country_sources:
                    person.country_sources.append(rid)
        email = row["email"].strip().lower()
        for note_id, found in store.lived_by_email.get(email, []):
            person.countries |= found
            person.country_sources.append(note_id)
    for check in approved:
        for value in check.country.split(";"):
            country = canonical(value) or None
            if country:
                person.countries.add(country)
    person.flags = picture_flags(store, person, rows, checks, alert)
    return person


def picture_flags(
    store: Store,
    person: Person,
    rows: list[dict[str, str]],
    checks: list[Check],
    alert: dict[str, str],
) -> list[Flag]:
    flags: list[Flag] = []
    rids = person.record_ids

    # Step 3: accounts.
    account_rows = [r for rid in rids for r in store.accounts_by_customer.get(rid, [])]
    accounts = sorted({r["account_id"] for r in account_rows})
    if len(accounts) > 1:
        flags.append(
            Flag(
                "3 Accounts",
                "info",
                "MULTIPLE_ACCOUNTS",
                f"{len(accounts)} accounts: " + ", ".join(accounts),
            )
        )
    handles = sorted({r["login_handle"] for r in account_rows if r["login_handle"]})
    if len(handles) > 1:
        # Linked records each carry their own login; several on one record is the odd case.
        per_record = max(
            len(
                {
                    r["login_handle"]
                    for r in store.accounts_by_customer.get(rid, [])
                    if r["login_handle"]
                }
            )
            for rid in rids
        )
        stems = {re.sub(r"\d+$", "", h.lower()) for h in handles}
        flags.append(
            Flag(
                "3 Accounts",
                "medium" if per_record > 1 and len(stems) > 1 else "info",
                "MULTIPLE_LOGIN_HANDLES",
                ", ".join(handles),
            )
        )
    seen_pairs: set[tuple[str, str]] = set()
    for row in account_rows:
        phone = _phone(row["phone"])
        if not phone:
            continue
        for other in store.accounts_by_phone.get(phone, []):
            if other["customer_id"] in rids or other["account_id"] == row["account_id"]:
                continue
            pair = (other["account_id"], other["customer_id"])
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            different_handle = fold(other["login_handle"]) != fold(row["login_handle"])
            if other["opened_at"] == row["opened_at"] and different_handle:
                flags.append(
                    Flag(
                        "3 Accounts",
                        "high",
                        "SAME_PHONE_SAME_OPENING_DATE",
                        f"login {row['login_handle'] or '-'} and {other['login_handle'] or '-'}"
                        f" ({other['customer_id']}, {other['account_id']}) share phone "
                        f"{row['phone']} and were opened the same day {row['opened_at']}",
                        (other["customer_id"],),
                    )
                )
            elif other["account_id"] not in accounts:
                flags.append(
                    Flag(
                        "3 Accounts",
                        "info",
                        "SHARED_PHONE",
                        f"phone {row['phone']} also on {other['account_id']} "
                        f"({other['customer_id']}, login {other['login_handle'] or '-'})",
                    )
                )
    for row in account_rows:
        if row["status"] in {"closed", "dormant"}:
            flags.append(
                Flag("3 Accounts", "info", "ACCOUNT_" + row["status"].upper(), row["account_id"])
            )

    # Step 4 and 5: onboarding and KYC.
    if not person.applicants:
        flags.append(Flag("4 Onboarding", "medium", "NO_KYC_APPLICANT", "no onboarding link"))
    kyc_checks = [c for c in checks if c.kind == "kyc"]
    bad = [c for c in kyc_checks if not c.approved]
    if bad:
        flags.append(
            Flag(
                "5 KYC",
                "medium",
                "REJECTED_OR_EXPIRED_CHECKS",
                ", ".join(f"{c.source_id} {c.result}" for c in bad),
            )
        )
    approved = [c for c in checks if c.approved]
    if not approved:
        flags.append(Flag("5 KYC", "medium", "NO_APPROVED_CHECK", "identity never verified"))

    # Different dates of birth used at different times. Only approved checks are evidence
    # (SOP 4), so a difference that appears only on rejected or expired checks is medium.
    def _dob_trail(items: list[Check]) -> str:
        return "; ".join(
            f"{c.source_id} ({c.result}, {c.checked}): {c.dob}" for c in items if c.dob is not None
        )

    approved_dobs = {str(c.dob) for c in approved if c.dob is not None}
    all_dobs = {str(c.dob) for c in checks if c.dob is not None}
    if len(approved_dobs) > 1:
        flags.append(Flag("5 KYC", "high", "DOB_CHANGES_ACROSS_CHECKS", _dob_trail(checks)))
    elif len(all_dobs) > 1:
        flags.append(Flag("5 KYC", "medium", "OTHER_DOB_ON_FAILED_CHECK", _dob_trail(checks)))
    approved_names = {clean(c.name) for c in approved if c.name}
    all_names = {clean(c.name) for c in checks if c.name}

    def _consistent(names: set[str]) -> bool:
        return all(
            names_agree(a, b, store.rules, company=person.company) for a in names for b in names
        )

    if len(approved_names) > 1 and not _consistent(approved_names):
        flags.append(
            Flag("5 KYC", "high", "NAME_CHANGES_ACROSS_CHECKS", "; ".join(sorted(all_names)))
        )
    elif len(all_names) > 1 and not _consistent(all_names):
        flags.append(
            Flag("5 KYC", "medium", "OTHER_NAME_ON_FAILED_CHECK", "; ".join(sorted(all_names)))
        )
    docs = {c.document_number for c in kyc_checks if c.document_number}
    if len(docs) > 1:
        flags.append(Flag("5 KYC", "medium", "SEVERAL_DOCUMENTS", ", ".join(sorted(docs))))
    v = person.verified
    if v is not None:
        record_dobs = {str(store.dob[r]) for r in rids if store.dob.get(r) is not None}
        if v.dob is not None and record_dobs and str(v.dob) not in record_dobs:
            flags.append(
                Flag(
                    "5 KYC",
                    "high",
                    "VERIFIED_DOB_DIFFERS_FROM_RECORD",
                    f"verified {v.dob} ({v.source_id}) vs record {', '.join(sorted(record_dobs))}",
                )
            )
        if v.name and not any(
            names_agree(v.name, n, store.rules, company=person.company) for n in person.record_names
        ):
            flags.append(
                Flag(
                    "5 KYC",
                    "high",
                    "VERIFIED_NAME_DIFFERS_FROM_RECORD",
                    f"verified '{v.name}' ({v.source_id}) vs record "
                    + " / ".join(person.record_names),
                )
            )
        if alert["customer_name"].strip() and not names_agree(
            v.name, alert["customer_name"], store.rules, company=person.company
        ):
            flags.append(
                Flag(
                    "5 KYC",
                    "medium",
                    "SCREENED_NAME_NOT_VERIFIED_NAME",
                    f"screened '{alert['customer_name']}' vs verified '{v.name}'",
                )
            )
    screened_dob = parse_date(alert["dob"])
    if (
        screened_dob is not None
        and person.dob is not None
        and not compatible(screened_dob, person.dob)
    ):
        flags.append(
            Flag(
                "5 KYC",
                "high",
                "SCREENED_DOB_DIFFERS",
                f"screened {screened_dob} vs {person.dob} ({person.dob_source})",
            )
        )

    # Step 6: several records for one person.
    if len(rids) > 1:
        flags.append(
            Flag(
                "6 Customers",
                "info",
                "LINKED_RECORDS",
                "same phone and date of birth, or same document: " + ", ".join(rids),
            )
        )
        differing: dict[str, list[str]] = {"high": [], "medium": [], "info": []}
        for column in ("dob", "nationality", "residence_country", "address", "email", "phone"):
            values = {
                (str(parse_date(r[column])) if column == "dob" else r[column].strip().lower())
                for r in rows
                if r[column].strip()
            }
            if column in ("nationality", "residence_country"):
                values = {canonical(x) or x for x in values}
            if len(values) > 1:
                severity = {"dob": "high", "nationality": "medium", "residence_country": "medium"}
                differing[severity.get(column, "info")].append(
                    f"{column}: {', '.join(sorted(values))}"
                )
        for severity, items in differing.items():
            if items:
                flags.append(
                    Flag("6 Customers", severity, "LINKED_RECORDS_DIFFER", "; ".join(items))
                )
    return flags


def joint_flags(store: Store, account_id: str, holders: list[str]) -> list[Flag]:
    """Step 6.1 and 6.2: compare the holders of a joint account."""
    flags: list[Flag] = []
    if len(holders) < 2:
        return flags
    flags.append(
        Flag("6 Joint", "info", "JOINT_ACCOUNT", f"{account_id} holders: " + ", ".join(holders))
    )
    rows = [store.customers[h] for h in holders if h in store.customers]
    for i, a in enumerate(rows):
        for b in rows[i + 1 :]:
            same = []
            if a["phone"] and _phone(a["phone"]) == _phone(b["phone"]):
                same.append("phone")
            if a["address"] and fold(a["address"]) == fold(b["address"]):
                same.append("address")
            if a["email"] and a["email"].lower() == b["email"].lower():
                same.append("email")
            dob_a, dob_b = parse_date(a["dob"]), parse_date(b["dob"])
            if same_full_date(dob_a, dob_b):
                same.append("date of birth")
            if canonical(a["nationality"]) and canonical(a["nationality"]) == canonical(
                b["nationality"]
            ):
                same.append("nationality")
            if not same:
                continue
            severity = "high" if {"phone", "date of birth"} <= set(same) else "info"
            if "email" in same:
                severity = "high"
            flags.append(
                Flag(
                    "6 Joint",
                    severity,
                    "JOINT_HOLDERS_SHARE_" + "_".join(s.upper().replace(" ", "_") for s in same),
                    f"{a['record_id']} ({a['full_name']}) and {b['record_id']} "
                    f"({b['full_name']}) share {', '.join(same)}",
                )
            )
    return flags


# ---------------------------------------------------------------- resolution


def resolve(store: Store, alert: dict[str, str]) -> Resolution:
    """Follow the alert's link, or search, then group the fitting records into persons."""
    flags: list[Flag] = []
    path: list[str] = []
    candidates: list[str] = []
    link = ""
    if alert["account_ref"].strip():
        link = f"account_ref {alert['account_ref']}"
        rows = store.accounts_by_id.get(alert["account_ref"].strip(), [])
        holders = sorted({r["customer_id"] for r in rows if r["customer_id"] in store.customers})
        path.append(f"{link} -> accounts -> {', '.join(holders) or 'nothing'}")
        flags.extend(joint_flags(store, alert["account_ref"], holders))
        candidates = holders
    elif alert["login_handle"].strip():
        link = f"login_handle {alert['login_handle']}"
        rows = store.accounts_by_login.get(alert["login_handle"].strip().lower(), [])
        holders = sorted({r["customer_id"] for r in rows if r["customer_id"] in store.customers})
        path.append(
            f"{link} -> accounts {', '.join(sorted({r['account_id'] for r in rows}))} -> "
            f"{', '.join(holders) or 'nothing'}"
        )
        if len(holders) > 1:
            flags.append(
                Flag("3 Accounts", "medium", "LOGIN_HANDLE_SEVERAL_CUSTOMERS", ", ".join(holders))
            )
        for account_id in sorted({r["account_id"] for r in rows}):
            ids = sorted({r["customer_id"] for r in store.accounts_by_id.get(account_id, [])})
            flags.extend(joint_flags(store, account_id, ids))
        candidates = holders
    elif alert["email"].strip():
        link = f"email {alert['email']}"
        candidates = list(store.customers_by_email.get(alert["email"].strip().lower(), []))
        path.append(f"{link} -> customers -> {', '.join(candidates) or 'nothing'}")
    if not link:
        link = "search by name, date of birth, country and city"
        candidates = search_customers(store, alert)
        path.append(f"{link} -> {', '.join(candidates) or 'no fitting record'}")
    elif len(candidates) > 1:
        fitting = [rid for rid in candidates if fits_screened(store, rid, alert)]
        if fitting:
            path.append(f"screened details fit {', '.join(fitting)}")
            candidates = fitting
    if not candidates:
        flags.append(Flag("3 Accounts", "high", "NO_CUSTOMER_FOUND", link))
    groups = group_people(store, candidates)
    persons = [build_person(store, g, alert) for g in groups]
    for person in persons:
        if len(person.record_ids) > 1:
            path.append("one person: " + ", ".join(person.record_ids))
    if len(persons) > 1:
        flags.append(
            Flag(
                "6 Customers",
                "high",
                "SEVERAL_PEOPLE_FIT",
                " | ".join(", ".join(p.record_ids) for p in persons),
            )
        )
    return Resolution(link=link, candidates=candidates, persons=persons, flags=flags, path=path)


__all__ = ["Flag", "Person", "Resolution", "resolve"]

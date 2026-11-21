"""Indexes over the bank's tables, built once per run."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from same_person.catalog import Table
from same_person.load import load_table, load_text
from same_person.screener.countries import countries_in_text
from same_person.screener.dates import PartialDate, find_dates, parse_date
from same_person.screener.hitfacts import search_query
from same_person.screener.names import NameRules, name_parts

NAME_IN_NOTE = re.compile(r"\b([A-Z][A-Z'\-]+(?:\s+[A-Z][A-Z'\-]+)+)\b")
NOT_NAME_WORDS = {"DOB", "ID", "KYC", "UK", "US", "USA"}


@dataclass(slots=True)
class Check:
    """An identity check: a KYC provider check or a KYC note."""

    source_id: str
    kind: str  # "kyc" or "note"
    approved: bool
    result: str
    checked: PartialDate | None
    name: str
    dob: PartialDate | None
    dob_raw: str
    country: str
    document_number: str = ""
    provider: str = ""


def _rows(frame) -> list[dict[str, str]]:
    return frame.to_dict("records")


def parse_kyc_note(note: dict[str, str]) -> Check | None:
    text = note["text"]
    created = parse_date(note["created_at"])
    name = ""
    for match in NAME_IN_NOTE.finditer(text):
        words = [w.strip("'-") for w in match.group(1).split()]
        words = [w for w in words if w not in NOT_NAME_WORDS]
        if len(words) >= 1:
            name = " ".join(words)
            break
    dob = None
    dob_raw = ""
    for start, end, value in find_dates(text):
        before = text[max(0, start - 4) : start].lower()
        if before.endswith("on ") or (created is not None and value == created):
            continue
        dob, dob_raw = value, text[start:end]
        break
    countries = sorted(countries_in_text(text))
    if not name and dob is None:
        return None
    return Check(
        source_id=note["note_id"],
        kind="note",
        approved=True,
        result="approved",
        checked=created,
        name=name,
        dob=dob,
        dob_raw=dob_raw,
        country=countries[0] if len(countries) == 1 else "; ".join(countries),
        provider="onboarding note",
    )


@dataclass(slots=True)
class Store:
    root: Path | None = None
    rules: NameRules = field(default_factory=NameRules)
    alerts: list[dict[str, str]] = field(default_factory=list)
    hits: list[dict[str, str]] = field(default_factory=list)
    labels: dict[str, dict[str, str]] = field(default_factory=dict)
    customers: dict[str, dict[str, str]] = field(default_factory=dict)
    dob: dict[str, PartialDate | None] = field(default_factory=dict)
    accounts_by_id: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    accounts_by_login: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    accounts_by_customer: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    accounts_by_phone: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    applicants_by_customer: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    customers_by_applicant: dict[str, set[str]] = field(default_factory=dict)
    checks_by_applicant: dict[str, list[Check]] = field(default_factory=dict)
    applicants_by_document: dict[str, set[str]] = field(default_factory=dict)
    customers_by_email: dict[str, list[str]] = field(default_factory=dict)
    customers_by_phone_dob: dict[tuple[str, PartialDate], set[str]] = field(default_factory=dict)
    customers_by_part: dict[str, set[str]] = field(default_factory=dict)
    customers_by_dob: dict[PartialDate, set[str]] = field(default_factory=dict)
    kyc_notes_by_email: dict[str, list[Check]] = field(default_factory=dict)
    lived_by_email: dict[str, list[tuple[str, set[str]]]] = field(default_factory=dict)
    search_index: dict[str, list[dict[str, str]]] = field(default_factory=dict)

    @classmethod
    def load(cls, root: Path | None = None) -> Store:
        store = cls(root=root)
        store.rules = NameRules.from_policy(load_text(Table.POLICY, root=root))
        store.alerts = _rows(load_table(Table.ALERTS, root=root))
        store.hits = _rows(load_table(Table.HITS, root=root))
        try:
            labels = _rows(load_table(Table.PRACTICE_LABELS, root=root))
        except FileNotFoundError:
            labels = []
        store.labels = {row["hit_id"]: row for row in labels}
        store._index_customers(_rows(load_table(Table.CUSTOMERS, root=root)))
        store._index_accounts(_rows(load_table(Table.ACCOUNTS, root=root)))
        store._index_onboarding(_rows(load_table(Table.ONBOARDING, root=root)))
        store._index_kyc(_rows(load_table(Table.KYC_CHECKS, root=root)))
        store._index_notes(_rows(load_table(Table.NOTES, root=root)))
        index: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in _rows(load_table(Table.SEARCH_RESULTS, root=root)):
            index[search_query(row["query"])].append(row)
        store.search_index = dict(index)
        return store

    def _index_customers(self, rows: list[dict[str, str]]) -> None:
        by_email: dict[str, list[str]] = defaultdict(list)
        by_phone_dob: dict[tuple[str, PartialDate], set[str]] = defaultdict(set)
        by_part: dict[str, set[str]] = defaultdict(set)
        by_dob: dict[PartialDate, set[str]] = defaultdict(set)
        for row in rows:
            rid = row["record_id"]
            self.customers[rid] = row
            dob = parse_date(row["dob"])
            self.dob[rid] = dob
            if row["email"]:
                by_email[row["email"].strip().lower()].append(rid)
            if dob is not None:
                by_dob[dob].add(rid)
                if dob.full and row["phone"]:
                    by_phone_dob[(_phone(row["phone"]), dob)].add(rid)
            company = row["entity_type"].lower() == "company"
            for name in (row["full_name"], row["preferred_name"], row["login_name"]):
                for part in name_parts(name, self.rules, company=company):
                    by_part[part].add(rid)
        self.customers_by_email = dict(by_email)
        self.customers_by_phone_dob = dict(by_phone_dob)
        self.customers_by_part = dict(by_part)
        self.customers_by_dob = dict(by_dob)

    def _index_accounts(self, rows: list[dict[str, str]]) -> None:
        by_id: dict[str, list[dict[str, str]]] = defaultdict(list)
        by_login: dict[str, list[dict[str, str]]] = defaultdict(list)
        by_customer: dict[str, list[dict[str, str]]] = defaultdict(list)
        by_phone: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in rows:
            by_id[row["account_id"]].append(row)
            if row["login_handle"]:
                by_login[row["login_handle"].strip().lower()].append(row)
            by_customer[row["customer_id"]].append(row)
            if row["phone"]:
                by_phone[_phone(row["phone"])].append(row)
        self.accounts_by_id = dict(by_id)
        self.accounts_by_login = dict(by_login)
        self.accounts_by_customer = dict(by_customer)
        self.accounts_by_phone = dict(by_phone)

    def _index_onboarding(self, rows: list[dict[str, str]]) -> None:
        by_customer: dict[str, list[tuple[str, str]]] = defaultdict(list)
        by_applicant: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            if not row["kyc_applicant_id"]:
                continue
            by_customer[row["customer_id"]].append((row["account_id"], row["kyc_applicant_id"]))
            by_applicant[row["kyc_applicant_id"]].add(row["customer_id"])
        self.applicants_by_customer = dict(by_customer)
        self.customers_by_applicant = dict(by_applicant)

    def _index_kyc(self, rows: list[dict[str, str]]) -> None:
        by_applicant: dict[str, list[Check]] = defaultdict(list)
        by_document: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            approved = row["result"].strip().lower() == "approved"
            check = Check(
                source_id=row["check_id"],
                kind="kyc",
                approved=approved,
                result=row["result"],
                checked=parse_date(row["checked_at"]),
                name=row["verified_name"],
                dob=parse_date(row["verified_dob"]),
                dob_raw=row["verified_dob"],
                country=row["document_country"],
                document_number=row["document_number"].strip(),
                provider=row["provider"],
            )
            by_applicant[row["applicant_id"]].append(check)
            if approved and check.document_number:
                by_document[check.document_number.upper()].add(row["applicant_id"])
        self.checks_by_applicant = dict(by_applicant)
        self.applicants_by_document = dict(by_document)

    def _index_notes(self, rows: list[dict[str, str]]) -> None:
        kyc: dict[str, list[Check]] = defaultdict(list)
        lived: dict[str, list[tuple[str, set[str]]]] = defaultdict(list)
        for row in rows:
            email = row["requester_email"].strip().lower()
            channel = row["channel"].strip().lower()
            if channel == "kyc":
                check = parse_kyc_note(row)
                if check is not None:
                    kyc[email].append(check)
            elif re.search(r"resid|lived|living|home|based|location", row["text"], re.I):
                found = countries_in_text(row["text"])
                if found:
                    lived[email].append((row["note_id"], found))
        self.kyc_notes_by_email = dict(kyc)
        self.lived_by_email = dict(lived)


def _phone(value: str) -> str:
    return re.sub(r"\D", "", value)

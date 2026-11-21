"""Unit tests for the screener rules and a regression run on the practice labels."""

from __future__ import annotations

import pytest

from same_person.screener.countries import canonical, countries_in_text
from same_person.screener.dates import PartialDate, dob_agrees, dob_contradicts, parse_date
from same_person.screener.hitfacts import dobs_from_profile, earliest_report
from same_person.screener.names import NameRules, names_agree

RULES = NameRules(
    groups={"muhammad": {0}, "mohammed": {0}, "joseph": {1}, "joe": {1}},
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1968-03-05", PartialDate(1968, 3, 5)),
        ("13/07/1969", PartialDate(1969, 7, 13)),
        ("March 15, 2005", PartialDate(2005, 3, 15)),
        ("6 August 1959", PartialDate(1959, 8, 6)),
        ("1996", PartialDate(1996)),
        ("2022-03", PartialDate(2022, 3)),
        ("", None),
    ],
)
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


def test_dob_rules_follow_the_sop():
    customer = PartialDate(1979, 11, 8)
    assert dob_contradicts(customer, PartialDate(1977))
    assert not dob_contradicts(customer, PartialDate(1978))
    assert dob_agrees(customer, PartialDate(1979))
    assert dob_contradicts(customer, PartialDate(1979, 4))
    assert not dob_agrees(customer, PartialDate(1979, 4))


@pytest.mark.parametrize(
    ("a", "b", "agree"),
    [
        ("Mohammed Ali Khan", "Muhammad Khan", True),
        ("Abdulsalam Bello", "Abdul Salam Bello", True),
        ("CHINEDU AZEEZ", "Ridwan, Azeez", False),
        ("Jonathan Reyes Whitfield", "John Whitfield", False),
        ("Stefanovic Milan", "Stefanović, Milan", True),
        ("Joe Smith", "Joseph Smyth", True),
    ],
)
def test_names(a, b, agree):
    assert names_agree(a, b, RULES) is agree


def test_company_legal_forms_are_not_parts():
    assert names_agree(
        "Pullman Global Solutions LLC", "PULLMAN GLOBAL SOLUTIONS", RULES, company=True
    )


def test_countries():
    assert canonical("GB") == "United Kingdom"
    assert canonical("Nigerian") == "Nigeria"
    assert canonical("International") is None
    assert "Brazil" in countries_in_text("Brazilian authorities published a notice")


def test_profile_ages():
    assert dobs_from_profile("Elvira Velasco Morillo, aged 30, in November 1998.") == [
        PartialDate(1967),
        PartialDate(1968),
    ]
    assert dobs_from_profile("When questioned in January 2023, he was 46.") == [
        PartialDate(1976),
        PartialDate(1977),
    ]
    assert dobs_from_profile("Christian Zaum was born on 30 June 1969.") == [
        PartialDate(1969, 6, 30)
    ]


def test_earliest_report():
    notes = "According to SEC; July 10, 2006: text\n\nAccording to SEC;  April 19, 2001: more"
    assert earliest_report(notes) == PartialDate(2001, 4, 19)


@pytest.mark.slow
def test_practice_labels_are_never_wrongly_cleared():
    from same_person.screener.pipeline import run_screener, score_against_labels
    from same_person.screener.store import Store

    store = Store.load()
    results = run_screener(store, hit_ids=set(store.labels))
    score = score_against_labels(results)
    assert score["wrongly_cleared"] == []
    assert score["accuracy"] >= 0.9

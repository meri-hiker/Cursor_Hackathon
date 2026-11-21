"""Uploaded data in other shapes is normalised to the catalog layout."""

from __future__ import annotations

import json
import zipfile

import pandas as pd
import pytest

from same_person.catalog import Table
from same_person.errors import SamePersonError
from same_person.load import load_table
from same_person.screener.ingest import ingest, table_for


def test_table_names_in_any_style():
    assert table_for("KYC checks") is Table.KYC_CHECKS
    assert table_for("kyc-checks") is Table.KYC_CHECKS
    assert table_for("searchResults") is Table.SEARCH_RESULTS
    assert table_for("Alerts_clean") is Table.ALERTS
    assert table_for("random") is None


def test_nested_jsonl_with_renamed_headers(tmp_path):
    upload = tmp_path / "upload"
    upload.mkdir()
    alerts = [
        {
            "Alert ID": "A-1",
            "Full Name": "Jane Doe",
            "Date of Birth": "1980-01-02",
            "hits": [
                {"hit_id": "H-1", "name": "Doe, Jane", "dobs": ["1980", "1981"]},
                {"hit_id": "H-2", "name": "John Roe", "match_score": 91.0},
            ],
        }
    ]
    (upload / "alerts.jsonl").write_text("\n".join(json.dumps(a) for a in alerts), encoding="utf-8")
    root, report = ingest(upload, tmp_path / "out")
    a = load_table(Table.ALERTS, root=root)
    h = load_table(Table.HITS, root=root)
    assert a.loc[0, "customer_name"] == "Jane Doe"
    assert a.loc[0, "dob"] == "1980-01-02"
    assert a.loc[0, "hit_ids"] == "H-1; H-2"
    assert list(h["alert_id"]) == ["A-1", "A-1"]
    assert h.loc[0, "dobs"] == "1980; 1981"
    assert h.loc[1, "match_score"] == "91"
    assert any("nested" in c for c in report.changes)
    # Optional tables are present and empty, so the loader still accepts them.
    assert load_table(Table.CUSTOMERS, root=root).empty


def test_zip_of_semicolon_csvs(tmp_path):
    archive = tmp_path / "upload.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("data/Alerts.csv", "alert_id;customer_name\nA-1;Jane Doe\n")
        z.writestr("data/Hits.csv", "hit_id;alert_id;name\nH-1;A-1;Jane Doe\n")
    root, _ = ingest(archive, tmp_path / "out")
    assert load_table(Table.HITS, root=root).loc[0, "name"] == "Jane Doe"
    # hit_ids are rebuilt from the hits when the alerts do not list them.
    assert load_table(Table.ALERTS, root=root).loc[0, "hit_ids"] == "H-1"


def test_workbook_with_other_sheet_names(tmp_path):
    book = tmp_path / "upload.xlsx"
    with pd.ExcelWriter(book) as writer:
        pd.DataFrame({"alert_id": ["A-1"], "name": ["Jane"]}).to_excel(
            writer, sheet_name="alerts", index=False
        )
        pd.DataFrame({"Hit ID": ["H-1"], "Alert": ["A-1"], "Full Name": ["Jane"]}).to_excel(
            writer, sheet_name="Hits", index=False
        )
    root, _ = ingest(book, tmp_path / "out")
    hits = load_table(Table.HITS, root=root)
    assert (hits.loc[0, "hit_id"], hits.loc[0, "alert_id"], hits.loc[0, "name"]) == (
        "H-1",
        "A-1",
        "Jane",
    )


def test_missing_required_table_is_a_clear_error(tmp_path):
    (tmp_path / "Alerts.csv").write_text("alert_id,customer_name\nA-1,Jane\n", encoding="utf-8")
    with pytest.raises(SamePersonError, match="hits"):
        ingest(tmp_path, tmp_path / "out")

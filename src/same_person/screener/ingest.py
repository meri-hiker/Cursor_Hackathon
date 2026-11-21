"""Bring screening data from anywhere into the layout the screener reads.

The screener reads one CSV per sheet in ``<root>/data`` with the exact headers in
``same_person.catalog``. Uploaded data rarely looks like that. This module accepts:

- a folder (searched recursively) of ``.csv``, ``.tsv``, ``.txt``, ``.json``,
  ``.jsonl``/``.ndjson`` or ``.xlsx``/``.xls`` files, named after the sheets in any
  style (``Alerts.csv``, ``alerts.jsonl``, ``KYC checks.xlsx``, ``kyc-checks.json``);
- one workbook with a sheet per table;
- a ``.zip`` of any of the above;
- JSON Lines alerts with their hits nested inside each alert.

It then fixes what it can and says what it changed: header case, spacing and common
synonyms (``date_of_birth`` for ``dob``, the ``score_matches.py`` clean headings, and
so on), lists in cells, numbers read as floats, timestamps, text encodings, and
optional tables or columns that are missing. Required tables (Alerts, Hits) and the
columns that join tables must be present; anything else missing is left blank.

Run on its own to check an upload::

    python -m same_person.screener.ingest PATH [--out FOLDER]

or let the screener do it::

    python -m same_person.screener --input PATH
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from same_person.catalog import CATALOG, Table, TableSpec
from same_person.errors import SamePersonError
from same_person.paths import data_dir

TABULAR = {".csv", ".tsv", ".txt", ".json", ".jsonl", ".ndjson", ".xlsx", ".xlsm", ".xls"}
REQUIRED_TABLES = {Table.ALERTS, Table.HITS}

# Columns the joins depend on. Without them a table cannot be used.
REQUIRED_COLUMNS: dict[Table, tuple[str, ...]] = {
    Table.ALERTS: ("alert_id", "customer_name"),
    Table.HITS: ("hit_id", "alert_id", "name"),
    Table.CUSTOMERS: ("record_id",),
    Table.ACCOUNTS: ("account_id", "customer_id"),
    Table.ONBOARDING: ("account_id", "customer_id", "kyc_applicant_id"),
    Table.KYC_CHECKS: ("applicant_id",),
    Table.NOTES: ("requester_email", "text"),
    Table.SEARCH_RESULTS: ("query", "snippet"),
    Table.PRACTICE_LABELS: ("hit_id", "decision"),
}

# Other names a column goes by, after normalising (lowercase, words joined by "_").
SYNONYMS: dict[Table, dict[str, tuple[str, ...]]] = {
    Table.ALERTS: {
        "alert_id": ("alert", "alertid", "id"),
        "screened_at": ("screening_date", "screened_on", "screened", "date_screened"),
        "customer_name": ("full_name", "name", "screened_name", "customer"),
        "entity_type": ("type", "entity", "customer_type"),
        "dob": ("date_of_birth", "birth_date", "birthdate", "dateofbirth"),
        "country": ("residence_country", "country_of_residence"),
        "account_ref": (
            "internal_account_reference",
            "account_reference",
            "account_id",
            "account",
        ),
        "login_handle": ("login", "username", "user_name", "handle", "login_name"),
        "email": ("email_address", "e_mail"),
        "hit_count": ("reported_hit_count", "hits_count", "number_of_hits", "n_hits"),
        "hit_ids": ("reported_hit_ids", "hit_id_list"),
    },
    Table.HITS: {
        "hit_id": ("hit", "hitid", "id"),
        "alert_id": ("alert", "alertid"),
        "match_score": ("original_match_score", "score"),
        "entity_type": ("type", "entity"),
        "name": ("full_name", "hit_name", "primary_name"),
        "aliases": ("alias", "aka", "other_names"),
        "dobs": ("dates_of_birth", "date_of_birth", "dob", "birth_dates"),
        "countries": ("associated_countries", "country"),
        "addresses": ("address",),
        "categories": ("category",),
        "profile_notes": ("notes", "profile", "profile_text"),
        "sources": ("source_details",),
        "source_urls": ("urls", "source_url"),
    },
    Table.CUSTOMERS: {
        "record_id": ("customer_id", "id", "record"),
        "full_name": ("name", "customer_name"),
        "dob": ("date_of_birth", "birth_date"),
        "residence_country": ("country", "country_of_residence"),
        "email": ("email_address",),
        "phone": ("phone_number", "mobile"),
    },
    Table.ACCOUNTS: {
        "account_id": ("account", "account_ref", "account_number"),
        "customer_id": ("record_id", "customer"),
        "login_handle": ("login", "username", "handle"),
        "phone": ("phone_number", "mobile"),
        "opened_at": ("opened", "open_date", "opened_on", "date_opened"),
    },
    Table.ONBOARDING: {
        "customer_id": ("record_id",),
        "kyc_applicant_id": ("applicant_id", "kyc_applicant", "applicant"),
    },
    Table.KYC_CHECKS: {
        "check_id": ("id", "check"),
        "applicant_id": ("kyc_applicant_id", "applicant"),
        "verified_dob": ("dob", "date_of_birth"),
        "verified_name": ("name", "full_name"),
        "checked_at": ("checked", "check_date", "date"),
        "document_country": ("country", "issuing_country"),
    },
    Table.NOTES: {
        "note_id": ("id",),
        "requester_email": ("email", "customer_email"),
        "created_at": ("created", "date"),
        "text": ("note", "body", "content"),
    },
    Table.SEARCH_RESULTS: {"result_id": ("id",)},
    Table.PRACTICE_LABELS: {"decision": ("label",), "reason_codes": ("reasons", "reason_code")},
}

# How each sheet may be named, after normalising.
TABLE_NAMES: dict[Table, tuple[str, ...]] = {
    Table.START_HERE: ("start_here", "readme", "start"),
    Table.ALERTS: ("alerts", "alert", "alerts_clean"),
    Table.HITS: ("hits", "hit", "hits_scored"),
    Table.CUSTOMERS: ("customers", "customer", "system_of_record"),
    Table.ACCOUNTS: ("accounts", "account"),
    Table.ONBOARDING: ("onboarding",),
    Table.KYC_CHECKS: ("kyc_checks", "kyc", "kyc_check", "identity_checks"),
    Table.NOTES: ("notes", "note", "support_notes"),
    Table.SEARCH_RESULTS: ("search_results", "search", "web_search"),
    Table.PRACTICE_LABELS: ("practice_labels", "labels", "training_labels", "answers"),
    Table.POLICY: ("policy", "sop", "name_variants"),
}
PROSE = (Table.POLICY, Table.START_HERE)


@dataclass
class IngestReport:
    """What was found, where it came from, and what was changed."""

    sources: dict[str, str] = field(default_factory=dict)
    rows: dict[str, int] = field(default_factory=dict)
    changes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = ["Tables:"]
        for spec in CATALOG:
            source = self.sources.get(spec.filename, "missing, left empty")
            count = self.rows.get(spec.filename)
            shown = "" if count is None else f"{count:>7} rows"
            lines.append(f"  {spec.filename:<22} {shown:<12} {source}")
        if self.changes:
            lines.append("Changes:")
            lines += [f"  - {c}" for c in self.changes]
        if self.warnings:
            lines.append("Warnings:")
            lines += [f"  ! {w}" for w in self.warnings]
        return "\n".join(lines)


def norm_key(text: object) -> str:
    """``"KYC checks.csv"`` and ``"kycChecks"`` both become ``kyc_checks``."""
    text = re.sub(r"([a-z])([A-Z])", r"\1_\2", str(text).strip())
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def table_for(name: str) -> Table | None:
    key = norm_key(name)
    key = re.sub(r"^(screening_dev_set_|dev_|test_|unseen_|browse_)", "", key)
    for table, names in TABLE_NAMES.items():
        if key in names or key == table.value:
            return table
    return None


# ---------------------------------------------------------------- reading files


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _read_delimited(path: Path) -> pd.DataFrame:
    text = _read_text(path)
    try:
        sep = csv.Sniffer().sniff(text[:20000], delimiters=",;\t|").delimiter
    except csv.Error:
        sep = "\t" if path.suffix.lower() == ".tsv" else ","
    return pd.read_csv(io.StringIO(text), sep=sep, dtype=str, keep_default_na=False)


def _read_json(path: Path) -> list | dict:
    text = _read_text(path).strip()
    if not text:
        return []
    lines = path.suffix.lower() in (".jsonl", ".ndjson") or (text[0] == "{" and "\n{" in text)
    if not lines:
        return json.loads(text)
    records = []
    for number, line in enumerate(text.splitlines(), 1):
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SamePersonError(
                    f"{path.name} line {number} is not valid JSON: {exc}"
                ) from exc
    return records


def _records_frame(records: list) -> pd.DataFrame:
    return pd.DataFrame([r if isinstance(r, dict) else {"value": r} for r in records])


def _frames_from_json(data: list | dict, origin: str) -> dict[str, pd.DataFrame]:
    """A list of records is one table; an object of lists is several tables by key."""
    if isinstance(data, dict):
        lists = {k: v for k, v in data.items() if isinstance(v, list)}
        if lists and len(lists) == len(data):
            return {k: _records_frame(v) for k, v in lists.items()}
        for key in ("data", "records", "rows", "items", "results"):
            if isinstance(data.get(key), list):
                return {origin: _records_frame(data[key])}
        return {origin: _records_frame([data])}
    return {origin: _records_frame(data)}


def read_any(path: Path) -> dict[str, pd.DataFrame]:
    """Every table in one file, keyed by sheet or file name."""
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm", ".xls"):
        book = pd.ExcelFile(path)
        frames = {}
        for sheet in book.sheet_names:
            header = None if table_for(sheet) in PROSE else 0
            frames[sheet] = pd.read_excel(book, sheet_name=sheet, dtype=str, header=header)
        return frames
    if suffix in (".json", ".jsonl", ".ndjson"):
        return _frames_from_json(_read_json(path), path.stem)
    if table_for(path.stem) in PROSE:
        rows = list(csv.reader(io.StringIO(_read_text(path))))
        return {path.stem: pd.DataFrame({0: [row[0] if row else "" for row in rows]})}
    return {path.stem: _read_delimited(path)}


def collect(source: Path, workdir: Path) -> list[Path]:
    """Files to read: the source itself, a folder's files, or a zip's contents."""
    source = Path(source).expanduser()
    if not source.exists():
        raise FileNotFoundError(f"Nothing at {source}")
    if source.is_file() and source.suffix.lower() == ".zip":
        target = workdir / "unzipped"
        with zipfile.ZipFile(source) as archive:
            for member in archive.infolist():
                name = Path(member.filename)
                if member.is_dir() or name.is_absolute() or ".." in name.parts:
                    continue
                if name.parts[0] == "__MACOSX" or name.name.startswith("."):
                    continue
                out = target / name
                out.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as src, out.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
        return collect(target, workdir)
    if source.is_file():
        return [source]
    out: list[Path] = []
    for path in sorted(source.rglob("*")):
        if not path.is_file() or path.name.startswith(("~$", ".")):
            continue
        if path.suffix.lower() == ".zip":
            out += collect(path, workdir / path.stem)
        elif path.suffix.lower() in TABULAR:
            out.append(path)
    return out


# ---------------------------------------------------------------- cleaning values


def _cell(value: object) -> str:
    """One cell as the screener expects it: a string, with lists joined by '; '."""
    if value is None:
        return ""
    if isinstance(value, float):
        if value != value:  # NaN
            return ""
        return str(int(value)) if value.is_integer() else str(value)
    if isinstance(value, (list, tuple, set)):
        return "; ".join(text for text in (_cell(v) for v in value) if text)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d") if value == value.normalize() else value.isoformat()
    text = str(value).strip()
    if text.lower() in ("nan", "nat", "none", "null"):
        return ""
    return re.sub(r"^(\d{4}-\d{2}-\d{2})[ T]00:00:00$", r"\1", text)


def conform(
    frame: pd.DataFrame, spec: TableSpec, origin: str, report: IngestReport
) -> pd.DataFrame:
    """Rename, fill and order the columns to match the catalog."""
    wanted = list(spec.columns or ())
    by_key: dict[str, object] = {}
    for column in frame.columns:
        by_key.setdefault(norm_key(column), column)
    synonyms = SYNONYMS.get(spec.table, {})
    renames: dict[object, str] = {}
    # Exact names first, so a synonym never steals a column that has its own target.
    for target in wanted:
        column = by_key.get(target)
        if column is not None:
            renames[column] = target
    for target in wanted:
        if target in renames.values():
            continue
        for key in synonyms.get(target, ()):
            column = by_key.get(norm_key(key))
            if column is not None and column not in renames:
                renames[column] = target
                break
    changed = [f"{src} -> {dst}" for src, dst in renames.items() if src != dst]
    if changed:
        report.changes.append(f"{spec.filename}: renamed " + ", ".join(changed))
    out = pd.DataFrame({dst: frame[src].map(_cell) for src, dst in renames.items()})
    missing = [c for c in wanted if c not in out.columns]
    required = [c for c in REQUIRED_COLUMNS.get(spec.table, ()) if c in missing]
    if required:
        raise SamePersonError(
            f"{origin}: {spec.sheet} needs column(s) {required}. Found {list(frame.columns)}."
        )
    for column in missing:
        out[column] = ""
    if missing:
        report.changes.append(f"{spec.filename}: added blank {', '.join(missing)}")
    extra = [str(c) for c in frame.columns if c not in renames]
    if extra:
        report.changes.append(f"{spec.filename}: ignored {', '.join(extra)}")
    return out[wanted].fillna("")


def explode_nested_hits(alerts: pd.DataFrame, report: IngestReport) -> pd.DataFrame | None:
    """Alerts that carry their hits inside them (JSON) become a Hits table."""
    column = next(
        (c for c in alerts.columns if norm_key(c) in ("hits", "matches", "hit_list")), None
    )
    if column is None or not alerts[column].map(lambda v: isinstance(v, list)).any():
        return None
    id_column = next(
        (c for c in alerts.columns if norm_key(c) in ("alert_id", "alert", "id")), None
    )
    if id_column is None:
        raise SamePersonError("Alerts with nested hits need an alert_id.")
    rows: list[dict] = []
    ids: list[str] = []
    for alert_id, nested in zip(alerts[id_column], alerts[column], strict=True):
        hit_ids = []
        for hit in nested if isinstance(nested, list) else []:
            if not isinstance(hit, dict):
                continue
            row = dict(hit)
            row.setdefault("alert_id", alert_id)
            rows.append(row)
            hit_id = row.get("hit_id") or row.get("id")
            if hit_id:
                hit_ids.append(str(hit_id))
        ids.append("; ".join(hit_ids))
    alerts["hit_ids"] = ids
    alerts["hit_count"] = [str(len(x.split("; "))) if x else "0" for x in ids]
    del alerts[column]
    report.changes.append(f"Alerts: took {len(rows)} hits nested in the alerts")
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- the whole upload


def ingest(source: Path, out_root: Path | None = None) -> tuple[Path, IngestReport]:
    """Normalise ``source`` into ``<out_root>/data``. Returns ``out_root`` and a report."""
    report = IngestReport()
    out_root = Path(out_root) if out_root else Path(tempfile.mkdtemp(prefix="screener_input_"))
    out_dir = out_root / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="screener_unpack_"))
    try:
        found = _find_tables(Path(source), work, report)
        if Table.ALERTS in found and Table.HITS not in found:
            nested = explode_nested_hits(found[Table.ALERTS][0], report)
            if nested is not None:
                found[Table.HITS] = (nested, found[Table.ALERTS][1] + " (nested hits)")
        missing = sorted(t.value for t in REQUIRED_TABLES if t not in found)
        if missing:
            raise SamePersonError(
                f"No {' or '.join(missing)} table found in {source}. Name the files or "
                "sheets after the tables, for example Alerts.csv and Hits.csv."
            )
        for spec in CATALOG:
            target = out_dir / spec.filename
            if spec.table not in found:
                _write_missing(spec, target, report)
                continue
            frame, origin = found[spec.table]
            report.sources[spec.filename] = origin
            if spec.prose:
                values = [_cell(v) for v in frame.iloc[:, 0].tolist()]
                with target.open("w", newline="", encoding="utf-8") as handle:
                    csv.writer(handle).writerows([v] for v in values)
                report.rows[spec.filename] = len(values)
                continue
            clean = _dedupe_keys(conform(frame, spec, origin, report), spec, report)
            clean.to_csv(target, index=False, encoding="utf-8")
            report.rows[spec.filename] = len(clean)
        _check_links(out_dir, report)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out_root, report


def _find_tables(
    source: Path, work: Path, report: IngestReport
) -> dict[Table, tuple[pd.DataFrame, str]]:
    found: dict[Table, tuple[pd.DataFrame, str]] = {}
    for path in collect(source, work):
        try:
            frames = read_any(path)
        except Exception as exc:  # noqa: BLE001 - one bad file should not stop the rest
            report.warnings.append(f"Could not read {path.name}: {exc}")
            continue
        single = len(frames) == 1
        for name, frame in frames.items():
            table = table_for(name) or (table_for(path.stem) if single else None)
            if table is None:
                report.warnings.append(f"Skipped {path.name} [{name}]: not a known table")
                continue
            origin = path.name if single else f"{path.name} [{name}]"
            if table not in found:
                found[table] = (frame, origin)
                continue
            previous, where = found[table]
            same_shape = {norm_key(c) for c in previous.columns} == {
                norm_key(c) for c in frame.columns
            }
            if same_shape and table not in PROSE:
                found[table] = (
                    pd.concat([previous, frame], ignore_index=True),
                    f"{where} + {origin}",
                )
                report.changes.append(f"{table.value}: appended {origin} to {where}")
            else:
                report.warnings.append(f"Two {table.value} tables; kept {where}, not {origin}")
    return found


def _write_missing(spec: TableSpec, target: Path, report: IngestReport) -> None:
    if spec.table == Table.POLICY:
        bundled = data_dir() / spec.filename
        if bundled.is_file():
            shutil.copyfile(bundled, target)
            report.sources[spec.filename] = "bundled SOP and name variants (none uploaded)"
            return
    if spec.prose:
        target.write_text("", encoding="utf-8")
    else:
        pd.DataFrame(columns=list(spec.columns or ())).to_csv(target, index=False)
    if spec.table in (Table.CUSTOMERS, Table.ACCOUNTS, Table.ONBOARDING, Table.KYC_CHECKS):
        report.warnings.append(
            f"No {spec.sheet} table, so customers cannot be followed through it and more "
            "hits will be REVIEW"
        )


def _dedupe_keys(frame: pd.DataFrame, spec: TableSpec, report: IngestReport) -> pd.DataFrame:
    if not spec.key or frame.empty:
        return frame
    keyed = (frame[list(spec.key)] != "").all(axis=1)
    duplicated = frame.duplicated(subset=list(spec.key), keep="first") & keyed
    if duplicated.any():
        report.warnings.append(
            f"{spec.filename}: dropped {int(duplicated.sum())} rows with a repeated "
            f"{' + '.join(spec.key)}"
        )
        frame = frame[~duplicated]
    return frame


def _check_links(out_dir: Path, report: IngestReport) -> None:
    alerts = pd.read_csv(out_dir / "Alerts.csv", dtype=str, keep_default_na=False)
    hits = pd.read_csv(out_dir / "Hits.csv", dtype=str, keep_default_na=False)
    orphans = set(hits["alert_id"]) - set(alerts["alert_id"])
    if orphans:
        report.warnings.append(f"{len(orphans)} alert ids in Hits are not in Alerts")
    if len(hits) and (alerts["hit_ids"] == "").all():
        grouped = hits.groupby("alert_id")["hit_id"].apply("; ".join)
        alerts["hit_ids"] = alerts["alert_id"].map(grouped).fillna("")
        alerts["hit_count"] = alerts["hit_ids"].map(lambda x: str(len(x.split("; "))) if x else "0")
        alerts.to_csv(out_dir / "Alerts.csv", index=False, encoding="utf-8")
        report.changes.append("Alerts: filled hit_ids and hit_count from Hits")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check and normalise uploaded screening data.")
    parser.add_argument("source", type=Path, help="Folder, workbook, zip, CSV or JSON(L) file.")
    parser.add_argument("--out", type=Path, default=None, help="Writes <out>/data.")
    args = parser.parse_args(argv)
    try:
        root, report = ingest(args.source, args.out)
    except (SamePersonError, FileNotFoundError) as exc:
        print(f"Cannot use this input: {exc}", file=sys.stderr)
        return 1
    print(report.summary())
    print(f"Normalised data: {root / 'data'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

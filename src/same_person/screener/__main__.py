"""Run the screener.

    python -m same_person.screener [--input PATH] [--out FOLDER] [--strict-sop]

Without ``--input`` it reads the checkout's ``data/``. With it, the upload (a folder,
workbook, zip, CSV or JSON file) is normalised by ``same_person.screener.ingest`` first.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

from same_person.errors import SamePersonError
from same_person.paths import repo_root
from same_person.screener.dashboard import write_dashboard
from same_person.screener.ingest import ingest
from same_person.screener.pipeline import run_screener, score_against_labels
from same_person.screener.store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Screen every hit and build the dashboard.")
    parser.add_argument("--out", type=Path, default=None, help="Output folder.")
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Data uploaded from anywhere: a folder, .xlsx workbook, .zip, .csv or .json(l).",
    )
    parser.add_argument(
        "--strict-sop",
        action="store_true",
        help="Follow the SOP exactly; do not hold red-flagged clearances for review.",
    )
    args = parser.parse_args(argv)
    out = args.out or repo_root() / "outputs" / "screener"
    out.mkdir(parents=True, exist_ok=True)

    started = time.time()
    root = None
    if args.input is not None:
        try:
            root, report = ingest(args.input, out / "input")
        except (SamePersonError, FileNotFoundError) as exc:
            print(f"Cannot use {args.input}: {exc}")
            return 1
        print(report.summary())
        print(f"Normalised input written to {root / 'data'}")
    store = Store.load(root)
    print(f"Loaded data in {time.time() - started:.1f}s")
    results = run_screener(store, cautious=not args.strict_sop)
    print(f"Screened {len(results)} hits in {time.time() - started:.1f}s")

    if not results:
        print("No hits to screen.")
        return 1
    rows = [r.to_row() for r in results]
    with (out / "screener_results.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with (out / "decisions.jsonl").open("w", encoding="utf-8") as handle:
        for r in results:
            record = {
                "hit_id": r.hit_id,
                "alert_id": r.alert_id,
                "decision": r.decision,
                "reason_codes": r.reason_codes,
                "customer_record_id": r.customer_record_id or None,
                "evidence": r.evidence,
                "rating": r.rating,
                "red_flags": [str(f) for f in r.flags if f.severity != "info"],
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    score = score_against_labels(results)
    (out / "label_score.json").write_text(json.dumps(score, indent=2) + "\n", encoding="utf-8")
    write_dashboard(results, score, out / "dashboard.html")

    counts: dict[str, int] = {}
    for r in results:
        counts[r.decision] = counts.get(r.decision, 0) + 1
    print("Decisions:", counts)
    if score.get("labelled"):
        print(
            f"Practice labels: {score['correct']}/{score['labelled']} correct "
            f"({score['accuracy']:.0%}); wrongly cleared: {len(score['wrongly_cleared'])}"
        )
    print(f"Wrote {out / 'screener_results.csv'}")
    print(f"Wrote {out / 'decisions.jsonl'}")
    print(f"Dashboard: {out / 'dashboard.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

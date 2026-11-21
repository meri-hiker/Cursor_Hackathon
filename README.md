git checkout -b my-branch# Same Person or Not?

Group workspace for the name-screening challenge. Each alert is a customer who might match one or more watchlist profiles. For every hit, decide whether it is the same person or company.

This checkout holds the browsable development set as one CSV per workbook sheet. The challenge text says the JSON Lines files in `dev/` are the scoring format. Those files are not in this repo yet. `data/` is the copy we load and test against.

## Setup

The project environment is `.Hack_venv`, created with Python 3.14. It stays on your machine. Python 3.11 or newer is enough to run the package.

```bash
python3 -m venv .Hack_venv
source .Hack_venv/bin/activate
python -m pip install -e ".[dev]"
```

If the environment already exists, the install line is the one you need. `make install` does the same thing against `.Hack_venv`.

```bash
python -m same_person
```

That prints a row count for every file in `data/`.

```python
from same_person import Table, load_table, load_text

alerts = load_table(Table.ALERTS)
policy = load_text(Table.POLICY)
```

Cells are strings. An empty cell is `""`. The token `NA` stays `NA`, because the SOP treats it as a name placeholder rather than a missing value.

## Layout

```text
data/                 one CSV per workbook sheet
src/same_person/      installable package
  catalog.py          file names, columns, and keys
  load.py             CSV readers
  decisions.py        decision and reason-code vocabulary
  export.py           rebuild the CSVs from the .xlsx workbook
tests/                contract tests against data/ and small fixtures
.github/workflows/    lint and tests on pull requests
```

`src/same_person/catalog.py` is the contract. The loader rejects a file whose header has drifted, and CI runs that check.

## Data

| File | Rows | Key | What it is |
| --- | ---: | --- | --- |
| `Start_here.csv` | 27 | | How the sheets fit together |
| `Alerts.csv` | 440 | `alert_id` | Customer as screened, plus hit ids |
| `Hits.csv` | 819 | `hit_id` | Watchlist profile that matched |
| `Customers.csv` | 72,265 | `record_id` | System of record |
| `Accounts.csv` | 86,659 | `account_id`, `customer_id` | Logins and account numbers. Joint accounts share an account id |
| `Onboarding.csv` | 72,265 | `account_id`, `customer_id` | Account to KYC applicant |
| `KYC_checks.csv` | 54,488 | `check_id` | Identity checks, keyed by applicant |
| `Notes.csv` | 31,895 | `note_id` | Notes filed by customer email |
| `Search_results.csv` | 16,882 | `result_id` | Web results for a name. Most are namesakes |
| `Practice_labels.csv` | 50 | `hit_id` | Answered hits and the path to the answer |
| `Policy.csv` | 117 | | The identity SOP and the name-variant groups |

Joins, from the orientation sheet:

- `Alerts.hit_ids` → `Hits.hit_id`
- `Alerts.account_ref` → `Accounts.account_id` → `Accounts.customer_id` → `Customers.record_id`
- `Alerts.login_handle` → `Accounts.login_handle` → `Customers.record_id`
- `Alerts.email` → `Customers.email`
- `Accounts.account_id` + `customer_id` → `Onboarding` → `kyc_applicant_id` → `KYC_checks.applicant_id`
- `Customers.email` → `Notes.requester_email`
- Hit name → `Search_results.query` (the name's lowercase words in alphabetical order)

`Practice_labels.csv` is the only file with answers. An alert is cleared only when every hit on it is `DIFFERENT_ENTITY`.

To rebuild `data/` from the browse workbook:

```bash
python -m same_person.export "Screening Dev Set - browse.xlsx"
```

That overwrites `data/`. Pass `--out` to write somewhere else. The workbook's sheet names and headers have to match the catalog.

## Decisions

`Decision` is `SAME_ENTITY`, `DIFFERENT_ENTITY`, or `REVIEW`. `ReasonCode` lists the codes in the SOP, and each code belongs to one decision. `parse_reason_codes` splits the semicolon-separated cell used in the practice labels.

### Data from anywhere

`--input` runs the screener on data that did not come from this checkout:

```powershell
.Hack_venv\Scripts\python.exe -m same_person.screener --input "C:\path\to\upload"
```

The input can be a folder (searched recursively), an `.xlsx` workbook, a `.zip`, or single `.csv`, `.tsv`, `.json` or `.jsonl` files. Files and sheets are matched to tables by name in any style, so `Alerts.csv`, `alerts.jsonl` and `KYC checks` all work. `same_person/screener/ingest.py` normalises the input into `outputs/screener/input/data` and prints what it found and changed. It handles renamed headers (including the `score_matches.py` clean headings), delimiters, text encodings, lists in cells, hits nested inside JSON alerts, and missing optional tables or columns. Alerts and Hits are required. If no Policy is uploaded, the bundled SOP is used. To only check an upload, run `python -m same_person.screener.ingest PATH`.

## Development

```bash
make check
```

`make check` lints with Ruff and runs the tests. `make format` applies the formatter. The tests confirm headers, unique keys, and that every practice label uses a reason code allowed for its decision.

Do not commit `.Hack_venv`. The root `.gitignore` excludes it, along with caches and `.env` files.

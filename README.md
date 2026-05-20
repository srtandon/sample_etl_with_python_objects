# OOP Data Ingestion Framework

A portfolio-quality demonstration of how object-oriented design solves a real
data-engineering problem: upstream feeds that keep changing structure.

---

## The Problem

Real-world data feeds are unstable.  Vendors rename keys, add wrapper objects,
and reuse the same key name at different nesting depths.  A procedural ingestion
script handles this with an ever-growing `if/elif` ladder:

```python
# Procedural — breaks every time the vendor reshuffles the JSON
if "cohorts" in data:
    cohorts = data["cohorts"]
elif "trial" in data and "cohorts" in data["trial"]:
    cohorts = data["trial"]["cohorts"]
elif "trial" in data and "trial" in data["trial"]:
    cohorts = data["trial"]["trial"]["cohorts"]   # same key, three levels deep
# ... and it keeps growing
```

Each new feed version touches every downstream script.

This project shows how a small OOP framework isolates that chaos in one place —
versioned *adapters* — so that processors, jobs, and tests never change.

---

## Architecture

```
Raw JSON/YAML feed
       │
       ▼
┌─────────────────────────────┐
│     Adapter Registry        │  picks the right adapter automatically
│   TrialFeedV1Adapter        │  flat shape (original vendor format)
│   TrialFeedV2Adapter        │  nested + repeated key name (new format)
│   TrialFeedV3Adapter  ...   │  add more without touching downstream code
└────────────┬────────────────┘
             │ normalises to
             ▼
┌─────────────────────────────┐
│   Stable Domain Model       │  TrialRecord / CohortRecord / ScheduleRecord
│   (dataclasses, frozen)     │  processors depend only on these
└────────────┬────────────────┘
             │ passed to
             ▼
┌─────────────────────────────┐
│   Composable Processors     │  CohortProcessor, ScheduleProcessor
│                             │  one TrialRecord → result dict, no side effects
└────────────┬────────────────┘
             │ orchestrated by
             ▼
┌─────────────────────────────┐
│   ImportJob + PipelineResult│  runs processors, collects success/errors
└─────────────────────────────┘
```

**Key OOP patterns used**

| Pattern | Where | Benefit |
|---------|-------|---------|
| Strategy | `TrialFeedAdapter` ABC + V1/V2 subclasses | Swap feed shapes without touching processors |
| Registry | `adapters/registry.py` | Auto-select adapter; replaces `if/elif` |
| Composition | `ImportJob(processors=[...])` | Combine processors freely; no MRO puzzles |
| Immutable value objects | `CohortRecord`, `ScheduleRecord` (frozen dataclasses) | Safe to share, easy to test |

---

## The Portfolio Punchline

`fixtures/feed_v1.json` and `fixtures/feed_v2.json` contain the **same
clinical-trial data** in completely different structures.  V2 wraps everything
inside a top-level `"trial"` key — and then uses `"trial"` *again* as a nested
key for the study data block.

Run the test suite and see both adapters produce an identical `TrialRecord`:

```
pytest tests/test_adapters.py::test_both_adapters_produce_equal_trial_records -v
```

That is the proof: downstream code sees one stable object regardless of the feed
chaos upstream.

---

## Project Layout

```
sample_etl_with_python_objects/
├── src/ingestion/
│   ├── models.py              ← TrialRecord, CohortRecord, ScheduleRecord
│   ├── io.py                  ← pathlib-based YAML/JSON loading
│   ├── jobs.py                ← ImportJob, PipelineResult, Processor protocol
│   ├── flattener.py           ← JsonFlattener, SchemaResolver, FlatRecord
│   ├── adapters/
│   │   ├── base.py            ← TrialFeedAdapter ABC
│   │   ├── v1.py              ← flat feed shape
│   │   ├── v2.py              ← nested + repeated-key shape
│   │   └── registry.py        ← auto-selects the right adapter
│   ├── processors/
│   │   ├── cohort.py          ← CohortProcessor
│   │   └── schedule.py        ← ScheduleProcessor
│   └── sinks/
│       ├── duckdb_sink.py     ← DuckDBSink (fixed-schema TrialRecord → Parquet)
│       └── flat_sink.py       ← FlatDuckDBSink (dynamic-schema FlatRecord → Parquet)
├── fixtures/
│   ├── feed_v1.json           ← clinical trial, flat shape
│   ├── feed_v2.json           ← clinical trial, nested + repeated key
│   ├── sports_game.json       ← sports feed v1 (league wraps region)
│   └── sports_game_v2.json    ← sports feed v2 (region wraps league)
├── tests/
│   ├── test_adapters.py       ← clinical trial adapter equivalence tests
│   ├── test_processors.py     ← processor + ImportJob behaviour
│   ├── test_sinks.py          ← DuckDBSink round-trip + cross-file join
│   └── test_flattener.py      ← JsonFlattener + sports feed equivalence
├── scripts/
│   ├── run_ingestion.py       ← clinical trial CLI
│   ├── run_sports.py          ← sports flattener CLI
│   ├── run_queries.py         ← DuckDB SQL query runner
│   └── samplecode_dataingestion.py  ← original prototype (kept for reference)
├── configs/
│   └── sample_ingestion_config.yaml
├── data/
│   └── sample_ingestion_data.yaml
└── pyproject.toml
```

---

## Quickstart

```bash
# Install the package with dev and storage extras (once)
pip install -e ".[dev,storage]"

# Run the pipeline against the original flat feed (V1 adapter auto-selected)
python scripts/run_ingestion.py --feed fixtures/feed_v1.json --trial-id TrialB

# Run against the restructured feed (V2 adapter auto-selected automatically)
python scripts/run_ingestion.py --feed fixtures/feed_v2.json --trial-id TrialB

# Limit to specific cohorts
python scripts/run_ingestion.py --feed fixtures/feed_v2.json --trial-id TrialB --cohorts A1 A4

# Run tests
pytest
```

Expected output (both feeds):

```
Selected adapter: TrialFeedV1Adapter          ← or V2, depending on feed
Trial loaded: 'TrialB' (Hospital Trial 1), study=A229
Available cohorts: ['A1', 'A2', 'A3', 'A4']
[OK] TrialB
  CohortProcessor: {'A1': 150.0, 'A2': 300.0, 'A3': 225.0, 'A4': 75.0}
  ScheduleProcessor: {'cycle_length': 15, 'duration': 6, 'total_days': 90}
```

---

## The Original Problem: Dynamic Sports API Feeds

The clinical-trial adapters work well when a feed has a **small number of
known shapes**.  The original motivating problem was different: a sports API
where the same key — `score`, `players`, `fouls` — appeared under `home`,
`away`, and `periods.1.home`, all at different depths, and the nesting ORDER
of fields like `league` and `region` shifted between API versions.

This repo now demonstrates both cases.

### Case 1 — Unknown shape, recursive context tracking: `JsonFlattener`

`JsonFlattener` walks any nested JSON and builds composite keys by joining
the parent-key path with `_`.  The parent-key context (the thing procedural
parsers track manually with variables or recursion args) is encapsulated in
the recursive call state:

```python
flat = JsonFlattener().flatten({
    "home":    {"score": 108},
    "away":    {"score": 103},
    "periods": {"1": {"home": {"score": 28}, "away": {"score": 25}}}
})
# → {"home_score": 108, "away_score": 103,
#     "periods_1_home_score": 28, "periods_1_away_score": 25}
```

The key `score` appears 10 times in a 4-period game — every flattened key
is unique because the path prefix disambiguates them.

### Case 2 — Nesting order shifts: `SchemaResolver`

The two sports fixtures contain identical data but different structures:

- `fixtures/sports_game.json`: `league → {name, region, season}`
- `fixtures/sports_game_v2.json`: `region → {name, league → {name, season}}`

Raw flattened keys differ (`league_name` vs `region_league_name`).
`SchemaResolver` maps both to the same canonical name with one rule — no
new adapter class required:

```python
resolver = SchemaResolver({
    "league_name":        "league_name",   # v1 pass-through
    "region_league_name": "league_name",   # v2 → canonical
    "league_region":      "region_name",   # v1 → canonical
    "region_name":        "region_name",   # v2 pass-through
})
```

Run the sports pipeline:

```bash
python scripts/run_sports.py --feed fixtures/sports_game.json
python scripts/run_sports.py --feed fixtures/sports_game_v2.json
```

Both produce **identical 31-field output** and write to the same Parquet
directory so they can be queried together.

### When to use each approach

| Situation | Use |
|-----------|-----|
| Small number of known shapes with structural differences | `TrialFeedAdapter` hierarchy |
| Continuously shifting / unknown shape | `JsonFlattener` + `SchemaResolver` |
| Both — known shape with some dynamic sub-fields | Adapter that calls `JsonFlattener` on the dynamic part |

---

## DuckDB Persistence and SQL Queries

Running the pipeline with `--output-dir` persists every `TrialRecord` to
Parquet files using DuckDB as the in-process write engine.

```bash
# Ingest both fixture feeds into output/
python scripts/run_ingestion.py --feed fixtures/feed_v1.json --trial-id TrialB --output-dir output
python scripts/run_ingestion.py --feed fixtures/feed_v2.json --trial-id TrialC --output-dir output
```

Output layout:

```
output/
  cohorts/
    TrialB.parquet     ← one row per cohort
    TrialC.parquet
  schedules/
    TrialB.parquet     ← one row per trial
    TrialC.parquet
```

Run the sample queries against all files at once:

```bash
python scripts/run_queries.py
```

Sample output:

```
──────────────────────────────────────────────────────────────
  Schedule info joined to cohort summary
──────────────────────────────────────────────────────────────
 trial_id │        name │ total_days │ cohort_count │ total_patients │ avg_patients_per_cohort
──────────┼─────────────┼────────────┼──────────────┼────────────────┼────────────────────────
   TrialB │ Hospital... │         90 │            4 │             34 │                     8.5
   TrialC │ Home Tr...  │        360 │            5 │             39 │                     7.8
```

DuckDB can join across Parquet files from different trials, run window
functions to rank cohorts, and produce aggregates — all locally, no server.
The raw SQL is in `queries/sample_queries.sql`.

### Why this fits the OOP design

`DuckDBSink` satisfies the same `Processor` protocol as `CohortProcessor` and
`ScheduleProcessor` — it has a `run(trial: TrialRecord)` method.  Adding
persistence required zero changes to `ImportJob`:

```python
# Before (compute only)
job = ImportJob(trial=trial, processors=[CohortProcessor("all"), ScheduleProcessor()])

# After (compute + persist)
job = ImportJob(trial=trial, processors=[CohortProcessor("all"), ScheduleProcessor(), DuckDBSink("output")])
```

---

## How to Add a V3 Feed Shape

1. Create `src/ingestion/adapters/v3.py` implementing `TrialFeedAdapter`.
2. Add `TrialFeedV3Adapter()` to `_ADAPTERS` in `adapters/registry.py` (before
   less-specific adapters).
3. Add a fixture in `fixtures/feed_v3.json` and a test in
   `tests/test_adapters.py` asserting equivalence with V1.

No processor, job, or script code changes.

---

## Data Domains

The `Trial / Cohort / Schedule` vocabulary is from clinical research, but the
framework is domain-agnostic.  Rename the classes and swap the YAML files to
adapt it for:

- **Sports analytics** — League / Team / SeasonSchedule
- **Retail** — Store / ProductCategory / PromotionSchedule
- **Finance** — Portfolio / AssetClass / TradingSchedule

---

## Requirements

- Python 3.10+
- `pyyaml >= 6.0`
- `pytest >= 8.0` (dev extra only)
- `duckdb >= 0.9` (storage extra, required for `DuckDBSink` and `run_queries.py`)

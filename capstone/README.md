# Olist Analytics Platform — Capstone

An end-to-end batch pipeline that turns raw Brazilian e-commerce data into a
tested star-schema warehouse and business-ready Parquet marts, reproducible
with two commands.

> **The question this answers:** which categories and states drive revenue,
> how is it trending, and where are deliveries late?

---

## Quick start

```bash
cd capstone
cp .env.example .env
make setup      # build images, start Postgres, create schemas, load source
make run        # batch 1 -> batch 2 -> Spark marts
make test       # 23 assertions
make serve      # the five business answers
```

Or all of it in one go:

```bash
make demo
```

**Prerequisites:** Docker Desktop, and the Olist CSVs in `../data/olist/`
(mounted read-only into the container — see [Dataset](#dataset)).

Nothing else. Python, Java, Spark and the Postgres JDBC driver all live
inside the image.

---

## Architecture

```
  CSV files
      │  bootstrap.py — COPY, no type inference
      ▼
  ┌─────────┐   extract.py      ┌─────┐   transform.py   ┌─────────┐
  │ source  │ ───────────────►  │ raw │ ──────────────►  │ staging │
  │  OLTP   │   watermark on    │ as  │   clean, dedupe, │  typed  │
  │ 9 tables│   purchase ts     │ -is │   derive         │  keyed  │
  └─────────┘                   └─────┘                  └────┬────┘
                                                              │
                                             ╔════════════════▼═══════════╗
                                             ║  QUALITY GATE — staging    ║
                                             ║  8 checks, aborts the run  ║
                                             ╚════════════════╤═══════════╝
                                                              │ load.py
                                                              ▼
                                                      ┌───────────────┐
                                                      │   warehouse   │
                                                      │  star schema  │
                                                      │ SCD2 customer │
                                                      └───────┬───────┘
                                             ╔════════════════▼═══════════╗
                                             ║  QUALITY GATE — warehouse  ║
                                             ║  7 checks, aborts the run  ║
                                             ╚════════════════╤═══════════╝
                                                              │ build_marts.py (PySpark, JDBC)
                                                              ▼
                                               ┌──────────────────────────┐
                                               │  marts/  Parquet         │
                                               │  partitioned year/month  │
                                               └──────────┬───────────────┘
                                                          │ serve.py
                                                          ▼
                                                   5 business answers

  Every step writes a row to meta.pipeline_runs.
  meta.watermarks records how far the extract has got.
```

Two services: `postgres` (the warehouse) and `app` (Python + JDK + Spark).
Spark runs in local mode inside `app` — there is no cluster to stand up.

---

## Data model

### Grain

> **One row of `fact_order_items` is one item within one order.**

This single sentence governs everything. It is asserted on every run: the
fact row count must equal `staging.order_items`, and
`(order_id, order_item_id)` must be unique.

### Star schema

```
                    dim_date
                        │
  dim_customer ── fact_order_items ── dim_product
    (SCD2)          /          \
            dim_seller      dim_geography
```

| Table | Rows | Notes |
|---|---:|---|
| `fact_order_items` | 112,650 | Keys + measures only, no descriptive text |
| `dim_date` | 1,461 | 2016-2019, generated with `generate_series` |
| `dim_customer` | ~96,100 | **SCD Type 2** — `valid_from`, `valid_to`, `is_current` |
| `dim_product` | 32,951 | SCD Type 1 (overwrite) |
| `dim_seller` | 3,095 | SCD Type 1 |
| `dim_geography` | ~15,000 | One row per zip prefix |

**Facts:** `price` and `freight_value` are additive. `delivery_days`,
`days_vs_estimate` and `is_late` are **non-additive** — average them, never
sum them.

**Degenerate dimensions:** `order_id`, `order_item_id` and `order_status` sit
on the fact row. They identify things but have no attributes worth a table,
and `order_id` is needed so "how many orders" stays answerable at item grain.

### Why `dim_customer` is SCD Type 2

When a customer moves city, Type 1 would overwrite the address and
retroactively claim their 2017 order shipped to the new city. Type 2 closes
the old row and opens a new one, so the old order keeps pointing at the
address it actually shipped to.

It is also keyed on `customer_unique_id`, **not** `customer_id` —
`customer_id` is generated per *order*, so grouping by it reports a 0%
repeat-customer rate with no error.

---

## Requirements — where each is met

| Requirement | Where | Evidence |
|---|---|---|
| **Ingestion** — CSVs into Postgres, orders split into 2 batches | `pipeline/bootstrap.py` | 1,550,922 rows; batch 1 = 45,430, batch 2 = 54,011 at `BATCH_CUTOFF` |
| **Incremental ELT** — watermark picks up only batch 2 on run 2 | `pipeline/extract.py` | Run 1 extracts 45,430; run 2 extracts 54,011; run 3 extracts **0** |
| **Warehouse** — star schema, `dim_customer` SCD2 | `sql/ddl/04_warehouse.sql`, `pipeline/load.py` | 6 tables, SCD2 invariants asserted |
| **PySpark marts** — ≥3 Parquet outputs partitioned by month | `spark/build_marts.py` | 3 marts, 24/23/23 files under `year=/month=` |
| **Data quality** — ≥8 checks that fail the run | `pipeline/quality.py` | **15 checks** (8 staging + 7 warehouse) |
| **Idempotency** — running twice changes nothing | `tests/test_idempotency.py` | Counts and revenue identical across 3 runs |
| **Observability** — run id, step, times, rows, status | `meta.pipeline_runs` | `make logs` |
| **Reproducibility** — compose up + one command | `docker-compose.yml`, `Makefile` | `make demo` |
| **Serving** — 5 business questions | `pipeline/serve.py`, `sql/queries/business_questions.sql` | `make serve` |

### The three marts

| Mart | Grain | Columns |
|---|---|---|
| `mart_revenue_daily` | day × category × state | revenue, orders, items, **7-day rolling average** |
| `mart_delivery_performance` | month × state × seller | late %, avg delay, avg vs promise |
| `mart_customer_cohorts` | cohort month × month offset | cohort size, returning customers, retention % |

### The 15 quality checks

**Staging (8):** not empty · unique `order_id` · purchase timestamp present ·
money non-negative · no orphan items · state is 2 chars · zip numeric ·
**zip leading zeros preserved**

**Warehouse (7):** fact count matches staging · grain unique · no orphan
dimension keys · one current SCD2 row per customer · validity windows
ordered · prices non-negative · revenue plausible

A failure raises `StepFailed`, which aborts the run **before** the load step,
so the warehouse is never touched. The failure and its message are written to
`meta.pipeline_runs`.

---

## Verified results

```
source.orders              99,441      batch 1: 45,430   batch 2: 54,011
warehouse.fact_order_items 112,650     revenue: 13,591,643.70
warehouse.dim_customer     96,138
marts                      56,659 + 46,775 + 79 rows
tests                      23 passed
```

**Idempotency** — three consecutive runs:

```
run 2:  extract 0 rows   fact 112,650   dim_customer 96,138   revenue 13,591,643.70
run 3:  extract 0 rows   fact 112,650   dim_customer 96,138   revenue 13,591,643.70
run 4:  extract 0 rows   fact 112,650   dim_customer 96,138   revenue 13,591,643.70
```

**Business answers** (`make serve`): `health_beauty` leads at 9.30% of
revenue; São Paulo at 38.35%; November 2017 +52% (Black Friday); Alagoas
worst for late delivery at 24.12%; month-1 retention 0.45%.

---

## Design decisions and trade-offs

**ELT, not ETL.** Data lands first and is transformed in SQL inside Postgres.
Python only issues statements. Once you have decided to keep an untouched
`raw` copy, transforming before landing buys nothing, and the database engine
beats a single Python process.

**`raw` has no constraints; `staging` has keys.** Raw must accept whatever
arrived so malformed rows stay visible for debugging. A duplicate reaching
`staging` is a bug in the transform, not bad input — so that is where the
primary keys live.

**`meta` is a separate schema.** A reset wipes the data layers, but run
history survives, because it describes the pipeline rather than the business.

**Gates sit between layers, and abort.** A report saying "0.3% of rows look
wrong" gets ignored; a run that stops gets fixed. The demonstrated failure
mode is a negative price injected into staging: the run exits 1 and the
warehouse stays untouched.

**Money is `NUMERIC` in Postgres but `double` in the marts.** Exact decimal
arithmetic matters when summing 112k rows; but `NUMERIC` arrives in Spark as
`DecimalType`, lands in Parquet as `decimal128`, and surfaces in pandas as
objects of `Decimal` — which has no `.round()` and crashed the serving layer.
The marts cast to `double` on write. A mart is a presentation artifact; the
warehouse remains the exact copy.

**SCD2 change detection must be deterministic.** `staging.customers` is keyed
on `customer_id`, and one person can own several with different cities.
Collapsing to one row per person with `DISTINCT ON (customer_unique_id)` and
*no tiebreak* makes Postgres pick an arbitrary row — a different one each run
— so every run reports a false change and versions the row again, forever.
The fix is `ORDER BY customer_unique_id, customer_id`. **Any
non-deterministic row selection is an idempotency bug.**

**Broadcast joins for every dimension.** All five dimensions are small, so
they are broadcast to each executor instead of shuffled. Spark auto-broadcasts
under 10 MB anyway; the explicit hints document the intent.

**`repartition` before `partitionBy`.** Without it each in-memory partition
writes its own file per month — hundreds of tiny files instead of two dozen.

### Known limitations

- **Distinct counts are not additive.** `mart_revenue_daily.orders` is a
  per-group distinct count; summing it across categories counts an order once
  per category it contains. Revenue reconciles exactly with the warehouse
  (asserted in the tests); order counts do not. Use
  `sql/queries/business_questions.sql` for exact order counts.
- **The rolling average is 7 rows, not 7 days.** Days with no sales produce no
  row, so the window silently reaches further back. A date spine would fix it.
- **The dataset stops mid-September 2018**, so the final month shows −99.98%
  growth. A collection artifact, not a business event.
- **Payments and reviews are not modelled.** They sit at a different grain
  (per payment, per review) and belong in their own fact tables sharing the
  same dimensions, not bolted onto the item fact.
- **No orchestrator.** `make run` sequences the steps. Airflow or Dagster
  would add retries, scheduling and backfill windows.

---

## Commands

| Command | Does |
|---|---|
| `make setup` | Build images, start services, create schemas, load source |
| `make run` | Batch 1 → batch 2 → marts |
| `make test` | 23 pytest assertions |
| `make serve` | The five business answers |
| `make demo` | setup + run + test + serve |
| `make demo-live` | **The live demo** — see below |
| `make demo-fail` | Just the deliberate quality failure |
| `make logs` | `meta.pipeline_runs` history |
| `make psql` | Interactive shell on the warehouse |
| `make clean` | Stop services, delete all data |

Useful variants:

```bash
docker compose exec app python -m pipeline.run --max-ts 2018-01-01  # batch 1 only
docker compose exec app python -m pipeline.run                      # incremental
docker compose exec app python -m pipeline.run --marts              # marts only
```

---

## Live demo

```bash
make demo-live
```

Destroys all state and walks five stages end to end, roughly 6-8 minutes:

| Stage | Shows |
|---|---|
| 1. Fresh run | Batch 1 only — 45,430 orders, watermark lands at 2017-12-31 |
| 2. Incremental | Batch 2 — 54,011 more; watermark moves to 2018-10-17 |
| 3. Re-run | Extracts **0**; every count unchanged — idempotency |
| 4. **Quality gate fails** | A bad row is injected, the run aborts, the warehouse is proven untouched, then recovers |
| 5. Marts + answers | Three Parquet marts and the five business questions |

Stage 4 on its own:

```bash
make demo-fail
```

Observed output:

```
-- warehouse before:
   fact=112,650  revenue=13,591,643.70  dim_customer=96,138  staging_items=112,650
-- injecting a negative price into staging...
-- running the pipeline (expected to FAIL):
    FAIL  staging.order_items money non-negative        = 1
  quality.staging          FAILED
RUN FAILED: 1 of 8 staging checks failed ... negative money
   exit code = 1
-- warehouse after the failed run (must be unchanged):
   fact=112,650  revenue=13,591,643.70  dim_customer=96,138  staging_items=112,651
-- removing the bad row and recovering:
  fact revenue: 13,591,643.70
run complete
```

The pair of snapshots is the whole point. `staging_items` goes 112,650 →
112,65**1**, so the bad row genuinely landed in staging — raw and staging
accept what arrives. But `fact` and `revenue` are byte-identical before and
after, because the gate aborted the run *before* `load`. Delete the row,
re-run, green, with no manual cleanup of the warehouse.

---

## Dataset

The CSVs are **not** in this repository. Download the
[Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)
and unzip the nine files into `../data/olist/` (one level above `capstone/`),
which `docker-compose.yml` mounts read-only at `/data/olist`.

```bash
pip install kaggle
kaggle datasets download -d olistbr/brazilian-ecommerce -p ../data/olist --unzip
```

To keep them elsewhere, edit the mount in `docker-compose.yml`, or set
`CSV_DIR` on the `app` service.

---

## Layout

```
capstone/
├── docker-compose.yml       postgres + app
├── Dockerfile               python 3.12 + JDK 21 + Spark + JDBC driver
├── Makefile                 setup | run | test | serve | demo | logs | clean
├── pipeline/
│   ├── db.py                connections, watermarks, run logging
│   ├── bootstrap.py         DDL + CSV ingest, split into 2 batches
│   ├── extract.py           source -> raw, watermarked
│   ├── transform.py         raw -> staging, cleaned and deduplicated
│   ├── load.py              staging -> warehouse, upserts + SCD2
│   ├── quality.py           15 gates
│   ├── serve.py             the 5 business answers
│   ├── runlog.py            run history
│   └── run.py               orchestrator
├── spark/build_marts.py     3 Parquet marts over JDBC
├── sql/
│   ├── ddl/                 01 schemas, 02 source, 03 pipeline, 04 warehouse
│   └── queries/             business_questions.sql
└── tests/                   23 assertions
```

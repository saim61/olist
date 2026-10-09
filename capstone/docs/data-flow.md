# Data flow — write path and read path

Two separate journeys. Data is **written** once a night through five layers;
a question is **read** back through whichever layer can answer it fastest.

---

## 1. The write path — how data moves

```mermaid
flowchart TB
    CSV[("CSV files<br/>9 files · 1.55M rows")]

    subgraph PG["PostgreSQL"]
        SRC[("<b>source</b><br/>simulated OLTP<br/>normalized · FK-enforced<br/>99,441 orders")]
        RAW[("<b>raw</b><br/>untouched extract<br/>no constraints<br/>+ _run_id · _extracted_at")]
        STG[("<b>staging</b><br/>typed · trimmed · deduped<br/>keys enforced<br/>+ delivery_days · is_late")]
        WH[("<b>warehouse</b><br/>star schema<br/>1 fact · 5 dimensions<br/>112,650 fact rows")]
        META[("<b>meta</b><br/>pipeline_runs<br/>watermarks")]
    end

    MARTS[("<b>marts</b> · Parquet<br/>partitioned year=/month=<br/>3 marts · 103k rows")]

    CSV -->|"bootstrap.py<br/>COPY · no type inference"| SRC
    SRC -->|"extract.py<br/>WHERE purchase_ts > watermark"| RAW
    RAW -->|"transform.py<br/>lower · trim · dedupe · derive"| STG
    STG --> GATE1{{"QUALITY GATE<br/>8 staging checks"}}
    GATE1 -->|pass| WH
    GATE1 -.->|"fail → abort run<br/>warehouse untouched"| STOP1(["exit 1"])
    WH --> GATE2{{"QUALITY GATE<br/>7 warehouse checks"}}
    GATE2 -->|pass| MARTS
    GATE2 -.->|fail| STOP2(["exit 1"])

    SRC -.->|"load.py also reads<br/>products · sellers"| WH
    RAW -.->|writes| META
    WH -.->|writes| META
    META -.->|"reads watermark"| SRC

    style GATE1 fill:#fde68a,stroke:#b45309,color:#000
    style GATE2 fill:#fde68a,stroke:#b45309,color:#000
    style STOP1 fill:#fecaca,stroke:#b91c1c,color:#000
    style STOP2 fill:#fecaca,stroke:#b91c1c,color:#000
    style MARTS fill:#bbf7d0,stroke:#15803d,color:#000
```

**What each arrow costs**

| Step | Bounded by | Rows moved (2nd run) |
|---|---|---|
| `bootstrap` | one-off | 1,550,922 |
| `extract` | **watermark** | 54,011 — only new orders |
| `transform` | whole table | 112,650 — upserts, so safe but not cheap |
| `load` | whole table | 112,650 — same |
| `build_marts` | whole warehouse | 112,650 → 103,513 aggregated |

Only `extract` is incremental. Everything downstream re-processes and
upserts, which is why re-running is safe and why the run time does not
shrink on a small delta.

---

## 2. The read path — how a query gets answered

```mermaid
flowchart TB
    Q["Question<br/><i>revenue by category,<br/>last 90 days</i>"]
    DECIDE{"Is it a question<br/>we already<br/>pre-computed?"}

    MART[("<b>marts</b> · Parquet<br/>grain: day × category × state")]
    WH[("<b>warehouse</b> · star schema<br/>grain: one order item")]
    SRC[("<b>source</b> · OLTP")]

    FAST(["~40 ms<br/>read 3 month-partitions"])
    DEEP(["~4 s<br/>5-way join over 112k rows"])
    NEVER(["don't"])

    Q --> DECIDE
    DECIDE -->|"yes — known,<br/>repeated, stable grain"| MART --> FAST
    DECIDE -->|"no — ad-hoc,<br/>needs item detail"| WH --> DEEP
    DECIDE -.->|"never query<br/>the source system"| SRC --> NEVER

    FAST -.->|"asked every day?"| PROMOTE["promote it<br/>into a mart"]
    DEEP -.-> PROMOTE
    PROMOTE -.-> MART

    style MART fill:#bbf7d0,stroke:#15803d,color:#000
    style WH fill:#bfdbfe,stroke:#1d4ed8,color:#000
    style SRC fill:#e5e7eb,stroke:#6b7280,color:#000
    style NEVER fill:#fecaca,stroke:#b91c1c,color:#000
    style FAST fill:#bbf7d0,stroke:#15803d,color:#000
```

### Which layer answers what

| Question | Layer | Why |
|---|---|---|
| "Revenue by category, last 90 days" | **mart** | Pre-aggregated at exactly that grain |
| "7-day rolling average for health_beauty in SP" | **mart** | Already computed at write time |
| "Average order value — fewer orders or smaller ones?" | **warehouse** | Needs item-level detail the mart discarded |
| "What did customer X buy on 14 March?" | **warehouse** | Atomic grain |
| "Which orders have no payment row?" | **source** | Operational question, not analytical |

The asymmetry that drives the whole design: **you can always compute the mart
from the warehouse; you can never recover the warehouse from the mart.**
Aggregation is lossy and one-directional.

---

## 3. Inside a warehouse query — how a fact row finds its context

```mermaid
flowchart LR
    subgraph FACT["fact_order_items · 112,650 rows"]
        F["date_key: 20180314<br/>customer_key: 4471<br/>product_key: 1902<br/>seller_key: 88<br/>geo_key: 1337<br/>─────────<br/>price: 58.90<br/>delivery_days: 12<br/>is_late: false"]
    end

    D1[("dim_date<br/>20180314 → March 2018")]
    D2[("dim_customer<br/>4471 → São Paulo<br/>valid 2016-01-01 → 9999-12-31")]
    D3[("dim_product<br/>1902 → health_beauty")]
    D4[("dim_seller<br/>88 → seller in PR")]
    D5[("dim_geography<br/>1337 → SP · zip 01037")]

    F -->|date_key| D1
    F -->|customer_key| D2
    F -->|product_key| D3
    F -->|seller_key| D4
    F -->|geo_key| D5

    style FACT fill:#bfdbfe,stroke:#1d4ed8,color:#000
```

The fact row holds **keys and numbers only** — no city name, no category
text. One join per dimension retrieves the context. That is what keeps the
fact table narrow enough to scan quickly, and why the same question against
`source` needed five joins through foreign keys designed for transactions
rather than analysis.

```sql
-- the whole star, in one query
select d.month_name, p.category_en, g.state, sum(f.price) as revenue
from warehouse.fact_order_items f
join warehouse.dim_date      d on d.date_key    = f.date_key
join warehouse.dim_product   p on p.product_key = f.product_key
join warehouse.dim_geography g on g.geo_key     = f.geo_key
where d.year = 2018
group by 1, 2, 3;
```

**SCD Type 2 note:** `customer_key` points at the version of the customer
that was current *when the order was placed*, not the version current today.
That is why a 2017 order still reports the city it actually shipped to after
the customer moves.

---

## 4. Where each piece physically lives

```mermaid
flowchart TB
    subgraph HOST["Your machine"]
        DBEAVER["DBeaver<br/>localhost:5434"]
        CSVDIR["../data/olist/*.csv<br/>mounted read-only"]
    end

    subgraph COMPOSE["docker compose"]
        subgraph PGC["capstone_postgres"]
            PGVOL[("volume: capstone_pgdata<br/>source · raw · staging<br/>warehouse · meta")]
        end
        subgraph APPC["capstone_app"]
            PY["python 3.12<br/>pipeline/*.py"]
            SPARK["Spark 4.0 local[*]<br/>+ JDBC driver"]
            MVOL[("volume: capstone_marts<br/>Parquet files")]
        end
    end

    CSVDIR -->|"/data/olist"| PY
    PY -->|"psycopg2 :5432"| PGVOL
    SPARK -->|"JDBC :5432"| PGVOL
    SPARK -->|writes| MVOL
    DBEAVER -->|":5434 → :5432"| PGVOL
    DBEAVER -.->|"cannot see<br/>Parquet in a volume"| MVOL

    style MVOL fill:#bbf7d0,stroke:#15803d,color:#000
    style PGVOL fill:#bfdbfe,stroke:#1d4ed8,color:#000
```

DBeaver reaches everything in Postgres on port 5434, but **not** the marts —
those are Parquet files inside a Docker volume. Inspect them from the
container, or switch the volume to a bind mount (`./marts:/app/marts`) to
see them on disk.

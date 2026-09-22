"""
Does index column order matter, and by how much?

    python benchmarks/index_ordering.py

The query filters on two columns for equality and sorts by a third:

    SELECT sale_id, sale_date, sale_total
      FROM sales
     WHERE sale_customer = ? AND sale_status = 'closed'
     ORDER BY sale_date DESC
     LIMIT 20;

Four candidate indexes are compared below. Three of them are filled in for you
to guess at; write the CREATE INDEX statements, run it, and see which you got
right. Read the EXPLAIN QUERY PLAN column as carefully as the timing -- SCAN
means the index was not used at all.
"""

import random
import sqlite3
import time

N_SALES = 400_000
N_CUSTOMERS = 5_000
REPS = 300

QUERY = """
SELECT sale_id, sale_date, sale_total
  FROM sales
 WHERE sale_customer = ? AND sale_status = 'closed'
 ORDER BY sale_date DESC
 LIMIT 20
"""

# ---------------------------------------------------------------------------
# Four candidates. A B-tree is sorted by its leading column, then within that by
# the next, and so on -- so to narrow on column N the engine must already have a
# value pinned for columns 1..N-1. A leading column the query does not constrain
# makes everything behind it unreachable.
#
# The last candidate leads with the primary key, which the query never filters
# on. It is here as the counterexample: every rowid is unique, so nothing narrows
# and the planner falls back to a full scan despite the index containing every
# column the query uses.
# ---------------------------------------------------------------------------
CANDIDATES = [
    ("no index", None),
    ("equality columns first, sort column last", "CREATE INDEX ix ON sales(sale_customer, sale_status, sale_date)"),
    ("  + the selected column appended", "CREATE INDEX ix ON sales(sale_customer, sale_status, sale_date, sale_total)"),
    ("leading with the primary key", "CREATE INDEX ix ON sales(sale_id, sale_customer, sale_status, sale_date)")
]


def build():
    con = sqlite3.connect(":memory:")
    con.execute("""CREATE TABLE sales (
                       sale_id       INTEGER PRIMARY KEY,
                       sale_date     TEXT NOT NULL,
                       sale_customer TEXT,
                       sale_total    NUMERIC,
                       sale_status   TEXT NOT NULL
                           CHECK (sale_status IN ('open','closed')))""")
    rng = random.Random(7)
    con.executemany("INSERT INTO sales VALUES (?,?,?,?,?)", [
        (i,
         f"2026-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}",
         f"cust_{rng.randint(1, N_CUSTOMERS)}",
         round(rng.uniform(10, 900), 2),
         rng.choice(["open", "closed"]))
        for i in range(1, N_SALES + 1)])
    con.commit()
    return con


def plan(con):
    """The first line of EXPLAIN QUERY PLAN. SEARCH = index used, SCAN = not."""
    return con.execute("EXPLAIN QUERY PLAN " + QUERY, ("cust_1",)).fetchone()[3]


def measure(con):
    """Median ms per query over REPS random customers, after a warm-up."""
    rng = random.Random(11)
    customers = [f"cust_{rng.randint(1, N_CUSTOMERS)}" for _ in range(REPS)]
    con.execute(QUERY, ("cust_1",)).fetchall()          # warm the cache
    samples = []
    for c in customers:
        t0 = time.perf_counter()
        con.execute(QUERY, (c,)).fetchall()
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    return samples[len(samples) // 2]


con = build()
print(f"{N_SALES:,} sales, {N_CUSTOMERS:,} customers, {REPS} lookups each\n")
print(f"  {'index':<44} {'ms':>8}   query plan")
print(f"  {'-'*44} {'-'*8}   {'-'*40}")

baseline = None
for label, ddl in CANDIDATES:
    con.execute("DROP INDEX IF EXISTS ix")
    if ddl:
        con.execute(ddl)
    ms = measure(con)
    if baseline is None:
        baseline, baseline_plan = ms, plan(con)
    # Only claim a speedup when the query plan actually changed. Two rows that
    # both full-scan differ by cache state, not by the index.
    speedup = f"{baseline/ms:,.0f}x faster" if plan(con) != baseline_plan else ""
    print(f"  {label:<44} {ms:8.3f}   {plan(con)}  {speedup}")

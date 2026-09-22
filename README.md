# warehouse-schema

A relational schema for a warehouse inventory and sales system, in SQLite.

## Why

I worked on a Java inventory management system for a university software engineering course (the original is a private team repo). It shipped with a working UI, a JDBC data-access layer, and a schema that declared no foreign keys, no UNIQUE, and no NOT NULL — every relationship existed by convention and was enforced by the application, or not at all.

Auditing the database that shipped with it:

```
foreign keys declared in the schema: 0

warehouse_products.product_category -> warehouse_categories.category_id
    orphaned rows: 3 of 5     <-- would be blocked by a FOREIGN KEY
warehouse_inventory.product_id -> warehouse_products.product_id
    orphaned rows: 17 of 20   <-- would be blocked by a FOREIGN KEY
duplicate warehouse_products.product_sku: 1

21 row(s) violate constraints the schema does not declare.
```

Seventeen of twenty inventory rows referenced products that do not exist. Nothing
errored, nothing logged, and the application kept working — which is exactly the
problem. A constraint enforced in application code is a property of one code path;
a constraint in the database is a property of the data.

This repository is that schema rebuilt with the integrity rules it should have
had, plus the reasoning behind each one and measurements for the two decisions
that are usually guessed at.

```
python audit.py path/to/database.db   # the audit above, against any database
python verify.py                      # constraints and triggers behave as documented
python benchmarks/index_ordering.py   # index column order, measured
python benchmarks/denormalization.py  # the denormalization I evaluated and rejected
```

`audit.py` reads a database read-only and reports the rows that would violate the
constraints a schema fails to declare. It is the most portable thing here — point
it at any SQLite database of this shape.

---

## Foreign keys are off by default

```sql
PRAGMA foreign_keys = ON;
```

SQLite does not enforce foreign keys unless you ask, per connection. Without
that line, every `REFERENCES` clause in this schema is a comment. This is the
single easiest way to ship a schema that looks constrained and isn't.

## `RESTRICT` and `CASCADE` are different answers to different questions

Both appear in this schema, chosen per relationship rather than by default:

| Relationship | Behavior | Why |
|---|---|---|
| `products.product_category` → categories | `RESTRICT` | Deleting a category must never silently delete the products in it. The delete fails and a human decides. |
| `inventory.product_id` → products | `CASCADE` | An inventory row has no meaning once its product is gone. There is nothing to decide. |
| `sale_lines.sale_id` → sales | `CASCADE` | A line item belongs to its sale. |
| `sale_lines.product_id` → products | `RESTRICT` | Sales history must not lose its referent. |

The question is whether the child row still means anything on its own. If yes,
`RESTRICT`. If no, `CASCADE`.

## A stored derived value, and what makes it safe

`sales.sale_total` is derivable — it is `SUM(unit_price * quantity)` over that
sale's lines. Storing it is a deliberate denormalization, and the dangerous kind:
nothing structural stops the stored number from disagreeing with the rows it came
from.

It is safe here because of one rule: **a derived value is safe only if every input
freezes at the same instant the output does.**

That takes five triggers, not one. Guarding only the parent table would be *worse*
than no guard — it would make a wrong total permanent, because you could no longer
correct it.

| Trigger | Table | Event |
|---|---|---|
| `no_edits_after_close_sales` | `sales` | `UPDATE` |
| `no_delete_after_close_sales` | `sales` | `DELETE` |
| `no_line_edits_after_close` | `sale_lines` | `UPDATE` |
| `no_line_delete_after_close` | `sale_lines` | `DELETE` |
| `no_line_insert_after_close` | `sale_lines` | `INSERT` |

Three details that are easy to get wrong:

**Trigger names are global in SQLite**, not scoped per table. Two triggers named
`no_edits_after_close` on different tables is an error, not an override.

**`INSERT` has no `OLD` row.** That trigger reads `NEW.sale_id`; the others read
`OLD.sale_id`.

**An `UPDATE` on `sale_lines` has two parent sales.** `sale_id` is half the primary
key, and primary keys are updatable in SQL. Checking only `OLD.sale_id` lets a row
be moved *into* a closed sale, corrupting a total that is supposed to be frozen.
That trigger checks both sides.

There is also an ordering consequence: the total has to be computed in the *same
statement* that closes the sale, because the moment `sale_status` becomes `closed`
the row is locked.

```sql
UPDATE sales
   SET sale_total  = (SELECT SUM(unit_price * quantity) FROM sale_lines WHERE sale_id = ?),
       sale_status = 'closed'
 WHERE sale_id = ?;
```

Close first and compute second, and the trigger blocks the second statement — the
sale is frozen permanently with a `NULL` total and no legal statement can fix it.

## Normalization, and the one denormalization I rejected

The schema is in third normal form. `unit_price` on `sale_lines` looks redundant
against a product's current price but is not: it is the price *at the time of
sale*, it depends on the whole composite key, and there is nowhere else that
historical fact exists. A line item is an immutable record of a transaction; the
product table is mutable current state. They hold the same number for one instant
and diverge after.

I also evaluated copying `category_name` onto `warehouse_products` to remove a join
from every product read, and decided against it. Those writes have to be atomic or
the two copies silently drift — and enforcing consistency with a foreign key on the
duplicated column makes the rename impossible without deferring constraint checks
to commit, because no ordering of the two statements leaves the constraint satisfied
at every intermediate step.

`benchmarks/denormalization.py` measures what the duplication actually buys.
100,000 products across 20 categories, five runs each on the same machine:

```
full scan      joined 150.9 ms   denormalized 139.4 ms   -> 7.6% faster, 5 of 5 runs
point lookup   joined   5.27 us  denormalized   4.46 us  -> not measurable
write cost     1 row normalized  ->  4,991 rows (4,990 products + 1 category)
```

A rename touches every product in the category. It is 4,990 here only because
100,000 products are spread over 20 categories — at ten million products it is half
a million rows, in one transaction, atomic or the copies drift. The row count is not
a constant; it is the size of the category.

So: roughly 5,000x the write cost, to buy 7.6% on a full catalogue scan. And a full
scan is not the workload the duplication was supposed to help — product pages are
point lookups, and there the benefit is not measurable at all.

**On that point-lookup row.** Per-run results ranged from 3% slower to 35% faster,
standard deviation 17 percentage points, and the two ranges overlap almost entirely.
The harness also always times the joined schema first, so any cache warming favours
the denormalized number. "Not measurable" is the honest claim; a mean would imply a
precision these runs do not support.

## Index column ordering

For a query with equality predicates and a sort:

```sql
SELECT sale_id, sale_date, sale_total
  FROM sales
 WHERE sale_customer = ? AND sale_status = 'closed'
 ORDER BY sale_date DESC
 LIMIT 20;
```

the index must lead with the **equality** columns and put the **sort** column last.
A B-tree orders rows by its leading columns, so equality predicates on those columns
narrow the scan to a contiguous range; with the sort column last, rows come out of
that range already ordered and `LIMIT` can stop early without a sort step.

Leading with a column the query does not filter on — a unique primary key, say —
makes the index useless: every row is its own range and nothing narrows.

`benchmarks/index_ordering.py` measures four candidates against 400,000 rows,
300 lookups each:

```
no index                                       28.010 ms   SCAN
leading with the primary key                   27.053 ms   SCAN
(sale_customer, sale_status, sale_date)         0.051 ms   SEARCH USING INDEX              546x
  + sale_total appended                         0.021 ms   SEARCH USING COVERING INDEX   1,347x
```

The fourth is a **covering index**: every column the query needs lives inside it, so
the engine answers without reading a single table row. The plan says so explicitly,
and it is worth another 2.4x over the merely-correct index.

**A measurement artifact worth recording.** An earlier run put the unusable index at
18.7 ms against 28.0 ms for no index — an apparent 30% gain from an index the planner
had already refused to use. Re-running with the candidate order reversed moved it to
27.1 ms. The gain followed execution position, not the index: page-cache warming from
running second. Both are full scans and the difference is not attributable to the
index, so the table above reports no speedup for rows that share a plan with the
baseline.

## Known limitations

- `sale_total` is written by the application rather than computed by the database.
  A recompute-on-close trigger would remove that trust requirement, but SQLite
  does not allow assigning to `NEW` in a `BEFORE` trigger, and an `AFTER` trigger
  would be blocked by the immutability triggers above.
- The optimistic-locking `version` column on `warehouse_inventory` is present but
  enforced by convention: the database does not require writers to supply it.
- SQLite only. The `ON DELETE` semantics port cleanly; the deferred-constraint
  mechanism (`PRAGMA defer_foreign_keys`) is SQLite-specific — PostgreSQL uses
  `DEFERRABLE INITIALLY DEFERRED` on the constraint itself.
- Both benchmarks run their candidates in a fixed order in a single process. The
  index benchmark's artifact above shows why that matters; the point-lookup
  comparison in `denormalization.py` has the same weakness and has not been
  controlled for.

## Attribution

The schema, the constraint and trigger design, and every measurement reported here
are mine. The test and benchmark harnesses — `verify.py`, `audit.py`, and the timing
scaffolds in `benchmarks/` — were built with AI assistance; I wrote the queries and
index definitions they measure, and the analysis of what came back.

"""
Audit an existing SQLite database for the integrity violations that foreign
keys, UNIQUE and NOT NULL would have prevented.

    python audit.py path/to/database.db

Point it at a database whose schema declares no constraints and it reports what
got in anyway. This is the script that produced the numbers in README.md.

It reads the database read-only and changes nothing.
"""

import sqlite3
import sys
from pathlib import Path

# Relationships the schema SHOULD declare, as (child table, child column,
# parent table, parent column). Edit to match the database being audited.
RELATIONSHIPS = [
    ("warehouse_products",  "product_category", "warehouse_categories", "category_id"),
    ("warehouse_inventory", "product_id",       "warehouse_products",   "product_id"),
]

# Columns that should be UNIQUE.
UNIQUE = [
    ("warehouse_categories", "category_name"),
    ("warehouse_products",   "product_sku"),
    ("warehouse_products",   "product_name"),
]

# Columns that should be NOT NULL.
NOT_NULL = [
    ("warehouse_categories", "category_name"),
    ("warehouse_products",   "product_sku"),
    ("warehouse_products",   "product_name"),
    ("warehouse_products",   "product_category"),
    ("warehouse_inventory",  "product_available"),
    ("warehouse_inventory",  "product_hold"),
]

# Columns that should carry a CHECK (col >= 0).
NON_NEGATIVE = [
    ("warehouse_inventory", "product_available"),
    ("warehouse_inventory", "product_hold"),
]


def table_exists(con, table):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def main(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)

    declared = sum(len(list(con.execute(f'PRAGMA foreign_key_list("{t}")')))
                   for (t,) in con.execute(
                       "SELECT name FROM sqlite_master WHERE type='table'"))
    print(f"{path}")
    print(f"  foreign keys declared in the schema: {declared}\n")

    findings = 0

    for child, col, parent, pcol in RELATIONSHIPS:
        if not (table_exists(con, child) and table_exists(con, parent)):
            continue
        n, total = con.execute(f"""
            SELECT (SELECT COUNT(*) FROM "{child}" c
                     WHERE NOT EXISTS (SELECT 1 FROM "{parent}" p
                                        WHERE p."{pcol}" = c."{col}")),
                   (SELECT COUNT(*) FROM "{child}")""").fetchone()
        findings += n
        flag = "  <-- would be blocked by a FOREIGN KEY" if n else ""
        print(f"  {child}.{col} -> {parent}.{pcol}")
        print(f"      orphaned rows: {n} of {total}{flag}")

    print()
    for table, col in UNIQUE:
        if not table_exists(con, table):
            continue
        n = con.execute(f'SELECT COUNT(*) - COUNT(DISTINCT "{col}") FROM "{table}"').fetchone()[0]
        findings += n
        if n:
            print(f"  duplicate {table}.{col}: {n}  <-- would be blocked by UNIQUE")

    for table, col in NOT_NULL:
        if not table_exists(con, table):
            continue
        n = con.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{col}" IS NULL').fetchone()[0]
        findings += n
        if n:
            print(f"  NULL in {table}.{col}: {n}  <-- would be blocked by NOT NULL")

    for table, col in NON_NEGATIVE:
        if not table_exists(con, table):
            continue
        n = con.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{col}" < 0').fetchone()[0]
        findings += n
        if n:
            print(f"  negative {table}.{col}: {n}  <-- would be blocked by CHECK")

    print(f"\n{findings} row(s) violate constraints the schema does not declare.")
    return 0 if findings == 0 else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    if not Path(sys.argv[1]).exists():
        raise SystemExit(f"no such file: {sys.argv[1]}")
    sys.exit(main(sys.argv[1]))

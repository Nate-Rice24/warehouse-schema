"""
Verifies that the schema enforces what README.md claims it enforces.

    python verify.py

Every assertion here corresponds to a claim in the README. If one fails,
either the schema regressed or the README is lying.
"""

import sqlite3
import sys
from pathlib import Path

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()

CATS = [(1, "Fasteners", "screws, bolts, nuts")]
PRODS = [(1, "SKU-001", "M4 bolt", "stainless", 1),
         (2, "SKU-002", "epoxy 50ml", "two-part", 1),
         (3, "SKU-003", "washer", "zinc", 1)]

results = []


def fresh():
    """A loaded database with foreign keys on and transactions under our control."""
    con = sqlite3.connect(":memory:")
    con.isolation_level = None
    con.executescript(SCHEMA)
    con.execute("PRAGMA foreign_keys = ON")
    con.executemany("INSERT INTO warehouse_categories VALUES (?,?,?)", CATS)
    con.executemany("INSERT INTO warehouse_products VALUES (?,?,?,?,?)", PRODS)
    return con


def expect(label, con, sql, args=(), blocked=True):
    """Run sql and record whether it was blocked, against what we expected."""
    try:
        con.execute(sql, args)
        actual = False
    except sqlite3.Error:
        actual = True
    ok = (actual == blocked)
    results.append(ok)
    word = "BLOCKED" if actual else "ALLOWED"
    print(f"  [{'ok' if ok else 'FAIL'}] {label:<46} {word}")


def closed_sale(con, sale_id=5):
    """Create a sale, add lines, then close it -- computing the total in the SAME
    statement, because the row locks the instant sale_status becomes 'closed'."""
    con.execute("INSERT INTO sales (sale_id, sale_date) VALUES (?, '2026-09-20')", (sale_id,))
    con.executemany("INSERT INTO sale_lines VALUES (?,?,?,?)",
                    [(sale_id, 1, 2.00, 10), (sale_id, 2, 40.00, 1)])
    con.execute("""UPDATE sales
                      SET sale_total  = (SELECT SUM(unit_price * quantity)
                                           FROM sale_lines WHERE sale_id = ?),
                          sale_status = 'closed'
                    WHERE sale_id = ?""", (sale_id, sale_id))
    return con


print("schema executes")
con = fresh()
triggers = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='trigger'")]
print(f"  [{'ok' if len(triggers) == 5 else 'FAIL'}] 5 triggers created                           {len(triggers)}")
results.append(len(triggers) == 5)

print("\nreferential integrity")
con = fresh()
expect("product in a nonexistent category", con,
       "INSERT INTO warehouse_products VALUES (9,'SKU-9','x','d',999)")
expect("delete a category that still has products", con,
       "DELETE FROM warehouse_categories WHERE category_id=1")

print("\ncheck constraints")
con = fresh()
con.execute("INSERT INTO warehouse_inventory VALUES (1,5,0,0)")
expect("negative available stock", con,
       "UPDATE warehouse_inventory SET product_available=-1 WHERE product_id=1")
con.execute("INSERT INTO sales (sale_id,sale_date) VALUES (1,'2026-09-20')")
expect("sale line with quantity 0", con,
       "INSERT INTO sale_lines VALUES (1,1,2.0,0)")
expect("sale line with a negative unit_price", con,
       "INSERT INTO sale_lines VALUES (1,1,-5.0,1)")
expect("sale_status 'Closed' (wrong case)", con,
       "INSERT INTO sales (sale_id,sale_date,sale_status) VALUES (2,'2026-09-20','Closed')")

print("\nclosed sales are immutable")
con = closed_sale(fresh())
total, status = con.execute("SELECT sale_total, sale_status FROM sales WHERE sale_id=5").fetchone()
print(f"  [{'ok' if (total, status) == (60, 'closed') else 'FAIL'}] close + total in one statement"
      f"                 total={total} status={status}")
results.append((total, status) == (60, "closed"))
expect("UPDATE sales", con, "UPDATE sales SET sale_total=9999 WHERE sale_id=5")
expect("UPDATE sale_lines", con, "UPDATE sale_lines SET quantity=1000 WHERE sale_id=5 AND product_id=1")
expect("DELETE FROM sale_lines", con, "DELETE FROM sale_lines WHERE sale_id=5 AND product_id=2")
expect("INSERT INTO sale_lines", con, "INSERT INTO sale_lines VALUES (5,3,1.0,1)")
expect("DELETE FROM sales", con, "DELETE FROM sales WHERE sale_id=5")

stored = con.execute("SELECT sale_total FROM sales WHERE sale_id=5").fetchone()[0]
real = con.execute("SELECT SUM(unit_price*quantity) FROM sale_lines WHERE sale_id=5").fetchone()[0]
print(f"  [{'ok' if stored == real else 'FAIL'}] stored total still agrees with its lines"
      f"       {stored} == {real}")
results.append(stored == real)

print("\nthe NEW side of an UPDATE")
con = fresh()
con.execute("INSERT INTO sales (sale_id,sale_date,sale_total,sale_status) VALUES (7,'2026-09-20',60,'closed')")
con.execute("INSERT INTO sales (sale_id,sale_date) VALUES (8,'2026-09-20')")
con.execute("INSERT INTO sale_lines VALUES (8,1,2.0,10)")
expect("move a line INTO a closed sale", con, "UPDATE sale_lines SET sale_id=7 WHERE sale_id=8")

print("\nopen sales stay fully mutable")
con = fresh()
con.execute("INSERT INTO sales (sale_id,sale_date) VALUES (9,'2026-09-20')")
expect("INSERT a line", con, "INSERT INTO sale_lines VALUES (9,1,2.0,10)", blocked=False)
expect("UPDATE a line", con, "UPDATE sale_lines SET quantity=5 WHERE sale_id=9 AND product_id=1", blocked=False)
expect("DELETE a line", con, "DELETE FROM sale_lines WHERE sale_id=9 AND product_id=1", blocked=False)
expect("DELETE the sale (cascades to lines)", con, "DELETE FROM sales WHERE sale_id=9", blocked=False)

passed, total_n = sum(results), len(results)
print(f"\n{passed}/{total_n} checks passed")
sys.exit(0 if passed == total_n else 1)

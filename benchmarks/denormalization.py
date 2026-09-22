"""
Is it worth copying category_name onto warehouse_products?

    python benchmarks/denormalization.py

The claim denormalization makes is "faster reads, costlier writes." The write
cost is easy to count. The read benefit is the part people assume instead of
measuring -- so measure it, on both shapes of read:

    full scan     every product with its category name
    point lookup  one product page view

Then decide. The answer is allowed to be no.
"""

import random
import sqlite3
import time

N_PRODUCTS = 100_000
N_CATEGORIES = 20
SCAN_REPS = 5
POINT_REPS = 5_000

# ---------------------------------------------------------------------------
# Both shapes return the same four fields. JOINED reads the category name from
# warehouse_categories; DENORM reads the copy sitting on the product row. The
# selected columns are identical on purpose -- differ them and you are timing two
# different amounts of work, not two schemas.
# ---------------------------------------------------------------------------
JOINED_SCAN = "SELECT p.product_id, p.product_sku, p.product_name, c.category_name FROM warehouse_products p JOIN warehouse_categories c ON p.product_category = c.category_id"
DENORM_SCAN = "SELECT product_id, product_sku, product_name, category_name FROM warehouse_products"

JOINED_POINT = "SELECT p.product_id, p.product_sku, p.product_name, c.category_name FROM warehouse_products p JOIN warehouse_categories c ON p.product_category = c.category_id WHERE p.product_id = ?"
DENORM_POINT = "SELECT product_id, product_sku, product_name, category_name FROM warehouse_products WHERE product_id = ?"


def build(denorm):
    con = sqlite3.connect(":memory:")
    con.execute("""CREATE TABLE warehouse_categories (
                       category_id   INTEGER PRIMARY KEY,
                       category_name TEXT NOT NULL UNIQUE)""")
    extra = ", category_name TEXT NOT NULL" if denorm else ""
    con.execute(f"""CREATE TABLE warehouse_products (
                        product_id       INTEGER PRIMARY KEY,
                        product_sku      TEXT NOT NULL,
                        product_name     TEXT NOT NULL,
                        product_category INTEGER NOT NULL
                            REFERENCES warehouse_categories(category_id){extra})""")
    names = [f"category_{i:02d}" for i in range(1, N_CATEGORIES + 1)]
    con.executemany("INSERT INTO warehouse_categories VALUES (?,?)",
                    list(enumerate(names, 1)))
    rng = random.Random(7)
    rows = []
    for p in range(1, N_PRODUCTS + 1):
        k = rng.randint(1, N_CATEGORIES)
        row = (p, f"SKU-{p:06d}", f"product {p}", k)
        rows.append(row + ((names[k - 1],) if denorm else ()))
    con.executemany(
        f"INSERT INTO warehouse_products VALUES (?,?,?,?{',?' if denorm else ''})", rows)
    con.commit()
    return con


def time_scan(con, sql, reps=SCAN_REPS):
    con.execute(sql).fetchall()
    t0 = time.perf_counter()
    for _ in range(reps):
        con.execute(sql).fetchall()
    return (time.perf_counter() - t0) / reps * 1000          # ms


def time_point(con, sql, reps=POINT_REPS):
    rng = random.Random(11)
    ids = [rng.randint(1, N_PRODUCTS) for _ in range(reps)]
    con.execute(sql, (1,)).fetchall()
    t0 = time.perf_counter()
    for i in ids:
        con.execute(sql, (i,)).fetchall()
    return (time.perf_counter() - t0) / reps * 1_000_000     # microseconds


if None in (JOINED_SCAN, DENORM_SCAN, JOINED_POINT, DENORM_POINT):
    raise SystemExit("Write the four queries at the top of this file first.")

joined, denorm = build(False), build(True)
print(f"{N_PRODUCTS:,} products, {N_CATEGORIES} categories\n")

js, ds = time_scan(joined, JOINED_SCAN), time_scan(denorm, DENORM_SCAN)
print("FULL SCAN   (every product with its category name)")
print(f"  joined        {js:8.1f} ms")
print(f"  denormalized  {ds:8.1f} ms   -> {(js-ds)/js*100:+.0f}%")

jp, dp = time_point(joined, JOINED_POINT), time_point(denorm, DENORM_POINT)
print("\nPOINT LOOKUP  (one product page view)")
print(f"  joined        {jp:8.2f} us")
print(f"  denormalized  {dp:8.2f} us   -> {(jp-dp)/jp*100:+.0f}%")

# ---------------------------------------------------------------------------
# The write cost. Renaming one category has to touch every product row carrying a
# copy of that name, plus the category row itself, atomically -- or the two copies
# drift. The normalized schema touches exactly 1 row for the same rename.
#
# This count is not a constant: it is the size of the category.
# ---------------------------------------------------------------------------
count = denorm.execute("SELECT COUNT(*) FROM warehouse_products WHERE product_category = 1").fetchone()[0]
print(f"\nWrite cost of one category rename: {count + 1} rows ({count} products + 1 category)")
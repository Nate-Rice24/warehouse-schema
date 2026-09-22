-- Warehouse inventory and sales schema (SQLite)
--
-- Design notes live in README.md. The comments here explain the choices
-- that are not obvious from the DDL itself.

PRAGMA foreign_keys = ON;   -- SQLite does NOT enforce foreign keys by default.
                            -- Without this line every REFERENCES clause below is
                            -- documentation, not a constraint.


-- ---------------------------------------------------------------- categories

CREATE TABLE warehouse_categories (
    category_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category_name        TEXT NOT NULL UNIQUE,
    category_description TEXT
);


-- ---------------------------------------------------------------- products

CREATE TABLE warehouse_products (
    product_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_sku         TEXT NOT NULL UNIQUE,
    product_name        TEXT NOT NULL UNIQUE,
    product_description TEXT,

    -- RESTRICT, not CASCADE: deleting a category should never silently delete
    -- the products in it. The delete fails and a human decides what to do.
    product_category    INTEGER NOT NULL
        REFERENCES warehouse_categories(category_id) ON DELETE RESTRICT
);


-- ---------------------------------------------------------------- inventory

CREATE TABLE warehouse_inventory (
    -- product_id is both the primary key and the foreign key: exactly one
    -- inventory row per product, enforced structurally rather than by convention.
    --
    -- CASCADE here, unlike products above: an inventory row has no meaning
    -- once its product is gone. There is nothing for a human to decide.
    product_id        INTEGER PRIMARY KEY
        REFERENCES warehouse_products(product_id) ON DELETE CASCADE,

    product_available INTEGER NOT NULL DEFAULT 0 CHECK (product_available >= 0),
    product_hold      INTEGER NOT NULL DEFAULT 0 CHECK (product_hold      >= 0),

    -- Optimistic locking. Readers capture version, writers require it to be
    -- unchanged. Two concurrent decrements can no longer both succeed off the
    -- same stale read, which is the classic oversell.
    version           INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0)
);


-- ---------------------------------------------------------------- sales

CREATE TABLE sales (
    sale_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_date     TEXT    NOT NULL,
    sale_customer TEXT,

    -- Derived from SUM(unit_price * quantity) over sale_lines. Storing it is a
    -- deliberate denormalization, safe ONLY because the triggers below freeze
    -- the line items at the same moment they freeze this column. See README.
    sale_total    NUMERIC,

    -- The CHECK matters as much as the column. Without it a typo like 'Closed'
    -- silently unlocks a closed sale, because none of the triggers would match.
    sale_status   TEXT NOT NULL DEFAULT 'open'
        CHECK (sale_status IN ('open', 'closed'))
);


-- ---------------------------------------------------------------- sale lines

CREATE TABLE sale_lines (
    sale_id    INTEGER NOT NULL
        REFERENCES sales(sale_id)                 ON DELETE CASCADE,

    -- RESTRICT: a product referenced by sales history cannot be deleted.
    -- Historical records must not lose their referent.
    product_id INTEGER NOT NULL
        REFERENCES warehouse_products(product_id) ON DELETE RESTRICT,

    -- unit_price is the price AT THE TIME OF SALE, not the product's current
    -- price. It depends on the whole key (which sale, which product), so this
    -- is 2NF-compliant, and it is not redundant: there is nowhere else that
    -- historical fact exists. A line item is an immutable record of a
    -- transaction; the product table is mutable current state.
    unit_price NUMERIC NOT NULL CHECK (unit_price >= 0),
    quantity   INTEGER NOT NULL CHECK (quantity  >  0),

    PRIMARY KEY (sale_id, product_id)
);


-- ---------------------------------------------------------------- immutability
--
-- A stored derived value (sales.sale_total) is safe only if every input
-- freezes at the same instant the output does. Guarding the parent alone
-- would be worse than no guard: it would make a WRONG total permanent.
--
-- Five triggers: UPDATE and DELETE on sales, and UPDATE, DELETE and INSERT
-- on sale_lines. Trigger names are global in SQLite, not scoped per table,
-- so each needs a distinct name.

CREATE TRIGGER no_edits_after_close_sales
BEFORE UPDATE ON sales
FOR EACH ROW
WHEN OLD.sale_status = 'closed'
BEGIN
    SELECT RAISE(ABORT, 'closed sales are immutable');
END;

CREATE TRIGGER no_delete_after_close_sales
BEFORE DELETE ON sales
FOR EACH ROW
WHEN OLD.sale_status = 'closed'
BEGIN
    SELECT RAISE(ABORT, 'closed sales are immutable');
END;

-- Checks BOTH sides. sale_id is half the primary key, and primary keys are
-- updatable in SQL, so an UPDATE has two parent sales: the one the row is
-- leaving (OLD) and the one it is joining (NEW). Checking only OLD would let
-- a line be moved INTO a closed sale, corrupting a total that is supposed to
-- be frozen.
CREATE TRIGGER no_line_edits_after_close
BEFORE UPDATE ON sale_lines
FOR EACH ROW
WHEN (SELECT sale_status FROM sales WHERE sale_id = OLD.sale_id) = 'closed'
   OR (SELECT sale_status FROM sales WHERE sale_id = NEW.sale_id) = 'closed'
BEGIN
    SELECT RAISE(ABORT, 'closed sales are immutable');
END;

CREATE TRIGGER no_line_delete_after_close
BEFORE DELETE ON sale_lines
FOR EACH ROW
WHEN (SELECT sale_status FROM sales WHERE sale_id = OLD.sale_id) = 'closed'
BEGIN
    SELECT RAISE(ABORT, 'closed sales are immutable');
END;

-- NEW, not OLD: there is no prior row on an INSERT.
CREATE TRIGGER no_line_insert_after_close
BEFORE INSERT ON sale_lines
FOR EACH ROW
WHEN (SELECT sale_status FROM sales WHERE sale_id = NEW.sale_id) = 'closed'
BEGIN
    SELECT RAISE(ABORT, 'closed sales are immutable');
END;

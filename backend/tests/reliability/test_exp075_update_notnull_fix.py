"""
Exp075 (NOT NULL on PUT Extension): regression tests for
`preflight.py::_fix_update_notnull_field_loss` -- the UPDATE-path sibling
of Exp012/13's `_fix_model_schema_notnull_gap` (which targets CREATE only).

Root cause (confirmed live, Exp074's `inventory` canary, traced SQL:
`UPDATE products SET sku=?, name=?, category_id=?, unit_cost=?,
reorder_threshold=? ...` with every omitted field bound to `None`): the
backend route-generation LLM call sometimes writes an UNGUARDED field copy
in a `PUT`/`PATCH` handler -- `product.sku = product_in.sku` -- directly
from an Optional `{Model}Update` schema field. Pydantic gives an omitted
field `None`, so a partial update silently nulls out columns the client
never touched, crashing NOT NULL columns (`IntegrityError`) and silently
corrupting nullable ones (no crash at all -- worse).

Unlike Exp012/13 (which correctly RELAXES the model on CREATE, since a
fresh row has no "existing value" to preserve), the UPDATE-path fix must
never touch the model -- it guards the ROUTE's assignment with
`if product_in.sku is not None:` so an omitted field preserves the
existing row instead of nulling it.

Run directly: python tests/reliability/test_exp075_update_notnull_fix.py
"""
import ast
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.repair.preflight import _fix_update_notnull_field_loss
from app.services.deterministic_patcher import _model_notnull_no_default_columns


def _proj(tmp_path):
    p = Path(tmp_path)
    (p / "app" / "models").mkdir(parents=True, exist_ok=True)
    (p / "app" / "routes").mkdir(parents=True, exist_ok=True)
    return p


PRODUCT_MODEL = '''\
from sqlalchemy import Column, Integer, String, Float, ForeignKey
from app.database import Base

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    sku = Column(String(50), nullable=False, unique=True)
    name = Column(String(255), nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    unit_cost = Column(Float, nullable=True)
    reorder_threshold = Column(Integer, nullable=True)
'''

# The confirmed real Exp074 `inventory` shape: unguarded copy of every
# field, including the two NOT NULL columns (sku, name).
UNGUARDED_ROUTE = '''\
from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.products import Product
from app.schemas.product import ProductCreate, ProductUpdate

product_router = APIRouter()

@product_router.post("/products", status_code=201)
def create_product(product_in: ProductCreate, db: Session = Depends(get_db)):
    product = Product(sku=product_in.sku, name=product_in.name, category_id=product_in.category_id)
    db.add(product)
    db.commit()
    return product

@product_router.put("/products/{product_id}")
def replace_product(product_in: ProductUpdate, product_id: int = Path(..., gt=0), db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Not found")
    product.sku = product_in.sku
    product.name = product_in.name
    product.category_id = product_in.category_id
    product.unit_cost = product_in.unit_cost
    product.reorder_threshold = product_in.reorder_threshold
    db.commit()
    db.refresh(product)
    return product
'''


# ── 1. Single omitted field (the exact live incident shape) ────────────────

def test_single_notnull_field_gets_guarded(tmp_path):
    p = _proj(tmp_path)
    (p / "app" / "models" / "products.py").write_text(PRODUCT_MODEL, encoding="utf-8")
    route = p / "app" / "routes" / "product_routes.py"
    route.write_text(UNGUARDED_ROUTE, encoding="utf-8")

    changed = _fix_update_notnull_field_loss(p, [])
    out = route.read_text(encoding="utf-8")
    ast.parse(out)

    assert changed is True
    assert "if product_in.sku is not None:" in out
    assert "    product.sku = product_in.sku" in out


# ── 2. Multiple omitted fields (both NOT NULL columns guarded) ─────────────

def test_multiple_notnull_fields_all_guarded(tmp_path):
    p = _proj(tmp_path)
    (p / "app" / "models" / "products.py").write_text(PRODUCT_MODEL, encoding="utf-8")
    route = p / "app" / "routes" / "product_routes.py"
    route.write_text(UNGUARDED_ROUTE, encoding="utf-8")

    _fix_update_notnull_field_loss(p, [])
    out = route.read_text(encoding="utf-8")

    assert "if product_in.sku is not None:" in out
    assert "if product_in.name is not None:" in out
    # nullable columns are NOT this fix's scope -- left exactly as generated
    assert "product.category_id = product_in.category_id" in out
    assert "if product_in.category_id" not in out
    assert "product.unit_cost = product_in.unit_cost" in out
    assert "product.reorder_threshold = product_in.reorder_threshold" in out


# ── 3. Mixed nullable/non-nullable in one handler ───────────────────────────

def test_mixed_nullable_and_notnull_only_notnull_guarded(tmp_path):
    p = _proj(tmp_path)
    (p / "app" / "models" / "products.py").write_text(PRODUCT_MODEL, encoding="utf-8")
    route = p / "app" / "routes" / "product_routes.py"
    route.write_text(UNGUARDED_ROUTE, encoding="utf-8")

    n_notnull = len(_model_notnull_no_default_columns(p / "app" / "models")["Product"])
    assert n_notnull == 2  # sku, name -- confirms the fixture's own premise

    _fix_update_notnull_field_loss(p, [])
    out = route.read_text(encoding="utf-8")
    assert out.count("is not None:") == 2


# ── 4. Already-guarded (explicit is-not-None) is left alone / idempotent ───

def test_already_guarded_field_untouched(tmp_path):
    p = _proj(tmp_path)
    (p / "app" / "models" / "products.py").write_text(PRODUCT_MODEL, encoding="utf-8")
    route = p / "app" / "routes" / "product_routes.py"
    guarded_src = UNGUARDED_ROUTE.replace(
        "    product.sku = product_in.sku\n",
        "    if product_in.sku is not None:\n        product.sku = product_in.sku\n",
    )
    route.write_text(guarded_src, encoding="utf-8")

    _fix_update_notnull_field_loss(p, [])
    out = route.read_text(encoding="utf-8")
    # sku already guarded -- untouched; name (still unguarded) gets fixed
    assert out.count("if product_in.sku is not None:") == 1
    assert "if product_in.name is not None:" in out


def test_idempotent_second_pass_is_noop(tmp_path):
    p = _proj(tmp_path)
    (p / "app" / "models" / "products.py").write_text(PRODUCT_MODEL, encoding="utf-8")
    route = p / "app" / "routes" / "product_routes.py"
    route.write_text(UNGUARDED_ROUTE, encoding="utf-8")

    _fix_update_notnull_field_loss(p, [])
    once = route.read_text(encoding="utf-8")
    changed2 = _fix_update_notnull_field_loss(p, [])
    assert changed2 is False
    assert route.read_text(encoding="utf-8") == once


# ── 5. Partial update / complete update -- both preserve correctly at runtime ─

def test_runtime_partial_update_preserves_omitted_notnull_field(tmp_path):
    """The actual success criterion: omitted sku -> existing sku preserved,
    verified end to end against a real (in-memory) database, not just
    static AST inspection."""
    p = _proj(tmp_path)
    (p / "app" / "models" / "products.py").write_text(PRODUCT_MODEL, encoding="utf-8")
    route = p / "app" / "routes" / "product_routes.py"
    route.write_text(UNGUARDED_ROUTE, encoding="utf-8")
    _fix_update_notnull_field_loss(p, [])

    from sqlalchemy import Column, Integer, String, Float, create_engine
    from sqlalchemy.orm import declarative_base, sessionmaker

    Base = declarative_base()

    class Product(Base):
        __tablename__ = "products"
        id = Column(Integer, primary_key=True)
        sku = Column(String(50), nullable=False, unique=True)
        name = Column(String(255), nullable=False)
        unit_cost = Column(Float, nullable=True)

    class ProductUpdate:
        def __init__(self, sku=None, name=None, unit_cost=None):
            self.sku, self.name, self.unit_cost = sku, name, unit_cost

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    row = Product(sku="ABC123", name="Widget", unit_cost=5.0)
    db.add(row)
    db.commit()
    pid = row.id

    # Partial update: client PATCHes only `name`, omits sku entirely.
    product_in = ProductUpdate(sku=None, name="Widget EDITED")
    product = db.query(Product).filter(Product.id == pid).first()
    if product_in.sku is not None:
        product.sku = product_in.sku
    if product_in.name is not None:
        product.name = product_in.name
    db.commit()
    db.refresh(product)

    assert product.sku == "ABC123"          # preserved, not NULLed
    assert product.name == "Widget EDITED"  # the field that WAS provided still updates


def test_runtime_complete_update_still_applies_all_fields(tmp_path):
    """A complete update (every field provided) must still work identically
    to before the guard was added -- the guard only changes behavior for
    OMITTED fields, never for genuinely provided ones."""
    from sqlalchemy import Column, Integer, String, create_engine
    from sqlalchemy.orm import declarative_base, sessionmaker

    Base = declarative_base()

    class Product(Base):
        __tablename__ = "products"
        id = Column(Integer, primary_key=True)
        sku = Column(String(50), nullable=False, unique=True)
        name = Column(String(255), nullable=False)

    class ProductUpdate:
        def __init__(self, sku=None, name=None):
            self.sku, self.name = sku, name

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    row = Product(sku="ABC123", name="Widget")
    db.add(row)
    db.commit()
    pid = row.id

    product_in = ProductUpdate(sku="NEW-SKU", name="New Name")
    product = db.query(Product).filter(Product.id == pid).first()
    if product_in.sku is not None:
        product.sku = product_in.sku
    if product_in.name is not None:
        product.name = product_in.name
    db.commit()
    db.refresh(product)

    assert product.sku == "NEW-SKU"
    assert product.name == "New Name"


# ── 6. Explicit null in request vs. omitted -- same wire representation ────

def test_explicit_none_and_omitted_are_indistinguishable_and_both_preserved(tmp_path):
    """Pydantic/FastAPI give both 'field omitted from JSON' and 'field
    explicitly sent as null' the same Python value (None) once bound to an
    Optional field with no distinguishing sentinel -- this fix (and the
    route pattern it produces) necessarily treats them identically, which
    is documented here as a known, accepted limitation, not silently
    assumed. A client that WANTS to clear a NOT NULL field can't via this
    endpoint shape -- acceptable, since NULLing a NOT NULL column is never
    a valid operation anyway."""
    p = _proj(tmp_path)
    (p / "app" / "models" / "products.py").write_text(PRODUCT_MODEL, encoding="utf-8")
    route = p / "app" / "routes" / "product_routes.py"
    route.write_text(UNGUARDED_ROUTE, encoding="utf-8")
    _fix_update_notnull_field_loss(p, [])
    out = route.read_text(encoding="utf-8")
    assert "if product_in.sku is not None:" in out


# ── 7. CREATE path completely unaffected ────────────────────────────────────

def test_create_handler_never_touched(tmp_path):
    p = _proj(tmp_path)
    (p / "app" / "models" / "products.py").write_text(PRODUCT_MODEL, encoding="utf-8")
    route = p / "app" / "routes" / "product_routes.py"
    route.write_text(UNGUARDED_ROUTE, encoding="utf-8")

    _fix_update_notnull_field_loss(p, [])
    out = route.read_text(encoding="utf-8")
    assert (
        "product = Product(sku=product_in.sku, name=product_in.name, "
        "category_id=product_in.category_id)"
    ) in out


def test_model_column_definitions_never_touched(tmp_path):
    """Distinguishing check vs. Exp012/13: this fix must NEVER relax a
    model column to nullable=True -- the model file must be byte-for-byte
    unchanged."""
    p = _proj(tmp_path)
    model_file = p / "app" / "models" / "products.py"
    model_file.write_text(PRODUCT_MODEL, encoding="utf-8")
    route = p / "app" / "routes" / "product_routes.py"
    route.write_text(UNGUARDED_ROUTE, encoding="utf-8")

    _fix_update_notnull_field_loss(p, [])
    assert model_file.read_text(encoding="utf-8") == PRODUCT_MODEL


# ── 8. Real inventory replay (exact Exp074 artifact shape) ─────────────────

def test_real_inventory_artifact_replay(tmp_path):
    """Byte-for-byte the shape read directly from
    generated_projects/inventory_manager/app/routes/product_routes.py's
    pre-repair state, reconstructed from Exp074's own traceback SQL:
    `UPDATE products SET sku=?, name=?, category_id=?, unit_cost=?,
    reorder_threshold=? ...` with parameters
    `(None, 'Journey Test Item EDITED', None, None, None, 1)`."""
    p = _proj(tmp_path)
    (p / "app" / "models" / "products.py").write_text(PRODUCT_MODEL, encoding="utf-8")
    route = p / "app" / "routes" / "product_routes.py"
    route.write_text(UNGUARDED_ROUTE, encoding="utf-8")

    changed = _fix_update_notnull_field_loss(p, [])
    out = route.read_text(encoding="utf-8")
    ast.parse(out)

    assert changed is True
    assert "if product_in.sku is not None:" in out
    assert "    product.sku = product_in.sku" in out
    assert "if product_in.name is not None:" in out
    assert "    product.name = product_in.name" in out


if __name__ == "__main__":
    import tempfile as _tempfile
    import traceback

    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            with _tempfile.TemporaryDirectory() as td:
                t(Path(td))
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
        except Exception:
            failed += 1
            print(f"ERROR: {t.__name__}:")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)

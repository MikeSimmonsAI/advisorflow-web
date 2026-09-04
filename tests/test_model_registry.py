"""
The model registry is the schema's single source of truth.

`Base.metadata.create_all()` and Alembic autogenerate both only see tables
whose module has been imported. That list of side-effect imports used to live
in app/main.py alone, which meant:

  * tests/conftest.py built a PARTIAL metadata and create_all() raised
    NoReferencedTableError - `proposals` is declared in models.py but its
    foreign keys point at `opportunities` and `brand_sales_orgs` in
    sales_models.py.
  * alembic/env.py built the same partial metadata, so autogenerate compared
    the real database against an incomplete schema and proposed DROPPING the
    tables and columns it could not see.

app/models/registry.py is now that one list, and this file is the guard that
keeps it one list. These are static/structural assertions on purpose: they
must hold for the Alembic process too, which never runs pytest.
"""

import pathlib
import pkgutil
import re

import app.models
import app.models.registry  # noqa: F401  (the thing under test)
from app.models.models import Base

REPO = pathlib.Path(__file__).resolve().parent.parent


def _source(rel):
    return (REPO / rel).read_text(encoding="utf-8")


# ── The registry covers every model module ───────────────────────────────────

def test_registry_imports_every_model_module():
    """A model module missing from the registry is a table that silently never
    gets created. Discovering the modules rather than listing them means a new
    one added tomorrow fails here instead of failing in production."""
    on_disk = {
        name for _, name, _ in pkgutil.iter_modules(app.models.__path__)
        if name not in ("registry",)
    }
    registry_src = _source("app/models/registry.py")
    imported = set(re.findall(r"^import app\.models\.(\w+)", registry_src, re.M))
    imported |= set(re.findall(r"^from app\.models\.(\w+) import", registry_src, re.M))

    missing = sorted(on_disk - imported)
    assert not missing, (
        "app/models/registry.py does not import: %s. "
        "Every module under app/models/ must be registered there." % ", ".join(missing)
    )


def test_there_is_only_one_registry():
    """main.py, conftest.py and alembic/env.py must all defer to the registry
    rather than keeping their own copy of the import list - a second copy is
    how the first one drifted."""
    for rel in ("app/main.py", "tests/conftest.py", "alembic/env.py"):
        src = _source(rel)
        assert "import app.models.registry" in src, \
            "%s does not import the model registry" % rel
        # No file may re-list individual model modules alongside the registry.
        strays = [m for m in re.findall(r"^import app\.models\.(\w+)", src, re.M)
                  if m != "registry"]
        assert not strays, (
            "%s imports model modules directly (%s) instead of using the "
            "registry" % (rel, ", ".join(strays))
        )


# ── Alembic specifically ─────────────────────────────────────────────────────

def test_alembic_registers_models_before_reading_target_metadata():
    """Order matters, not just presence: target_metadata is a snapshot taken at
    assignment time, so an import placed after it would register the models too
    late to appear in an autogenerate run."""
    src = _source("alembic/env.py")
    reg = src.index("import app.models.registry")
    tgt = src.index("target_metadata = Base.metadata")
    assert reg < tgt, (
        "alembic/env.py imports the registry after target_metadata is "
        "assigned; autogenerate would still see a partial schema."
    )


# ── The metadata that results ────────────────────────────────────────────────

def test_cross_module_foreign_keys_resolve():
    """The exact failure this whole arrangement exists to prevent: `proposals`
    lives in models.py, `opportunities` and `brand_sales_orgs` live in
    sales_models.py, and a metadata holding only the first cannot resolve the
    link between them."""
    for table in ("proposals", "opportunities", "brand_sales_orgs"):
        assert table in Base.metadata.tables, "%s missing from Base.metadata" % table

    # sorted_tables walks every foreign key in the metadata and raises
    # NoReferencedTableError if any target is absent.
    Base.metadata.sorted_tables


def test_proposal_foreign_key_targets_are_present():
    fks = {fk.column.table.name
           for fk in Base.metadata.tables["proposals"].foreign_keys}
    assert {"opportunities", "brand_sales_orgs"} <= fks
    assert fks <= set(Base.metadata.tables)

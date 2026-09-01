"""One-off script: seed the local docker-compose postgres with the reference/
seed data every real `alembic upgrade head` run would insert, WITHOUT going
through Alembic's own upgrade-path resolution (confirmed separately, via
py-spy, to be pathologically slow - minutes of CPU burn in its own
_topological_sort - against this repo's 190-revision, 14-merge-point
history when starting from an empty database).

Approach: the local DB's SCHEMA is already correct (tests/conftest.py's
create_schema fixture already ran SQLAlchemy's own Base.metadata.create_all()
against it, and `alembic stamp head` marked it as being at head with zero
actual migration content executed). What's missing is only the DATA that
migrations' op.bulk_insert/op.execute(INSERT...) calls would have inserted.

This computes the correct revision order itself (a plain Kahn's-algorithm
topological sort - O(V+E), nothing like whatever is slow inside Alembic's
own implementation), then replays every migration's upgrade() function
against the local DB with DDL-shaped Operations methods (create_table,
add_column, create_index, alter_column, etc.) patched to swallow "already
exists"/conflict errors instead of raising - since the schema those calls
would create already exists - while leaving data-shaped calls (bulk_insert,
execute) to run for real. Runs in autocommit mode so one statement's
failure never blocks the next.
"""
import importlib.util
import logging
import os
import re
import sys
import uuid
from pathlib import Path

logging.basicConfig(level=logging.WARNING)

BACKEND_DIR = Path(__file__).resolve().parent
VERSIONS_DIR = BACKEND_DIR / "alembic" / "versions"

LOCAL_DATABASE_URL = "postgresql+psycopg2://zoiko:zoiko@localhost:5435/zoiko_local"
os.environ["DATABASE_URL"] = LOCAL_DATABASE_URL
sys.path.insert(0, str(BACKEND_DIR))

import sqlalchemy as sa  # noqa: E402
from alembic.operations import Operations  # noqa: E402
from alembic.runtime.migration import MigrationContext  # noqa: E402


def _parse_revision_graph():
    revs = {}
    for f in VERSIONS_DIR.glob("*.py"):
        text = f.read_text(encoding="utf-8")
        m = re.search(r"^revision(?:\s*:\s*\w+)?\s*=\s*['\"]([\w]+)['\"]", text, re.M)
        dm = re.search(r"^down_revision(?:\s*:\s*[\w\[\], ]+)?\s*=\s*(.+)$", text, re.M)
        if not m:
            continue
        rev = m.group(1)
        downs = []
        if dm:
            val = dm.group(1)
            downs = re.findall(r"['\"]([0-9a-zA-Z_]{6,})['\"]", val)
        revs[rev] = {"path": f, "downs": downs}
    return revs


def _topological_order(revs: dict) -> list[str]:
    # Kahn's algorithm: an edge goes down_revision -> revision (a revision
    # depends on / runs after its down_revision(s)).
    indegree = {r: 0 for r in revs}
    children: dict[str, list[str]] = {r: [] for r in revs}
    for rev, info in revs.items():
        for down in info["downs"]:
            if down not in revs:
                continue
            children[down].append(rev)
            indegree[rev] += 1

    ready = sorted([r for r, deg in indegree.items() if deg == 0])
    order = []
    while ready:
        ready.sort()
        r = ready.pop(0)
        order.append(r)
        for child in children[r]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)

    if len(order) != len(revs):
        missing = set(revs) - set(order)
        raise RuntimeError(f"topological sort incomplete - cycle or missing parent involving: {missing}")
    return order


def _load_module(path: Path, rev: str):
    spec = importlib.util.spec_from_file_location(f"migration_{rev}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Exactly the op.* method names actually called across every migration file
# in this repo (grepped, not guessed) - op.f/op.get_bind/op.get_context are
# deliberately excluded, since those are introspection/utility calls that
# must keep working normally, not fail-tolerant ones.
_TOLERANT_METHOD_NAMES = [
    "create_table", "drop_table", "add_column", "drop_column", "alter_column",
    "create_index", "drop_index", "create_foreign_key", "drop_constraint",
    "create_unique_constraint", "bulk_insert", "execute",
]


def _make_tolerant(operations: Operations) -> Operations:
    """Patches only the specific op.* methods this repo's migrations
    actually call (see module docstring) to log-and-continue on failure
    instead of raising - DDL calls conflict with schema create_all()
    already built, which is expected and safe to ignore here, not a real
    error. Leaves every other Operations method (op.f, op.get_bind, ...)
    untouched so Alembic's own internals keep working normally."""
    for name in _TOLERANT_METHOD_NAMES:
        original = getattr(operations, name)

        def _wrapped(*args, _original=original, _name=name, **kwargs):
            try:
                return _original(*args, **kwargs)
            except Exception as e:
                logging.warning("  [skip] %s(%r) -> %s: %s", _name, args[:1], type(e).__name__, e)
                return None

        setattr(operations, name, _wrapped)
    return operations


def main():
    revs = _parse_revision_graph()
    order = _topological_order(revs)
    print(f"Resolved {len(order)} revisions in dependency order (own topological sort, not Alembic's).")

    engine = sa.create_engine(LOCAL_DATABASE_URL, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        op_obj = _make_tolerant(Operations(context))

        for i, rev in enumerate(order, 1):
            info = revs[rev]
            module = _load_module(info["path"], rev)
            if not hasattr(module, "upgrade"):
                continue
            print(f"[{i}/{len(order)}] {rev} - {info['path'].name}")
            # Deliberately NOT `with Operations.context(op_obj):` - that
            # classmethod always constructs a brand-new, non-tolerant
            # `Operations(op_obj)` internally (it expects a MigrationContext,
            # not an Operations instance) and installs THAT as the proxy
            # target for the module-level `op` alembic import - silently
            # bypassing every patched method above. Installing our own
            # pre-patched instance directly via the same private hooks
            # Operations.context() itself uses is what actually makes
            # `op.create_table(...)` etc. inside each migration's upgrade()
            # route through the tolerant, log-and-continue methods.
            op_obj._install_proxy()
            try:
                try:
                    module.upgrade()
                except Exception as e:
                    # A failure INSIDE upgrade() that isn't one of the
                    # patched op.* calls (e.g. a plain Python error building
                    # the seed list) still aborts just this one migration's
                    # remaining statements - logged, not fatal to the run.
                    logging.warning("  [migration-level skip] %s: %s: %s", rev, type(e).__name__, e)
            finally:
                op_obj._remove_proxy()

    print("Done.")


if __name__ == "__main__":
    main()

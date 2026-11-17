"""#146 guard: the SQLite test adapter must pass every upsert through verbatim.

`tests/conftest.py::_convert_on_conflict_to_sqlite` used to rewrite every
`INSERT ... ON CONFLICT ... DO UPDATE` lacking `RETURNING` into
`INSERT OR REPLACE` — a DELETE followed by an INSERT of the VALUES row. Tests
driving those branches then read the VALUES defaults instead of the pre-state,
so the branch under test never ran and the assertion passed for the wrong
reason. #41 (a paying customer's groups silently unmoderated for ~36 days) and
#130 (`is_registered` lost on procedure rows) were both certified that way.

The fix was to delete the rewrite, not to work around it: SQLite 3.24+ supports
`ON CONFLICT ... DO UPDATE` natively and 3.35+ supports `RETURNING`, so there
was never anything to convert. `tests/upsert_masking.py` enumerates the sites by
AST and this file asks the ADAPTER ITSELF about each one — deliberately not a
second copy of the adapter's predicate, which is how the first version of the
enumerator silently drifted from the code it was measuring.

Two tests, and the second is the one that matters: a guard that only ever runs
against a healthy tree has never been shown to fire. `test_the_guard_has_teeth`
reinstates the pre-#146 rewrite in memory and asserts the same comparison flags
it — so the guard is proven to detect the defect it exists for.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# Sibling test-support module, per the convention in tests/gate_registry.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import upsert_masking  # noqa: E402


def _adapter(test_pool):
    """The SQLite connection adapter, or skip on a PostgreSQL backend."""
    adapter = getattr(test_pool, "_conn", None)
    if not hasattr(adapter, "_convert_on_conflict_to_sqlite"):
        pytest.skip("PostgreSQL backend: the rewrite this guards is SQLite-only")
    return adapter


def test_the_adapter_passes_every_upsert_through_verbatim(test_pool):
    """No `DO UPDATE` site in `src/` may be rewritten by the test adapter."""
    adapter = _adapter(test_pool)
    sites = upsert_masking.enumerate_upsert_sites()

    assert sites, (
        "enumerator found no `DO UPDATE` sites in src/ — the instrument is "
        "broken, and a clean zero from a broken instrument proves nothing "
        "(#146)"
    )

    rewritten = []
    for site in sites:
        converted = adapter._convert_on_conflict_to_sqlite(site.sql)
        if converted != site.sql:
            rewritten.append((site, converted))

    assert not rewritten, (
        "the SQLite test adapter rewrites these upserts, so tests of their "
        "branches certify behaviour production never has (#146):\n"
        + "\n".join(
            f"  {site.where}\n    -> {converted.strip()!r}"
            for site, converted in rewritten
        )
    )


def _legacy_convert(query: str) -> str:
    """The pre-#146 rewrite, reinstated here as a positive control."""
    if "RETURNING" in query.upper() and "DO UPDATE" in query.upper():
        return query
    query = re.sub(r"\bINSERT\b", "INSERT OR REPLACE", query, flags=re.IGNORECASE)
    return re.sub(
        r"\s+ON\s+CONFLICT\b.*?(?=RETURNING|;|$)",
        "",
        query,
        flags=re.IGNORECASE | re.DOTALL,
    )


def test_the_guard_has_teeth():
    """Positive control: the pre-#146 rewrite IS flagged by the same comparison.

    Without this, `test_the_adapter_passes_every_upsert_through_verbatim` would
    also pass against an enumerator that found nothing, or against a comparison
    that could never differ. Historically this flags 9 of the 10 sites — the
    tenth carries `RETURNING` and was the only faithful one.
    """
    sites = upsert_masking.enumerate_upsert_sites()
    flagged = [site for site in sites if _legacy_convert(site.sql) != site.sql]

    assert flagged, (
        "the pre-#146 rewrite flagged no site, so the guard above cannot bite: "
        "either the enumerator sees no real upserts or the comparison is inert"
    )
    # The rewrite destroyed the `SET` clause, which is the whole defect.
    for site in flagged:
        assert "INSERT OR REPLACE" in _legacy_convert(site.sql), site.where

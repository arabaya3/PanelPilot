"""Importing any one table module is enough for every mapper to configure.

Found live: the worker imported ``ingestion`` alone, and the first crawl to
reach its last step failed resolving ``verification_items.flagged_answer_id``.
A fresh interpreter per module, because in this process every table has long
since been imported by some other test.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

TABLES = Path(__file__).resolve().parents[3] / "models" / "tables"
MODULES = sorted(p.stem for p in TABLES.glob("*.py") if p.stem != "__init__")


@pytest.mark.parametrize("module", MODULES)
def test_one_table_module_alone_resolves_every_foreign_key(module: str) -> None:
    # Resolving every foreign key's target is what a flush does, and what
    # failed; `configure_mappers` alone does not reach it.
    code = (
        "from sqlalchemy.orm import configure_mappers\n"
        f"import app.models.tables.{module}\n"
        "from app.models.tables.base import Base\n"
        "configure_mappers()\n"
        "[fk.column for t in Base.metadata.tables.values() for fk in t.foreign_keys]\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=TABLES.parents[2],
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]

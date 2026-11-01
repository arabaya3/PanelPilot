"""``entities``: plain values whose modules import no framework."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from app.design.model import migrate_v2

MODEL_DIR = Path(migrate_v2.__file__).parent
API_ROOT = MODEL_DIR.parents[2]
PURE_MODULES = ("entities.py", "findings_v2.py", "domain.py")
FRAMEWORKS = ("pydantic", "fastapi", "sqlalchemy", "starlette")


# --------------------------------------------------------------------------- #
# Purity
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("module", PURE_MODULES)
def test_domain_modules_import_only_the_standard_library_and_each_other(module: str) -> None:
    tree = ast.parse((MODEL_DIR / module).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            base = node.module or ""
            imported |= (
                {f"{base}.{alias.name}" for alias in node.names}
                if base == ("app.design.model")
                else {base}
            )
    app_imports = {name for name in imported if name.split(".")[0] == "app"}
    assert app_imports <= {"app.design.model.domain", "app.design.model.entities"}, app_imports
    roots = {name.split(".")[0] for name in imported - app_imports}
    assert roots <= {"__future__", "collections", "dataclasses", "decimal", "types", "typing"}


@pytest.mark.parametrize("module", ["entities", "findings_v2"])
def test_domain_modules_load_no_framework_in_a_fresh_interpreter(module: str) -> None:
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(API_ROOT)!r})\n"
        f"import app.design.model.{module}\n"
        f"bad = sorted(m for m in sys.modules if m.split('.')[0] in {FRAMEWORKS!r})\n"
        "print(','.join(bad))\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == ""

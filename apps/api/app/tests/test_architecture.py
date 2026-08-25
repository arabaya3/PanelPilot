"""Executable guards for the architectural rules the README describes.

Written conventions get violated; asserted ones do not. These tests fail CI when
a boundary is crossed, so a reviewer never has to spot it by eye.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
TESTS_ROOT = APP_ROOT / "tests"

FORBIDDEN_IN_ROUTES = ("opensearchpy", "sqlalchemy", "app.ai", "app.ingestion")
FORBIDDEN_IN_DOMAIN_AND_AI = ("fastapi", "starlette")
# The worker is a runtime of its own; pulling in the web framework means
# someone put request-shaped logic in a batch job. See ADR 0002.
FORBIDDEN_IN_WORKER = ("fastapi", "starlette", "opensearchpy", "app.ai")
DOCSTRING_REQUIRED_DIRS = ("domain", "ai")


def _source_modules(*relative_dirs: str) -> list[Path]:
    """Return every module under the given app subdirectories.

    Dunder modules (``__init__``, ``__main__``) are excluded: they exist to
    satisfy the import system, hold no logic, and need no mirrored test.
    """
    paths: list[Path] = []
    for relative in relative_dirs:
        paths.extend(
            p
            for p in (APP_ROOT / relative).rglob("*.py")
            if not p.name.startswith("__") and TESTS_ROOT not in p.parents
        )
    return sorted(paths)


def _imported_names(module: Path) -> set[str]:
    """Return every module name imported by a source file."""
    tree = ast.parse(module.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("module", _source_modules("api/v1"), ids=str)
def test_route_modules_stay_thin(module: Path) -> None:
    """Routes parse, delegate, and return — no DB, search, or AI calls.

    ``api/deps.py`` is exempt by design: dependencies resolve inputs, so it is
    the one place in the API layer allowed to name a ``Session``.
    """
    offenders = [
        name
        for name in _imported_names(module)
        if any(name.startswith(forbidden) for forbidden in FORBIDDEN_IN_ROUTES)
    ]
    assert not offenders, (
        f"{module.relative_to(APP_ROOT)} imports {offenders}. "
        "Route handlers call one app.domain function; move this there."
    )


@pytest.mark.parametrize("module", _source_modules("domain", "ai"), ids=str)
def test_domain_and_ai_are_framework_agnostic(module: Path) -> None:
    """Business logic must not depend on the web framework."""
    offenders = [
        name
        for name in _imported_names(module)
        if any(name.startswith(forbidden) for forbidden in FORBIDDEN_IN_DOMAIN_AND_AI)
    ]
    assert not offenders, (
        f"{module.relative_to(APP_ROOT)} imports {offenders}. "
        "Raise an app.core.errors exception instead; the API layer translates it."
    )


@pytest.mark.parametrize("module", _source_modules("worker"), ids=str)
def test_worker_jobs_stay_thin(module: Path) -> None:
    """Jobs open a session, call one app.domain function, and return.

    Thin for the same reason routes are thin: the worker is a delivery
    mechanism, not a place for logic. See ADR 0002.
    """
    offenders = [
        name
        for name in _imported_names(module)
        if any(name.startswith(forbidden) for forbidden in FORBIDDEN_IN_WORKER)
    ]
    assert not offenders, (
        f"{module.relative_to(APP_ROOT)} imports {offenders}. "
        "Background jobs call one app.domain function; move this there."
    )


@pytest.mark.parametrize("module", _source_modules(*DOCSTRING_REQUIRED_DIRS), ids=str)
def test_domain_and_ai_functions_are_documented(module: Path) -> None:
    """Every public function in domain/ and ai/ carries a docstring."""
    tree = ast.parse(module.read_text(encoding="utf-8"))
    undocumented = [
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
        and ast.get_docstring(node) is None
    ]
    assert not undocumented, f"{module.relative_to(APP_ROOT)}: undocumented {undocumented}"


@pytest.mark.parametrize("module", _source_modules("ai/tools"), ids=str)
def test_calc_tools_cite_their_source(module: Path) -> None:
    """Calc-tool docstrings name the manufacturer guide behind the formula."""
    if module.name == "registry.py":
        pytest.skip("the registry holds no formulas")
    tree = ast.parse(module.read_text(encoding="utf-8"))
    uncited = [
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
        and "Source:" not in (ast.get_docstring(node) or "")
    ]
    assert not uncited, (
        f"{module.relative_to(APP_ROOT)}: {uncited} lack a 'Source:' section naming "
        "the manufacturer guide or standard clause the formula came from."
    )


@pytest.mark.parametrize(
    "module",
    _source_modules("core", "api", "domain", "ai", "ingestion", "models", "worker"),
    ids=str,
)
def test_every_module_has_a_mirrored_test_file(module: Path) -> None:
    """tests/ mirrors app/ 1:1 so a module's tests are findable without searching."""
    relative = module.relative_to(APP_ROOT)
    expected = TESTS_ROOT / relative.parent / f"test_{relative.name}"
    assert expected.exists(), f"expected {expected.relative_to(APP_ROOT)} to exist"


def test_only_the_promotion_module_writes_production() -> None:
    """Production index writes have exactly one home: app/domain/promotion.py.

    See docs/adr/0001-staging-vs-production-index.md.
    """
    # The invariant is about WRITES. Reading production is what answer
    # generation does, so the retrieval modules legitimately name the target;
    # what must stay in promotion.py is the write path. Each exemption is
    # listed with why it is allowed to say the word.
    allowed = {
        APP_ROOT / "domain" / "promotion.py",  # the one write path
        APP_ROOT / "core" / "config.py",  # declares the index names
        APP_ROOT / "ai" / "retrieval" / "client.py",  # resolves target -> name
        APP_ROOT / "ai" / "retrieval" / "hybrid_search.py",  # reads, never writes
    }
    offenders = [
        p.relative_to(APP_ROOT)
        for p in _source_modules("ingestion", "domain", "ai", "api", "worker")
        if p not in allowed and "IndexTarget.PRODUCTION" in p.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        f"{offenders} reference the production index directly. "
        "Read docs/adr/0001-staging-vs-production-index.md before adding a second write path."
    )

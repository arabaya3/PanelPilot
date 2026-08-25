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


# --- BE-004: staging/production separation, proven structurally --------------
#
# The acceptance criterion is "provable by code review of the retrieval query
# scope". These tests are that code review, run on every commit.


def test_crawler_has_no_reference_to_the_production_index() -> None:
    """The crawler must be structurally incapable of reaching production.

    Not policy — capability. It imports no production client, names no
    production target, and calls no index-write helper that could reach one.
    A crawler that *could* write production but is trusted not to is a
    different, weaker guarantee than one that cannot.
    """
    forbidden_names = ("IndexTarget.PRODUCTION", "opensearch_production_index")
    for module in _source_modules("ingestion"):
        source = module.read_text(encoding="utf-8")
        hits = [name for name in forbidden_names if name in source]
        assert not hits, (
            f"{module.relative_to(APP_ROOT)} references {hits}. "
            "app/ingestion/ writes to staging only; promotion is "
            "app/domain/promotion.py. See docs/adr/0001."
        )


def test_ingestion_cannot_reach_the_production_index_at_all() -> None:
    """Capability check, not a name check.

    An earlier version listed forbidden import names, which review defeated
    three ways against a green suite: importing the module rather than the name
    (``from app.ai.retrieval import client as _c``), building the index name
    with ``getattr``, and selecting the target by enum ordinal
    (``list(IndexTarget)[1]``). So this asserts the stronger property: nothing
    under app/ingestion/ may reach ANY symbol that can resolve or write an
    index, by any spelling.
    """
    # Reaching production requires one of these. Denying all of them to
    # ingestion is what makes the isolation structural rather than trusted.
    index_capable = {
        "index_chunk",
        "ensure_index",
        "resolve_index",
        "get_client",
        "promote_chunk",
        "promote_document",
        "IndexTarget",
    }
    # Modules whose members could be reached by attribute access after a
    # module-level import, which no name-based check would catch.
    index_capable_modules = {
        "app.ai.retrieval.client",
        "app.ai.retrieval",
        "app.domain.promotion",
        "opensearchpy",
    }

    for module in _source_modules("ingestion"):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                # `from app.ai.retrieval import client` — the name is a module.
                for alias in node.names:
                    full = f"{node.module}.{alias.name}"
                    assert alias.name not in index_capable, (
                        f"{module.relative_to(APP_ROOT)} imports {alias.name!r}, "
                        "which can reach an index. Ingestion stages only."
                    )
                    assert full not in index_capable_modules, (
                        f"{module.relative_to(APP_ROOT)} imports the module "
                        f"{full!r}; its members can reach production."
                    )
                assert node.module not in index_capable_modules, (
                    f"{module.relative_to(APP_ROOT)} imports from {node.module!r}, "
                    "which can reach production."
                )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in index_capable_modules, (
                        f"{module.relative_to(APP_ROOT)} imports {alias.name!r}, "
                        "which can reach production."
                    )


def test_ingestion_makes_no_raw_index_write() -> None:
    """A raw ``client.index(...)`` bypasses every helper-name check.

    Review reached production from ingestion with
    ``get_client().index(index=getattr(settings, "opensearch_" + "production_index"))``
    while the whole suite stayed green. Denying the import above is the real
    fix; this asserts the call shape too, so the failure is named clearly if
    someone reintroduces a client by another route.
    """
    write_methods = {"index", "bulk", "update", "delete_by_query", "reindex"}
    for module in _source_modules("ingestion"):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in write_methods
                # Only flag calls on something that looks like a client, not
                # e.g. list.index() on a plain sequence.
                and any(kw.arg == "index" for kw in node.keywords)
            ):
                raise AssertionError(
                    f"{module.relative_to(APP_ROOT)} line {node.lineno}: raw index "
                    f"write via .{node.func.attr}(). Ingestion writes staging "
                    "through the staging pipeline only."
                )


def test_the_chat_path_cannot_reach_staging() -> None:
    """The answer path must be structurally unable to read unverified content.

    Reads the source FILE and walks its AST rather than using
    ``inspect.getsource`` on the imported module: the import is cached, so a
    source-level check against the loaded module silently validates a stale
    copy. Found the hard way — an earlier version of this test passed while
    ``search()`` was redirected at staging on disk.
    """
    module = APP_ROOT / "ai" / "retrieval" / "hybrid_search.py"
    tree = ast.parse(module.read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }

    for name in ("search", "search_staging", "_search"):
        assert name in functions, f"hybrid_search.{name} is missing"

    # The public answer path takes no argument that could select an index.
    answer_args = {
        a.arg for a in functions["search"].args.args + functions["search"].args.kwonlyargs
    }
    for forbidden in ("index", "target", "verified_only"):
        assert (
            forbidden not in answer_args
        ), f"search() accepts {forbidden!r}; the answer path must not be steerable"

    def names_in(node: ast.AST) -> set[str]:
        return {
            f"{n.value.id}.{n.attr}"
            for n in ast.walk(node)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
        }

    answer_targets = names_in(functions["search"])
    assert (
        "IndexTarget.PRODUCTION" in answer_targets
    ), "search() must name the production target explicitly"
    assert (
        "IndexTarget.STAGING" not in answer_targets
    ), "search() references the staging target; the answer path must never reach it"
    # The reviewer path is the mirror image.
    reviewer_targets = names_in(functions["search_staging"])
    assert "IndexTarget.STAGING" in reviewer_targets
    assert "IndexTarget.PRODUCTION" not in reviewer_targets

    # And the shared helper must derive verified_only from the target rather
    # than hardcoding it: True breaks the reviewer path, False lets production
    # serve unverified content. Both directions have already regressed once.
    shared = ast.unparse(functions["_search"])
    assert (
        "verified_only=target is IndexTarget.PRODUCTION" in shared
    ), "_search must derive verified_only from the target, not hardcode it"


def test_only_domain_promotion_calls_the_index_write_helper() -> None:
    """``index_chunk`` is the single write helper; keep its call sites countable."""
    allowed = {APP_ROOT / "domain" / "promotion.py"}
    offenders = [
        p.relative_to(APP_ROOT)
        for p in _source_modules("ingestion", "domain", "ai", "api", "worker")
        if p not in allowed
        and p != APP_ROOT / "ai" / "retrieval" / "client.py"  # defines it
        and "index_chunk(" in p.read_text(encoding="utf-8")
    ]
    assert not offenders, f"{offenders} call index_chunk directly"


# --- BE-002: the quota gate must stay the locked one -------------------------


def test_check_free_question_allowed_is_never_used_as_the_gate() -> None:
    """The advisory check must not become the enforcement point.

    ``check_free_question_allowed`` is an unlocked read. Enforcing with it and
    then consuming separately is exactly the race that served 15 free questions
    against a limit of 5: every concurrent caller read "allowed" before any of
    them incremented. ``consume_free_question`` takes the decision under a row
    lock and raises on its own.

    A docstring saying so is not a guardrail — this is. BE-008 wires the quota
    into the diagnosis path, and the function whose name reads like a
    permission check is the one it will reach for first.
    """
    callers = [
        p.relative_to(APP_ROOT)
        for p in _source_modules("domain", "api", "worker", "ai", "ingestion")
        if p != APP_ROOT / "domain" / "auth.py"
        and "check_free_question_allowed" in p.read_text(encoding="utf-8")
    ]
    assert not callers, (
        f"{callers} call check_free_question_allowed. It is advisory only — a "
        "friendlier pre-flight message, never the gate. Call "
        "consume_free_question and let it raise."
    )

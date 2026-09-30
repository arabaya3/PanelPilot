"""Tests for `app/api/v1/routes/plc.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

The validation logic itself is exercised in `app/tests/ai/plc/`. What is under
test here is the HTTP contract and the one behaviour this layer genuinely
owns: what happens when validation *itself* falls over.

That case is the acceptance criterion's real edge. A validator that raises has
not passed anything, and code returned with nothing said about it reads as
approval — so the endpoint must say "not checked" out loud rather than stay
quiet.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai.plc import writer as plc_writer
from app.ai.plc.generation import GenerationError
from app.api import deps
from app.api.v1.routes import plc as plc_route
from app.core.db import get_session
from app.core.errors import install_exception_handlers
from app.domain import model_budget
from app.domain import plc as plc_domain
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.plc import LadderContact, LadderRung, PlcDialect, ValidationStatus

VALID_ST = """PROGRAM MotorStart
VAR_INPUT
    StartButton : BOOL;
END_VAR
VAR_OUTPUT
    MotorRun : BOOL;
END_VAR
    IF StartButton THEN
        MotorRun := TRUE;
    END_IF;
END_PROGRAM"""

UNCLOSED_RUNG = """PROGRAM P
VAR
    A : BOOL;
    B : BOOL;
END_VAR
    IF A THEN
        B := TRUE;
END_PROGRAM"""

UNDEFINED_TAG = """PROGRAM P
VAR
    A : BOOL;
END_VAR
    A := NeverDeclared;
END_PROGRAM"""

TYPE_MISMATCH = """PROGRAM P
VAR
    Counter : INT;
END_VAR
    Counter := TRUE;
END_PROGRAM"""


class _Session:
    """Records whether the request committed its charge."""

    def commit(self) -> None:
        """Accept the commit."""


@pytest.fixture(name="budget", autouse=True)
def _budget(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The monthly budget is its own tested module; here it records charges."""
    charged: list[str] = []

    def _charge(**kwargs: str) -> int:
        charged.append(kwargs["tenant_id"])
        return len(charged)

    monkeypatch.setattr(model_budget, "charge_model_call", _charge)
    return charged


@pytest.fixture(name="client")
def _client() -> Iterator[TestClient]:
    """A client bound to just this router, signed in.

    Review takes no user; generation does, and is rate-limited -- the limit is
    its own tested dependency and not the subject here.
    """
    app = FastAPI()
    app.include_router(plc_route.router, prefix="/plc")
    app.dependency_overrides[deps.get_current_user] = lambda: CurrentUser(
        id="u", email="e@example.com", tenant_id="t", roles=frozenset({Role.ENGINEER})
    )
    app.dependency_overrides[deps.enforce_trial_rate_limit] = lambda: None
    app.dependency_overrides[get_session] = _Session
    install_exception_handlers(app)

    with TestClient(app) as test_client:
        yield test_client


# --- review: the acceptance criterion -----------------------------------------


def test_valid_code_passes_cleanly(client: TestClient) -> None:
    response = client.post("/plc/review", json={"source": VALID_ST})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "valid"
    assert not [f for f in body["findings"] if f["severity"] == "error"]


@pytest.mark.parametrize(
    "source",
    [UNCLOSED_RUNG, UNDEFINED_TAG, TYPE_MISMATCH],
    ids=["unclosed rung", "undefined tag reference", "type mismatch"],
)
def test_known_invalid_code_returns_failures_not_a_false_pass(
    client: TestClient, source: str
) -> None:
    # The acceptance criterion, stated almost verbatim: "A request for
    # known-invalid code returns validation failures with location info, not a
    # false pass."
    response = client.post("/plc/review", json={"source": source})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "invalid"
    assert body["findings"]


def test_a_syntax_failure_carries_location_information(client: TestClient) -> None:
    # "with location info". A verdict that says only "invalid" sends an
    # engineer hunting through a program by hand.
    response = client.post("/plc/review", json={"source": UNCLOSED_RUNG})

    findings = response.json()["findings"]
    assert findings[0]["line"] is not None


def test_a_tag_failure_names_the_symbol(client: TestClient) -> None:
    # Location for a semantic finding is the name, not a line: the tag may be
    # wrong in three places and right in the declaration.
    response = client.post("/plc/review", json={"source": UNDEFINED_TAG})

    messages = " ".join(f["message"] for f in response.json()["findings"])
    assert "NeverDeclared" in messages


def test_the_review_endpoint_reports_which_dialect_it_assumed(client: TestClient) -> None:
    response = client.post(
        "/plc/review",
        json={"source": VALID_ST, "dialect": PlcDialect.ROCKWELL_ST.value},
    )

    assert response.json()["dialect"] == "rockwell-st"


def test_unsupported_constructs_come_back_incomplete(client: TestClient) -> None:
    # Not a pass and not a failure. The endpoint carries AI-009's third answer
    # through rather than flattening it into one of the other two.
    response = client.post(
        "/plc/review",
        json={
            "source": "PROGRAM P\nVAR\n X : INT;\nEND_VAR\n CASE X OF\n 1: X := 2;\n END_CASE;\nEND_PROGRAM"
        },
    )

    assert response.json()["status"] == "incomplete"


def test_empty_source_is_rejected_by_the_schema(client: TestClient) -> None:
    response = client.post("/plc/review", json={"source": ""})

    assert response.status_code == 422


def test_an_oversized_source_is_rejected(client: TestClient) -> None:
    # Bounded because it is client-supplied and goes into a parser.
    response = client.post("/plc/review", json={"source": "A" * 100_001})

    assert response.status_code == 422


# --- the edge case this layer owns --------------------------------------------


def test_a_validator_that_raises_reports_unavailable_not_a_pass(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # BE-010's stated edge case. "If validation itself errors out (rather than
    # returning a valid fail result), the endpoint returns an explicit
    # 'validation unavailable' state — never falls back to returning code as if
    # it passed."
    def _explode(source: str, dialect: PlcDialect) -> None:
        del source, dialect
        raise RuntimeError("the parser fell over")

    monkeypatch.setattr(plc_domain, "validate_plc_code", _explode)

    response = client.post("/plc/review", json={"source": VALID_ST})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == ValidationStatus.INCOMPLETE.value
    assert body["checked_by"] == plc_domain.VALIDATION_UNAVAILABLE


def test_an_unavailable_validation_is_distinguishable_from_an_unsupported_dialect(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Both are INCOMPLETE and both are untrusted, which is correct. But one is
    # a known gap and the other is a defect somebody has to fix, and a
    # maintainer reading the response should be able to tell which.
    def _explode(source: str, dialect: PlcDialect) -> None:
        del source, dialect
        raise RuntimeError("boom")

    monkeypatch.setattr(plc_domain, "validate_plc_code", _explode)
    broken = client.post("/plc/review", json={"source": VALID_ST}).json()

    monkeypatch.undo()
    unsupported = client.post(
        "/plc/review",
        json={
            "source": "FUNCTION_BLOCK FB\nVAR\n A : BOOL;\nEND_VAR\n A := TRUE;\nEND_FUNCTION_BLOCK"
        },
    ).json()

    assert broken["status"] == unsupported["status"] == "incomplete"
    assert broken["checked_by"] != unsupported["checked_by"]


def test_a_validator_that_raises_still_blocks_ready(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The property that matters more than the status string: whatever went
    # wrong, the caller must not be able to read this as approved.
    def _explode(source: str, dialect: PlcDialect) -> None:
        del source, dialect
        raise RuntimeError("boom")

    monkeypatch.setattr(plc_domain, "validate_plc_code", _explode)

    body = client.post("/plc/review", json={"source": VALID_ST}).json()

    assert body["status"] != "valid"
    assert any(f["code"] == plc_domain.VALIDATION_UNAVAILABLE for f in body["findings"])


# --- generate -----------------------------------------------------------------


def test_generation_returns_the_writers_code_with_the_validators_verdict(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plc_writer, "write_source", lambda _request: VALID_ST)

    response = client.post("/plc/generate", json={"description": "start a motor"})

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == VALID_ST
    assert body["validation"]["status"] == ValidationStatus.VALID.value


def test_generation_needs_a_session_and_is_rate_limited() -> None:
    """Every call is a paid model request; the trial session carries the limit.

    Read off the route rather than exercised: the client fixture overrides
    both dependencies, and resolving them for real needs a database.
    """
    route = next(r for r in plc_route.router.routes if getattr(r, "path", None) == "/generate")
    calls = {dep.call for dep in route.dependant.dependencies}  # type: ignore[attr-defined]
    assert deps.get_current_user in calls
    assert deps.enforce_trial_rate_limit in calls

    review = next(r for r in plc_route.router.routes if getattr(r, "path", None) == "/review")
    assert deps.get_current_user not in {
        dep.call for dep in review.dependant.dependencies  # type: ignore[attr-defined]
    }


def test_a_program_the_model_could_not_write_is_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _nothing(_request: object) -> str:
        raise GenerationError("the model returned no program")

    monkeypatch.setattr(plc_writer, "write_source", _nothing)

    response = client.post("/plc/generate", json={"description": "start a motor"})

    assert response.status_code == 422
    assert "no program" in response.json()["detail"]
    assert "source" not in response.json()


def test_an_unreachable_model_is_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _down(_request: object) -> str:
        raise ConnectionError("provider down")

    monkeypatch.setattr(plc_writer, "write_source", _down)

    response = client.post("/plc/generate", json={"description": "start a motor"})

    assert response.status_code == 503


def test_generation_validates_the_request_shape(client: TestClient) -> None:
    response = client.post("/plc/generate", json={"description": ""})

    assert response.status_code == 422


def test_generation_accepts_the_documented_languages(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    rung = LadderRung(
        comment="Run",
        elements=[LadderContact(tag="Start", kind="no"), LadderContact(tag="Stop", kind="nc")],
        output=LadderContact(tag="Motor", kind="coil"),
    )
    monkeypatch.setattr(plc_writer, "write_ladder", lambda _request: [rung])

    response = client.post(
        "/plc/generate",
        json={"description": "start a motor", "language": "ladder"},
    )

    assert response.status_code == 200
    assert response.json()["rungs"][0]["output"]["tag"] == "Motor"


def test_an_unknown_language_is_rejected_by_the_schema(client: TestClient) -> None:
    response = client.post(
        "/plc/generate",
        json={"description": "start a motor", "language": "flowchart"},
    )

    assert response.status_code == 422


def test_an_unknown_dialect_is_rejected_by_the_schema(client: TestClient) -> None:
    response = client.post(
        "/plc/review",
        json={"source": VALID_ST, "dialect": "mitsubishi-whatever"},
    )

    assert response.status_code == 422


def test_each_generation_is_charged_to_the_tenants_month(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, budget: list[str]
) -> None:
    monkeypatch.setattr(plc_writer, "write_source", lambda _request: VALID_ST)
    client.post("/plc/generate", json={"description": "start a motor"})
    assert budget == ["t"]


def test_a_spent_month_is_429_and_reaches_no_model(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    written: list[object] = []

    def _spent(**_kwargs: object) -> int:
        raise model_budget.ModelBudgetExceededError("used its 1000 model calls")

    monkeypatch.setattr(model_budget, "charge_model_call", _spent)
    monkeypatch.setattr(plc_writer, "write_source", lambda request: written.append(request))

    response = client.post("/plc/generate", json={"description": "start a motor"})

    assert response.status_code == 429
    assert written == []

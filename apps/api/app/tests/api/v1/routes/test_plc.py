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

from app.api.v1.routes import plc as plc_route
from app.domain import plc as plc_domain
from app.models.schemas.plc import PlcDialect, ValidationStatus

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


@pytest.fixture(name="client")
def _client() -> Iterator[TestClient]:
    """A client bound to just this router.

    No auth override: these endpoints take no user, so binding one would test
    a dependency the routes do not declare.
    """
    app = FastAPI()
    app.include_router(plc_route.router, prefix="/plc")

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


def test_generation_reports_that_it_is_not_wired_yet(client: TestClient) -> None:
    # Refused rather than stubbed. A plausible stub would make the endpoint
    # look finished and hand a caller code no model wrote.
    response = client.post("/plc/generate", json={"description": "start a motor"})

    assert response.status_code == 422
    assert "not yet wired" in response.json()["detail"]


def test_generation_never_returns_code_no_model_wrote(client: TestClient) -> None:
    # The property, not just the message. A stub returning plausible-looking
    # ST would satisfy a test that only checks the refusal text, and would
    # hand a caller a program that no model produced and no requirement
    # described — wearing whatever verdict the validator gave it.
    #
    # Mutation-checked: replacing the writer with a canned valid program is
    # caught here and nowhere else.
    response = client.post("/plc/generate", json={"description": "start a motor"})

    assert response.status_code != 200
    assert "source" not in response.json()


def test_generation_validates_the_request_shape(client: TestClient) -> None:
    response = client.post("/plc/generate", json={"description": ""})

    assert response.status_code == 422


def test_generation_accepts_the_documented_languages(client: TestClient) -> None:
    # Reaches the unwired writer rather than being rejected as an unknown
    # language, which is what proves the routing is right.
    response = client.post(
        "/plc/generate",
        json={"description": "start a motor", "language": "ladder"},
    )

    assert response.status_code == 422
    assert "ladder generation is not yet wired" in response.json()["detail"]


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

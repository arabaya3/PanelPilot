"""Evaluating a rule reproducibly, and keeping draft results apart.

A rule (``registry.RuleVersion``) is evaluated by the evaluator it names,
registered here by id and version. An evaluator is a pure function from typed
inputs to typed outputs: no I/O, clock or randomness. Inputs and outputs are
canonical (decimals as normalised strings, integers, booleans, text; no
floats), checked against the rule's declared ports and units, and digested,
so an :class:`EvaluationRecord` names exactly what went in and came out and
:func:`reproduce` can check it.

**Status.** Never taken on trust: :func:`evaluate` and :func:`reproduce`
derive a rule's effective status from the server's decisions handed in as a
``ReviewAnchor`` (:func:`rule_status`), and :func:`reproduce` refuses a record
whose recorded status is not the status the decisions give now
(``rule_status_changed``).

**Modes.** ``reviewed`` is allowed only for a rule whose effective status is
``approved`` (a decision recorded on the server). ``draft`` is allowed for a
``draft`` or ``unreviewed`` rule and gives a :class:`DraftEvaluation`:
labelled, flagged, never a value for a design (:meth:`DraftEvaluation.as_design_value`
refuses), and never described as approved. A ``rejected`` or ``withdrawn``
rule is not evaluated in either mode: not even as a draft.

No evaluator is shipped: :func:`shipped_evaluators` is empty.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from app.design import snapshot
from app.design.model import anchoring
from app.design.rules import registry

Mode = Literal["reviewed", "draft"]

#: What every draft result is labelled with.
DRAFT_LABEL = "DRAFT EVALUATION - not reviewed engineering guidance"

#: The flags every draft result carries.
DRAFT_FLAGS = ("draft_evaluation", "not_engineering_guidance")

#: A canonical value: a decimal's normalised text, an int, a bool or text.
Value = str | int | bool

Function = Callable[[Mapping[str, Value]], Mapping[str, Any]]


class EvaluationRefusedError(Exception):
    """An evaluation that may not run, with a stable code."""

    def __init__(self, message: str, code: str) -> None:
        """Carry a message and a stable refusal code."""
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Evaluator:
    """A registered, versioned, pure function."""

    id: str
    version: str
    function: Function


class Evaluators:
    """Evaluators by ``(id, version)``."""

    def __init__(self, evaluators: tuple[Evaluator, ...] = ()) -> None:
        """Register each of ``evaluators``."""
        self._by: dict[tuple[str, str], Evaluator] = {}
        for evaluator in evaluators:
            self.add(evaluator)

    def add(self, evaluator: Evaluator) -> None:
        """Register one; an id and version is registered once."""
        key = (evaluator.id, evaluator.version)
        if key in self._by:
            raise ValueError(f"{evaluator.id} {evaluator.version} is registered already")
        self._by[key] = evaluator

    def get(self, evaluator_id: str, version: str) -> Evaluator | None:
        """The evaluator, or ``None``."""
        return self._by.get((evaluator_id, version))


def shipped_evaluators() -> Evaluators:
    """The evaluators PanelPilot ships: none in M3."""
    return Evaluators()


def _canonical(port: registry.Port, given: Any, where: str) -> Value:
    """A port value in canonical form; refused when of the wrong kind."""
    if isinstance(given, float):
        raise EvaluationRefusedError(f"{where} {port.name} is a float", "evaluation_value_invalid")
    if port.kind == "decimal":
        if isinstance(given, bool) or not isinstance(given, (str, int)):
            raise EvaluationRefusedError(
                f"{where} {port.name} is not a decimal", "evaluation_value_invalid"
            )
        try:
            number = Decimal(str(given))
        except InvalidOperation as exc:
            raise EvaluationRefusedError(
                f"{where} {port.name} is not a decimal", "evaluation_value_invalid"
            ) from exc
        if not number.is_finite():
            raise EvaluationRefusedError(
                f"{where} {port.name} is not finite", "evaluation_value_invalid"
            )
        text = format(number.normalize(), "f")
        return "0" if Decimal(text) == 0 else text
    if port.kind == "integer":
        if isinstance(given, bool) or not isinstance(given, int):
            raise EvaluationRefusedError(
                f"{where} {port.name} is not an integer", "evaluation_value_invalid"
            )
        return given
    if port.kind == "boolean":
        if not isinstance(given, bool):
            raise EvaluationRefusedError(
                f"{where} {port.name} is not a boolean", "evaluation_value_invalid"
            )
        return given
    if not isinstance(given, str):
        raise EvaluationRefusedError(f"{where} {port.name} is not text", "evaluation_value_invalid")
    return given


def _typed(
    ports: tuple[registry.Port, ...], given: Mapping[str, Any], where: str
) -> dict[str, dict[str, Any]]:
    """Values by port name, each ``{"value", "unit"}``; exactly the declared ports.

    ``given`` maps a name to ``{"value": ..., "unit": ...}``: the unit must be
    the declared one (compared as text, never converted).
    """
    names = {port.name for port in ports}
    if set(given) != names:
        raise EvaluationRefusedError(
            f"{where}s must be exactly {sorted(names)}", "evaluation_ports_mismatch"
        )
    out: dict[str, dict[str, Any]] = {}
    for port in ports:
        item = given[port.name]
        if not isinstance(item, Mapping) or set(item) != {"value", "unit"}:
            raise EvaluationRefusedError(
                f"{where} {port.name} is {{value, unit}}", "evaluation_value_invalid"
            )
        if item["unit"] != port.unit:
            raise EvaluationRefusedError(
                f"{where} {port.name} is in {item['unit']!r}, not {port.unit!r}",
                "evaluation_unit_mismatch",
            )
        out[port.name] = {"value": _canonical(port, item["value"], where), "unit": port.unit}
    return out


@dataclass(frozen=True)
class EvaluationRecord:
    """What reproduces an evaluation: what was evaluated, by what, on what, giving what."""

    rule_ref: str
    rule_digest: str
    evaluator_id: str
    evaluator_version: str
    input_digest: str
    output_digest: str
    mode: Mode
    rule_status: str

    def as_json(self) -> dict[str, str]:
        """The record as JSON."""
        return {
            "rule_ref": self.rule_ref,
            "rule_digest": self.rule_digest,
            "evaluator_id": self.evaluator_id,
            "evaluator_version": self.evaluator_version,
            "input_digest": self.input_digest,
            "output_digest": self.output_digest,
            "mode": self.mode,
            "rule_status": self.rule_status,
        }


@dataclass(frozen=True)
class Evaluation:
    """The result of a ``reviewed`` evaluation of an approved rule."""

    record: EvaluationRecord
    inputs: Mapping[str, Mapping[str, Any]]
    outputs: Mapping[str, Mapping[str, Any]]

    def describe(self) -> str:
        """A plain line naming the rule and its server-recorded status."""
        return f"{self.record.rule_ref} evaluated; rule status {self.record.rule_status}"


@dataclass(frozen=True)
class DraftEvaluation:
    """The result of a ``draft`` evaluation: labelled, flagged, never a design value."""

    record: EvaluationRecord
    inputs: Mapping[str, Mapping[str, Any]]
    outputs: Mapping[str, Mapping[str, Any]]
    label: str = DRAFT_LABEL
    flags: tuple[str, ...] = field(default=DRAFT_FLAGS)

    def describe(self) -> str:
        """A plain line, always starting with the draft label."""
        return f"{self.label}: {self.record.rule_ref} (rule status {self.record.rule_status})"

    def as_design_value(self) -> None:
        """Refused: a draft result is never stored as an engineering value.

        Raises:
            EvaluationRefusedError: Always, ``draft_not_storable``.
        """
        raise EvaluationRefusedError(
            "a draft evaluation is never stored as an engineering value", "draft_not_storable"
        )


def rule_status(anchor: anchoring.ReviewAnchor, rule: registry.RuleVersion) -> str:
    """A rule's effective status under the server's decisions: its decision, else its declared one.

    ``withdrawn`` when the newest decision in a scope withdrew it and no
    other decides it.
    """
    decided = anchor.deciding("rule", rule.ref, rule.digest)
    if decided is None:
        key = ("rule", rule.ref, rule.digest)
        withdrawn = any(
            d is not None and d.decision == "withdrawn"
            for d in (anchor.tenant.get(key), anchor.global_.get(key))
        )
        return "withdrawn" if withdrawn else rule.declared_status
    return registry.effective_status(rule, decided.decision)


def evaluate(
    rule: registry.RuleVersion,
    inputs: Mapping[str, Any],
    *,
    anchor: anchoring.ReviewAnchor,
    mode: Mode,
    evaluators: Evaluators,
) -> Evaluation | DraftEvaluation:
    """Evaluate a rule on typed inputs.

    Args:
        rule: The rule version.
        inputs: By port name, ``{"value": ..., "unit": ...}``.
        anchor: The server's decisions that apply (``engineering_reviews.anchor_for``);
            the rule's status is derived from them (:func:`rule_status`).
        mode: ``reviewed`` or ``draft``.
        evaluators: Where its evaluator is registered.

    Returns:
        An :class:`Evaluation` (``reviewed``) or a :class:`DraftEvaluation`.

    Raises:
        EvaluationRefusedError: ``rule_not_approved`` for ``reviewed`` on a rule not
            approved; ``rule_not_evaluable`` for a rejected or withdrawn rule;
            ``evaluator_unknown``; ``evaluation_mode_unknown``; or a port, unit
            or value refusal.
    """
    if mode not in ("reviewed", "draft"):
        raise EvaluationRefusedError(f"unknown mode {mode!r}", "evaluation_mode_unknown")
    status = rule_status(anchor, rule)
    if status not in registry.RULE_STATUSES:
        raise EvaluationRefusedError(f"unknown status {status!r}", "rule_not_evaluable")
    if status in ("rejected", "withdrawn"):
        raise EvaluationRefusedError(
            f"{rule.ref} is {status}; it is not evaluated", "rule_not_evaluable"
        )
    if mode == "reviewed" and status != "approved":
        raise EvaluationRefusedError(
            f"{rule.ref} is {status}; only a draft evaluation is allowed", "rule_not_approved"
        )
    evaluator = evaluators.get(rule.evaluator_id, rule.evaluator_version)
    if evaluator is None:
        raise EvaluationRefusedError(
            f"no evaluator {rule.evaluator_id} {rule.evaluator_version}", "evaluator_unknown"
        )
    typed_in = _typed(rule.inputs, inputs, "input")
    values = {name: item["value"] for name, item in typed_in.items()}
    produced = evaluator.function(dict(values))
    typed_out = _typed(rule.outputs, produced, "output")
    record = EvaluationRecord(
        rule_ref=rule.ref,
        rule_digest=rule.digest,
        evaluator_id=evaluator.id,
        evaluator_version=evaluator.version,
        input_digest=snapshot.sha256_of(typed_in),
        output_digest=snapshot.sha256_of(typed_out),
        mode=mode,
        rule_status=status,
    )
    if mode == "draft":
        return DraftEvaluation(record=record, inputs=typed_in, outputs=typed_out)
    return Evaluation(record=record, inputs=typed_in, outputs=typed_out)


def reproduce(
    record: EvaluationRecord,
    inputs: Mapping[str, Any],
    *,
    rules: registry.Registry,
    anchor: anchoring.ReviewAnchor,
    evaluators: Evaluators,
) -> bool:
    """Whether re-running a recorded evaluation gives the same output digest.

    The rule must still be registered under the recorded ref with the
    recorded digest, and the inputs must digest to the recorded input digest.
    Re-run in the recorded mode, under the status the server's decisions
    give the rule now, which must be the recorded one.

    Args:
        record: The record.
        inputs: The inputs, as given to :func:`evaluate`.
        rules: Where the rule is registered.
        anchor: The server's decisions that apply now.
        evaluators: Where its evaluator is.

    Returns:
        ``True`` exactly when the rule, inputs and outputs all match.

    Raises:
        EvaluationRefusedError: ``rule_status_changed`` when the recorded
            status is not the one the decisions give now; or any refusal of
            :func:`evaluate`.
    """
    rule = rules.get(record.rule_ref)
    if rule is None or rule.digest != record.rule_digest:
        return False
    if (rule.evaluator_id, rule.evaluator_version) != (
        record.evaluator_id,
        record.evaluator_version,
    ):
        return False
    status = rule_status(anchor, rule)
    if status != record.rule_status:
        raise EvaluationRefusedError(
            f"{rule.ref} was evaluated as {record.rule_status}; the server's decisions "
            f"now make it {status}",
            "rule_status_changed",
        )
    again = evaluate(rule, inputs, anchor=anchor, mode=record.mode, evaluators=evaluators)
    return (
        again.record.input_digest == record.input_digest
        and again.record.output_digest == record.output_digest
    )

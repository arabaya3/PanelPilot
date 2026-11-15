"""The rule registry: what a rule is, how it is named, digested and applied.

Pure: no I/O, no clock, no database. A rule version is data describing a
registered evaluator (``app.design.rules.evaluation``): its typed inputs and
outputs, the model data it applies to, and where it came from. Its review
status in code is only ``draft`` or ``unreviewed``; ``approved``,
``rejected`` and ``withdrawn`` exist only as the result of a decision
recorded on the server (:func:`effective_status`).

**Nothing is shipped.** :data:`SHIPPED` is empty: PanelPilot carries no rule
in this registry yet, and describing the existing calculation functions here
is left to a reviewed step (``docs/architecture/m3-rule-registry-and-review.md``,
O-9). Rules with the ``synthetic`` scope exist for tests only and may never be
shipped.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.design import snapshot

#: A rule reference: ``<scope>/<id>/v<version>``, lower case, no ``@``.
RULE_REF_PATTERN = re.compile(
    r"^([a-z][a-z0-9-]{0,39})/([a-z0-9][a-z0-9_.-]{0,99})/v([1-9][0-9]{0,8})$"
)

#: The scope of rules that exist for tests only.
SYNTHETIC_SCOPE = "synthetic"

#: Every status a rule may have, declared or effective.
RULE_STATUSES = ("draft", "unreviewed", "approved", "rejected", "withdrawn")

#: The statuses a rule may declare in code. The rest are decided on the server.
DECLARED_STATUSES = ("draft", "unreviewed")

#: What a server-side decision may say (``engineering_reviews``).
DECISIONS = ("approved", "rejected", "withdrawn")

#: The kinds of value a port carries.
VALUE_KINDS = ("decimal", "integer", "boolean", "text")

#: The operators an applicability predicate may use.
PREDICATE_OPS = ("eq", "ne", "in", "present", "absent")

_PORT_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_EVALUATOR_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,99}$")
_PATH = re.compile(r"^[a-z_][a-z0-9_]{0,63}(\.[a-z_][a-z0-9_]{0,63}){0,15}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class RuleDefinitionError(ValueError):
    """A rule version that is not well formed."""


@dataclass(frozen=True)
class Port:
    """A typed input or output. The unit is data, compared as text, never converted."""

    name: str
    kind: str
    unit: str | None = None


@dataclass(frozen=True)
class Provenance:
    """Where a rule came from. What is not known stays ``None`` or empty.

    Attributes:
        author: Who wrote it, as stated; ``None`` when not recorded.
        created: When, as an ISO date; ``None`` when not recorded.
        sources: Source documents, named by reference only (never quoted).
        statement: A plain statement of what this provenance is and is not.
    """

    author: str | None
    created: str | None
    sources: tuple[str, ...]
    statement: str


@dataclass(frozen=True)
class Predicate:
    """One condition on model data: ``path`` ``op`` ``value``.

    ``path`` is a dotted path through mappings of the model data; ``value`` is
    a JSON scalar, or for ``in`` a tuple of them; ``present`` and ``absent``
    take none.
    """

    path: str
    op: str
    value: Any = None


@dataclass(frozen=True)
class RuleVersion:
    """One version of a rule.

    Attributes:
        ref: ``<scope>/<id>/v<version>``.
        title: A plain name.
        evaluator_id: The registered evaluator that computes it.
        evaluator_version: That evaluator's version.
        inputs: Its inputs, by name.
        outputs: Its outputs, by name.
        applicability: Predicates over model data that must all hold.
        provenance: Where it came from.
        declared_status: ``draft`` or ``unreviewed``.
        synthetic: For tests only: inert, of no engineering meaning.
    """

    ref: str
    title: str
    evaluator_id: str
    evaluator_version: str
    inputs: tuple[Port, ...]
    outputs: tuple[Port, ...]
    applicability: tuple[Predicate, ...]
    provenance: Provenance
    declared_status: str = "draft"
    synthetic: bool = False

    def __post_init__(self) -> None:
        """Refuse a malformed version (:func:`check`)."""
        check(self)

    @property
    def scope(self) -> str:
        """The scope part of the ref."""
        return self.ref.split("/", 1)[0]

    @property
    def digest(self) -> str:
        """The SHA-256 of this version's canonical JSON."""
        return snapshot.sha256_of(dump(self))


def _port(port: Port, where: str) -> None:
    if not _PORT_NAME.fullmatch(port.name):
        raise RuleDefinitionError(f"{where} {port.name!r} is not a port name")
    if port.kind not in VALUE_KINDS:
        raise RuleDefinitionError(f"{where} {port.name!r} has kind {port.kind!r}")
    if port.unit is not None and (not port.unit.strip() or len(port.unit) > 40):
        raise RuleDefinitionError(f"{where} {port.name!r} has a blank or long unit")
    if port.kind in {"boolean", "text"} and port.unit is not None:
        raise RuleDefinitionError(f"{where} {port.name!r}: a {port.kind} carries no unit")


def _scalar(value: Any) -> bool:
    return value is None or (isinstance(value, (str, int, bool)) and not isinstance(value, float))


def _predicate(predicate: Predicate) -> None:
    if not _PATH.fullmatch(predicate.path):
        raise RuleDefinitionError(f"predicate path {predicate.path!r} is not a dotted path")
    if predicate.op not in PREDICATE_OPS:
        raise RuleDefinitionError(f"predicate op {predicate.op!r} is unknown")
    if predicate.op in {"present", "absent"}:
        if predicate.value is not None:
            raise RuleDefinitionError(f"{predicate.op} takes no value")
    elif predicate.op == "in":
        if not isinstance(predicate.value, tuple) or not all(map(_scalar, predicate.value)):
            raise RuleDefinitionError("in takes a tuple of scalars")
    elif not _scalar(predicate.value):
        raise RuleDefinitionError(f"{predicate.op} takes a scalar")


def check(rule: RuleVersion) -> None:
    """Refuse a rule version that is not well formed.

    Args:
        rule: The rule version.

    Raises:
        RuleDefinitionError: For a malformed ref, evaluator id, port or
            predicate, duplicate port names, a declared status other than
            ``draft`` or ``unreviewed``, a synthetic rule outside the
            ``synthetic`` scope (or the reverse), or a blank provenance
            statement.
    """
    if "@" in rule.ref or not RULE_REF_PATTERN.fullmatch(rule.ref):
        raise RuleDefinitionError(f"{rule.ref!r} is not <scope>/<id>/v<version>")
    if rule.synthetic != (rule.ref.split("/", 1)[0] == SYNTHETIC_SCOPE):
        raise RuleDefinitionError("a synthetic rule, and only one, has the synthetic scope")
    if not rule.title.strip() or len(rule.title) > 200:
        raise RuleDefinitionError("a rule has a plain title")
    if not _EVALUATOR_ID.fullmatch(rule.evaluator_id) or not rule.evaluator_version.strip():
        raise RuleDefinitionError("a rule names a registered evaluator and its version")
    if rule.declared_status not in DECLARED_STATUSES:
        raise RuleDefinitionError(
            f"a rule declares only {' or '.join(DECLARED_STATUSES)}; "
            "approval is recorded on the server"
        )
    for where, ports in (("input", rule.inputs), ("output", rule.outputs)):
        names = [port.name for port in ports]
        if len(set(names)) != len(names):
            raise RuleDefinitionError(f"{where} names repeat")
        for port in ports:
            _port(port, where)
    if not rule.outputs:
        raise RuleDefinitionError("a rule gives at least one output")
    for predicate in rule.applicability:
        _predicate(predicate)
    if not rule.provenance.statement.strip():
        raise RuleDefinitionError("a provenance says plainly what it is")


def dump(rule: RuleVersion) -> dict[str, Any]:
    """A rule version's canonical JSON, every list sorted.

    Args:
        rule: The rule version.

    Returns:
        A JSON-shaped dict.
    """

    def ports(items: Sequence[Port]) -> list[dict[str, Any]]:
        return [
            {"name": p.name, "kind": p.kind, "unit": p.unit}
            for p in sorted(items, key=lambda p: p.name)
        ]

    return {
        "ref": rule.ref,
        "title": rule.title,
        "evaluator": {"id": rule.evaluator_id, "version": rule.evaluator_version},
        "inputs": ports(rule.inputs),
        "outputs": ports(rule.outputs),
        "applicability": sorted(
            (
                {
                    "path": p.path,
                    "op": p.op,
                    "value": list(p.value) if isinstance(p.value, tuple) else p.value,
                }
                for p in rule.applicability
            ),
            key=snapshot.canonical_json,
        ),
        "provenance": {
            "author": rule.provenance.author,
            "created": rule.provenance.created,
            "sources": sorted(rule.provenance.sources),
            "statement": rule.provenance.statement,
        },
        "declared_status": rule.declared_status,
        "synthetic": rule.synthetic,
    }


_MISSING = object()


def _lookup(data: Any, path: str) -> Any:
    current = data
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return _MISSING
        current = current[part]
    return current


def applies(rule: RuleVersion, data: Mapping[str, Any]) -> bool:
    """Whether every applicability predicate holds over model data.

    Only mappings are walked: a path through a list, or to a missing key,
    is absent. Values compare by equality and type (``True`` is not ``1``).

    Args:
        rule: The rule version.
        data: Model data, JSON-shaped.

    Returns:
        ``True`` when all hold (a rule with no predicate always applies).
    """

    def same(a: Any, b: Any) -> bool:
        return type(a) is type(b) and a == b

    for predicate in rule.applicability:
        found = _lookup(data, predicate.path)
        if predicate.op == "present":
            ok = found is not _MISSING
        elif predicate.op == "absent":
            ok = found is _MISSING
        elif found is _MISSING:
            ok = False
        elif predicate.op == "eq":
            ok = same(found, predicate.value)
        elif predicate.op == "ne":
            ok = not same(found, predicate.value)
        else:
            ok = any(same(found, v) for v in predicate.value)
        if not ok:
            return False
    return True


def effective_status(rule: RuleVersion, decision: str | None) -> str:
    """A rule's status given the current server-side decision for its digest.

    Args:
        rule: The rule version.
        decision: ``approved``, ``rejected`` or ``withdrawn`` from a decision
            recorded for exactly this rule's ref and digest; ``None`` when
            there is none.

    Returns:
        The decision, or the rule's declared status when there is none.

    Raises:
        ValueError: For an unknown decision.
    """
    if decision is None:
        return rule.declared_status
    if decision not in DECISIONS:
        raise ValueError(f"unknown decision {decision!r}")
    return decision


def is_digest(value: str) -> bool:
    """Whether a string is 64 lower-case hex digits."""
    return bool(_DIGEST.fullmatch(value))


class Registry:
    """Rule versions by ref; a ref is registered once."""

    def __init__(self, rules: Sequence[RuleVersion] = ()) -> None:
        """Register each of ``rules``."""
        self._rules: dict[str, RuleVersion] = {}
        for rule in rules:
            self.add(rule)

    def add(self, rule: RuleVersion) -> None:
        """Register a rule version.

        Raises:
            RuleDefinitionError: If its ref is registered already.
        """
        if rule.ref in self._rules:
            raise RuleDefinitionError(f"{rule.ref} is registered already")
        self._rules[rule.ref] = rule

    def get(self, ref: str) -> RuleVersion | None:
        """The rule version under ``ref``, or ``None``."""
        return self._rules.get(ref)

    def refs(self) -> list[str]:
        """Every registered ref, sorted."""
        return sorted(self._rules)


#: The rule versions PanelPilot ships. None: see the module docstring.
SHIPPED: tuple[RuleVersion, ...] = ()


def shipped() -> Registry:
    """The registry of shipped rules (empty in M3)."""
    return Registry(SHIPPED)

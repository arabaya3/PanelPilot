"""Saved design projects: save, list, open, revise, approve, export, delete.

A project is kept as what the engineer entered, one immutable revision per
save (``app.models.tables.design_projects``). Each save also designs the
request on the server and stores that design (its snapshot), the snapshot's
canonical SHA-256, the profile hash, the rules and catalogue versions, and the
findings (``app.design.integrity``); a design the client sent is never stored.

An engineer approves a revision once, by name, with the revision row locked;
the approval records the snapshot hash it approved and the applicable policy it
was granted under (the resolved profile hash, the rules and catalogue versions
and the findings classification), and counts only while each equals the
revision's and today's. The tenant's live company settings are not part of
that policy: a saved revision carries its own. The approver's display name is
frozen with the approval, and every export prints that. A revision with no snapshot (saved before snapshots
were kept, or whose design was refused) or with blocking findings cannot be
approved. A revision export is made from the stored snapshot alone, after its
hash, rules version and profile hash are checked again: a revision whose rules
changed is refused as stale, never silently re-designed. Nothing is exported
for execution: no execution package exists yet.

What a new revision's snapshot is written in is ``DESIGN_SNAPSHOT_SCHEMA``
(``pp-design/1`` unless set to ``pp-design/2``); every stored snapshot, of
either schema, is read through ``read_snapshot`` after its hash is checked,
and the renderers are given its v1 form. A stored row is never rewritten.

The revisions up to the one opened are its title block's revision list, with
who approved each. A project with an approved revision cannot be deleted: what
was signed for stays reproducible. Every function binds the session to the
caller's tenant first, so a project of another tenant does not exist as far as
it can tell (ADR 0003), and asking for one is answered as for an id that never
existed.
"""

from __future__ import annotations

import base64
import copy
import dataclasses
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import structlog
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, or_, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, object_session

from app.core import config
from app.core.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    PanelPilotError,
    ValidationError,
)
from app.core.tenancy import bind_tenant
from app.design import (
    CATALOGUE_VERSION,
    RENDERER_BASELINE,
    RULES_VERSION,
    ai_intake,
    approval_scope,
    connection_drawing_pdf,
    drawing_text,
    findings_policy,
    integrity,
    marking,
    renderer_version,
    requirements,
    safety_review,
    schedule_pdf,
    snapshot,
)
from app.design import project as project_design
from app.design.model import (
    anchoring,
    codec_v3,
    connection_drawing,
    entities,
    findings_v2,
    schedules,
    versions,
)
from app.design.model import links as link_model
from app.design.model.read import VersionedModel, read_snapshot
from app.design.model.write import snapshot_v2_from_v1, snapshot_v3_from_v2
from app.domain import billing, design_ai, design_configuration, engineering_reviews, permissions
from app.domain import design as design_domain
from app.domain.plans import Feature
from app.models.schemas.auth import CurrentUser, Permission
from app.models.schemas.design import (
    AcknowledgedRequirements,
    ApprovalHeld,
    ApproveRevisionRequest,
    BoardDesignResponse,
    ConfigurationReview,
    ConfigurationValidateRequest,
    ConfirmedLoad,
    DesignFinding,
    DesignProject,
    ExportFormat,
    ExportPurpose,
    ExportStatus,
    FindingSeverity,
    InstallationConditions,
    InterBoardLinkView,
    PricingSettings,
    ProjectDesignRequest,
    ProjectInfo,
    ProjectPage,
    ProjectSummary,
    QuestionAction,
    RequirementsInput,
    RequirementsPreview,
    RequirementsPreviewRequest,
    RequirementsRecord,
    ReviseProjectRequest,
    Revision,
    RevisionExportRequest,
    RevisionSummary,
    SafetyConcern,
    SafetyResolution,
    SafetyResolutionEligibility,
    SafetyResolutionRequest,
    SavedProject,
    SaveProjectRequest,
)
from app.models.tables.design_projects import DesignProjectRow, DesignRevisionRow
from app.models.tables.user import User

logger = structlog.get_logger(__name__)

#: Revisions one project may hold. A project is revised by hand; a client
#: saving in a loop is stopped here rather than filling the table.
MAX_REVISIONS = 200

#: Projects one tenant may hold.
MAX_PROJECTS = 1000

#: How many hex digits of a snapshot hash an export prints.
SHORT_HASH = 12

_PAGE_DEFAULT = 25
_PAGE_MAX = 100


def _scope_to(session: Session, user: CurrentUser) -> uuid.UUID:
    try:
        tenant = uuid.UUID(user.tenant_id)
    except ValueError as exc:
        raise NotFoundError("no such tenant") from exc
    bind_tenant(session, tenant)
    return tenant


def _release(session: Session) -> None:
    """Roll back after a failure, so no lock outlives it, keeping that failure.

    A rollback that fails too (the connection is gone) is logged rather than
    raised, so the error the caller sees is the one that happened first.
    """
    try:
        session.rollback()
    except Exception:
        logger.exception("design.rollback_failed")


def _load(session: Session, project_id: str, *, lock: bool = False) -> DesignProjectRow:
    """The caller's project; with ``lock``, its row locked (``SELECT ... FOR UPDATE``).

    Locking the project serializes revisions: each save reads and raises
    ``revision_count`` only once the save before it has committed, so
    concurrent saves get distinct numbers rather than colliding.
    """
    try:
        key = uuid.UUID(project_id)
    except ValueError as exc:
        raise NotFoundError("no such project", code="project_not_found") from exc
    found = session.get(DesignProjectRow, key, with_for_update=lock, populate_existing=lock)
    if found is None:
        raise NotFoundError("no such project", code="project_not_found")
    return found


@dataclass(frozen=True)
class _Validation:
    """What a revision's stored design says now: its status and why."""

    status: ExportStatus
    findings: list[DesignFinding]
    #: Whether its approval counts: of the design it holds, under today's policy.
    approval_bound: bool
    #: What changed since it was saved, for a stale revision.
    changed: tuple[str, ...] = ()
    #: What changed since it was approved; empty when it counts or none was given.
    approval_changed: tuple[str, ...] = ()
    #: The stored snapshot as read, when it passed its hash check and read.
    read: VersionedModel | None = None


def _stored_findings(row: DesignRevisionRow) -> list[DesignFinding]:
    return [DesignFinding.model_validate(found) for found in row.findings or []]


def _snapshot_matches(row: DesignRevisionRow) -> bool:
    """Whether the stored design still hashes to the hash recorded with it.

    A design that cannot be hashed (a float written into it outside this code)
    does not match: it is refused as altered, never answered with a 500; nor can one nested past
    the interpreter's recursion limit (a crafted row).
    """
    try:
        return snapshot.sha256_of(row.design_snapshot) == row.snapshot_sha256
    except (ValueError, RecursionError):
        return False


def _requirements_match(row: DesignRevisionRow) -> bool:
    """Whether a stored requirements record matches its hash; ``True`` when none is stored.

    A revision without a record (saved before M6a) gets a derived one, which
    never counts as acknowledged, so a record removed reads as never made.
    """
    if row.requirements is None:
        return True
    try:
        if snapshot.sha256_of(row.requirements) != row.requirements_sha256:
            return False
    except (TypeError, ValueError):
        return False
    # A record of the wrong shape, rehashed to match, was not written by this
    # software: refused as altered like a hash mismatch, never read further.
    try:
        RequirementsRecord.model_validate(row.requirements)
    except PydanticValidationError:
        return False
    return True


def _current_profile_sha256(row: DesignRevisionRow) -> str | None:
    """The hash of the revision's company settings as they resolve now; ``None`` if refused.

    The profile is the one the design was made under: the settings saved in
    the revision's own request. The tenant's company settings do not feed a
    saved design, so changing them does not make a revision stale; a change
    to this software's default profile, which those settings inherit, does.
    """
    try:
        request = ProjectDesignRequest.model_validate(row.request)
        return snapshot.profile_sha256(design_domain.resolve_profile(request.profile))
    except (PydanticValidationError, ValidationError):
        return None


def _classified(findings: Sequence[DesignFinding]) -> list[tuple[str, str, bool]]:
    return sorted(
        (found.code, found.severity.value, found.requires_engineering_confirmation)
        for found in findings
    )


def _findings_fingerprint(classified: Sequence[tuple[str, str, bool]]) -> str:
    """The SHA-256 of a findings classification's canonical JSON."""
    return snapshot.sha256_of([list(item) for item in classified])


def _current_classification(
    row: DesignRevisionRow, read: VersionedModel | None
) -> list[tuple[str, str, bool]] | None:
    """How today's findings policy classifies the frozen snapshot; ``None`` if it cannot.

    The policy and integrity checks are re-run on the stored design, which
    recalculates nothing. A snapshot that did not read (``read`` is ``None``)
    is left to the integrity check, which refuses it with its own code.

    Args:
        row: The revision.
        read: Its snapshot, already hash-checked and read by the caller
            (``_read_stored`` or ``_check_snapshot``), so it is read once.
    """
    if read is None:
        return None
    try:
        request = ProjectDesignRequest.model_validate(row.request)
    except PydanticValidationError:
        return None
    conditions = {board.name: board.conditions for board in request.boards}
    return _classified(_design_findings(read.project, conditions, read.v2, read.links))


#: Which ``pp-design/2`` structural findings an engineer must confirm. Their
#: severity is the findings module's own (``findings_v2.SEVERITIES``, the
#: single source), and so is their execution effect
#: (``findings_v2.EXECUTION_BLOCKING``): an unreviewed template or pattern is a
#: warning for design review (every legacy template is unreviewed) and an
#: execution blocker; a rejected or restricted-scope template, a rejected
#: pattern, or an explicitly required terminal in no net blocks approval.
#: Every review decision, every unresolved or assumed value, and the label
#: and number repeats a reviewer must judge, need confirmation; the two
#: blocking consistency codes (``unconnected_required_terminal``,
#: ``net_unrealised``) are fixed in the data, not confirmed.
_V2_CONFIRM: frozenset[str] = frozenset(
    {
        "template_unreviewed",
        "template_rejected",
        "out_of_execution_scope",
        "pattern_unreviewed",
        "pattern_rejected",
        "terminal_requiredness_unresolved",
        "net_signal_conflict",
        "wire_number_duplicate",
        "wire_attribute_unset",
        "wire_attribute_assumed",
    }
)


def _v2_findings(model: entities.DesignModelV2) -> list[DesignFinding]:
    """A v2 model's structural findings as stored findings (source ``integrity``).

    A code this mapping does not name is stored blocking: a new check is never
    quietly weaker than its author meant.
    """
    out: list[DesignFinding] = []
    for found in findings_v2.structural_findings(model):
        # An unknown severity is stored blocking: never quietly weaker than meant.
        severity = (
            FindingSeverity(found.severity)
            if found.severity in {item.value for item in FindingSeverity}
            else FindingSeverity.BLOCKING
        )
        confirm = found.code in _V2_CONFIRM
        out.append(
            DesignFinding(
                code=found.code,
                severity=severity,
                board_id=found.location.get("board_id"),
                values={key: str(value) for key, value in sorted(found.location.items())},
                message=found.message,
                requires_engineering_confirmation=confirm,
                source="integrity",
            )
        )
    return out


def _design_findings(
    project: DesignProject,
    conditions: Mapping[str, InstallationConditions],
    v2: entities.DesignModelV2 | None,
    links: Sequence[entities.InterBoardLink] = (),
) -> list[DesignFinding]:
    """Every finding of a stored design: the v1 checks, a v2 model's structural ones, its links'.

    A v1 snapshot (``v2`` is ``None``) gets exactly the findings it always
    did, so no v1 revision's findings, fingerprint or staleness change; a v2
    snapshot holds no links, so its findings are unchanged too.
    """
    found = integrity.project_findings(project, conditions)
    if v2 is not None:
        found = [*found, *_v2_findings(v2)]
        found = [*found, *_link_findings(v2, links)]
    return found


def _link_findings(
    model: entities.DesignModelV2, links: Sequence[entities.InterBoardLink]
) -> list[DesignFinding]:
    """A v3 model's link findings as stored findings (source ``integrity``); all warnings."""
    return [
        DesignFinding(
            code=found.code,
            severity=FindingSeverity(found.severity),
            board_id=found.location.get("board_id"),
            values={key: str(value) for key, value in sorted(found.location.items())},
            message=found.message,
            requires_engineering_confirmation=found.code in link_model.CONFIRM,
            source="integrity",
        )
        for found in link_model.findings(model, links)
    ]


@dataclass(frozen=True)
class _Applicable:
    """The policy that applies to a revision now, which its design and approval are held to.

    The snapshot is read only for a thorough check: the revision opened,
    approved or exported (one snapshot read per request). The list of a
    project's other revisions, and their rows in a title block, are checked
    cheaply, from columns alone: the recorded versions, and an approval's
    recorded profile hash against the one its stored request resolves to now
    (``profile_sha256``). Their snapshots are read for integrity as well
    (``_read_classified``), and where that read classified the stored design
    under today's policy (the revision records the current rules and
    catalogue), the classification is reused: a findings-policy change shows
    as ``stale`` (and an approval as not current) in the list as on open, at
    no extra cost. Under other rules the revision is stale by its version
    anyway. Only a profile change since the revision was saved is left to
    the thorough check of that revision (opening, approving or exporting it).
    """

    thorough: bool
    rules_version: str
    catalogue_version: str
    #: The revision's own saved settings, resolved over today's default profile.
    profile_sha256: str | None = None
    #: Today's classification of the stored design; ``None`` when it does not read.
    findings: list[tuple[str, str, bool]] | None = None
    #: Whether the stored design passes its hash check and reads (thorough only).
    snapshot_reads: bool = True
    #: What the exports print the stored design with: the renderer version of
    #: the revision's own schema (``RENDERER_VERSIONS``), so a v2 renderer
    #: change never touches a v1 approval.
    renderer_version: str = renderer_version(versions.V1)


def _row_schema(row: DesignRevisionRow, read: VersionedModel | None = None) -> str:
    """The schema a revision's snapshot is stored in; v1 when it has none or is unknown.

    Read from the marker alone (no hash, no parse) unless the caller read it.
    An unknown or unreadable snapshot is never approvable or exported, so the
    version it is compared with does not decide anything for it.
    """
    if read is not None:
        return read.schema
    try:
        return versions.identify(row.design_snapshot)
    except (versions.SnapshotSchemaUnknownError, versions.SnapshotUnreadableError):
        return versions.V1


def _renderer_of(row: DesignRevisionRow, read: VersionedModel | None = None) -> str:
    """The renderer version that applies to a revision now, by its schema."""
    return renderer_version(_row_schema(row, read))


def _applicable(
    row: DesignRevisionRow,
    *,
    thorough: bool = True,
    read: VersionedModel | None = None,
    read_checked: bool = False,
    classified: list[tuple[str, str, bool]] | None = None,
) -> _Applicable:
    """The policy that applies to ``row`` now.

    Args:
        row: The revision.
        thorough: Whether its profile and findings policy are resolved too.
        read: Its snapshot as the caller read it once (hash checked first);
            ``None`` when it is absent or did not read, so no findings are
            classified and an approval of it does not count (``snapshot_reads``).
        read_checked: For a cheap check, whether the caller read the snapshot
            for integrity (a listed revision): then an approval of one that
            did not read does not count; otherwise it is not judged here.
        classified: For a cheap check, today's classification of the stored
            design when the integrity read already computed it
            (``_read_classified``), so a findings-policy change shows at no
            extra cost; ``None`` leaves the findings policy unjudged.
    """
    profile = _current_profile_sha256(row)
    if not thorough:
        return _Applicable(
            False,
            RULES_VERSION,
            CATALOGUE_VERSION,
            profile_sha256=profile,
            findings=classified if read is not None else None,
            # A listed revision's snapshot is read for integrity as well: an
            # approval of one that fails it does not count, as when opened.
            snapshot_reads=read is not None or not read_checked,
            renderer_version=_renderer_of(row, read),
        )
    return _Applicable(
        True,
        RULES_VERSION,
        CATALOGUE_VERSION,
        profile_sha256=profile,
        findings=_current_classification(row, read),
        snapshot_reads=read is not None,
        renderer_version=_renderer_of(row, read),
    )


def _changed(row: DesignRevisionRow, applicable: _Applicable) -> tuple[str, ...]:
    """What no longer matches what the revision was designed under.

    A reclassification of its findings (a warning made blocking) makes it
    stale, rather than leaving its saved findings to say what the policy no
    longer does.
    """
    changed: list[str] = []
    if row.rules_version != applicable.rules_version:
        changed.append("rules_version")
    if row.catalogue_version != applicable.catalogue_version:
        changed.append("catalogue_version")
    if applicable.thorough and row.profile_sha256 != applicable.profile_sha256:
        changed.append("profile")
    if applicable.findings is not None:
        changed += _findings_parts(_classified(_stored_findings(row)), applicable.findings)
    return tuple(changed)


def _findings_parts(
    stored: Sequence[tuple[str, str, bool]], current: Sequence[tuple[str, str, bool]]
) -> list[str]:
    """What changed between a stored and today's classification of the same design.

    ``findings_policy``: the same findings (codes, review findings aside)
    classified differently. ``review_decisions``: the review findings differ
    (a review decision recorded, superseded or withdrawn since, M3) while
    every other finding code is the same. Other findings altogether are
    neither: under other rules they are explained by the version the stale
    check names; under the same rules the stored findings no longer belong
    to the design (``_findings_mismatch``).
    """
    parts: list[str] = []
    plain_stored, plain_current = _without_reviews(stored), _without_reviews(current)
    if _codes(plain_stored) != _codes(plain_current):
        return parts
    if plain_stored != plain_current:
        parts.append("findings_policy")
    if _only_reviews(stored) != _only_reviews(current):
        parts.append("review_decisions")
    return parts


#: The finding codes a template's or pattern's review status alone decides
#: (``findings_v2._review_findings``): what a server-side review decision
#: legitimately changes in a stored design's findings (M3).
_REVIEW_CODES: frozenset[str] = frozenset(
    {"template_unreviewed", "template_rejected", "pattern_unreviewed", "pattern_rejected"}
)


def _without_reviews(
    classified: Sequence[tuple[str, str, bool]],
) -> list[tuple[str, str, bool]]:
    return [item for item in classified if item[0] not in _REVIEW_CODES]


def _only_reviews(classified: Sequence[tuple[str, str, bool]]) -> list[tuple[str, str, bool]]:
    return [item for item in classified if item[0] in _REVIEW_CODES]


def _review_groups(
    findings: Sequence[DesignFinding],
) -> dict[tuple[str, str], tuple[set[str], list[str]]] | None:
    """The review findings by ``(kind, ref)``: their statuses and sorted locations.

    ``None`` if one does not name its definition (it was not written here).
    """
    groups: dict[tuple[str, str], tuple[set[str], list[str]]] = {}
    for found in findings:
        if found.code not in _REVIEW_CODES:
            continue
        kind, status = found.code.split("_", 1)
        ref = found.values.get(kind)
        if ref is None:
            return None
        statuses, places = groups.setdefault((kind, ref), (set(), []))
        statuses.add(status)
        places.append(snapshot.sha256_of(dict(sorted(found.values.items()))))
    for _, places in groups.values():
        places.sort()
    return groups


def _reviews_explained(row: DesignRevisionRow, read: VersionedModel) -> bool:
    """Whether the stored review findings are those of the stored design under some decisions.

    Each definition the design uses gives, under any set of decisions, either
    no review finding (approved) or one ``*_unreviewed`` or ``*_rejected``
    finding at each place it is used, all of the one status. So the stored
    review findings must name, for each definition, exactly the places an
    unreviewed definition is found at, with a single status, or none of
    them. A definition without review findings (approved) or with
    ``*_rejected`` ones must also have had such a decision recorded for its
    exact digest, at some time, in the tenant or globally
    (``engineering_reviews.ever_decided``). Anything else was not written by
    a save (whatever decisions stood then) and is an integrity failure.
    """
    stored = _review_groups(_stored_findings(row))
    if stored is None:
        return False
    if read.v2 is None:
        return not stored
    bare = dataclasses.replace(
        read.v2,
        templates=tuple(
            dataclasses.replace(t, review=anchoring.UNREVIEWED) for t in read.v2.templates
        ),
        patterns=tuple(
            dataclasses.replace(p, review=anchoring.UNREVIEWED) for p in read.v2.patterns
        ),
    )
    expected = _review_groups(_v2_findings(bare)) or {}
    for key, (statuses, places) in stored.items():
        if key not in expected or len(statuses) != 1 or places != expected[key][1]:
            return False
    # A status other than unreviewed is explained only by a decision the
    # server recorded for that exact definition, at any time and by anyone,
    # in the tenant or globally: one the save may have been anchored to.
    claimed = {
        key: (next(iter(stored[key][0])) if key in stored else "approved") for key in expected
    }
    if all(status == "unreviewed" for status in claimed.values()):
        return True
    session = object_session(row)
    if session is None:  # pragma: no cover - every row read here is loaded
        return False
    recorded = engineering_reviews.ever_decided(session=session, tenant_id=row.tenant_id)
    digests = {("template", t.ref): anchoring.definition_digest(t) for t in bare.templates}
    digests.update({("pattern", p.ref): anchoring.definition_digest(p) for p in bare.patterns})
    for (kind, ref), status in claimed.items():
        if status == "unreviewed":
            continue
        digest = digests.get((kind, ref))
        if digest is None or (kind, ref, digest, status) not in recorded:
            return False
    return True


def _explained(
    row: DesignRevisionRow, read: VersionedModel, current: list[tuple[str, str, bool]]
) -> bool:
    """Whether today's findings of the stored design account for its stored findings.

    The same codes; or the same codes but for the review findings, which
    are then those of the stored design under some set of review decisions
    (``_reviews_explained``): a decision recorded since the save (M3),
    which makes the revision stale (``review_decisions``), not altered.
    """
    stored = _stored_findings(row)
    if _codes(current) == _codes(stored):
        return True
    if _codes(_without_reviews(current)) != _codes(_without_reviews(_classified(stored))):
        return False
    return _reviews_explained(row, read)


def _requirements_binding(row: DesignRevisionRow) -> str:
    """What an approval binds of the requirements record: its hash, or JSON null's for none."""
    return row.requirements_sha256 or snapshot.sha256_of(None)


def _approval_changed(row: DesignRevisionRow, applicable: _Applicable) -> tuple[str, ...]:
    """What no longer matches what the revision's approval was granted under.

    Empty when the approval counts, or when there is none. ``snapshot``: it
    approved another design, or (thorough) the design it holds no longer
    passes its hash check or reads as a design. Then each recorded policy value against the one
    that applies now. A version or profile hash the approval did not record
    (a row neither the approval nor the migration wrote) reads as the
    revision's own, which an approval required to be current; a findings
    policy not recorded (an approval made before it was) reads as the
    classification of the revision's stored findings, which an approval
    required to match the policy then.
    """
    if row.approved_at is None:
        return ()
    changed: list[str] = []
    if (
        row.snapshot_sha256 is None
        or row.approved_snapshot_sha256 != row.snapshot_sha256
        or not applicable.snapshot_reads
    ):
        changed.append("snapshot")
    # ``requirements``: the record it was granted with is not the record held
    # (or fails its hash), or the approval recorded none (made before the
    # binding was: not bound, never assumed to be).
    if (
        row.approved_requirements_sha256 is None
        or row.approved_requirements_sha256 != _requirements_binding(row)
        or not _requirements_match(row)
    ):
        changed.append("requirements")
    if (row.approved_rules_version or row.rules_version) != applicable.rules_version:
        changed.append("rules_version")
    if (row.approved_catalogue_version or row.catalogue_version) != applicable.catalogue_version:
        changed.append("catalogue_version")
    if (row.approved_profile_sha256 or row.profile_sha256) != applicable.profile_sha256:
        changed.append("profile")
    if (row.approved_renderer_version or RENDERER_BASELINE) != applicable.renderer_version:
        changed.append("renderer_version")
    # The approval-scope statement confirmed (``approved_scope_version``) is
    # not compared here: it is printed on every review export, so changing it
    # changes the renderer's reference output and bumps the renderer version,
    # which stops every earlier approval counting (``renderer_version``).
    if not applicable.thorough and applicable.findings is None:
        # No classification of the snapshot (a cheap check whose integrity
        # read did not classify it): the recorded fingerprint is held to the
        # stored findings' classification only, which catches a row altered
        # after approval but not a policy change (that needs the snapshot).
        if row.approved_findings_policy is not None and row.approved_findings_policy != (
            _findings_fingerprint(_classified(_stored_findings(row)))
        ):
            changed.append("findings_policy")
        return tuple(changed)
    if applicable.findings is not None:
        approved = row.approved_findings_policy or _findings_fingerprint(
            _classified(_stored_findings(row))
        )
        if approved != _findings_fingerprint(applicable.findings):
            stored = _classified(_stored_findings(row))
            # An approval of the stored findings: name what moved since, a
            # review decision included; else (or nothing named) the policy.
            parts = (
                _findings_parts(stored, applicable.findings)
                if approved == _findings_fingerprint(stored)
                else []
            )
            changed += parts or ["findings_policy"]
    return tuple(changed)


def _request_problem(row: DesignRevisionRow) -> DesignFinding | None:
    """Why the revision's stored request no longer validates; ``None`` if it does.

    An older revision may hold what today's bounds refuse (a 600-character
    load description). It still opens, as stored, but it is not validated:
    it cannot be approved or exported until it is corrected and saved again.
    """
    try:
        ProjectDesignRequest.model_validate(row.request)
    except PydanticValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        return DesignFinding(
            code="request_not_valid",
            severity=FindingSeverity.BLOCKING,
            values={"field": where, "problem": first["type"]},
            message=f"The saved project no longer validates at {where}: {first['msg']}; "
            "correct it and save again",
            requires_engineering_confirmation=False,
            source="policy",
        )
    return None


def _schema_finding(exc: versions.SnapshotSchemaUnknownError) -> DesignFinding:
    """The blocking finding of a snapshot in a schema this version does not read.

    Such a revision still opens, as stored, but it is not validated: it cannot
    be approved or exported (``snapshot_schema_unknown``).
    """
    return DesignFinding(
        code="snapshot_schema_unknown",
        severity=FindingSeverity.BLOCKING,
        values={"schema": exc.schema},
        message=f"The stored design is in schema {exc.schema!r}, "
        "which this version of PanelPilot does not read",
        requires_engineering_confirmation=False,
        source="policy",
    )


def _read_stored(row: DesignRevisionRow) -> VersionedModel | DesignFinding:
    """The revision's snapshot read once (``_read_classified`` without the classification)."""
    return _read_classified(row)[0]


def _read_classified(
    row: DesignRevisionRow,
) -> tuple[VersionedModel | DesignFinding, list[tuple[str, str, bool]] | None]:
    """The revision's snapshot read once, or the blocking finding approval and export refuse it with.

    Checked as ``_check_snapshot`` checks it: the hash first, over the
    snapshot as stored, then ``read_snapshot``. So a revision opens as not
    validated exactly when approval and export refuse it, with the same code:
    an altered snapshot is ``snapshot_integrity_failed`` whatever marker it
    holds; an unaltered one in an unknown schema is ``snapshot_schema_unknown``.

    Also returns today's classification of the stored design when the
    findings check computed it (the recorded rules and catalogue are the
    current ones), else ``None``; a listed revision reuses it for its
    findings-policy check, so that costs no second run of the checks.
    """
    if not _snapshot_matches(row):
        return _integrity_finding("hash_mismatch"), None
    if not _requirements_match(row):
        return _integrity_finding("requirements_mismatch"), None
    try:
        read = read_snapshot(row.design_snapshot)
    except versions.SnapshotSchemaUnknownError as exc:
        return _schema_finding(exc), None
    except versions.SnapshotUnreadableError:
        return _integrity_finding("unreadable"), None
    anchored = _anchored(read, row)
    if anchored is None:
        return _integrity_finding("review_not_issued"), None
    current = _current_classification(row, anchored) if _versions_current(row) else None
    if _findings_mismatch(row, anchored, current):
        return _integrity_finding("findings_mismatch"), None
    return anchored, current


#: The codes of the findings a revision is refused with when its stored
#: design fails integrity; a revision holding one is never ``validated``.
_INTEGRITY_CODES: frozenset[str] = frozenset(
    {"snapshot_integrity_failed", "snapshot_schema_unknown"}
)


def _codes(findings: Sequence[DesignFinding] | Sequence[tuple[str, str, bool]]) -> list[str]:
    return sorted(item.code if isinstance(item, DesignFinding) else item[0] for item in findings)


def _versions_current(row: DesignRevisionRow) -> bool:
    return row.rules_version == RULES_VERSION and row.catalogue_version == CATALOGUE_VERSION


def _findings_mismatch(
    row: DesignRevisionRow,
    read: VersionedModel,
    current: list[tuple[str, str, bool]] | None = None,
) -> bool:
    """Whether the stored findings are not the findings of the stored design.

    The checks are re-run on the stored design under the rules and catalogue
    it records. While those versions are still the current ones, the same
    design gives the same findings, so a different set of finding codes
    means the stored findings no longer belong to the stored design (a row
    rewritten outside the application with its hash recomputed): an
    integrity failure, not a rules change. Only a different classification of
    the same codes is a findings-policy change (``_changed``). When the rules
    or catalogue version differs, the difference is left to the stale check,
    which names that version.

    This holds only while every change to what the checks find bumps
    ``RULES_VERSION``: a check changed under an unchanged version would make
    every intact row it affects read as an integrity failure. CI enforces it
    (``app/tests/design/test_findings_fingerprint.py`` records a digest of the
    finding codes the checks produce on every committed fixture and
    configuration, keyed by ``RULES_VERSION``).

    Args:
        row: The revision.
        read: Its snapshot, hash-checked and read.
        current: Today's classification of it, if the caller computed it.
    """
    if not _versions_current(row):
        return False
    current = current if current is not None else _current_classification(row, read)
    if current is None:
        return False
    return not _explained(row, read, current)


def _anchor_of(row: DesignRevisionRow) -> anchoring.ReviewAnchor:
    """The server's review decisions that apply in the revision's tenant (M3)."""
    session = object_session(row)
    if session is None:  # pragma: no cover - every row read here is loaded
        return anchoring.EMPTY
    return engineering_reviews.anchor_for(session=session, tenant_id=row.tenant_id)


def _anchored(read: VersionedModel, row: DesignRevisionRow) -> VersionedModel | None:
    """The stored design with its review statuses anchored to the server's decisions.

    Each template's and pattern's review is replaced, in memory only, by the
    one the server's decisions give for its exact definition digest in the
    revision's tenant or the global scope (``anchoring.anchor_model``):
    approved or rejected only with a matching record, else unreviewed.
    ``None`` when a stored review claims anything else: it was written
    outside the application, even if the row's lock and hash were recomputed
    to match, and the design is refused like an altered one
    (``review_not_issued``). With no decisions recorded this is M2's rule:
    any stored status other than ``unreviewed`` is refused.
    """
    if read.v2 is None:
        return read
    try:
        model = anchoring.anchor_model(read.v2, _anchor_of(row))
    except anchoring.ReviewClaimUnmatchedError:
        return None
    if model is read.v2:
        return read
    return VersionedModel(schema=read.schema, project=read.project, v2=model, links=read.links)


#: Why a stored design failed its integrity check, as a finding says it.
_INTEGRITY_MESSAGES: dict[str, str] = {
    "hash_mismatch": "The stored design does not match its recorded hash",
    "unreadable": "The stored design no longer reads as a design",
    "review_not_issued": "The stored design holds a review status this server never issued",
    "findings_mismatch": "The findings stored with this revision are not the findings of "
    "its stored design under the rules it records",
    "requirements_mismatch": "The requirements record stored with this revision does not "
    "match its recorded hash",
}


def _integrity_finding(reason: str) -> DesignFinding:
    """The blocking finding of a snapshot that failed integrity, ``reason`` saying how.

    ``hash_mismatch``, ``unreadable``, ``review_not_issued`` or ``findings_mismatch``.
    """
    return DesignFinding(
        code="snapshot_integrity_failed",
        severity=FindingSeverity.BLOCKING,
        values={"reason": reason},
        message=f"{_INTEGRITY_MESSAGES[reason]}; save it again as a new revision",
        requires_engineering_confirmation=False,
        source="policy",
    )


def _validation(row: DesignRevisionRow, *, thorough: bool = True) -> _Validation:
    """The revision's export status and findings.

    The stored snapshot is read once (``_read_stored``: hash first, then
    read, review status and findings), thorough or not, so a listed revision
    that fails integrity is ``not_validated`` as it is when opened. Thorough,
    that read is also what today's findings policy classifies.
    """
    findings = _stored_findings(row)
    problem = _request_problem(row) if thorough else None
    has_snapshot = row.design_snapshot is not None and row.snapshot_sha256 is not None
    stored: VersionedModel | DesignFinding | None = None
    classified: list[tuple[str, str, bool]] | None = None
    if has_snapshot:
        # Read even when the request no longer validates: whether it reads
        # decides whether an approval of it counts (``snapshot_reads``). A
        # listed revision is checked for integrity too, so the list never
        # calls a revision validated that opening it would refuse.
        stored, classified = _read_classified(row)
    applicable = _applicable(
        row,
        thorough=thorough,
        read=stored if isinstance(stored, VersionedModel) else None,
        read_checked=stored is not None,
        classified=classified,
    )
    moved = _approval_changed(row, applicable)
    bound = row.approved_at is not None and not moved
    if problem is not None:
        return _Validation(ExportStatus.NOT_VALIDATED, [*findings, problem], bound, (), moved)
    if not has_snapshot:
        return _Validation(ExportStatus.NOT_VALIDATED, findings, bound, (), moved)
    if isinstance(stored, DesignFinding):
        return _Validation(ExportStatus.NOT_VALIDATED, [*findings, stored], bound, (), moved)
    read = stored if isinstance(stored, VersionedModel) else None
    changed = _changed(row, applicable)
    if changed:
        return _Validation(ExportStatus.STALE, findings, bound, changed, moved, read)
    if integrity.blocking(findings):
        return _Validation(ExportStatus.BLOCKED, findings, bound, (), moved, read)
    if bound:
        return _Validation(ExportStatus.REVIEW_APPROVED, findings, bound, read=read)
    return _Validation(ExportStatus.DRAFT, findings, bound, (), moved, read)


def _intact(checked: _Validation) -> bool:
    """Whether the stored design passed its integrity checks (hash, read, review, findings)."""
    return not any(found.code in _INTEGRITY_CODES for found in checked.findings)


def _execution_blockers(checked: _Validation) -> list[str]:
    """Why a revision cannot be exported for execution, most fundamental first."""
    blockers = list(marking.EXECUTION_PACKAGE_MISSING)
    if checked.status is ExportStatus.NOT_VALIDATED:
        blockers.append("not_validated")
    if checked.status is ExportStatus.STALE:
        blockers.append("stale")
    if not checked.approval_bound:
        blockers.append("approval")
    blockers.extend(f"finding:{found.code}" for found in integrity.blocking(checked.findings))
    # A v2 design on an unreviewed or out-of-scope template may not be
    # executed, whatever else is resolved; named by code (v2 rows only).
    blockers.extend(
        sorted(
            {
                found.code
                for found in checked.findings
                if found.code in findings_v2.EXECUTION_BLOCKING
                or found.code in link_model.EXECUTION_BLOCKING
            }
        )
    )
    return blockers


def _structural(found: DesignFinding) -> bool:
    """Whether a stored finding is a v2 structural one (``source`` ``integrity``).

    ``designation_duplicate`` is made by both checks; only the v2 one is
    structural, so a v1 revision still prints its own.
    """
    shared = found.code in findings_policy.INTEGRITY
    return found.code in findings_v2.CODES and (not shared or found.source == "integrity")


def _printed(findings: Sequence[DesignFinding], schema: str) -> list[DesignFinding]:
    """The findings a revision's review exports print.

    A v2 revision's exports print every finding, its structural ones too
    (warnings and unresolved items included), so a review document exposes
    them; that is the v2 renderer version. A v1 revision holds no structural
    finding, so it prints exactly what it always did.
    """
    if schema in (versions.V2, versions.V3):
        return list(findings)
    return [found for found in findings if not _structural(found)]


def _written_schema() -> str:
    """The schema a new revision is written in: ``DESIGN_SNAPSHOT_SCHEMA`` (v1 by default)."""
    # Only the switch: a save must not need the database URL or secrets.
    return config.design_snapshot_schema()


#: How an approval names its approver when the account has no display name,
#: is no longer the tenant's, or was not recorded (an approval made before
#: accounts were). Never the name typed at approval, nor an email.
APPROVER_FALLBACK = drawing_text.APPROVER_FALLBACK


def _display_name(full_name: str | None) -> str | None:
    """An account's name as a revision list may show it; ``None`` if it has none.

    A name that holds an ``@`` is refused as well: it may be the email itself,
    and the email is never sent to a client.
    """
    name = (full_name or "").strip()
    return name if name and "@" not in name else None


def _now_iso() -> str:
    """Now, in UTC, as ISO 8601 to the second: when a save records feeder link values."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _account_id(user: CurrentUser) -> uuid.UUID | None:
    """The caller's account id; ``None`` for a caller with no account row id (a trial)."""
    try:
        return uuid.UUID(user.id)
    except ValueError:
        return None


@dataclass(frozen=True)
class _Names:
    """The display names of the accounts a project's revisions record.

    Read when a response or export is built, from the project's own tenant
    only: an account of another tenant's, or one deleted since, has no name.
    """

    by_email: dict[str, str]
    by_id: dict[uuid.UUID, str]

    def author(self, row: DesignRevisionRow) -> str:
        """Who saved it: the display name, else the recorded account email, else unknown.

        Never empty and never invented: the email is what the server recorded,
        and is shown to the tenant's members on the Team page.
        """
        if not row.author:
            return requirements.UNKNOWN_ACCOUNT
        return self.by_email.get(row.author.lower()) or row.author

    def approver(self, row: DesignRevisionRow) -> str | None:
        """The approving account's display name; ``None`` if none resolves.

        An approval that recorded the account's id is resolved by it alone:
        the email it recorded may since belong to another account. One made
        before ids were recorded is resolved by its email, in the tenant. The
        legacy typed name (``approved_by``) is never used.
        """
        if row.approved_at is None:
            return None
        if row.approved_display_name is not None:
            # Frozen with the approval (or by the migration): "" is no name.
            return row.approved_display_name or None
        if row.approved_user_id is not None:
            return self.by_id.get(row.approved_user_id)
        if row.approved_account:
            return self.by_email.get(row.approved_account.lower())
        return None

    def printed_approver(self, row: DesignRevisionRow) -> str:
        """The approver as an export prints it: the display name, or the fallback."""
        return self.approver(row) or APPROVER_FALLBACK


def _display_names(
    session: Session,
    tenant: uuid.UUID,
    revisions: Sequence[DesignRevisionRow],
    *,
    also: uuid.UUID | None = None,
    extra_ids: Sequence[uuid.UUID] = (),
) -> _Names:
    """The display names of the accounts the revisions record (and ``also``).

    The rows record an author by email, and an approver by id (or, approved
    before ids were recorded, by email): the audit identity, kept as it was.
    """
    emails = {
        email.lower() for row in revisions for email in (row.author, row.approved_account) if email
    }
    ids = {row.approved_user_id for row in revisions if row.approved_user_id is not None}
    if also is not None:
        ids.add(also)
    ids.update(extra_ids)
    if not emails and not ids:
        return _Names(by_email={}, by_id={})
    found = session.execute(
        select(User.id, User.email, User.full_name).where(
            User.tenant_id == tenant,
            or_(func.lower(User.email).in_(emails), User.id.in_(ids)),
        )
    ).all()
    by_email: dict[str, str] = {}
    by_id: dict[uuid.UUID, str] = {}
    for user_id, email, full_name in found:
        name = _display_name(full_name)
        if name is not None:
            by_email[email.lower()] = name
            by_id[user_id] = name
    return _Names(by_email=by_email, by_id=by_id)


def _summary(
    row: DesignRevisionRow, names: _Names, checked: _Validation | None = None
) -> RevisionSummary:
    approved = row.approved_at is not None
    # One of up to MAX_REVISIONS rows: its policy checked from its columns alone
    # (``_Applicable``), its stored design for integrity (``_read_stored``), so
    # ``validated`` and ``not_validated`` agree with what opening it would say.
    # The revision opened is checked in full (``_opened`` passes its check),
    # and approval and export check theirs in full again under the project lock.
    checked = checked or _validation(row, thorough=False)
    return RevisionSummary(
        number=row.number,
        note=row.note,
        author=names.author(row),
        created_at=row.created_at.isoformat(),
        approved_by=names.printed_approver(row) if approved else None,
        approved_at=row.approved_at.isoformat() if row.approved_at else None,
        approver_account_recorded=approved
        and (row.approved_user_id is not None or bool(row.approved_account)),
        approver_name=names.approver(row),
        approval_note=(row.approval_note or None) if approved else None,
        validated=row.design_snapshot is not None and _intact(checked),
        export_status=checked.status,
        approval_current=checked.approval_bound if approved else None,
        approval_changed=list(checked.approval_changed),
        author_unknown=_author_unknown(row),
        author_established=_author_established(row),
    )


def _read_applicable(row: DesignRevisionRow) -> _Applicable:
    """The policy that applies to ``row`` now, its stored design read and classified.

    The same check the revision list makes of each row (``_validation``), so
    the opened request's title block, the list, the approval status and the
    exports agree on whether an approval counts, a findings-policy change
    included.
    """
    stored, classified = (
        _read_classified(row)
        if row.design_snapshot is not None and row.snapshot_sha256 is not None
        else (None, None)
    )
    return _applicable(
        row,
        thorough=False,
        read=stored if isinstance(stored, VersionedModel) else None,
        read_checked=stored is not None,
        classified=classified,
    )


def _title_revisions(
    revisions: Sequence[DesignRevisionRow],
    upto: int,
    names: _Names,
    *,
    bound_to: _Applicable | None = None,
    upto_counts: bool | None = None,
) -> list[Revision]:
    """The title block's revision list: each saved revision up to ``upto``.

    Args:
        revisions: The project's revisions, in order.
        upto: The last revision listed.
        names: The accounts' display names, which name the approver.
        bound_to: Given, name an approver only where the approval counts, as
            an export prints it: of the design the revision holds, under
            today's policy. ``bound_to`` is that policy for ``upto``, resolved
            in full from the snapshot the caller read once; the others are
            checked from their columns alone (``_Applicable``).
        upto_counts: Given, whether ``upto``'s approval counts, as the caller
            already checked it in full (an opened revision's own check), so
            the snapshot is not read a second time.
    """
    listed: list[Revision] = []
    for row in revisions:
        if row.number > upto:
            continue
        applicable = bound_to
        if bound_to is not None and row.number != upto and row.approved_at is not None:
            # Read for integrity as the revision list reads it, so an earlier
            # approval of a design that fails it never prints as approved.
            applicable = _read_applicable(row)
        if row.number == upto and upto_counts is not None:
            shown = row.approved_at is not None and upto_counts
        else:
            shown = row.approved_at is not None and (
                applicable is None or not _approval_changed(row, applicable)
            )
        listed.append(
            Revision(
                index=f"{row.number:02d}",
                date=(row.approved_at or row.created_at).date().isoformat(),
                description=row.note,
                approved_by=names.printed_approver(row) if shown else "",
                approval_note=(row.approval_note or "") if shown else "",
            )
        )
    return listed


def _design_refused(exc: ValidationError) -> DesignFinding:
    return DesignFinding(
        code="design_refused",
        severity=FindingSeverity.BLOCKING,
        values={**exc.params, **({"refusal": exc.code} if exc.code else {})},
        message=f"The design was refused: {exc}",
        requires_engineering_confirmation=False,
        source="policy",
    )


@dataclass(frozen=True)
class _LinkEntry:
    """Who saves a revision's feeder links, and the record refs they name (M7a)."""

    board_refs: Mapping[str, str]
    entered_by_user_id: str | None
    entered_at: str | None


def _validate(
    request: ProjectDesignRequest,
    configuration: Mapping[str, Any] | None = None,
    anchor: anchoring.ReviewAnchor = anchoring.EMPTY,
    link_entry: _LinkEntry | None = None,
) -> dict[str, Any]:
    """Design a request on the server: the revision's snapshot columns.

    The snapshot's title block carries no revision rows: who checked and
    approved a revision is read from the database when it is exported, never
    from what the client typed.

    A request the design refuses is still saved, so no entered work is lost,
    with no snapshot and its refusal as a blocking finding: it opens, and
    cannot be approved until a revision of it designs.

    With a configuration, the switch must be ``pp-design/2`` (v1 cannot hold
    one: ``configuration_requires_v2``), and the design must not be refused:
    a configuration is never dropped while the revision is saved without it.
    """
    written = _written_schema()
    if request.feeder_links is not None:
        _require_v3_writes(written)
        link_model.check_request(request)
    if configuration is not None:
        _require_v2_writes()
        design_configuration.check_configuration(configuration)
    as_entered = request.model_copy(
        update={"info": request.info.model_copy(update={"revisions": []})}
    )
    try:
        designed, company = design_domain.build_design(as_entered)
    except ValidationError as exc:
        if configuration is not None:
            raise ValidationError(
                f"the design was refused, so the configuration cannot be applied: {exc}",
                code="configuration_refused",
                params={"refusal": "design_refused", **exc.params},
            ) from exc
        return {"findings": [_design_refused(exc).model_dump(mode="json")]}
    conditions = {board.name: board.conditions for board in request.boards}
    stored = designed.model_dump(mode="json")
    v2: entities.DesignModelV2 | None = None
    if configuration is not None:
        stored = design_configuration.compose_snapshot(stored, configuration)
        v2 = read_snapshot(stored).v2
    elif written in (versions.V2, versions.V3):
        # No configuration: the v1 devices become components on their legacy
        # templates. Read back through the one reader, so what is stored is
        # what every later read sees.
        stored = snapshot_v2_from_v1(stored)
        v2 = read_snapshot(stored).v2
    links: list[entities.InterBoardLink] = []
    if written == versions.V3 and v2 is not None:
        # M7a: one link per server-owned feeder, with what the user entered;
        # its terminals are checked against the model it is stored with.
        entry = link_entry or _LinkEntry(
            board_refs=requirements.board_refs(request, None),
            entered_by_user_id=None,
            entered_at=None,
        )
        links = link_model.derive(
            v2,
            request.feeder_links,
            entry.board_refs,
            entered_by_user_id=entry.entered_by_user_id,
            entered_at=entry.entered_at,
        )
        stored = snapshot_v3_from_v2(stored, links)
    # The findings of what every later read sees: its reviews anchored to the
    # server's decisions (a save writes only ``unreviewed``), so a revision
    # saved after a decision does not read as stale.
    if v2 is not None:
        v2 = anchoring.anchor_model(v2, anchor)
    findings = _design_findings(designed, conditions, v2, links)
    return {
        "design_snapshot": stored,
        "snapshot_sha256": snapshot.sha256_of(stored),
        "profile_sha256": snapshot.profile_sha256(company),
        "rules_version": RULES_VERSION,
        "catalogue_version": CATALOGUE_VERSION,
        "findings": [found.model_dump(mode="json") for found in findings],
    }


def _require_v3_writes(written: str) -> None:
    """Refuse feeder links where new revisions are not written in ``pp-design/3`` (M7a)."""
    if written != versions.V3:
        raise ConflictError(
            f"this deployment writes revisions in {written}, which cannot hold feeder links; "
            "they are stored only where DESIGN_SNAPSHOT_SCHEMA is pp-design/3",
            code="feeder_links_require_v3",
            params={"written_schema": written, "required_schema": versions.V3},
        )


def _require_v2_writes() -> None:
    """Refuse a configuration where new revisions are written in ``pp-design/1``."""
    written = _written_schema()
    if written not in (versions.V2, versions.V3):
        raise ConflictError(
            f"this deployment writes revisions in {written}, which cannot hold a "
            "configuration; it is stored only where DESIGN_SNAPSHOT_SCHEMA is pp-design/2",
            code="configuration_requires_v2",
            params={"written_schema": written, "required_schema": versions.V2},
        )


ConfigurationStatus = Literal["none", "stored", "not_recoverable"]

#: Composer refusal params naming what a carried configuration refers to,
#: most specific first (M7b ``configuration_conflict``).
_CONFLICT_REFS = ("device", "cable", "circuit", "component", "board", "strip", "slot", "ref")
#: Where in the configuration the reference sits: one of these is sent.
_CONFLICT_PLACES = ("binding", "strip", "component")


def _configuration_conflict(exc: ValidationError) -> ValidationError | ConflictError:
    """A carried configuration that no longer composes with the new request (M7b).

    Nothing is saved: the configuration is never dropped or regenerated. Other
    refusals (of the request itself) are returned unchanged.
    """
    params = dict(exc.params)
    if exc.code in ("link_terminal_unknown", "link_end_off_board"):
        out: dict[str, Any] = {
            "ref": f"{params.get('end', '')} end of {params.get('fed_board', '')}",
            "kind": "terminal",
            "link": str(params.get("fed_board", "")),
            "reason": exc.code,
        }
    elif exc.code == "configuration_refused":
        reason = str(params.pop("refusal", "configuration_refused"))
        kind = next((name for name in _CONFLICT_REFS if name in params), "configuration")
        out = {"ref": str(params.get(kind, "")), "kind": kind, "reason": reason}
        place = next((name for name in _CONFLICT_PLACES if name in params), None)
        if place is not None:
            out[place] = str(params[place])
    else:
        return exc
    return ConflictError(
        "the configuration carried from the latest revision no longer fits this project: "
        f"{exc} — edit or remove the reference it names, then save again; nothing was saved",
        code="configuration_conflict",
        params=out,
    )


def _read_v2(stored: Any) -> entities.DesignModelV2 | None:
    """A stored design's v2 model, or ``None`` (none, v1, or unreadable)."""
    if stored is None:
        return None
    try:
        return read_snapshot(stored).v2
    except (versions.SnapshotSchemaUnknownError, versions.SnapshotUnreadableError):
        return None


def _model_configured(model: entities.DesignModelV2 | None) -> bool:
    """Whether a v2 model was composed from a configuration (strips, patterns ...)."""
    if model is None:
        return False
    return bool(
        model.strips
        or model.nets
        or model.connections
        or model.wires
        or model.patterns
        or any(component.legacy_device_id is None for component in model.components)
    )


def _configuration_of(
    row: DesignRevisionRow,
    record: Mapping[str, Any] | None,
    read: VersionedModel | bool | None = True,
) -> tuple[dict[str, Any] | None, ConfigurationStatus]:
    """A revision's stored configuration and its status (M7b).

    ``stored``: the record holds it; ``not_recoverable``: its design was
    composed from one that was saved before M7b and so not kept apart;
    ``none`` otherwise.
    """
    if record is not None and isinstance(record.get("configuration"), dict):
        return copy.deepcopy(record["configuration"]), "stored"
    # ``read``: the design already read (``None``: none readable); ``True``: read it now.
    model = _read_v2(row.design_snapshot) if read is True else getattr(read, "v2", None)
    if _model_configured(model):
        return None, "not_recoverable"
    return None, "none"


def _carried_configuration(
    row: DesignRevisionRow, record: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    """The configuration a save without one carries from ``row``; refused if lost (M7b)."""
    configuration, status = _configuration_of(row, record)
    if status == "not_recoverable":
        raise ConflictError(
            f"revision {row.number} was composed from a configuration saved before it was "
            "stored apart, so it cannot be carried without loss; enter the configuration "
            "again (or remove it explicitly) and save",
            code="configuration_not_recoverable",
            params={"revision": row.number},
        )
    return configuration


def _add_revision(
    session: Session,
    project: DesignProjectRow,
    tenant: uuid.UUID,
    user: CurrentUser,
    request: ProjectDesignRequest,
    note: str,
    configuration: Mapping[str, Any] | None = None,
    requirements_input: RequirementsInput | None = None,
    prior: Mapping[str, Any] | None = None,
    answered: Sequence[Mapping[str, Any]] = (),
    resaved_from: int | None = None,
    carried: bool = False,
) -> None:
    """Add the project's next revision.

    ``configuration`` is stored verbatim in the revision's requirements record
    (M7b); ``carried`` says it came from an earlier revision, so a refusal to
    compose it is a ``configuration_conflict``.
    """
    if project.revision_count >= MAX_REVISIONS:
        raise ValidationError(
            f"a project holds at most {MAX_REVISIONS} revisions",
            code="project_revisions_full",
            params={"limit": str(MAX_REVISIONS)},
        )
    project.revision_count += 1
    saving_account = _account_id(user) if user.email else None
    try:
        columns = _validate(
            request,
            configuration,
            engineering_reviews.anchor_for(session=session, tenant_id=tenant),
            _LinkEntry(
                board_refs=requirements.board_refs(request, prior),
                entered_by_user_id=str(saving_account) if saving_account is not None else None,
                entered_at=_now_iso(),
            ),
        )
    except ValidationError as exc:
        if carried and configuration is not None:
            raise _configuration_conflict(exc) from exc
        raise
    stored_read = (
        read_snapshot(columns["design_snapshot"])
        if columns.get("design_snapshot") is not None
        else None
    )
    designed = stored_read.project if stored_read is not None else None
    # M7a: in a v3 revision, a feeder with a link is recorded by the link and
    # its findings, not by the open item ``inter_board_wiring_not_modelled``.
    linked = (
        frozenset(
            board.name
            for link in stored_read.links
            for board in stored_read.project.boards
            if board.id == link.feeder.fed_board
        )
        if stored_read is not None
        else frozenset()
    )
    proposal = None
    ai_block = None
    if requirements_input is not None and requirements_input.ai is not None:
        # Under the save's transaction: the proposal row is locked until it
        # commits, so no second save applies it (``ai_proposal_used``).
        proposal, ai_block = design_ai.apply(
            session=session,
            user=user,
            request=request,
            given=requirements_input.ai,
            control=requirements_input.control,
        )
        ai_block["applied_in_revision"] = project.revision_count
    record = requirements.build(
        request,
        designed,
        [DesignFinding.model_validate(found) for found in columns["findings"]],
        requirements_input,
        prior=prior,
        answered=answered,
        ai=ai_block,
        linked=linked,
    )
    if resaved_from is not None:
        # A pp-req/1 addition: the revision whose stored request this one re-designed.
        record["resaved_from"] = resaved_from
    if configuration is not None:
        # M7b: verbatim, so it is carried losslessly and the requirements hash
        # (the approval binding) covers it. Absent with none: a record without
        # it stores byte for byte as before.
        record["configuration"] = copy.deepcopy(dict(configuration))
    revision = DesignRevisionRow(
        tenant_id=tenant,
        project_id=project.id,
        number=project.revision_count,
        note=note,
        author=user.email,
        # A trial's subject is an anonymous session, not an account: the
        # account that later claims the tenant has another id, so recording
        # the session's would let that account approve what it saved.
        author_user_id=_account_id(user) if user.email else None,
        request=request.model_dump(mode="json"),
        requirements=record,
        requirements_sha256=snapshot.sha256_of(record),
        **columns,
    )
    session.add(revision)
    if proposal is not None:
        session.flush()
        design_ai.mark_used(proposal, revision.id)


def _stored_schema(stored: Any) -> str | None:
    """The schema a stored design names (its marker alone), or ``None`` if none is known."""
    if stored is None:
        return None
    try:
        return versions.identify(stored)
    except (versions.SnapshotSchemaUnknownError, versions.SnapshotUnreadableError):
        return None


def _revisions(session: Session, project: DesignProjectRow) -> Sequence[DesignRevisionRow]:
    return session.scalars(
        select(DesignRevisionRow)
        .where(DesignRevisionRow.project_id == project.id)
        .order_by(DesignRevisionRow.number)
    ).all()


def _opened(
    session: Session, project: DesignProjectRow, number: int | None, viewer: CurrentUser
) -> SavedProject:
    revisions = _revisions(session, project)
    chosen = (
        revisions[-1]
        if number is None
        else next((r for r in revisions if r.number == number), None)
    )
    if chosen is None:
        raise NotFoundError("no such revision", code="revision_not_found")
    viewer_id = _account_id(viewer)
    names = _display_names(session, project.tenant_id, revisions, also=viewer_id)
    # Bound like an export's title block, so the opened request never lists an
    # approval that no longer counts (stale, unbound or failing integrity).
    checked = _validation(chosen)
    # The opened revision's approver is named only where its own full check
    # (``checked``, findings policy included) says the approval counts, as the
    # list, ``approval_current`` and an export say; earlier ones as an export.
    title = _title_revisions(
        revisions,
        chosen.number,
        names,
        bound_to=_applicable(chosen, thorough=False),
        upto_counts=checked.approval_bound,
    )
    readable = _request_problem(chosen) is None
    if readable:
        request = ProjectDesignRequest.model_validate(chosen.request)
        request = request.model_copy(
            update={"info": request.info.model_copy(update={"revisions": title})}
        )
    else:
        # Sent back as stored (``SavedProject.as_stored``), with the title
        # block's revisions from the database as for any other revision.
        stored: dict[str, Any] = dict(chosen.request)
        info = stored.get("info")
        stored["info"] = {
            **(info if isinstance(info, dict) else {}),
            "revisions": [listed.model_dump(mode="json") for listed in title],
        }
        request = ProjectDesignRequest.model_construct(**stored)
    opened = SavedProject(
        id=str(project.id),
        name=project.name,
        revisions=[_summary(r, names, checked if r is chosen else None) for r in revisions],
        revision=chosen.number,
        request=request,
        findings=checked.findings,
        validated=readable and chosen.design_snapshot is not None and _intact(checked),
        snapshot_sha256=chosen.snapshot_sha256,
        rules_version=chosen.rules_version,
        approved_snapshot_sha256=chosen.approved_snapshot_sha256,
        approval_current=checked.approval_bound if chosen.approved_at is not None else None,
        approval_changed=list(checked.approval_changed),
        export_status=checked.status,
        execution_blockers=_execution_blockers(checked),
        # Not for a stale revision: what it shows no longer holds, and a review
        # page must not present it as the revision's current content.
        model_v2=(
            design_configuration.summary(checked.read.v2, _V2_CONFIRM)
            if checked.read is not None
            and checked.read.v2 is not None
            and checked.status is not ExportStatus.STALE
            else None
        ),
        links=(
            [
                InterBoardLinkView.model_validate(item)
                for item in codec_v3.dump_links(checked.read.links)
            ]
            if checked.read is not None
            and checked.read.schema == versions.V3
            and checked.status is not ExportStatus.STALE
            else None
        ),
        written_schema=_written_schema(),
        snapshot_schema=_stored_schema(chosen.design_snapshot),
        approving_as=names.by_id.get(viewer_id) if viewer_id is not None else None,
        permissions=permissions.allowed(session=session, user=viewer),
        requirements=_record_view(_record_of(chosen, checked)),
        author_unknown=_author_unknown(chosen),
        author_established=_author_established(chosen),
        is_author=_is_author(chosen, viewer),
    )
    stored_record = _stored_record(chosen)
    found = _found(chosen, revisions)
    opened.safety_concerns = _safety_concerns(found, stored_record)
    opened.safety_resolution = resolution_eligibility(
        session=session,
        row=chosen,
        record=stored_record,
        found=found,
        latest=project.revision_count,
        user=viewer,
    )
    opened.approval_held = (
        _approval_held(found, stored_record) if chosen.approved_at is None else None
    )
    opened.approval_scope_version = approval_scope.APPROVAL_SCOPE_VERSION
    opened.approved_scope_version = chosen.approved_scope_version
    opened.configuration, opened.configuration_status = _configuration_of(
        chosen, stored_record, checked.read
    )
    return opened if readable else opened.as_stored(stored)


def save_project(
    *, session: Session, user: CurrentUser, request: SaveProjectRequest
) -> SavedProject:
    """Save a new project as its first revision, designed on the server.

    Args:
        session: Open database session.
        user: The authenticated caller; the revision's author.
        request: Its name, the project as entered, and a note.

    Returns:
        The project as saved, with what the checks found.

    Raises:
        ValidationError: If the tenant already holds the most projects
            allowed, or the company settings are unknown or malformed.
        AuthorizationError: If its plan keeps no more projects.
    """
    tenant = _scope_to(session, user)
    # Malformed company settings are the request's error, refused as such,
    # not saved as a design the server could not make.
    design_domain.resolve_profile(request.request.profile)
    # A feeder is the designer's: a typed one is refused, never saved.
    project_design.refuse_client_feeders(request.request.boards)
    check_requirements_input(request.request, request.requirements_input)
    billing.require_project_room(session=session, user=user)
    try:
        held = session.scalars(select(DesignProjectRow.id).limit(MAX_PROJECTS)).all()
        if len(held) >= MAX_PROJECTS:
            raise ValidationError(
                f"at most {MAX_PROJECTS} projects can be saved",
                code="projects_full",
                params={"limit": str(MAX_PROJECTS)},
            )
        project = DesignProjectRow(tenant_id=tenant, name=request.name.strip(), revision_count=0)
        session.add(project)
        session.flush()
        _add_revision(
            session,
            project,
            tenant,
            user,
            request.request,
            request.note,
            requirements_input=request.requirements_input,
        )
        session.commit()
    except BaseException:
        # Whatever failed, nothing half-saved is kept and no lock outlives it.
        _release(session)
        raise
    logger.info("design.project_saved", tenant_id=str(tenant), project_id=str(project.id))
    return _opened(session, project, None, user)


def revise_project(
    *, session: Session, user: CurrentUser, project_id: str, request: ReviseProjectRequest
) -> SavedProject:
    """Save a new revision of a project, designed on the server.

    Args:
        session: Open database session.
        user: The authenticated caller; the revision's author.
        project_id: The project.
        request: The project as entered now, and what changed.

    Returns:
        The project, at its new revision, with what the checks found.

    Raises:
        NotFoundError: If no such project is the caller's.
        ValidationError: If it holds the most revisions allowed, or its
            company settings are unknown or malformed.
    """
    tenant = _scope_to(session, user)
    try:
        project = _load(session, project_id, lock=True)
        # Malformed company settings are refused, not saved as a refused design.
        design_domain.resolve_profile(request.request.profile)
        # So is a typed feeder row, which an earlier revision may hold: the
        # user removes it and sets "Fed from" (the stored revision is untouched).
        project_design.refuse_client_feeders(request.request.boards)
        check_requirements_input(request.request, request.requirements_input)
        # Raising the revision count updates the row, which moves its
        # ``updated_at`` (the list's order) to now.
        latest_row = _revisions(session, project)[-1]
        prior = _stored_record(latest_row)
        configuration = request.configuration
        carried = False
        if configuration is None and not request.configuration_removed:
            # M7b: a save without a configuration carries the latest one.
            configuration = _carried_configuration(latest_row, prior)
            carried = configuration is not None
        _add_revision(
            session,
            project,
            tenant,
            user,
            request.request,
            request.note,
            configuration,
            requirements_input=request.requirements_input,
            prior=prior,
            carried=carried,
        )
        # The number this save took: by the time it is opened, a concurrent
        # save may have added a later one.
        number = project.revision_count
        session.commit()
    except IntegrityError as exc:
        _release(session)
        raise ConflictError(
            "another revision was saved at the same time; save again",
            code="revision_conflict",
        ) from exc
    except BaseException:
        # Any failure, a domain refusal or not, releases the project lock now
        # rather than when the session is closed.
        _release(session)
        raise
    logger.info(
        "design.project_revised",
        tenant_id=str(tenant),
        project_id=str(project.id),
        revision=number,
    )
    return _opened(session, project, number, user)


def open_project(
    *, session: Session, user: CurrentUser, project_id: str, revision: int | None = None
) -> SavedProject:
    """Open a project at its latest revision, or at one asked for.

    Args:
        session: Open database session.
        user: The authenticated caller.
        project_id: The project.
        revision: The revision to open; ``None`` for the latest.

    Returns:
        The project, its revisions, and that revision as entered, with its
        findings, snapshot hash, rules version, approval binding and export
        status.

    Raises:
        NotFoundError: If no such project or revision is the caller's.
    """
    _scope_to(session, user)
    # Asked for a revision, the answer is about that revision, as approve and
    # export answer: a project that is not the caller's is revision_not_found.
    project = (
        _load(session, project_id) if revision is None else _revision_project(session, project_id)
    )
    return _opened(session, project, revision, user)


def _revision_project(session: Session, project_id: str, *, lock: bool = False) -> DesignProjectRow:
    """The project a revision endpoint names; a missing one is ``revision_not_found``.

    Approve and export answer for a revision, so a project that is not the
    caller's (another tenant's, or none) is refused as its revision would be.
    """
    try:
        return _load(session, project_id, lock=lock)
    except NotFoundError as exc:
        raise NotFoundError("no such revision", code="revision_not_found") from exc


def _locked_revision(session: Session, project: DesignProjectRow, number: int) -> DesignRevisionRow:
    """The revision row, locked until the transaction ends (``SELECT … FOR UPDATE``)."""
    row = session.scalars(
        select(DesignRevisionRow)
        .where(DesignRevisionRow.project_id == project.id, DesignRevisionRow.number == number)
        .with_for_update()
        # Read what the lock protects, not what this session saw earlier.
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is None:
        raise NotFoundError("no such revision", code="revision_not_found")
    return row


def _check_snapshot(row: DesignRevisionRow, number: int) -> VersionedModel:
    """The revision's stored design, refused unless it is there, unaltered and readable.

    The hash is checked over the snapshot as stored; only then is it read,
    through ``read_snapshot``, which refuses a schema this version does not
    read (``snapshot_schema_unknown``) and content that does not validate.
    """
    if row.design_snapshot is None or row.snapshot_sha256 is None:
        raise ConflictError(
            f"revision {number} was saved before designs were validated on save; "
            "save it again as a new revision",
            code="revision_not_validated",
            params={"revision": number},
        )
    problem = _request_problem(row)
    if problem is not None:
        raise ConflictError(
            f"revision {number}'s saved project no longer validates "
            f"({problem.values['field']}); correct it and save it again as a new revision",
            code="revision_not_validated",
            params={"revision": number, "reason": "request_not_valid", **problem.values},
        )
    if not _snapshot_matches(row):
        raise ConflictError(
            f"revision {number}'s stored design does not match its recorded hash",
            code="snapshot_integrity_failed",
            params={"revision": number, "reason": "hash_mismatch"},
        )
    if not _requirements_match(row):
        raise ConflictError(
            f"revision {number}'s requirements record does not match its recorded hash",
            code="snapshot_integrity_failed",
            params={"revision": number, "reason": "requirements_mismatch"},
        )
    try:
        read = read_snapshot(row.design_snapshot)
    except versions.SnapshotUnreadableError as exc:
        raise ConflictError(
            f"revision {number}'s stored design no longer reads as a design",
            code="snapshot_integrity_failed",
            params={"revision": number, "reason": "unreadable"},
        ) from exc
    anchored = _anchored(read, row)
    if anchored is None:
        raise ConflictError(
            f"revision {number}'s stored design holds a review status this server never issued",
            code="snapshot_integrity_failed",
            params={"revision": number, "reason": "review_not_issued"},
        )
    if _findings_mismatch(row, anchored):
        raise ConflictError(
            f"revision {number}'s stored findings are not the findings of its stored design "
            "under the rules it records; save it again as a new revision",
            code="snapshot_integrity_failed",
            params={"revision": number, "reason": "findings_mismatch"},
        )
    return anchored


def _recorded_rules_version(row: DesignRevisionRow, applicable: _Applicable) -> str:
    """The rules version the stale check found out of date.

    The revision's own when it was designed under other rules; else, for an
    approval granted under other rules, the version that approval recorded.
    """
    if row.rules_version != applicable.rules_version or row.approved_at is None:
        return row.rules_version or ""
    return row.approved_rules_version or row.rules_version or ""


def _refuse_stale(row: DesignRevisionRow, number: int, applicable: _Applicable) -> None:
    """Refuse a revision designed, or approved, under other rules or settings.

    An approved revision whose approval was granted under another policy is
    refused as well, the parts that changed since the approval listed: an
    export never prints it as approved, nor quietly as a draft.
    """
    changed = list(_changed(row, applicable))
    approval = _approval_changed(row, applicable)
    changed += [
        part
        for part in approval
        # Another design approved is not a policy change: it exports as a draft,
        # and which renderer printed that other design does not matter.
        # Nor is a requirements record other than the one approved (or an
        # approval that recorded none): the approval does not count, and the
        # revision exports as a draft, never as approved.
        if part not in ("snapshot", "requirements")
        and not (part == "renderer_version" and "snapshot" in approval)
        and part not in changed
    ]
    if changed:
        raise ConflictError(
            f"revision {number} was designed under other rules or settings "
            f"({', '.join(changed)} changed); save it again as a new revision",
            code="revision_stale",
            params={
                "revision": number,
                "changed": ",".join(changed),
                # The version each check compared: the revision's own for the
                # design, the approval's for the approval, which may differ.
                "saved_rules_version": row.rules_version or "",
                "recorded_rules_version": _recorded_rules_version(row, applicable),
                "current_rules_version": applicable.rules_version,
                "approved_renderer_version": (
                    (row.approved_renderer_version or RENDERER_BASELINE)
                    if row.approved_at is not None
                    else ""
                ),
                "current_renderer_version": applicable.renderer_version,
            },
        )


def approve_revision(
    *,
    session: Session,
    user: CurrentUser,
    project_id: str,
    number: int,
    request: ApproveRevisionRequest,
) -> SavedProject:
    """Approve one revision of a project, once, bound to the design it holds.

    The project row, then the revision row, is locked while it is checked
    and approved, so two approvals cannot both pass the check (nor a delete
    remove it meanwhile), and the approval records the
    snapshot hash it was given for.

    Args:
        session: Open database session.
        user: The authenticated caller; recorded as the approving account.
        project_id: The project.
        number: The revision.
        request: An optional note to the approval. It names no one: the
            approver is ``user``, shown by its display name.

    Returns:
        The project, opened at that revision.

    Raises:
        NotFoundError: If no such project or revision is the caller's.
        ConflictError: If the revision is approved already (``revision_already_approved``).
        ConflictError: If it holds no validated design
            (``revision_not_validated``), its design was altered
            (``snapshot_integrity_failed``), is in a schema this version does not
            read (``snapshot_schema_unknown``), its rules or settings changed
            (``revision_stale``), or it has blocking findings
            (``revision_has_blocking_findings``), or its author is not
            established (``approval_author_not_established``), or its
            stored requirements record holds an unresolved safety concern
            (``approval_blocked_by_safety``, params ``count`` and ``codes``).
        AuthorizationError: If the caller saved it (``approval_by_author``).
        AuthorizationError: If the caller is not a signed-in account
            (``approval_requires_account``: a trial has none), their plan
            does not include approval, or they are neither the tenant's owner
            nor an admin nor a reviewer (``approval_not_permitted``).
    """
    _require_account(user)
    billing.require_feature(session=session, user=user, feature=Feature.APPROVAL_WORKFLOW)
    tenant = _scope_to(session, user)
    try:
        # The project row first, then the revision's, in the order a save and
        # a delete take them: a delete cannot remove a revision as it is
        # approved, and neither can wait on the other's second lock.
        project = _revision_project(session, project_id, lock=True)
        # Checked once the project is found as the caller's, so another
        # tenant's project stays "not found" rather than "forbidden".
        permissions.require(session=session, user=user, permission=Permission.APPROVE_REVISION)
        row = _locked_revision(session, project, number)
        applicable = _approvable(session, project, row, number)
        # Under the same locks: no approval while a safety concern of the
        # stored (hash-checked) record stands unresolved.
        _refuse_unresolved_safety(row, number, _found(row, _revisions(session, project)))
        # After what the revision itself refuses (approved, stale, blocked):
        # those say what is wrong with the revision, this who may approve it.
        _refuse_author(row, user, number)
        # Who approves is the signed-in account, from its token: the request
        # names no one. ``approved_by`` (the legacy typed name) stays NULL.
        row.approved_account = user.email
        row.approved_user_id = _account_id(user)
        row.approval_note = request.note or None
        # The scope statement the approver ticked (strictly true, schema-checked).
        row.approved_scope_version = approval_scope.APPROVAL_SCOPE_VERSION
        row.approved_at = datetime.now(UTC)
        row.approved_snapshot_sha256 = row.snapshot_sha256
        # Checked against its hash under the lock (``_check_snapshot``).
        row.approved_requirements_sha256 = _requirements_binding(row)
        # The policy it is granted under (checked current just now), and the
        # name it is granted by, as every export will print them.
        row.approved_profile_sha256 = applicable.profile_sha256
        row.approved_rules_version = applicable.rules_version
        row.approved_catalogue_version = applicable.catalogue_version
        row.approved_renderer_version = applicable.renderer_version
        row.approved_findings_policy = (
            _findings_fingerprint(applicable.findings) if applicable.findings is not None else None
        )
        approver = _account_id(user)
        named = _display_names(session, project.tenant_id, [], also=approver)
        row.approved_display_name = named.by_id.get(approver, "") if approver is not None else ""
        session.commit()
    except BaseException:
        # Release the locks now, whatever failed, not when the session is closed.
        _release(session)
        raise
    logger.info(
        "design.revision_approved",
        tenant_id=str(tenant),
        project_id=str(project.id),
        revision=number,
        snapshot_sha256=row.snapshot_sha256,
    )
    return _opened(session, project, number, user)


def resave_revision(
    *, session: Session, user: CurrentUser, project_id: str, number: int
) -> SavedProject:
    """Save revision ``number``'s stored request again, as the caller's new latest revision.

    The recovery for a revision whose author is not established: its stored
    request is designed again under today's rules and saved as a new
    revision whose author is the caller (note ``Re-saved from revision <n>
    for independent review``; its record's ``resaved_from`` is ``n``), for a
    different member to approve. Control needs carry; an acknowledgement
    carries, with its original account and time, while its question still
    stands, as on an answer. Nothing of revision ``number`` (its snapshot,
    hashes, record or approval) is changed.

    A request today's rules refuse is refused as a save would refuse it
    (``feeder_server_owned``, ``sub_board_load_server_owned``, ...), and
    nothing is saved: the user's data is never stripped or rewritten here;
    they fix it in the editor and save normally.

    Raises:
        NotFoundError: ``revision_not_found`` (also another tenant's project).
        AuthorizationError: ``resave_requires_account`` (a trial: its revision
            would again record no author).
        ConflictError: ``revision_not_validated`` (``request_not_valid``: the
            stored request no longer parses; ``configuration_not_carried``:
            its design holds a configuration, which a re-save would drop),
            ``snapshot_integrity_failed`` (``requirements_mismatch``),
            ``revision_conflict``.
        ValidationError: As a revision save refuses the request.
    """
    tenant = _scope_to(session, user)
    try:
        project = _revision_project(session, project_id, lock=True)
        row = session.scalars(
            select(DesignRevisionRow).where(
                DesignRevisionRow.project_id == project.id, DesignRevisionRow.number == number
            )
        ).one_or_none()
        if row is None:
            raise NotFoundError("no such revision", code="revision_not_found")
        if not user.email or _account_id(user) is None:
            raise AuthorizationError(
                "re-saving a revision for review requires a signed-in account",
                code="resave_requires_account",
            )
        if row.requirements is not None and not _requirements_match(row):
            raise ConflictError(
                f"revision {number}'s requirements record does not match its recorded hash",
                code="snapshot_integrity_failed",
                params={"revision": number, "reason": "requirements_mismatch"},
            )
        checked = _validation(row, thorough=False)
        record = _record_of(row, checked)
        if record is None:
            raise ConflictError(
                f"revision {number}'s saved project no longer validates; correct it and save",
                code="revision_not_validated",
                params={"revision": number, "reason": "request_not_valid"},
            )
        configuration, configuration_status = _configuration_of(row, record)
        if configuration_status == "not_recoverable":
            # The configuration is composed into the snapshot, not kept apart:
            # a re-save of the request alone would silently drop it.
            raise ConflictError(
                f"revision {number} holds a connection configuration a re-save cannot "
                "carry: it is stored only inside that revision's design and carrying it "
                "is not proven lossless, so it is never dropped; the revision and its "
                "configuration are kept unchanged. Save a new revision from the editor "
                "and enter the configuration again",
                code="revision_not_validated",
                params={"revision": number, "reason": "configuration_not_carried"},
            )
        entered = ProjectDesignRequest.model_validate(row.request)
        # Refused exactly as a save refuses them, the 422 unchanged.
        design_domain.resolve_profile(entered.profile)
        project_design.refuse_client_feeders(entered.boards)
        _add_revision(
            session,
            project,
            tenant,
            user,
            entered,
            f"Re-saved from revision {number} for independent review",
            configuration,
            prior=record if not record.get("derived") else {**record, "acknowledgements": []},
            resaved_from=number,
            carried=configuration is not None,
        )
        latest = project.revision_count
        session.commit()
    except IntegrityError as exc:
        _release(session)
        raise ConflictError(
            "another revision was saved at the same time; save again",
            code="revision_conflict",
        ) from exc
    except BaseException:
        _release(session)
        raise
    logger.info(
        "design.revision_resaved",
        tenant_id=str(tenant),
        project_id=str(project.id),
        revision=latest,
        resaved_from=number,
    )
    return _opened(session, project, latest, user)


def _author_unknown(row: DesignRevisionRow) -> bool:
    """Whether the revision records no author (saved before authors were): never guessed."""
    return row.author_user_id is None and not (row.author or "").strip()


def _author_established(row: DesignRevisionRow) -> bool:
    """Whether the revision records the saving account's id: the only proof of its author.

    A row saved before ``author_user_id`` existed records at most an email,
    which another account may since have taken, or its author changed: such
    an author is not established, and no id is ever guessed from the email.
    """
    return row.author_user_id is not None


def _is_author(row: DesignRevisionRow, user: CurrentUser) -> bool:
    """Whether ``user`` saved the revision, by the saving account's id alone.

    An email changed since, or an email later given to another account,
    changes nothing. A row without the id names no author (it is not
    established, and is never matched by email).
    """
    return row.author_user_id is not None and row.author_user_id == _account_id(user)


def _refuse_author(row: DesignRevisionRow, user: CurrentUser, number: int) -> None:
    """Refuse an approval no independent reviewer can be shown to give (M6a §4).

    A revision whose author is not established (no ``author_user_id``: saved
    before it was recorded, whatever email it holds) is refused 409
    ``approval_author_not_established``: it is saved again
    (``resave_revision``) and the new revision approved. One saved by the
    approver's own account is refused 403 ``approval_by_author``, owners
    included.
    """
    if not _author_established(row):
        raise ConflictError(
            f"revision {number}'s author cannot be established, so its approval "
            "could not be shown to be independent; save it as a new revision for review",
            code="approval_author_not_established",
            params={"revision": number},
        )
    if _is_author(row, user):
        raise AuthorizationError(
            f"revision {number} was saved by you; another member must approve it",
            code="approval_by_author",
            params={"revision": number},
        )


def _require_account(user: CurrentUser) -> None:
    """Refuse an approval by a caller with no account: it would name no one.

    A trial token resolves to an engineer with no email and no account row,
    so an approval it made could identify nobody.
    """
    if not user.email or _account_id(user) is None:
        raise AuthorizationError(
            "approving a revision requires a signed-in account",
            code="approval_requires_account",
        )


def _approvable(
    session: Session,
    project: DesignProjectRow,
    row: DesignRevisionRow,
    number: int,
) -> _Applicable:
    """Refuse approving a revision that is approved, unvalidated, stale or blocked.

    Returns:
        The policy it is approvable under, resolved from its snapshot as read
        once here (hash first), which the approval records.
    """
    if row.approved_at is not None:
        approver = _display_names(session, project.tenant_id, [row]).printed_approver(row)
        raise ConflictError(
            f"revision {number} is approved already, by {approver}",
            code="revision_already_approved",
            params={"revision": str(number), "approver": approver},
        )
    read = _check_snapshot(row, number)
    applicable = _applicable(row, read=read)
    _refuse_stale(row, number, applicable)
    blocking = integrity.blocking(_stored_findings(row))
    if blocking:
        raise ConflictError(
            f"revision {number} has {len(blocking)} blocking finding(s); "
            "fix them and save a new revision",
            code="revision_has_blocking_findings",
            params={
                "revision": number,
                "count": len(blocking),
                "codes": ",".join(sorted({found.code for found in blocking})),
            },
        )
    return applicable


def _revision_mark(
    project: DesignProjectRow,
    row: DesignRevisionRow,
    findings: list[DesignFinding],
    names: _Names,
    approval_counts: bool,
    renderer: str,
    lines: Sequence[str] = (),
    found: Sequence[safety_review.Concern] = (),
) -> marking.ExportMark:
    digest = (row.snapshot_sha256 or "")[:SHORT_HASH]
    where = f"{project.name}, revision {row.number:02d}"
    if approval_counts and row.approved_at is not None:
        approver = names.printed_approver(row)
        identity = (
            f"{where}, approved by {approver} on {row.approved_at.date().isoformat()}, "
            f"design {digest}, rules {row.rules_version}, renderer {renderer}"
        )
        label = marking.REVIEW_APPROVED_LABEL
    else:
        identity = (
            f"{where}, not approved, design {digest}, rules {row.rules_version}, "
            f"renderer {renderer}"
        )
        label = marking.DRAFT_LABEL
    approved = approval_counts and row.approved_at is not None
    stored = _stored_record(row) if row.requirements is not None else None
    printed = _printed_name(names)
    excluded: list[str] = []
    false_positives: list[str] = []
    for line in safety_review.resolution_lines(found, stored, name_of=printed):
        (excluded if line.startswith("[EXCLUDED FROM SCOPE]") else false_positives).append(line)
    scope = approval_scope.statement_lines()
    if approved:
        scope.append(approval_scope.confirmed_line(row.approved_scope_version))
    return marking.ExportMark(
        label=label,
        identity=identity,
        findings=tuple(findings),
        requirements=tuple(lines),
        scope=tuple(scope),
        approval=approval_scope.block(
            approved=approved,
            scope_version=row.approved_scope_version,
            excluded=excluded,
            false_positives=false_positives,
        ),
    )


def _printed_name(names: _Names) -> Callable[[str | None], str]:
    """Who an exported line names, from an account id: display name, else "a team member"."""

    def printed(user_id: str | None) -> str:
        try:
            account = uuid.UUID(str(user_id))
        except ValueError:
            return APPROVER_FALLBACK
        return names.by_id.get(account) or APPROVER_FALLBACK

    return printed


def _estimate_subject(
    project: DesignProjectRow, row: DesignRevisionRow, approval_counts: bool, renderer: str
) -> str:
    """The design a quotation of this revision prices, without approving its prices.

    It names the revision, its design hash, rules and renderer, and whether
    the design's review approval counts: not who approved it, nor any word
    that a reader could take for an approval of the estimate.
    """
    digest = (row.snapshot_sha256 or "")[:SHORT_HASH]
    review = (
        "design review approved (covers the design only)"
        if approval_counts
        else ("design not review approved")
    )
    return (
        f"{_quoted_name(project.name)}, revision {row.number:02d}, design {digest}, "
        f"rules {row.rules_version}, renderer {renderer}; {review}"
    )


#: The longest project name an estimate's stamp quotes; the rest is elided.
_ESTIMATE_NAME_CHARS = 60


def _quoted_name(name: str) -> str:
    """A project name as an estimate's stamp prints it: quoted and capped.

    Quoted, so a name such as "REVIEW APPROVED by …" reads as a name; capped,
    so the stamp keeps room for what follows it.
    """
    if len(name) > _ESTIMATE_NAME_CHARS:
        name = name[: _ESTIMATE_NAME_CHARS - 1] + "…"
    return f"“{name}”"


def export_revision(
    *,
    session: Session,
    user: CurrentUser,
    project_id: str,
    number: int,
    request: RevisionExportRequest,
) -> design_domain.ExportedFile:
    """Export a saved revision from the design the server stored for it.

    Nothing the client sends but the format and pricing is used: the design is
    the revision's snapshot, its hash checked again; the approval, approver
    and date are the database's, and count only for the design approved.

    Args:
        session: Open database session.
        user: The authenticated caller.
        project_id: The project.
        number: The revision.
        request: The format, pricing, and purpose.

    Returns:
        The file, labelled draft or review-approved, with its findings.

    Raises:
        NotFoundError: If no such project or revision is the caller's
            (``revision_not_found``).
        ConflictError: ``revision_not_validated``, ``snapshot_integrity_failed``,
            ``snapshot_schema_unknown``,
            ``revision_stale``, ``export_format_unprotected`` for a format
            that cannot keep a mark once imported, or, for execution, always
            ``execution_package_incomplete``.
        ValidationError: If a quotation lacks pricing or a control program
            has no PLC-switched circuit.
        AuthorizationError: If the caller's plan does not include the format.
    """
    tenant = _scope_to(session, user)
    project = _revision_project(session, project_id)
    revisions = _revisions(session, project)
    row = next((r for r in revisions if r.number == number), None)
    if row is None:
        raise NotFoundError("no such revision", code="revision_not_found")
    findings = _stored_findings(row)
    if request.purpose is ExportPurpose.EXECUTION:
        # Refused before anything else is checked: whatever state the revision
        # is in, no execution package exists, and the refusal says all of why.
        checked = _validation(row)
        missing = _execution_blockers(checked)
        # Every integrity refusal is named by its own code: an altered design
        # and one in a schema this version does not read alike.
        missing.extend(
            sorted({found.code for found in checked.findings if found.code in _INTEGRITY_CODES})
        )
        raise ConflictError(
            "an execution package cannot be exported yet: "
            + ", ".join(marking.EXECUTION_PACKAGE_MISSING)
            + " are not produced",
            code="execution_package_incomplete",
            params={
                "revision": number,
                "missing": ",".join(marking.EXECUTION_PACKAGE_MISSING),
                "approval": "present" if checked.approval_bound else "missing",
                "blocking_findings": ",".join(
                    sorted({found.code for found in integrity.blocking(findings)})
                ),
                "blockers": ",".join(missing),
            },
        )
    design_domain.refuse_unprotected(request.format)
    read = _check_snapshot(row, number)
    applicable = _applicable(row, read=read)
    _refuse_stale(row, number, applicable)
    # Not stale, so any approval left that does not count approved another design.
    approval_counts = row.approved_at is not None and not _approval_changed(row, applicable)
    designed = read.project
    design_domain.require_format(session=session, user=user, export_format=request.format)
    names = _display_names(session, project.tenant_id, revisions, extra_ids=_acknowledgers(row))
    found = _found(row, revisions)
    mark = _revision_mark(
        project,
        row,
        _printed(findings, read.schema),
        names,
        approval_counts,
        applicable.renderer_version,
        _requirement_lines(row, read, names, found),
        found,
    )
    if request.format in design_domain.SCHEDULE_FORMATS:
        exported = _schedule_export(project, row, read, request.format, mark, applicable)
        logger.info(
            "design.revision_exported",
            tenant_id=str(tenant),
            project_id=str(project.id),
            revision=number,
            format=request.format.value,
            approved=approval_counts,
            snapshot_sha256=row.snapshot_sha256,
        )
        return exported
    company = design_domain.resolve_profile(
        ProjectDesignRequest.model_validate(row.request).profile
    )
    info: ProjectInfo = designed.info.model_copy(
        update={"revisions": _title_revisions(revisions, number, names, bound_to=applicable)}
    )
    exported = design_domain.render_export(
        designated=designed.model_copy(update={"info": info}),
        company=company,
        export_format=request.format,
        pricing=_revision_pricing(request.pricing),
        # A v2 revision prints its structural findings too (``_printed``),
        # under its own renderer version; a v1 revision prints what it did.
        mark=mark,
        estimate_for=_estimate_subject(project, row, approval_counts, applicable.renderer_version),
        # M7a: a v3 revision's JSON carries its links; v1 and v2 JSON is unchanged.
        links=(codec_v3.dump_links(read.links) if read.schema == versions.V3 else None),
    )
    logger.info(
        "design.revision_exported",
        tenant_id=str(tenant),
        project_id=str(project.id),
        revision=number,
        format=request.format.value,
        approved=approval_counts,
        snapshot_sha256=row.snapshot_sha256,
    )
    return exported


def _schedule_export(
    project: DesignProjectRow,
    row: DesignRevisionRow,
    read: VersionedModel,
    export_format: ExportFormat,
    mark: marking.ExportMark,
    applicable: _Applicable,
) -> design_domain.ExportedFile:
    """A review-only schedule or connection drawing, from the saved v2 model alone.

    All three documents of a revision are made here, from ``read``: the one
    snapshot read (``_check_snapshot``, its hash checked and its reviews
    anchored) that the gates above passed. The drawing's terminals and wires
    are the schedules' own rows (``connection_drawing.drawing_set``), and it
    prints the revision's snapshot hash on every sheet.

    Raises:
        ConflictError: ``schedule_requires_v2`` for a v1 revision: it holds
            no terminals, nets or wires to schedule.
    """
    if read.v2 is None:
        raise ConflictError(
            f"revision {row.number} is stored in {read.schema}, which holds no terminals, "
            "nets or wires to schedule",
            code="schedule_requires_v2",
            params={"revision": row.number, "schema": read.schema, "format": export_format.value},
        )
    model = read.v2
    header = schedule_pdf.ScheduleHeader(
        project=project.name,
        revision=f"{row.number:02d}",
        design_sha256=row.snapshot_sha256 or "",
        schema=read.schema,
        rules_version=row.rules_version or "",
        renderer_version=applicable.renderer_version,
    )
    structural = [
        design_configuration.finding_view(found, _V2_CONFIRM)
        for found in findings_v2.structural_findings(model)
    ]
    other = [found for found in mark.findings if not _structural(found)]
    plain = marking.ExportMark(
        label=mark.label,
        identity=mark.identity,
        findings=tuple(other),
        requirements=mark.requirements,
        scope=mark.scope,
        approval=mark.approval,
    )
    # M7a: a v3 revision's links, printed from one set of rows by both the
    # wire schedule and the drawing; none for v2, whose files are unchanged.
    link_rows = link_model.rows(model, read.links)
    if export_format is ExportFormat.CONNECTION_DRAWING_PDF:
        content = connection_drawing_pdf.render_connection_drawing(
            connection_drawing.drawing_set(model),
            connection_drawing_pdf.DrawingHeader(
                project=header.project,
                revision=header.revision,
                design_sha256=header.design_sha256,
                schema=header.schema,
                rules_version=header.rules_version,
                renderer_version=header.renderer_version,
            ),
            plain,
            structural,
            link_rows,
        )
        suffix = "connection-drawing.pdf"
    elif export_format is ExportFormat.TERMINAL_SCHEDULE_PDF:
        content = schedule_pdf.render_terminal_schedule(
            schedules.terminal_schedule(model), header, plain, structural
        )
        suffix = "terminal-schedule.pdf"
    else:
        content = schedule_pdf.render_wire_schedule(
            schedules.wire_schedule(model), header, plain, structural, link_rows
        )
        suffix = "wire-schedule.pdf"
    return design_domain.exported_file(content, "application/pdf", project.name, suffix)


def validate_configuration(
    *,
    session: Session,
    user: CurrentUser,
    project_id: str,
    request: ConfigurationValidateRequest,
) -> ConfigurationReview:
    """Compose a configuration with the latest revision's request, designed now; save nothing.

    Args:
        session: Open database session.
        user: The authenticated caller.
        project_id: The project.
        request: The configuration.

    Returns:
        The composed model, its findings and schedules, and the hash it
        would be stored under.

    Raises:
        NotFoundError: If no such project is the caller's.
        ConflictError: ``revision_not_validated`` if the latest revision's
            request no longer validates.
        ValidationError: ``configuration_too_large``,
            ``configuration_claims_review``, ``configuration_ref_reserved`` or
            ``configuration_refused``.
    """
    _scope_to(session, user)
    project = _load(session, project_id)
    revisions = _revisions(session, project)
    latest = revisions[-1]
    try:
        entered = ProjectDesignRequest.model_validate(latest.request)
    except PydanticValidationError as exc:
        raise ConflictError(
            f"revision {latest.number}'s saved project no longer validates; "
            "correct it and save it again first",
            code="revision_not_validated",
            params={"revision": latest.number, "reason": "request_not_valid"},
        ) from exc
    design_configuration.check_configuration(request.configuration)
    project_design.refuse_client_feeders(entered.boards)
    as_entered = entered.model_copy(
        update={"info": entered.info.model_copy(update={"revisions": []})}
    )
    try:
        designed, _company = design_domain.build_design(as_entered)
    except ValidationError as exc:
        raise ValidationError(
            f"the design was refused, so the configuration cannot be applied: {exc}",
            code="configuration_refused",
            params={"refusal": "design_refused", **exc.params},
        ) from exc
    stored = design_configuration.compose_snapshot(
        designed.model_dump(mode="json"), request.configuration
    )
    read = read_snapshot(stored)
    model = read.v2
    if model is None:  # pragma: no cover - compose always writes v2
        raise ValidationError("the configuration did not compose", code="configuration_refused")
    model = anchoring.anchor_model(
        model, engineering_reviews.anchor_for(session=session, tenant_id=project.tenant_id)
    )
    conditions = {board.name: board.conditions for board in entered.boards}
    written = _written_schema()
    return ConfigurationReview(
        based_on_revision=latest.number,
        snapshot_schema=read.schema,
        written_schema=written,
        saveable=written in (versions.V2, versions.V3),
        snapshot_sha256=snapshot.sha256_of(stored),
        findings=_design_findings(read.project, conditions, model),
        model=design_configuration.summary(model, _V2_CONFIRM),
    )


def _revision_pricing(pricing: PricingSettings | None) -> PricingSettings | None:
    """The pricing a revision's quotation applies: prices and rates only.

    The client's pricing sets what an item costs, never what is quoted: the
    devices, cables and quantities are the snapshot's. A default cable length
    would turn a cable the revision gives no length into metres it never
    designed, so it is dropped and such a cable stays unpriced, "no length".
    """
    if pricing is None:
        return None
    return pricing.model_copy(update={"cable_length_m": None})


def _cursor(updated_at: datetime, project_id: uuid.UUID) -> str:
    raw = f"{updated_at.isoformat()}|{project_id}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        when, key = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return datetime.fromisoformat(when), uuid.UUID(key)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationError("not a cursor this service issued", code="bad_cursor") from exc


def list_projects(
    *, session: Session, user: CurrentUser, limit: int = _PAGE_DEFAULT, cursor: str | None = None
) -> ProjectPage:
    """List the caller's saved projects, most recently saved first.

    Args:
        session: Open database session.
        user: The authenticated caller.
        limit: Most rows to return; held to a server-side maximum.
        cursor: From a previous page, or ``None`` for the first.

    Returns:
        One page of projects.

    Raises:
        ValidationError: If ``limit`` is not positive or the cursor is not one
            this service issued.
    """
    _scope_to(session, user)
    if limit < 1:
        raise ValidationError("limit must be at least 1")
    limit = min(limit, _PAGE_MAX)
    query = select(DesignProjectRow).order_by(
        DesignProjectRow.updated_at.desc(), DesignProjectRow.id.desc()
    )
    if cursor is not None:
        when, key = _decode(cursor)
        query = query.where(
            tuple_(DesignProjectRow.updated_at, DesignProjectRow.id) < tuple_(when, key)
        )
    rows = session.scalars(query.limit(limit + 1)).all()
    page = rows[:limit]
    return ProjectPage(
        projects=[
            ProjectSummary(
                id=str(row.id),
                name=row.name,
                revisions=row.revision_count,
                updated_at=row.updated_at.isoformat(),
            )
            for row in page
        ],
        next_cursor=_cursor(page[-1].updated_at, page[-1].id) if len(rows) > limit else None,
        permissions=permissions.allowed(session=session, user=user),
    )


def delete_project(*, session: Session, user: CurrentUser, project_id: str) -> None:
    """Delete a project and every revision of it.

    Args:
        session: Open database session.
        user: The authenticated caller.
        project_id: The project.

    Raises:
        NotFoundError: If no such project is the caller's.
        AuthorizationError: If the caller is not the tenant's owner
            (``project_delete_not_permitted``).
        ValidationError: If a revision of it is approved.
    """
    tenant = _scope_to(session, user)
    try:
        # Locked as an approval locks it, first: an approval in progress
        # finishes before this reads whether any revision is approved, and one
        # that starts after waits, then finds no project.
        project = _load(session, project_id, lock=True)
        # After the load: another tenant's project stays "not found".
        permissions.require(session=session, user=user, permission=Permission.DELETE_PROJECT)
        approved = session.scalars(
            select(DesignRevisionRow.number).where(
                DesignRevisionRow.project_id == project.id,
                DesignRevisionRow.approved_at.is_not(None),
            )
        ).first()
        if approved is not None:
            raise ValidationError(
                "a project with an approved revision cannot be deleted",
                code="project_approved",
                params={"revision": str(approved)},
            )
        session.delete(project)
        session.commit()
    except BaseException:
        _release(session)
        raise
    logger.info("design.project_deleted", tenant_id=str(tenant), project_id=project_id)


# --------------------------------------------------------------------------- #
# Requirements records (M6a, ``pp-req/1``)
# --------------------------------------------------------------------------- #


def check_requirements_input(
    request: ProjectDesignRequest, given: RequirementsInput | None
) -> None:
    """Refuse guided input that names no load, or a load that is not controlled.

    Raises:
        ValidationError: ``control_load_unknown`` (no such board or load),
            ``control_need_requires_controlled`` (the load's ``controlled``
            flag, the designer's only control model, is not set), or
            ``control_given_twice``.
    """
    if given is None:
        return
    seen: set[tuple[int, int]] = set()
    for item in given.control:
        where = {"board_index": item.board_index, "load_index": item.load_index}
        if item.board_index >= len(request.boards) or item.load_index >= len(
            request.boards[item.board_index].loads
        ):
            raise ValidationError(
                f"control needs name board {item.board_index}, load {item.load_index}, "
                "which the project does not have",
                code="control_load_unknown",
                params=where,
            )
        board = request.boards[item.board_index]
        if not board.loads[item.load_index].controlled:
            raise ValidationError(
                f"{board.name}, load {item.load_index + 1}: control needs are for a "
                "controlled load; mark it controlled first",
                code="control_need_requires_controlled",
                params={"board": board.name, "load_index": item.load_index},
            )
        if (item.board_index, item.load_index) in seen:
            raise ValidationError(
                f"{board.name}, load {item.load_index + 1}: control needs are given twice",
                code="control_given_twice",
                params={"board": board.name, "load_index": item.load_index},
            )
        seen.add((item.board_index, item.load_index))


def _stored_record(row: DesignRevisionRow) -> dict[str, Any] | None:
    """The revision's stored requirements record, when it has one matching its hash."""
    if row.requirements is None or not _requirements_match(row):
        return None
    return dict(row.requirements)


def _record_of(row: DesignRevisionRow, checked: _Validation | None = None) -> dict[str, Any] | None:
    """The revision's record: stored, or derived for an older revision; ``None`` if neither.

    A stored record that fails its hash is not shown (the revision opens as
    not validated, ``requirements_mismatch``); a derived one is built from
    the stored request and findings, never from a new design.
    """
    if row.requirements is not None:
        return _stored_record(row)
    if _request_problem(row) is not None:
        return None
    request = ProjectDesignRequest.model_validate(row.request)
    read = checked.read if checked is not None else None
    return requirements.build(
        request,
        read.project if read is not None else None,
        _stored_findings(row),
        derived=True,
    )


def _record_view(record: Mapping[str, Any] | None) -> RequirementsRecord | None:
    return RequirementsRecord.model_validate(record) if record is not None else None


def _acknowledgers(row: DesignRevisionRow) -> list[uuid.UUID]:
    """The account ids a stored record's acknowledgements name, for their display names."""
    out: list[uuid.UUID] = []
    stored = _stored_record(row) or {}
    for ack in [
        *stored.get("acknowledgements", []),
        *(stored.get("ai") or {}).get("clarifications", []),
        *(stored.get("ai") or {}).get("resolutions", []),
        *stored.get("safety_resolutions", []),
    ]:
        try:
            out.append(uuid.UUID(str(ack.get("by_user_id"))))
        except ValueError:
            continue
    return out


def _requirement_lines(
    row: DesignRevisionRow,
    read: VersionedModel,
    names: _Names,
    found: Sequence[safety_review.Concern] = (),
) -> list[str]:
    """What a review export prints of the revision's record: open items, then acknowledgements.

    An exported document never prints an email address: an acknowledgement
    names the account by its display name, else "a team member" (the in-app
    record may show the email to the tenant's members).
    """
    record = row.requirements if row.requirements is not None else None
    if record is None:
        if _request_problem(row) is not None:
            return []
        record = requirements.build(
            ProjectDesignRequest.model_validate(row.request),
            read.project,
            _stored_findings(row),
            derived=True,
        )

    stored = _stored_record(row) if row.requirements is not None else None
    shown = {**record, "open_items": _open_items_shown(record, found)}
    return [
        *requirements.open_item_lines(shown),
        # M6e: request and carried safety concerns, then every resolution.
        *safety_review.open_lines(found, stored),
        *requirements.acknowledged_lines(record, name_of=_printed_name(names)),
        *safety_review.resolution_lines(found, stored, name_of=_printed_name(names)),
    ]


def preview_requirements(
    *, user: CurrentUser, request: RequirementsPreviewRequest, session: Session | None = None
) -> RequirementsPreview:
    """The record a guided input would be stored with, and the design; nothing is saved.

    Raises:
        ValidationError: As a save refuses the request (a typed feeder, a
            control need on no controlled load, malformed settings), or the
            design's own refusal.
    """
    entered = request.request
    project_design.refuse_client_feeders(entered.boards)
    check_requirements_input(entered, request.requirements_input)
    as_entered = entered.model_copy(
        update={"info": entered.info.model_copy(update={"revisions": []})}
    )
    designed, company = design_domain.build_design(as_entered)
    conditions = {board.name: board.conditions for board in entered.boards}
    findings = integrity.project_findings(designed, conditions)
    ai_block = None
    given = request.requirements_input
    if given.ai is not None:
        if session is None:  # pragma: no cover - the route always passes one
            raise RuntimeError("an AI block is previewed with a session")
        # Checked as a save checks it, read only: nothing is locked or marked.
        _scope_to(session, user)
        _, ai_block = design_ai.apply(
            session=session,
            user=user,
            request=entered,
            given=given.ai,
            control=given.control,
            lock=False,
        )
        session.rollback()
    record = requirements.build(entered, designed, findings, given, ai=ai_block)
    logger.info("design.requirements_previewed", tenant_id=user.tenant_id)
    return RequirementsPreview(
        requirements=RequirementsRecord.model_validate(record),
        design=BoardDesignResponse(project=designed, profile=company),
        findings=findings,
    )


def _question_in(record: Mapping[str, Any], question_id: str) -> dict[str, Any]:
    found = next((q for q in record.get("questions", []) if q["id"] == question_id), None)
    if found is None:
        raise NotFoundError("no such question", code="question_not_found")
    return dict(found)


def _answered_request(
    request: ProjectDesignRequest,
    record: Mapping[str, Any],
    question: Mapping[str, Any],
    value: Any,
) -> ProjectDesignRequest:
    """The request with an answer typed in where its question points."""
    body = request.model_dump(mode="json")
    subject = question.get("subject") or {}
    names = {b["ref"]: b["name"] for b in record.get("boards", [])}
    board_name = names.get(subject.get("board_ref") or "")
    index = next((i for i, b in enumerate(body["boards"]) if b["name"] == board_name), None)
    if index is None:
        raise ConflictError(
            "the question's board is no longer in the project", code="question_not_answerable"
        )
    board = body["boards"][index]
    field = question["field"]
    if field == "fault_level_ka":
        board.setdefault("supply", {})["fault_level_ka"] = str(value)
    elif field == "ze_ohm":
        board.setdefault("supply", {})["earth_loop_ohm"] = str(value)
    elif field == "feeder_length_m":
        board["feeder_length_m"] = str(value)
    elif field == "power_factor":
        refs = [
            load["ref"]
            for load in record.get("loads", [])
            if load["board_ref"] == subject.get("board_ref")
        ]
        try:
            position = refs.index(subject.get("load_ref"))
        except ValueError:
            position = -1
        if not 0 <= position < len(board["loads"]):
            raise ConflictError(
                "the question's load is no longer in the project", code="question_not_answerable"
            )
        board["loads"][position]["power_factor"] = str(value)
    else:  # pragma: no cover - guarded by ``answerable``
        raise ConflictError("this question takes no value", code="question_not_answerable")
    try:
        return ProjectDesignRequest.model_validate(body)
    except PydanticValidationError as exc:
        raise ValidationError(
            f"{value} is not a valid {field}",
            code="answer_not_valid",
            params={"field": field, "value": value},
        ) from exc


def answer_question(
    *,
    session: Session,
    user: CurrentUser,
    project_id: str,
    number: int,
    question_id: str,
    action: QuestionAction,
) -> SavedProject | AcknowledgedRequirements:
    """Answer a question (a new revision) or acknowledge it (recorded on this revision).

    Who and when are the signed-in account and the server's clock. An answer
    is typed into the latest revision's request and saved as a new revision,
    designed on the server; an acknowledgement is appended to the revision's
    stored record, its hash recomputed, the design untouched.

    Returns:
        The project at the new revision for an answer; the updated record for
        an acknowledgement.

    Raises:
        NotFoundError: ``revision_not_found`` (also another tenant's project),
            ``question_not_found``.
        ConflictError: ``requirements_derived`` (acknowledging an older
            revision's derived record), ``revision_already_approved``
            (acknowledging an approved revision), ``revision_not_latest``
            (answering or acknowledging a revision a later one replaced),
            ``question_not_answerable``, ``question_answered``,
            ``snapshot_integrity_failed`` (``requirements_mismatch``).
        AuthorizationError: ``acknowledgement_requires_account`` (a trial).
        ValidationError: ``answer_not_valid``, or as a revision save refuses.
    """
    tenant = _scope_to(session, user)
    try:
        project = _revision_project(session, project_id, lock=True)
        row = _locked_revision(session, project, number)
        if row.requirements is not None and not _requirements_match(row):
            raise ConflictError(
                f"revision {number}'s requirements record does not match its recorded hash",
                code="snapshot_integrity_failed",
                params={"revision": number, "reason": "requirements_mismatch"},
            )
        now = datetime.now(UTC).isoformat()
        result: AcknowledgedRequirements | None = None
        account = _account_id(user)
        named = _display_names(session, project.tenant_id, [], also=account)
        # Never empty, never invented: the display name, else the account's
        # email (shown to the tenant's members on the Team page), else
        # "unknown account".
        by = (
            (named.by_id.get(account) if account is not None else None)
            or user.email
            or requirements.UNKNOWN_ACCOUNT
        )
        if action.action == "acknowledge":
            result = AcknowledgedRequirements(
                requirements=_acknowledge(
                    row, number, project.revision_count, question_id, user, by, now
                )
            )
        else:
            latest = project.revision_count
            if number != latest:
                raise ConflictError(
                    f"revision {number} is not the latest; answer on revision {latest}",
                    code="revision_not_latest",
                    params={"revision": number, "latest": latest},
                )
            record = _record_of(row, _validation(row, thorough=False))
            if record is None:
                raise ConflictError(
                    f"revision {number}'s saved project no longer validates",
                    code="revision_not_validated",
                    params={"revision": number, "reason": "request_not_valid"},
                )
            question = _question_in(record, question_id)
            if question["state"] == "answered":
                raise ConflictError("this question is answered", code="question_answered")
            if not question.get("answerable"):
                raise ConflictError(
                    "no input holds this value; acknowledge the assumption instead",
                    code="question_not_answerable",
                    params={"field": question.get("field") or ""},
                )
            # M7b: the answer carries the revision's configuration (refused if lost).
            configuration = _carried_configuration(row, record)
            entered = ProjectDesignRequest.model_validate(row.request)
            answered = _answered_request(entered, record, question, action.value)
            # A typed feeder an earlier revision holds is refused here too.
            project_design.refuse_client_feeders(answered.boards)
            design_domain.resolve_profile(answered.profile)
            _add_revision(
                session,
                project,
                tenant,
                user,
                answered,
                f"Answered: {question['field']}",
                configuration,
                prior=record if not record.get("derived") else {**record, "acknowledgements": []},
                answered=[
                    {
                        **question,
                        "answer": format(action.value, "f"),
                        "by_user_id": user.id,
                        "by_name": by,
                        "at": now,
                    }
                ],
                carried=configuration is not None,
            )
            number = project.revision_count
        session.commit()
    except IntegrityError as exc:
        _release(session)
        raise ConflictError(
            "another revision was saved at the same time; save again",
            code="revision_conflict",
        ) from exc
    except BaseException:
        _release(session)
        raise
    logger.info(
        "design.question_" + ("acknowledged" if action.action == "acknowledge" else "answered"),
        tenant_id=str(tenant),
        project_id=str(project.id),
        revision=number,
        question=question_id,
    )
    if result is None:
        return _opened(session, project, number, user)
    return result


def _acknowledge(
    row: DesignRevisionRow,
    number: int,
    latest: int,
    question_id: str,
    user: CurrentUser,
    by: str | None,
    now: str,
) -> RequirementsRecord:
    if not user.email or _account_id(user) is None:
        raise AuthorizationError(
            "acknowledging an assumption requires a signed-in account",
            code="acknowledgement_requires_account",
        )
    record = _stored_record(row)
    if record is None:
        raise ConflictError(
            f"revision {number} was saved before requirements were recorded; "
            "save it again as a new revision to acknowledge its assumptions",
            code="requirements_derived",
            params={"revision": number},
        )
    if row.approved_at is not None:
        raise ConflictError(
            f"revision {number} is approved; what it was approved with does not change",
            code="revision_already_approved",
            params={"revision": str(number)},
        )
    # Checked under the project lock, as an answer is: an acknowledgement of a
    # revision a later one replaced (an answer that committed first) would be
    # on no revision anyone works on, and missing from the latest one.
    if number != latest:
        raise ConflictError(
            f"revision {number} is not the latest; acknowledge on revision {latest}",
            code="revision_not_latest",
            params={"revision": number, "latest": latest},
        )
    question = _question_in(record, question_id)
    if question["state"] == "answered":
        raise ConflictError("this question is answered", code="question_answered")
    if question["state"] != "acknowledged":
        record = {
            **record,
            "acknowledgements": [
                *record.get("acknowledgements", []),
                {
                    "question_id": question_id,
                    "by_user_id": user.id,
                    "by_name": by,
                    "at": now,
                    # What it acknowledged: it carries forward only while unchanged.
                    **({"basis": question["basis"]} if question.get("basis") else {}),
                },
            ],
            "questions": [dict(q) for q in record["questions"]],
        }
        record = requirements.apply_acknowledgements(record)
        row.requirements = record
        row.requirements_sha256 = snapshot.sha256_of(record)
    return RequirementsRecord.model_validate(record)


def _rederive_open_items(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The record's open items with its AI ones derived again from its ``ai`` block, in place."""
    kept = [
        dict(o)
        for o in record.get("open_items", [])
        if not str(o.get("code", "")).startswith("ai_")
    ]
    return kept + ai_intake.open_items(record.get("ai"))


# --------------------------------------------------------------------------- #
# Safety concerns: the approval hold and its two resolutions (M6d, M6e)
# --------------------------------------------------------------------------- #


def _stored_of(row: DesignRevisionRow) -> safety_review.Stored:
    """A revision as stored, for re-deriving its concerns (its record read as stored).

    An earlier revision's record is read even when it fails its hash: an
    over-inclusive concern is safer than a dropped one.
    """
    record = row.requirements if isinstance(row.requirements, Mapping) else None
    request = row.request if isinstance(row.request, Mapping) else None
    return safety_review.Stored(number=row.number, request=request, record=record)


def _found(
    row: DesignRevisionRow, revisions: Sequence[DesignRevisionRow]
) -> list[safety_review.Concern]:
    """Every safety concern of ``row``: its own AI block and request, and every earlier revision's."""
    return safety_review.concerns(
        _stored_of(row), [_stored_of(r) for r in revisions if r.number < row.number]
    )


def _approval_held(
    found: Sequence[safety_review.Concern],
    record: Mapping[str, Any] | None,
) -> ApprovalHeld | None:
    """Why a revision cannot be approved now: its unresolved safety concerns, or ``None``.

    Generic acknowledgements never resolve one (they are not read here). A
    revision with no stored record (derived) cannot record a resolution, so
    any concern holds it for good: it is saved again.
    """
    unresolved = safety_review.unresolved(found, record)
    if not unresolved:
        return None
    return ApprovalHeld(count=len(unresolved), codes=sorted({c.code for c in unresolved}))


def _refuse_unresolved_safety(
    row: DesignRevisionRow, number: int, found: Sequence[safety_review.Concern]
) -> None:
    """Refuse approving a revision with an unresolved safety concern (under the approval's locks)."""
    held = _approval_held(found, _stored_record(row) if row.requirements is not None else None)
    if held is not None:
        raise ConflictError(
            f"revision {number} has {held.count} unresolved safety concern(s): "
            "each life-safety or possibly-safety mention, item or request entry (this "
            "revision's or an earlier one's) must be resolved by a second member before approval",
            code="approval_blocked_by_safety",
            params={"revision": number, "count": held.count, "codes": ",".join(held.codes)},
        )


def _open_items_shown(
    record: Mapping[str, Any], found: Sequence[safety_review.Concern]
) -> list[dict[str, Any]]:
    """The record's open items without the AI safety ones a resolution in force now covers."""
    proposal = str(((record.get("ai") or {}).get("proposal") or {}).get("id") or "")
    resolved = set(safety_review.resolutions_in_force(found, record))
    out = []
    for item in record.get("open_items", []):
        subject = item.get("subject") or {}
        if (
            subject.get("mention_id")
            and ("mention", str(subject["mention_id"]), proposal) in resolved
        ):
            continue
        if (
            item.get("code") == "ai_unsupported"
            and (
                "item",
                str(subject.get("item_id")),
                proposal,
            )
            in resolved
        ):
            continue
        out.append(dict(item))
    return out


def _confirmed(row: safety_review.Candidate) -> ConfirmedLoad:
    board, index, description, power, kind = row
    return ConfirmedLoad(
        board=board, load_index=index, description=description, power_kw=power, kind=kind
    )


def _safety_concerns(
    found: Sequence[safety_review.Concern], record: Mapping[str, Any] | None
) -> list[SafetyConcern]:
    """Every safety concern of a revision, and how each stands, for the app."""
    resolved = safety_review.resolutions_in_force(found, record)
    out = []
    for c in found:
        entry = resolved.get(c.key)
        out.append(
            SafetyConcern(
                kind=c.kind,
                target_id=c.target_id,
                proposal_id=c.proposal_id,
                level=c.level,
                code=c.code,
                label=c.label,
                start=c.start,
                end=c.end,
                resolved=entry is not None,
                resolution=(
                    SafetyResolution.model_validate(
                        {
                            **{k: entry.get(k) for k in SafetyResolution.model_fields},
                            "target_kind": c.kind,
                            "target_id": c.target_id,
                        }
                    )
                    if entry is not None
                    else None
                ),
                false_positive_refusal=safety_review.refusal(c, safety_review.FALSE_POSITIVE),
                exclusion_refusal=safety_review.refusal(c, safety_review.EXCLUDED_FROM_SCOPE),
                raised_on=c.raised_on,
                carried=not c.present,
                where=c.where,
                confirmation_candidates=(
                    [_confirmed(row) for row in c.candidates] if c.needs_confirmation else None
                ),
            )
        )
    return out


#: Why a caller may not resolve a safety concern now (``SafetyResolutionEligibility``).
_RESOLVE_REFUSALS: dict[str, tuple[type[PanelPilotError], str, str]] = {
    "requires_account": (
        AuthorizationError,
        "safety_resolution_requires_account",
        "resolving a safety concern requires a signed-in account",
    ),
    "not_permitted": (
        AuthorizationError,
        "safety_resolution_not_permitted",
        "only a member who may approve revisions may resolve a safety concern",
    ),
    "requirements_derived": (
        ConflictError,
        "requirements_derived",
        "this revision was saved before requirements were recorded; save it again",
    ),
    "revision_already_approved": (
        ConflictError,
        "revision_already_approved",
        "this revision is approved; what it was approved with does not change",
    ),
    "revision_not_latest": (
        ConflictError,
        "revision_not_latest",
        "this revision is not the latest; resolve on the latest revision",
    ),
    "nothing_to_resolve": (
        NotFoundError,
        "safety_target_not_found",
        "this revision has no such safety concern",
    ),
    "proposal_author": (
        AuthorizationError,
        "safety_resolution_by_proposal_author",
        "you created this AI proposal; another member must resolve its safety concerns",
    ),
    "revision_author": (
        AuthorizationError,
        "safety_resolution_by_revision_author",
        "you saved this revision (or its author is not established); another member "
        "must resolve its safety concerns",
    ),
}


def _proposal_creators(
    row: DesignRevisionRow, revisions: Sequence[DesignRevisionRow]
) -> dict[str, str]:
    """Each AI proposal applied by this or an earlier revision, and the account that created it."""
    out: dict[str, str] = {}
    for r in [*revisions, row]:
        if r.number > row.number or not isinstance(r.requirements, Mapping):
            continue
        proposal = (r.requirements.get("ai") or {}).get("proposal") or {}
        if proposal.get("id"):
            out[str(proposal["id"])] = str(proposal.get("created_by_user_id") or "")
    return out


def _resolve_blocker(
    record: Mapping[str, Any] | None,
    row: DesignRevisionRow,
    latest: int,
    user: CurrentUser,
    found: Sequence[safety_review.Concern],
    *,
    may_approve: bool,
) -> str | None:
    """Why ``user`` may not resolve a safety concern of this revision now, or ``None``.

    In the order the endpoint checks: who (account, permission), the
    revision (derived, approved, latest), that it holds a concern at all,
    then four-eyes: not the creator of the revision's own AI proposal (an
    unknown creator is refused alike), not the revision's author (an author
    not established is refused alike). A concern of an earlier proposal is
    checked against that proposal's creator when it is resolved.
    """
    account = _account_id(user)
    if not user.email or account is None:
        return "requires_account"
    if not may_approve:
        return "not_permitted"
    if record is None or record.get("derived"):
        return "requirements_derived"
    if row.approved_at is not None:
        return "revision_already_approved"
    if row.number != latest:
        return "revision_not_latest"
    if not found:
        return "nothing_to_resolve"
    ai = record.get("ai")
    if ai:
        creator = str((ai.get("proposal") or {}).get("created_by_user_id") or "")
        if not creator or creator == str(account):
            return "proposal_author"
    if not _author_established(row) or _is_author(row, user):
        return "revision_author"
    return None


def resolution_eligibility(
    *,
    session: Session,
    row: DesignRevisionRow,
    record: Mapping[str, Any] | None,
    found: Sequence[safety_review.Concern],
    latest: int,
    user: CurrentUser,
) -> SafetyResolutionEligibility | None:
    """The flag a client shows the resolve actions by; ``None`` when there is no concern."""
    if not found:
        return None
    may = Permission.APPROVE_REVISION in permissions.allowed(session=session, user=user)
    reason = _resolve_blocker(record, row, latest, user, found, may_approve=may)
    return SafetyResolutionEligibility(eligible=reason is None, reason=reason)


def resolve_safety_concern(
    *,
    session: Session,
    user: CurrentUser,
    project_id: str,
    number: int,
    request: SafetyResolutionRequest,
) -> AcknowledgedRequirements:
    """Record that one safety concern of the latest revision is excluded from scope or a false positive.

    Under the project lock, then the revision's (the approval's order); the
    stored record must match its hash; not derived, not approved, the latest
    revision. Who: a member holding ``approve_revision`` (checked with the
    approval's ``FOR SHARE`` locks) who neither created the AI proposal the
    concern belongs to nor saved the revision. The concern may be this
    revision's own (its AI block or its request) or one carried from an
    earlier revision. Appended to the record's ``safety_resolutions``
    (append-only, carried to later revisions), the hash recomputed; the
    design and every item untouched (nothing becomes a load or a placeable
    item); the approval later binds the new hash.

    Raises:
        NotFoundError: ``revision_not_found`` (also another tenant's),
            ``safety_target_not_found``.
        AuthorizationError: ``safety_resolution_requires_account`` (a trial
            too), ``safety_resolution_not_permitted``,
            ``safety_resolution_by_proposal_author``,
            ``safety_resolution_by_revision_author``.
        ConflictError: ``requirements_derived``, ``revision_already_approved``,
            ``revision_not_latest``, ``safety_concern_already_resolved``,
            ``false_positive_not_allowed`` (``reason``: ``item``,
            ``life_safety``, ``life_safety_item_overlaps`` or ``in_request``),
            ``exclusion_not_allowed`` (``in_request``: a life-safety board or
            load still in the request is removed, not excluded),
            ``snapshot_integrity_failed`` (``requirements_mismatch``).
    """
    tenant = _scope_to(session, user)
    kind = (
        "mention"
        if request.mention_id is not None
        else "item" if request.item_id is not None else "request"
    )
    target_id = str(request.mention_id or request.item_id or request.request_id)
    try:
        project = _revision_project(session, project_id, lock=True)
        row = _locked_revision(session, project, number)
        if row.requirements is not None and not _requirements_match(row):
            raise ConflictError(
                f"revision {number}'s requirements record does not match its recorded hash",
                code="snapshot_integrity_failed",
                params={"revision": number, "reason": "requirements_mismatch"},
            )
        account = _account_id(user)
        may = False
        if user.email and account is not None:
            try:
                # Locked FOR SHARE until commit, as an approval's check is.
                permissions.require(
                    session=session, user=user, permission=Permission.APPROVE_REVISION
                )
                may = True
            except AuthorizationError:
                may = False
        revisions = _revisions(session, project)
        found = _found(row, revisions)
        record = _stored_record(row)
        blocker = _resolve_blocker(
            record, row, project.revision_count, user, found, may_approve=may
        )
        if blocker is not None or record is None:
            error_kind, code, message = _RESOLVE_REFUSALS[blocker or "requirements_derived"]
            params: dict[str, Any] = {"revision": number}
            if code == "revision_not_latest":
                params["latest"] = project.revision_count
            raise error_kind(message, code=code, params=params)
        current = str(((record.get("ai") or {}).get("proposal") or {}).get("id") or "")
        matches = [
            c
            for c in found
            if c.kind == kind
            and c.target_id == target_id
            and (request.proposal_id is None or (c.proposal_id or "") == request.proposal_id)
        ]
        if len(matches) > 1:
            # The same id in two proposals: the revision's own block, unless named.
            matches = [c for c in matches if (c.proposal_id or "") == current] or matches[:1]
        if not matches:
            raise NotFoundError(
                "this revision has no such safety concern",
                code="safety_target_not_found",
                params={"revision": number},
            )
        target = matches[0]
        if target.proposal_id:
            creator = _proposal_creators(row, revisions).get(target.proposal_id, "")
            if not creator or creator == str(account):
                error_kind, code, message = _RESOLVE_REFUSALS["proposal_author"]
                raise error_kind(message, code=code, params={"revision": number})
        if target.key not in {c.key for c in safety_review.unresolved(found, record)}:
            raise ConflictError(
                "this safety concern is resolved already; a resolution is never replaced",
                code="safety_concern_already_resolved",
                params={"revision": number},
            )
        refusal = safety_review.refusal(target, request.resolution)
        if refusal is not None:
            raise ConflictError(
                (
                    "this concern cannot be a false positive; record it as excluded_from_scope"
                    if request.resolution == safety_review.FALSE_POSITIVE
                    and refusal != "in_request"
                    else (
                        "a board or load still in the request that names life-safety "
                        "equipment cannot be resolved: remove it from the request and save a new "
                        "revision, then record the exclusion"
                        if refusal == "in_request" and target.level == "life_safety"
                        else (
                            "the request still draws a load matching this concern (same board and "
                            "position, or same board, power and kind): renaming it does not remove it; "
                            "remove the load, or change it so none matches, then record the exclusion"
                            if refusal == "still_in_request"
                            else "a possibly-safety board or load still in the request can only be "
                            "judged a false positive; or remove it from the request"
                        )
                    )
                ),
                code=(
                    "false_positive_not_allowed"
                    if request.resolution == safety_review.FALSE_POSITIVE
                    else "exclusion_not_allowed"
                ),
                params={"revision": number, "reason": refusal},
            )
        confirmed: list[dict[str, Any]] | None = None
        listed = {(r.board, r.load_index) for r in request.not_this_equipment or []}
        wanted = (
            {(c[0], c[1]): c for c in target.candidates}
            if target.needs_confirmation and request.resolution == safety_review.EXCLUDED_FROM_SCOPE
            else {}
        )
        extra = sorted(listed - set(wanted))
        if extra:
            raise ValidationError(
                "not_this_equipment lists loads that are not candidates for this exclusion",
                code="exclusion_confirmation_extra",
                params={"extra": ",".join(f"{b}:{i}" for b, i in extra)},
            )
        if wanted:
            missing = sorted(set(wanted) - listed)
            if missing:
                raise ConflictError(
                    "confirm that every candidate load of the request is not this equipment "
                    "(not_this_equipment) before excluding it",
                    code="exclusion_confirmation_incomplete",
                    params={
                        "revision": number,
                        "missing": ",".join(f"{b}:{i}" for b, i in missing),
                    },
                )
            confirmed = [_confirmed(wanted[key]).model_dump() for key in sorted(wanted)]
        elif target.needs_confirmation and request.resolution == safety_review.EXCLUDED_FROM_SCOPE:
            confirmed = []
        named = _display_names(session, project.tenant_id, [], also=account)
        by = (
            (named.by_id.get(account) if account is not None else None)
            or user.email
            or requirements.UNKNOWN_ACCOUNT
        )
        entry = {
            "target_kind": kind,
            "target_id": target_id,
            "proposal_id": target.proposal_id,
            "raised_on": target.raised_on,
            "resolution": request.resolution,
            "reason": request.reason,
            "by_user_id": user.id,
            "by_name": by,
            "at": datetime.now(UTC).isoformat(),
            **({"confirmed_loads": confirmed} if confirmed is not None else {}),
        }
        record = dict(record)
        if target.kind != "request" and target.present and target.proposal_id == current:
            # A concern of the revision's own AI block: on that block, as in M6d.
            ai = dict(record["ai"])
            ai["resolutions"] = [*ai.get("resolutions", []), entry]
            record["ai"] = ai
        else:
            # A request concern, or one carried from an earlier proposal.
            record["safety_resolutions"] = [*record.get("safety_resolutions", []), entry]
        record["open_items"] = _open_items_shown(record, found)
        row.requirements = record
        row.requirements_sha256 = snapshot.sha256_of(record)
        session.commit()
    except BaseException:
        _release(session)
        raise
    logger.info(
        "design.safety_concern_resolved",
        tenant_id=str(tenant),
        project_id=str(project.id),
        revision=number,
        target_kind=kind,
        target=target_id,
        resolution=request.resolution,
    )
    return AcknowledgedRequirements(requirements=RequirementsRecord.model_validate(record))

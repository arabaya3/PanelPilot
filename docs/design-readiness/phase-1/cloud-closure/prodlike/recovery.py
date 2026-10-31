"""Recovery path on the synthetic production-like database. Usage: recovery.py <url> phase1|recover"""
import hashlib, json, sys, uuid
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from app.core.errors import ConflictError
from app.core.tenancy import bind_tenant
from app.domain import design_projects as dp
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.design import ApproveRevisionRequest, ReviseProjectRequest, RevisionExportRequest
from sqlalchemy.pool import NullPool
eng = create_engine(sys.argv[1], poolclass=NullPool); mode = sys.argv[2]
def owner(tid):
    with eng.connect() as c:
        uid, email = c.execute(text("select u.id, u.email from tenants t join users u on u.id=t.owner_user_id where t.id=:t"), {"t": tid}).one()
    return CurrentUser(id=str(uid), email=email, tenant_id=str(tid), roles=frozenset({Role.ENGINEER}))
def s(tid):
    x = Session(eng); bind_tenant(x, uuid.UUID(str(tid))); return x
def exp(u, tid, pid, n, fmt, purpose="review"):
    return dp.export_revision(session=s(tid), user=u, project_id=str(pid), number=n, request=RevisionExportRequest.model_validate({"format": fmt, "purpose": purpose}))
def latest_approved(limit, offset, cond):
    with eng.connect() as c:
        return c.execute(text(f"""select distinct on (r.project_id) r.project_id, r.tenant_id, r.number from design_project_revisions r
            join design_projects p on p.id=r.project_id join tenants t on t.id=r.tenant_id
            where r.number=p.revision_count and r.approved_at is not null and t.is_active and t.owner_user_id is not null and {cond}
            order by r.project_id limit {limit} offset {offset}""")).all()
def rowhash(pid, n):
    with eng.connect() as c:
        row = c.execute(text("select * from design_project_revisions where project_id=:p and number=:n"), {"p": pid, "n": n}).mappings().one()
    return hashlib.sha256(json.dumps({k: str(v) for k, v in row.items() if k != "updated_at"}, sort_keys=True).encode()).hexdigest()
def recover(pid, tid, n):
    u = owner(tid); before = rowhash(pid, n)
    opened = dp.open_project(session=s(tid), user=u, project_id=str(pid))
    try:
        exp(u, tid, pid, n, "pdf"); old_export = "200"
    except ConflictError as e:
        old_export = e.code
    try:
        dp.approve_revision(session=s(tid), user=u, project_id=str(pid), number=n, request=ApproveRevisionRequest(note="again")); reapprove = "approved"
    except ConflictError as e:
        reapprove = e.code
    revised = dp.revise_project(session=s(tid), user=u, project_id=str(pid), request=ReviseProjectRequest(request=opened.request, note="Re-approval under the current renderer"))
    new = revised.revision
    dp.approve_revision(session=s(tid), user=u, project_id=str(pid), number=new, request=ApproveRevisionRequest(note="re-approved"))
    marks = []
    for fmt in ("pdf", "calculations_pdf", "dxf", "json"):
        content = exp(u, tid, pid, new, fmt).content
        marks.append(fmt if b"REVIEW APPROVED" in content or fmt in ("pdf", "calculations_pdf", "dxf") else fmt + "?")
    status = json.loads(exp(u, tid, pid, new, "json").content)["export_status"]
    try:
        exp(u, tid, pid, new, "json", "execution"); execution = "200"
    except ConflictError as e:
        execution = e.code
    return dict(old_export=old_export, reapprove_same=reapprove, new_revision=new, new_label=status["label"][:15],
                renderer_in_identity="renderer " in status["identity"], execution=execution,
                old_row_unchanged=rowhash(pid, n) == before)
if mode == "phase1":
    # Phase 1-era approvals: a new revision designed and approved by Phase 1 code.
    for pid, tid, n in latest_approved(20, 100, "r.approved_snapshot_sha256 is null"):
        u = owner(tid); opened = dp.open_project(session=s(tid), user=u, project_id=str(pid))
        r = dp.revise_project(session=s(tid), user=u, project_id=str(pid), request=ReviseProjectRequest(request=opened.request, note="Phase 1 era"))
        dp.approve_revision(session=s(tid), user=u, project_id=str(pid), number=r.revision, request=ApproveRevisionRequest(note="phase 1"))
    print("phase1-era approvals created: 20")
else:
    results = {}
    for label, cond, off in (("pre-phase1 (unbound)", "r.approved_snapshot_sha256 is null", 0),
                             ("phase1-era (no renderer)", "r.approved_snapshot_sha256 is not null and r.approved_renderer_version is null", 0)):
        rows = latest_approved(20, off, cond)
        out = [recover(pid, tid, n) for pid, tid, n in rows]
        agg = {}
        for o in out:
            for k, v in o.items():
                if k != "new_revision": agg.setdefault(k, {}).setdefault(str(v), 0); agg[k][str(v)] += 1
        results[label] = {"projects": len(out), **agg}
    print(json.dumps(results, indent=1))

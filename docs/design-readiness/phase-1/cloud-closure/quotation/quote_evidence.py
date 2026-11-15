"""Quotation evidence: one approved synthetic design, two price lists, an unapproved one, a draft."""
import json, sys, uuid
import pdfplumber
from decimal import Decimal
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from app.core.tenancy import bind_tenant
from app.domain import design as design_domain, design_projects as dp
from app.models.schemas.auth import CurrentUser, Role
from app.models.schemas.design import *  # noqa
url, out = sys.argv[1], sys.argv[2]
eng = create_engine(url, poolclass=NullPool)
tid, uid = uuid.uuid4(), uuid.uuid4()
with eng.begin() as c:
    c.execute(text("insert into tenants(id,slug,name,is_active,owner_user_id) values (:t,:s,'Synthetic Estimates Co',true,null)"), {"t": tid, "s": f"synthetic-q-{tid.hex[:8]}"})
    c.execute(text("insert into users(id,tenant_id,email,full_name,is_active) values (:u,:t,:e,'Synthetic Reviewer',true)"), {"u": uid, "t": tid, "e": f"q-{tid.hex[:8]}@synthetic-example.com"})
    c.execute(text("update tenants set owner_user_id=:u where id=:t"), {"u": uid, "t": tid})
user = CurrentUser(id=str(uid), email=f"q-{tid.hex[:8]}@synthetic-example.com", tenant_id=str(tid), roles=frozenset({Role.ENGINEER}))
def s():
    x = Session(eng); bind_tenant(x, tid); return x
req = ProjectDesignRequest.model_validate({"info": {"name": "Estimate evidence"}, "boards": [{"name": "MCC1", "loads": [
    {"description": "Pump", "load": "motor", "power_kw": "5.5", "phases": 3, "starter": "dol"},
    {"description": "Sockets", "load": "socket", "power_kw": "3"}]}]})
approved = dp.save_project(session=s(), user=user, request=SaveProjectRequest(name="Estimate evidence", request=req))
dp.approve_revision(session=s(), user=user, project_id=approved.id, number=1, request=ApproveRevisionRequest(note="synthetic"))
unapproved = dp.save_project(session=s(), user=user, request=SaveProjectRequest(name="Estimate evidence (unapproved)", request=req))
lists = {"price-list-A": {"currency": "JOD", "labour_per_circuit": "10"},
         "price-list-B": {"currency": "JOD", "labour_per_circuit": "95", "markup_percent": "20", "vat_percent": "16"}}
def export(pid, pricing):
    return dp.export_revision(session=s(), user=user, project_id=pid, number=1,
        request=RevisionExportRequest.model_validate({"format": "quotation_pdf", "purpose": "review", "pricing": pricing})).content
files = {f"approved-{k}.pdf": export(approved.id, v) for k, v in lists.items()}
files["unapproved-price-list-A.pdf"] = export(unapproved.id, lists["price-list-A"])
files["approved-design.pdf"] = dp.export_revision(session=s(), user=user, project_id=approved.id, number=1,
    request=RevisionExportRequest.model_validate({"format": "pdf", "purpose": "review"})).content
draft = design_domain.export_design(session=s(), user=user, request=DesignExportRequest.model_validate({
    "project": design_domain.build_design(req)[0].model_dump(mode="json"), "format": "quotation_pdf", "pricing": lists["price-list-A"]}))
files["draft-price-list-A.pdf"] = draft.content
summary = {}
for name, content in files.items():
    open(f"{out}/{name}", "wb").write(content)
    pages = [p.extract_text() or "" for p in pdfplumber.open(f"{out}/{name}").pages]
    first = pages[0].splitlines()
    total = next((l for p in pages for l in p.splitlines() if l.startswith("Total")), "")
    summary[name] = {"pages": len(pages), "label_every_page": first[0], "identity": first[1] if len(first) > 1 else "",
        "says_review_approved": any("REVIEW APPROVED" in p for p in pages), "names_approver": any("Synthetic Reviewer" in p for p in pages),
        "total_line": total}
print(json.dumps(summary, indent=1, ensure_ascii=False))

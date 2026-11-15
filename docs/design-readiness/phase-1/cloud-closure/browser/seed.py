"""Seed synthetic projects for the browser check, through the API, then alter rows."""
import json, sys, urllib.request
from sqlalchemy import create_engine, text
from app.design import snapshot
API = "http://localhost:8000/api/v1"
def call(path, body, token=None):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(), method="POST",
        headers={"content-type": "application/json", **({"authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req) as r: return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"{}")
email = sys.argv[1]
_, tok = call("/auth/signup", {"email": email, "password": "throwaway-pass-123", "full_name": "Synthetic Owner"})
token = tok["access_token"]
req = {"info": {"name": "x"}, "boards": [{"name": "DB1", "loads": [{"description": "Sockets", "load": "socket", "power_kw": "3"}]}]}
ids = {}
for name in ("Unknown schema", "Renderer stale"):
    st, saved = call("/design/projects", {"name": name, "request": {**req, "info": {"name": name}}}, token); assert st in (200, 201), (st, saved)
    ids[name] = saved["id"]
st, ap = call(f"/design/projects/{ids['Renderer stale']}/revisions/1/approval", {"note": "synthetic"}, token); assert st == 200, (st, ap)
st, ex = call(f"/design/projects/{ids['Renderer stale']}/revisions/1/export", {"format": "json", "purpose": "review"}, token); print("export before:", st)
eng = create_engine(sys.argv[2])
with eng.begin() as c:
    env = {"schema": "pp-design/9", "model": {}}
    c.execute(text("UPDATE design_project_revisions SET design_snapshot = CAST(:s AS JSONB), snapshot_sha256 = :h WHERE project_id = :p"),
              {"s": json.dumps(env), "h": snapshot.sha256_of(env), "p": ids["Unknown schema"]})
    c.execute(text("UPDATE design_project_revisions SET approved_renderer_version = NULL WHERE project_id = :p"), {"p": ids["Renderer stale"]})
st, ex = call(f"/design/projects/{ids['Renderer stale']}/revisions/1/export", {"format": "pdf", "purpose": "review"}, token)
print("export after NULL renderer:", st, ex.get("code"), ex.get("params"))
with eng.begin() as c:
    c.execute(text("UPDATE design_project_revisions SET approved_renderer_version = :v, approved_rules_version = 'r-approved-earlier' WHERE project_id = :p"),
              {"v": __import__("app.design", fromlist=["x"]).RENDERER_VERSION, "p": ids["Renderer stale"]})
st, ex = call(f"/design/projects/{ids['Renderer stale']}/revisions/1/export", {"format": "pdf", "purpose": "review"}, token)
print("export after older approval rules:", st, ex.get("code"), ex.get("params"))
with eng.begin() as c:
    c.execute(text("UPDATE design_project_revisions SET approved_renderer_version = NULL, approved_rules_version = rules_version WHERE project_id = :p"), {"p": ids["Renderer stale"]})
st, ex = call(f"/design/projects/{ids['Renderer stale']}/revisions/1/export", {"format": "json", "purpose": "execution"}, token); print("execution:", st, ex.get("code"))
st, ex = call(f"/design/projects/{ids['Renderer stale']}/revisions/1/export", {"format": "parts_csv", "purpose": "review"}, token); print("csv:", st, ex.get("code"))

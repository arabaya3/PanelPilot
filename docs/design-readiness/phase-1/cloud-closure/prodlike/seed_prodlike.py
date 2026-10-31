"""Synthetic, production-shaped data at main's schema (a7d1e3f5b2c8). No real names."""
import json, random, sys, uuid
from datetime import datetime, timedelta, timezone
import psycopg
rnd = random.Random(20261031)
conn = psycopg.connect(sys.argv[1]); cur = conn.cursor()
roles = {}
for name in ("engineer", "reviewer", "ingestion", "admin"):
    rid = uuid.uuid4(); roles[name] = rid
    cur.execute("insert into roles(id,name) values (%s,%s)", (rid, name))
T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
KINDS = ["socket", "lighting", "air_conditioning", "water_heater", "fan", "kitchen"]
cases = ["normal", "tie", "oldest_inactive", "oldest_inactive_no_admin", "tenant_inactive", "single_no_admin"]
counts = {c: 0 for c in cases}; approved = 0; revisions = 0
for i in range(300):
    case = cases[i % len(cases)]; counts[case] += 1
    tid = uuid.uuid4()
    cur.execute("insert into tenants(id,slug,name,is_active,created_at) values (%s,%s,%s,%s,%s)",
                (tid, f"synthetic-{i:03d}", f"Synthetic Co {i:03d}", case != "tenant_inactive", T0 + timedelta(days=i)))
    n = 1 if case == "single_no_admin" else rnd.randint(2, 6)
    users = []
    for u in range(n):
        uid = uuid.uuid4()
        created = T0 + timedelta(days=i, hours=u)
        if case == "tie" and u == 1: created = users[0][1]
        active = not (case.startswith("oldest_inactive") and u == 0)
        cur.execute("insert into users(id,tenant_id,email,full_name,is_active,created_at) values (%s,%s,%s,%s,%s,%s)",
                    (uid, tid, f"u{u}.t{i:03d}@synthetic-example.com", f"Synthetic User {i:03d}-{u}", active, created))
        users.append((uid, created, active))
    admin_idx = None if case in ("oldest_inactive_no_admin", "single_no_admin") else (1 if case.startswith("oldest_inactive") else 0)
    if admin_idx is not None and admin_idx < len(users):
        cur.execute("insert into user_roles(user_id,role_id) values (%s,%s)", (users[admin_idx][0], roles["admin"]))
    if len(users) > 2:
        cur.execute("insert into user_roles(user_id,role_id) values (%s,%s)", (users[2][0], roles["reviewer"]))
    for p in range(2):
        pid = uuid.uuid4(); nrev = rnd.randint(1, 3)
        cur.execute("insert into design_projects(id,tenant_id,name,revision_count) values (%s,%s,%s,%s)", (pid, tid, f"Project {i:03d}-{p}", nrev))
        for r in range(1, nrev + 1):
            loads = [{"description": f"Circuit {k}", "load": rnd.choice(KINDS), "power_kw": str(rnd.choice([1, 1.5, 2, 3, 4.5]))} for k in range(rnd.randint(1, 6))]
            req = {"info": {"name": f"Project {i:03d}-{p}"}, "boards": [{"name": "DB1", "loads": loads}]}
            is_approved = rnd.random() < 0.4
            approved += is_approved; revisions += 1
            cur.execute("insert into design_project_revisions(id,project_id,tenant_id,number,note,author,request,approved_by,approved_account,approved_at) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (uuid.uuid4(), pid, tid, r, "", f"u0.t{i:03d}@synthetic-example.com", json.dumps(req),
                         "Typed Approver" if is_approved else None,
                         f"u0.t{i:03d}@synthetic-example.com" if is_approved and rnd.random() < 0.8 else None,
                         T0 + timedelta(days=i + r) if is_approved else None))
conn.commit()
print("tenants by case:", counts, "revisions:", revisions, "approved (legacy, typed name):", approved)

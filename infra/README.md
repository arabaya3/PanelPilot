# infra/

Infrastructure and deployment configuration.

Nothing here yet beyond local development. When real IaC lands, keep it split
the way the runtime is split:

```
infra/
  docker/         local development compose + Dockerfiles
  terraform/      cloud resources, one module per concern
  opensearch/     index mappings and lifecycle policies
```

## Deploy targets

Three, from two build artifacts:

| Target   | Build                                | Runs                                    |
| -------- | ------------------------------------ | --------------------------------------- |
| `web`    | `apps/web`                           | `next start`                            |
| `api`    | `apps/api`                           | `uvicorn app.main:create_app --factory` |
| `worker` | `apps/api` — **same image as `api`** | `panelpilot-worker <job>`, scheduled    |

The API and worker share one image on purpose; only the command differs. See
[ADR 0002](../docs/adr/0002-one-package-two-runtimes.md). Scale them
independently — the worker's load follows corpus size, the API's follows user
traffic.

Give the worker its own service account. It needs write access to staging and
to Postgres, and must **not** hold write access to the production index.

Three constraints that outlive whichever tool we pick:

1. **Staging and production indices are provisioned separately**, with
   separate credentials. The service account used by ingestion must not hold
   write permission on the production index — the code-level guard in
   `app/tests/test_architecture.py` is the second line of defence, not the
   first. See [ADR 0001](../docs/adr/0001-staging-vs-production-index.md).
2. **Secrets come from the platform's secret store**, injected as the
   environment variables listed in `.env.example`. No secret is ever committed
   here, in any form, including in a `.tfvars` example.
3. **The worker is scheduled, not long-running.** Each invocation runs one job
   and exits. Configure retries and timeouts on the scheduler; do not add a
   loop to the worker to work around a missing scheduler feature.

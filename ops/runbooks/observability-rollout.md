# Runbook — Teracorp observability with redaction (P4 / O5) rollout (founder-gated)

What this slice ships: four read-only jobs on the **kod (kodeme.io)** toolbox,
one per signal class, each writing a redacted snapshot and pinging its own
Healthchecks check. Nothing is deployed, no schedule is enabled, no new service
exists — the schedules ship `enabled: false` in
`deploys/instances/production/kod-infra-kctl.yaml` and every step below is a
founder gate. Every `kctl-*` command uses `-p kodemeio`; no other estate's host,
key, job or bucket is named anywhere in this runbook.

Spec: `kodemeio-docs/superpowers/specs/2026-09-27-teracorp-observability-design.md`
(D1–D7, O1–O6). Roadmap row **P4** stays `built-local` until M1–M7 hold and the
evidence below is recorded.

| Job | Class | What it reads | Cadence (UTC) |
|---|---|---|---|
| `kod-metrics-token-cost` | token-cost | LiteLLM `GET /key/list` — per key-alias spend vs that alias's daily budget | daily 23:55 |
| `kod-metrics-queue-lag` | queue-lag | Hatchet `.../workflow-runs` — counts by status, oldest running/queued age | every 15 min |
| `kod-metrics-outcome` | action-outcome | Odoo counts by state: `factory.work_order`, `tier.review`, `mcp.operation` | daily 23:35 |
| `kod-metrics-funnel` | product-funnel | Odoo counts, last 24 h: `landing_lead` → `sale_order` → `account_payment` | daily 23:45 |

The thresholds, the snapshots' class table and the Healthchecks contract are
committed in `ops/monitoring/metrics/thresholds.yaml`; the manifest comment
names the same gate list. `deploys/tests/test_metrics_thresholds.py` and
`deploys/tests/test_kod_schedule_gates.py` fail if any of the three drift apart.

---

## M1 — The credentials to provision (names only; never in git)

Everything lands in Dokploy's env store for **kod-infra-kctl** — start from
`deploys/env/production/.env.kod-infra-kctl.example`, which now lists all of
these. Real values stay in 1Password / Dokploy; nothing here is ever committed.

| Env var | What it is | Where it comes from | Privilege |
|---|---|---|---|
| `LITELLM_URL` | `https://llm.kodeme.io` | fixed | — |
| `LITELLM_ADMIN_KEY` | LiteLLM **master key** — the only key that can read every key's spend | 1Password item the LiteLLM runbook already defines (kodemeio-llmlite `docs/runbook-production.md`) | can create/block keys; this job only `GET /key/list` |
| `HATCHET_URL` | `https://hatchet.kodeme.io` | fixed | — |
| `HATCHET_API_TOKEN` | a Hatchet API token (bearer JWT; the job reads the tenant from its `sub` claim) | Hatchet dashboard → API tokens | read |
| `KCTL_ODOO_PROFILE` | `kodemeio` | fixed (the toolbox's mounted config) | — |
| `KCTL_ODOO_URL` / `_DATABASE` / `_USERNAME` | the kod erp instance, its database, the operator login | `https://erp.kodeme.io`, `kod_odoo_erp` | — |
| `KCTL_ODOO_API_KEY` | a **read-only operator user's** Odoo API key (Preferences → Account Security → New API key) | Odoo, on the kod erp instance | must be able to read the four tables and call `sql.guard`; it can run any SELECT (`sql run` is audited to `sql.guard.log`) — do not hand it an admin password |
| `HC_KOD_METRICS_TOKEN_COST` / `_QUEUE_LAG` / `_OUTCOME` / `_FUNNEL` | one ping URL per job | M2 | — |
| `METRICS_DIR` | `/var/lib/kod-metrics` | fixed (the `kod-metrics` volume) | — |

- Verify the *names* reached the container (never the values):
  `kctl-dokploy -p kodemeio compose env list <compose-id>` — each name above
  appears; a value never does.
- Rollback: unset the variable and redeploy; the affected job fails loudly
  (`RESULT=FAILED`) rather than silently skipping. `KCTL_ODOO_*` is read by any
  `kctl-odoo` call in that container, so a leaked value is a leak for every job
  there — rotate the key, not just the variable.

## M2 — Healthchecks: four more checks (extends Wave 0 G3)

Wave 0's G3 created four checks (`teracorp-wave0-rollout.md`). Create four
more; the **name must equal the job name**, for the same reason: `hc.sh` pings
by URL while `jobrun`'s failure mail names the job.

| Check name | Period | Grace |
|---|---|---|
| `kod-metrics-token-cost` | 1 d | 2 h |
| `kod-metrics-queue-lag` | 15 min | 15 min |
| `kod-metrics-outcome` | 1 d | 2 h |
| `kod-metrics-funnel` | 1 d | 2 h |

This check **is** the "a dead job is visible from outside" mechanism (spec D7):
a job cannot report its own absence, and the grace period IS the snapshot's
freshness threshold. That is why Gatus gains no endpoint for these classes (the
ruling is written next to the other "deliberately not checked here" note in
`ops/monitoring/gatus/config.yaml`). Note the Healthchecks Hobbyist plan's
20-check limit — Wave 0 used 4, this adds 4.

- Verify: `curl -fsS <ping-url>` turns a check green once; append `/fail` and
  a check actually messages Telegram **and** email (`kod-metrics-*` failures
  are mailed by jobrun too, but the withhold is silent by design — this is the
  half that covers a job that never runs).
- Rollback: pause the check; the job's `HC_*` var may stay set (it pings into
  the void harmlessly) or be unset.

## M3 — Confirm the thresholds (spec §6 gate 2)

Committed in `ops/monitoring/metrics/thresholds.yaml`, enforced in
`kodemeio-skills docker/lib/metrics.py`, overridable per run:

| Class | Threshold | Env override | Proposed default | Why that number |
|---|---|---|---|---|
| token-cost | a key alias at/over `warn_pct` of its daily budget | `METRICS_TOKEN_WARN_PCT` | **80 %** | LiteLLM blocks the key at 100 % (`max_budget` + `budget_duration: 1d` on every key in `kodemeio-llmlite/config/keys.yaml`); this fires before callers start failing |
| queue-lag | a running or queued run older than `lag_seconds` | `METRICS_QUEUE_LAG_S` | **1800 s** | an order of magnitude above the few-second runs the Stage C integration tests observed |
| action-outcome | none | — | — | an in-flight count has no "too high" without a baseline |
| product-funnel | none | — | — | same |

A breach still writes its snapshot, prints `ALERT`/`BREACH` lines, exits 3 and
**withholds** its ping, so jobrun mails it and Healthchecks keeps alarming until
a clean run. Change a default in both places (yaml + `metrics.py`) or
`test_metrics_thresholds.py` fails.

## M4 — LiteLLM (the token-cost job's only source)

**LiteLLM is not deployed on kod today** (Wave 0 removed the `litellm/`
freshness prefix for that reason; the Gatus `llm-*` endpoints ship
`enabled: false`). Deploy it first per `kodemeio-llmlite/docs/runbook-production.md`
(registry → config → postgres → keys via `reconcile_keys.py` → prove `spend > 0`
with a throwaway key). Then:

- `LITELLM_ADMIN_KEY` is the master key from that runbook's 1Password item.
- Verify by hand, from anywhere:
  `curl -sS -H "Authorization: Bearer $MASTER" https://llm.kodeme.io/key/list | head -c 200`
  → a `keys` array. If it 401s, the job would fail loudly every day.
- Until this is done, the token-cost schedule **must stay disabled**: it would
  alarm daily by design, and an alarm that is always red is one nobody reads.

## M5 — Hatchet API token

Hatchet is deployed (`hatchet.kodeme.io`), its `/api/*` routes are bearer-only.
Create an API token in the dashboard and put it in `HATCHET_API_TOKEN`.

- Verify (read-only):
  ```bash
  TOKEN=$(op read 'op://Kodemeio/hatchet-api-token/credential')   # keep it out of argv where you can
  curl -sS -H "Authorization: Bearer $TOKEN" \
    "https://hatchet.kodeme.io/api/v1/stable/tenants/<tenant>/workflow-runs?since=2026-09-01T00:00:00.000000%2B0000&only_tasks=false&include_payloads=false&limit=5" \
    | head -c 300
  ```
  `<tenant>` is the token's own `sub` claim (decode the middle JWT segment; the
  job does this itself when `HATCHET_TENANT_ID` is unset). A `rows` array is
  what the job expects; `include_payloads=false` is what keeps a task's
  input/output out of the response.
- Rollback: delete the token; the job fails loudly, no snapshot, `/fail` ping.

## M6 — Odoo read-only operator key + module prerequisites

1. On the kod erp instance create (or reuse) an operator user, create an API
   key for it, and put the four `KCTL_ODOO_*` values in the toolbox env.
2. The outcome job reads `factory.work_order`, `tier.review` and
   `mcp.operation`; the funnel job reads `landing_lead`, `sale_order` and
   `account_payment`. Confirm the modules are installed **before** enabling:
   ```bash
   kctl-odoo -p kodemeio-kod-odoo-erp modules list --filter mcp_base
   kctl-odoo -p kodemeio-kod-odoo-erp modules list --filter approval
   ```
   (`factory_base` ships with the kod ERP bundle; `sql_guard` is what
   `sql run` executes inside — without it every query fails.)
3. Prove the read path by hand, from a workstation (read-only, audited):
   ```bash
   kctl-odoo -p kodemeio-kod-odoo-erp --format json sql run \
     "SELECT state, count(*) AS n FROM factory_work_order GROUP BY state"
   ```
   One row per state. The same statement is what the job sends.
- Rollback: revoke the API key. The jobs fail loudly with no snapshot.

## M7 — Toolbox image, deploy, schedules (extends Wave 0 G4)

The Wave 0 preconditions for the *backup* schedules (G2, the hrms dump, the
restic repos, G5/G6, the Mattermost sidecar) are separate and unchanged. The
metrics schedules need the image from **P4 T1/T2**:

1. Merge kodemeio-skills to `main` (`docker/**` changes build the image); read
   the `sha-<short>` tag and replace `sha-PENDING` in
   `kodemeio-skills/compose/toolbox-kod.yml`.
2. ```bash
   cd kodemeio-skills
   bash tests/test_kod_metrics.sh                 # the job suite, offline
   DEPLOY_CHECK=1 bash tests/test_toolbox_kod.sh  # refuses the sha-PENDING placeholder
   ```
3. Apply the manifest (it creates **all seven** schedules, every one
   `enabled: false`):
   ```bash
   kctl-dokploy -p kodemeio deploy apply -f deploys/instances/production/kod-infra-kctl.yaml --dry-run
   kctl-dokploy -p kodemeio deploy apply -f deploys/instances/production/kod-infra-kctl.yaml
   kctl-dokploy -p kodemeio deploy schedules-diff -f deploys/instances/production/kod-infra-kctl.yaml
   ```
   (the verify phase of that app fails by design — it has no HTTP surface.)
4. Run each metrics job by hand and READ its output before enabling anything —
   a registered schedule proves nothing:
   ```bash
   COMPOSE=$(kctl-dokploy -p kodemeio compose search --name kod-infra-kctl --json | jq -r '.[0].composeId')
   kctl-dokploy -p kodemeio schedules list "$COMPOSE"     # seven rows, all enabled=false
   kctl-dokploy -p kodemeio schedules run <schedule-id>   # one row's id at a time
   ```
   Expect `RESULT=ok class=<class>` + a `SUMMARY` line. `RESULT=FAILED` names
   the reason and means a credential in M1/M4/M5/M6 is not there yet;
   `RESULT=ALERT` means a threshold is breached for real.
5. Only then, flip the four metrics schedules to `enabled: true` **and** add
   their names to `ENABLED_BY_EXCEPTION` in
   `deploys/tests/test_kod_schedule_gates.py` with the evidence — the gate test
   fails by design on an undocumented flip. Record which of M4/M5/M6 you
   completed.

## M8 — The alert drill (do it once, on purpose)

A threshold that cannot be *made* to fire is a threshold nobody has tested
end-to-end. On the toolbox host, with the schedule disabled:

```bash
# 1. the breach path: a warn line below the real number forces a BREACH
docker exec -e METRICS_TOKEN_WARN_PCT=1 <toolbox-container> jobrun kod-metrics-token-cost
```
Expect: `BREACH class=token-cost alias=…`, `ALERT class=token-cost breached=1`,
`RESULT=ALERT`, exit 3, `HC-PING-WITHHELD`, and a jobrun failure mail naming the
job. The snapshot is still written.

```bash
# 2. the dead-man path: a ping that never arrives
#    (pause the Healthchecks check's period instead of faking it: the whole
#     point is that the absence is what alarms)
```
Expect: Healthchecks → Telegram + email after the grace period.

```bash
# 3. the failed-read path: point the job at a source that cannot answer
docker exec -e LITELLM_URL=https://llm.invalid <toolbox-container> jobrun kod-metrics-token-cost
```
Expect: `RESULT=FAILED`, `reason=source-unreadable`, **no** snapshot written,
the check going red immediately (`/fail`).

## M9 — Reading a snapshot

Snapshots live in the toolbox's `kod-metrics` volume at `METRICS_DIR`
(`/var/lib/kod-metrics`): `<class>.json` is the latest, and
`history/<class>/<date>.json` keeps 30 days — the answer to "was yesterday's
alarm about something since fixed?" (which is why the volume is a named volume
and not the container's filesystem).

```bash
HOST=$(kctl-dokploy -p kodemeio compose get <compose-id> --json | jq -r '.server.host')  # confirm the name
CT=$(ssh "$HOST" "docker ps -qf label=com.docker.compose.service=kctl" | head -1)
ssh "$HOST" "docker exec $CT cat /var/lib/kod-metrics/token-cost.json"
ssh "$HOST" "docker exec $CT cat /var/lib/kod-metrics/history/queue-lag/$(date -u +%F).json"
```
Shape, exactly:
```json
{"class": "token-cost", "source": "litellm-key-list", "generated_at": "2026-09-27T23:55:00Z",
 "dropped": 2,
 "values": {"window": "budget-period", "warn_pct": 80.0, "keys_total": 4, "keys_over_warn": 1,
            "unbudgeted_keys": 0, "spend_usd_total": 3.1, "budget_usd_total": 19.0,
            "key_costs": [{"alias": "kido", "spend_usd": 1.7, "budget_usd": 2.0,
                           "used_pct": 85.0, "over_warn": true}]}}
```
- `values` holds **only** the class's allow-listed fields; `dropped` counts
  everything the source offered that the allow-list refused (never a value).
  `keys_total` (or `work_orders_total`) counts what the **source** reported;
  the list next to it holds the rows that survived, so the two can differ — the
  difference is what `dropped` is telling you.
- The same file must pass the redaction contract before the job may succeed, so
  a snapshot on disk is a snapshot that was checked:
  `bash kodemeio-dokploy/ops/scripts/redaction-grep.sh --contract kodemeio-dokploy/contracts/observability/redaction.v1.yaml /var/lib/kod-metrics/`

## M10 — Grafana (optional, founder-gated; spec D2)

Nothing in this slice depends on Grafana, and no monitoring stack runs today —
this section is a recipe, not a step. If the founder provisions it:

1. Grafana needs an HTTP view of the snapshots (it cannot read another
   container's volume). Cheapest: a read-only static file server over
   `/var/lib/kod-metrics` on `dokploy-network`, behind Authentik forward-auth
   (the estate's rule for every admin surface). That service is **not** part of
   this slice.
2. Datasource: **Infinity/JSON API** pointed at that URL, one query per class
   (`token-cost.json` → `values`).
3. Panels worth building first, from fields that already exist:
   | Panel | Field | Threshold |
   |---|---|---|
   | "keys over budget" stat | `values.keys_over_warn` | red at ≥ 1 |
   | per-key spend table | `values.key_costs[]` (`alias`, `spend_usd`, `used_pct`) | `used_pct` ≥ `warn_pct` |
   | "oldest running run" stat | `values.oldest_running_age_s` | red at `lag_threshold_s` (1800) |
   | work in flight | `values.*_open`, `values.work_order_states[]` | none |
   | funnel, 24 h | `values.leads` / `orders` / `payments` | none |
   | snapshot freshness | `generated_at` age | red past the job's period + grace (M2) |
   `values` never carries a name, a phone, an email, an amount per person or a
   payload, so a dashboard built from it cannot leak one.

---

## Roadmap rows and evidence

| Row | Gate | Evidence to record |
|---|---|---|
| P4 (O5) | dashboards + redaction test | kodemeio-skills `c56837d4` + `d0f0e51e` (`bash tests/test_kod_metrics.sh` → 145 ok, ALL PASS; mutation half fails the suite on purpose); kodemeio-dokploy `uv run pytest deploys/tests -q` (incl. `test_metrics_thresholds.py`, `test_kod_schedule_gates.py`); `uv run pytest ops/monitoring/gatus/tests -q` (15 passed, forced-outage e2e included); the four schedules shipped `enabled: false`; then **M1–M7 evidence** and, for the gate's dashboard half, either M10 built or the decision to defer it recorded in the results doc |

The row stays `built-local` until M1–M7 hold and the evidence above exists. The
redaction half of the gate is met by the local test suite (a planted value
fails it); the dashboard half is met by M10 **or** by the recorded decision
that Grafana is deferred, because spec D2 makes it optional.

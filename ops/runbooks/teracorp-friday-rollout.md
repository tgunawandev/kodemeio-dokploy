# Teracorp SW1 / FRIDAY rollout runbook

**Founder-gated.** Every command below is prepared, not run, by writing this document — no
GitHub token, label, webhook, branch protection rule, secret, or deployment described here has
been created or changed. The founder runs each step (or explicitly authorizes running one
command) and confirms its verification before moving to the next. Local only; nothing in
kodemeio-dsh, kodemeio-hatchet or any of the target repos has been pushed. Each step's commands
re-derive their own ids, so they are safe to run in a fresh shell days apart — do not assume a
variable from an earlier step is still set.

FRIDAY turns an **approved** GitHub issue (the `friday:approved` label, applied by a human) into
a **draft pull request**. CI checks it; **only the founder merges**. FRIDAY never merges, marks a
PR ready for review, deploys, or reads a production secret or database — the design forbids all
four in code (spec §4, broker/worker static tests) as well as here.

- Design: `kodemeio-docs/superpowers/specs/2026-09-26-teracorp-sw1-friday-design.md`
- Broker + PR tagging: `kodemeio-docs/.superpowers/sdd/2026-09-26-teracorp-sw1-friday/task-2-report.md`
- Session runner / `friday` container: `kodemeio-docs/.superpowers/sdd/2026-09-26-teracorp-sw1-friday/task-3-report.md`,
  kodemeio-dsh `docs/friday-session.md`
- CI gates: `kodemeio-docs/.superpowers/sdd/2026-09-26-teracorp-sw1-friday/task-5-report.md`
- Worker (`friday_dispatch`): `kodemeio-docs/.superpowers/sdd/2026-09-26-teracorp-sw1-friday/task-4-brief.md`
  and `deploy-notes.md` in the same directory (Task 4 is in flight; **re-check that file's facts
  against this document before you deploy Step 6** — anything it hasn't landed yet is marked
  `PENDING` below).
- Contracts: kodemeio-dokploy `contracts/agents/friday.yaml` (repos: `kodemeio-dokploy,
  kodemeio-next, kodemeio-react, kodemeio-odoo, kodemeio-hatchet, kodemeio-llmlite` — six repos,
  every step below iterates exactly this list, not the broker's broader `DSH_BROKER_REPOS`).

## Ordering against the LiteLLM rollout — read this first

This slice depends on `kodemeio-llmlite/docs/runbook-production.md` and must not be started
before that runbook's **Step 13** is done:

- That runbook's **Step 14** (dsh outbound containment) is standalone and pushes only
  `bdc234b:refs/heads/main` — it deliberately **excludes** every FRIDAY commit. Doing Step 14
  alone leaves dsh in an interim **"DeepSeek direct"** mode where `DSH_EGRESS_MODEL_HOSTS` only
  allows `api.deepseek.com`; `llm-edge` (FRIDAY's model path) is **not** in that allowlist, so a
  `friday` container deployed at that point could not reach any model.
- **Step 13** ends that interim mode and removes the `DEEPSEEK_BASE_URL`/`DEEPSEEK_API_KEY`
  overrides, restoring the default `http://llm-edge:8080/v1` route. Only after Step 13 is
  `llm-edge` the live path, which is what `FRIDAY_LLM_API_KEY` needs.
- LiteLLM's own **Step 8** creates a `friday` virtual key (DeepSeek-only, its own daily budget)
  and writes it to `~/.config/kodemeio/llmlite-keys.env` as `LITELLM_KEY_FRIDAY`. **That alias is
  not an env var any container reads.** Step 3 below is exactly the wiring step that turns
  `LITELLM_KEY_FRIDAY` into this stack's `FRIDAY_LLM_API_KEY`.

Do not run Step 5 (deploy the `friday` container) until the LiteLLM runbook's Step 13 is verified
done on the live dsh host.

## What "done" looks like today (2026-09-26, read before you start)

- kodemeio-dsh local `main` is **nine** commits ahead of `bdc234b`; **none have been pushed**.
  The broker's `work_order_id`/PR-tagging commits (`d802dce`, `312f467`) and the containment
  commits (`aa7b282` … `bdc234b`) are inert or containment-only until the LiteLLM runbook pushes
  them (its Step 14). The FRIDAY **push range** Step 5 publishes is *everything in
  `bdc234b..main`* (final review C2) — not "the five FRIDAY commits" this document used to list.
  As of this revision that range is, newest first:

  ```
  9a46e73 docs(dsh): SW1 final review I5 — broker token scope: Issues: write      <- final fix wave
  c2e9c88 fix(dsh): SW1 final review I7 — FRIDAY secrets render non-fatally         <- final fix wave
  db6590a test(egress): F6 checks for production credentials in FRIDAY sessions
  6d816ab feat(broker): broker-only dsh-dispatch network for the friday_dispatch worker
  2fe3cb2 fix(friday): wipe as the session uid, fail closed on Landlock and scratch (fix round 3)
  b313a48 fix(friday): sweep loop, fd-3 summary, state wipe, Landlock, own proxy (fix round 2)
  750b6c2 refactor: rename DSH_MODEL_UPSTREAM_NETWORK to DSH_LLM_UPSTREAM_NETWORK   <- LiteLLM track, rides along
  ee1b4ee fix(friday): own container, uid and network; hard deadline (fix round 1)
  5f52c25 feat(runtime): headless FRIDAY session runner (dsh-headless)
  ```

  `6d816ab` is **not** optional: it creates the `kod-infra-dsh-dispatch` network and attaches the
  broker to it, which is what Step 6's pre-check and the worker depend on; `750b6c2` is not a
  FRIDAY commit at all (it is the LiteLLM gateway track's rename). Always re-derive at run time
  with `git -C kodemeio-dsh log --oneline bdc234b..main` — this list is a snapshot, not a
  contract: anything beyond it is a later fix round, and you should read that round's report
  before pushing (a missing commit is worse than an extra one here).
- kodemeio-hatchet's `workers/friday_dispatch/` is **committed but not pushed**: `d9fa292` is the
  whole worker and it is **not an ancestor of `origin/main`** (tip `cd359af`; local `main` was 31
  commits ahead when this document was written and other tracks keep committing, so count it at
  run time with `git rev-list --count origin/main..main`, never from this page). The older "local
  and untracked" note in this document is stale. Step 6 clones its source from GitHub, so Step 6
  has **no source to build** until the push in its pre-step happens (final review C3). Local
  `main` also carries other tracks' committed work (`kido_chat`, `order_intake`, CI) — publishing
  `main` publishes all of it, which is why Step 6's pre-step makes you read `origin/main..main`
  first.
- **PENDING:** no `deploys/instances/production/kod-infra-friday-dispatch.yaml` manifest exists,
  and none exists for any other Hatchet worker either (`order_intake`, `kido_chat`) — worker
  compose services are not on the declarative `deploy apply -f <manifest>` path yet. Step 6 below
  uses the imperative `compose create`/`compose update` path instead; adopt the declarative
  manifest later if one gets added for this class of service.
- `deploys/env/production/.env.kod-infra-dsh.example` (committed, sanitized) now lists
  `FRIDAY_RUNNER_TOKEN` and `FRIDAY_LLM_API_KEY` alongside
  `COMPOSE_PROJECT_NAME`/`DEEPSEEK_API_KEY`/`GATEWAY_AUTH_SECRET`. It is still behind
  kodemeio-dsh's own `.env.example` on the broker variables (`GITHUB_OWNER`, `DSH_BROKER_REPOS`,
  `GITHUB_TOKEN`, `BROKER_CLIENT_TOKEN`) — the founder should add those to the real
  `deploys/env/production/.env.kod-infra-dsh` and its `.example` when convenient, per the repo's
  rule that every `.env` has a sanitized `.example`.
- **FRIDAY's two secrets no longer block the declarative path** (final review I7): kodemeio-dsh's
  `docker-compose.prod.yml` renders `friday` with `${FRIDAY_RUNNER_TOKEN:-}` /
  `${FRIDAY_LLM_API_KEY:-}`, so `kctl-dokploy -p kodemeio deploy apply -f
  deploys/instances/production/kod-infra-dsh.yaml` can never fail on a FRIDAY-only value and take
  the web Harness down with it. The fail-closed part lives in `friday-server.mjs`, which exits 1
  before it listens when either value is empty. **If you want FRIDAY to run from the declarative
  path, the two values must also be in `deploys/env/production/.env.kod-infra-dsh`** — Dokploy's
  own env store (Steps 3/5) and that file are separate sources, and only Dokploy's carries them
  today. A declarative apply without them leaves `friday` restarting with
  `friday-server: FRIDAY_RUNNER_TOKEN must be set (32+ characters)` in its log: FRIDAY down, the
  web Harness unaffected.

---

## Step 1 — GitHub tokens

Four distinct fine-grained PATs are involved. Never let any two of them be the same value —
the broker, checks and comment paths are deliberately separated so that a compromised read-only
or comment-only token cannot write code or merge anything.

| Token | Env var | Scope | Repos | Status |
|---|---|---|---|---|
| Broker's own token | `GITHUB_TOKEN` (kodemeio-dsh) | Contents: write, Pull requests: write, **Issues: write** (the PR label call is an issues-API call — see the scope note below) | `DSH_BROKER_REPOS` (13 repos, already live) | **exists** — needs `kodemeio-hatchet` and `kodemeio-llmlite` added to its repository access list, and its permission list checked against the note below |
| Read-only checks token | `FRIDAY_CHECKS_TOKEN` (worker) | Checks: read, Pull requests: read, Metadata: read | the 6 friday.yaml repos only | new |
| Optional comment token | `FRIDAY_COMMENT_TOKEN` (worker) | Issues: write only | the 6 friday.yaml repos only | new, optional (empty = no issue comments, Mattermost notify only) |
| CI sibling-checkout token | `CONTRACTS_READ_TOKEN` (GitHub Actions secret, kodemeio-dokploy) | Contents: read | `kodemeio-dsh` only | new |

**Scope note for the broker token (final review I5 — verify at rollout):** the broker's label
call is `POST /repos/{owner}/{repo}/issues/{n}/labels` (`kodemeio-dsh/broker/server.py`,
`publish`). GitHub's REST reference for *Add labels to an issue* lists **Issues: write** for
fine-grained tokens — **not** `Pull requests: write`, even though the issue is a pull request —
and this runbook and kodemeio-dsh's own docs used to claim the opposite. Nobody has made that
call against the live token yet, so treat it as a claim to check, not a proven defect: the label
step runs *after* the draft PR is persisted, so an under-scoped token produces the worst shape of
failure (PR created, `friday` label never applied, broker returns 502, worker classifies
`broker_502` as retryable, retries 3×, then reports the run failed — with an unlabelled draft PR
sitting on GitHub). **Step 9, step 4 is the check:** the first live PR must carry the `friday`
label. If it does not, grant the broker token **Issues: write** (permissions can be edited on a
fine-grained token without changing its value) and re-run — do not mint a new token, or the
broker needs redeploying.

**Do (broker token):** GitHub → Settings → Developer settings → Fine-grained tokens → find the
token that is `kodemeio-dsh`'s live `GITHUB_TOKEN` → Repository access → add `kodemeio-hatchet`
and `kodemeio-llmlite`; then check Permissions → Repository permissions has `Issues: write` as
well as `Contents: write` and `Pull requests: write`. Editing repository access or permissions on
an existing fine-grained token does not change its value, so nothing needs redeploying for this
half.

**Do (the three new tokens):** create each at
`https://github.com/settings/personal-access-tokens/new` with exactly the scopes and repos in
the table above, resource owner `tgunawandev`, expiration per your token-rotation policy (fine-
grained tokens cap at 1 year; put a rotation reminder wherever you already track that). Save each
value to 1Password immediately, never to a file in this repo.

**Command (CONTRACTS_READ_TOKEN, once the value exists):**
```bash
kctl-github secrets set CONTRACTS_READ_TOKEN -r kodemeio-dokploy
# prompts for the value; never echo it back
```

**Verify:**
```bash
kctl-github secrets list kodemeio-dokploy    # CONTRACTS_READ_TOKEN present
kctl-github secrets audit                    # matrix view, confirm no unintended repo has it
```
Push kodemeio-dokploy's `validate.yml` restructuring (task-2-report fix round 1: sibling checkout
of `kodemeio-dsh`) only after this secret exists — before that, the `repository` job in
`validate.yml` fails loudly by design (task-2-report C1). Do not add `repository` to branch
protection's required checks (Step 10) until a CI run on GitHub has gone green with this secret
in place.

**Rollback:** delete or edit-down repository access on the broker token; delete the two new
worker tokens from GitHub Settings; `kctl-github secrets list kodemeio-dokploy` then remove
`CONTRACTS_READ_TOKEN` via the GitHub UI (Settings → Secrets and variables → Actions) if it must
be un-set — `kctl-github secrets` has no delete verb.

---

## Step 2 — Labels: `friday:approved` and `friday`

`friday:approved` is the approval record itself (spec §2: "the label is the approval record") —
it must exist before anyone can apply it. `friday` is what the broker adds to a FRIDAY PR; GitHub
can auto-create it on first use, but pre-creating it keeps its color/description consistent and
lets you filter on it immediately.

**Command** (loop over exactly the six friday.yaml repos, not "all kodemeio-* repos" —
`kctl-github labels sync` fans out to every kodemeio-* repo, which is wider than this list):
```bash
for repo in kodemeio-dokploy kodemeio-next kodemeio-react kodemeio-odoo kodemeio-hatchet kodemeio-llmlite; do
  gh label create "friday:approved" -R "tgunawandev/${repo}" \
    -c 0E8A16 -d "Human approval to let FRIDAY open a draft PR for this issue" -f
  gh label create "friday" -R "tgunawandev/${repo}" \
    -c 5319E7 -d "Opened by FRIDAY (kodemeio-dsh session)" -f
done
```

**Verify:**
```bash
for repo in kodemeio-dokploy kodemeio-next kodemeio-react kodemeio-odoo kodemeio-hatchet kodemeio-llmlite; do
  kctl-github labels list "$repo" | grep -E "friday(:approved)?"
done
```

**Rollback:** `gh label delete "friday:approved" -R tgunawandev/<repo> --yes` per repo (this
strips the label from any issue that already carries it — think before removing on a repo with
open work).

---

## Step 3 — Wire the LiteLLM `friday` key

**Prerequisite:** `kodemeio-llmlite/docs/runbook-production.md` Step 8 has run
(`reconcile_keys.py --apply`) and `~/.config/kodemeio/llmlite-keys.env` holds `LITELLM_KEY_FRIDAY`.

**Do:** that alias is not the env var name any container reads — this step is exactly the
renaming hop. `FRIDAY_LLM_API_KEY` goes on the `friday` compose service (kodemeio-dsh), never on
the interactive web Harness's `dsh` service (which keeps its own `DEEPSEEK_API_KEY`/LiteLLM `dsh`
key — separate budget, separate kill switch, design D5).

**Command:**
```bash
D_ID=$(kctl-dokploy --json -p kodemeio compose list \
  | jq -r '.[] | select(.name=="kod-infra-dsh") | .composeId')
kctl-dokploy -p kodemeio compose env set "$D_ID" FRIDAY_LLM_API_KEY \
  "$(grep '^LITELLM_KEY_FRIDAY=' ~/.config/kodemeio/llmlite-keys.env | cut -d= -f2)"
```
This only stages the value in Dokploy — it takes effect at Step 5's redeploy, which also renders
the `friday` service for the first time. Setting it before Step 5 is required, not optional: the
compose renders without it (see the I7 note below), but `friday-server` refuses to start with an
empty one, so Step 5 would deploy a `friday` container that exits 1 on start.

**Also add both FRIDAY names to the declarative env file (final review I7).** Dokploy's own env
store (the command above, and `FRIDAY_RUNNER_TOKEN` in Step 5) is the live source of truth, but
the repo's declarative path renders the same compose from a *file*:
`deploys/env/production/.env.kod-infra-dsh`, which does not carry these two. The compose no longer
refuses to render without them (`${FRIDAY_RUNNER_TOKEN:-}`/`${FRIDAY_LLM_API_KEY:-}`), so a
`deploy apply` cannot take the web Harness down — but it would leave `friday` crash-looping with
`friday-server: FRIDAY_LLM_API_KEY must be set`. Append both lines to the real (gitignored)
`deploys/env/production/.env.kod-infra-dsh` with the same values Dokploy holds, and keep the
sanitized `.env.kod-infra-dsh.example` (already carrying both names, values empty) in sync.

**Verify (never print the key):**
```bash
kctl-dokploy --json -p kodemeio compose env list "$D_ID" | jq 'has("FRIDAY_LLM_API_KEY")'   # true
grep -c '^FRIDAY_LLM_API_KEY=' deploys/env/production/.env.kod-infra-dsh                      # 1
grep -c '^FRIDAY_LLM_API_KEY=' deploys/env/production/.env.kod-infra-dsh.example              # 1 (empty value)
```

**Rollback:** `kctl-dokploy -p kodemeio compose env delete "$D_ID" FRIDAY_LLM_API_KEY` — safe
before Step 5 has deployed the `friday` service; after that, removing it makes the next redeploy
start `friday` with an empty key, and `friday-server` exits 1 before it listens — FRIDAY stops,
the rest of the stack (web Harness included) is unaffected. That is a valid kill switch (see
"Kill switch" below).

---

## Step 4 — The live git bug (`GIT_CONFIG_KEY_0` scrub)

**Background (task-3-report, "likely prod bug in web sessions"):** the harness scrubs any
environment variable matching `/KEY|PASSWORD|SECRET|TOKEN/i` from every agent command. The old
compose passed git's safe-directory config as three variables —
`GIT_CONFIG_COUNT=1`/`GIT_CONFIG_KEY_0=safe.directory`/`GIT_CONFIG_VALUE_0=*` — and the scrub
strips `GIT_CONFIG_KEY_0` (it matches `KEY`), so git inside a session fails with `missing config
key GIT_CONFIG_KEY_0`. This affects **every** session, not just FRIDAY's — the same scrub runs in
the interactive web Harness.

**Fix:** kodemeio-dsh's already-committed fix round 1 (`ee1b4ee`) replaces the triple with a
single variable the scrub does not match:
```yaml
GIT_CONFIG_PARAMETERS: "'safe.directory'='*'"
```
This is already in the `dsh` service in the local `docker-compose.prod.yml` (confirm with
`grep -n GIT_CONFIG_PARAMETERS docker-compose.prod.yml` before Step 5 — it should be the only
`GIT_CONFIG_*` variable left). **There is nothing to author here** — this step exists in the
runbook so the founder knows Step 5's push is also a live-bug fix for the web UI, not only a
FRIDAY feature push, and so the verification below is run once Step 5 is live.

**Verify (after Step 5 is deployed):** open the web Harness UI and run any bash tool call that
touches git inside a task checkout (e.g. `git status`) — it must succeed, not raise
`missing config key GIT_CONFIG_KEY_0`. For FRIDAY, this is covered by
`tests/egress/run.sh`'s FRIDAY section already exercising git inside the sandboxed session
(task-3-report fix round 1, "friday edit session exit 0").

---

## Step 5 — Deploy the `friday` container (kod-infra-dsh redeploy)

This redeploys the **existing** `kod-infra-dsh` compose with the FRIDAY commit range applied. It
adds the `friday` and `friday-egress-proxy` services and the `dsh-friday`/`dsh-dispatch` internal
networks; it does not touch the web Harness's own containment (already live via the LiteLLM
runbook's Step 14).

**Pre-checks:**
```bash
D_ID=$(kctl-dokploy --json -p kodemeio compose list \
  | jq -r '.[] | select(.name=="kod-infra-dsh") | .composeId')
SERVER_ID=$(kctl-dokploy --json -p kodemeio compose get "$D_ID" | jq -r '.serverId')
kctl-dokploy --json -p kodemeio servers get "$SERVER_ID" | jq '{name, ipAddress}'
DSH_SSH="<your-ssh-user>@<ipAddress above>"   # identify the host by IP, not by name (per LiteLLM runbook, names have diverged before)

# both secrets must exist before the redeploy, or `friday` starts and exits 1
# (empty FRIDAY_* -> friday-server refuses to run). The compose itself renders
# without them -- that is deliberate (I7): the declarative apply path carries
# neither, and it must never be FRIDAY that makes the web Harness un-renderable.
kctl-dokploy -p kodemeio compose env set "$D_ID" FRIDAY_RUNNER_TOKEN "$(openssl rand -hex 32)"
# FRIDAY_LLM_API_KEY was set in Step 3 -- confirm it is still there
kctl-dokploy --json -p kodemeio compose env list "$D_ID" | jq '{FRIDAY_RUNNER_TOKEN: has("FRIDAY_RUNNER_TOKEN"), FRIDAY_LLM_API_KEY: has("FRIDAY_LLM_API_KEY")}'
```

**Command (push the whole FRIDAY range — re-derive it, never trust a list written days ago;
final review C2):**
```bash
git -C kodemeio-dsh fetch origin
git -C kodemeio-dsh log --oneline bdc234b..main
# expect these nine, newest first (see "What 'done' looks like today"; re-check against a newer
# fix round if one has landed -- the list is a snapshot, the command is the contract):
#   9a46e73 c2e9c88 db6590a 6d816ab 2fe3cb2 b313a48 750b6c2 ee1b4ee 5f52c25
# 6d816ab is REQUIRED: it creates the kod-infra-dsh-dispatch network the worker uses (Step 6
# pre-checks it). 750b6c2 belongs to the LiteLLM track and rides along.
git -C kodemeio-dsh merge-base --is-ancestor origin/main bdc234b && echo "LiteLLM runbook Step 14 already pushed: OK"
git -C kodemeio-dsh merge-base --is-ancestor bdc234b main && echo "fast-forward from bdc234b: OK"
git -C kodemeio-dsh push origin main:refs/heads/main

kctl-dokploy -p kodemeio compose redeploy "$D_ID"
kctl-dokploy -p kodemeio compose logs "$D_ID" -n 80
kctl-dokploy -p kodemeio compose services "$D_ID"
# expect: gateway, harness-edge, egress-proxy, llm-edge, dsh, broker, friday, friday-egress-proxy
```

**Verify:**
```bash
ssh "$DSH_SSH" 'docker inspect -f "{{.State.Health.Status}}" kod-infra-dsh-friday-1 kod-infra-dsh-friday-egress-proxy-1 kod-infra-dsh-broker-1'
# expect: healthy healthy healthy (container name suffix may differ -- confirm with `docker ps`)

# friday listens on nothing but 8090 on its own network, no Traefik route, no published port
ssh "$DSH_SSH" 'F=$(docker ps -q --filter label=com.docker.compose.service=friday --filter label=com.docker.compose.project=kod-infra-dsh);
  docker inspect -f "{{range \$k, \$v := .NetworkSettings.Networks}}{{\$k}} {{end}}" $F;
  docker port $F'
# expect: exactly one network (…_dsh-friday), no port lines

# Re-run the containment suite the F6/F7 evidence comes from, from a kodemeio-dsh checkout ON the
# host, at the commit Step 5 just pushed. It builds its OWN throwaway copy of the stack (own
# project name, own stand-in networks, generated test-only secrets -- the real .env is never
# read), runs the session probe and the peer checks, and tears it all down: nothing live is
# touched and no state is left behind. (final review C1)
ssh "$DSH_SSH" 'cd /path/to/kodemeio-dsh && git fetch origin && git checkout main && git pull --ff-only && bash tests/egress/run.sh'
# expect: "== total: 0 failure(s)", and in the FRIDAY section the 67 session checks PASS --
# session runs as uid 10002, no dsh-token/dsh-home visible, no route to harness:3081/broker:8081/
# dsh:3080 except the model through the session's own egress proxy, and that proxy is
# friday-egress-proxy, not the web Harness's egress-proxy.
#
# DO NOT run tests/egress/friday-probe.sh by hand in the web Harness's `dsh` container (final
# review C1). That probe is written to run INSIDE a FRIDAY session: it asserts `id -u` is 10002,
# that /run/dsh and /home/dsh/.dsh are empty, that 127.0.0.1:3080 does not answer, that the
# session's proxy is friday-egress-proxy, and it reads the other-task.txt fixture from a session
# checkout. In the dsh container every one of those is false (uid 10000, the launch token in
# /run/dsh, the live session store in /home/dsh/.dsh, the Harness itself on 127.0.0.1:3080, the
# web egress-proxy), so it prints a wall of FAILs and proves nothing about FRIDAY. A verifier that
# always fails gets ignored; a probe that never really ran means F6 is "green" only because
# nothing checked.
```
Step 4's own verification is separate and is *not* covered by this suite: run one manual `git
status` in the web Harness UI (Step 4). Do not read it out of the FRIDAY probe's output.

**Rollback:** revert **the whole pushed range**, newest first, on top of what is actually deployed
— never force-push, and never revert a hand-copied list of "the FRIDAY commits" (final review C2:
the old five-SHA list here omitted `6d816ab`, which creates the `dsh-dispatch` network, and
`db6590a` the probe commit, and wrongly included `750b6c2`, which belongs to the LiteLLM track):
```bash
git -C kodemeio-dsh fetch origin
git -C kodemeio-dsh switch -c rollback/friday-container origin/main
git -C kodemeio-dsh revert --no-edit $(git -C kodemeio-dsh rev-list --no-merges bdc234b..main)
git -C kodemeio-dsh push origin HEAD:refs/heads/main
kctl-dokploy -p kodemeio compose redeploy "$D_ID"
```
`rev-list --no-merges bdc234b..main` re-derives the set at run time (today: `9a46e73 c2e9c88
db6590a 6d816ab 2fe3cb2 b313a48 750b6c2 ee1b4ee 5f52c25`, i.e. the same nine Step 5 pushed); if a
later fix round landed after this runbook, it is included automatically, which is the point. The
`friday` and `friday-egress-proxy` services and the `dsh-friday`/`dsh-dispatch` networks
disappear; the broker and the web Harness are unaffected
(they predate this range). If the `friday_dispatch` worker (Step 6) is still running when you do
this, its calls to `http://friday:8090` start failing closed (503/connection refused) — stop the
worker first (kill switch, below) unless you want it to fail loudly instead of silently.

---

## Step 6 — Deploy the `friday_dispatch` worker

**Pre-step — push the worker's source first (final review C3).** `compose update --source-type
github` below makes Dokploy clone `kodemeio-hatchet@main` and build
`workers/friday_dispatch/deploy/Dockerfile` from it, so Step 6 has **no source to deploy** until
that branch actually carries the worker. Today it does not: `workers/friday_dispatch/` is
committed locally (`d9fa292`) but `d9fa292` is **not an ancestor of `origin/main`** (tip
`cd359af`), which is why this step used to say "local and untracked" — it is committed, just
unpublished. Count the gap at run time; other tracks are still committing to that branch.

```bash
git -C kodemeio-hatchet fetch origin
git -C kodemeio-hatchet log --oneline origin/main..main
# READ every commit above before publishing: local main also carries other tracks' finished work
# (kido_chat, order_intake, CI changes). Pushing main publishes all of it, not just the worker.
git -C kodemeio-hatchet push origin main:refs/heads/main
git -C kodemeio-hatchet merge-base --is-ancestor d9fa292 origin/main && echo "worker commit is on origin/main: OK"
git -C kodemeio-hatchet ls-tree --name-only origin/main -- workers/friday_dispatch/deploy/
# expect at least: Dockerfile, docker-compose.yml, .env.example
```
Without this, Step 6 fails with a missing compose path (or, worse, builds a stale tree if a subset
ever gets pushed). Nothing in this slice pushes it for you — it is a founder step.

**PENDING — confirm before running:** this step assumes Task 4's
`kodemeio-hatchet/workers/friday_dispatch/deploy/{Dockerfile,docker-compose.yml,.env.example}`
are committed as described in `deploy-notes.md` (they are: `d9fa292`). Re-read `deploy-notes.md`
and diff it against the actual files before running these commands — service names, networks and
required env vars all come from that compose file.

Two containers, one image, split by what each may reach: `friday-ingress` (public webhook
receiver, `dokploy-network` only) and `friday-worker` (the Hatchet task; **listens on nothing** —
no `expose`, no `ports`, no Traefik label in the compose file; it only calls out to
`friday:8090` over `dsh-friday`, `broker:8081` over `dsh-dispatch`, and its own `friday-egress`
bridge for the Hatchet engine, GitHub and Mattermost). Must deploy on the **same host** as
`kod-infra-dsh`, and **after** Step 5 (it consumes the two networks Step 5 creates).

**Pre-checks:**
```bash
D_ID=$(kctl-dokploy --json -p kodemeio compose list \
  | jq -r '.[] | select(.name=="kod-infra-dsh") | .composeId')
SERVER_ID=$(kctl-dokploy --json -p kodemeio compose get "$D_ID" | jq -r '.serverId')
DSH_SSH="<your-ssh-user>@<ipAddress from Step 5's pre-checks>"

# same host as kod-infra-dsh -- both external networks must already exist
ssh "$DSH_SSH" 'docker network inspect -f "{{.Name}} internal={{.Internal}}" kod-infra-dsh-friday kod-infra-dsh-dispatch'
# expect both: internal=true

ENV_ID=$(kctl-dokploy --json -p kodemeio projects get apps \
  | jq -r '.environments[] | select(.name=="production") | .environmentId')
```

**Command (create against the git source, same pattern as `kod-infra-dsh`):**
```bash
W_ID=$(kctl-dokploy --json -p kodemeio compose create "$ENV_ID" \
  --name kod-infra-friday-dispatch \
  --description "FRIDAY dispatch: webhook -> broker -> friday runner -> draft PR (Teracorp SW1)" \
  --server "$SERVER_ID" --no-auto-deploy \
  | jq -r '.composeId')

kctl-dokploy -p kodemeio compose update "$W_ID" \
  --source-type github --owner tgunawandev --repo kodemeio-hatchet --branch main \
  --compose-path workers/friday_dispatch/deploy/docker-compose.yml

# secrets first: THIS worker compose (workers/friday_dispatch/deploy/docker-compose.yml, its own
# imperative stack) really does use :?required and refuses to render without them. Do not confuse
# it with kod-infra-dsh's shared compose, which is deliberately non-fatal for FRIDAY's values (I7).
# FRIDAY_WEBHOOK_SECRET is generated ONCE, here -- Step 8 reads it back rather than
# re-generating it, so the worker and every repo's GitHub webhook always agree on the value.
kctl-dokploy -p kodemeio compose env set "$W_ID" FRIDAY_WEBHOOK_SECRET "$(openssl rand -hex 32)"
kctl-dokploy -p kodemeio compose env set "$W_ID" FRIDAY_GITHUB_OWNER tgunawandev
kctl-dokploy -p kodemeio compose env set "$W_ID" FRIDAY_REPOS \
  "kodemeio-dokploy,kodemeio-next,kodemeio-react,kodemeio-odoo,kodemeio-hatchet,kodemeio-llmlite"
kctl-dokploy -p kodemeio compose env set "$W_ID" FRIDAY_APPROVERS tgunawandev
kctl-dokploy -p kodemeio compose env set "$W_ID" HATCHET_CLIENT_TOKEN "<from 1Password, same tenant token order_intake/kido_chat use>"
kctl-dokploy -p kodemeio compose env set "$W_ID" FRIDAY_CHECKS_TOKEN "<Step 1's read-only PAT>"
kctl-dokploy -p kodemeio compose env set "$W_ID" FRIDAY_COMMENT_TOKEN "<Step 1's optional PAT, or leave unset>"
kctl-dokploy -p kodemeio compose env set "$W_ID" FRIDAY_NOTIFY_URL "<Mattermost incoming webhook, https, optional>"

# these two must be byte-identical to kod-infra-dsh's values -- never printed, only compared (below)
kctl-dokploy -p kodemeio compose env set "$W_ID" BROKER_CLIENT_TOKEN \
  "$(kctl-dokploy --json -p kodemeio compose env list "$D_ID" | jq -r '.BROKER_CLIENT_TOKEN')"
kctl-dokploy -p kodemeio compose env set "$W_ID" FRIDAY_RUNNER_TOKEN \
  "$(kctl-dokploy --json -p kodemeio compose env list "$D_ID" | jq -r '.FRIDAY_RUNNER_TOKEN')"

kctl-dokploy -p kodemeio compose start "$W_ID"
```
Immediately after, push `FRIDAY_WEBHOOK_SECRET` to 1Password too (Dokploy stays the live source
of truth; 1Password is the durable backup, per this workspace's "never store secrets only in one
place" habit). `kctl-op push` uploads **.env files** — it does not read stdin and has no verb that
takes a value (final review M1), so the readback has to be materialized into a file first:
```bash
# The file is gitignored (workers/friday_dispatch/deploy/.gitignore, asserted by
# tests/test_deploy.py); umask keeps it 0600. Delete it once the push below is verified if you do
# not want the value on disk.
(umask 077; kctl-dokploy --json -p kodemeio compose env list "$W_ID" \
  | jq -r '"FRIDAY_WEBHOOK_SECRET=" + .FRIDAY_WEBHOOK_SECRET' \
  > kodemeio-hatchet/workers/friday_dispatch/deploy/.env.kod-infra-friday-dispatch)

kctl-op -p kodemeio push --project kodemeio-hatchet --env kod-infra-friday-dispatch --dry-run
# the dry-run prints the vault/item it would write -- and errors with "No .env file found for
# kodemeio-hatchet/kod-infra-friday-dispatch" if discovery does not see the file, so read it
# before trusting the real push
kctl-op -p kodemeio push --project kodemeio-hatchet --env kod-infra-friday-dispatch
kctl-op -p kodemeio vault items --vault <the vault the dry-run named>   # item exists, names only
unset FRIDAY_WEBHOOK_SECRET
```
**Rollback:** the 1Password item is a backup copy, not an input to anything — delete or rotate it
in 1Password if the value is being retired; Dokploy remains the live source either way.

**Verify:**
```bash
kctl-dokploy -p kodemeio compose services "$W_ID"   # friday-ingress, friday-worker, both running
kctl-dokploy -p kodemeio compose service-logs "$W_ID" --tail 80

# secrets match kod-infra-dsh's, without ever printing either
for v in BROKER_CLIENT_TOKEN FRIDAY_RUNNER_TOKEN; do
  a=$(kctl-dokploy --json -p kodemeio compose env list "$D_ID" | jq -r --arg v "$v" '.[$v]' | sha256sum)
  b=$(kctl-dokploy --json -p kodemeio compose env list "$W_ID" | jq -r --arg v "$v" '.[$v]' | sha256sum)
  [ "$a" = "$b" ] && echo "$v matches" || echo "$v MISMATCH -- fix before continuing"
done

# friday-worker must not listen on dsh-friday or anywhere else (N6, task-3-report)
ssh "$DSH_SSH" 'W=$(docker ps -q --filter label=com.docker.compose.service=friday-worker --filter label=com.docker.compose.project=kod-infra-friday-dispatch);
  docker inspect -f "{{range \$k, \$v := .NetworkSettings.Networks}}{{\$k}} {{end}}" $W;
  docker port $W'
# expect: dsh-friday, dsh-dispatch and its own friday-egress -- no port lines, no dokploy-network

# /healthz stays container-local, never routed by Traefik
ssh "$DSH_SSH" 'I=$(docker ps -q --filter label=com.docker.compose.service=friday-ingress --filter label=com.docker.compose.project=kod-infra-friday-dispatch);
  docker exec "$I" python -c "import urllib.request; urllib.request.urlopen(\"http://127.0.0.1:8080/healthz\", timeout=3)" && echo "local healthz: OK"'

# operator CLI (inside friday-ingress)
INGRESS=$(ssh "$DSH_SSH" "docker ps -q --filter label=com.docker.compose.service=friday-ingress --filter label=com.docker.compose.project=kod-infra-friday-dispatch")
ssh "$DSH_SSH" "docker exec $INGRESS python -m friday_dispatch unsubmitted"   # expect empty
```

**Rollback:**
```bash
kctl-dokploy -p kodemeio compose stop "$W_ID"          # stop, keep the record for inspection
# or, to remove the compose service entirely:
kctl-dokploy -p kodemeio compose delete "$W_ID"        # destructive
```

---

## Step 7 — DNS for `friday-hooks.kodeme.io`

Run this after Step 6 has the ingress container running (so the record points at a live host),
and before Step 8's webhook creation (GitHub's initial `ping` delivery needs the name to resolve
to get a clean 202, though a webhook can be created before the name resolves — it just shows a
failed delivery until it does).

**Command:**
```bash
SERVER_ID=$(kctl-dokploy --json -p kodemeio compose list \
  | jq -r '.[] | select(.name=="kod-infra-friday-dispatch") | .serverId')
DSH_PUBLIC_IP=$(kctl-dokploy --json -p kodemeio servers get "$SERVER_ID" | jq -r '.ipAddress')
kctl-cf -p kodemeio records create --zone kodeme.io --type A --name friday-hooks \
  --content "$DSH_PUBLIC_IP" --proxied
```

**Verify:**
```bash
kctl-cf --json -p kodemeio records list --zone kodeme.io --type A \
  | jq '.[] | select(.name=="friday-hooks.kodeme.io")'
dig +short friday-hooks.kodeme.io

curl -s -o /dev/null -w '%{http_code}\n' -X POST https://friday-hooks.kodeme.io/github/webhook
# expect 400/401 (no valid signature yet -- no webhook has posted a real one) -- NOT 404, and
# NOT a connection error, which would mean DNS or Traefik routing is still wrong
```

**Rollback:**
```bash
RECORD_ID=$(kctl-cf --json -p kodemeio records list --zone kodeme.io --type A \
  | jq -r '.[] | select(.name=="friday-hooks.kodeme.io") | .id')
kctl-cf -p kodemeio records delete "$RECORD_ID" --zone kodeme.io --force
```

---

## Step 8 — Webhooks

One webhook per repo, `issues` events only, pointed at the worker's ingress from Step 6 at the
DNS name from Step 7.

**Do:** read back the secret Step 6 generated — never re-generate it here, or the worker and
GitHub will disagree and every signature check will fail closed:
```bash
W_ID=$(kctl-dokploy --json -p kodemeio compose list \
  | jq -r '.[] | select(.name=="kod-infra-friday-dispatch") | .composeId')
FRIDAY_WEBHOOK_SECRET=$(kctl-dokploy --json -p kodemeio compose env list "$W_ID" | jq -r '.FRIDAY_WEBHOOK_SECRET')
```

**Command** (per repo — `gh api`, since neither `kctl-github` nor `gh` core has a webhook-create
verb of its own):
```bash
for repo in kodemeio-dokploy kodemeio-next kodemeio-react kodemeio-odoo kodemeio-hatchet kodemeio-llmlite; do
  gh api "repos/tgunawandev/${repo}/hooks" -X POST \
    -f name=web \
    -f config[url]=https://friday-hooks.kodeme.io/github/webhook \
    -f config[content_type]=json \
    -f config[secret]="$FRIDAY_WEBHOOK_SECRET" \
    -f config[insecure_ssl]=0 \
    -f events[]=issues \
    -f active=true
done
unset FRIDAY_WEBHOOK_SECRET
```

**Verify:**
```bash
for repo in kodemeio-dokploy kodemeio-next kodemeio-react kodemeio-odoo kodemeio-hatchet kodemeio-llmlite; do
  gh api "repos/tgunawandev/${repo}/hooks" --jq '.[] | select(.config.url=="https://friday-hooks.kodeme.io/github/webhook") | {id, events, active}'
done
```
Then, from each repo's Settings → Webhooks → (this hook) → Recent Deliveries: the initial `ping`
should show **202 `{"code":"ignored_event"}`** per `deploy-notes.md` — that is the expected,
correct response, not a failure. If a delivery shows 503 `submit_failed`, press **Redeliver**
(deploy-notes.md's documented recovery) once the worker is confirmed healthy.

**Rollback:**
```bash
HOOK_ID=$(gh api "repos/tgunawandev/<repo>/hooks" --jq '.[] | select(.config.url=="https://friday-hooks.kodeme.io/github/webhook") | .id')
gh api "repos/tgunawandev/<repo>/hooks/${HOOK_ID}" -X DELETE
```

---

## Step 9 — End-to-end smoke test

Pick the lowest-stakes repo of the six (e.g. `kodemeio-llmlite`) for the first live run.

1. Open a throwaway issue: a one-line, harmless request (e.g. "add a comment to the README
   noting today's date").
2. Apply `friday:approved` as the `tgunawandev` account (must be in `FRIDAY_APPROVERS`).
3. Watch: `kctl-dokploy -p kodemeio compose service-logs "$W_ID" --tail 80 -f` — expect
   `received -> task_created -> session_running -> published`.
4. `kctl-github prs` / `gh pr list -R tgunawandev/kodemeio-llmlite` — a **draft** PR titled
   `[WO-…] …`, labelled `friday`, body linking the issue and the work order id. **Check the label
   explicitly** — this is the live verification of the broker token's permission scope (finding
   I5): `gh pr view <n> -R tgunawandev/kodemeio-llmlite --json labels --jq '.labels[].name'`. If
   the label is missing, the PR is still good (title and body carry the WO) but the label call
   was refused: grant the broker token **Issues: write** (Step 1's scope note) and re-run, do not
   mint a new token.
5. Confirm the CI check named in Step 10's table below runs and reports on that PR.
6. Confirm no auto-merge, no ready-for-review flip, no deploy happened — the PR stays draft until
   a human acts on it.
7. Close the loop: either merge it (founder) or close it, then delete the throwaway issue's
   label so it does not linger as "approved."

If step 3 never reaches `published`, check `checks.wait`'s failure step in the log (comment on
the issue if `FRIDAY_COMMENT_TOKEN` is set) and Mattermost if `FRIDAY_NOTIFY_URL` is set.

---

## Step 10 — Branch protection (do this last)

**Do not run this until:**
1. Each repo's CI workflow (Task 5) is pushed and has produced **at least one green run on
   GitHub** under the exact check name in the table below (a required check that has never
   reported success permanently blocks every PR merge on that repo).
2. For kodemeio-dokploy specifically: `CONTRACTS_READ_TOKEN` (Step 1) exists, the pre-existing
   ruff-format failure named in "Two checks that cannot go green today" below is fixed, and the
   `repository` job has gone green with both.
3. Step 9's smoke test has produced one real FRIDAY PR and its check ran.
4. You have read "Two checks that cannot go green today" below — the table is not the same list
   it was before that section existed.

| Repo | Branch | Required check(s) (exact GitHub context) |
|---|---|---|
| kodemeio-odoo | `18.0` | `PR Gate` |
| kodemeio-next | `main` | `ci` |
| kodemeio-llmlite | `main` | `Unit tests + compose syntax` |
| kodemeio-dokploy | `main` | `manifests`, `terraform`, and `repository` **only after** the blocker in "Two checks that cannot go green today" below is cleared |
| kodemeio-hatchet | `main` | `validate`, `worker-tests` |
| kodemeio-react | `main` | **none yet** — `Quality Checks` is honest and red; see below |

Context names are each workflow's job `name:` field when set, otherwise the job id verbatim —
read from the workflow files directly (`grep -n 'name:\|^  [a-z-]*:$' .github/workflows/*.yml`
under `jobs:`) rather than assumed, since a rename in either place breaks this silently.

### Two checks that cannot go green today (read before requiring them)

Both of these would satisfy Step 10's precondition 1 on paper and then permanently block PR
merges on their repo — including FRIDAY's — because `enforce_admins: true` applies to you too.

**kodemeio-react's `Quality Checks` is deliberately NOT required (final review I2).** The
workflow is honest (its `continue-on-error` was removed on 2026-09-26 and the header documents
why), but it is red on arrival for two reasons that live in other in-flight work, so it cannot
produce the one green run a required check needs:

- `@kodemeio/kctl-api#test:ci` — `src/__tests__/info.test.ts` times out at vitest's 5 s default
  (first-run tsx/esbuild compile cost, not a logic bug). Remediation: raise `testTimeout` for
  that suite (per-test or in the package's vitest config), then re-run.
- `@kodemeio/erp-spa#build` — its `prebuild`/`fetch:schema` step calls a live Odoo instance and
  refuses with "set ODOO_API_KEY, or ODOO_USER and ODOO_PASSWORD, before running codegen", and
  this workflow sets none of those. Remediation: add an `ODOO_API_KEY` repository secret (a
  read-only Odoo account) for the codegen step, or give the build a `--no-codegen` path.

Add `Quality Checks` to kodemeio-react's required checks only after both are fixed **and** a run
on GitHub is green. FRIDAY can still open PRs against kodemeio-react in the meantime; they are
simply not gate-protected there yet.

**kodemeio-dokploy's `repository` is red for a second, pre-existing reason (final review I3).**
Reason one is by design and documented: the sibling checkout needs `CONTRACTS_READ_TOKEN` (Step 1)
and fails loudly until it exists. Reason two is outside this slice and has nothing to do with
FRIDAY: `uv run ruff format --check deploys ops/scripts` fails on
`deploys/tests/test_gen_hermes_agents.py` (committed by the Hermes/Factory track, `06b2bd3`); this
is the PROGRAM NOTE carried in the SDD ledger and it is reproduced by
`uv run ruff format --check deploys ops/scripts` → "Would reformat:
deploys/tests/test_gen_hermes_agents.py". One command in that track clears it:

```bash
uv run ruff format deploys/tests/test_gen_hermes_agents.py   # then commit it in that track
```

Sequence it that way: format commit → a green `repository` run on GitHub → then add `repository`
to kodemeio-dokploy's required checks. Adding it before that makes every PR to dokploy
unmergeable, which is the failure this table exists to avoid.

`enforce_admins: true` so that even the founder's own account cannot use the **merge button**
without the check passing — this is what makes "branch protection requires the CI gate" (spec
§2) actually true for FRIDAY's own PRs, which are opened under the same GitHub identity as the
broker's token. `restrictions: null` (no push allowlist) keeps **direct `git push`** to the
branch open, consistent with this workspace's rule that `main`/`18.0` are the team's development
branches and direct pushes are allowed during active development — required status checks gate
the PR merge button, not a bare `git push`.

**Command** (repeat per row in the table; example for kodemeio-odoo):
```bash
gh api repos/tgunawandev/kodemeio-odoo/branches/18.0/protection -X PUT --input - <<'JSON'
{
  "required_status_checks": {"strict": true, "checks": [{"context": "PR Gate"}]},
  "enforce_admins": true,
  "required_pull_request_reviews": null,
  "restrictions": null
}
JSON
```
For kodemeio-hatchet, list both contexts (`validate`, `worker-tests`) in one `checks` array. For
kodemeio-dokploy, list `manifests` and `terraform` only — add `repository` to that array after
the blocker above is cleared and it has one green run (the same `PUT` re-sends the whole array,
so re-run it with all three when that day comes).

**Verify:**
```bash
gh api repos/tgunawandev/kodemeio-odoo/branches/18.0/protection --jq '.required_status_checks'
# open a second throwaway PR (any repo): confirm the merge button is disabled until the named
# check reports success, then confirm a normal `git push` to the branch itself still succeeds
```

**Rollback:**
```bash
gh api repos/tgunawandev/kodemeio-odoo/branches/18.0/protection -X DELETE
```

---

## Kill switch

Four independent ways to stop FRIDAY, from least to most disruptive (task-4-brief's own list —
use the narrowest one that solves the problem):

1. **Remove approval capability.** Drop the approver from `FRIDAY_APPROVERS`
   (`kctl-dokploy -p kodemeio compose env set "$W_ID" FRIDAY_APPROVERS "<remaining logins>"` +
   redeploy), or remove `friday:approved` label-write permission from whoever should no longer
   approve. New approvals stop; anything already queued still runs.
2. **Stop the worker.** `kctl-dokploy -p kodemeio compose stop "$W_ID"` — queued Hatchet runs wait
   up to the task's 24 h `schedule_timeout` then expire; nothing new dispatches. Re-labelling an
   issue while stopped is simply picked up once the worker restarts (re-run on the same work
   order, same branch/PR, since the work order id is deterministic per `owner/repo#issue`).
3. **Revoke a token.** Revoking `BROKER_CLIENT_TOKEN` (rotate it on both `kod-infra-dsh` and
   `kod-infra-friday-dispatch`, letting them mismatch) or the broker's own GitHub PAT stops every
   `POST /tasks`/`publish` call immediately, including any in flight; the interactive human DSH
   flow that shares the same broker is affected too — prefer switch 2 if only FRIDAY should stop.
4. **Disable the webhook.** Toggle "Active" off on a repo's webhook (GitHub UI, or
   `gh api repos/<owner>/<repo>/hooks/<id> -X PATCH -F active=false`) to stop new approvals from
   that one repo reaching the worker at all, without touching any other repo or the worker itself.

---

## Full rollback (reverse order)

If FRIDAY must come out entirely: Step 10 (delete branch protection) → kill switch 4 on every
repo (disable webhooks) or delete them (Step 8's rollback) → Step 6's rollback (stop or delete
the worker compose) → Step 5's rollback (revert the FRIDAY commit range, redeploy `kod-infra-dsh`)
→ Step 1's rollback (revoke the three new tokens; restore the broker token's repository access)
→ Step 2's rollback (delete the labels) → Step 7's rollback (delete the DNS record) if the
endpoint is being retired for good. Never force-push any of this; a revert commit on top of
`origin/main`, as in Step 5, is always the way back.

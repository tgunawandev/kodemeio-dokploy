# Runbook — Website factory on Terakidz (founder-gated)

Takes the Factory commons slice (FC1–FC5) and the website factory (F2) from *built and
tested locally* to **one live Terakidz lead page** on the kodeme.io estate: brand-kit
governed, licensed assets only, automatic checks → expert → founder, and every captured lead
landing in Odoo in the Terakidz company.

Everything in this runbook is a founder step (**M**) except the read-only checks, which are
marked as such. Nothing here has been applied. Every command uses the kodeme.io estate only
(`-p kodemeio`, or `./odoo.sh kod|kod-desk … prod`); no idtpp host, bucket, key or job is
touched, and no production write happens anywhere in this document without `--yes`.

Slice of record:
F0 factory-commons design, 2026-09-26 and
F0 factory-commons plan, 2026-09-26 (Tasks 1–11). Roadmap rows: F0a, F0c, F0d,
F0e, F2.

## Facts this runbook is built on (verified read-only, 2026-09-27)

| # | Fact | How it was read |
|---|---|---|
| 1 | The landing renderer is ALREADY LIVE as compose **`kod-landing-web`** (id `WC--JcWDKQquI3nOsjoDj`, created 2026-09-19 outside the manifest tree), project `apps`, server **abc-prod-02** — the same host as erp/hrms/desk Odoo, Authentik and Mattermost | `kctl-dokploy -p kodemeio compose list` / `compose get` |
| 2 | It serves **`go.desk.kodeme.io`** (Dokploy domain → port 3000) with `IMAGE=ghcr.io/tgunawandev/kodemeio-landing-web:sha-678db05`, `ODOO_BASE_URL=https://desk.kodeme.io`, `LANDING_ROOT_SLUG=workspace`, `autoDeploy=false` | `compose get` (non-secret keys only) |
| 3 | It is built from **`tgunawandev/kodemeio-next@main`**, composePath `apps/landing-web/docker-compose.prod.yml` | `compose get` |
| 4 | That compose already carries every production rule the estate asks for: external `dokploy-network`, `restart: unless-stopped`, healthcheck on `/api/healthz`, limits 1.0 cpu / 512M, **no published ports**, Traefik rule `Host(${LANDING_HOST})` with `priority=100` + `tls=true` + `certresolver=letsencrypt` | `kodemeio-next/apps/landing-web/docker-compose.prod.yml` |
| 5 | `deploys/generate.py` does **not** own this path: it generates the `odoo` / `web.corporate` / `web.careers` shapes only, and `--tenant kod --dry-run --diff` writes nothing under `apps/landing-web` | `python3 deploys/generate.py --tenant kod --dry-run --diff` |
| 6 | `bases/nextjs.yaml` names `server: kodemeio-service` and `project: kod` — **neither exists** on this Dokploy (servers are abc-prod-01/02 and kod-prod-01/02; projects are web/database/apps). The new manifest overrides both. | `kctl-dokploy -p kodemeio servers list` / `projects list` |
| 7 | `go.terakidz.id` **does not exist**; the `terakidz.id` zone is active in Cloudflare here. `terakidz.id` and `www.terakidz.id` currently point at `terakidz-landing.pages.dev` (Cloudflare Pages) — leave them alone. The live campaign-host pattern is `go.desk.kodeme.io` → **A 138.199.213.219, unproxied** (abc-prod-02) | `kctl-cf -p kodemeio zones list` / `records list --zone …` |
| 8 | Odoo profiles: `./odoo.sh kod prod` → `kodemeio-kod-odoo-erp` (erp.kodeme.io); `./odoo.sh kod-desk prod` → `kodemeio-kod-odoo-desk` (desk.kodeme.io); `./odoo.sh kod-hris prod` → `kodemeio-kod-odoo-hrms` | `kodemeio-odoo/odoo.yaml` |
| 9 | `kctl-odoo shell call` IS a generic ORM call (`kctl-odoo shell call <model> <method> '[args]' -k '{kwargs}'`), which is what pushes a brand kit — no bespoke JSON-RPC needed. It calls **model-level** methods: `import_kit` works; a **recordset** method does not (it silently no-ops on an empty recordset) | `kctl-odoo shell call --help` |
| 10 | The factory modules are committed and green locally: `factory_base` 139, `factory_landing` 46 (4 acceptance), `landing_base` 96, `api_landing` 22, `landing_crm` 6 — 0 failed each (counts after the final review's fix wave); `install/private-factory.yaml` validates (2 modules / 2 groups, 0 errors) | the slice's task reports + `final-review-fix-report.md`; `bin/validate-bundles` |

🔴 **The one design trap this estate must respect**: the renderer resolves the TENANT inside
Odoo (`landing.domain` → site), and its `ODOO_BASE_URL` is ONE instance for ALL hosts it
serves. So two campaign hosts served by **different Odoo instances** need **two containers** —
and the compose's Traefik router name is the literal `landing-web`, so a second container of
that same compose on the same Traefik would collide on the router name (`traefik.http.routers.
landing-web.*`). Step M0 decides between the two legal shapes; step M7 gives both.

---

## M0 — Decide: which Odoo instance serves the Terakidz pages, and one container or two

Options, with what each costs:

| Shape | When it is right | Cost |
|---|---|---|
| **A. Re-point the existing `kod-landing-web`** (`ODOO_BASE_URL` → the Terakidz instance, `LANDING_HOST` → `go.terakidz.id`) | Terakidz pages and the desk pages live on the SAME Odoo. **Today they do not** | `go.desk.kodeme.io` stops resolving against desk — every desk page it serves goes down with it |
| **B. Second container, same Odoo instance** (a new Dokploy domain on the existing compose; `go.terakidz.id` added to that instance's `landing.domain`) | Terakidz and desk pages share one Odoo instance | no new container; needs the instance to hold both sites |
| **C. Second container, different Odoo instance** | Terakidz pages on erp.kodeme.io while the desk stays on desk.kodeme.io (the likely end state) | 🔴 needs the router name parameterised in `kodemeio-next` FIRST (M7a), otherwise two routers named `landing-web` fight over the same Traefik |

Recommended order: **B now, C when the Terakidz pages must live on the ERP instance.** No
manifest change is needed for B (a Dokploy domain + a `landing.domain` row); C is what
`deploys/instances/production/kod-web-landing.yaml` describes.

**Founder decision to record here (fill in and commit):**

```
Terakidz pages are served by:   [ ] desk.kodeme.io (kod-desk)   [ ] erp.kodeme.io (kod)
Shape:                          [ ] A   [ ] B   [ ] C
Campaign host:                  go.terakidz.id      (confirm or replace)
```

**Rollback:** none — this step decides, it does not change anything.

---

## M1 — Install the factory + landing modules on the chosen instance (M)

🔴 **The install is the LAST step of a build chain, not a push.** `factory_base`/`factory_landing`
are new modules AND the `factory` bucket was added to `docker/odoo.conf.template`
(`addons_path`), and **that template is baked into the BASE image** (`Dockerfile.base`;
`docker/entrypoint.sh` renders `/etc/odoo/odoo.conf` from it at start). An app image inherits
whichever base it was built from, and **nothing in this repository builds on a push** (owner
rule, 2026-09-26: `build-base.yml` is manual-only). So on a target whose base predates this
slice, the modules are not on the addons path at all and M1's install refuses — correctly
(`✘ not in the running image: …`, "a not-yet-shipped module can't be RPC-installed (would
drift)"). The artefact order is:

| # | Artefact | How it is made | Must exist before |
|---|---|---|---|
| 1 | the pushed commit on `origin/18.0` | `git push` of the founder's checkout | everything below |
| 2 | the **BASE** image (carries `odoo.conf.template` → the `factory` addons path) | `gh workflow run build-base.yml --ref 18.0` (~7 min, **manual only**) | step 3 |
| 3 | the **APP** image (`FROM base` + `src/private`) | `./odoo.sh release … install …` (triggers `build-only.yml` and waits), or `gh workflow run build-only.yml --ref 18.0` (~3 min) | step 4 |
| 4 | the **redeploy** of the compose onto that image | inside `release … install`, or `--no-build` after step 3 | step 5 |
| 5 | the modules **installed** | M1's `addon install` (RPC) or the same `release … install` | M2 onwards |

### M1.0 — Build and deploy the image that carries them (M)

```bash
cd kodemeio-odoo                                   # the founder's checkout, on 18.0
git push origin 18.0                               # 1: `release` builds from origin/18.0, not the working tree

# 2: the BASE image -- MANUAL BY DESIGN, and the step that is easy to miss. The factory bucket
#    lives in docker/odoo.conf.template, which is baked into the base; without this build the
#    app image below inherits a base whose addons_path has no /opt/odoo/src/private/factory.
gh workflow run build-base.yml --ref 18.0
gh run list --workflow build-base.yml --branch 18.0 -L 1     # verify: completed / success

# 3+4+5: app image (build-only.yml, waited on), redeploy, and `-i` for the modules -- one command.
#    `install` mode (not `reliable`): `-u` cannot install a module the target does not have yet.
./odoo.sh release kod prod install factory_base,factory_landing,landing_base,landing_crm,api_landing --yes
# (or ./odoo.sh release kod-desk prod install … for the desk instance from M0)
```

`release … install` is the whole chain: it triggers the app-image build, waits for the run it
started, redeploys with `ODOO_INSTALL_MODULES` armed and clears that variable afterwards.
`--dry-run` prints the resolved plan and changes nothing (verified:
`Compose: y0gfh3PIrT9lDn_OdSUEI (dokploy: kodemeio)`, `Action: CI build → ODOO_INSTALL_MODULES
redeploy (-i)`). If the app image was already built, `--no-build` skips step 3 — and refuses
unless the tree is clean and `HEAD == origin/18.0`.

**Then the ordinary one-liner below works**, because the modules are in the image. It is the
same install over RPC, and it verifies image presence first (refusing rather than drifting):

```bash
cd kodemeio-odoo                                   # the founder's checkout, on 18.0
./odoo.sh addon kod prod install factory_base,factory_landing,landing_base,landing_crm,api_landing --yes
# (or ./odoo.sh addon kod-desk prod install … for the desk instance)
```

Resolution (verified with `--dry-run`): `kctl-odoo -p kodemeio-kod-odoo-erp modules install
factory_base,factory_landing,landing_base,landing_crm,api_landing`.

The bundle names behind that list: `private-factory:core,landing` (factory_base,
factory_landing) + `private-landing:core,crm` (landing_base, landing_crm) + `api_landing`
(the `agents` group of private-landing). On a fresh instance the deploy-time form is
`ODOO_INSTALL_BUNDLE=private-factory:core,landing,private-landing:core,crm,private-landing:agents`
in the compose env — but on a live instance use the command above.

**Verify**

```bash
./odoo.sh addon kod prod list | grep -E "factory_base|factory_landing|landing_base|landing_crm|api_landing"
./odoo.sh addon kod prod health                 # no missing deps, no orphans
```

**Rollback** — uninstall is not needed and is destructive (`--yes` drops data): if the
install must be undone, `./odoo.sh addon kod prod uninstall factory_landing,factory_base
--yes` **only** while no work order exists. Prefer leaving the modules installed and inert;
they ship inert and create nothing until M4–M6.

---

## M2 — Users: founder, experts, asset manager (Authentik + Odoo groups) (M)

1. **Authentik** (login identity; the Odoo accounts get their SSO mapping):

```bash
kctl-ak -p kodemeio users invite expert@terakidz.id --name "Terakidz Expert"
kctl-ak -p kodemeio users list | grep terakidz          # confirm + capture the OIDC UID
```

2. **Odoo users** with the SSO mapping (repeat per person; `--groups` is not used here —
   group assignment is step 3, because the factory groups are not in the role catalog yet):

```bash
kctl-odoo -p kodemeio-kod-odoo-erp users create expert@terakidz.id \
  --name "Terakidz Expert" --email expert@terakidz.id \
  --oauth-provider authentik --oauth-uid <uid-from-step-1>
```

3. **Factory groups** (assign in Settings → Users & Companies → Users → *Access Rights*, or
   the group's own form). Four roles, no more:

| Person | Odoo groups to add |
|---|---|
| Founder | `Factory / Founder` (`factory_base.group_factory_founder`) — implies manager + user |
| Expert A/B (terakidz.locale) | `Factory / User` (`factory_base.group_factory_user`) + **named in the brand kit's `reviewers.expert_logins`** (M4) |
| Asset manager | `Factory / Asset Manager` (`factory_base.group_factory_asset_manager`) — **at least two people**: an uploader cannot verify their own asset |
| Producer (page author) | `Landing / User` (`landing_base.group_landing_user`) + `Factory / User` |

🔴 The founder group has **no seed user** on purpose: whoever holds
`group_factory_founder` can approve anything, and the tier's second signature is what makes
the gate a gate. Grant it to exactly the people who may publish.

🔴 **Founder decision, recorded (2026-09-27): a submitter who also holds `group_factory_founder`
may approve the founder tier of their own order.** The gate bars cycle submitters from the
*expert* tier and bars one person from approving both tiers, which is what the spec requires;
the founder tier stays open to the producer because on a single-brand estate the founder is
often the producer. This is a founder ruling, **not a defect** — revisit it when a second
factory line reuses this adapter, where the second signature will have to come from the founder
tier rather than the expert tier.

**Verify**

```bash
kctl-odoo -p kodemeio-kod-odoo-erp users groups <login> | grep -i factory
```

**Rollback:** remove the group assignment in the same screen; deactivate the Odoo user
(`kctl-odoo … users deactivate <login>`) and deactivate the Authentik account. An expert who
leaves is also removed from the kit's `expert_logins` at the next kit import (M5).

---

## M3 — Register and verify the assets, with licence evidence on file (M)

Every image and font a factory page may use is a `factory.asset` row: Factory → Assets →
New. Register, then **a second person** verifies.

Per asset: `key` (`^[a-z0-9-]+$`), `name`, `kind`, `public_url` (https, no other scheme), the
licence (`licence_type`, `commercial_use`), and the **evidence**: either
`licence_evidence_attachment_id` (upload the receipt/licence file) or `licence_evidence_url`.
`owned` needs an author note; `cc_by` needs the attribution; fonts need `family`.

The Terakidz kit already names two things, so both must exist before the kit can be imported:

| Asset | kind | Where | Notes |
|---|---|---|---|
| `font-nunito` (family `Nunito`) | font | Google Fonts | OFL-1.1 — evidence = the licence page URL |
| `font-nunito` is also the kit's `assets` entry; add at least one hero image, e.g. `hero-terakidz`, kind `image`, with the licence receipt attached | image | your own photo/shot | `owned` + author note, or a stock licence with its receipt |

🔴 `action_verify()` refuses the uploader, an asset with no evidence, a non-commercial
licence, an expired one, and any font without a family. A revoked asset cannot reach a new
publish (the checks re-run at publish; a revocation schedules a to-do for every founder
listing the live pages that load it).

**Verify**

```
Factory → Assets → filter State = Verified       # every kit ref is present
```

**Rollback:** `Revoke` with a reason (the reason goes to the chatter; the live page keeps
serving until a person withdraws it, but no new publish passes).

---

## M4 — Push the brand kits from the committed YAML (M)

The kits live in `kodemeio-dokploy/brands/*.yaml` (contract:
`contracts/brands/brand_kit.v1.schema.json`). `factory.brand.kit.import_kit(payload, sha256)`
is manager-only and recomputes the sha over the canonical JSON, so the payload must be
canonicalised exactly the same way.

```bash
cd kodemeio-dokploy
# 1. build the canonical (payload, sha256) pair straight from the committed YAML
python3 - <<'PY' > /tmp/terakidz-kit.json
import hashlib, json, yaml
d = yaml.safe_load(open("brands/terakidz.yaml"))
c = json.dumps(d, sort_keys=True, separators=(",", ":"))
print(json.dumps([d, hashlib.sha256(c.encode()).hexdigest()]))
PY
# 2. push it as the founder/manager (the API key comes from 1Password into the kctl-odoo
#    profile; it is never pasted into a document, a ticket or a chat)
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.brand.kit import_kit "$(cat /tmp/terakidz-kit.json)"
```

Before importing, the founder must fill in the two placeholders the kit ships with:
`reviewers.expert_logins` (the logins from M2) and — if the palette/WhatsApp number are to
change — `colors.*` / `contact.whatsapp`. Both kits have been prepared and schema-validated
locally; `brands/terakon.yaml` exists to prove two brands render differently from one tool.

**Verify**

```bash
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.brand.kit search_read '[[]]' \
  -k '{"fields":["code","version","status","active"],"order":"code"}'
# expect terakidz 1 active (and terakon 1 draft/active per the file)
```

The Odoo view (Factory → Brand Kits) shows the same rows read-only: a kit is a snapshot of
the file, and the only way to change one is to import a new version.

**Rollback:** import an earlier version of the YAML — `import_kit` archives the previous
active version of the same code. There is no "un-import": a kit that names a bad expert login
is replaced, never edited.

---

## M5 — The governed site, its domain, the CRM route, the WhatsApp CTA (M)

In Odoo, on the chosen instance, for the Terakidz company (repeat per brand):

1. **Site** — Landing → Sites → New: `name` "Terakidz", a short `key`, `company` = the
   Terakidz company, **`brand_kit_id` = `terakidz`**. Setting the kit is what makes the site
   *factory-governed*: the kit's theme and prompt context replace the site's, and every
   publish needs an approved work order. 🔴 Clearing the kit again needs a system
   administrator, and a site with live pages cannot be governed in one step (withdraw them
   first) — that is deliberate.
2. **Domain** — on the site, add `landing.domain` = **`go.terakidz.id`** (the campaign host
   from M0). This row is the only authorization the public route has: an unknown host is a
   404, never a default site.
3. **CRM route** — Landing → CRM Routes → New: `site_id` = the site, `team_id` = the team
   that owns Terakidz leads, `auto_create` = **on**, tags as wanted. Without a route, a lead
   is captured and stays a `landing.lead`; with `auto_create` on, every new consenting
   lead becomes a `crm.lead` in the same transaction, in the **site's company**.
4. **CTA with the kit's number** — Landing → CTA Actions: a `whatsapp` action whose
   `whatsapp_number` is the kit's `contact.whatsapp`. A CTA to any other number fails the
   `pii` check at submit (the renderer builds the same `https://wa.me/…` target the check
   inspects).
5. **WIP limit** — Factory → Lines → `website`: `wip_limit` = the number of Terakidz pages
   that may be in flight at once (the seeded default is 1; the limit counts
   in_progress + checking + in_review + approved, and `done`/`cancelled` release the slot).

**Verify**

```bash
kctl-odoo -p kodemeio-kod-odoo-erp shell call landing.site search_read \
  '[[]]' -k '{"fields":["key","company_id","brand_kit_id","factory_governed"]}'
kctl-odoo -p kodemeio-kod-odoo-erp shell call landing.crm.route search_read \
  '[[]]' -k '{"fields":["site_id","team_id","auto_create"]}'
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.line search_read \
  '[[]]' -k '{"fields":["code","wip_limit","wip_count"]}'
```

**Rollback:** un-govern the site (system administrator, and only with no live page), or set
`auto_create=false` on the route to stop new leads routing into CRM. `landing.domain` rows
can be deleted; nothing else depends on them.

---

## M6 — DNS for the campaign host (M)

```bash
# read first: what exists on the zone today
kctl-cf -p kodemeio records list --zone terakidz.id | grep -E "go\.|terakidz.id|www"

# the campaign host, exactly like go.desk.kodeme.io (unproxied A → the Dokploy server,
# so the container's own Traefik router and its letsencrypt certificate handle the request)
kctl-cf -p kodemeio records create --zone terakidz.id --type A --name go \
  --content 138.199.213.219 --ttl 1 --no-proxied
```

🔴 Do NOT touch the existing `terakidz.id` / `www.terakidz.id` CNAMEs (Cloudflare Pages) or
the Hostinger MX/DKIM rows: this step only ADDS `go.`.

**Verify**

```bash
dig +short go.terakidz.id            # 138.199.213.219
```

**Rollback:** `kctl-cf -p kodemeio records delete --zone terakidz.id --type A --name go --force`
(DNS only; the container keeps serving if it is reachable by another name).

---

## M7 — Deploy the renderer for the Terakidz host (M)

**M7a (only for shape C — a second container on a different Odoo instance).** The compose's
router name is the literal `landing-web`; two containers of it on one Traefik collide. Ship
this one-line change in `kodemeio-next` first (PR + review, it is a routing change):

```yaml
# apps/landing-web/docker-compose.prod.yml — every router/service label line
- "traefik.http.routers.${LANDING_ROUTER:-landing-web}.rule=Host(`${LANDING_HOST:?LANDING_HOST is required}`)"
# …and the same suffix on .priority / .tls / .tls.certresolver / .service, keeping
# traefik.http.services.${LANDING_ROUTER:-landing-web}.loadbalancer.server.port=3000
```
then `LANDING_ROUTER=landing-terakidz` on the new container.

**M7b (build + pin the image).** The image is built by hand from the monorepo root (there is
no CI job for it):

```bash
cd kodemeio-next
TAG=sha-$(git rev-parse --short HEAD)
docker build -f apps/landing-web/Dockerfile -t ghcr.io/tgunawandev/kodemeio-landing-web:$TAG .
docker push ghcr.io/tgunawandev/kodemeio-landing-web:$TAG
```

**M7c (manifest → live).** Shape B uses the existing compose; shape C applies the manifest in
this repo:

```bash
cd kodemeio-dokploy
# 1. validate + see what it would do (no writes)
kctl-dokploy -p kodemeio deploy validate -f deploys/instances/production/kod-web-landing.yaml
kctl-dokploy -p kodemeio deploy status   -f deploys/instances/production/kod-web-landing.yaml

# 2. the environment file (gitignored): copy the example and fill it in
cp deploys/env/production/.env.kod-web-landing.example deploys/env/production/.env.kod-web-landing
#    ODOO_BASE_URL  = the instance from M0            (hostname, not IP)
#    LANDING_HOST   = go.terakidz.id
#    IMAGE          = ghcr.io/tgunawandev/kodemeio-landing-web:<TAG from M7b>

# 3. the apply (founder gate; writes DNS, compose, domain, env and then deploys)
kctl-dokploy -p kodemeio deploy apply -f deploys/instances/production/kod-web-landing.yaml
kctl-dokploy -p kodemeio deploy verify -f deploys/instances/production/kod-web-landing.yaml
```

For shape B instead: add `go.terakidz.id` as a domain on the existing compose
(`kctl-dokploy -p kodemeio compose domains …`) and add `landing.domain` for the new host in
M5 — no new container, no new env file.

**Verify**

```bash
kctl-dokploy -p kodemeio compose get <id>          # status done, both domains listed
curl -sS -o /dev/null -w '%{http_code}\n' https://go.terakidz.id/api/healthz   # 200
curl -sS -o /dev/null -w '%{http_code}\n' https://go.desk.kodeme.io/api/healthz  # still 200
```

**Rollback:** `kctl-dokploy -p kodemeio compose stop <id>` (the container stops; DNS may
stay), or `kctl-dokploy -p kodemeio compose redeploy <id>` after pinning the previous
`IMAGE` value to go back one tag. For shape B, remove the domain entry.

---

## M8 — Smoke test: preview → approvals → publish → lead (M)

Run this on the live instance with a real, small page. Steps 1–5 are the factory's own gate;
6–8 prove the wire.

1. **Create the work order** — Factory → Work Orders → New: line `website`, brand kit
   `terakidz`, a goal, and the inputs (audience, offer, tone, keywords, must_include,
   must_avoid). **Start** it (it takes a WIP slot).
2. **Materialise** — `Materialise landing page` on the order: a DRAFT page + brief + first
   prompt version appear on the governed site.
3. **Generate** — on the page, generate with the brand kit's prompt version, then evaluate
   the result against the rubric (Landing → Generation → Evaluate). 🔴 A generated version
   needs BOTH a passing evaluation and an approval before it may publish.
4. **Submit** — `Submit for review` on the order (binds the version + its document hash;
   the automatic checks run: licence, links, brand rules, AI disclosure, PII).
5. **Expert, then founder** — the named expert approves first, then the founder. Both must be
   different people from the submitter; the expert must not be the page's author either.
   Publish is the founder's (or a Landing Manager's) act and closes the work order itself.
6. **Preview (optional)** — there is no preview button in the Odoo UI; mint the token
   through the governed REST action, which needs an api key or JWT:

```bash
TOKEN=$(curl -sS -X POST "https://<odoo-host>/api/v1/landing-pages/<page_id>/preview-token" \
  -H "Authorization: Bearer $ODOO_API_KEY" -H 'Content-Type: application/json' \
  -d '{"hours":1}' | jq -r .token)
curl -sS -o /dev/null -w '%{http_code}\n' "https://go.terakidz.id/preview?token=$TOKEN"   # 200
```

7. **The live page** — after publish (within the 60 s cache window):

```bash
curl -sS "https://go.terakidz.id/<slug>" | grep -o "<headline text>"        # rendered copy
curl -sS -o /dev/null -w '%{http_code}\n' https://go.terakidz.id/<slug>     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "https://go.terakidz.id/?host=evil.example"  # 404 for an unknown host
```

8. **A lead reaches CRM in the Terakidz company** — submit the page's own form (or POST the
   same JSON the renderer sends):

```bash
curl -sS -X POST "https://go.terakidz.id/api/landing/lead" -H 'Content-Type: application/json' \
  -d '{"host":"go.terakidz.id","slug":"<slug>","name":"Test Lead","email":"test@terakidz.id","consent":true}'
# then, on the instance:
kctl-odoo -p kodemeio-kod-odoo-erp shell call landing.lead search_read '[[]]' \
  -k '{"fields":["id","company_id","email","crm_lead_id","state"],"limit":3,"order":"id desc"}'
kctl-odoo -p kodemeio-kod-odoo-erp shell call crm.lead search_read \
  '[["email_from","=","test@terakidz.id"]]' -k '{"fields":["name","company_id","team_id","contact_name"]}'
```

Expected: a `landing.lead` in the site's company, and a `crm.lead` in the **Terakidz company**
carrying `contact_name`, `email_from` and the phone as FIELDS (not buried in the description).
A user of any other company must not be able to read it.

⚠️ On publish, expect the page to be refused if anything changed since the approval: a
licence revoked, an asset expired, the document re-compiled (a new sha), the WIP slot
released, or an approver who has since lost the group. Each refusal names the work order and
its state — the fix is a new submission, never `force` (which is refused outright on a
governed page).

⚠️ The version is re-checked against the **site as it is now**, at submit and again at publish,
not only when it was frozen: a document compiled while the site was still free (raw image URLs,
the site's own theme) or compiled under a **different brand kit** is refused by name — re-compile
it on the governed site instead of approving it.

---

## M9 — Rollback

| What | Command | What it does / does not do |
|---|---|---|
| Take the page down | Page → **Withdraw** (manager only) | The page stops serving; the site, the kit and the lead history stay |
| Back to an earlier approved version | Page → **Roll back** to that version | Only a version that was already published under an approved order can be rolled back to |
| Un-govern the site | Site → clear `brand_kit_id` (**system administrator**) | The kit theme/rules stop applying and the publish gate stops applying — do this only with no live page, and know it is the one action that removes the gate |
| Stop the renderer | `kctl-dokploy -p kodemeio compose stop <id>` | Every site that container serves goes down, not just Terakidz |
| One tag back | set `IMAGE` to the previous tag, then `compose redeploy <id>` | The previous image serves again |
| Undo the DNS row | `kctl-cf -p kodemeio records delete --zone terakidz.id --type A --name go --force` | DNS only |
| Undo the bundle install | `./odoo.sh addon <tenant> prod uninstall factory_landing,factory_base --yes` | Only while no work order exists; drops the ledger |

---

## Not in this runbook (deliberate)

- **Cloudflare Pages is not used.** The F2 row names it, but the renderer that shipped and is
  live is our own container (`kodemeio-next apps/landing-web`); Pages would add a second
  deploy path to the same app. This slice deploys the container only.
- **No auto-building image.** `kodemeio-next` has no workflow for `kodemeio-landing-web`; M7b
  builds and pushes by hand, and the tag is pinned in the Dokploy env. A CI job is a
  follow-up, not a prerequisite.
- **Factory roles are not in `install/roles-erp.yaml`** yet, so M2 step 3 assigns groups by
  hand. Adding the four factory roles to the catalog is a follow-up that would make it one
  `roles assign` (which REPLACES a user's whole role set — read that trap before using it).
- **One brand per campaign host.** `landing.domain` resolves the SITE; the site's kit is what
  makes the theme and rules. A second brand on the same host is a second site + domain row.

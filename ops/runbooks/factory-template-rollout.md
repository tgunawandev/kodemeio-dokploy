# Runbook — Template factory on kodeme.io (founder-gated)

Takes the template factory (F3 / TPL1) from *built and tested locally* to **one released
printable and one released workbook** on the kodeme.io estate: committed templates imported
read-only, brand-kit governed, licence-clean, released only through the expert → founder gate.

Everything in this runbook is a founder step (**M**) except the read-only checks, which are
marked as such. Nothing here has been applied. Every command uses the kodeme.io estate only
(`-p kodemeio`, or `./odoo.sh kod|kod-desk … prod`); no idtpp host, bucket, key or job is
touched, and no production write happens anywhere in this document without `--yes`.

Slice of record: `kodemeio-docs/superpowers/specs/2026-09-27-teracorp-template-factory-design.md`
and `…/plans/2026-09-27-teracorp-template-factory.md` (Tasks 1–5), plus the **2026-09-27 D2
amendment** (a layout is never a program). Roadmap row: **F3** (TPL1).

## Facts this runbook is built on (verified read-only, 2026-09-27)

| # | Fact | How it was read |
|---|---|---|
| 1 | The two templates and the contract are committed: `templates/terakidz-learning-pack/{template.yaml,layout.html}`, `templates/terakon-planner/{template.yaml,layout.yaml}`, `contracts/templates/template.v1.schema.json` (`kodemeio-dokploy` **1ca1052**) — 27 contract tests green, 0 errors | `uv run pytest deploys/tests -k contracts -q`; `git show --stat 1ca1052` |
| 2 | The module `factory_template` is committed on `18.0` (`kodemeio-odoo` **31abc3607** T2, **2f02ce6b2** T3, **ca6cf7cf3** T4) — **130 tests, 0 failed**; `factory_base` 140 and `factory_landing` 48 stay green on the same test DB | `TEST_DB=odoo_test_factory_tpl ./odoo.sh dev t factory_template` |
| 3 | Bundle group `templates` exists in `install/private-factory.yaml` (depends `core`, one module); `bin/validate-bundles` reports **0 errors** | `bin/validate-bundles install` |
| 4 | 🔴 **The `typst` Python package is NOT in the image** — `python3 -c "import typst"` → `ModuleNotFoundError`. The renderer ships implemented and **refuses with the named error `typst not installed`** at materialise time. It renders the same closed block vocabulary as `wkhtml` (`image` and `spacer` included) and refuses a block it cannot render **by name**, but it does **not** interpret the `.html` layout sidecar — it builds its document from the blocks, the kit's tokens and the page box | `docker exec <odoo> python3 -c "import typst"` (dev image, 2026-09-27); `factory_template/tests/test_formats.py` |
| 5 | `wkhtmltopdf 0.12.6.1`, `openpyxl 3.1.2` and `pypdf 6.18.1` ARE in the image; `wkhtmltox.deb` is installed in the **base** image (`docker/Dockerfile.base:222`), and Python deps come from `requirements-oca.txt` (`docker/Dockerfile.base:116`) | same image; `docker/Dockerfile.base` |
| 6 | The `factory` bucket is already on the addons path in the rendered `/etc/odoo/odoo.conf` (`/opt/odoo/src/private/factory`) — the base image that carries it is built and deployed | `grep factory /etc/odoo/odoo.conf` in the dev image |
| 7 | The four module-level RPC shapes this runbook uses are the ones the F2 runbook already proved: `kctl-odoo shell call <model> <method> '<args json>'` takes **positional args as a JSON array**, `-k` takes kwargs, and it calls **model-level** methods | `kctl-odoo shell call --help` |
| 8 | The `wkhtml` page box is set through wkhtmltopdf's own flags (`--page-size`, `--orientation`, `--margin-*`): the engine ignores the CSS `@page { size: … }` property (measured: an A5 document came out 595×842pt before this was fixed) | `kodemeio-odoo` `factory_template_renderer.py`, `test_variants.py::test_page_size_changes_the_page_box_and_is_recorded` |

🔴 **The one thing that can refuse this whole slice on a real instance**: a template whose
declared fonts/images are not **usable** `factory.asset` rows cannot be imported at all
(`_fa_require_usable` is the single entry point). Step M3 therefore comes BEFORE M4: register
`font-nunito`, `font-inter` and one hero image with licence evidence on file, verified by a
second person.

---

## M0 — Decide: which instance, and whether Typst is wanted now (M)

| Question | Options | Cost |
|---|---|---|
| Which Odoo instance | `./odoo.sh kod prod` (erp.kodeme.io) is the default here; `kod-desk` for the desk instance | one `-p` profile per instance; nothing else changes |
| Renderer for the PDF printable | **`wkhtml` (works today, no image change)** or **`typst`** (needs a base-image rebuild) | Typst is the blueprint's intent; wkhtml is the fleet's existing engine. A template names its renderer, so switching later is a data change in kodemeio-dokploy — not a code change. It is **not a no-op**: typst ignores the html sidecar (its look comes from the kit's tokens and the block vocabulary), so expect a different document |
| Workbook | `xlsx` (openpyxl, in the image) | nothing to decide |

**Founder decision to record here (fill in and commit):**

```
Target instance:       [ ] kod (erp.kodeme.io)   [ ] kod-desk (desk.kodeme.io)
PDF renderer:          [ ] wkhtml (today)        [ ] typst (needs M1b)
Import both templates: [ ] terakidz-learning-pack  [ ] terakon-planner
```

**Rollback:** none — this step decides, it does not change anything.

---

## M1 — Install the bundle group (M)

### M1a — The ordinary path (the `factory` bucket is already in the image)

```bash
cd kodemeio-odoo                                   # the founder's checkout, on 18.0
git push origin 18.0                               # `release` builds from origin/18.0, not the tree

./odoo.sh release kod prod install factory_base,factory_template --yes
# or, if the app image already carries them:
./odoo.sh addon kod prod install factory_base,factory_template --yes
```

`release … install` is the whole chain (app-image build → redeploy with `ODOO_INSTALL_MODULES`
→ clear the variable). `--dry-run` prints the resolved plan and changes nothing. `addon install`
verifies the module is already in the running image and **refuses** rather than drifting.

The `template` `factory.line` row is the kernel's own seed (`factory_base`, `noupdate`, WIP
limit 1) — installing the group adds no line, no template, no kit and no asset.

### M1b — ONLY if M0 chose Typst: the image dependency (M, founder-gated)

The `typst` package and the licensed font files are image-level, never module-level:

```bash
# 1. declare the package (pinned) next to openpyxl/pypdf
edit kodemeio-odoo/requirements-oca.txt           # add:  typst==<version>
# 2. the licensed fonts the templates render with (OFL) must be on the image too: copy them in
edit kodemeio-odoo/docker/Dockerfile.base         # COPY fonts/ /usr/share/fonts/typst/ (or a
                                                  # pip extra), and the renderer's --font-path
git commit … && git push origin 18.0
# 3. the BASE image -- MANUAL BY DESIGN, and the step that is easy to miss
#    (requirements-oca.txt is baked into the base)
gh workflow run build-base.yml --ref 18.0
gh run list --workflow build-base.yml --branch 18.0 -L 1        # verify: completed / success
# 4. the APP image, then the redeploy + install
./odoo.sh release kod prod install factory_base,factory_template --yes
```

**Verify (read-only):**

```bash
kctl-odoo -p kodemeio-kod-odoo-erp shell call ir.module.module search_read \
  '[["name","in",["factory_base","factory_template"]]]' -k '{"fields":["name","state"],"order":"name"}'
# expect: both "installed"
```

There is no separate "is typst available" query to run: a renderer's availability is a property
of the image, and the module reports it through the refusal itself. Materialising a `typst`
template (M5 step 3) either renders or refuses with **`typst not installed` at that moment** —
never silently, and never at release time.

**Rollback:** `./odoo.sh addon kod prod uninstall factory_template --yes` (only while nothing is
released — it drops the renders). The base image goes back by rebuilding it from the previous
commit.

---

## M2 — Users: founder, experts, template users (Authentik + Odoo groups) (M)

| Role | Who | Why |
|---|---|---|
| `factory_base.group_factory_founder` | the founder (+ a second person) | the founder tier; also the ONLY role that may **import a template** (with `base.group_system`) |
| `factory_base.group_factory_manager` | the founder's delegate | manages lines/kits; **cannot** import a template |
| brand-kit experts (`factory.brand.kit.expert_user_ids`) | the named reviewers | the expert tier. Until a kit names at least one, `action_submit` fails closed with *"names no expert reviewer"* |
| `factory_template.group_factory_template_user` | whoever runs template work orders | creates renders through the work order |
| `factory_base.group_factory_asset_manager` | a second person | verifies assets (the uploader never verifies their own) |

Assign through the estate's normal access path (`Authentik → Odoo` roles/groups) — the same route
the F2 runbook used. The kit's `reviewers.expert_logins` are filled in M4.

**Verify:** `kctl-odoo -p kodemeio-kod-odoo-erp groups list --role …` or the Odoo UI
(Settings → Users → Groups → Factory).

**Rollback:** remove the group memberships; nothing else is affected.

---

## M3 — Register and verify the assets, with licence evidence on file (M)

Factory → Assets → New, then **a second person** verifies. What the shipped templates require:

| Asset | kind | Licence | Notes |
|---|---|---|---|
| `font-nunito` | font | OFL-1.1 | family `Nunito`; evidence = the Google Fonts licence URL. Already named by `brands/terakidz.yaml` |
| `font-inter` | font | OFL-1.1 | family `Inter`; named by `brands/terakon.yaml` |
| `image-terakidz-learning-hero` | image | owned (or a stock licence) | the printable's one image role. `owned` needs an author note; a stock licence needs its receipt attached |

Per asset: `key` (`^[a-z0-9-]+$`), `name`, `kind`, `public_url` (**https** — no other scheme),
`licence_type`, `commercial_use`, and the evidence (`licence_evidence_attachment_id` for a file,
`licence_evidence_url` for a link). 🔴 `action_verify()` refuses the uploader, a missing
evidence, a non-commercial licence, an expired one, and a font without a family.

**Verify (read-only):**

```bash
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.asset search_read '[[]]' \
  -k '{"fields":["key","kind","state","licence_type","commercial_use","expires_on"],"order":"key"}'
# expect: the three keys above, state=verified, commercial_use=true, no expiry (or a future one)
```

**Rollback:** `Revoke` with a reason (the reason goes to the chatter). A revoked asset refuses
every later render, submission and release by name; the released artefacts stay as they are.

---

## M4 — Push the brand kits, then import the two templates (M)

The kits come first: `import_template` refuses a template whose fonts are not usable font assets,
and the fonts resolve through the kit.

```bash
cd kodemeio-dokploy
# 1. canonical (payload, sha256) straight from the committed YAML
python3 - <<'PY' > /tmp/terakidz-kit.json
import hashlib, json, yaml
d = yaml.safe_load(open("brands/terakidz.yaml"))
d["reviewers"]["expert_logins"] = ["<expert login from M2>"]
c = json.dumps(d, sort_keys=True, separators=(",", ":"))
print(json.dumps([d, hashlib.sha256(c.encode()).hexdigest()]))
PY
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.brand.kit import_kit "$(cat /tmp/terakidz-kit.json)"
# …and brands/terakon.yaml the same way (it is a DRAFT fixture: import it only to prove the
# two-kit variant, never as approved brand content)

# 2. the templates: (payload, sha256, layout sidecar) — three positional args
for T in terakidz-learning-pack terakon-planner; do
python3 - "$T" <<'PY' > "/tmp/$T.json"
import hashlib, json, sys, yaml
t = sys.argv[1]
d = yaml.safe_load(open(f"templates/{t}/template.yaml"))
layout = open(f"templates/{t}/{d['layout']}").read()
c = json.dumps(d, sort_keys=True, separators=(",", ":"))
print(json.dumps([d, hashlib.sha256(c.encode()).hexdigest(), layout]))
PY
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.template import_template "$(cat /tmp/$T.json)"
done
```

🔴 `import_template` is **founder/administrator only** (a plain manager is refused). It refuses a
sha mismatch, a sidecar that is not `.html`/`.yaml`/`.json` (a `.py` layout above all), a
non-increasing version, an unknown renderer, a block outside the closed vocabulary, an undeclared
variable, and any font/image that is not a usable asset.

**Verify (read-only):**

```bash
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.template search_read '[[]]' \
  -k '{"fields":["code","version","kind","renderer","layout","active"],"order":"code"}'
# expect: terakidz-learning-pack 1 pdf wkhtml active; terakon-planner 1 xlsx xlsx active
```

**Rollback:** a template is archived by importing a newer version, or by an administrator
clearing `active` — it is never edited. Nothing a template does can be undone by editing it.

---

## M5 — One work order, end to end (M)

Factory → Work Orders → New:

1. line **`template`**, brand kit **`terakidz`**, a goal, and the inputs:
   `{"template": "terakidz-learning-pack", "options": {"page_size": "A4", "locale": "id_ID"}}`.
2. **Start** it (it takes the line's WIP slot — limit 1 by default).
3. **Materialise Template**: the render is created and rendered **here**. This is the step that
   refuses an unavailable renderer (`typst not installed`), a sidecar outside its vocabulary, an
   unusable asset, a missing required variable and an option outside the closed set.
4. **Submit for review**: binds `factory.template.render,<id>` + the render's `input_sha256` and
   runs the automatic checks (licence, links, brand rules, AI disclosure, affiliate disclosure,
   PII, plus the three template checks). A failure returns the order to in-progress unbound.
5. **Expert, then founder**: the named expert first (never the render's author or the submitter),
   then a founder. Two different people, neither an agent.
6. **Release** (the adapter's release action, or the render's own state move once the order is
   done): the predicate re-verifies the template, the kit, every licence and every variable
   against what they are NOW, re-runs the checks, and only then marks the order done and the
   render released. 🔴 A kit superseded, a font revoked or an asset expired after the approval
   refuses here by name — the fix is a new submission, never a force (there is none).
7. For the workbook: repeat with `terakon-planner` and the `terakon` kit.

**Verify (read-only):**

```bash
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.template.render search_read '[[]]' \
  -k '{"fields":["template_id","brand_kit_id","state","renderer","page_count","byte_count","input_sha256","artefact_sha256"],"order":"id desc","limit":5}'
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.work.order search_read \
  '[["line_id.code","=","template"]]' -k '{"fields":["work_order_id","state","bound_ref","published_ref"],"order":"id desc","limit":5}'
# expect: a released render, and its order done with published_ref == bound_ref
```

The artefact itself is on the render record (Factory → Renders → *Artefact*); the released
render is immutable for every environment, including an administrator's — the **row** (state,
artefact, hashes, options) and the **artefact's bytes**: `artefact_sha256` is what the release
path re-reads, and a released render's attachment can no longer be written, repointed, re-created
or deleted. A tampered artefact refuses the next submit/release by name.

---

## M6 — Smoke test on the running instance (M)

The local acceptance suite (T1–T8, 130 tests) is the evidence for the rules; this is the
instance-level smoke test:

```bash
# 1. the two templates are imported and active
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.template search_read '[[]]' \
  -k '{"fields":["code","version","renderer","active"],"order":"code"}'

# 2. the assets are verified
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.asset search_read \
  '[["key","in",["font-nunito","font-inter","image-terakidz-learning-hero"]]]' \
  -k '{"fields":["key","kind","state","usable"]}'

# 3. one released render per format
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.template.render search_read \
  '[["state","=","released"]]' -k '{"fields":["template_id","renderer","page_count","byte_count"],"limit":10}'

# 4. nothing is released without an order: this MUST be empty
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.template.render search_read \
  '[["state","=","released"],["input_sha256","=",false]]' -k '{"fields":["id"]}'
```

⚠️ Expected refusals (each names the reason, and each is a feature): submitting after a licence
was revoked; releasing after the kit was superseded; releasing through another work order;
releasing in another company; importing a `.py` layout; importing a template whose font is not a
verified `font` asset.

---

## M7 — Rollback

| What | Command / action | What it does / does not do |
|---|---|---|
| One released render | none — a released render is immutable and is evidence (its row AND its artefact's bytes) | Supersede it with a new render (materialise a new variant) and release that |
| A template that is wrong | import a newer version (M4) or, as an administrator, clear `active` | Nothing already released changes; a new render under the archived template refuses |
| An asset's licence withdrawn | Assets → **Revoke** with a reason | Every later submit/release refuses by name; released artefacts stay |
| The whole line | `./odoo.sh addon kod prod uninstall factory_template --yes` | Only while nothing must be kept: it drops the templates, the renders and the artefacts. `factory_base` (and the ledger) stays |
| One image tag back | set `IMAGE_TAG` to the previous tag, then `compose redeploy <id>` | The previous image serves again |
| Typst was a mistake | set the templates' `renderer` back to `wkhtml` in kodemeio-dokploy and re-import a newer version | Data change; no code change, no rollback of the image needed. The styling changes again (typst ignores the html sidecar) — re-render and re-release, never edit a released render |

---

## Not in this runbook (deliberate)

- **No delivery.** Entitlements, product pages and R2 upload of the artefacts are R6/DIG1. This
  slice stops at a released render with its `ir.attachment` and the licence-checked asset URLs
  recorded on the row.
- **No Typst by default.** The renderer ships implemented and refuses by name until the image
  carries the package (M1b). Turning it on is founder-gated because it is a base-image rebuild.
- **No MCP tools for templates** — the `mcp_base` conflict is unresolved (spec section 7).
- **Factory roles are not in `install/roles-erp.yaml`**, so M2 assigns groups by hand (the same
  follow-up the F2 runbook records).
- **The two shipped templates are examples.** Real pack/planner content is the factory's job
  later; `terakon-planner` renders under a `draft` kit that is itself a test fixture.

# Runbook — E-book factory on kodeme.io (founder-gated)

Takes the e-book factory (F4 / EBK1) from *built and tested locally* to **one released book in
both formats** on the kodeme.io estate: a committed book imported read-only, brand-kit governed,
licence-clean, released only through the expert → founder gate.

Everything in this runbook is a founder step (**M**) except the read-only checks, which are marked
as such. Nothing here has been applied. Every command uses the kodeme.io estate only
(`-p kodemeio`, or `./odoo.sh kod|kod-desk … prod`); no idtpp host, bucket, key or job is touched,
and no production write happens anywhere in this document without `--yes`.

Slice of record: F4 e-book factory design, 2026-09-27 and
F4 e-book factory plan, 2026-09-27 (Tasks 1–4). Roadmap row: **F4** (EBK1).

## Facts this runbook is built on (verified read-only, 2026-09-27)

| # | Fact | How it was read |
|---|---|---|
| 1 | The contract and the example book are committed: `contracts/ebooks/ebook.v1.schema.json`, `ebooks/terakidz-example/{ebook.yaml,book.md}` (`kodemeio-dokploy` **86f93d9**) — 27 contract tests green, 0 errors | `uv run pytest deploys/tests -k contracts -q`; `git show --stat 86f93d9` |
| 2 | The module `factory_ebook` is committed on `18.0` (`kodemeio-odoo` **123a42308** T2, T3, T4) — the full suite green on the test DB, `factory_base` 140 and `factory_template` 144 stay green | `TEST_DB=odoo_test_factory_f4 ./odoo.sh dev t factory_ebook` |
| 3 | Bundle group `ebooks` exists in `install/private-factory.yaml` (depends `core`, one module); `bin/validate-bundles install` reports **0 errors** | `bin/validate-bundles install` |
| 4 | 🔴 **NEITHER ENGINE IS IN THE IMAGE** — `import typst` → `ModuleNotFoundError`, `pandoc` is not on `PATH`. Both renderers ship implemented and **refuse with their named error** (`typst not installed`, `pandoc not installed`) at materialise time, naming every unavailable renderer at once | `docker exec <odoo> python3 -c "import typst"`; `docker exec <odoo> which pandoc`; `factory_ebook/tests/test_formats.py` |
| 5 | `wkhtmltopdf`, `openpyxl` and `pypdf` ARE in the image (the F3 slice proved it); neither is a substitute here — a book's PDF is the Typst route, by spec D2, with **no fallback** | same image; F3 runbook fact 5 |
| 6 | The `factory` bucket is already on the addons path in the rendered `/etc/odoo/odoo.conf` (`/opt/odoo/src/private/factory`) | `grep factory /etc/odoo/odoo.conf` in the dev image |
| 7 | The module-level RPC shape this runbook uses is the one the F2/F3 runbooks proved: `kctl-odoo shell call <model> <method> '<args json>'` takes **positional args as a JSON array**, `-k` takes kwargs, and it calls **model-level** methods | `kctl-odoo shell call --help` |
| 8 | `book.md` is DERIVED from `ebook.yaml` (`factory_ebook.markdown_for` and dokploy's `render_markdown` are the same function) and the import refuses a source that is not the canonical derivation | `factory_ebook/tests/test_import.py::test_a_drifted_markdown_source_is_refused_by_name` |

🔴 **The one thing that can refuse this whole slice on a real instance**: a book whose declared
fonts, images or cover logo are not **usable** `factory.asset` rows cannot be imported at all
(`_fa_require_usable` is the single entry point). Step M3 therefore comes BEFORE M4: register
`font-nunito`, `font-inter`, `image-terakidz-book-logo` and `image-terakidz-learning-hero` with
licence evidence on file, verified by a second person.

🔴 **The second thing is the image itself** (M1b): until the base image carries BOTH engines, a
two-format book cannot be materialised at all, and a single-format one is refused unless the work
order records the choice (M5). That is the honest-refusal law of this slice, not a bug.

---

## M0 — Decide: which instance, and when to close each image gate (M)

| Question | Options | Cost |
|---|---|---|
| Which Odoo instance | `./odoo.sh kod prod` (erp.kodeme.io) is the default here; `kod-desk` for the desk instance | one `-p` profile per instance; nothing else changes |
| The PDF gate (**Typst**) | **wait** (a book cannot be rendered as PDF until the base image carries the `typst` package + the licensed font files) or **rebuild the base image** | the blueprint's intent; a base-image rebuild is founder-gated and is the only thing that turns `typst not installed` into a render |
| The EPUB gate (**Pandoc**) | **wait** (same shape: `pandoc` is a BINARY, not a Python package) or **rebuild the base image** | the blueprint's second format |
| Shipping one format meanwhile | allowed **only** with the work order's explicit recorded choice (`inputs.format_waiver`) | the release predicate refuses a silent single-format release; the choice is visible in the ledger |

**Founder decision to record here (fill in and commit):**

```
Target instance:       [ ] kod (erp.kodeme.io)   [ ] kod-desk (desk.kodeme.io)
Install the group:     [ ] yes
Image gate A (typst):  [ ] now   [ ] later        Image gate B (pandoc): [ ] now   [ ] later
Import the book:       [ ] terakidz-example
Release both formats:  [ ] yes   [ ] single format, recorded choice: ______________________
```

**Rollback:** none — this step decides, it does not change anything.

---

## M1 — Install the bundle group (M)

### M1a — The ordinary path (the `factory` bucket is already in the image)

```bash
cd kodemeio-odoo                                   # the founder's checkout, on 18.0
git push origin 18.0                               # `release` builds from origin/18.0, not the tree

./odoo.sh release kod prod install factory_base,factory_ebook --yes
# or, if the app image already carries them:
./odoo.sh addon kod prod install factory_base,factory_ebook --yes
```

`release … install` is the whole chain (app-image build → redeploy with `ODOO_INSTALL_MODULES`
→ clear the variable). `--dry-run` prints the resolved plan and changes nothing. `addon install`
verifies the module is already in the running image and **refuses** rather than drifting.

The `ebook` `factory.line` row is the kernel's own seed (`factory_base`, `noupdate`, WIP limit 1)
— installing the group adds no line, no book, no kit and no asset.

### M1b — The TWO image gates: Typst (PDF) and Pandoc (EPUB) (M, founder-gated)

🔴 **There are two, they are different kinds of dependency, and each is refused by name until it
is closed.** Both live in the **base** image, so one rebuild covers both:

```bash
# ---- Gate A: Typst (a PYTHON PACKAGE + the licensed font files) -------------------------
# 1. declare the package (pinned) next to openpyxl/pypdf
edit kodemeio-odoo/requirements-oca.txt           # add:  typst==<version>
# 2. the licensed fonts the books render with (OFL/Nunito, OFL/Inter) must be on the image too
edit kodemeio-odoo/docker/Dockerfile.base         # COPY fonts/ /usr/share/fonts/typst/ (or a
                                                  # pip extra), and the renderer's --font-path
# ---- Gate B: Pandoc (a BINARY, not a package) -------------------------------------------
# 3. install it in the base image (apt/pandoc deb, pinned version), and pin it in the Dockerfile
edit kodemeio-odoo/docker/Dockerfile.base         # RUN apt-get install -y pandoc=<version>
git commit … && git push origin 18.0

# 4. 🔴 THE BASE IMAGE -- MANUAL BY DESIGN, and the step that is easy to miss
#    (requirements-oca.txt AND the Dockerfile edits are baked into the base)
gh workflow run build-base.yml --ref 18.0
gh run list --workflow build-base.yml --branch 18.0 -L 1        # verify: completed / success
# 5. the APP image, then the redeploy + install
./odoo.sh release kod prod install factory_base,factory_ebook --yes
```

**Verify (read-only):**

```bash
kctl-odoo -p kodemeio-kod-odoo-erp shell call ir.module.module search_read \
  '[["name","in",["factory_base","factory_ebook"]]]' -k '{"fields":["name","state"],"order":"name"}'
# expect: both "installed"
```

There is no separate "is typst/pandoc available" query to run: availability is a property of the
IMAGE, and the module reports it through the refusal itself. Materialising a book (M5 step 3)
either renders or refuses with **`typst not installed`** / **`pandoc not installed`** at that
moment — naming both when both are missing — never silently, and never at release time.

🔴 **What the two gates do NOT change:** a book's PDF is the Typst route (spec D2) and its EPUB is
the Pandoc route. There is **no `wkhtml` fallback for a book**: `wkhtmltopdf` renders printables,
not books, and F3's renderer is a different line. Adding a fallback would be a spec change, not a
configuration one.

**Rollback:** `./odoo.sh addon kod prod uninstall factory_ebook --yes` (only while nothing is
released — it drops the books and the renders). A base image goes back by rebuilding it from the
previous commit.

---

## M2 — Users: founder, experts, ebook users (Authentik + Odoo groups) (M)

| Role | Who | Why |
|---|---|---|
| `factory_base.group_factory_founder` | the founder (+ a second person) | the founder tier; also the ONLY role that may **import a book** (with `base.group_system`) |
| `factory_base.group_factory_manager` | the founder's delegate | manages lines/kits; **cannot** import a book |
| brand-kit experts (`factory.brand.kit.expert_user_ids`) | the named reviewers | the expert tier. Until a kit names at least one, `action_submit` fails closed with *"names no expert reviewer"* |
| `factory_ebook.group_factory_ebook_user` | whoever runs ebook work orders | creates renders through the work order |
| `factory_base.group_factory_asset_manager` | a second person | verifies assets (the uploader never verifies their own) |

Assign through the estate's normal access path (`Authentik → Odoo` roles/groups) — the same route
the F2 and F3 runbooks used. The kit's `reviewers.expert_logins` are filled in M4.

**Verify:** `kctl-odoo -p kodemeio-kod-odoo-erp groups list --role …` or the Odoo UI
(Settings → Users → Groups → Factory).

**Rollback:** remove the group memberships; nothing else is affected.

---

## M3 — Register and verify the assets, with licence evidence on file (M)

Factory → Assets → New, then **a second person** verifies. What the shipped book requires:

| Asset | kind | Licence | Notes |
|---|---|---|---|
| `font-nunito` | font | OFL-1.1 | family `Nunito`; evidence = the Google Fonts licence URL. Named by `brands/terakidz.yaml` |
| `font-inter` | font | OFL-1.1 | family `Inter`; named by `brands/terakon.yaml` |
| `image-terakidz-book-logo` | image | owned (or a stock licence) | **the cover logo** — the one asset the cover block set places (spec D3). `owned` needs an author note; a stock licence needs its receipt attached |
| `image-terakidz-learning-hero` | image | owned (or a stock licence) | the book's one in-chapter illustration |

Per asset: `key` (`^[a-z0-9-]+$`), `name`, `kind`, `public_url` (**https** — no other scheme),
`licence_type`, `commercial_use`, and the evidence (`licence_evidence_attachment_id` for a file,
`licence_evidence_url` for a link). 🔴 `action_verify()` refuses the uploader, a missing evidence,
a non-commercial licence, an expired one, and a font without a family.

🔴 **The fonts are also the TYPST gate's other half.** Gate A (M1b) puts the `typst` package in
the image; the *files* the kit's `fonts` refs resolve to are the licensed font assets registered
here, and the renderer is told where they are. A font that is not a verified, commercial,
non-expired `font` asset refuses the import, the render, the submit and the release by name — so
register them before the book, not after.

**Verify (read-only):**

```bash
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.asset search_read '[[]]' \
  -k '{"fields":["key","kind","state","licence_type","commercial_use","expires_on"],"order":"key"}'
# expect: the four keys above, state=verified, commercial_use=true, no expiry (or a future one)
```

**Rollback:** `Revoke` with a reason (the reason goes to the chatter). A revoked asset refuses
every later render, submission and release by name; released artefacts stay as they are.

---

## M4 — Push the brand kits, then import the book (M)

The kits come first: `import_ebook` refuses a book whose fonts or images are not usable assets,
and the fonts resolve through the kit. (If F3 has already been rolled out on this instance, M4's
kit step is done — re-running an unchanged kit with a higher version is not needed.)

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

# 2. the book: (payload, sha256, markdown_source) — three positional args. The Markdown is
#    DERIVED from the YAML, exactly as the module derives it; the import refuses anything else.
python3 - <<'PY' > /tmp/terakidz-example.json
import hashlib, json, yaml
d = yaml.safe_load(open("ebooks/terakidz-example/ebook.yaml"))
md = open("ebooks/terakidz-example/book.md").read()
c = json.dumps(d, sort_keys=True, separators=(",", ":"))
print(json.dumps([d, hashlib.sha256(c.encode()).hexdigest(), md]))
PY
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.ebook import_ebook "$(cat /tmp/terakidz-example.json)"
```

🔴 `import_ebook` is **founder/administrator only** (a plain manager is refused). It refuses a sha
mismatch, a missing Markdown source, a **Markdown source that is not the canonical derivation**
(regenerate `book.md`; never hand-edit it), a non-increasing version, a block outside the closed
vocabulary, an undeclared variable, a book with no `disclaimer` block, a declared image nothing
places, `javascript:`/`data:`-shaped markup anywhere a reader sees text, and any font/image/logo
that is not a usable asset.

**Verify (read-only):**

```bash
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.ebook search_read '[[]]' \
  -k '{"fields":["code","version","title","author","locale","outputs","renderers","active"],"order":"code"}'
# expect: terakidz-example 1 "Kata Pertama" id_ID ["pdf","epub"] {"pdf":"typst","epub":"pandoc"} active
```

**Rollback:** a book is archived by importing a newer version, or by an administrator clearing
`active` — it is never edited. Nothing a book does can be undone by editing it.

---

## M5 — One work order, end to end (M)

Factory → Work Orders → New:

1. line **`ebook`**, brand kit **`terakidz`**, a goal, and the inputs:
   `{"ebook": "terakidz-example", "options": {"locale": "id_ID"}}`.
   *Shipping one format instead* takes `"formats": ["pdf"]` **and** `"format_waiver": "<why>"`;
   without the waiver the order refuses at materialise time (spec A5).
2. **Start** it (it takes the line's WIP slot — limit 1 by default).
3. **Materialise Ebook**: the render is created and rendered **here**, one artefact per format.
   This is the step that refuses an unavailable renderer — **naming `typst not installed` AND
   `pandoc not installed` together when both are missing** — an unusable asset, a missing
   required variable, an option outside the closed set, and a format choice that explains nothing.
4. **Submit for review**: binds `factory.ebook.render,<id>` + the render's `input_sha256` and runs
   the automatic checks (licence, links, brand rules, AI disclosure, affiliate disclosure, PII,
   plus `ebook_assets`, `ebook_variables`, `ebook_layout_rules`, `ebook_formats`). A failure
   returns the order to in-progress unbound.
5. **Expert, then founder**: the named expert first (never the render's author or the submitter),
   then a founder. Two different people, neither an agent.
6. **Release**: the predicate re-verifies the book (still active, still the snapshot this render
   was made from, its Markdown still the canonical derivation), the kit, **every format's
   artefact bytes** (`artefact_sha256` re-read) and every licence against what they are NOW,
   re-runs the checks, **requires the work order to carry the same recorded format choice the
   render recorded** when a two-format book ships one, and only then marks the order done and the
   render released. 🔴 A kit superseded, a book re-imported, a font revoked or an asset expired
   after the approval refuses here by name — the fix is a new submission, never a force (there is
   none).

**Verify (read-only):**

```bash
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.ebook.render search_read '[[]]' \
  -k '{"fields":["book_id","brand_kit_id","state","formats","format_waiver","input_sha256"],"order":"id desc","limit":5}'
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.ebook.artefact search_read '[[]]' \
  -k '{"fields":["render_id","format","renderer","page_count","byte_count","artefact_sha256"],"order":"id desc","limit":5}'
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.work.order search_read \
  '[["line_id.code","=","ebook"]]' -k '{"fields":["work_order_id","state","bound_ref","published_ref"],"order":"id desc","limit":5}'
# expect: a released render with TWO artefact rows (pdf + epub), and its order done with
#         published_ref == bound_ref
```

The documents are on the render record (Factory → Renders → *Artefacts*); a released render is
immutable for every environment, including an administrator's — the **row** (state, formats,
hashes, options) and **every artefact's bytes**: `artefact_sha256` is what the release path
re-reads, and a released render's attachments can no longer be written, repointed, re-created or
deleted. A tampered artefact refuses the next submit/release by name.

### 🔴 Two deliberate boundaries (so a later slice does not trip on them)

- **A two-format book may be released in one format only with the recorded choice.** The render
  records it at create and the release predicate requires the work order to carry the SAME string,
  read fresh. A waiver for a book nothing is missing from is itself refused — a choice that
  explains nothing is a licence to release anything. Dropping a format later is a new work order,
  never an edit to a released render.
- **An unavailable renderer is refused at MATERIALISE time, not at release.** That is deliberate:
  a render that exists but cannot be produced would sit in the ledger looking like work in
  progress. The cost is that M1b must be closed before a two-format book can be materialised at
  all — which is what this runbook says in its first line.

---

## M6 — Smoke test on the running instance (M)

The local acceptance suite (A1–A7) is the evidence for the rules; this is the instance-level
smoke test:

```bash
# 1. the book is imported and active
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.ebook search_read \
  '[["active","=",true]]' -k '{"fields":["code","version","outputs","renderers"]}'

# 2. the assets are verified
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.asset search_read \
  '[["key","in",["font-nunito","font-inter","image-terakidz-book-logo","image-terakidz-learning-hero"]]]' \
  -k '{"fields":["key","kind","state","usable"]}'

# 3. the released render and BOTH its artefacts
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.ebook.render search_read \
  '[["state","=","released"]]' -k '{"fields":["book_id","formats","format_waiver"],"limit":10}'
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.ebook.artefact search_read \
  '[["render_id.state","=","released"]]' -k '{"fields":["format","renderer","byte_count"],"limit":10}'

# 4. nothing is released without an order: this MUST be empty
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.ebook.render search_read \
  '[["state","=","released"],["input_sha256","=",false]]' -k '{"fields":["id"]}'

# 5. 🔴 nothing was released single-format without a recorded choice: this MUST be empty
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.ebook.render search_read \
  '[["state","=","released"],["format_waiver","=",false],["formats","not ilike","epub"]]' \
  -k '{"fields":["id","formats"],"limit":10}'
```

⚠️ Expected refusals (each names the reason, and each is a feature): materialising before M1b is
closed (both renderers named); releasing after a licence was revoked; releasing after the book was
re-imported; releasing through another work order; releasing in another company; importing a book
whose `book.md` was edited by hand; importing a book whose cover logo is not a verified image
asset.

---

## M7 — Rollback

| What | Command / action | What it does / does not do |
|---|---|---|
| One released book | none — a released render is immutable and is evidence (its row and every artefact's bytes) | Supersede it with a new render (materialise a new variant) and release that |
| A book that is wrong | import a newer version (M4) or, as an administrator, clear `active` | Nothing already released changes; a new render under the archived book refuses |
| An asset's licence withdrawn | Assets → **Revoke** with a reason | Every later submit/release refuses by name; released artefacts stay |
| The whole line | `./odoo.sh addon kod prod uninstall factory_ebook --yes` | Only while nothing must be kept: it drops the books, the renders and the artefacts. `factory_base` (and the ledger) stays |
| One image tag back | set `IMAGE_TAG` to the previous tag, then `compose redeploy <id>` | The previous image serves again |
| A gate was a mistake | leave it unbuilt; the renderer keeps refusing by name | No rollback needed — an absent engine is a named refusal, never a silent fallback |

---

## Not in this runbook (deliberate)

- **No delivery.** Entitlements, product pages and storefront listing are R5/R6. This slice stops
  at a released render with its `ir.attachment`s and the licence-checked asset URLs recorded on
  the row. 🔴 R6/DIG1 must **re-read each artefact's `artefact_sha256` and compare it with the
  bytes it is about to ship** before delivering a released book: the immutability guard is
  ORM-level, so the hash is what makes a tamper that reached the database some other way visible
  at delivery time (the same re-read the submit and release paths already do).
- **No EPUB DRM and no translation** (spec section 7).
- **No MCP tools for books** — the `mcp_base` conflict is unresolved.
- **Factory roles are not in `install/roles-erp.yaml`**, so M2 assigns groups by hand (the same
  follow-up the F2 and F3 runbooks record).
- **The shipped book is an example.** Real catalogue content is the factory's job later;
  `terakidz-example` renders under a `draft` kit (`terakon`) for the two-kit proof, never as
  approved brand content.

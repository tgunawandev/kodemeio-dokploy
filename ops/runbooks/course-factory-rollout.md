# Runbook — Course factory on kodeme.io (founder-gated)

Takes the course factory (F5 / CRS1) from *built and tested locally* to **one released course,
published per brand site**: a committed course imported read-only, brand-kit governed,
licence-clean, released only through the expert → founder gate, and published through the target
the render names.

Everything here is a founder step (**M**) except the read-only checks, which are marked as such.
Nothing here has been applied. Every command uses the kodeme.io estate only (`-p kodemeio`, or
`./odoo.sh kod|kod-desk … prod`); no idtpp host, bucket, key or job is touched, and no production
write happens anywhere in this document without `--yes`.

Slice of record: F5 course factory design, 2026-09-27
(D1–D6, A1–A7) and F5 course factory plan, 2026-09-27 (Tasks 1–4). Roadmap row: **F5**
(CRS1). The pattern is F3's (`ops/runbooks/factory-template-rollout.md`); this document records
what differs.

## Facts this runbook is built on (verified read-only, 2026-09-27)

| # | Fact | How it was read |
|---|---|---|
| 1 | The contract and the example course are committed: `contracts/courses/course.v1.schema.json`, `courses/terakidz-komunikasi-dasar/{course.yaml,8 × *.md}`, `contracts/examples/courses/` (`kodemeio-dokploy` **ffd3935**, **4954f74**) — 21 contract tests green, 0 errors | `uv run pytest deploys/tests -q` |
| 2 | The module `factory_course` is committed on `18.0` (`kodemeio-odoo` **02294cdfc** T2, **9bc77c37c** T3, **b346aebdc** T4) — **147 tests, 0 failed**; `factory_base` 140, `factory_template` 144 and `factory_landing` 48 stay green on the same test DB | `TEST_DB=odoo_test_factory_f5 ./odoo.sh dev t factory_course` (and the three neighbours) |
| 3 | Bundle group `courses` exists in `install/private-factory.yaml` (depends `core`, one module, in no profile and no recipe); `bin/validate-bundles` reports **0 errors**; `bin/lint-addon-i18n src/private/factory/factory_course` exits 0 | `bin/validate-bundles install`; `bin/lint-addon-i18n …` |
| 4 | 🔴 **`website_slides` (Odoo eLearning) depends on `website`, which this estate rejects by owner decision.** The module IS in the image (`/opt/odoo/src/odoo/addons/website_slides`, `installable: True`), reachable only through `install/core-website.yaml`'s `elearning` group, which **no profile and no recipe installs** | `docker exec … ls /opt/odoo/src/odoo/addons/website_slides`; `install/core-website.yaml:53-60`; `.claude/skills/odoo-landing/SKILL.md:10-11` |
| 5 | The `digital` publish arm works: `digital_base` (products, editions, `digital.render`) is installed by the `private-digital` bundle's `core` group and depends only on `base`/`mail`/`queue_job` | `src/private/digital/digital_base/__manifest__.py`; A5 green in the local suite |
| 6 | 🔴 **The `typst` Python package is NOT in the image** — the `typst` renderer ships implemented and **refuses with the named error `typst not installed`**; the `html` renderer needs nothing | `factory_course/README.md`; `tests/test_render.py::test_an_unavailable_renderer_refuses_at_materialise_time_by_name` |
| 7 | The `course` `factory.line` row is the kernel's own seed (`factory_base`, `noupdate`, WIP limit 1) — installing the group adds no line, no course, no kit and no asset | `factory_base/data/factory_line_data.xml:21` |
| 8 | The four module-level RPC shapes this runbook uses are the ones the F2/F3 runbooks already proved: `kctl-odoo shell call <model> <method> '<args json>'` takes positional args as a JSON **array**, `-k` takes kwargs, and it calls **model-level** methods | `kctl-odoo shell call --help` |

🔴 **The one thing that can refuse this whole slice on a real instance**: a course whose declared
fonts, images or **footage** are not **usable** `factory.asset` rows cannot be imported at all
(`_fa_require_usable` is the single entry point). The shipped course names a video, deliberately
(F6 owns video): until `footage-terakidz-intro` exists as a verified, commercial, unexpired
`footage` asset, the course refuses to import — or the runbook's M3b removes the video lesson
first. That is a feature, not a bug: a lesson never silently loses its video.

---

## M0 — Decide: the publish target (spec D3) (M)

**The verify-first finding**: the spec's eLearning arm is unavailable on this estate, because
`website_slides` depends on `website` and the owner rejects `website`
("website in this repo means the Next.js app"). The digital-product arm (the R6 path) is available
today. The choice is DATA — a render's `options.publish_target`, inside its `input_sha256` — so
switching later is a data change plus an install, never a rewrite.

**Founder decision to record here (fill in and commit):**

```
Publish target:   [x] digital  (R6: the course pack becomes a digital product's edition render)
                  [ ] slides   (Odoo eLearning: requires accepting the `website` module FIRST,
                                then installing install/core-website.yaml's `elearning` group)
Slides renderer:  [x] html     (works today)   [ ] typst (needs the base-image rebuild, M1b)
Import the course:[ ] terakidz-komunikasi-dasar      Video lesson l2-rutinitas: [ ] keep (needs M3b) [ ] drop
```

Accepting the `slides` arm is a **real architectural decision**: it installs `website` (and
`website_mail`, `website_profile`, `portal_rating`) on an instance that has never carried them,
and the estate's landing framework exists precisely because `website` is not the delivery surface.
The publisher is implemented either way; on a database without `slide.channel` it refuses BY NAME
(`Odoo eLearning (website_slides) is not installed — it depends on the website module, which this
estate rejects by owner decision`).

**Rollback:** none — this step decides, it does not change anything.

---

## M1 — Install the bundle group (M)

```bash
cd kodemeio-odoo                                   # the founder's checkout, on 18.0
git push origin 18.0                               # `release` builds from origin/18.0, not the tree

./odoo.sh release kod prod install factory_base,factory_course --yes
# or, if the app image already carries them:
./odoo.sh addon kod prod install factory_base,factory_course --yes
```

`release … install` is the whole chain (app-image build → redeploy with `ODOO_INSTALL_MODULES` →
clear the variable). `--dry-run` prints the resolved plan and changes nothing. `addon install`
verifies the module is already in the running image and **refuses** rather than drifting.

The `course` `factory.line` row is the kernel's own seed (`factory_base`, `noupdate`, WIP limit 1).

### M1a — The digital arm's framework (M, only if M0 chose `digital`)

The publish target probes `env.registry` and does not depend on the framework, so nothing is
required to *install* `factory_course` — but a release with `publish_target = digital` refuses by
name until `digital_base` is there:

```bash
./odoo.sh release kod prod install digital_base --yes    # from install/private-digital.yaml, group core
```

### M1b — ONLY if M0 chose Typst: the image dependency (M, founder-gated)

Exactly as F3's runbook M1b describes for `factory_template`: `typst` + the licensed OFL fonts go
into `requirements-oca.txt` and `docker/Dockerfile.base`, the **base** image is rebuilt by hand
(`gh workflow run build-base.yml --ref 18.0`, ~7 min) and then the app image. There is no separate
"is typst available" query: materialising a course whose renderer is `typst` either renders or
refuses with **`typst not installed` at that moment** — never silently, and never at release time.

**Rollback:** `./odoo.sh addon kod prod uninstall factory_course --yes` (only while nothing is
released — it drops the courses and the renders). The base image goes back by rebuilding it from
the previous commit.

---

## M2 — Users: founder, experts, course users (Authentik + Odoo groups) (M)

| Role | Who | Why |
|---|---|---|
| `factory_base.group_factory_founder` | the founder (+ a second person) | the founder tier; also the ONLY role that may **import a course** (with `base.group_system`) |
| `factory_base.group_factory_manager` | the founder's delegate | manages lines/kits; **cannot** import a course |
| brand-kit experts (`factory.brand.kit.expert_user_ids`) | the named reviewers | the expert tier. Until a kit names at least one, `action_submit` fails closed with *"No expert of the brand kit is independent…"* |
| `factory_course.group_factory_course_user` | whoever runs course work orders | creates renders through the work order |
| `factory_base.group_factory_asset_manager` | a second person | verifies assets (the uploader never verifies their own) |

Assign through the estate's normal access path (`Authentik → Odoo` roles/groups) — the same route
the F2/F3 runbooks used.

**Rollback:** remove the group memberships; nothing else is affected.

---

## M3 — Register and verify the assets, with licence evidence on file (M)

Factory → Assets → New, then **a second person** verifies. What the shipped course requires:

| Asset | kind | Licence | Notes |
|---|---|---|---|
| `font-nunito` | font | OFL-1.1 | family `Nunito`; named by `brands/terakidz.yaml` already |
| `font-inter` | font | OFL-1.1 | family `Inter`; named by `brands/terakona.yaml` |
| `image-terakidz-learning-hero` | image | owned (or a stock licence) | the course's cover role |
| `footage-terakidz-intro` | **footage** | the source's own licence | the `l2-rutinitas` video (spec D4: video is F6) |

### M3b — If the video lesson is kept (M), or dropped (M)

- **Kept**: register `footage-terakidz-intro` as a usable `footage` asset. Until it exists and is
  verified, the course refuses to IMPORT by name (`footage-terakidz-intro: unknown` / `not
  verified`), and after import the render and the release refuse by the same key.
- **Dropped**: remove the `video` block from `courses/terakidz-komunikasi-dasar/course.yaml`, the
  `assets.footage` bucket and the `sources`/lesson references are unaffected — then re-import a
  NEWER `version` and release the new variant. A released render is never edited.

**Verify (read-only):**

```bash
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.asset search_read '[[]]' \
  -k '{"fields":["key","kind","state","licence_type","commercial_use","expires_on"],"order":"key"}'
# expect: the four keys above, state=verified, commercial_use=true, no expiry (or a future one)
```

**Rollback:** `Revoke` with a reason (the reason goes to the chatter). A revoked asset refuses
every later render, submission and release by name; the released artefacts stay as they are.

---

## M4 — Push the brand kits, then import the course (M)

The kits come first: `import_course` refuses a course whose fonts are not usable font assets, and
the fonts resolve through the kit.

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
# …and brands/terakona.yaml the same way (a DRAFT fixture: import it only to prove the two-kit
# variant, never as approved brand content)

# 2. the course: three positional args — (payload, sha256, sources map key -> Markdown text)
python3 - <<'PY' > /tmp/terakidz-course.json
import hashlib, json, yaml
d = yaml.safe_load(open("courses/terakidz-komunikasi-dasar/course.yaml"))
sources = {k: open(f"courses/terakidz-komunikasi-dasar/{v}").read() for k, v in d["sources"].items()}
c = json.dumps(d, sort_keys=True, separators=(",", ":"))
print(json.dumps([d, hashlib.sha256(c.encode()).hexdigest(), sources]))
PY
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.course import_course "$(cat /tmp/terakidz-course.json)"
```

🔴 `import_course` is **founder/administrator only**. It refuses a sha mismatch, a source that is
not `.md` (a `.py` above all), a non-increasing version, an unknown renderer, a source outside the
closed Markdown vocabulary, a placeholder nothing supplies, a quiz whose answer is not one of its
own options, and any font/image/footage that is not a usable asset.

**Verify (read-only):**

```bash
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.course search_read \
  '[["active","=",true]]' \
  -k '{"fields":["code","version","title","level","locale","renderer","brand_kits","source_sha256","sources_sha256"]}'
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.course.lesson search_read \
  '[[]]' -k '{"fields":["course_id","module_id","lesson_id","title","video_asset"],"order":"sequence"}'
# expect: terakidz-komunikasi-dasar v1, html, kits [terakidz, terakona], 4 lessons in 3 modules
```

**Rollback:** a course is archived by importing a newer version, or by an administrator clearing
`active` — it is never edited.

---

## M5 — One work order, end to end (M)

Factory → Work Orders → New:

1. line **`course`**, brand kit **`terakidz`**, a goal, and the inputs:
   `{"course": "terakidz-komunikasi-dasar", "options": {"locale": "id_ID", "publish_target": "digital"}}`.
2. **Start** it (it takes the line's WIP slot — limit 1 by default).
3. **Materialise Course**: the render is created and rendered **here** — every lesson's slides, its
   reading and its quiz, plus the course pack. This is the step that refuses an unavailable
   renderer (`typst not installed`), a source outside the Markdown vocabulary, an unusable asset
   (**the video's footage asset included**), a missing required variable, a malformed quiz and an
   option outside the closed set.
4. **Submit for review**: binds `factory.course.render,<id>` + the render's `input_sha256` and runs
   the automatic checks (licence, links, brand rules, AI disclosure, affiliate disclosure, PII,
   plus `course_assets`, `course_variables` and `course_quiz`). A failure returns the order to
   in-progress unbound.
5. **Expert, then founder**: the named expert first (never the render's author or the submitter),
   then a founder. Two different people, neither an agent.
6. **Release**: the predicate re-verifies the course, the kit, every licence, the quiz shape, every
   artefact's BYTES and the pack's, re-runs the checks, then **PUBLISHES** through the render's
   target and records the kernel's `action_done`. 🔴 An absent target refuses the whole release by
   name and nothing is marked released — the artefacts and the pack already exist; the course
   simply does not ship.
7. The second kit: repeat with the `terakona` kit (a distinct render, distinct token values).

**Verify (read-only):**

```bash
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.course.render search_read '[[]]' \
  -k '{"fields":["course_id","brand_kit_id","state","renderer","options","page_count","byte_count","input_sha256","pack_sha256","published_target_ref"],"order":"id desc","limit":5}'
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.course.artefact search_read \
  '[[]]' -k '{"fields":["lesson_id","kind","format","filename","byte_count","sha256"],"order":"id desc","limit":20}'
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.work.order search_read \
  '[["line_id.code","=","course"]]' -k '{"fields":["work_order_id","state","bound_ref","published_ref"],"order":"id desc","limit":5}'
# expect: a released render with 10 artefacts (4 slides + 4 readings + 2 quizzes), its pack, and
# its order done with published_ref == bound_ref
```

### 🔴 What "published" means, per arm

```bash
# digital (the default): the pack becomes the edition's render
kctl-odoo -p kodemeio-kod-odoo-erp shell call digital.product search_read \
  '[["code","=","terakidz-komunikasi-dasar"]]' -k '{"fields":["name","status","language"]}'
kctl-odoo -p kodemeio-kod-odoo-erp shell call digital.render search_read \
  '[["renderer_code","=","factory_course"]]' \
  -k '{"fields":["edition_id","format","state","checksum","attachment_id","size"]}'
# expect: one product (status NOT published -- publishing a digital product is a Digital Manager
# act in that framework), one edition, one done render whose attachment IS the pack and whose
# checksum IS the render's pack_sha256
```

Two boundaries the module states and this runbook repeats, so R6 does not trip on them:

- **The factory does not publish the digital product.** `digital.product.status` stays where the
  digital framework left it; flipping it is the Digital Manager's act.
- **The pack carries TEXT.** Every artefact's bytes live in their own `ir.attachment` on the
  render; the pack carries each artefact's text plus its sha256, and says so in its own
  `release.note`. Bundling the bytes for delivery is R6's job, and R6 must re-read each
  `sha256`/`pack_sha256` before shipping what the gate verified.

---

## M6 — Smoke test on the running instance (M)

The local acceptance suite (A1–A7, 147 tests) is the evidence for the rules; this is the
instance-level smoke test:

```bash
# 1. the course is imported and active
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.course search_read '[["active","=",true]]' \
  -k '{"fields":["code","version","renderer"]}'

# 2. the assets are verified (the footage one included, or the course refuses by name)
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.asset search_read \
  '[["key","in",["font-nunito","font-inter","image-terakidz-learning-hero","footage-terakidz-intro"]]]' \
  -k '{"fields":["key","kind","state","usable"]}'

# 3. one released render, with its artefacts and its pack
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.course.render search_read \
  '[["state","=","released"]]' \
  -k '{"fields":["course_id","brand_kit_id","published_target_ref","pack_sha256"],"limit":10}'

# 4. nothing is released without an order: this MUST be empty
kctl-odoo -p kodemeio-kod-odoo-erp shell call factory.course.render search_read \
  '[["state","=","released"],["input_sha256","=",false]]' -k '{"fields":["id"]}'

# 5. the absent arm's reason, read from the instance: eLearning and the module it depends on
kctl-odoo -p kodemeio-kod-odoo-erp shell call ir.module.module search_read \
  '[["name","in",["website","website_slides","digital_base"]]]' \
  -k '{"fields":["name","state"],"order":"name"}'
# expect: digital_base installed; website and website_slides uninstalled (or absent from the list)
```

The registry probe itself is not reachable over RPC (`_fcp_registry` is a private method) — the
module reports the two arms through the release's own refusal, and the local suite asserts both
(`tests/test_publish.py`).

⚠️ Expected refusals (each names the reason, and each is a feature): submitting after a licence was
revoked (the footage one included); releasing after the kit was superseded; releasing through
another work order; releasing in another company; importing a `.py` source; importing a course
whose quiz answer is not one of its options; releasing with `publish_target = slides` on a database
without `slide.channel`.

---

## M7 — Rollback

| What | Command / action | What it does / does not do |
|---|---|---|
| One released course | none — a released render is immutable and is evidence (its row, its artefacts and its pack) | Supersede it with a new render (materialise a new variant) and release that |
| A course that is wrong | import a newer version (M4) or, as an administrator, clear `active` | Nothing already released changes; a new render under the archived course refuses |
| An asset's licence withdrawn | Assets → **Revoke** with a reason | Every later submit/release refuses by name; released artefacts stay |
| The published digital product | unpublish it in the digital framework (its own manager act) | The factory never flips it either way; the released render keeps its `published_target_ref` |
| The whole line | `./odoo.sh addon kod prod uninstall factory_course --yes` | Only while nothing must be kept: it drops the courses, the renders, the artefacts and the pack. `factory_base` (and the ledger) stays |
| One image tag back | set `IMAGE_TAG` to the previous tag, then `compose redeploy <id>` | The previous image serves again |
| Switching the publish arm | change `options.publish_target` on the NEXT render (a data change in the work order's inputs) | A released render is not edited; the new variant publishes through the other arm, and the switch is inside `input_sha256`, so it is approved explicitly |

---

## Not in this runbook (deliberate)

- **No learner data.** No enrolment, no progress, no accounts: nothing in this slice stores a
  learner, and the released artefacts carry no personal data (the `pii` check runs on every
  submission). Enrolment is a later decision, and it is the reason the eLearning arm is a founder
  decision rather than a default.
- **No Marp.** The slides are Markdown and the renderer builds the deck; a Marp renderer would be
  one more entry behind the same registry, and it would bring a Node toolchain into the image.
- **No video production** (F6): a lesson's `video` is a licensed `footage` reference today.
- **No delivery**: entitlements, product pages, prices and R2 upload are R6/DIG1. This slice stops
  at a released render plus the digital product's edition render.
- **No MCP tools for courses** — the `mcp_base` conflict is unresolved (spec section 7).
- **Factory roles are not in `install/roles-erp.yaml`**, so M2 assigns groups by hand.
- **The shipped course is an example.** Real curriculum content is the factory's job later, and
  `terakona` is a `draft` kit that is itself a test fixture.

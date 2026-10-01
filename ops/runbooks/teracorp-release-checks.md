# Teracorp release checks

The 2026-10-01 production-readiness implementation restores `validate.yml` and
`secret-scan.yml` locally after commit `70f2cb5` disabled every workflow. That
commit records no reason beyond disabling Actions. The restored validation
retains the existing QR tests and adds `ops/scripts/tests`. Deployment and
scheduled infrastructure workflows remain disabled; this change does not deploy.

Before a release, establish the checks on the actual pushed revision:

1. Verify Actions is enabled and review the workflow diff. `Validate` must run
   its repository, offline manifests and Terraform jobs; secret scanning must
   run too. Local pytest does not establish remote workflow success.
2. Configure the existing `DSH_READ_DEPLOY_KEY` read-only sibling checkout secret
   through the normal credential process. Never put its value in this runbook.
   The broker contract test intentionally fails in CI if that checkout is missing.
3. Confirm the exact remote check names, then require the selected validation
   and secret-scan checks in branch protection/rulesets. Inspect effective
   protection; a YAML file does not prove enforcement.
4. Record SHA, run links, results, and any skipped cross-repository checks in the
   release packet. Pin compatible sibling inputs for reproducible release tests.
5. Run affected product and operations acceptance from the implementation plan;
   these workflows do not prove live account readiness, restore, monitoring,
   payment recovery or deployed configuration.

Local equivalents for the Python portion:

```bash
uv sync --locked
uv run pytest deploys/tests ops/scripts/tests -q
uv run ruff check deploys ops/scripts
uv run ruff format --check deploys ops/scripts
uv run python deploys/generate.py --dry-run
```

Use the workflow's separate QR, manifest and Terraform commands for those gates.
Do not mark them passed merely because the Python portion passes. Restoring an
earlier workflow is reversible, but disabling its checks again requires the
release evidence to identify and enforce a replacement before a launch.

The secret scan uses read-only permissions and disables PR comments; findings
remain in the job result/summary. This matches the action's
[documented comment control](https://github.com/gitleaks/gitleaks-action/tree/v2#environment-variables)
without giving the release check a PR-write token.

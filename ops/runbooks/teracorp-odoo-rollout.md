# Teracorp MCP governance rollout on an Odoo instance

Installs or upgrades the Stage B governance addons on a real Odoo instance:
`mcp_base` (≥ 18.0.11.1.0, read/write resource split), `mcp_operation_binding`
(policy/environment binding and the global kill switch) and
`sale_integration_ref`.

- Design: kodemeio-docs/superpowers/specs/2026-09-25-teracorp-stage-bc-design.md
- Results: kodemeio-docs/superpowers/specs/2026-09-25-teracorp-stage-c-results.md
- Code and module docs: kodemeio-odoo `src/private/services/mcp_operation_binding/CLAUDE.md`,
  `src/private/services/mcp_base/CLAUDE.md`
- Commands below run from the kodemeio-odoo checkout through `./odoo.sh` (never raw SQL:
  `ir.config_parameter` is cached per worker and only `set_param` invalidates it).

## Before you install: this addon fails closed

`mcp_operation_binding` refuses **every** MCP prepare until `mcp_base.environment` is set to
`local`, `staging` or `production` (any other value is refused too). Nothing else on the
instance changes, but every agent or integration that writes through MCP stops at once.
The install log carries a WARNING saying so when the parameter is unset.

Operations prepared **before** the install carry no binding stamp and are refused at execute
("prepared before binding was enabled"). The caller must prepare them again. The exposure lasts
up to the approval TTL of the profile that prepared them (`approval_ttl_minutes`, at most
1440 min), after which they would have expired anyway.

## Install (or first upgrade to these versions)

1. Pick the environment label for the target: `staging` or `production` (`local` only on a
   developer stack).
2. Set it **before** the install, either way:
   - container env `KODEMEIO_ENVIRONMENT=<label>` in the instance's env (the post-init hook seeds
     the parameter from it when the database has none), or
   - `./odoo.sh <tenant> <target> shell call ir.config_parameter set_param '["mcp_base.environment", "<label>"]'`
3. Drain writers you control (pause the order-intake worker; ask agents to stop preparing).
4. Deploy through the normal path: `bin/deploy-preflight <tenant> <target>` must say `CLEAR`;
   staging before production.
5. Install `mcp_operation_binding` and `sale_integration_ref`; upgrade `mcp_base`
   (`-u mcp_base`). The binding module seeds `mcp_base.policy_version=1` and
   `mcp_base.execution_enabled=1` (`noupdate`, never overwritten later).
6. Verify: `./odoo.sh <tenant> <target> shell call ir.config_parameter get_param '["mcp_base.environment"]'`
   returns the label; one read-only MCP call (`odoo_whoami`) works; one prepare on a test resource
   returns an operation instead of "mcp_base.environment is not set".
7. Split read from write on every write-capable profile: set **Write Resources**
   (`allowed_write_resources`) to the resources the profile may write. Empty keeps the old
   behavior (everything in Allowed Resources writable). For KIDO the values come from
   `contracts/agents/kido.yaml` (read: products, sale-orders, partners; write: sale-orders).
8. Resume writers.

## Upgrade `mcp_base` later

A plain `-u mcp_base` through the same deploy path. Check the module's `CLAUDE.md` for new
fields first; a new profile field is empty on upgrade and keeps the previous behavior.
Bumping `mcp_base.policy_version` (step "Policy change" below) is a separate, deliberate act.

## Kill switch

Stops **all** MCP executes on the instance, including replays of already-succeeded operations.
Prepares and reads keep working.

```
./odoo.sh <tenant> <target> shell call ir.config_parameter set_param '["mcp_base.execution_enabled", "0"]'
./odoo.sh <tenant> <target> shell call ir.config_parameter set_param '["mcp_base.execution_enabled", "1"]'   # back on
```

Narrower switches: deactivate the agent's key (`mcp.key`) or its profile (`mcp.tool.profile`) in
Settings → Technical → MCP; both are enforced at HTTP authentication.

## Policy change

Set `mcp_base.policy_version` to a new value. Every approved-but-unexecuted operation prepared
under the old value is refused at execute and must be prepared (and approved) again.

## Rollback

1. Flip the kill switch off (`execution_enabled=0`) if anything is misbehaving.
2. Uninstall `mcp_operation_binding` to remove the binding and the kill switch entirely;
   `mcp_base` keeps working without it. Operations stay readable. The audit log keeps every row
   (the append-only guard goes with the module).
3. `sale_integration_ref` can stay: it only adds a nullable, unique-per-company reference.
4. Downgrading `mcp_base` is not supported; clear `allowed_write_resources` on a profile instead
   to return it to the old read-equals-write behavior.

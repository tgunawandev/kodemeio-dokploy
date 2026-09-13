#!/usr/bin/env python3
"""Generate deploy instance YAMLs and env files from tenant manifests.

Usage:
    python generate.py                    # Generate all tenants
    python generate.py --tenant mac       # Generate single tenant
    python generate.py --dry-run          # Preview without writing
    python generate.py --diff             # Show diff vs existing files
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

import yaml

DEPLOY_DIR = Path(__file__).parent
TENANTS_DIR = DEPLOY_DIR / "tenants"
INSTANCES_DIR = DEPLOY_DIR / "instances"
ENV_DIR = DEPLOY_DIR / "env"
DOMAINS_DIR = DEPLOY_DIR / "domains"
_MOVED_STAGES = frozenset({"dual", "redirect", "permanent", "cleaned"})

HEADER = "# GENERATED FROM tenants/{code}.yaml — DO NOT EDIT\n"

# ---------------------------------------------------------------------------
# Recipe table — maps high-level recipe names (used in tenants/*.yaml under
# `odoo:` entries as `recipe: <name>`) to (profile, extras) pairs that
# materialize as ODOO_DEPLOY_PROFILE + ODOO_INSTALL_BUNDLE in generated env files.
#
# Single source of truth — must stay in sync with:
#   kodemeio-odoo/install/README.md
#   kodemeio-odoo/.env.example
#   kodemeio-odoo/scripts/verify-profile-migration.sh
# ---------------------------------------------------------------------------
ODOO_RECIPES: dict[str, tuple[str, str]] = {
    # recipe_name: (profile, extras_csv_for_ODOO_INSTALL_BUNDLE)
    "erp": ("erp", ""),
    "hrms": ("hrms", ""),
    # Standalone service desk — no ERP underneath. A deployment SHAPE,
    # like hrms, not a vertical composed on top of erp.
    "helpdesk": ("helpdesk", ""),
    "import": ("erp", ""),
    "distribution": (
        "erp",
        "private-field-ops,private-desktop-apps:dms,private-retail:shop,private-analytics",
    ),
    "manufacturing": (
        "erp",
        "private-mrp,private-sfa,private-desktop-apps:tpm,private-analytics",
    ),
    "retail": ("erp", "oca-pos,private-retail"),
    "trading": ("erp", "private-analytics"),
    "import-hrms": ("erp", "private-hr-full"),
    "distribution-hrms": (
        "erp",
        "private-field-ops,private-desktop-apps:dms,private-retail:shop,private-analytics,private-hr-full",
    ),
    "manufacturing-hrms": (
        "erp",
        "private-mrp,private-sfa,private-desktop-apps:tpm,private-analytics,private-hr-full",
    ),
    "retail-hrms": ("erp", "oca-pos,private-retail,private-hr-full"),
    "full": (
        "erp",
        "core-hr,core-maintenance,core-fleet,oca-report,oca-hr,oca-maintenance,oca-pos,oca-saas,private-finance-hr,private-form,private-sfa,private-lfa,private-mrp,private-hrm,private-desktop-apps,private-retail,private-analytics,private-asset,private-recruitment,private-saas",
    ),
}

# Per-edition container resource limits. business == docker-compose.prod.yml
# defaults; superuser is raised (coding agents run Node/builds and OOM at 2G).
HERMES_EDITION_RESOURCES: dict[str, dict[str, str]] = {
    "superuser": {
        "HERMES_CPU_LIMIT": "4.0",
        "HERMES_MEM_LIMIT": "6G",
        "HERMES_MEM_RESERVATION": "1G",
    },
    "business": {
        "HERMES_CPU_LIMIT": "2.0",
        "HERMES_MEM_LIMIT": "2G",
        "HERMES_MEM_RESERVATION": "512M",
    },
}

# Upstream image tag emitted when a tenant does not pin `hermes.upstream_ref`.
# Deliberately the value the existing tenants already run — bumping this moves
# EVERY tenant at once, which is never what a per-agent version bump wants.
# Pin per tenant in tenants/<code>.yaml instead (see gen_hermes).
HERMES_DEFAULT_UPSTREAM_REF = "v2026.5.16"


def resolve_recipe(recipe_or_profile: str) -> tuple[str, str]:
    """Resolve a tenant `recipe:` (preferred) or legacy `profile:` value to
    (deploy_profile, install_bundle_extras).

    Accepts both new recipe names (`distribution`, `manufacturing`, …) and
    legacy profile names (same strings, since recipes are named after the
    profiles they replace) so old tenant YAMLs keep working during rollout.
    """
    if recipe_or_profile not in ODOO_RECIPES:
        raise ValueError(f"Unknown odoo recipe {recipe_or_profile!r}. Valid: {sorted(ODOO_RECIPES.keys())}")
    return ODOO_RECIPES[recipe_or_profile]


# ---------------------------------------------------------------------------
# Naming convention: {tenant}-{stack}-{app}
#
#   Filename:      {tenant}-{stack}-{app}.yaml
#   instance.name: {tenant}-{stack}-{app}
#   DNS name:      {tenant}-{app}{dns_suffix}.{domain}
#   Database:      {tenant}_{stack}_{app}{db_suffix}
#   Env file:      .env.{tenant}-{stack}-{app}
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# YAML helpers
# ---------------------------------------------------------------------------


def yaml_dump(data: dict) -> str:
    return yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)


def load_tenant(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def load_domain_moves(directory: Path = DOMAINS_DIR) -> dict[tuple[str, str], dict]:
    """(instance, environment) -> move, from the `kctl-dokploy domains move` files."""
    moves: dict[tuple[str, str], dict] = {}
    if not directory.is_dir():
        return moves
    for path in sorted(directory.glob("*.yaml")):
        for move in (yaml.safe_load(path.read_text()) or {}).get("moves") or []:
            moves[(move["instance"], move.get("environment", "production"))] = move
    return moves


DOMAIN_MOVES = load_domain_moves()


def moved_hosts(instance: str, env_name: str, moves: dict | None = None) -> tuple[str | None, str | None]:
    """(served host, legacy host) for an instance whose move is live, else (None, None).

    A move becomes live at `dual`; the legacy host is kept as DOMAIN_LEGACY
    until `cleaned`, and never for a `redirect: false` hand-over.
    """
    move = (DOMAIN_MOVES if moves is None else moves).get((instance, env_name))
    if not move or move.get("stage", "planned") not in _MOVED_STAGES:
        return None, None
    legacy = None if move["stage"] == "cleaned" or move.get("redirect", True) is False else move["from"][0]
    return move["to"], legacy


def odoo_public_host(tenant: dict, odoo_entry: dict, env_name: str, dns_suffix: str) -> str:
    """Hostname an Odoo entry is served on: a live move, else `host:` (production only), else the convention."""
    code, short, domain = tenant["code"], odoo_entry["short"], tenant["domain"]
    moved, _ = moved_hosts(f"{code}-odoo-{short}{dns_suffix}", env_name)
    if moved:
        return moved
    # `host:` is production-only on purpose: a staging twin of a bare subdomain
    # would render the SAME host and collide on the Traefik router.
    if odoo_entry.get("host") and not dns_suffix:
        return odoo_entry["host"]
    return f"{code}-odoo-{short}{dns_suffix}.{domain}"


def odoo_legacy_host(tenant: dict, odoo_entry: dict, env_name: str, dns_suffix: str) -> str | None:
    return moved_hosts(f"{tenant['code']}-odoo-{odoo_entry['short']}{dns_suffix}", env_name)[1]


def react_public_host(tenant: dict, app: str, env_name: str, dns_suffix: str) -> str:
    moved, _ = moved_hosts(f"{tenant['code']}-react-{app}{dns_suffix}", env_name)
    return moved or f"{tenant['code']}-{app}{dns_suffix}.{tenant['domain']}"


# ---------------------------------------------------------------------------
# Instance generators
# ---------------------------------------------------------------------------


def gen_react_pwa(
    tenant: dict,
    odoo_entry: dict,
    app: str,
    env_name: str = "production",
    server: str = "",
    dns_suffix: str = "",
    db_suffix: str = "",
) -> tuple[str, str, str, str | None]:
    """Generate react-pwa instance YAML + env content.

    Returns (yaml_filename, yaml_content, env_filename, env_content).
    """
    code = tenant["code"]
    display = tenant.get("short_name", tenant["name"])
    domain = tenant["domain"]
    app_upper = app.upper().replace("-", "_")

    # The unified `erp` PWA (@kodemeio/erp) calls /api/v1/base/modules and
    # /api/v1/{module}/... directly at the Odoo root, so its base URL is the
    # host without a path prefix. Per-app legacy PWAs embed /{app}/api.
    odoo_host = odoo_public_host(tenant, odoo_entry, env_name, dns_suffix)
    host = react_public_host(tenant, app, env_name, dns_suffix)
    api_base_url = f"https://{odoo_host}" if app == "erp" else f"https://{odoo_host}/{app}/api"

    yaml_filename = f"{code}-react-{app}.yaml"
    env_filename = f".env.{code}-react-{app}"

    instance: dict = {
        "kind": "instance",
        "extends": "../../bases/react-pwa.yaml",
        "instance": {
            "name": f"{code}-react-{app}",
            "description": f"{display} — {app_upper} PWA",
        },
        "project": code,
        "environment": env_name,
    }
    if server:
        instance["server"] = server
    instance.update(
        {
            "source_overrides": {
                "compose_path": f"compose/docker-compose.{app}.yml",
            },
            "dns": {
                "zone": domain,
                "name": host.removesuffix(f".{domain}"),
            },
            "domain": {
                "host": host,
                "port": 80,
                "service": app,
                "https": True,
            },
            "env_file": f"../../env/{env_name}/{env_filename}",
            "env_overrides": {
                f"VITE_{app_upper}_APP_NAME": f"{display} {app_upper}",
                f"VITE_{app_upper}_API_BASE_URL": api_base_url,
            },
        }
    )

    slug = f"{code}-react-{app}"
    env_content = (
        f"VITE_{app_upper}_APP_NAME={display} {app_upper}\n"
        f"VITE_{app_upper}_API_BASE_URL={api_base_url}\n"
        f"VITE_AUTH_MODE=oidc\n"
        f"VITE_OIDC_AUTHORITY=https://auth.kodeme.io/application/o/{slug}/\n"
        f"VITE_{app_upper}_OIDC_CLIENT_ID=\n"
        f"VITE_{app_upper}_OIDC_REDIRECT_URI=https://{host}/auth/callback\n"
        f"VITE_{app_upper}_THEME={app}\n"
    )

    return yaml_filename, yaml_dump(instance), env_filename, env_content


def gen_odoo(
    tenant: dict,
    odoo_entry: dict,
    env_name: str = "production",
    server: str = "",
    dns_suffix: str = "",
    db_suffix: str = "",
) -> tuple[str, str, str, str]:
    """Generate odoo instance YAML + env.example content.

    Returns (yaml_filename, yaml_content, env_example_filename, env_example_content).
    """
    code = tenant["code"]
    display = tenant.get("short_name", tenant["name"])
    domain = tenant["domain"]
    # Accept new `recipe:` key (preferred) or legacy `profile:` key.
    # Both resolve through ODOO_RECIPES, so legacy tenant YAMLs keep working.
    recipe_name = odoo_entry.get("recipe") or odoo_entry["profile"]
    deploy_profile, install_extras = resolve_recipe(recipe_name)
    short = odoo_entry["short"]
    description = odoo_entry.get("description", recipe_name)
    workers = odoo_entry.get("workers", 4)
    db_name = f"{code}_odoo_{short}{db_suffix}"

    # Public name per environment (see odoo_public_host); DOMAIN_LEGACY keeps the
    # old name served beside it while a domain move is in progress.
    host = odoo_public_host(tenant, odoo_entry, env_name, dns_suffix)
    dns_name = host.removesuffix(f".{domain}")
    legacy_host = odoo_legacy_host(tenant, odoo_entry, env_name, dns_suffix)

    yaml_filename = f"{code}-odoo-{short}.yaml"
    env_example_filename = f".env.{code}-odoo-{short}.example"

    instance: dict = {
        "kind": "instance",
        "extends": "../../bases/odoo.yaml",
        "instance": {
            "name": f"{code}-odoo-{short}{dns_suffix}",
            "description": f"{display} — {description}",
        },
        "project": code,
        "environment": env_name,
    }
    if server:
        instance["server"] = server
    instance.update(
        {
            "dns": {
                "zone": domain,
                "name": dns_name,
            },
            "domain": {
                "host": host,
                "port": 8069,
                "service": "odoo-web",
                "https": True,
                "cert": "letsencrypt",
            },
            "database": {
                "name": db_name,
                "user": "odoo",
            },
            "env_file": f"../../env/{env_name}/.env.{code}-odoo-{short}",
            "env_overrides": {
                "COMPOSE_PROJECT_NAME": f"{code}-odoo-{short}",
                "TENANT": code,
                "PGDATABASE": db_name,
                "PGUSER": "odoo",
                "ODOO_DB_FILTER": f"^{db_name}$",
                "DOMAIN": host,
                **({"DOMAIN_LEGACY": legacy_host} if legacy_host else {}),
                "ODOO_WORKERS": str(workers),
                # 🔴 Staging runs ZERO cron threads.
                #
                # A staging instance is a restored production dump. backups pull
                # neutralises it (ir_cron.active=false, mail off, uuid scrambled)
                # and then the redeploy runs `-u`, which reloads each module's
                # ir.cron XML and sets active back to True. Measured on
                # mac-odoo-erp-stg 2026-08-15: 13 crons active again right after
                # a clean neutralisation, all module-provided, nextcall stamped
                # at the minute the module update ran -- including writers (SVL
                # /GL invariant monitor, bank-reconciliation drift, fleet
                # document-number reconciliation).
                #
                # Disabling the RECORDS cannot win that race. Removing the
                # THREADS does: the odoo-cron container starts and stays idle, so
                # an active ir_cron row is inert. Production is untouched.
                **({"ODOO_MAX_CRON_THREADS": "0"} if env_name == "staging" else {}),
            },
            "post_deploy": {
                "odoo_profile": f"profile-{deploy_profile}",
                "odoo_install_bundle": install_extras,
                "recipe": recipe_name,
            },
        }
    )

    env_example = (
        f"# =============================================================================\n"
        f"# {display} {description} — {env_name.title()} Environment\n"
        f"# =============================================================================\n"
        f"# Instance: {host}\n"
        f"# Recipe:   {recipe_name}\n"
        f"# Profile:  {deploy_profile}\n"
        f"# Extras:   {install_extras or '(none)'}\n"
        f"# =============================================================================\n"
        f"\n"
        f"# PROJECT IDENTIFICATION\n"
        f"COMPOSE_PROJECT_NAME={code}-odoo-{short}\n"
        f"TENANT={code}\n"
        f"DOMAIN={host}\n" + (f"DOMAIN_LEGACY={legacy_host}\n" if legacy_host else "") + f"\n"
        f"# DATABASE\n"
        f"PGHOST=10.0.0.3\n"
        f"PGPORT=5432\n"
        f"PGUSER=odoo\n"
        f"PGPASSWORD=CHANGE_ME\n"
        f"PGDATABASE={db_name}\n"
        f"ODOO_DB_MAXCONN=32\n"
        f"\n"
        f"# ODOO CORE\n"
        f"ODOO_ADMIN_PASSWD=CHANGE_ME\n"
        f"ODOO_DB_FILTER=^{db_name}$\n"
        f"ODOO_DB_NAME={db_name}\n"
        f"ODOO_LIST_DB=False\n"
        f"ODOO_INIT_DB=true\n"
        f"RUNNING_ENV={env_name}\n"
        f"WITHOUT_DEMO=True\n"
        f"ODOO_HTTP_PORT=8069\n"
        f"ODOO_GEVENT_PORT=8072\n"
        f"ODOO_DATA_DIR=/var/lib/odoo\n"
        f"ODOO_LOG_LEVEL=warn\n"
        f"ODOO_SERVER_WIDE_MODULES=base,web,bus,bus_alt_connection,session_db,dbfilter_from_header\n"
        f"\n"
        f"# DEPLOY PROFILE\n"
        f"ODOO_DEPLOY_PROFILE={deploy_profile}\n"
        + (f"ODOO_INSTALL_BUNDLE={install_extras}\n" if install_extras else "")
        + f"ODOO_RUN_UPDATE=false\n"
        f"\n"
        f"# WORKERS\n"
        f"ODOO_WORKERS={workers}\n"
        f"ODOO_MAX_CRON_THREADS=2\n"
        f"ODOO_QUEUE_JOB_CHANNELS=root:2\n"
        f"\n"
        f"# RESOURCES\n"
        f"CPU_LIMIT=2\n"
        f"MEMORY_LIMIT=4G\n"
        f"CPU_LIMIT_CRON=1\n"
        f"MEMORY_LIMIT_CRON=1G\n"
        f"ODOO_LIMIT_MEMORY_SOFT=2147483648\n"
        f"ODOO_LIMIT_MEMORY_HARD=4294967296\n"
        f"ODOO_LIMIT_TIME_CPU=600\n"
        f"ODOO_LIMIT_TIME_REAL=1200\n"
        f"ODOO_LIMIT_TIME_REAL_CRON=1800\n"
        f"\n"
        f"# SMTP\n"
        f"SMTP_SERVER=mail.kodeme.io\n"
        f"SMTP_PORT=587\n"
        f"SMTP_USER={code}@kodeme.io\n"
        f"SMTP_PASSWORD=CHANGE_ME\n"
        f"SMTP_SSL=True\n"
        f"EMAIL_FROM={code}@kodeme.io\n"
        f"\n"
        f"# STORAGE\n"
        f"IR_ATTACHMENT_LOCATION=file\n"
        f"TZ=Asia/Jakarta\n"
        f"IMAGE_TAG=latest\n"
    )

    return yaml_filename, yaml_dump(instance), env_example_filename, env_example


def gen_nextjs_corporate(
    tenant: dict,
    env_name: str = "production",
    server: str = "",
    dns_suffix: str = "",
) -> tuple[str, str, str, str]:
    """Generate Next.js corporate website instance YAML + env."""
    code = tenant["code"]
    name = tenant["name"]
    domain = tenant["domain"]
    compose_brand = tenant["_web_corporate_brand"]

    yaml_filename = f"{code}-nextjs-web.yaml"
    env_filename = f".env.{code}-nextjs-web"

    # Production uses "@" (apex), staging uses suffixed subdomain
    if dns_suffix:
        dns_name = f"{code}-web{dns_suffix}"
        host = f"{code}-web{dns_suffix}.{domain}"
    else:
        dns_name = "@"
        host = domain

    instance: dict = {
        "kind": "instance",
        "extends": "../../bases/nextjs.yaml",
        "instance": {
            "name": f"{code}-nextjs-web",
            "description": f"{name} company website",
        },
        "source_overrides": {
            "compose_path": f"compose/docker-compose.{compose_brand}.yml",
        },
        "project": code,
        "environment": env_name,
    }
    if server:
        instance["server"] = server
    instance.update(
        {
            "dns": {
                "zone": domain,
                "name": dns_name,
            },
            "domain": {
                "host": host,
                "port": 3000,
                "service": f"{compose_brand}-web",
                "https": True,
                "cert": "letsencrypt",
            },
            "env_file": f"../../env/{env_name}/{env_filename}",
        }
    )

    env_content = f"NODE_ENV=production\nNEXT_PUBLIC_SITE_URL=https://{host}\nTZ=Asia/Jakarta\n"

    return yaml_filename, yaml_dump(instance), env_filename, env_content


def gen_nextjs_careers(
    tenant: dict,
    env_name: str = "production",
    server: str = "",
    dns_suffix: str = "",
) -> tuple[str, str, str, str]:
    """Generate Next.js careers portal instance YAML + env."""
    code = tenant["code"]
    name = tenant["name"]
    domain = tenant["domain"]
    dns_name = f"{code}-careers{dns_suffix}"
    host = f"{code}-careers{dns_suffix}.{domain}"
    hrms_host = odoo_public_host(tenant, {"short": "hrms"}, env_name, dns_suffix)

    yaml_filename = f"{code}-nextjs-careers.yaml"
    env_filename = f".env.{code}-nextjs-careers"

    instance: dict = {
        "kind": "instance",
        "extends": "../../bases/nextjs.yaml",
        "instance": {
            "name": f"{code}-nextjs-careers",
            "description": f"{name} — Careers Portal (recruitment)",
        },
        "source_overrides": {
            "compose_path": "compose/docker-compose.prod.careers.yml",
        },
        "project": code,
        "environment": env_name,
    }
    if server:
        instance["server"] = server
    instance.update(
        {
            "healthcheck": {
                "port": 3000,
            },
            "dns": {
                "zone": domain,
                "name": dns_name,
            },
            "domain": {
                "host": host,
                "port": 3000,
                "service": "careers",
                "https": True,
            },
            "env_overrides": {
                "NEXT_PUBLIC_SITE_URL": f"https://{host}",
                "NEXT_PUBLIC_SITE_NAME": name,
                "NEXT_PUBLIC_COMPANY_WEBSITE": f"https://{domain}",
                "NEXT_PUBLIC_API_URL": f"https://{hrms_host}",
                "NEXT_PUBLIC_RECRUITMENT_API_URL": f"https://{hrms_host}/recruitment/api",
                "API_URL": f"https://{hrms_host}",
            },
        }
    )

    env_content = (
        f"NODE_ENV=production\n"
        f"NEXT_PUBLIC_SITE_URL=https://{host}\n"
        f"NEXT_PUBLIC_SITE_NAME={name}\n"
        f"NEXT_PUBLIC_COMPANY_WEBSITE=https://{domain}\n"
        f"NEXT_PUBLIC_API_URL=https://{hrms_host}\n"
        f"NEXT_PUBLIC_RECRUITMENT_API_URL=https://{hrms_host}/recruitment/api\n"
        f"API_URL=https://{hrms_host}\n"
        f"TZ=Asia/Jakarta\n"
    )

    return yaml_filename, yaml_dump(instance), env_filename, env_content


def _read_env_var(env_path: Path, key: str) -> str | None:
    """Read a KEY=value line from an env file. Returns None if file or key missing."""
    if not env_path.exists():
        return None
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == key:
            return v.strip()
    return None


def gen_accurate_sync(
    tenant: dict,
    accurate_cfg: dict,
    odoo_entry: dict,
    env_name: str = "production",
    server: str = "",
    dns_suffix: str = "",
    db_suffix: str = "",
) -> tuple[str, str, str, str]:
    """Generate accurate-sync instance YAML + env content.

    Returns (yaml_filename, yaml_content, env_filename, env_content).

    Secrets (PGPASSWORD, ODOO_ADMIN_PASSWD) are sourced from the sibling
    .env.{code}-odoo-{short} file when present — this keeps the accurate-sync
    env in lock-step with the Odoo instance it targets without duplicating
    the secret values in tenants/*.yaml. If the sibling file is missing or
    a key is absent, CHANGE_ME is emitted and the caller is expected to
    populate it via the Dokploy UI.
    """
    code = tenant["code"]
    display = tenant.get("short_name", tenant["name"])
    short = odoo_entry["short"]
    db_name = f"{code}_odoo_{short}{db_suffix}"
    odoo_host = odoo_public_host(tenant, odoo_entry, env_name, dns_suffix)
    accurate_tenants = ",".join(accurate_cfg.get("tenants", [code]))
    image_tag = accurate_cfg.get("image_tag", "latest")

    # TRIGGER_SECRET — preserve existing value, else generate fresh.
    # Never rotate automatically (would break the Odoo→sync HTTP handshake).
    import secrets as _secrets

    _target_env_path = ENV_DIR / env_name / f".env.{code}-accurate-sync"
    _existing_secret = _read_env_var(_target_env_path, "TRIGGER_SECRET") if _target_env_path.exists() else None
    trigger_secret = _existing_secret or _secrets.token_hex(32)

    # Bridge the secret into the sibling Odoo container's env so the
    # accurate_integration post-migration hook can seed
    # ir.config_parameter.accurate_sync.trigger_secret on install/upgrade.
    # Env var name differs from the sync side to avoid collision:
    #   sync container  → TRIGGER_SECRET
    #   Odoo container  → ACCURATE_TRIGGER_SECRET
    sibling_env_path = ENV_DIR / env_name / f".env.{code}-odoo-{short}"
    if sibling_env_path.exists():
        existing_text = sibling_env_path.read_text()
        if "ACCURATE_TRIGGER_SECRET=" in existing_text:
            # Replace the existing value in-place (preserves surrounding comments)
            import re as _re

            new_text = _re.sub(
                r"^ACCURATE_TRIGGER_SECRET=.*$",
                f"ACCURATE_TRIGGER_SECRET={trigger_secret}",
                existing_text,
                flags=_re.MULTILINE,
            )
        else:
            # Append at end
            new_text = (
                existing_text.rstrip()
                + "\n"
                + "\n"
                + "# ACCURATE_TRIGGER_SECRET — mirrors TRIGGER_SECRET in\n"
                + f"# .env.{code}-accurate-sync; read by accurate_integration\n"
                + "# post-migration to populate ir.config_parameter.\n"
                + f"ACCURATE_TRIGGER_SECRET={trigger_secret}\n"
            )
        sibling_env_path.write_text(new_text)

    # Pull secrets + PGHOST from the sibling Odoo env file so we don't duplicate
    # them in tenants/*.yaml. Fallback to CHANGE_ME / default PGHOST when missing.
    sibling_env = ENV_DIR / env_name / f".env.{code}-odoo-{short}"
    pghost = _read_env_var(sibling_env, "PGHOST") or "10.0.0.3"
    pgpassword = _read_env_var(sibling_env, "PGPASSWORD") or "CHANGE_ME"
    odoo_admin_passwd = _read_env_var(sibling_env, "ODOO_ADMIN_PASSWD") or "CHANGE_ME"

    yaml_filename = f"{code}-accurate-sync.yaml"
    env_filename = f".env.{code}-accurate-sync"

    instance: dict = {
        "kind": "instance",
        "extends": "../../bases/accurate-sync.yaml",
        "instance": {
            "name": f"{code}-accurate-sync",
            "description": f"{display} — Accurate Online sync worker",
        },
        "project": code,
        "environment": env_name,
    }
    if server:
        instance["server"] = server
    instance.update(
        {
            "env_file": f"../../env/{env_name}/.env.{code}-accurate-sync",
            "env_overrides": {
                # COMPOSE_PROJECT_NAME intentionally omitted — Dokploy manages
                # the project namespace via its auto-generated random `appName`
                # suffix. Injecting this variable caused the 2026-04-16 outage
                # where deploys finished "done" but no containers came up.
                "TENANT": code,
                "PGDATABASE": db_name,
                "PGUSER": "odoo",
                "ODOO_URL": f"https://{odoo_host}",
                "ODOO_DB_FILTER": f"^{db_name}$",
                "ACCURATE_TENANTS": accurate_tenants,
                "IMAGE_TAG": image_tag,
            },
        }
    )

    env_content = (
        f"# =============================================================================\n"
        f"# {display} Accurate-Sync — {env_name.title()} Environment\n"
        f"# =============================================================================\n"
        f"# GENERATED from tenants/{code}.yaml (accurate_sync block).\n"
        f"# Secrets (PGPASSWORD, ODOO_ADMIN_PASSWD) are copied from the sibling\n"
        f"# .env.{code}-odoo-{short} file at generate time. Re-run generate.py\n"
        f"# after rotating those secrets to keep this file in sync.\n"
        f"# =============================================================================\n"
        f"\n"
        f"TENANT={code}\n"
        f"\n"
        f"# DATABASE — mirrors .env.{code}-odoo-{short}\n"
        f"PGHOST={pghost}\n"
        f"PGPORT=5432\n"
        f"PGUSER=odoo\n"
        f"PGPASSWORD={pgpassword}\n"
        f"PGDATABASE={db_name}\n"
        f"\n"
        f"# ODOO — URL of the sibling Odoo instance, admin password for RPC calls\n"
        f"ODOO_URL=https://{odoo_host}\n"
        f"ODOO_DB_FILTER=^{db_name}$\n"
        f"ODOO_ADMIN_PASSWD={odoo_admin_passwd}\n"
        f"\n"
        f"# TENANT LIST — comma-separated slugs from accurate_company table\n"
        f"ACCURATE_TENANTS={accurate_tenants}\n"
        f"\n"
        f"# IMAGE\n"
        f"IMAGE_TAG={image_tag}\n"
        f"TZ=Asia/Jakarta\n"
        f"\n"
        f"# TRIGGER_SECRET — shared secret for the HTTP /sync endpoint.\n"
        f"# The Odoo addon's ir.config_parameter.accurate_sync.trigger_secret\n"
        f"# must hold the SAME value (seeded via post-migration from the\n"
        f"# sibling Odoo container's ACCURATE_TRIGGER_SECRET env; Task 9 of\n"
        f"# the rollout plan bridges the two values).\n"
        f"TRIGGER_SECRET={trigger_secret}\n"
    )

    return yaml_filename, yaml_dump(instance), env_filename, env_content


def gen_ofelia(
    tenant: dict,
    scheduler: dict,
    env_name: str = "production",
    server: str = "",
) -> tuple[str, str, str, str]:
    """Generate Ofelia scheduler instance YAML + env.example.

    Each scheduler entry produces an independent Ofelia instance that loads
    a specific INI schedule file from the kodemeio-ofelia repo.
    """
    code = tenant["code"]
    name = tenant["name"]
    sched_name = scheduler["name"]
    schedule_file = scheduler["schedule_file"]

    yaml_filename = f"{code}-ofelia-{sched_name}.yaml"
    env_example_filename = f".env.{code}-ofelia-{sched_name}.example"
    container_name = f"{code}-ofelia-{sched_name}"

    instance: dict = {
        "kind": "instance",
        "extends": "../../bases/ofelia.yaml",
        "instance": {
            "name": container_name,
            "description": f"{name} — Ofelia scheduler ({sched_name})",
        },
        "project": code,
        "environment": env_name,
    }
    if server:
        instance["server"] = server
    instance.update(
        {
            "env_file": f"../../env/{env_name}/.env.{code}-ofelia-{sched_name}",
            "env_overrides": {
                "SCHEDULE_FILE": schedule_file,
                "CONTAINER_NAME": container_name,
            },
        }
    )

    config_host = scheduler.get("config_host", "/opt/kodemeio/config.yaml")
    env_example = (
        f"# Ofelia scheduler ({sched_name}) for {name}\n"
        f"SCHEDULE_FILE={schedule_file}\n"
        f"CONTAINER_NAME={container_name}\n"
        f"KODEMEIO_CONFIG_HOST={config_host}\n"
        f"TZ=Asia/Jakarta\n"
        f"OFELIA_TAG=latest\n"
    )

    return yaml_filename, yaml_dump(instance), env_example_filename, env_example


def gen_filestore_backup(
    tenant: dict,
    entry: dict,
    env_name: str = "production",
    server: str = "",
    suffix: str = "",
) -> tuple[str, str, str, str]:
    """Generate a filestore backup instance YAML + env.example.

    One standalone restic stack per Odoo instance. It mounts that instance's
    filestore volume external + READ-ONLY and is pinned to the same server, so
    it never requires a change to or a redeploy of the Odoo compose.

    ``entry["volume"]`` must be the exact docker volume name on that host:
    Dokploy names compose volumes {appName}_{volume}, so decoy filestore
    volumes sit beside the live one.
    """
    code = tenant["code"]
    name = tenant["name"]
    short = entry["short"]
    # The suffix keeps staging in its OWN restic repository — sharing one with
    # production would let a staging run's retention prune production history.
    #
    # A tenant with MORE THAN ONE filestore backup in the same environment must
    # disambiguate the instance name, or both entries generate the same YAML and
    # env filename and the write loop in main() silently keeps only the last —
    # one repository would never exist while the deploy reported success. tpp and
    # tpp25 have a single production entry each and omit this, so their live
    # compose and already-seeded repo names are unchanged.
    instance_short = entry.get("instance_short", "")
    if instance_short:
        instance_name = f"{code}-odoo-{instance_short}-filestore-backup{suffix}"
    else:
        instance_name = f"{code}-odoo-filestore-backup{suffix}"
    repo_prefix = f"{code}-odoo-{short}{suffix}"
    bucket = entry["bucket"]
    endpoint = "https://fsn1.your-objectstorage.com"

    yaml_filename = f"{instance_name}.yaml"
    env_example_filename = f".env.{instance_name}.example"

    instance: dict = {
        "kind": "instance",
        "extends": "../../bases/odoo-filestore-backup.yaml",
        "instance": {
            "name": instance_name,
            "description": f"{name} — filestore backup ({short}) → {bucket}",
        },
        "project": code,
        "environment": env_name,
    }
    if server:
        instance["server"] = server
    instance.update(
        {
            "env_file": f"../../env/{env_name}/.env.{instance_name}",
            "env_overrides": {
                "TENANT": code,
                "FILESTORE_VOLUME": entry["volume"],
                "FILESTORE_DB": entry["database"],
                "FILESTORE_MIN_FILES": str(entry["min_files"]),
                "BACKUP_TAG": repo_prefix,
                "BACKUP_CRON_PINNED": entry.get("pinned_cron", ""),
                "RESTIC_REPOSITORY": f"s3:{endpoint}/{bucket}/{repo_prefix}",
            },
        }
    )

    env_example = (
        f"# Filestore backup for {name} ({short})\n"
        f"# RESTIC_PASSWORD is in 1Password: 'restic: {instance_name}'.\n"
        f"# Losing it makes every snapshot permanently unreadable — there is no\n"
        f"# recovery path. It must exist off this host BEFORE the first snapshot.\n"
        f"RESTIC_PASSWORD=CHANGEME\n"
        f"AWS_ACCESS_KEY_ID=CHANGEME\n"
        f"AWS_SECRET_ACCESS_KEY=CHANGEME\n"
    )

    return yaml_filename, yaml_dump(instance), env_example_filename, env_example


def gen_notify(
    tenant: dict,
    env_name: str = "production",
    server: str = "",
    dns_suffix: str = "",
) -> tuple[str, str, str, str]:
    """Generate notify service instance YAML + env.example."""
    code = tenant["code"]
    name = tenant["name"]
    domain = tenant["domain"]
    dns_name = f"{code}-notify{dns_suffix}"
    host = f"{code}-notify{dns_suffix}.{domain}"

    yaml_filename = f"{code}-hono-notify.yaml"
    env_example_filename = f".env.{code}-hono-notify.example"

    # Build ALLOWED_ORIGINS from all React PWA apps across all Odoo entries
    all_apps = []
    for odoo_entry in tenant.get("_odoo_entries", []):
        for app in odoo_entry.get("apps", []):
            all_apps.append(f"https://{code}-{app}{dns_suffix}.{domain}")
    allowed_origins = ",".join(all_apps)

    instance: dict = {
        "kind": "instance",
        "extends": "../../bases/infra.yaml",
        "instance": {
            "name": f"{code}-hono-notify",
            "description": "Notification dispatch service (FCM push + SSE real-time + Telegram)",
        },
        "source_overrides": {
            "type": "github",
            "owner": "tgunawandev",
            "repo": "kodemeio-react",
            "branch": "main",
            "compose_path": "apps/api/notify/docker-compose.yml",
        },
        "project": code,
        "environment": env_name,
    }
    if server:
        instance["server"] = server
    else:
        instance["server"] = "kodeme-service"
    instance.update(
        {
            "healthcheck": {
                "path": "/health",
                "port": 3020,
                "expected_status": 200,
                "timeout": 60,
                "interval": 10,
            },
            "dns": {
                "zone": domain,
                "name": dns_name,
            },
            "domain": {
                "host": host,
                "port": 3020,
                "service": "notify",
                "https": True,
            },
            "env_overrides": {
                "NODE_ENV": "production",
                "PORT": "3020",
                "DATABASE_URL": "postgresql://kodemeio:${DB_PASSWORD}@postgres:5432/kodemeio",
                "REDIS_URL": "redis://redis:6379",
                "DISPATCH_SECRET": "${DISPATCH_SECRET}",
                "JWT_SECRET": "${JWT_SECRET}",
                "ALLOWED_ORIGINS": allowed_origins,
                "FIREBASE_SERVICE_ACCOUNT_JSON": "${FIREBASE_SERVICE_ACCOUNT_JSON}",
            },
        }
    )

    env_example = (
        f"# Notify service for {name}\n"
        f"NODE_ENV=production\n"
        f"PORT=3020\n"
        f"DATABASE_URL=postgresql://kodemeio:CHANGE_ME@postgres:5432/kodemeio\n"
        f"REDIS_URL=redis://redis:6379\n"
        f"DISPATCH_SECRET=CHANGE_ME\n"
        f"JWT_SECRET=CHANGE_ME\n"
        f"ALLOWED_ORIGINS={allowed_origins}\n"
        f"FIREBASE_SERVICE_ACCOUNT_JSON=CHANGE_ME\n"
    )

    return yaml_filename, yaml_dump(instance), env_example_filename, env_example


def gen_hermes_agents(
    tenant: dict,
    hermes: dict,
    env_name: str = "production",
) -> list[tuple[str, str, str, str]]:
    """Generate one manifest per agent.

    A tenant with no `agents:` list yields exactly one agent, so every existing
    tenant file keeps working unchanged. With a list, each entry inherits the
    parent hermes block and overrides what it names — shared config is declared
    once, per-agent differences (edition, model, inbound) sit with the agent.
    """
    agents = hermes.get("agents")
    if not agents:
        return [gen_hermes(tenant, hermes, env_name)]

    shared = {k: v for k, v in hermes.items() if k != "agents"}
    seen: set[str] = set()
    out: list[tuple[str, str, str, str]] = []
    for entry in agents:
        name = (entry or {}).get("name") or ""
        if name in seen:
            label = name or "<unnamed>"
            raise ValueError(
                f"tenants/{tenant['code']}.yaml: hermes.agents names must be unique "
                f"— {label!r} appears twice; two agents sharing a name would "
                f"collide on manifest, env file, container and volume"
            )
        seen.add(name)
        merged = {**shared, **{k: v for k, v in (entry or {}).items() if k != "name"}}
        out.append(gen_hermes(tenant, merged, env_name, agent_name=name or None))
    return out


def gen_hermes(
    tenant: dict,
    hermes: dict,
    env_name: str = "production",
    agent_name: str | None = None,
) -> tuple[str, str, str, str]:
    """Generate the per-tenant Hermes Dokploy manifest + env.example.

    See kodemeio-hermes/docs/superpowers/specs/2026-05-23-hermes-generator-design.md.

    Returns (yaml_filename, yaml_content, env_example_filename, env_example_content)
    in the same shape as gen_notify et al. The HEADER ('# GENERATED FROM ...')
    is prepended by generate_tenant() — gen_hermes returns content WITHOUT header.
    """
    code = tenant["code"]
    short = tenant.get("short_name", code.upper())

    server = hermes.get("server")
    if not server:
        raise ValueError(
            f"tenants/{code}.yaml: hermes.enabled is true but hermes.server is "
            f"required (no implicit default — must declare server explicitly)"
        )

    edition = hermes.get("edition", "business")
    if edition not in ("superuser", "business"):
        raise ValueError(f"tenants/{code}.yaml: hermes.edition must be 'superuser' or 'business', got {edition!r}")

    # Upstream image tag. Tenant-scoped ON PURPOSE: a tenant that does not pin
    # one keeps the fleet default, so bumping one agent never drags the others
    # onto an untested upstream release.
    upstream_ref = hermes.get("upstream_ref", HERMES_DEFAULT_UPSTREAM_REF)
    if not isinstance(upstream_ref, str) or not upstream_ref.strip():
        raise ValueError(
            f"tenants/{code}.yaml: hermes.upstream_ref must be a non-empty string "
            f"(an upstream git tag, e.g. 'v2026.8.31'), got {upstream_ref!r}"
        )
    upstream_ref = upstream_ref.strip()

    dashboard_block = hermes.get("dashboard", {})
    dashboard_on = bool(dashboard_block.get("enabled"))
    if dashboard_on and edition != "superuser":
        raise ValueError(
            f"tenants/{code}.yaml: hermes.dashboard.enabled requires "
            f"hermes.edition: superuser (got {edition!r}) — the dashboard is an "
            f"admin surface and must not ship on a business-edition bot"
        )

    inbound = hermes.get("inbound", {})
    telegram_on = bool(inbound.get("telegram", {}).get("enabled"))
    mm_block = inbound.get("mattermost", {})
    mattermost_on = bool(mm_block.get("enabled"))

    if not (telegram_on or mattermost_on):
        raise ValueError(
            f"tenants/{code}.yaml: hermes is enabled but no inbound adapter is "
            f"enabled — need telegram or mattermost. (Hermes is useless without "
            f"an inbound; matches the Phase 1 init-script guard.)"
        )

    if mattermost_on and not mm_block.get("url"):
        raise ValueError(
            f"tenants/{code}.yaml: hermes.inbound.mattermost.enabled is true but "
            f"hermes.inbound.mattermost.url is required"
        )

    honcho_block = hermes.get("memory", {}).get("honcho", {})
    honcho_on = bool(honcho_block.get("enabled"))
    workspace = honcho_block.get("workspace", f"{code}-hermes") if honcho_on else None

    # Build description from active surfaces
    desc_parts = []
    if telegram_on and mattermost_on:
        desc_parts.append("Telegram + Mattermost inbound")
    elif telegram_on:
        desc_parts.append("Telegram inbound")
    elif mattermost_on:
        mm_host = mm_block["url"].replace("https://", "").replace("http://", "")
        desc_parts.append(f"Mattermost inbound ({mm_host})")
    desc_parts.append("MCP outbound")
    if honcho_on:
        desc_parts.append(f"Honcho memory ({workspace})")
    description = f"Hermes Agent ({short}) — " + ", ".join(desc_parts)

    # A tenant may run several agents (FRIDAY engineering + JARVIS helpdesk).
    # The suffix keeps their manifests, env files, containers and volumes apart.
    instance_name = f"{code}-infra-hermes" + (f"-{agent_name}" if agent_name else "")

    yaml_filename = f"{instance_name}.yaml"
    env_example_filename = f".env.{instance_name}.example"

    # Build manifest dict (yaml_dump preserves order)
    instance: dict = {
        "kind": "instance",
        "extends": "../../bases/hermes.yaml",
        "instance": {
            "name": instance_name,
            "description": description,
        },
        "project": code,
        "server": server,
        "env_file": f"../../env/{env_name}/.env.{instance_name}",
        "env_overrides": {
            "HERMES_EDITION": edition,
            # Authoritative pin: env_overrides is applied AFTER env_file, so the
            # manifest — not an operator remembering a runbook step — decides
            # which upstream release this agent builds from.
            # COROLLARY: this now BEATS a hand-set Dokploy env var. If an
            # operator has bumped a live instance's ref by hand, record it as
            # hermes.upstream_ref in that tenant BEFORE the next `deploy apply`
            # or the apply silently rolls that instance back.
            "HERMES_UPSTREAM_REF": upstream_ref,
            "HERMES_CONTAINER_PREFIX": instance_name,
            **HERMES_EDITION_RESOURCES[edition],
        },
    }

    # Compose env.example block-by-block based on enabled adapters
    lines: list[str] = [
        f"# Real values live in .env.{instance_name} (gitignored, operator-managed).",
        "# This file documents the env contract; never commit secrets here.",
        "",
        "# === Upstream image + identity ===",
        f"HERMES_EDITION={edition}",
        f"HERMES_UPSTREAM_REF={upstream_ref}",
        "HERMES_LOG_LEVEL=info",
        "HERMES_UID=10000",
        "HERMES_GID=10000",
        "TZ=Asia/Jakarta",
        "",
    ]

    if edition == "superuser":
        lines += [
            "# === Coding-agent workspace (superuser only) ===",
            "# Fine-grained GitHub PAT (Contents+PRs RW). Set yourself; never paste in chat.",
            "# OAuth (Claude/ChatGPT) bootstrapped once via scripts/bootstrap-coding-agents.sh.",
            "HERMES_WORKSPACE_GH_PAT=CHANGE_ME",
            "",
        ]

    if dashboard_on:
        lines += [
            "# === Web dashboard (loopback-only; SSH tunnel, no public host) ===",
            "# Bound 0.0.0.0 in-container, published only to 127.0.0.1 on the host.",
            "# Upstream fails closed: a non-loopback bind with no auth provider",
            "# refuses to start. Set the password yourself; never paste it in chat.",
            "COMPOSE_PROFILES=dashboard",
            "HERMES_DASHBOARD_PORT=9119",
            "# Basic-auth provider (plugins/dashboard_auth/basic). Activates ONLY with",
            "# USERNAME + PASSWORD_HASH; without it a non-loopback bind refuses to start.",
            "# SECRET is REQUIRED: when empty a random per-process key is generated and",
            "# every session dies on container restart. openssl rand -base64 32",
            "HERMES_DASHBOARD_BASIC_AUTH_USERNAME=admin",
            "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH=CHANGE_ME",
            "HERMES_DASHBOARD_BASIC_AUTH_SECRET=CHANGE_ME",
            "",
        ]

    if telegram_on:
        lines += [
            "# === Telegram inbound ===",
            "TELEGRAM_BOT_TOKEN=CHANGE_ME",
            "TELEGRAM_ALLOWED_USERS=",
            "",
        ]

    if mattermost_on:
        allowed = ",".join(mm_block.get("allowed_channels") or [])
        reply_mode = mm_block.get("reply_mode", "thread")
        require_mention = "true" if mm_block.get("require_mention", True) else "false"
        lines += [
            "# === Mattermost INBOUND adapter ===",
            f"MATTERMOST_URL={mm_block['url']}",
            "MATTERMOST_TOKEN=CHANGE_ME",
            f"MATTERMOST_REPLY_MODE={reply_mode}",
            f"MATTERMOST_REQUIRE_MENTION={require_mention}",
            f"MATTERMOST_ALLOWED_CHANNELS={allowed}",
            "",
        ]

    # GATEWAY_ALLOW_ALL_USERS only when Mattermost is the ONLY inbound
    if mattermost_on and not telegram_on:
        lines += [
            "# === Gateway-level allowlist ===",
            "# Fail closed. Upstream DOES enforce MATTERMOST_ALLOWED_USERS as of",
            "# v2026.8.31 (its troubleshooting guide: 'Bot ignores you — your User",
            "# ID isn't in MATTERMOST_ALLOWED_USERS'), so the allowlist — not",
            "# Mattermost-side channel scoping alone — is the inbound boundary.",
            "GATEWAY_ALLOW_ALL_USERS=false",
            "# 26-character Mattermost user IDs, comma-separated. NOT @usernames:",
            "# a username here matches nobody and silently locks the bot out.",
            "# Read one off: System Console > Users, or `kctl-mm users get <name>`.",
            ("MATTERMOST_ALLOWED_USERS=" + (",".join(mm_block.get("allowed_users") or []) or "CHANGE_ME")),
            "",
        ]

    if honcho_on:
        base_url = honcho_block.get("base_url", "https://honcho.kodeme.io")
        lines += [
            "# === Honcho memory ===",
            "HONCHO_API_KEY=CHANGE_ME",
            f"HONCHO_BASE_URL={base_url}",
            f"HONCHO_WORKSPACE={workspace}",
            "",
        ]

    # MCP outbound block — always present
    lines += [
        "# === MCP outbound (always; per-service opt-in via real .env) ===",
        "MCP_ODOO_URL=",
        "MCP_ODOO_AUTH_TOKEN=",
        "MATTERMOST_BASE_URL=",
        "MATTERMOST_BOT_TOKEN=",
        "MATTERMOST_TEAM_ID=",
        "MCP_MATTERMOST_URL=",
        "MCP_MATTERMOST_AUTH_TOKEN=",
        "MAILCOW_BASE_URL=",
        "MAILCOW_API_KEY=",
        "MAILCOW_API_KEY_TYPE=",
        "MCP_MAILCOW_URL=",
        "MCP_MAILCOW_AUTH_TOKEN=",
        "WAHA_BASE_URL=",
        "WAHA_API_KEY=",
        "WAHA_HMAC_KEY=",
        "MCP_WAHA_URL=",
        "MCP_WAHA_AUTH_TOKEN=",
    ]
    env_example = "\n".join(lines) + "\n"

    import re

    rendered = yaml_dump(instance)
    if re.match(r"^tpp-prod-\d+$", server):
        rendered += (
            "\n"
            "# bases/hermes.yaml's backup block targets 'kodemeio-s3-backups' which\n"
            "# exists on dokploy.kodeme.io but NOT on dokploy.idtpp.com. Backup phase\n"
            "# fails cosmetically during `deploy apply`; the actual deploy still succeeds.\n"
        )
    return yaml_filename, rendered, env_example_filename, env_example


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------


def generate_tenant(tenant_path: Path) -> list[tuple[Path, str]]:
    """Generate all files for a single tenant.

    Returns list of (file_path, content) tuples.
    """
    raw = load_tenant(tenant_path)
    t = raw["tenant"]
    code = t["code"]

    # Stash odoo entries for notify generator
    t["_odoo_entries"] = raw.get("odoo", [])

    header = HEADER.format(code=code)
    files: list[tuple[Path, str]] = []

    environments = raw.get(
        "environments",
        {
            "production": {"server": "", "dns_suffix": "", "db_suffix": ""},
        },
    )

    for env_name, env_config in environments.items():
        server = env_config.get("server", "")
        dns_suffix = env_config.get("dns_suffix", "")
        db_suffix = env_config.get("db_suffix", "")
        inst_dir = INSTANCES_DIR / env_name
        env_dir = ENV_DIR / env_name

        # --- React PWAs + Odoo instances ---
        for odoo_entry in raw.get("odoo", []):
            if env_name not in odoo_entry.get("environments", [env_name]):
                continue
            # Odoo instance
            y_name, y_content, e_name, e_content = gen_odoo(t, odoo_entry, env_name, server, dns_suffix, db_suffix)
            files.append((inst_dir / y_name, header + y_content))
            files.append((env_dir / e_name, e_content))

            # React PWAs for this Odoo
            for app in odoo_entry.get("apps", []):
                y_name, y_content, e_name, e_content = gen_react_pwa(
                    t, odoo_entry, app, env_name, server, dns_suffix, db_suffix
                )
                files.append((inst_dir / y_name, header + y_content))
                files.append((env_dir / e_name, e_content))

        # --- Next.js corporate ---
        web = raw.get("web", {})
        if "corporate" in web:
            t["_web_corporate_brand"] = web["corporate"]["compose_brand"]
            y_name, y_content, e_name, e_content = gen_nextjs_corporate(t, env_name, server, dns_suffix)
            files.append((inst_dir / y_name, header + y_content))
            files.append((env_dir / e_name, e_content))

        # --- Next.js careers ---
        if web.get("careers"):
            y_name, y_content, e_name, e_content = gen_nextjs_careers(t, env_name, server, dns_suffix)
            files.append((inst_dir / y_name, header + y_content))
            files.append((env_dir / e_name, e_content))

        # --- Accurate-sync ---
        accurate_cfg = raw.get("accurate_sync")
        if accurate_cfg and accurate_cfg.get("enabled"):
            ref_short = accurate_cfg["odoo_ref"]
            try:
                odoo_entry = next(e for e in raw.get("odoo", []) if e["short"] == ref_short)
            except StopIteration as exc:
                raise ValueError(
                    f"accurate_sync.odoo_ref={ref_short!r} does not match any odoo[] entry in tenants/{code}.yaml"
                ) from exc
            acc_server = accurate_cfg.get("server", server)
            y_name, y_content, e_name, e_content = gen_accurate_sync(
                t, accurate_cfg, odoo_entry, env_name, acc_server, dns_suffix, db_suffix
            )
            files.append((inst_dir / y_name, header + y_content))
            files.append((env_dir / e_name, e_content))

        # --- Ofelia schedulers ---
        for scheduler in raw.get("schedulers", []):
            sched_server = scheduler.get("server", server)
            y_name, y_content, e_name, e_content = gen_ofelia(t, scheduler, env_name, sched_server)
            files.append((inst_dir / y_name, header + y_content))
            files.append((env_dir / e_name, e_content))

        # --- Filestore backups ---
        # Each entry names an EXACT docker volume on an EXACT host, so it is
        # tied to one environment; entries declare which (default production).
        # dns_suffix keeps staging in its own restic repository — sharing one
        # with production would let a staging retention run prune prod history.
        for fsb in raw.get("filestore_backups", []):
            if fsb.get("environment", "production") != env_name:
                continue
            fsb_server = fsb.get("server", server)
            y_name, y_content, e_name, e_content = gen_filestore_backup(t, fsb, env_name, fsb_server, dns_suffix)
            files.append((inst_dir / y_name, header + y_content))
            files.append((env_dir / e_name, e_content))

        # --- Notify ---
        services = raw.get("services", {})
        notify_cfg = services.get("notify")
        if notify_cfg:
            # `services.notify` accepts either `true` (use tenant default server)
            # or a mapping like `{server: tpp-prod-01}` to pin notify to a
            # specific host (e.g., colocate with tpp-infra-* services).
            notify_server = server
            if isinstance(notify_cfg, dict):
                notify_server = notify_cfg.get("server", server)
            y_name, y_content, e_name, e_content = gen_notify(t, env_name, notify_server, dns_suffix)
            files.append((inst_dir / y_name, header + y_content))
            files.append((env_dir / e_name, e_content))

        # --- Hermes (per-tenant agent: Telegram/Mattermost inbound, Honcho memory) ---
        # Production-only per v1 spec. Staging support is a documented
        # out-of-scope follow-up; the hermes: block is currently tenant-level
        # (not per-env), so generating staging would produce nonsense values.
        hermes = raw.get("hermes", {})
        if hermes.get("enabled") and env_name == "production":
            # The env example carries the header too. Without the marker the
            # writer treats an existing .example as hand-authored and REFUSES
            # to overwrite it — which is how the hermes env contract silently
            # went stale while the manifests moved on.
            for y_name, y_content, e_name, e_content in gen_hermes_agents(t, hermes, env_name):
                files.append((inst_dir / y_name, header + y_content))
                files.append((env_dir / e_name, header + e_content))

    return files


def is_secret_env(path: Path) -> bool:
    """Check if a .env file likely contains secrets that must not be overwritten (Odoo, Notify, React PWA with OIDC).

    Note: .env.*-accurate-sync is intentionally NOT treated as secret — its
    contents are fully managed by the generator, which pulls PGPASSWORD and
    ODOO_ADMIN_PASSWD from the sibling .env.{tenant}-odoo-{short} file on
    every run. This keeps the accurate-sync env in lock-step with the Odoo
    instance it targets (no drift, no manual resync).
    """
    name = path.name
    return name.startswith(".env.") and ("-odoo-" in name or "-hono-notify" in name or "-react-" in name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate deploy instances from tenant manifests")
    parser.add_argument("--tenant", "-t", help="Generate for a single tenant (e.g., mac)")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Preview without writing")
    parser.add_argument("--diff", "-d", action="store_true", help="Show diff vs existing files")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite hand-authored files (those without a GENERATED FROM marker). "
        "Required only for the first retrofit of files that pre-date the generator.",
    )
    args = parser.parse_args()

    if args.tenant:
        tenant_files = [TENANTS_DIR / f"{args.tenant}.yaml"]
        if not tenant_files[0].exists():
            print(f"Error: tenant file not found: {tenant_files[0]}", file=sys.stderr)
            sys.exit(1)
    else:
        tenant_files = sorted(TENANTS_DIR.glob("*.yaml"))
        if not tenant_files:
            print("No tenant files found in tenants/", file=sys.stderr)
            sys.exit(1)

    all_files: list[tuple[Path, str]] = []
    for tf in tenant_files:
        print(f"Processing tenant: {tf.stem}")
        all_files.extend(generate_tenant(tf))

    wrote = 0
    skipped = 0
    unchanged = 0

    for path, content in all_files:
        path.parent.mkdir(parents=True, exist_ok=True)
        is_example = path.name.endswith(".example")
        existing_secret_env = not is_example and is_secret_env(path) and path.exists()

        if existing_secret_env:
            # Never overwrite .env files with secrets
            example_path = Path(str(path) + ".example")
            if args.diff and example_path.exists():
                old = example_path.read_text().splitlines(keepends=True)
                new = content.splitlines(keepends=True)
                diff = difflib.unified_diff(old, new, fromfile=str(example_path), tofile=str(example_path))
                sys.stdout.writelines(diff)
            if not args.dry_run:
                example_path.write_text(content)
            print(f"  SKIP (secrets): {path.name} → wrote .example instead")
            skipped += 1
            continue

        if path.exists():
            existing = path.read_text()
            # Guard: refuse to overwrite hand-authored files unless --force.
            # A file is considered hand-authored if it exists but lacks our
            # GENERATED FROM marker. After the first --force retrofit the
            # marker is present, and subsequent regenerations proceed normally.
            existing_first_line = existing.splitlines()[0] if existing else ""
            is_generated = existing_first_line.startswith("# GENERATED FROM")
            if not is_generated and not args.force and not args.dry_run and not args.diff:
                print(
                    f"  REFUSED: {path.relative_to(DEPLOY_DIR)} exists without "
                    f"GENERATED FROM marker. Pass --force to overwrite."
                )
                skipped += 1
                continue
            if existing == content:
                unchanged += 1
                continue
            if args.diff:
                old = existing.splitlines(keepends=True)
                new = content.splitlines(keepends=True)
                diff = difflib.unified_diff(old, new, fromfile=str(path), tofile=str(path))
                sys.stdout.writelines(diff)

        if args.dry_run:
            print(f"  WOULD WRITE: {path.relative_to(DEPLOY_DIR)}")
        else:
            path.write_text(content)
            print(f"  WROTE: {path.relative_to(DEPLOY_DIR)}")
        wrote += 1

    action = "Would write" if args.dry_run else "Wrote"
    print(f"\nDone. {action}: {wrote}, Skipped (secrets): {skipped}, Unchanged: {unchanged}")


if __name__ == "__main__":
    main()

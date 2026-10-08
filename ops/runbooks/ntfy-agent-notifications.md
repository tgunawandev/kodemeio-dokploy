# ntfy agent notifications (`kod-infra-ntfy`)

Self-hosted **ntfy** at <https://ntfy.kodeme.io> that pages a phone when an
agent **finishes** or **needs confirmation**. It is the one notification channel
that works in this estate's headless Orca deployment: Orca's own beep is
renderer/Electron-only, the browser bundle has no audio, and Orca Mobile push
needs the upstream `push.onorca.dev` cloud. ntfy is independent of all three.

## What is deployed

| | |
|---|---|
| Dokploy compose | `kod-infra-ntfy` (`JRCq3agAfYxr3mbEC7nr0`), project `apps`, environment `production` |
| Server | `kod-hzc-02` (46.225.42.130) |
| Source | **raw Compose** — `deploys/composes/ntfy/docker-compose.yml` |
| Domain | `ntfy.kodeme.io` → `kod-infra-ntfy` service port 80, Let's Encrypt |
| DNS | `A ntfy.kodeme.io 46.225.42.130`, TTL 60, unproxied |
| Volume | `ntfy-data` (user DB + message cache; disposable) |
| Image | `binwiederhier/ntfy:v2.29.0` |

Auth is **deny-all**. Two users are seeded idempotently by the `ntfy-auth`
one-shot on every stack start:

- `orca-agents` — regular user, read-write on topic `kod-orca-agents` only.
  Used by the Pi extension and the Codex notify hook.
- `kodeme` — admin, for the ntfy web UI and phone apps.

Credentials live in 1Password (Kodemeio vault, item
`kodemeio-platform/kod-infra-ntfy`) and the gitignored
`deploys/env/production/.env.kod-infra-ntfy`. Neither is in git.

## Deploy / redeploy (raw source)

The generic `deploy apply` assumes a GitHub source even for
`source.type: raw`, so this stack is operated directly. Preview first; queuing
a deployment does not establish success — poll until `done`, then verify.

```bash
./dokploy.sh kodemeio compose update JRCq3agAfYxr3mbEC7nr0 \
  --source-type raw --compose-file deploys/composes/ntfy/docker-compose.yml --no-auto-deploy --yes
./dokploy.sh kodemeio env push JRCq3agAfYxr3mbEC7nr0 \
  deploys/env/production/.env.kod-infra-ntfy --yes
./dokploy.sh kodemeio compose start JRCq3agAfYxr3mbEC7nr0 --yes
kctl-dokploy -p kodemeio deployments list --compose JRCq3agAfYxr3mbEC7nr0 --limit 1
```

A fresh host needs the env file materialised from 1Password first; see
`deploys/env/production/.env.kod-infra-ntfy.example` for the key names.

## Verify

```bash
curl -fsS https://ntfy.kodeme.io/v1/health            # {"healthy":true}
# Publish as the agent account and read back as admin (no secrets echoed):
set -a; . deploys/env/production/.env.kod-infra-ntfy; set +a
curl -sS -u "$NTFY_AGENTS_USER:$NTFY_AGENTS_PASSWORD" \
  -d 'deploy smoke' "https://ntfy.kodeme.io/$NTFY_TOPIC"
curl -sS -u "$NTFY_ADMIN_USER:$NTFY_ADMIN_PASSWORD" \
  "https://ntfy.kodeme.io/$NTFY_TOPIC/json?poll=1"
# Anonymous must be denied:
curl -sS -o /dev/null -w '%{http_code}\n' "https://ntfy.kodeme.io/$NTFY_TOPIC"  # 403
```

## Subscribe on a phone

The phone must use the native **ntfy app** (`io.heckel.ntfy`, Play Store or
F-Droid) — the web page at <https://ntfy.kodeme.io/kod-orca-agents> shows
messages but never sounds. Add server `https://ntfy.kodeme.io`, sign in as the
admin user, and subscribe to `kod-orca-agents`.

Sound is owned by the Android notification channel, not the server. Set a sound
on ntfy's "High priority" and "Max priority" channels (Settings → Apps → ntfy →
Notifications), enable **Instant delivery**, and give ntfy **Unrestricted**
battery usage. Messages are sent at high (4) for `finished` and urgent (5) for
`needs-input`/`needs-approval` so they use those channels.

## Agent integration

Both harnesses call one shared helper, `kodemeio-agent-notify`, so they format
and authenticate identically.

- Pi: `.pi/agent/extensions/agent-notify-ntfy.ts` (from
  `kodemeio-orca/config/pi/extensions/`) fires on `agent_settled` and on
  `ui_prompt_start` (a blocking confirm/select/input).
- Codex: `notify = [...]` in `~/.codex/config.toml` fires on
  `agent-turn-complete`, `approval-requested` and `async-question`.
- Config: `~/.config/kodemeio-pi/ntfy.json`, mode 0600, written from 1Password
  by `kodemeio-orca/scripts/install-runtime-agents.sh`.

The helper is best effort: it exits 0 on any error, times out in seconds, and
never prints a credential. `NTFY_DISABLE=1` disables it.

## Rollback

```bash
./dokploy.sh kodemeio compose stop JRCq3agAfYxr3mbEC7nr0 --yes
kctl-cf -p kodemeio records delete <record-id>   # drop the A record if retiring
```

Agents then simply fail open: the helper cannot reach ntfy and exits 0.

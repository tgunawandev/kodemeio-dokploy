# Runbook — Hetzner disk resize (idtpp fleet)

**Date:** 2026-04-19
**Owner:** @tgunawan
**Window:** overnight (low-traffic)
**Goal:** grow each server's root disk to the size the `cpxXX` plan already bills for.

---

## Background

On Hetzner Cloud, resizing a server's CPU/RAM tier does **not** grow the disk unless the operator checks "Enlarge disk" at rescale time. Four of the five `idtpp` servers have disks smaller than their current plan allows — we're paying for the capacity but the VM only sees a fraction of it. Disk expansion is **one-way** (can't shrink later).

## Affected servers

Source of truth: `uv run kctl-hz -p idtpp --json servers get <id>`.

| # | Server | Type | API allocates | Actual disk | Gap | Priority |
|---|---|---|---|---|---|---|
| 1 | `tpp-prod-03` | cpx52 | 480 GB | **38 GB** | +442 GB | 🔴 urgent (21% full on 38 GB) |
| 2 | `tpp-prod-05` | cpx42 | 320 GB | **76 GB** | +244 GB | 🟠 high (48% full) |
| 3 | `tpp-prod-01` | cpx52 | 480 GB | **305 GB** | +175 GB | 🟢 routine |
| 4 | `tpp-prod-04` | cpx42 | 320 GB | **153 GB** | +167 GB | 🟢 routine |

`tpp-prod-02` is correctly sized (152 GB of 160 GB) — **do NOT touch**.

## What runs on each server

| Server | Services (from `docker ps` at time of writing) | Downtime impact |
|---|---|---|
| `tpp-prod-01` | **dokploy control plane**, mailcow, jitsi (jicofo/jvb), mattermost-1, dokploy-postgres, dokploy-redis, traefik | 🔴 **Cascading** — dokploy UI goes down, mail inbound queues, jitsi calls drop, traefik stops routing. Plan longest window. |
| `tpp-prod-03` | Odoo (web/cron/gevent), traefik | 🟠 Single Odoo tenant down (tpp-odoo-*) |
| `tpp-prod-04` | Mattermost + mm-postgres + mm-backup, traefik | 🟢 Team chat down |
| `tpp-prod-05` | Mattermost *(second instance)* + mm-postgres, Odoo (web/cron/gevent), traefik | 🟠 Odoo + secondary mattermost down |

## Pre-flight checklist (run once, before starting)

- [ ] Announce window in #ops (1 hr before)
- [ ] Confirm laptop has SSH key `~/.ssh/id_rsa_kodeme`
- [ ] Verify kctl-hz profile works:
  ```bash
  uv run kctl-hz -p idtpp servers list
  ```
- [ ] Verify Hetzner Cloud web console login works (fallback if CLI fails): <https://console.hetzner.cloud/>, account `trigunawan.note@gmail.com`
- [ ] Back up the Hetzner server IDs (lose these and you can't easily find them again):

  | Server | ID |
  |---|---|
  | tpp-prod-01 | `126000539` |
  | tpp-prod-03 | `126273126` |
  | tpp-prod-04 | `126910087` |
  | tpp-prod-05 | `126377999` |

- [ ] Spot-check each server is healthy right now:
  ```bash
  for p in 178.104.127.104 46.225.215.106 178.104.169.250 178.104.171.122; do
    ssh -o BatchMode=yes -i ~/.ssh/id_rsa_kodeme root@$p \
        "hostname; uptime; df -h /"
  done
  ```

## Execution order

Do them in this order to minimize blast radius early and keep the most-impacting one last (so you can abort cleanly if any of the earlier ones surprise you):

1. **tpp-prod-04** — just mattermost, lowest risk, lets you validate the procedure end-to-end
2. **tpp-prod-05** — mattermost-2 + odoo
3. **tpp-prod-03** — Odoo only, urgent due to disk pressure
4. **tpp-prod-01** — last, biggest blast radius (dokploy itself)

## Per-server procedure

The same sequence applies to every server. Substitute the server name below.

### Step 1 — Graceful shutdown

```bash
SERVER=tpp-prod-04          # or 03, 05, 01
PUBLIC_IP=178.104.169.250   # set from the table above

# Drain container traffic cleanly — stop compose stacks first
ssh -i ~/.ssh/id_rsa_kodeme root@$PUBLIC_IP 'docker ps --format "{{.Names}}"' \
    | xargs -r -n1 echo "running:"   # note what's up, for post-resize verify

# Shut down from inside
ssh -i ~/.ssh/id_rsa_kodeme root@$PUBLIC_IP "shutdown -h now"

# Wait for Hetzner to show it as off
sleep 30
uv run kctl-hz -p idtpp servers list | grep "$SERVER"
# Status should be "off". If still "running", wait another 30s.
```

### Step 2 — Resize with disk enlargement

```bash
# Same type, but with --upgrade-disk. Irreversible.
uv run kctl-hz -p idtpp servers resize $SERVER \
    --type <current-type> \
    --upgrade-disk \
    --force

# Example for tpp-prod-04:
# uv run kctl-hz -p idtpp servers resize tpp-prod-04 --type cpx42 --upgrade-disk --force
```

**Current type for each server (pass to `--type`):**

| Server | Type |
|---|---|
| tpp-prod-01 | `cpx52` |
| tpp-prod-03 | `cpx52` |
| tpp-prod-04 | `cpx42` |
| tpp-prod-05 | `cpx42` |

> ⚠️ `--upgrade-disk` is ONE-WAY. Once grown, Hetzner will never allow shrinking.

> ⚠️ If `--upgrade-disk` flag is unsupported or `kctl-hz servers resize` refuses, fall back to the Hetzner Cloud web UI:
> `Server → "Rescale"` → keep same type → **check "Enlarge disk"** → Confirm.

### Step 3 — Power on

```bash
uv run kctl-hz -p idtpp servers reboot $SERVER   # or: hetzner UI → "Power on"
# Wait for SSH to come back
until ssh -o ConnectTimeout=5 -o BatchMode=yes -i ~/.ssh/id_rsa_kodeme root@$PUBLIC_IP "echo up" 2>/dev/null; do
    sleep 5
done
```

### Step 4 — Grow the partition + filesystem

Inside the booted VM:

```bash
ssh -i ~/.ssh/id_rsa_kodeme root@$PUBLIC_IP << 'EOF'
# Verify the kernel now sees the larger disk
lsblk /dev/sda

# Grow partition 1 to fill the disk
growpart /dev/sda 1

# Grow the ext4 filesystem
resize2fs /dev/sda1

# Confirm the new size
df -h /
EOF
```

Expected after Step 4:

| Server | `df -h /` should show ~ |
|---|---|
| tpp-prod-01 | `470G` total |
| tpp-prod-03 | `470G` total |
| tpp-prod-04 | `315G` total |
| tpp-prod-05 | `315G` total |

(Slightly less than the plan's advertised figure because of partition + filesystem overhead.)

### Step 5 — Post-resize validation

```bash
# Containers are back?
ssh -i ~/.ssh/id_rsa_kodeme root@$PUBLIC_IP \
    "docker ps --format '{{.Names}}\t{{.Status}}'"

# Service health for the specific server (pick the right check):

# tpp-prod-01 — dokploy UI
curl -fsSI https://dokploy.idtpp.com | head -3

# tpp-prod-03 — TPP Odoo ERP
curl -fsS https://erp.idtpp.com/web/health

# tpp-prod-04 — Mattermost
curl -fsS https://mm.idtpp.com/api/v4/system/ping

# tpp-prod-05 — Mattermost secondary (check whichever domain you use)
#   and Odoo instance running here
```

If any check fails, **do not proceed to the next server** — diagnose first.

## Rollback / abort

- **Before the resize command runs**: just power the server back on with `kctl-hz servers reboot $SERVER`. No change made.
- **After `--upgrade-disk` runs**: **cannot roll back the disk size.** You can only power on and proceed. If the resize itself failed for some reason (e.g. Hetzner bug), the server is still bootable — try powering on and file a Hetzner ticket.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `growpart: device does not exist` | Verify with `lsblk` — the device name might be `vda` on some rescue boots. Substitute accordingly. |
| `resize2fs: bad superblock` | Partition wasn't actually grown. Re-run `growpart /dev/sda 1`; if still failing, boot into Hetzner rescue and run `e2fsck -f /dev/sda1` before resize2fs. |
| Server won't power on after resize | Hetzner UI → server → "Console" for a VNC view. Mount rescue system if needed. |
| Containers don't come back automatically | `systemctl restart docker && docker start $(docker ps -aq)` — they should have restart policies but not all do. |
| Dokploy traefik not routing (tpp-prod-01) | Traefik restart: `docker restart dokploy-traefik` |
| Mailcow stuck (tpp-prod-01) | `cd /opt/mailcow-dockerized && docker compose up -d` |

## After all four servers are done

```bash
# Confirm no more undersized servers
for id in 126000539 126273126 126910087 126377999; do
    uv run kctl-hz -p idtpp --json servers get $id | \
        python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"{d['name']}: type.disk={d['server_type']['disk']}GB\")"
done
```

Then verify actual disk on each:

```bash
for p in 178.104.127.104 46.225.215.106 178.104.169.250 178.104.171.122; do
    ssh -i ~/.ssh/id_rsa_kodeme root@$p "echo -n \"\$(hostname): \"; df -h / | awk 'NR==2 {print \$2}'"
done
```

## Decisions for next iteration (not for this run)

- **tpp-prod-04 is wildly underutilized** (0.00 load, 6% RAM on a cpx42). Candidate for a later downsize to `cx22` (~€4/month instead of ~€24/month).
- **tpp-prod-02 is RAM-tight** (only 2.8 GB available of 7.6 GB). If MAC traffic grows, upgrade to cpx42 (with `--upgrade-disk=false` this time, since its disk is already full-size for the current plan).
- **Add swap**: none of the idtpp servers have swap configured. A 4–8 GB swap file per server is cheap insurance against OOM.

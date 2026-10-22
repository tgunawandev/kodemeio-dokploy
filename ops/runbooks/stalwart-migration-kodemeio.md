# Mailcow → Stalwart migration (kodeme.io)

Moves mail for `kodeme.io` from Mailcow on **kod-ovh-01** to Stalwart on
**kod-hzc-01**, with Roundcube as webmail. Planned and executed 2026-10-18.

## Status — cut over 2026-10-18 14:04:45 UTC

| | |
|---|---|
| `A mail.kodeme.io` | 57.131.162.248 → **49.13.116.191** |
| Stalwart | `kod-infra-stalwart`, compose `rW7XoyGT-OclHEytipNx5`, appName `compose-transmit-neural-panel-hxge1l` |
| Certificate | Let's Encrypt via Stalwart ACME, TLS-ALPN-01 through Traefik passthrough — `mail`, `autoconfig`, `autodiscover`; issued 30 s after the DNS change |
| Verified from outside | valid chain on 443, 465, 587, 993, 25 (STARTTLS), 4190 |
| Inbound | delivery from another host through MX lookup → mailbox |
| Outbound | Port25 report: **SPF pass, DKIM pass (`d=kodeme.io`, `s=dkim`), iprev pass** |
| Accounts | 6, original bcrypt hashes — passwords unchanged; a Dovecot-generated hash logs in |
| Mail copied | 6/6 messages; nothing reached Mailcow after the cutover |
| Webmail | `https://webmail.kodeme.io`, end-to-end login with verified TLS to Stalwart |
| Backups | daily volume backups, data volume with container stopped; first archive (2.0 MB) confirmed in `kodemeio-backups` |

### Deviations from the plan
- **TLS: TLS-ALPN instead of DNS-01.** The kctl-cf token cannot mint scoped
  tokens (`User.API Tokens.Edit` missing), and handing Stalwart the broad token
  was not acceptable. Traefik passes TLS for the three names through to
  Stalwart (compose labels), so one Stalwart-held certificate covers HTTPS and
  every mail port with no DNS credential at all.
- **Mail copy without imapsync.** Stalwart allows one password credential per
  account and refuses admin-set app passwords, so the two accounts that had
  mail got a temporary hash for the copy and their original hash back after.
  Source login used Mailcow's generated Dovecot master user (`/etc/sogo/sieve.creds`).
- **Gatus and Mattermost SMTP were already broken on Mailcow** — the passwords
  in their Dokploy env did not match their mailboxes (19 failed logins in 7 d).
  Their Stalwart mailboxes were set to the passwords those apps already use, so
  both send again with no change on the app side.
- POP3 listener removed (995 was never published); autoconfig still lists POP3
  after IMAP, which clients ignore.

### Still to do
- [ ] Tell users: webmail is now `https://webmail.kodeme.io`; passwords unchanged.
- [ ] Store `.env.kod-infra-stalwart` in 1Password — `kctl-op push` currently
      refuses because the `kodemeio` profile lists a missing scan root (`kodemeio-core`).
- [ ] 2026-10-25: confirm Mailcow received nothing since the cutover, then stop
      and remove `kod-infra-mailcow` from kod-ovh-01 (frees ~1.8 GB RAM).
      Note: Mailcow's volume backups never ran after 2026-10-10 — there is no
      Mailcow backup to fall back on beyond the running stack.
- [ ] Optional: point Stalwart's DNS resolver at a DNSSEC-validating resolver
      (it warns that Docker's resolver cannot validate, which disables DANE).

### Operating Stalwart
Administration is the web UI at `https://mail.kodeme.io/admin` (user
`admin@kodeme.io`) or the JMAP management API at `/jmap/` with the same Basic
credentials. Configuration lives in the data volume, not in this repo.

## Starting point (measured 2026-10-18)

| | |
|---|---|
| Domains | `kodeme.io` only |
| Mailboxes | 6 — asep.iwan, gatus, info, mattermost, postmaster, tri.gunawan; all `BLF-CRYPT` (bcrypt) |
| Aliases | 6, all Mailcow's default self-aliases — nothing to recreate |
| Mail stored | 6 messages, 17 KB (tri.gunawan 5, postmaster 1) |
| SOGo calendars / contacts | 0 / 0 |
| App passwords, transports, relayhosts | none |
| Active SMTP senders (7 d) | `gatus@kodeme.io` only; 2 failed logins as `noreply@idtpp.com` (no such mailbox here) |
| IMAP logins (14 d) | all from SOGo on the Mailcow host itself — people use webmail |
| DKIM | selector `dkim`, RSA, key held in Mailcow's redis |
| Mailcow memory | ≈1.8 GB over 18 containers (ClamAV 1 GB) |

DNS (Cloudflare, all TTL auto = 300 s, unproxied):

```
A      mail.kodeme.io               57.131.162.248      ← the only record that moves
CNAME  autoconfig / autodiscover    mail.kodeme.io
SRV    _autodiscover._tcp           1 443 mail.kodeme.io
MX     kodeme.io                    mail.kodeme.io
TXT    kodeme.io                    v=spf1 mx -all      ← follows MX, no change
TXT    dkim._domainkey              (kept — same key is imported)
TXT    _dmarc                       p=quarantine        ← no change
```

Because every mail record points through `mail.kodeme.io`, **the cutover is one
A-record change**, reversible within the 5-minute TTL.

## Target

| | |
|---|---|
| Host | kod-hzc-01 (49.13.116.191), cx33, about 3.6 GB RAM free, 27 GB disk free |
| Stalwart | `stalwartlabs/stalwart:v0.16.24`, RocksDB store, internal directory |
| Webmail | Roundcube at `webmail.kodeme.io`, SQLite, IMAP/SMTP to Stalwart on the compose network |
| Mail ports | 25, 465, 587, 993, 4190 published **on the host** — SMTP/IMAP cannot ride the HTTP reverse proxy; the documented exception to "no direct ports" |
| HTTP | `mail`, `autoconfig`, `autodiscover`, `webmail` `.kodeme.io` via Dokploy Traefik |
| Mail-port TLS | Stalwart's own ACME (planned DNS-01; done as TLS-ALPN-01 through Traefik passthrough — see Deviations) |
| Backups | volume backup of the Stalwart data volume to `kodemeio-s3-backups`, daily, keep 7 |

Desired state: `deploys/instances/production/kod-infra-stalwart.yaml`, raw
compose `deploys/composes/stalwart/docker-compose.yml`, values in the ignored
`deploys/env/production/.env.kod-infra-stalwart` (example committed).

## Phases

### 0 — Prepare (no user impact)
1. Hetzner firewall `firewall-1`: allow inbound tcp 587, 993, 4190 (25 and 465 already open).
2. rDNS 49.13.116.191 → `mail.kodeme.io`.
3. Cloudflare: scoped API token (Zone DNS edit, `kodeme.io` only) for Stalwart ACME.
4. DNS `A webmail.kodeme.io → 49.13.116.191` (new name, nothing depends on it).
5. Secrets in 1Password item `kodemeio-platform/kod-infra-stalwart` and the ignored env file.

### 1 — Deploy alongside Mailcow (no user impact)
1. Create the compose in Dokploy on kod-hzc-01 (`project: apps`), raw source, start.
2. Complete the bootstrap non-interactively: hostname `mail.kodeme.io`, domain
   `kodeme.io`, RocksDB, internal directory, permanent admin.
3. ACME DNS-01 certificate for `mail.kodeme.io`.
4. Import the Mailcow DKIM key under selector `dkim`.
5. Create the 6 accounts with their **existing bcrypt hashes** — passwords keep working.

### 2 — Verify before cutover
- Health endpoint, listeners on 25/465/587/993/4190, certificate on 465/993.
- IMAP and SMTP-submission login against Stalwart directly with a known account.
- Local delivery between two kodeme.io accounts; DKIM signature present.

### 3 — Copy mail
`imapsync` source Mailcow (Dovecot master user) → Stalwart (temporary second
credential per account, removed afterwards). Six messages; seconds.

### 4 — Cutover (5-minute TTL)
1. `A mail.kodeme.io → 49.13.116.191`.
2. Watch Traefik issue certificates for `mail`/`autoconfig`/`autodiscover`.
3. Inbound test: SMTP to `mail.kodeme.io:25` from another host; delivery lands.
4. Outbound test: Gatus alert mail and a message to an external mailbox; check
   SPF/DKIM/DMARC pass in the received headers.
5. Final `imapsync` pass for anything that reached Mailcow during propagation.

### 5 — Settle
- Keep Mailcow running but unused for 7 days (rollback target).
- Then stop it, take a final backup, and remove the compose from kod-ovh-01.
- Tell users: webmail moves from SOGo (`mail.kodeme.io`) to `webmail.kodeme.io`; passwords unchanged.

## Rollback
Point `A mail.kodeme.io` back to `57.131.162.248`. Mail that reached Stalwart
in the meantime is copied back with `imapsync` in the other direction.

## Clients that send through this server
| Client | Account | Change needed |
|---|---|---|
| Gatus (kod-hzc-01, kod-ovh-01) | `gatus@kodeme.io` | none — host and password unchanged |
| Mattermost | `mattermost@kodeme.io` | none |
| idtpp app trying `noreply@idtpp.com` | no such mailbox | out of scope; fix on the idtpp side |

# IDTPP Gatus Authentik routing

Browser requests to `https://gatus.idtpp.com` use provider 42 (`gatus`) and
Authentik's embedded outpost. The application policy permits active accounts
`tri.gunawan`, `anggi.fernadi`, and `abdul.hadi`.

Gatus runs on tpp-prod-06; the embedded outpost runs on tpp-prod-01.
The public Authentik router overwrites forwarded host headers, so using
`https://auth.idtpp.com/outpost.goauthentik.io/auth/traefik` as the forward-auth
address returns 404: the outpost cannot match the Gatus provider. Preserve the
Gatus host through a dedicated outpost router instead:

1. On tpp-prod-01, deploy `ops/traefik/tpp-gatus-embedded-outpost.yml` as
   `tpp-gatus-embedded-outpost.yml`. It routes only the Gatus outpost path to
   `authentik-server:9000` on the existing Dokploy network.
2. On tpp-prod-06, privately render `tpp-gatus-authentik.template.yml` with
   `GATUS_UPSTREAM_AUTHORIZATION` from the existing monitoring operator account.
   Deploy as `tpp-gatus-authentik.yml` and protect the remote file with mode 600.
   Never commit the rendered file.
3. The forward-auth address uses the Gatus hostname. Its outpost service reaches
   `https://auth.idtpp.com` with the original Host header; TLS uses the Authentik
   hostname. The dedicated router on tpp-prod-01 preserves the provider host.

Use `./dokploy.sh idtpp traefik file put ... --server <id> --explain --yes`
for preview, then omit `--explain` to apply. The bounded remote protection helper
is `./dokploy.sh monitoring-remote idtpp <tpp-prod-06-id> protect-sso-file --apply`.

The browser middleware refuses caller-supplied forwarded headers. After SSO,
a separate middleware supplies native Basic authentication to the Gatus backend.
Sentinel's fixed `/api/v1/` requests retain native Basic enforcement; invalid
credentials receive 401. `/health` stays public. The existing GlitchTip webhook
router keeps its own priority and signature checks.

Verification on 2026-10-07: root and unauthenticated API redirect to Authentik,
then to its authentication flow (200); no Basic challenge. Outpost ping 204;
health 200; operator API 200; wrong API credentials 401. Spoofed forwarded host,
URI and protocol headers still redirect for the Gatus callback. All three named
accounts pass the expression policy; another active account fails. Interactive
sign-in with the users' credentials remains for the users to exercise.

Rollback: remove the owned browser/outpost routers and SSO middlewares from
the protected file through the same front door. The original Docker browser
router then resumes native Basic authentication; keep the machine API and
health routes plus the existing webhook router.
Do not alter global Traefik forwarded-header trust or stop Dokploy/Traefik.

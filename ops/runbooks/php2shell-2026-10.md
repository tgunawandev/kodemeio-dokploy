# PHP2Shell (CVE-2026-109618 / CVE-2026-109619) — 2026-10-22 response

Unauthenticated RCE in PHP request handling (FPM, mod_php, cgi). Fixed in
PHP 8.5.12 / 8.4.27 / 8.3.36 / 8.2.35, released 2026-10-21.

## Sweep

Every running container on tpp-prod-01…07, kod-ovh-01 and kod-hzc-01 was
checked with `docker exec <c> php -r 'echo PHP_VERSION;'`. Only the services
below run PHP. GLPI (tpp-prod-01) had no container anywhere and
glpi.idtpp.com already answered 503. Shlink is not running.

| Service | Host | Before | After | How |
|---|---|---|---|---|
| tpp-infra-nextcloud | tpp-prod-04 | nextcloud:34.0.3-apache, PHP 8.5.10 | 34.0.5-apache, PHP 8.5.12 | kodemeio-nextcloud#1 + Dokploy env `NEXTCLOUD_VERSION=34.0.5`, autodeploy |
| tpp-infra-mailcow | tpp-prod-01 | phpfpm:8.2.29-2 | phpfpm:8.2.35-1 | host edit of the clone's `mailcow/docker-compose.yml`, `up -d --no-deps php-fpm-mailcow` |
| kod-infra-mailcow (rollback target) | kod-ovh-01 | phpfpm:8.2.29-3 | phpfpm:8.2.35-1 | host edit of `/opt/mailcow-dockerized/docker-compose.yml`, same recreate |
| kod-infra-stalwart → roundcube | kod-hzc-01 | roundcubemail:1.7.3-apache, PHP 8.4.25 | 1.7.5-apache, PHP 8.4.27 | Dokploy stored raw compose + host copy + `deploys/composes/stalwart`, `up -d --no-deps roundcube` |

Before each single-service recreate, `docker compose config --hash <svc>` was
compared with the running container's `com.docker.compose.config-hash`, and
both matched. So the only change was the image tag.

## Stopgaps still in place

`session.upload_progress.enabled = Off` was applied first (partial mitigation
only). It is harmless, but remove it in a later change:

- TPP Mailcow: appended to the clone's `data/conf/phpfpm/php-conf.d/other.ini`.
- kod Mailcow: appended to `/opt/mailcow-dockerized/data/conf/phpfpm/php-conf.d/other.ini`.
- Nextcloud and Roundcube: these overrides lived in the container filesystem and
  were dropped when the containers were recreated on the patched images.

## Backups taken

- `tpp-prod-01:/root/backups/php2shell/nextcloud-pre-34.0.5-20261022.dump` (pg_dump -Fc)
- `kod-hzc-01:/root/backups/php2shell/roundcube-db-pre-1.7.5-20261022.tgz`
  and `stalwart-compose-pre-20261022.yml`
- `*.bak-php2shell-20261022` next to every edited mailcow compose or ini file.

## Follow-ups

1. TPP Mailcow: migrate to `/opt/mailcow-dockerized` at release `2026-10`
   (planned window). Until then, never redeploy it from Dokploy (see the header
   of `tpp-infra-mailcow.yaml`).
2. kod Mailcow: retire on 2026-10-25 as planned.
3. GLPI: decide whether to remove the glpi.idtpp.com DNS record and `tpp-infra-glpi.yaml`.
4. `kctl-pg -p idtpp backup dump` (0.1.0) targets Dokploy's internal postgres,
   not kodemeio-postgres. Upgrade it before relying on it.

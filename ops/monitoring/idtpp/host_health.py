"""Push reviewed Compose service state to native Gatus external endpoints.

No container environment, logs, credentials, or arbitrary commands are collected.
Docker health checks are honored; absent checks mean runtime state only.
"""
import concurrent.futures
import json
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

FORMAT = ('{"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
          '"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
          '"status":{{json .State.Status}},"exit":{{.State.ExitCode}},'
          '"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}}}')


def inventory():
    ids = subprocess.run(['docker', 'ps', '-aq'], capture_output=True, text=True,
                         check=True, timeout=8).stdout.split()
    if not ids:
        return []
    # A container may disappear after ps. Docker still prints surviving rows
    # with a nonzero exit status; retain them so only the missing stack fails.
    result = subprocess.run(['docker', 'inspect', '--format', FORMAT, *ids],
                            capture_output=True, text=True, check=False, timeout=10)
    return [json.loads(line) for line in result.stdout.splitlines()]


def evaluate(rows, target):
    found = {}
    for row in rows:
        if row['project'] == target['project']:
            found.setdefault(row['service'], []).append(row)
    # Only previously confirmed successful init jobs are exempt from runtime checks.
    for service in target['services']:
        replicas = found.get(service, [])
        if not replicas or not any(r['status'] == 'running' and r['health'] in (None, 'healthy')
                                   for r in replicas):
            return False
    for service in target.get('init_services', []):
        if not found.get(service) or any(r['status'] != 'exited' or r['exit'] != 0 for r in found[service]):
            return False
    for service, replicas in found.items():
        if service not in target.get('init_services', []) and any(
            r['status'] != 'running' or r['health'] not in (None, 'healthy') for r in replicas
        ):
            return False
    return bool(target['services'])


def push(config, target, success):
    query = urllib.parse.urlencode({'success': str(success).lower(),
                                    'error': '' if success else 'Container readiness check failed'})
    url = config['url'] + '/api/v1/endpoints/' + target['key'] + '/external?' + query
    req = urllib.request.Request(url, data=b'', method='POST',
                                 headers={'Authorization': 'Bearer ' + config['token']})
    try:
        with urllib.request.urlopen(req, timeout=6) as response:
            return response.status == 200
    except Exception:
        return False


def main():
    config = json.loads(Path('/etc/kodemeio/gatus-exporter/config.json').read_text())
    try:
        rows = inventory()
    except Exception:
        rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda t: push(config, t, evaluate(rows, t)), config['targets']))
    print(json.dumps({'checks': len(results), 'delivered': sum(results)}))
    if not all(results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()

import importlib.util
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('host_health', Path(__file__).parents[1] / 'host_health.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class HealthTest(unittest.TestCase):
    def setUp(self):
        self.target = {'project': 'fixed', 'services': ['postgres'], 'init_services': []}
        self.row = {'project': 'fixed', 'service': 'postgres', 'status': 'running', 'health': 'healthy', 'exit': 0}
    def test_missing_container_fails(self):
        self.assertFalse(module.evaluate([], self.target))
    def test_unhealthy_or_stopped_fails(self):
        for update in [{'health':'unhealthy'}, {'health':'starting'}, {'status':'exited'}]:
            self.assertFalse(module.evaluate([self.row | update], self.target))
    def test_other_project_cannot_satisfy_check(self):
        self.assertFalse(module.evaluate([self.row | {'project':'other'}], self.target))
    def test_failed_init_fails(self):
        target = self.target | {'init_services':['init']}
        init = self.row | {'service':'init','status':'exited','health':None}
        self.assertTrue(module.evaluate([self.row, init], target))
        self.assertFalse(module.evaluate([self.row, init | {'exit':1}], target))
    def test_extra_failed_service_fails(self):
        self.assertFalse(module.evaluate([self.row, self.row | {'service':'worker','status':'exited'}], self.target))
    def test_container_disappearing_does_not_fail_other_stack(self):
        completed = [
            subprocess.CompletedProcess([], 0, stdout='surviving vanished\n'),
            subprocess.CompletedProcess([], 1, stdout=json.dumps(self.row) + '\n',
                                        stderr='Error: No such object: vanished'),
        ]
        with patch.object(module.subprocess, 'run', side_effect=completed) as run:
            rows = module.inventory()
        self.assertTrue(module.evaluate(rows, self.target))
        self.assertFalse(module.evaluate(rows, self.target | {'project':'missing'}))
        self.assertFalse(run.call_args.kwargs['check'])
    def test_complete_inspect_failure_fails_closed(self):
        completed = [
            subprocess.CompletedProcess([], 0, stdout='unavailable\n'),
            subprocess.CompletedProcess([], 1, stdout='', stderr='daemon unavailable'),
        ]
        with patch.object(module.subprocess, 'run', side_effect=completed):
            self.assertFalse(module.evaluate(module.inventory(), self.target))

class AlertIdentityTest(unittest.TestCase):
    def test_distinct_check_signals_are_mapped_without_changing_native_keys(self):
        base = Path(__file__).parents[1]
        config = json.loads((base / 'gatus.json').read_text())
        services = json.loads((base / 'services.json').read_text())
        signals = []
        for endpoint in config['endpoints'] + config['external-endpoints']:
            for alert in endpoint['alerts']:
                body = alert.get('provider-override', {}).get(
                    'body', config['alerting']['custom']['body'])
                signal = json.loads(body.replace('[ENDPOINT_NAME]', endpoint['name']))['endpoint']
                signals.append(signal)
        self.assertEqual(len(signals), 61)
        self.assertEqual(len(set(signals)), 61)
        mapped = [alias for service in services.values() for alias in service.get('gatus_endpoints', [])]
        for signal in signals:
            self.assertEqual(mapped.count(signal), 1, signal)
        runtime = next(e for e in config['external-endpoints'] if e['name'] == 'tpp-react-erp')
        self.assertEqual(runtime['group'], 'TPP Runtime')
        self.assertEqual(services['tpp-react-erp']['gatus_endpoints'],
                         ['tpp-react-erp', 'tpp-react-erp-runtime'])
    def test_missing_heartbeat_detected_within_180_seconds(self):
        config = json.loads((Path(__file__).parents[1] / 'gatus.json').read_text())
        for endpoint in config['external-endpoints']:
            self.assertEqual(endpoint['heartbeat']['interval'], '90s')

if __name__ == '__main__':
    unittest.main()

import importlib.util
import unittest
from pathlib import Path

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

if __name__ == '__main__':
    unittest.main()

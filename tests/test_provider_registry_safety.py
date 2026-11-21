import threading
import time
import types
import unittest
from unittest.mock import patch

from providers import registry


class ProviderRegistrySafetyTests(unittest.TestCase):
    def setUp(self):
        self.declarations = dict(registry._declarations)
        self.instances = dict(registry._instances)
        self.failures = dict(registry._failed)
        self.modules = dict(registry._MODULES)
        registry._declarations.clear()
        registry._instances.clear()
        registry._failed.clear()
        registry._MODULES = {"llm": ("test_provider",)}

    def tearDown(self):
        registry._declarations.clear()
        registry._declarations.update(self.declarations)
        registry._instances.clear()
        registry._instances.update(self.instances)
        registry._failed.clear()
        registry._failed.update(self.failures)
        registry._MODULES = self.modules

    @staticmethod
    def _module(factory, available=lambda: True):
        return types.SimpleNamespace(
            PROVIDER={
                "id": "test_provider",
                "kind": "llm",
                "label": "Test provider",
                "factory": factory,
            },
            available=available,
        )

    def test_availability_exception_is_recorded_and_skipped(self):
        def unavailable():
            raise RuntimeError("dependency probe failed")

        module = self._module(lambda _cfg: object(), unavailable)
        with patch.object(registry.importlib, "import_module", return_value=module):
            self.assertEqual(registry.discover("llm"), [])

        self.assertIn("test_provider", registry.failures())
        self.assertIn("dependency probe failed", registry.failures()["test_provider"])

    def test_same_configuration_is_constructed_once_across_threads(self):
        created = []
        created_lock = threading.Lock()

        class Instance:
            pass

        def factory(_cfg):
            time.sleep(0.05)
            item = Instance()
            with created_lock:
                created.append(item)
            return item

        module = self._module(factory)
        results = []
        errors = []

        def build():
            try:
                results.append(registry.build("test_provider", {"model": "local"}))
            except Exception as exc:  # surfaced in the test thread
                errors.append(exc)

        with patch.object(registry.importlib, "import_module", return_value=module):
            threads = [threading.Thread(target=build) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)

        self.assertFalse(errors)
        self.assertEqual(len(created), 1)
        self.assertEqual(len(results), 2)
        self.assertIs(results[0], results[1])


if __name__ == "__main__":
    unittest.main()

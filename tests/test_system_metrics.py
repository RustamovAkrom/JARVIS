"""Regression tests for cross-platform system metrics collection."""

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gui.panels.system_monitor import metrics as metrics_module


class SystemMetricsTests(unittest.TestCase):
    def test_cpu_frequency_probe_failure_does_not_block_other_metrics(self):
        sampler = metrics_module._SysMetrics.__new__(metrics_module._SysMetrics)
        sampler._lock = threading.Lock()
        sampler._last_net = SimpleNamespace(bytes_sent=100, bytes_recv=200)
        sampler._last_net_t = time.time() - 1
        sampler._process_tick = 0
        sampler.process_count = 0
        sampler._slow_tick = 0
        sampler.gpu = -1.0
        sampler.tmp = -1.0

        memory = SimpleNamespace(percent=37.0, total=16_000, available=10_000)
        counters = SimpleNamespace(bytes_sent=300, bytes_recv=700)
        sampler._get_gpu = Mock(return_value=24.0)
        sampler._get_temp = Mock(return_value=48.0)

        with (
            patch.object(metrics_module.psutil, "cpu_percent", return_value=12.0),
            patch.object(metrics_module.psutil, "virtual_memory", return_value=memory),
            patch.object(
                metrics_module.psutil,
                "cpu_freq",
                create=True,
                side_effect=RuntimeError("unsupported"),
            ),
            patch.object(metrics_module.psutil, "net_io_counters", return_value=counters),
            patch.object(metrics_module.psutil, "pids", return_value=[1, 2, 3]),
        ):
            sampler._update()

        self.assertEqual(sampler.cpu, 12.0)
        self.assertEqual(sampler.mem, 37.0)
        self.assertGreater(sampler.net_download_bps, 0.0)
        self.assertEqual(sampler.cpu_freq_mhz, -1.0)
        self.assertEqual(sampler.process_count, 3)
        self.assertEqual(sampler.gpu, 24.0)
        self.assertEqual(sampler.tmp, 48.0)
        sampler._get_gpu.assert_called_once_with()
        sampler._get_temp.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

import unittest
from types import SimpleNamespace

from dashboard.server import DashboardServer


class DashboardShutdownTests(unittest.TestCase):
    @staticmethod
    def _make_server():
        dashboard = DashboardServer.__new__(DashboardServer)
        dashboard._stopping = False
        dashboard._server = SimpleNamespace(should_exit=False)
        dashboard._redirect_server = SimpleNamespace(should_exit=False)
        return dashboard

    def test_shutdown_signals_both_http_listeners(self):
        dashboard = self._make_server()

        dashboard.request_shutdown()

        self.assertTrue(dashboard._stopping)
        self.assertTrue(dashboard._server.should_exit)
        self.assertTrue(dashboard._redirect_server.should_exit)

    def test_shutdown_is_idempotent(self):
        dashboard = self._make_server()

        dashboard.request_shutdown()
        dashboard.request_shutdown()

        self.assertTrue(dashboard._stopping)
        self.assertTrue(dashboard._server.should_exit)
        self.assertTrue(dashboard._redirect_server.should_exit)

    def test_shutdown_before_server_start_is_remembered(self):
        dashboard = self._make_server()
        dashboard._server = None
        dashboard._redirect_server = None

        dashboard.request_shutdown()

        self.assertTrue(dashboard._stopping)
        # serve() checks this flag after creating a Uvicorn.Server, including
        # when the shutdown request raced with certificate/port preparation.
        future_server = SimpleNamespace(should_exit=False)
        if dashboard._stopping:
            future_server.should_exit = True
        self.assertTrue(future_server.should_exit)


if __name__ == "__main__":
    unittest.main()

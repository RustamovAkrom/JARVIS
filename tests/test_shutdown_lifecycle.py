import threading
import unittest

from gui.app.main_window import JarvisUI


class ShutdownLifecycleTests(unittest.TestCase):
    @staticmethod
    def _make_ui():
        ui = JarvisUI.__new__(JarvisUI)
        ui._shutdown_services_lock = threading.Lock()
        ui._shutdown_services_started = False
        ui._shutdown_services_thread = None
        return ui

    def test_about_to_quit_does_not_wait_for_worker_cleanup(self):
        ui = self._make_ui()
        cleanup_started = threading.Event()
        allow_cleanup_to_finish = threading.Event()
        callback_thread_id = []
        cleanup_thread_id = []

        def slow_cleanup():
            cleanup_thread_id.append(threading.get_ident())
            cleanup_started.set()
            allow_cleanup_to_finish.wait(timeout=2)

        ui._shutdown_camera_services_impl = slow_cleanup

        def invoke_qt_callback():
            callback_thread_id.append(threading.get_ident())
            ui._shutdown_camera_services()

        callback_thread = threading.Thread(target=invoke_qt_callback)
        callback_thread.start()
        self.assertTrue(cleanup_started.wait(timeout=1))
        callback_thread.join(timeout=1)
        self.assertFalse(
            callback_thread.is_alive(),
            "aboutToQuit waited for camera workers on the Qt thread",
        )

        allow_cleanup_to_finish.set()
        worker = ui._shutdown_services_thread
        self.assertIsNotNone(worker)
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertNotEqual(cleanup_thread_id[0], callback_thread_id[0])
        self.assertTrue(worker.daemon)

    def test_assistant_shutdown_cleans_once_on_a_worker_thread(self):
        ui = self._make_ui()
        cleanup_thread_ids = []
        cleanup_finished = threading.Event()
        caller_thread_id = threading.get_ident()

        def cleanup():
            cleanup_thread_ids.append(threading.get_ident())
            cleanup_finished.set()

        ui._shutdown_camera_services_impl = cleanup
        ui.shutdown_services(timeout=1.0)
        self.assertTrue(cleanup_finished.wait(timeout=1))
        worker = ui._shutdown_services_thread
        self.assertIsNotNone(worker)
        worker.join(timeout=1)
        ui._shutdown_camera_services()

        self.assertEqual(len(cleanup_thread_ids), 1)
        self.assertNotEqual(cleanup_thread_ids[0], caller_thread_id)
        self.assertTrue(worker.daemon)


if __name__ == "__main__":
    unittest.main()

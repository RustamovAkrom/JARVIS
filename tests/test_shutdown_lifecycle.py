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

    def test_assistant_shutdown_cleans_once_before_qt_quit(self):
        ui = self._make_ui()
        cleanup_thread_ids = []
        caller_thread_id = threading.get_ident()

        def cleanup():
            cleanup_thread_ids.append(threading.get_ident())

        ui._shutdown_camera_services_impl = cleanup
        ui.shutdown_services()
        ui._shutdown_camera_services()

        self.assertEqual(cleanup_thread_ids, [caller_thread_id])
        self.assertIsNone(ui._shutdown_services_thread)


if __name__ == "__main__":
    unittest.main()

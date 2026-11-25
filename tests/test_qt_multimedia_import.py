import unittest


class QtMultimediaImportTests(unittest.TestCase):
    def test_multimedia_wrapper_exports_qt_player_class(self):
        from core.qt.QtMultimedia import QMediaPlayer

        self.assertTrue(callable(QMediaPlayer))


if __name__ == "__main__":
    unittest.main()

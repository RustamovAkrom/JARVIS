import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from io import BytesIO

from PIL import Image
from PyQt6.QtCore import QPointF, QRect, Qt
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QApplication

from ui import ScreenRegionSelector


class _MouseEvent:
    def __init__(self, x, y, button=Qt.MouseButton.LeftButton):
        self._position = QPointF(x, y)
        self._button = button
        self.accepted = False

    def position(self):
        return self._position

    def button(self):
        return self._button

    def accept(self):
        self.accepted = True


class _KeyEvent:
    def key(self):
        return Qt.Key.Key_Escape

    def accept(self):
        pass


class ScreenRegionSelectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_drag_returns_only_the_selected_jpeg_crop(self):
        screenshot = QPixmap(200, 100)
        screenshot.fill(QColor(20, 80, 140))
        captured = []
        cancelled = []
        selector = ScreenRegionSelector(
            screenshot, QRect(0, 0, 200, 100), captured.append, cancelled.append
        )

        selector.mousePressEvent(_MouseEvent(20, 10))
        selector.mouseMoveEvent(_MouseEvent(120, 60))
        selector.mouseReleaseEvent(_MouseEvent(120, 60))

        self.assertEqual(len(captured), 1)
        self.assertEqual(cancelled, [])
        self.assertTrue(captured[0].startswith(b"\xff\xd8"))
        with Image.open(BytesIO(captured[0])) as crop:
            self.assertEqual(crop.size, (101, 51))

    def test_escape_cancels_without_returning_an_image(self):
        screenshot = QPixmap(64, 64)
        cancelled = []
        selector = ScreenRegionSelector(
            screenshot,
            QRect(0, 0, 64, 64),
            lambda data: self.fail("unexpected crop"),
            cancelled.append,
        )
        selector.keyPressEvent(_KeyEvent())
        self.assertEqual(cancelled, [""])

    def test_selected_region_is_bright_and_outside_is_dimmed(self):
        screenshot = QPixmap(160, 100)
        screenshot.fill(QColor(180, 180, 180))
        selector = ScreenRegionSelector(
            screenshot, QRect(0, 0, 160, 100), lambda _data: None, lambda _msg: None
        )
        selector._selection = QRect(60, 40, 50, 35)
        rendered = QPixmap(selector.size())
        rendered.fill(Qt.GlobalColor.transparent)
        selector.render(rendered)

        image = rendered.toImage()
        outside = image.pixelColor(30, 70).lightness()
        inside = image.pixelColor(80, 60).lightness()
        self.assertGreater(inside, outside + 40)


if __name__ == "__main__":
    unittest.main()

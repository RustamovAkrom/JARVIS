"""Native chat composer with keyboard and image-paste support."""

from pathlib import Path

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QKeySequence
from PyQt6.QtWidgets import QApplication, QLineEdit


class ChatInput(QLineEdit):
    """Command field that accepts dropped files and pasted screenshots."""

    file_dropped = pyqtSignal(str)
    image_pasted = pyqtSignal(bytes)
    image_paste_failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Paste):
            clipboard = QApplication.clipboard()
            if clipboard.mimeData().hasImage():
                image = clipboard.image()
                if image.isNull():
                    self.image_paste_failed.emit(
                        "The clipboard contains an unreadable image."
                    )
                    event.accept()
                    return
                if image.width() > 1280 or image.height() > 720:
                    image = image.scaled(
                        QSize(1280, 720),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                data = QByteArray()
                buffer = QBuffer(data)
                if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
                    self.image_paste_failed.emit(
                        "Could not prepare the clipboard image."
                    )
                    event.accept()
                    return
                saved = image.save(buffer, "JPEG", 82)
                buffer.close()
                if not saved:
                    self.image_paste_failed.emit(
                        "Could not encode the clipboard image."
                    )
                else:
                    self.image_pasted.emit(bytes(data))
                event.accept()
                return
        super().keyPressEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls() and any(
            u.isLocalFile() for u in event.mimeData().urls()
        ):
            event.acceptProposedAction()
            self.setProperty("draggingFile", True)
            self.style().unpolish(self)
            self.style().polish(self)
        else:
            super().dragEnterEvent(event)

    def dragLeaveEvent(self, event):
        self.setProperty("draggingFile", False)
        self.style().unpolish(self)
        self.style().polish(self)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent):
        self.setProperty("draggingFile", False)
        self.style().unpolish(self)
        self.style().polish(self)
        paths = [
            u.toLocalFile()
            for u in event.mimeData().urls()
            if u.isLocalFile() and Path(u.toLocalFile()).is_file()
        ]
        if paths:
            self.file_dropped.emit(paths[0])
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

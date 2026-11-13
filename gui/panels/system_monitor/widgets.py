"""Custom-painted gauges and trend charts for system telemetry."""

import math

from PyQt6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation
from PyQt6.QtGui import QBrush, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from gui.themes.tokens import C, qcol
from gui.themes.typography import FONT_MONO, FONT_UI


class MetricGauge(QWidget):

    def __init__(self, label: str, color: str = C.PRI, detail: str = "", parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._detail = detail
        self._value = 0.0
        self._display_value = 0.0
        self._text = "--"
        self.setFixedSize(84, 84)
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(520)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._set_display_value)

    def set_value(self, pct: float, text: str):
        v = max(0.0, min(100.0, float(pct)))
        if v == self._value and text == self._text:
            return
        self._value = v
        self._text = text
        self._animation.stop()
        self._animation.setStartValue(self._display_value)
        self._animation.setEndValue(v)
        self._animation.start()
        self.update()

    def _set_display_value(self, value):
        self._display_value = float(value)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        outer = QRectF(2.5, 2.5, W - 5, H - 5)
        ring = QRectF(9, 9, W - 18, H - 18)

        if self._value > 85:
            bar_col = qcol(C.RED)
        elif self._value > 65:
            bar_col = qcol(C.ACC)
        else:
            bar_col = qcol(self._color)

        p.setPen(QPen(qcol(C.BORDER), 1))
        p.setBrush(QBrush(qcol(C.PANEL2)))
        p.drawEllipse(outer)

        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(
            QPen(qcol(C.BORDER_B), 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        )
        p.drawArc(ring, 90 * 16, -360 * 16)

        for tick in range(24):
            angle = math.radians(tick * 15 - 90)
            major = tick % 6 == 0
            start_r = 36.5 if major else 37.5
            end_r = 39.2
            p.setPen(
                QPen(
                    qcol(self._color, 155 if major else 72),
                    1.4 if major else 1,
                )
            )
            p.drawLine(
                QPointF(cx + start_r * math.cos(angle), cy + start_r * math.sin(angle)),
                QPointF(cx + end_r * math.cos(angle), cy + end_r * math.sin(angle)),
            )

        progress = max(0.0, min(100.0, self._display_value))
        if progress > 0:
            span = -round(360 * progress * 16 / 100)
            p.setPen(
                QPen(
                    qcol(self._color, 34),
                    11,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                )
            )
            p.drawArc(ring, 90 * 16, span)
            p.setPen(QPen(bar_col, 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(ring, 90 * 16, span)

            angle = math.radians(90 - 360 * progress / 100)
            end_x = cx + (ring.width() / 2) * math.cos(angle)
            end_y = cy - (ring.height() / 2) * math.sin(angle)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(bar_col))
            p.drawEllipse(QPointF(end_x, end_y), 2.5, 2.5)

        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.setFont(QFont(FONT_UI, 9, QFont.Weight.DemiBold))
        p.drawText(
            QRectF(5, cy - 13, W - 10, 11),
            Qt.AlignmentFlag.AlignCenter,
            self._label.upper(),
        )

        value_size = 11 if len(self._text) <= 5 else 8
        value_font = QFont(FONT_MONO, value_size, QFont.Weight.Bold)
        while (
            value_size > 7
            and QFontMetricsF(value_font).horizontalAdvance(self._text) > W - 16
        ):
            value_size -= 1
            value_font.setPointSize(value_size)
        p.setFont(value_font)
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(
            QRectF(5, cy - 2, W - 10, 16), Qt.AlignmentFlag.AlignCenter, self._text
        )

        detail_font = QFont(FONT_UI, 8)
        while (
            QFontMetricsF(detail_font).horizontalAdvance(self._detail) > W - 16
            and detail_font.pointSize() > 7
        ):
            detail_font.setPointSize(detail_font.pointSize() - 1)
        p.setFont(detail_font)
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(
            QRectF(5, cy + 14, W - 10, 9),
            Qt.AlignmentFlag.AlignCenter,
            self._detail.upper(),
        )

        p.end()


class MetricTrendChart(QWidget):
    def __init__(self, title: str, empty_text: str, memory_label: str, parent=None):
        super().__init__(parent)
        self._title = title
        self._empty_text = empty_text
        self._memory_label = memory_label
        self._samples: list[tuple[float, float]] = []
        self.setMinimumHeight(100)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_values(self, cpu: float, memory: float):
        sample = (
            max(0.0, min(100.0, float(cpu))),
            max(0.0, min(100.0, float(memory))),
        )
        self._samples.append(sample)
        del self._samples[:-30]
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        frame = QRectF(0.5, 0.5, W - 1, H - 1)
        p.setPen(QPen(qcol(C.BORDER), 1))
        p.setBrush(QBrush(qcol(C.PANEL2)))
        p.drawRoundedRect(frame, 7, 7)

        p.setFont(QFont(FONT_MONO, 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_MED), 1))
        p.drawText(
            QRectF(10, 7, max(50, W - 130), 14),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._title,
        )

        legend_x = W - 104
        for color, label in ((C.GREEN, "CPU"), (C.ACC2, self._memory_label)):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(qcol(color)))
            p.drawEllipse(QPointF(legend_x, 14), 2.5, 2.5)
            p.setFont(QFont(FONT_MONO, 6, QFont.Weight.Bold))
            p.setPen(QPen(qcol(C.TEXT_DIM), 1))
            p.drawText(
                QRectF(legend_x + 5, 7, 31, 14),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                label,
            )
            legend_x += 43

        plot = QRectF(31, 29, W - 42, H - 50)
        p.setFont(QFont(FONT_MONO, 5))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        for value in (100, 50, 0):
            y = plot.bottom() - plot.height() * value / 100
            p.drawText(
                QRectF(5, y - 5, 21, 10),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                str(value),
            )
            p.setPen(QPen(qcol(C.BORDER, 170), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        for division in range(1, 6):
            x = plot.left() + plot.width() * division / 6
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(
            QRectF(plot.left(), H - 16, 36, 10),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "-60s",
        )
        p.drawText(
            QRectF(plot.right() - 30, H - 16, 30, 10),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            "NOW",
        )

        if len(self._samples) < 2:
            p.setFont(QFont(FONT_MONO, 6))
            p.setPen(QPen(qcol(C.TEXT_DIM), 1))
            p.drawText(plot, Qt.AlignmentFlag.AlignCenter, self._empty_text)
            p.end()
            return

        for index, color in ((0, C.GREEN), (1, C.ACC2)):
            points = [
                QPointF(
                    plot.left() + plot.width() * i / (len(self._samples) - 1),
                    plot.bottom() - plot.height() * sample[index] / 100,
                )
                for i, sample in enumerate(self._samples)
            ]
            line = QPainterPath(points[0])
            for point in points[1:]:
                line.lineTo(point)

            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(
                QPen(qcol(color, 42), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            )
            p.drawPath(line)
            p.setPen(QPen(qcol(color, 215), 1.6))
            p.drawPath(line)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(qcol(color)))
            p.drawEllipse(points[-1], 2.2, 2.2)

        p.end()

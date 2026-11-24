"""Centralized, deterministic theme application for the JARVIS Qt UI.

Every widget stylesheet is kept as a source snapshot paired with the semantic
palette that was active when it was authored. Theme changes are always rendered
from that snapshot, never by repeatedly replacing colours in already-mutated QSS.
Custom-painted widgets can implement apply_jarvis_theme() to refresh cached
paint colours after the semantic tokens change.
"""
from __future__ import annotations

import re
import weakref
from collections import defaultdict
from typing import Callable, Iterable

from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication, QWidget

from gui.themes.tokens import (
    C,
    apply_ui_accent,
    apply_ui_theme,
    current_palette,
)


class ThemeManager(QObject):
    """Single owner of live theme transitions, Qt palettes and widget refresh."""

    _instances = weakref.WeakKeyDictionary()

    def __init__(self, app: QApplication):
        super().__init__(app)
        self._app = app
        self._applying = False
        self._sources = weakref.WeakKeyDictionary()
        self._app.installEventFilter(self)
        self._capture_all()

    @classmethod
    def for_app(cls, app: QApplication | None = None) -> "ThemeManager":
        selected_app = app or QApplication.instance()
        if selected_app is None:
            raise RuntimeError("ThemeManager requires an active QApplication")
        manager = cls._instances.get(selected_app)
        if manager is None:
            manager = cls(selected_app)
            cls._instances[selected_app] = manager
        return manager

    def eventFilter(self, watched, event):
        if (
            not self._applying
            and event.type() == QEvent.Type.StyleChange
            and isinstance(watched, QWidget)
        ):
            self._capture_widget(watched)
        return False

    def _capture_widget(self, widget: QWidget) -> None:
        try:
            self._sources[widget] = (widget.styleSheet(), current_palette())
        except RuntimeError:
            self._sources.pop(widget, None)

    def _capture_all(self) -> None:
        for widget in self._app.allWidgets():
            self._capture_widget(widget)

    @staticmethod
    def _replacement_map(
        source_palette: dict[str, str], target_palette: dict[str, str]
    ) -> tuple[dict[str, str], dict[str, str]]:
        # A literal can represent several semantic tokens (all surfaces are
        # intentionally black in Black mode). Resolve unique values globally
        # and ambiguous values from their QSS property below.
        destinations: dict[str, set[str]] = defaultdict(set)
        for key, source in source_palette.items():
            target = target_palette.get(key)
            if source and target and source.lower() != target.lower():
                destinations[source.lower()].add(target.lower())
        unique = {
            source: next(iter(targets))
            for source, targets in destinations.items()
            if len(targets) == 1
        }
        ambiguous = {
            source: ",".join(sorted(targets))
            for source, targets in destinations.items()
            if len(targets) > 1
        }
        return unique, ambiguous

    def _render_widget(
        self, widget: QWidget, source: str, palette: dict[str, str]
    ) -> None:
        target_palette = current_palette()
        replacements, ambiguous = self._replacement_map(palette, target_palette)
        rendered = source
        if replacements:
            pattern = re.compile(
                "|".join(
                    re.escape(value)
                    for value in sorted(replacements, key=len, reverse=True)
                ),
                re.IGNORECASE,
            )
            rendered = pattern.sub(
                lambda match: replacements[match.group(0).lower()], rendered
            )

        if ambiguous:
            property_roles = {
                "background": "PANEL2",
                "background-color": "PANEL2",
                "color": "TEXT",
                "border": "BORDER",
                "border-color": "BORDER",
                "border-top": "BORDER",
                "border-right": "BORDER",
                "border-bottom": "BORDER",
                "border-left": "BORDER",
                "outline": "BORDER",
                "outline-color": "BORDER",
                "selection-background-color": "PRI_GHO",
                "selection-color": "TEXT",
            }
            declaration = re.compile(
                r"(?P<property>selection-background-color|selection-color|"
                r"background(?:-color)?|border(?:-(?:top|right|bottom|left))?"
                r"(?:-color)?|outline(?:-color)?|color)\s*:\s*"
                r"(?P<value>[^;{}]+)",
                re.IGNORECASE,
            )

            def resolve(match):
                role = property_roles.get(match.group("property").lower())
                target = target_palette.get(role, "") if role else ""
                value = match.group("value")
                if not target:
                    return match.group(0)
                value_pattern = re.compile(r"#[0-9a-fA-F]{6}")
                return (
                    match.group("property")
                    + ": "
                    + value_pattern.sub(
                        lambda color: target
                        if color.group(0).lower() in ambiguous
                        else color.group(0),
                        value,
                    )
                )

            rendered = declaration.sub(resolve, rendered)

        if widget.styleSheet() != rendered:
            widget.setStyleSheet(rendered)

    def retheme(self, old: dict[str, str], new: dict[str, str]) -> None:
        """Compatibility bridge for legacy callers; new code should use apply()."""
        destinations: dict[str, set[str]] = defaultdict(set)
        for key, source in old.items():
            target = new.get(key, source)
            if source and target and source.lower() != target.lower():
                destinations[source.lower()].add(target.lower())
        replacements = {
            source: next(iter(targets))
            for source, targets in destinations.items()
            if len(targets) == 1
        }
        if not replacements:
            return
        pattern = re.compile(
            "|".join(re.escape(value) for value in sorted(replacements, key=len, reverse=True)),
            re.IGNORECASE,
        )
        self._applying = True
        try:
            for widget in self._app.allWidgets():
                source = widget.styleSheet()
                rendered = pattern.sub(
                    lambda match: replacements[match.group(0).lower()], source
                ) if source else source
                if rendered != source:
                    widget.setStyleSheet(rendered)
                widget.update()
        finally:
            self._applying = False
        self._capture_all()

    def _apply_native_palette(self) -> None:
        palette = self._app.palette()
        roles = {
            QPalette.ColorRole.Window: C.BG,
            QPalette.ColorRole.WindowText: C.TEXT,
            QPalette.ColorRole.Base: C.PANEL2,
            QPalette.ColorRole.AlternateBase: C.PANEL2,
            QPalette.ColorRole.ToolTipBase: C.PANEL,
            QPalette.ColorRole.ToolTipText: C.TEXT,
            QPalette.ColorRole.Text: C.TEXT,
            QPalette.ColorRole.Button: C.PANEL,
            QPalette.ColorRole.ButtonText: C.TEXT,
            QPalette.ColorRole.BrightText: C.PRI,
            QPalette.ColorRole.Highlight: C.PRI,
            QPalette.ColorRole.HighlightedText: C.DARK,
            QPalette.ColorRole.Link: C.PRI,
            QPalette.ColorRole.LinkVisited: C.PRI,
        }
        for role, value in roles.items():
            palette.setColor(role, QColor(value))
        self._app.setPalette(palette)

    def apply(
        self,
        *,
        accent: str | None = None,
        mode: str | None = None,
        refresh_callbacks: Iterable[Callable[[], object]] = (),
    ) -> bool:
        """Apply one atomic theme transition and refresh all theme-aware UI."""
        if accent is not None and not re.fullmatch(r"#[0-9a-fA-F]{6}", accent.strip()):
            return False
        if mode is not None and str(mode).strip().lower() not in {
            "dark", "light", "black", "white"
        }:
            return False

        self._applying = True
        try:
            if mode is not None and not apply_ui_theme(mode):
                return False
            if accent is not None and not apply_ui_accent(accent):
                return False
            for widget, (source, source_palette) in list(self._sources.items()):
                try:
                    self._render_widget(widget, source, source_palette)
                except RuntimeError:
                    self._sources.pop(widget, None)
            self._apply_native_palette()
            for widget in self._app.allWidgets():
                widget.update()
        finally:
            self._applying = False

        # Complex layout-specific QSS is rebuilt by its owning window.
        for callback in refresh_callbacks:
            try:
                callback()
            except Exception as exc:
                print(f"[Theme] owner refresh failed: {exc}")

        # Custom-painted controls refresh cached semantic colours explicitly.
        for widget in self._app.allWidgets():
            refresh = getattr(widget, "apply_jarvis_theme", None)
            if callable(refresh):
                try:
                    refresh()
                except Exception as exc:
                    print(f"[Theme] widget refresh failed for {type(widget).__name__}: {exc}")
        self._app.sendPostedEvents(None, QEvent.Type.UpdateRequest)
        return True

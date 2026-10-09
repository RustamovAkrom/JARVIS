import pathlib
import re
import shutil
import sys

path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "ui.py")
src = path.read_text(encoding="utf-8")


def sub1(old, new):
    global src
    n = src.count(old)
    assert n == 1, f"anchor found {n} times: {old[:70]!r}"
    src = src.replace(old, new)


def span(name):
    base = src.index("class MainWindow")
    m = re.compile(rf"^    def {name}\(", re.M).search(src, base)
    assert m, f"method not found: {name}"
    nxt = re.compile(r"^(    def |    @|    #|class |def )", re.M).search(src, m.end())
    return m.start(), (nxt.start() if nxt else len(src))


def put(name, new):
    global src
    s, e = span(name)
    src = src[:s] + new + src[e:]


def drop(name):
    put(name, "")


# ── 1. syntax ────────────────────────────────────────────────────────────────
src = src.replace("except TypeError, ValueError:", "except (TypeError, ValueError):")

# ── 2. theme at start-up: one colour model, cyan by default ──────────────────
sub1(
    '''        _mode = (_cfg.get("ui_theme") or "").strip().lower()
        if _mode not in ("dark", "light", "custom"):
            _mode = "custom" if _ui_color and _ui_color != DEFAULT_UI_COLOR else "dark"
        apply_theme(_mode, _ui_color)
''',
    "        apply_ui_color(_ui_color or DEFAULT_UI_COLOR)\n",
)

# ── 3. state used by the settings switches ───────────────────────────────────
sub1(
    "        self._ignore_clip = False\n",
    "        self._ignore_clip = False\n"
    "        self._wake_cache = None\n"
    "        self._wake_downloading = False\n",
)
sub1("        self._ctrl_drawer = self._build_controls_drawer()\n", "")

# ── 4. shortcuts / palette / close ───────────────────────────────────────────
sub1(
    '        add("Mini orb window", "F9", lambda: self.set_mini(True))\n',
    '        add("Mini orb window", "F9", lambda: self.set_mini(True))\n'
    '        add("Top dock bar", "F10", lambda: self.set_dock(not self._dock_active))\n',
)
sub1(
    '        ("Mini orb window", "F9"),\n',
    '        ("Mini orb window", "F9"),\n        ("Top dock bar", "F10"),\n',
)
sub1(
    '            self._log.append_log(f"ERR: Auto-start failed — {e}")\n',
    '            self._log.append_log(f"ERR: Auto-start failed — {e}")\n'
    "            self._update_autostart_btn(self._check_autostart())\n",
)

put(
    "closeEvent",
    '''    def closeEvent(self, e):
        if self._mini:
            self._save_mini_geom()
        if self._dock is not None:
            self._dock.close()
        super().closeEvent(e)

''',
)

# ── 5. dead controls drawer ──────────────────────────────────────────────────
drop("_build_controls_drawer")
drop("_toggle_controls")
drop("_position_ctrl_drawer")

# ── 6. settings drawer ───────────────────────────────────────────────────────
put(
    "_build_quick_drawer",
    r'''    def _build_quick_drawer(self) -> QWidget:
        """Grouped, scrollable settings drawer anchored under the gear button."""
        btn_style = f"""
            QPushButton {{
                background: rgba(255, 255, 255, 14);
                color: {C.TEXT_MED};
                border: 1px solid {C.BORDER};
                border-radius: 8px;
                text-align: left;
                padding: 0 12px;
            }}
            QPushButton:hover {{
                color: {C.PRI};
                border-color: {C.PRI_DIM};
                background: {C.PRI_GHO};
            }}
        """
        primary = f"""
            QPushButton {{
                background: {C.PRI_GHO};
                color: {C.PRI};
                border: 1px solid {C.PRI_DIM};
                border-radius: 8px;
                text-align: left;
                padding: 0 12px;
            }}
            QPushButton:hover {{ border-color: {C.PRI}; }}
        """
        self._BTN_PRI, self._BTN_DIM = primary, btn_style

        w = QWidget(self.centralWidget())
        w.setObjectName("ModernDrawer")
        w.setStyleSheet(f"""
            QWidget#ModernDrawer {{
                background: rgba(4, 14, 22, 244);
                border: 1px solid rgba(255, 255, 255, 40);
                border-radius: 16px;
            }}
        """)
        w.hide()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(14, 13, 8, 13)
        outer.setSpacing(6)

        header = QLabel("SETTINGS")
        header.setFont(QFont(_FONT, 9, QFont.Weight.Bold))
        header.setStyleSheet(
            f"color: {C.PRI}; letter-spacing: 2px; background: transparent;"
        )
        outer.addWidget(header)
        sub = QLabel("Choose a category — changes are saved automatically")
        sub.setFont(QFont(_FONT, 8))
        sub.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        outer.addWidget(sub)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{ background: transparent; width: 6px; border: none; }}
            QScrollBar::handle:vertical {{ background: rgba(255, 255, 255, 50);
                border-radius: 3px; min-height: 24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        """)
        inner = QWidget()
        inner.setObjectName("DrawerInner")
        inner.setStyleSheet("QWidget#DrawerInner { background: transparent; }")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 2, 8, 2)
        lay.setSpacing(8)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)
        self._drawer_lay = lay

        def category(title: str, detail: str, open_now: bool = False):
            head = QToolButton()
            head.setText(title)
            head.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            head.setArrowType(
                Qt.ArrowType.DownArrow if open_now else Qt.ArrowType.RightArrow
            )
            head.setCheckable(True)
            head.setChecked(open_now)
            head.setCursor(Qt.CursorShape.PointingHandCursor)
            head.setFont(QFont(_FONT, 9, QFont.Weight.Bold))
            head.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            head.setStyleSheet(f"""
                QToolButton {{ color: {C.TEXT}; background: {C.PANEL2};
                    border: 1px solid {C.BORDER}; border-radius: 9px;
                    text-align: left; padding: 9px 10px; }}
                QToolButton:hover, QToolButton:checked {{ color: {C.PRI};
                    border-color: {C.PRI_DIM}; background: {C.PRI_GHO}; }}
            """)
            lay.addWidget(head)

            body = QWidget()
            body_lay = QVBoxLayout(body)
            body_lay.setContentsMargins(8, 1, 4, 4)
            body_lay.setSpacing(5)
            hint = QLabel(detail)
            hint.setWordWrap(True)
            hint.setFont(QFont(_FONT, 7))
            hint.setStyleSheet(
                f"color: {C.TEXT_DIM}; padding: 2px 3px; background: transparent;"
            )
            body_lay.addWidget(hint)
            body.setVisible(open_now)

            def toggle(checked: bool, b=body, h=head):
                b.setVisible(checked)
                h.setArrowType(
                    Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
                )
                QTimer.singleShot(0, self._position_quick_drawer)

            head.toggled.connect(toggle)
            lay.addWidget(body)
            return body_lay

        def action_row(parent, title, description, slot, closes=False):
            row = QWidget()
            row_lay = QVBoxLayout(row)
            row_lay.setContentsMargins(2, 2, 2, 2)
            row_lay.setSpacing(1)
            button = QPushButton(title)
            button.setFixedHeight(31)
            button.setFont(QFont(_FONT, 8, QFont.Weight.Bold))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(btn_style)
            if closes:
                button.clicked.connect(
                    lambda _=False, s=slot: (self._close_setup(), s())
                )
            else:
                button.clicked.connect(lambda _=False, s=slot: s())
            row_lay.addWidget(button)
            note = QLabel(description)
            note.setWordWrap(True)
            note.setFont(QFont(_FONT, 7))
            note.setStyleSheet(
                f"color: {C.TEXT_DIM}; padding: 0 4px 3px 4px; background: transparent;"
            )
            row_lay.addWidget(note)
            parent.addWidget(row)
            button.row = row
            return button

        def switch_row(parent, title, description, checked, changed):
            row = QFrame()
            row.setObjectName("SwitchRow")
            row.setStyleSheet(f"""
                QFrame#SwitchRow {{ background: rgba(255, 255, 255, 8);
                    border: 1px solid {C.BORDER}; border-radius: 8px; }}
                QFrame#SwitchRow QLabel {{ background: transparent; border: none; }}
                QCheckBox {{ color: {C.TEXT_MED}; font-weight: bold; spacing: 7px;
                    background: transparent; }}
                QCheckBox:disabled {{ color: {C.TEXT_DIM}; }}
                QCheckBox::indicator {{ width: 34px; height: 18px; border-radius: 9px;
                    background: #06131a; border: 1px solid {C.BORDER_B}; }}
                QCheckBox::indicator:checked {{ background: {C.GREEN_D};
                    border-color: {C.GREEN}; }}
            """)
            row_lay = QVBoxLayout(row)
            row_lay.setContentsMargins(9, 6, 9, 6)
            row_lay.setSpacing(1)
            top = QHBoxLayout()
            label = QLabel(title)
            label.setFont(QFont(_FONT, 8, QFont.Weight.Bold))
            label.setStyleSheet(f"color: {C.TEXT};")
            top.addWidget(label)
            top.addStretch(1)
            toggle = QCheckBox("ON" if checked else "OFF")
            toggle.setChecked(checked)
            toggle.setCursor(Qt.CursorShape.PointingHandCursor)
            top.addWidget(toggle)
            row_lay.addLayout(top)
            note = QLabel(description)
            note.setWordWrap(True)
            note.setFont(QFont(_FONT, 7))
            note.setStyleSheet(f"color: {C.TEXT_DIM};")
            row_lay.addWidget(note)
            toggle._note = note

            def on_toggle(value: bool, box=toggle):
                box.setText("ON" if value else "OFF")
                changed(value)

            toggle.toggled.connect(on_toggle)
            parent.addWidget(row)
            return toggle

        assistant = category(
            "✦  ASSISTANT & APPEARANCE",
            "Identity, visual style, and the devices JARVIS uses to listen and speak.",
            True,
        )
        action_row(
            assistant,
            "CUSTOMISE ASSISTANT   [F7]",
            "Name, voice, accent colour, and appearance.",
            self._open_customize,
            True,
        )
        action_row(
            assistant,
            "AUDIO DEVICES",
            "Choose the microphone and speakers.",
            self._open_audio_devices,
            True,
        )
        self._settings_hud_btn = action_row(
            assistant,
            "HUD STYLE",
            "Switch between the animated face and the particle orb.",
            self._toggle_hud_style,
        )

        voice = category(
            "◉  VOICE & INPUT",
            "Control when JARVIS listens and how you start a conversation.",
        )
        self._settings_wake_switch = switch_row(
            voice,
            "WAKE WORD",
            "Say “Hey Jarvis” to begin.",
            False,
            self._set_wake_from_switch,
        )
        self._settings_wake_sleep_btn = action_row(
            voice,
            "SLEEP / WAKE NOW",
            "Pause or resume wake-word listening.",
            self._tap_wake_manual,
        )
        self._settings_ptt_switch = switch_row(
            voice,
            "PUSH-TO-TALK",
            "Keeps the microphone closed until you hold the configured key.",
            False,
            self._set_ptt_from_switch,
        )

        system = category(
            "▣  SYSTEM & DAILY USE",
            "Startup behaviour and window controls.",
        )
        self._settings_autostart_switch = switch_row(
            system,
            "START WITH COMPUTER",
            "Open JARVIS automatically after you sign in.",
            False,
            self._set_autostart_from_switch,
        )
        self._settings_brief_switch = switch_row(
            system,
            "MORNING BRIEF",
            "Offer a short daily overview when JARVIS starts.",
            False,
            self._set_brief_from_switch,
        )
        action_row(
            system,
            "CREATE DESKTOP SHORTCUT",
            "Add a shortcut to launch JARVIS quickly.",
            self._create_desktop_shortcut,
        )
        action_row(
            system,
            "FULLSCREEN   [F11]",
            "Expand the interface to the whole screen.",
            self._toggle_fullscreen,
            True,
        )
        action_row(
            system,
            "MINI ORB WINDOW   [F9]",
            "A small always-on-top window with only the orb.",
            lambda: self.set_mini(True),
            True,
        )
        action_row(
            system,
            "TOP DOCK BAR   [F10]",
            "A slim always-on-top bar that expands when you hover it.",
            lambda: self.set_dock(not self._dock_active),
            True,
        )
        action_row(
            system,
            "COMMAND PALETTE   [Ctrl+K]",
            "Search and run any action by name.",
            self._open_palette,
            True,
        )
        action_row(
            system,
            "KEYBOARD SHORTCUTS   [F1]",
            "Every shortcut in one list.",
            self._open_help,
            True,
        )

        data = category(
            "◆  DATA, PLUGINS & REMOTE",
            "Memory, optional integrations, and access from another device.",
        )
        action_row(
            data,
            "MEMORY",
            "Review and delete what JARVIS has stored.",
            self._open_memory_panel,
            True,
        )
        action_row(
            data,
            "PLUGINS",
            "Enable or disable installed capabilities.",
            self._open_plugin_manager,
            True,
        )
        action_row(
            data,
            "PLUGIN SETTINGS",
            "Configure plugins that provide their own settings.",
            self._open_plugin_settings,
            True,
        )
        action_row(
            data,
            "REMOTE CONTROL",
            "Pair a phone for remote access.",
            self._open_remote,
            True,
        )

        lay.addStretch(1)
        self._refresh_settings_switches()
        return w

''',
)

put(
    "_toggle_drawer",
    '''    def _toggle_drawer(self, checked: bool):
        if checked:
            self._refresh_settings_switches()
            self._position_quick_drawer()
            self._quick_drawer.show()
            self._quick_drawer.raise_()
        else:
            self._quick_drawer.hide()

''',
)

put(
    "_position_quick_drawer",
    '''    def _position_quick_drawer(self):
        d = getattr(self, "_quick_drawer", None)
        if d is None:
            return
        cw = self.centralWidget()
        width = min(360, max(300, cw.width() // 3))
        top = getattr(self, "_header_height_px", 54) + 8
        want = self._drawer_lay.sizeHint().height() + 90
        height = min(want, max(160, cw.height() - top - 12))
        try:
            cx = self._drawer_btn.mapTo(cw, QPoint(self._drawer_btn.width() // 2, 0)).x()
        except Exception:
            cx = cw.width() // 2
        left = max(10, min(int(cx - width / 2), cw.width() - width - 10))
        d.setGeometry(left, top, width, height)

''',
)

# ── 7. switch state: autostart, brief, wake, push-to-talk, HUD ───────────────
put(
    "_update_autostart_btn",
    '''    def _update_autostart_btn(self, enabled: bool):
        self._set_settings_switch("_settings_autostart_switch", bool(enabled))

''',
)

put(
    "_refresh_settings_switches",
    '''    def _refresh_settings_switches(self):
        """Repaint the drawer from the persisted runtime settings."""
        try:
            from memory.config_manager import get_brief_enabled

            self._set_settings_switch("_settings_brief_switch", get_brief_enabled())
        except Exception:
            pass
        self._update_autostart_btn(self._check_autostart())
        for fn in (
            self._refresh_wake_btns,
            self._refresh_talk_btns,
            self._refresh_hud_btn,
        ):
            try:
                fn()
            except Exception:
                pass

''',
)

put(
    "_update_brief_btn",
    '''    def _update_brief_btn(self, enabled: bool):
        self._set_settings_switch("_settings_brief_switch", bool(enabled))

''',
)

put(
    "_wake_state",
    '''    def _wake_state(self, cached: bool = False) -> dict:
        """State of the wake word. With cached=True it never imports the wake
        engine (slow), it only reads what the background warm-up found."""
        if self.wake_get_state:
            try:
                s = self.wake_get_state()
                return {
                    "ready": bool(s.get("ready")),
                    "enabled": bool(s.get("enabled")),
                    "awake": bool(s.get("awake")),
                }
            except Exception:
                pass
        if cached:
            if self._wake_cache is not None:
                return dict(self._wake_cache)
            return {"ready": False, "enabled": False, "awake": True}
        ready, enabled = False, False
        try:
            from core.wake_word import is_ready
            from memory.config_manager import get_wake_word_enabled

            ready, enabled = is_ready(), get_wake_word_enabled()
        except Exception:
            pass
        self._wake_cache = {"ready": ready, "enabled": enabled, "awake": True}
        return dict(self._wake_cache)

''',
)

put(
    "_refresh_wake_btns",
    '''    def _refresh_wake_btns(self):
        sw = getattr(self, "_settings_wake_switch", None)
        if sw is None:
            return
        note = getattr(sw, "_note", None)
        sleep_btn = getattr(self, "_settings_wake_sleep_btn", None)
        if self._wake_downloading:
            self._set_settings_switch("_settings_wake_switch", True, "…")
            sw.setEnabled(False)
            if note is not None:
                note.setText("Downloading the wake-word model (one-time)…")
            if sleep_btn is not None:
                sleep_btn.row.setVisible(False)
            return
        st = self._wake_state(cached=True)
        on = bool(st["ready"] and st["enabled"])
        sw.setEnabled(True)
        self._set_settings_switch("_settings_wake_switch", on)
        if note is not None:
            note.setText(
                "Say “Hey Jarvis” to begin."
                if st["ready"]
                else "Say “Hey Jarvis” to begin. First use downloads a small local model."
            )
        if sleep_btn is not None:
            sleep_btn.row.setVisible(on)
            sleep_btn.setText("SLEEP NOW" if st["awake"] else "WAKE NOW")

''',
)

put(
    "_refresh_talk_btns",
    '''    def _refresh_talk_btns(self):
        """Repaint the push-to-talk switch from the saved setting."""
        sw = getattr(self, "_settings_ptt_switch", None)
        if sw is None:
            return
        from memory.config_manager import get_push_to_talk_enabled

        ptt = get_push_to_talk_enabled()
        self._set_settings_switch("_settings_ptt_switch", ptt)
        try:
            from core.hotkey import chord_label

            key = chord_label()
        except Exception:
            key = "the push-to-talk key"
        note = getattr(sw, "_note", None)
        if note is not None:
            note.setText(
                f"The microphone stays closed until you hold {key}."
            )

''',
)

put(
    "_refresh_hud_btn",
    '''    def _refresh_hud_btn(self):
        btn = getattr(self, "_settings_hud_btn", None)
        if btn is None:
            return
        from memory.config_manager import get_hud_style

        face = get_hud_style() == "face"
        btn.setText("HUD STYLE:  ANIMATED FACE" if face else "HUD STYLE:  PARTICLE ORB")
        btn.setToolTip(
            "An animated head that speaks your words. Click to switch to the particle orb."
            if face
            else "A sphere of connected dots moving with your voice. Click to switch to the animated head."
        )

''',
)

put(
    "_toggle_wake_word",
    '''    def _toggle_wake_word(self):
        if self._wake_downloading:
            return
        st = self._wake_state()
        if not st["ready"]:
            self._wake_downloading = True
            self._refresh_wake_btns()

            def _work():
                try:
                    from core.wake_word import install_and_download

                    ok, msg = install_and_download(
                        logger=lambda m: self._log_sig.emit(f"SYS: {m}")
                    )
                except Exception as e:
                    ok, msg = False, str(e)
                if ok and self.on_wake_toggle:
                    try:
                        self.on_wake_toggle(True)
                    except Exception:
                        pass
                self._wake_dl_sig.emit(ok, msg)

            threading.Thread(target=_work, daemon=True).start()
            return
        new = not st["enabled"]
        if self.on_wake_toggle:
            try:
                self.on_wake_toggle(new)
            except Exception as e:
                self._log.append_log(f"ERR: Wake word failed — {e}")
        if self._wake_cache is not None:
            self._wake_cache["enabled"] = new
        self._refresh_wake_btns()

''',
)

put(
    "_on_wake_install_done",
    '''    def _on_wake_install_done(self, ok: bool, msg: str):
        self._wake_downloading = False
        self._wake_cache = None
        try:
            self._wake_state()
        except Exception:
            pass
        self._log_sig.emit(
            f"SYS: {'Wake word ready.' if ok else 'Wake word setup failed: ' + msg}"
        )
        self._refresh_wake_btns()

''',
)

# ── 8. verify and write ──────────────────────────────────────────────────────
compile(src, str(path), "exec")
shutil.copyfile(path, str(path) + ".bak")
path.write_text(src, encoding="utf-8")
print(f"OK: {path} fixed (backup: {path}.bak)")

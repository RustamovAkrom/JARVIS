# JARVIS UI localization

## Language model

- **Interface language**: persisted as `ui_language`; supported values currently come from `memory.translations.LANGUAGES` (English, Russian, Uzbek).
- **Speech language**: Gemini Live receives the user's audio and performs recognition/language understanding. JARVIS does not run a second language detector on Gemini's transcript by default.
- **Reply language**: Gemini Live naturally follows the language and context of the user's utterance. The interface language is not intended to override conversational language.

## Translation source of truth

`memory/translations.py` remains the catalog used by the existing UI. The new `core/i18n.py` API gives new and migrated components a stable interface:

```python
from core.i18n import t

label = t("Settings")
message = t("Saved {name}", name="Akrom")
```

- Unknown phrases remain unchanged, making untranslated UI visible instead of hiding it.
- Missing format placeholders are preserved.
- `in_language()` can translate a phrase into a specific supported locale.
- `current()` reads the persisted UI language and safely falls back to English.

## Migration strategy

Do not mass-replace existing `translate()` calls. Existing GUI code may translate strings during construction or use `translate_from()` to re-render UI in place. Migrate components incrementally to `core.i18n.t()`, keeping the catalog and behavior compatible.

A complete GUI localization requires auditing every visible string, including dynamically created dialogs, HUD, menus, tooltips, errors, settings, plugin panels, and onboarding. The catalog currently contains English/Russian/Uzbek entries but coverage is not guaranteed to be complete.

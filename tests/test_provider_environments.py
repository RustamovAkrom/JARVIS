"""Regression tests for independent Online and Offline AI provider profiles."""

import unittest
from unittest.mock import patch

from memory import config_manager


class ProviderEnvironmentTests(unittest.TestCase):
    def test_legacy_local_pipeline_is_detected_as_offline(self):
        legacy = {
            "mode": "pipeline",
            "pipeline": {"stt": "whisper", "llm": "openai", "tts": "piper"},
            "creds": {
                "whisper": {"backend": "local", "catalog": "local"},
                "openai": {"catalog": "ollama"},
                "piper": {"catalog": "piper"},
            },
        }
        with patch.object(
            config_manager, "load_api_keys", return_value={"providers": legacy}
        ):
            self.assertEqual(config_manager.get_provider_environment(), "offline")
            self.assertEqual(config_manager.get_provider_mode(), "pipeline")
            self.assertEqual(
                config_manager.get_pipeline("offline"),
                {"stt": "whisper", "llm": "openai", "tts": "piper"},
            )
            self.assertEqual(config_manager.get_pipeline("online"), {})

    def test_legacy_cloud_pipeline_is_detected_as_online(self):
        legacy = {
            "mode": "pipeline",
            "pipeline": {"stt": "whisper", "llm": "openai", "tts": "elevenlabs"},
            "creds": {
                "whisper": {"backend": "api", "catalog": "groq"},
                "openai": {"catalog": "groq"},
                "elevenlabs": {"catalog": "elevenlabs"},
            },
        }
        with patch.object(
            config_manager, "load_api_keys", return_value={"providers": legacy}
        ):
            self.assertEqual(config_manager.get_provider_environment(), "online")
            self.assertEqual(config_manager.get_provider_mode(), "pipeline")
            self.assertEqual(config_manager.get_pipeline("online"), legacy["pipeline"])

    def test_online_and_offline_profiles_are_read_independently(self):
        providers = {
            "environment": "offline",
            "mode": "pipeline",
            "online_mode": "pipeline",
            "pipeline": {"llm": "ollama"},
            "pipeline_online": {"stt": "groq", "llm": "openai", "tts": "elevenlabs"},
            "pipeline_offline": {"stt": "whisper", "llm": "ollama", "tts": "piper"},
        }
        with patch.object(
            config_manager, "load_api_keys", return_value={"providers": providers}
        ):
            self.assertEqual(config_manager.get_pipeline("online")["llm"], "openai")
            self.assertEqual(config_manager.get_pipeline("offline")["llm"], "ollama")
            self.assertEqual(config_manager.get_provider_environment(), "offline")
            self.assertEqual(config_manager.get_provider_mode(), "pipeline")

    def test_saving_offline_profile_does_not_overwrite_online_profile(self):
        data = {
            "providers": {
                "environment": "online",
                "mode": "pipeline",
                "online_mode": "pipeline",
                "pipeline": {"llm": "groq"},
                "pipeline_online": {"stt": "groq", "llm": "groq", "tts": "elevenlabs"},
                "pipeline_offline": {"stt": "whisper", "llm": "ollama", "tts": "piper"},
            }
        }

        def apply_update(update):
            update(data)

        with patch.object(config_manager, "_update_config", side_effect=apply_update):
            config_manager.save_providers(
                environment="offline",
                mode="pipeline",
                pipeline={"stt": "whisper", "llm": "ollama", "tts": "piper"},
                pipelines={
                    "online": {"stt": "groq", "llm": "groq", "tts": "elevenlabs"},
                    "offline": {"stt": "whisper", "llm": "ollama", "tts": "piper"},
                },
                online_mode="pipeline",
            )

        providers = data["providers"]
        self.assertEqual(providers["pipeline_online"]["llm"], "groq")
        self.assertEqual(providers["pipeline_offline"]["llm"], "ollama")
        self.assertEqual(providers["pipeline"]["llm"], "ollama")
        self.assertEqual(providers["environment"], "offline")
        self.assertEqual(providers["online_mode"], "pipeline")

    def test_provider_credentials_are_isolated_by_environment(self):
        data = {
            "providers": {
                "environment": "online",
                "creds": {
                    "whisper": {
                        "model": "legacy-model",
                        "base_url": "https://legacy.example/v1",
                    }
                },
            }
        }

        def update_config(**changes):
            data.update(changes)

        with (
            patch.object(
                config_manager,
                "get_providers",
                side_effect=lambda: data["providers"],
            ),
            patch.object(config_manager, "_patch_config", side_effect=update_config),
        ):
            config_manager.save_provider_config(
                "whisper",
                {"model": "local-whisper", "base_url": "http://127.0.0.1:11434"},
                environment="offline",
            )
            self.assertEqual(
                config_manager.get_provider_config("whisper", "online")["model"],
                "legacy-model",
            )
            self.assertEqual(
                config_manager.get_provider_config("whisper", "offline")["model"],
                "local-whisper",
            )

            config_manager.save_provider_config(
                "whisper",
                {"model": "groq-whisper", "base_url": "https://api.groq.com/openai/v1"},
                environment="online",
            )

            self.assertEqual(
                config_manager.get_provider_config("whisper", "online")["model"],
                "groq-whisper",
            )
            self.assertEqual(
                config_manager.get_provider_config("whisper", "offline")["model"],
                "local-whisper",
            )
            self.assertEqual(
                data["providers"]["creds"]["whisper"]["model"],
                "legacy-model",
            )

    def test_provider_catalog_defaults_never_overwrite_existing_values(self):
        merged = config_manager.merge_provider_catalog_defaults(
            {
                "model": "user-selected-model",
                "max_tools": "37",
                "api_key": "user-key",
            },
            {
                "model": "preset-model",
                "max_tools": "8",
                "base_url": "https://preset.example/v1",
            },
            "groq",
        )
        self.assertEqual(merged["model"], "user-selected-model")
        self.assertEqual(merged["max_tools"], "37")
        self.assertEqual(merged["api_key"], "user-key")
        self.assertEqual(merged["base_url"], "https://preset.example/v1")
        self.assertEqual(merged["catalog"], "groq")

    def test_save_providers_writes_credentials_to_active_environment(self):
        data = {
            "providers": {
                "environment": "online",
                "creds": {"shared": {"model": "legacy"}},
                "creds_online": {"shared": {"model": "cloud-model"}},
            }
        }

        def apply_update(update):
            update(data)

        with patch.object(config_manager, "_update_config", side_effect=apply_update):
            config_manager.save_providers(
                environment="offline",
                mode="pipeline",
                pipeline={"llm": "shared"},
                creds={"shared": {"model": "local-model", "max_tools": "12"}},
            )

        providers = data["providers"]
        self.assertEqual(
            providers["creds_online"]["shared"]["model"], "cloud-model"
        )
        self.assertEqual(
            providers["creds_offline"]["shared"]["model"], "local-model"
        )
        self.assertEqual(
            providers["creds_offline"]["shared"]["max_tools"], "12"
        )
        self.assertEqual(providers["creds"]["shared"]["model"], "legacy")

    def test_switching_back_to_online_keeps_online_engine_choice(self):
        data = {
            "providers": {
                "environment": "offline",
                "mode": "pipeline",
                "online_mode": "gemini",
                "pipeline_online": {"llm": "openai"},
                "pipeline_offline": {"llm": "ollama"},
                "pipeline": {"llm": "ollama"},
            }
        }

        def apply_update(update):
            update(data)

        with patch.object(config_manager, "_update_config", side_effect=apply_update):
            config_manager.save_providers(
                environment="online",
                mode="gemini",
                online_mode="gemini",
                pipeline={"stt": "", "llm": "", "tts": ""},
            )

        providers = data["providers"]
        self.assertEqual(providers["online_mode"], "gemini")
        self.assertEqual(providers["pipeline_online"]["llm"], "")
        self.assertEqual(providers["pipeline_offline"]["llm"], "ollama")
        self.assertEqual(providers["pipeline"]["llm"], "")


if __name__ == "__main__":
    unittest.main()

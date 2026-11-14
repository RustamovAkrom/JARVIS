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

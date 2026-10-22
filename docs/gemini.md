# Gemini in JARVIS

[??????? ??????](gemini-ru.md) ? [Main documentation](../README.md)

Gemini Live is the default conversation engine. It combines the audio session, responses, and calls to declared tools. One-shot helper requests and some plugins may also call Gemini, depending on enabled features.

## Create and store a key

1. Open [Google AI Studio](https://aistudio.google.com/apikey), select a project, and create a Gemini API key.
2. Check which models are available to that project.
3. Run `python main.py` and enter the key in setup, or update it through the UI.
4. Keep `config/api_keys.json` local. Do not commit or share it in messages, issues, screenshots, or public backups.

If a key is exposed, revoke it with Google and create a new one. Never embed a key in source files or plugins.

## Models and fallback

The model list and retry order are defined by the current code in `core/gemini.py` and `main.py`; they may change with the API. Check current IDs and availability in Google?s official catalogue for your project. Gemini Live and the regular REST API have different interfaces and capabilities: a REST model cannot simply replace a Live model.

Before changing source model lists, verify Google?s documentation, required capabilities, tool calls, and fallback behaviour. Do not assume a preview model is available to every project.

## Where AI is used

| Scenario | Components |
|---|---|
| Default conversation | Gemini Live in `main.py` |
| Helper requests | Wrappers in `core/gemini.py` |
| Computer tools | Schemas from `actions/` and enabled plugins; executed by local Python code |
| MY OWN SETUP | `providers/`; a separate pipeline, not Gemini Live |

An API key does not guarantee free access or a fixed quota. Check Google?s current limits and pricing.

## Data and privacy

With a cloud model, audio, transcript, prompt, selected image/screen capture, and tool results may be sent to Google or another API involved in a feature. Consider the contents before sending a file, screen, or camera frame.

For local processing, use local STT, LLM, and TTS and disable cloud plugins you do not need. A local LLM does not make the other components local.

## Common errors

- **Invalid API key:** check the project, key completeness, and status.
- **403 / model not found:** access depends on project, account, region, and API.
- **429 / quota:** check limits and usage; avoid unbounded retries.
- **Timeout / unavailable:** check the network and service status, retry later, or switch to the pipeline.
- **Text works but there is no sound:** check microphone permissions and audio devices separately.

Official references: [API keys](https://ai.google.dev/gemini-api/docs/api-key), [models](https://ai.google.dev/gemini-api/docs/models), [Live API](https://ai.google.dev/gemini-api/docs/live-api), [pricing](https://ai.google.dev/gemini-api/docs/pricing).

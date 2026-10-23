# Local and third-party models

[??????? ??????](local-llm-ru.md) ? [Main documentation](../README.md)

Configure providers in **SETUP ? ENGINE & PROVIDERS**, then press **SAVE & APPLY**. Gemini Live remains the default mode.

## Local LLM

1. Install and start a model server separately: Ollama, LM Studio, llama.cpp server, vLLM, or Jan.
2. Download a model in that app and enable its API server.
3. In JARVIS, select **MY OWN SETUP**, then the matching LLM entry.
4. Check the Base URL and enter the model ID. Presets: Ollama `http://127.0.0.1:11434/v1`, LM Studio `http://127.0.0.1:1234/v1`, llama.cpp `:8080/v1`, vLLM `:8000/v1`, Jan `:1337/v1`.
5. Use TEST if available, then press **SAVE & APPLY**.

The server must support OpenAI Chat Completions. Tool calling depends on the specific server and model. Advanced settings include a smaller tool budget for some local models.

## Hosted and custom APIs

The catalogue includes OpenAI, Groq, DeepSeek, Mistral, xAI, OpenRouter, Together, and Cerebras. Enter the endpoint, model ID, and key if required. A custom endpoint must be compatible with OpenAI Chat Completions; an incompatible proprietary API needs a dedicated adapter. Check provider pricing and data policies.

## STT ? LLM ? TTS pipeline

The three slots are configured independently and can mix local and cloud services:

- **STT / HEARING:** local faster-whisper (`tiny`, `base`, `small`, `medium`, `large-v3`, `large-v3-turbo`; CPU and `base` by default), or a remote OpenAI-compatible transcription endpoint.
- **LLM / THINKING:** a local or hosted model from the catalogue.
- **TTS / SPEAKING:** local Piper (voice download may be needed), OS-installed voices, or ElevenLabs (key and internet required).

A fully local pipeline requires local implementations in all three slots. It does not automatically make search, network actions, or plugins offline. The pipeline is usually slower than Gemini Live because recognition, generation, and synthesis are separate steps. Tool availability depends on the engine and model.

## Sidecar

An additional LLM can serve as a sidecar for short background requests. **Sidecar first** controls whether it is preferred for those tasks; the main conversation model does not change.

## Troubleshooting

- **Connection refused/timeout:** start the server and verify its API URL.
- **No model list:** download a model; check model-list support or enter the model ID manually if the field is available.
- **Poor transcription:** check STT model, language, and microphone.
- **No spoken answer:** check TTS, selected voice, and output device.
- **Tool not called:** the model or engine may lack tool-calling support.
- **Credential error:** verify the key and access at the provider.

Provider fields are defined in `providers/`; settings are stored under `providers` in `config/api_keys.json`. Never publish keys.

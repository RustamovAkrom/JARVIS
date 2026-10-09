# Локальные LLM: Ollama и LM Studio

> Статус на 9 октября 2026 г.: `core/llm_client.py` реализует клиент для
> Ollama и OpenAI-совместимых серверов, включая LM Studio, но **не подключён к
> основному runtime JARVIS**. Голосовой диалог и функции ассистента по-прежнему
> используют Gemini. Эта страница описывает модуль и его текущие ограничения.

## Что уже реализовано

`core/llm_client.py` читает настройки из `config/api_keys.json` и предоставляет:

| Возможность | Ollama | LM Studio / OpenAI-compatible |
| --- | --- | --- |
| Проверка доступности | `GET /api/tags`; при необходимости пытается запустить `ollama serve` | `GET /v1/models`; сервер пользователь запускает сам |
| Обычный чат | `POST /api/chat` | `POST /v1/chat/completions` |
| Потоковая генерация | Да, JSON-строки Ollama | Да, SSE OpenAI Chat Completions |
| Вызовы инструментов | Передаются как `tools` | Передаются как OpenAI `tools`, ответ нормализуется |
| Прогрев | Да, с `keep_alive: -1` | Минимальный запрос для загрузки модели |

Синтаксис модуля проверен командой `uv run python -m py_compile
core/llm_client.py`.

## Важное ограничение: это ещё не переключатель JARVIS

Поиск по проекту не находит импортов `core.llm_client` за пределами самого
`core/llm_client.py`. Следовательно, создание `llm_provider` в конфигурации
**не переключает** Live-сессию, голос, плагины или Gemini-вызовы на локальную
модель. Для настоящей интеграции нужно явно подключить `call_llm()` или
`call_llm_stream()` в выбранный маршрут приложения и определить правила
fallback между локальной моделью и Gemini.

Не стоит утверждать, что JARVIS уже работает полностью offline: интерфейс и
модуль существуют, но основной оркестратор пока использует Gemini Live.

## Ollama: подготовка и конфигурация

1. Установите Ollama с [официального сайта](https://ollama.com/download).
2. Загрузите модель. Пример с именем по умолчанию проекта:

   ```powershell
   ollama pull llama3.2
   ```

3. Обычно приложение Ollama запускает локальный сервер автоматически. Если
   нет, выполните:

   ```powershell
   ollama serve
   ```

4. Добавьте поля в `config/api_keys.json`, **сохранив** существующие
   `gemini_api_key` и `os_system`:

   ```json
   {
     "gemini_api_key": "ваш_ключ_Gemini",
     "os_system": "windows",
     "llm_provider": "ollama",
     "llm_url": "http://localhost:11434",
     "llm_model": "llama3.2"
   }
   ```

Локальный API Ollama использует `http://localhost:11434/api`; для локальных
моделей API-ключ не нужен. У модели должны быть достаточные RAM/VRAM и свободное
место: размер зависит от выбранной модели и квантизации.

## LM Studio: подготовка и конфигурация

1. Установите [LM Studio](https://lmstudio.ai/), скачайте совместимую модель и
   загрузите её в приложении.
2. Откройте вкладку **Developer** и включите **Start server**, либо выполните
   `lms server start`.
3. Укажите точный идентификатор загруженной модели из LM Studio и добавьте в
   `config/api_keys.json`:

   ```json
   {
     "gemini_api_key": "ваш_ключ_Gemini",
     "os_system": "windows",
     "llm_provider": "openai",
     "llm_url": "http://localhost:1234",
     "llm_model": "идентификатор_модели_из_LM_Studio"
   }
   ```

Можно написать `"lmstudio"` вместо `"openai"`: модуль приводит оба значения к
одному OpenAI-совместимому пути. Не добавляйте `/v1` в `llm_url`: код сам строит
`/v1/models` и `/v1/chat/completions`.

LM Studio поддерживает OpenAI-совместимые `/v1/models` и
`/v1/chat/completions`, на которые рассчитан клиент. Возможность tool calling
зависит и от сервера, и от конкретной загруженной модели.

## Ограничения и известные проблемы модуля

- `call_llm()` и `call_llm_stream()` выбирают Ollama или OpenAI-совместимый
  backend корректно по `llm_provider`.
- `call_llm_text()` сейчас всегда вызывает Ollama `/api/chat`; при
  `llm_provider: "openai"` она не работает с LM Studio. Не используйте её для
  LM Studio, пока маршрутизация не будет исправлена.
- Для OpenAI-совместимого backend `check_model_available()` возвращает `True`
  без проверки, что указанная модель действительно загружена.
- LM Studio не запускается автоматически: модуль только проверяет его endpoint.
- `config/api_keys.json` сейчас также редактирует UI. Если снова появится
  первоначальный экран настройки (например, после ошибки ключа), его обработчик
  записывает только `gemini_api_key` и `os_system`; локальные `llm_*` поля могут
  быть потеряны. Храните копию конфигурации без секретов или добавьте merge-логику
  перед использованием этой настройки в production.

## Проверка сервера вручную

Ollama:

```powershell
Invoke-RestMethod http://localhost:11434/api/tags
```

LM Studio:

```powershell
Invoke-RestMethod http://localhost:1234/v1/models
```

Ответ без сетевой ошибки подтверждает, что сервер доступен. Это не доказывает
интеграцию с основным JARVIS: в текущей версии модуль не вызывается runtime-ом.

## Что нужно для полноценной интеграции

1. Добавить в UI отдельные поля провайдера, URL и модели; сохранять их через
   merge, а не перезапись `api_keys.json`.
2. Выбрать область применения: только фоновые текстовые задачи или также
   основной диалог. Gemini Live нельзя заменить обычным HTTP-чатом без нового
   аудиоконтура.
3. Исправить `call_llm_text()` для OpenAI-compatible endpoint и добавить тесты
   для Ollama и LM Studio с mock HTTP-сервером.
4. Явно определить обработку ошибок, таймаутов, инструменты и fallback в Gemini.
5. Добавить индикатор активного провайдера, чтобы пользователь не думал, что
   голосовой ассистент уже перешёл на локальную модель.

## Официальные ссылки

- [Ollama: Quickstart](https://docs.ollama.com/quickstart)
- [Ollama: API](https://docs.ollama.com/api/introduction)
- [LM Studio: запуск локального API-сервера](https://lmstudio.ai/docs/developer/core/server)
- [LM Studio: OpenAI-compatible endpoints](https://lmstudio.ai/docs/developer/openai-compat)

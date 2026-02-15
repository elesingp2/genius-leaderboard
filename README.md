# genius-leaderboard

Пошаговый setup MVP `Genius-bench`: как с нуля поднять локальный бенчмарк для сравнения LLM по объяснению строк песен и поиску отсылок с доказательствами.

## Что делает проект

Для каждой строки песни агент должен:

- объяснить смысл строки;
- найти возможные отсылки и дать доказательства (`url` + `snippet`).

Оценка одной метрикой: `GeniusScore` в диапазоне `0..1`.

## 0) Предварительные требования

Нужно установить:

- `Node.js` (рекомендуется 18+), чтобы запускать `promptfoo` через `npx`;
- `Python` 3.10+ для `providers/agent.py`;
- API-ключи:
  - `OPENROUTER_API_KEY`
  - `SEARCH_API_KEY` (например Tavily или другой search API).

## 1) Подготовь структуру проекта

В корне репозитория должны быть файлы:

- `promptfooconfig.yaml`
- `tests.jsonl`
- `providers/agent.py`

Если их нет:

```bash
mkdir -p providers
touch promptfooconfig.yaml tests.jsonl providers/agent.py
```

## 2) Настрой переменные окружения

Создай `.env` в корне:

```bash
OPENROUTER_API_KEY=...
SEARCH_API_KEY=...
```

Загрузи переменные в сессию:

```bash
set -a
source .env
set +a
```

`.env` уже в `.gitignore`, поэтому ключи не должны попасть в репозиторий.

## 3) Подготовь тесты (`tests.jsonl`)

Каждая строка в `tests.jsonl` — отдельный JSON-объект с `vars`.

Минимальный пример для сравнения двух моделей:

```json
{"vars":{"line":"Tonight it's Resident Evil and I feel like Leon","model":"openai/gpt-5-mini"}}
{"vars":{"line":"Tonight it's Resident Evil and I feel like Leon","model":"anthropic/claude-3.5-sonnet"}}
```

Обязательные поля:

- `line` — строка песни;
- `model` — ID модели OpenRouter.

## 4) Реализуй агент (`providers/agent.py`)

`promptfoo` будет вызывать этот скрипт как `exec` provider. Агент должен:

1. получить входные `vars` (как минимум `line` и `model`);
2. сходить в search API и получить подтверждающие snippets;
3. вызвать модель в OpenRouter;
4. вернуть в `stdout` строго валидный JSON.

Обязательный формат ответа агента:

```json
{
  "meaning": "строковое объяснение смысла",
  "references": [
    {
      "claim": "конкретная отсылка",
      "url": "https://...",
      "snippet": "короткая выдержка из источника",
      "why_it_supports": "почему snippet подтверждает claim",
      "confidence": 0.0
    }
  ],
  "uncertainties": []
}
```

Критично:

- JSON всегда валиден;
- каждый объект в `references` содержит `url` и `snippet`;
- если доказательств нет, возвращай `references: []`.

## 5) Настрой `promptfooconfig.yaml`

В конфиге должно быть:

- provider типа `exec`, который вызывает `python providers/agent.py`;
- подключение тестов из `tests.jsonl`;
- один `assert` типа `llm-rubric` для расчета `GeniusScore`.

Логика метрики:

- hard gate: если ответ невалидный JSON, или в reference нет `url`/`snippet` -> `score = 0`;
- иначе: `score = 0.6 * meaning + 0.4 * references`.

## 6) Запусти оценку

Из корня проекта:

```bash
npx promptfoo eval
```

(опционально) посмотреть результаты:

```bash
npx promptfoo view
```

## 7) Чек готовности MVP

MVP готов, если:

- `npx promptfoo eval` отрабатывает без падений;
- сравниваются минимум 2 модели;
- у каждой модели считается `GeniusScore`;
- агент стабильно возвращает валидный JSON.

## Типовые проблемы

- `score = 0` у всех ответов: чаще всего нарушен JSON-формат или пустые `url`/`snippet`.
- `401/403` от API: проверь `OPENROUTER_API_KEY` и `SEARCH_API_KEY`.
- `provider exec failed`: проверь путь к `providers/agent.py` и что скрипт печатает только JSON в `stdout`.

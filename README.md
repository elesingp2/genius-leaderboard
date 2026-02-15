# genius-leaderboard

Мини-бенч для оценки как llm шарят за репчик  (`promptfoo` + OpenRouter + web evidence).

## Что делает

Для каждой целевой строки агент (`providers/agent.py`) возвращает JSON:

- `meaning`: интерпретация строки (коротко и по делу);
- `references`: источники с `claim/url/snippet/why_it_supports/confidence`;
- `uncertainties`: причины понижения уверенности.

Оценка идёт через `llm-rubric` метрику `GeniusScore` (`0..1`) в `promptfooconfig.yaml`.

## Требования

- Python 3.10+;
- Node.js 18+;
- `promptfoo` (через `npm i -g promptfoo` или `npx promptfoo ...`).

## Конфиг

1) Заполни `.env`:

```bash
OPENROUTER_API_KEY=sk-or-v1-...
SEARCH_API_KEY=tvly-...
OPENROUTER_JUDGE_MODEL=liquid/lfm-2.5-1.2b-instruct:free
```

1) Настрой `llm_config.json` (актуальные ключи):

```json
{
  "openrouter_model": "arcee-ai/trinity-large-preview:free",
  "enable_web_search": true,
  "system_prompt": "...",
  "user_prompt": "..."
}
```

`enable_web_search: false` отключает Tavily, и агент строит интерпретацию только по тексту песни/контексту.

## Данные тестов

`tests.jsonl` содержит строки в формате:

```json
{"vars":{"song_title":"amy","artist":"Kai Angel","target_line":"I stole a Chrome beanie"}}
```

Тексты песен и дефолтные переменные заданы в `promptfooconfig.yaml` (`defaultTest.vars`).

## Запуск

```bash
promptfoo eval --no-cache
```

Опционально открыть UI:

```bash
promptfoo view
```

## Контракт ответа агента

Агент должен печатать в `stdout` только валидный JSON:

```json
{
  "meaning": "string | null",
  "references": [
    {
      "claim": "string",
      "url": "https://...",
      "snippet": "string",
      "why_it_supports": "string",
      "confidence": 0.0
    }
  ],
  "uncertainties": ["string"]
}
```

Примечания:

- если надёжных источников нет, возвращается `references: []`;
- слабые источники (`confidence < 0.60`) не отдаются в финальный JSON;
- `uncertainties` объясняет, почему нет веб-доказательств (disabled/error/low-confidence/no usable sources).

## Частые проблемы

- `provider exec failed`: скрипт вернул невалидный JSON или упал;
- `SEARCH_API_KEY` пустой при включённом web search: будут `uncertainties` про поиск;
- `MetadataLookupWarning`: шум зависимостей, обычно не влияет на результаты.

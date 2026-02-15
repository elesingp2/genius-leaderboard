# genius-leaderboard

Мини-бенч для сравнения LLM по разбору строк песен.

## Что делает

Для каждой целевой строки песни агент возвращает JSON:

- `meaning`: краткое объяснение смысла;
- `references`: ссылки с `url` и `snippet`;
- `uncertainties`: список сомнений/ошибок.

Оценка: `GeniusScore` (`0..1`) через `promptfoo` rubric.

## Быстрый старт

1) Заполни `.env` (только ключи):

```bash
OPENROUTER_API_KEY=...
SEARCH_API_KEY=...
```

2) Настрой `llm_config.json`:

```json
{
  "openrouter_model": "liquid/lfm-2.5-1.2b-instruct:free",
  "openrouter_judge_model": "liquid/lfm-2.5-1.2b-instruct:free",
  "enable_web_search": true,
  "system_prompt": "You explain one target lyric line using song context. Write 1-2 concise sentences.",
  "user_prompt": "Song: {song_title} — {artist}\\n\\nTarget line:\\n\\\"{target_line}\\\"\\n\\nContext window from song:\\n{context_window}\\n\\nEvidence:\\n{evidence}"
}
```

`enable_web_search: false` выключает Tavily и агент отвечает только по входному тексту песни/весам модели.

3) Подготовь `tests.jsonl` (по одному кейсу на песню/строку):

```json
{"vars":{"song_title":"sirens","artist":"Kai Angel","target_line":"She said, \"I am not afraid to die\"","song_text":"...full song text..."}}
```

4) Запусти:

```bash
promptfoo eval
```

Опционально:

```bash
promptfoo view
```

## Контракт ответа агента

`providers/agent.py` обязан печатать в `stdout` только валидный JSON:

```json
{
  "meaning": "string",
  "references": [
    {
      "claim": "string",
      "url": "https://...",
      "snippet": "string",
      "why_it_supports": "string",
      "confidence": 0.0
    }
  ],
  "uncertainties": []
}
```

Если доказательств нет: `references: []`.

## Частые проблемы

- `provider exec failed`: скрипт напечатал не JSON или упал;
- `MetadataLookupWarning`: шум Node-зависимостей, можно игнорировать.

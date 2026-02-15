# genius-leaderboard

I’ll answer as a world-famous LLM evaluation researcher (PhD) with the ACM SIGSOFT Distinguished Paper Award.

# ТЗ: MVP проекта "Genius-bench"

## 1. Цель

Создать простой бенчмарк для сравнения LLM по задаче:

- Объяснение смысла строки/фрагмента песни  
- Поиск отсылок **ТОЛЬКО** с доказательствами (URL + snippet)

Оценка производится одной метрикой: `GeniusScore`.

Масштабируемость не требуется. Приоритет — простота и минимальный стек.

---

## 2. Входные данные

Файл `tests.jsonl`

Каждый тест содержит:

- `line` — строка песни (обязательное поле)
- `model` — id модели OpenRouter (обязательное поле)

Пример:

```json
{"vars":{"line":"Tonight it's Resident Evil and I feel like Leon","model":"openai/gpt-5-mini"}}
```

---

## 3. Формат ответа агента

Агент обязан вернуть **СТРОГИЙ JSON**:

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

### Требования:
- JSON всегда валиден
- Все `references` содержат `url` и `snippet`
- Если доказательств нет → `references: []`

---

## 4. Архитектура

Минимальная схема:
1. **promptfoo** запускает provider типа `exec`
2. **agent.py** выполняет:
   - Web search через API поиска
   - Извлечение snippets
   - Вызов модели через OpenRouter
   - Возврат JSON в stdout
3. **promptfoo** применяет одну метрику `llm-rubric` (GeniusScore)

---

## 5. Метрика: GeniusScore (0..1)

Оценивается через `llm-rubric`.

### Hard Gate (обязательное условие)

Если:
- output невалидный JSON
- **ИЛИ** хотя бы один reference не содержит `url` или `snippet`

**→ score = 0**

### Если гейт пройден:

`score = 0.6 * meaning + 0.4 * references`

#### meaning:
- точность
- ясность
- отсутствие воды

#### references:
- claim конкретный
- snippet выглядит как выдержка
- `why_it_supports` логично связывает snippet и claim
- нет утверждений вне evidence

---

## 6. Структура проекта

Обязательные файлы:
- `promptfooconfig.yaml`
- `tests.jsonl`
- `providers/agent.py`

---

## 7. Переменные окружения

- `OPENROUTER_API_KEY`
- `SEARCH_API_KEY`

---

## 8. Критерии готовности

Проект считается завершённым если:
- `npx promptfoo eval` успешно запускается
- Можно сравнить минимум 2 модели
- Для каждой модели считается `GeniusScore`
- Агент всегда возвращает валидный JSON

#!/usr/bin/env python3
import json, os, re, sys, urllib.request, urllib.error, urllib.parse


# ── Config ──────────────────────────────────────────────────────────

def _load_env(path=".env"):
    for row in open(path):
        row = row.strip()
        if row and not row.startswith("#") and "=" in row:
            k, v = row.split("=", 1)
            v = v.strip()
            if " #" in v:
                v = v.split(" #")[0].strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
                v = v[1:-1]
            os.environ.setdefault(k.strip(), v)

def _load_json(path="llm_config.json"):
    with open(path) as f:
        return json.load(f)

_load_env()
_cfg = _load_json()

API_KEY    = os.environ["OPENROUTER_API_KEY"]
SEARCH_KEY = os.getenv("SEARCH_API_KEY", "")
MODEL      = _cfg.get("openrouter_model", "").strip()
SYS_PROMPT = _cfg.get("system_prompt", "").strip()
USR_PROMPT = _cfg.get("user_prompt", "").strip()
WEB_SEARCH = str(_cfg.get("enable_web_search", True)).lower() in ("1", "true", "yes", "on")


# ── Blocked hosts & garbage patterns ────────────────────────────────

# Хосты без полезных интерпретаций (тексты / видео / соцсети / IDE-песочницы)
BLOCKED_HOSTS = {
    "genius.com"
}

# Паттерны мусора в сниппете: base64, data-URI, hex-блобы, длинные хэши
GARBAGE_PATTERNS = re.compile(
    r"(?i)"
    r"(data:image/|base64,|"                         # data-URI / base64
    r"iVBOR|/9j/4|R0lGOD|"                          # начало PNG/JPEG/GIF base64
    r"[A-Za-z0-9+/=]{80,}|"                         # длинная base64-строка (≥80 символов подряд)
    r"[0-9a-f]{40,})"                                # hex-хэш ≥40 символов
)

# Слова-мусор: часто в сниппетах, но не несут смысла
NOISE_WORDS = {"search", "video", "click", "download", "free", "play", "watch", "share"}


# ── Scoring ─────────────────────────────────────────────────────────
#
#  Двухэтапная оценка:
#    1) lyric_overlap  — сколько слов из СТРОКИ ПЕСНИ нашлось в сниппете
#    2) meta_overlap   — сколько слов из названия/артиста нашлось в сниппете
#    3) depth          — доля «новых» слов (≈ интерпретация, а не цитата)
#
#  Ключевое правило: depth вносит вклад ТОЛЬКО пропорционально lyric_overlap.
#  Это гейтит мусор: случайная статья с нулевым overlap не наберёт score,
#  даже если в ней много уникальных слов.
#
#  confidence = BASE + lyric_hits * LYRIC_W + meta_hits * META_W + depth * DEPTH_W * gate
#  gate = min(lyric_hits / 2, 1.0)  — depth полностью включается при ≥2 совпадениях

CONFIDENCE_BASE   = 0.10   # стартовый балл (только за то, что нашёлся)
LYRIC_WEIGHT      = 0.15   # за каждое совпавшее слово из строки
META_WEIGHT       = 0.06   # за каждое совпадение с title/artist (менее важно)
DEPTH_WEIGHT      = 0.30   # вес «глубины» — но только при наличии overlap
MIN_LYRIC_OVERLAP = 1      # хотя бы 1 слово из строки должно быть в сниппете
STRONG_CONFIDENCE = 0.60   # порог сильного источника (совпадает с грейдером)
MAX_STRONG_REFS   = 3
MAX_WEAK_REFS     = 2
MAX_RESULTS_PER_Q = 5
MIN_SNIPPET_WORDS = 8


def _keywords(text):
    """Множество нормализованных слов длиной > 3."""
    return {w.strip(".,:;!?()[]{}\"'").lower() for w in text.split() if len(w) > 3}


def _host_of(url):
    try:
        return urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return ""


def _is_blocked(url):
    """URL заблокирован: PDF, чёрный список хостов."""
    lower = url.lower()
    return lower.endswith(".pdf") or any(h in _host_of(url) for h in BLOCKED_HOSTS)


def _is_garbage_snippet(snippet):
    """
    Сниппет — мусор, если:
      - содержит base64 / data-URI / hex-блоб
      - слишком мало слов (не текст)
      - больше половины «слов» не ASCII-буквы (бинарный шум)
    """
    if GARBAGE_PATTERNS.search(snippet):
        return True
    words = snippet.split()
    if len(words) < MIN_SNIPPET_WORDS:
        return True
    ascii_words = sum(1 for w in words if re.match(r"^[A-Za-z'''-]+$", w))
    if ascii_words / max(len(words), 1) < 0.5:
        return True
    return False


def score_reference(line, song_title, artist, url, snippet):
    """
    Оценить один поисковый результат → dict или None (если мусор / нерелевант).

    Ключевая идея: depth (уникальные слова) учитывается только
    пропорционально lyric_overlap. Это убивает мусор вроде
    TVTropes/random-blog, который набирает «глубину» без релевантности.
    """
    if _is_garbage_snippet(snippet):
        return None

    lyric_kws   = _keywords(line)
    snippet_kws = _keywords(snippet) - NOISE_WORDS
    meta_kws    = _keywords(f"{song_title} {artist}")

    # Сколько слов из строки нашлось в сниппете
    lyric_hits = len(lyric_kws & snippet_kws)
    # Сколько слов из title/artist нашлось
    meta_hits  = len(meta_kws & snippet_kws)

    # Гейт: если ни одного слова из строки — нерелевантный результат
    if lyric_hits < MIN_LYRIC_OVERLAP:
        return None

    # Depth: доля «новых» слов (не из запроса) — мера интерпретации
    depth = len(snippet_kws - lyric_kws) / max(len(snippet_kws), 1)

    # Gate: depth вносит полный вклад только при ≥2 совпадениях со строкой
    depth_gate = min(lyric_hits / 2.0, 1.0)

    confidence = round(min(1.0,
        CONFIDENCE_BASE
        + lyric_hits * LYRIC_WEIGHT
        + meta_hits  * META_WEIGHT
        + depth * DEPTH_WEIGHT * depth_gate
    ), 2)

    claim = snippet.lstrip(".-:; ").split(". ")[0][:120].strip() or "Lyric-related reference"
    why   = "Interpretive context" if depth > 0.4 and lyric_hits >= 2 else "Confirms the quote"

    return {
        "claim": claim,
        "url": url,
        "snippet": snippet[:300],
        "why_it_supports": why,
        "confidence": confidence,
    }


# ── Search ──────────────────────────────────────────────────────────

def _tavily_search(query):
    return _post("https://api.tavily.com/search", {
        "api_key": SEARCH_KEY,
        "query": query,
        "max_results": MAX_RESULTS_PER_Q,
    })


def search(line, song_title="", artist=""):
    """
    Поиск интерпретаций через Tavily: strict → loose.
    Возвращает (references, note).
    """

    meta = f"{song_title} {artist} meaning explained interpretation".strip()
    # Ищем отсылки/значения, а не текст песни
    queries = [
        f'"{line}" {meta}',
        f"{line} {meta}",
        f"{line} reference allusion slang meaning",
    ]

    seen, results, errors = set(), [], []

    for q in queries:
        data = _tavily_search(q)
        if data.get("_error"):
            errors.append(data["_error"])
            continue
        for r in data.get("results", [])[:MAX_RESULTS_PER_Q]:
            url     = r.get("url", "")
            snippet = (r.get("content") or "").strip()
            if not url or not snippet or url in seen:
                continue
            seen.add(url)
            if _is_blocked(url):
                continue
            scored = score_reference(line, song_title, artist, url, snippet)
            if scored is None:
                continue
            results.append(scored)

    results.sort(key=lambda r: -r["confidence"])

    strong = [r for r in results if r["confidence"] >= STRONG_CONFIDENCE]
    if strong:
        return strong[:MAX_STRONG_REFS], None
    # Слабые refs не возвращаем — грейдер бракует любой ref < 0.60
    if errors:
        return [], f"Web search request failed: {errors[0]}."
    if results:
        return [], "Search returned only low-confidence sources."
    return [], "Web search returned no usable sources."


# ── HTTP ────────────────────────────────────────────────────────────

def _post(url, body, headers=None):
    h = {**(headers or {}), "Content-Type": "application/json"}
    data = json.dumps(body, ensure_ascii=True).encode("ascii")
    req = urllib.request.Request(url, data, h, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=25)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        sys.stderr.write(f"[DEBUG] HTTP {e.code}: {err_body}\n")
        return {"_error": f"http_{e.code}"}
    except Exception as e:
        sys.stderr.write(f"[DEBUG] _post exception: {type(e).__name__}: {e}\n")
        return {"_error": str(e)}


# ── LLM ─────────────────────────────────────────────────────────────

def _extract_text(response):
    """Текст из OpenRouter response (content → reasoning fallback)."""
    try:
        msg = (response.get("choices") or [{}])[0].get("message") or {}
        text = msg.get("content")
        if isinstance(text, list):
            text = " ".join(
                (c.get("text", "") if isinstance(c, dict) else str(c)) for c in text
            )
        text = (text or "").strip()
        if not text:
            text = (msg.get("reasoning") or "").strip()
        return text[:500] if text else None
    except Exception:
        return None


def _render_prompt(template, **variables):
    for key, value in variables.items():
        template = template.replace("{" + key + "}", value or "")
    return template


def get_meaning(line, song_text, song_title, artist, model, refs):
    """Запросить у LLM интерпретацию строки. Возвращает str или None."""
    evidence = "\n".join(f"- {r['url']}: {r['snippet']}" for r in refs) or "None"
    context  = context_window(song_text, line)

    prompt = _render_prompt(
        USR_PROMPT,
        line=line, target_line=line,
        song_text=song_text[:1800], context_window=context,
        song_title=song_title, artist=artist, evidence=evidence,
    )

    response = _post(
        "https://openrouter.ai/api/v1/chat/completions",
        {
            "model": model,
            "messages": [
                {"role": "system", "content": SYS_PROMPT},
                {"role": "user",   "content": prompt},
            ],
            "temperature": 1.0,
            "max_tokens": 256,
        },
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    text = _extract_text(response)
    if text is None:
        sys.stderr.write(f"[DEBUG] LLM returned no text. Response: {json.dumps(response, ensure_ascii=False)[:500]}\n")
    return text


# ── Helpers ─────────────────────────────────────────────────────────

def context_window(song_text, line, radius=2):
    """±radius строк вокруг target_line."""
    if not song_text or not line:
        return ""
    rows = [x.strip() for x in song_text.splitlines() if x.strip()]
    idx = next((i for i, r in enumerate(rows) if line.lower() in r.lower()), -1)
    if idx < 0:
        return "\n".join(rows[:6])[:700]
    lo = max(0, idx - radius)
    hi = min(len(rows), idx + radius + 1)
    return "\n".join(rows[lo:hi])[:900]


def build_uncertainties(refs, web_enabled, search_note):
    if not web_enabled:
        return ["Web search disabled; interpretation is based on lyric text alone."]
    if search_note:
        return [search_note]
    if not refs:
        return ["No supporting evidence found; interpretation is based on lyric text alone."]
    if all(r["confidence"] < STRONG_CONFIDENCE for r in refs):
        return ["Low-confidence sources"]
    return []


# ── Input ───────────────────────────────────────────────────────────

def parse_input():
    raw = sys.argv[1] if len(sys.argv) > 1 else ""
    if not raw and not sys.stdin.isatty():
        raw = sys.stdin.read()
    data = json.loads(raw.strip()) if raw.strip() else {}

    def field(*keys):
        return next((data.get(k, "").strip() for k in keys if data.get(k)), "")

    model = field("model") or MODEL
    if model.startswith("{{"):
        model = MODEL

    return {
        "line":       field("target_line", "line"),
        "song_text":  field("song_text"),
        "song_title": field("song_title"),
        "artist":     field("artist"),
        "model":      model,
    }


# ── Main ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    inp = parse_input()

    if WEB_SEARCH:
        refs, note = search(inp["line"], inp["song_title"], inp["artist"])
    else:
        refs, note = [], None

    output = {
        "meaning":       get_meaning(inp["line"], inp["song_text"], inp["song_title"],
                                     inp["artist"], inp["model"], refs),
        "references":    refs,
        "uncertainties": build_uncertainties(refs, WEB_SEARCH, note),
    }

    print(json.dumps(output, ensure_ascii=False))

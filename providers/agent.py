#!/usr/bin/env python3
import json, os, re, sys, urllib.request, urllib.error, urllib.parse

# --- .env ---
if os.path.exists(".env"):
    for row in open(".env"):
        row = row.strip()
        if not row or row.startswith("#"): continue
        sep = "=" if "=" in row else (":" if ":" in row else None)
        if not sep: continue
        k, v = row.split(sep, 1)
        os.environ.setdefault(k.strip(), v.strip().replace("\\n", "\n"))

def load_llm_config(path="llm_config.json"):
    def as_bool(v, default=True):
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            s = v.strip().lower()
            if s in {"1", "true", "yes", "on"}:
                return True
            if s in {"0", "false", "no", "off"}:
                return False
        return default

    with open(path, "r", encoding="utf-8") as fp:
        cfg = json.load(fp)
    return {
        "model": (cfg.get("openrouter_model") or "").strip(),
        "judge_model": (cfg.get("openrouter_judge_model") or "").strip(),
        "system_prompt": (cfg.get("system_prompt") or "").strip(),
        "user_prompt": (cfg.get("user_prompt") or "").strip(),
        "enable_web_search": as_bool(cfg.get("enable_web_search"), True),
    }

CFG = load_llm_config()
API_KEY    = os.getenv("OPENROUTER_API_KEY")
SEARCH_KEY = os.getenv("SEARCH_API_KEY")
MODEL      = CFG["model"]
SYS_PROMPT = CFG["system_prompt"]
USR_PROMPT = CFG["user_prompt"]
ENABLE_WEB_SEARCH = CFG["enable_web_search"]

HARD_BLOCKED_HOSTS = ("genius.com", "youtube.com", "youtu.be", "facebook.com", "instagram.com", "tiktok.com")
LOW_TRUST_HOSTS = ("azlyrics.com", "lyrics.com", "musixmatch.com", "yarn.co")
BLOCKED_URL_PARTS = ("/smash/get/", "arxiv.org", "researchgate.net")
WEB_NOISE = {"search", "video", "clips", "click", "watch", "share", "download", "online",
             "best", "free", "site", "page", "find", "view", "play", "quote", "about"}

# --- http ---
def post(url, body, headers={}):
    req = urllib.request.Request(url, json.dumps(body).encode(), {**headers, "Content-Type": "application/json"}, method="POST")
    try:
        return json.loads(urllib.request.urlopen(req, timeout=25).read())
    except urllib.error.HTTPError as e:
        try: return {"_error": f"http_{e.code}", "body": e.read().decode("utf-8", "ignore")}
        except: return {"_error": f"http_{e.code}"}
    except Exception as e:
        return {"_error": str(e)}

# --- input ---
def parse_input():
    raw = sys.argv[1] if len(sys.argv) > 1 else ""
    if not raw and not sys.stdin.isatty():
        raw = sys.stdin.read()
    data = json.loads(raw.strip()) if raw.strip() else {}
    line = (data.get("target_line") or data.get("line") or "").strip()
    song_text = (data.get("song_text") or "").strip()
    song_title = (data.get("song_title") or "").strip()
    artist = (data.get("artist") or "").strip()
    model = (data.get("model") or MODEL or "").strip()
    if model.startswith("{{") and model.endswith("}}"): model = MODEL or ""
    model = model.removeprefix("openrouter:")
    return {"line": line, "song_text": song_text, "song_title": song_title, "artist": artist, "model": model}

# --- search ---
def kws(s):
    return {w.strip(".,:;!?()[]{}\"'").lower() for w in s.split() if len(w) > 3}

def host(url):
    try: return urllib.parse.urlparse(url).netloc.lower()
    except: return ""

def context_window(song_text, line, radius=2):
    if not song_text or not line: return ""
    rows = [x.strip() for x in song_text.splitlines() if x.strip()]
    idx = next((i for i, r in enumerate(rows) if line.lower() in r.lower()), -1)
    if idx < 0: return "\n".join(rows[:6])[:700]
    lo, hi = max(0, idx - radius), min(len(rows), idx + radius + 1)
    return "\n".join(rows[lo:hi])[:900]

def build_query(line, song_title="", artist="", strict=True):
    meta = " ".join(x for x in [song_title, artist, "lyrics meaning explained"] if x).strip()
    if strict:
        return f"\"{line}\" {meta}".strip()
    return f"{line} {meta}".strip()

def _score_result(line, song_title, artist, url, snippet):
    q = kws(line)
    meta_q = kws(" ".join([song_title, artist]))
    s_kws = kws(snippet) - WEB_NOISE
    overlap = len(q & s_kws)
    meta_overlap = len(meta_q & s_kws) if meta_q else 0
    if q and overlap == 0 and meta_overlap == 0:
        return None
    extra = len(s_kws - q)
    depth = round(extra / max(len(s_kws), 1), 2)
    prose = len(re.findall(r"[.!?]\s+[A-Z]", snippet))
    if prose < 2:
        depth *= 0.5
    conf = round(min(1, 0.2 + overlap * 0.1 + meta_overlap * 0.06 + depth * 0.45), 2)
    if any(h in host(url) for h in LOW_TRUST_HOSTS):
        conf = round(conf * 0.75, 2)
    why = "Provides interpretive context" if depth > 0.5 else \
          "Partial context beyond the lyric" if depth > 0.25 else \
          "Mostly confirms the quote"
    claim = snippet.lstrip(".-:; ").split(". ")[0][:120].strip() or "Lyric-related reference"
    return {"claim": claim, "url": url, "snippet": snippet[:300], "why_it_supports": why, "confidence": conf}

def _run_search_query(query, max_results=8):
    return post("https://api.tavily.com/search", {"api_key": SEARCH_KEY, "query": query, "max_results": max_results})

def is_blocked_url(url):
    u = (url or "").lower()
    if not u:
        return True
    if u.startswith("ftp://"):
        return True
    if u.endswith(".pdf"):
        return True
    return any(part in u for part in BLOCKED_URL_PARTS)

def search(line, song_title="", artist=""):
    if not SEARCH_KEY:
        return []
    queries = [
        build_query(line, song_title, artist, strict=True),
        build_query(line, song_title, artist, strict=False),
    ]
    seen_urls, out = set(), []
    for qv in queries:
        data = _run_search_query(qv, max_results=8)
        if data.get("_error"):
            continue
        for r in data.get("results", [])[:8]:
            url, sn = r.get("url"), (r.get("content") or "").strip()
            if not url or not sn:
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)
            if is_blocked_url(url):
                continue
            if any(b in host(url) for b in HARD_BLOCKED_HOSTS):
                continue
            scored = _score_result(line, song_title, artist, url, sn)
            if not scored:
                continue
            out.append(scored)
            if len(out) >= 8:
                break
        if len(out) >= 8:
            break
    out = sorted(out, key=lambda r: -r["confidence"])
    strong = [r for r in out if r["confidence"] >= 0.55]
    if strong:
        return strong[:3]
    return out[:2]

def assess_uncertainties(refs, web_search_enabled=True):
    if not web_search_enabled:
        return ["Web search disabled; interpretation is based on lyric text alone."]
    if not refs:
        return ["No supporting evidence found; interpretation is based on lyric text alone."]
    u = []
    avg = sum(r["confidence"] for r in refs) / len(refs)
    if avg < 0.5:
        u.append("Evidence is shallow — sources confirm the quote but add little interpretive context.")
    if all(r["confidence"] < 0.6 for r in refs):
        u.append("No high-confidence sources; interpretation may be speculative.")
    return u

# --- llm ---
def _normalize_content(val):
    """Строку вернуть как есть, массив частей — склеить."""
    if isinstance(val, list):
        return " ".join((x.get("text") or "") if isinstance(x, dict) else str(x) for x in val).strip()
    return (val or "").strip()

def extract_text(data):
    """Достать текст из ответа OpenRouter (content → reasoning → reasoning_details)."""
    try:
        c = (data.get("choices") or [{}])[0]
        m = c.get("message") or {}
        # 1) content — основной ответ
        txt = _normalize_content(m.get("content"))
        # 2) fallback: reasoning-поля (для reasoning-моделей без content)
        if not txt:
            txt = (m.get("reasoning") or c.get("reasoning") or "").strip()
        if not txt and isinstance(m.get("reasoning_details"), list) and m["reasoning_details"]:
            txt = (m["reasoning_details"][0].get("text") or "").strip()
        if not txt: return None
        # 3) если content подозрительно длинный — возможно, модель вклеила CoT
        paras = [p.strip() for p in txt.split("\n\n") if p.strip()]
        if len(paras) > 1 and len(paras[-1]) > 20:
            txt = paras[-1]
        return txt[:500]
    except: return None

def fill_prompt(tpl, **kv):
    for k, v in kv.items(): tpl = tpl.replace("{" + k + "}", v or "")
    return tpl

def local_meaning_fallback(line, song_text):
    basis = (line or "").strip().strip("\"'")
    ctx = context_window(song_text, line)
    if basis:
        return f'The lyric likely conveys emotional tension around "{basis}", suggesting conflict, pressure, or uncertainty in the song context.'
    if ctx:
        return "The excerpt suggests emotional tension and unresolved conflict, with the speaker reacting to pressure and uncertainty."
    return "The lyric suggests emotional tension and uncertainty, with the speaker expressing a conflicted and vulnerable state."

def meaning(line, song_text, song_title, artist, model, refs):
    evidence = "\n".join(f"- {r['url']}: {r['snippet']}" for r in refs) or "None"
    ctx = context_window(song_text, line)
    data = post("https://openrouter.ai/api/v1/chat/completions", {
        "model": model,
        "messages": [
            {"role": "system", "content": SYS_PROMPT},
            {"role": "user",   "content": fill_prompt(
                USR_PROMPT,
                line=line, target_line=line, song_text=song_text[:1800], context_window=ctx,
                song_title=song_title, artist=artist, evidence=evidence
            )},
        ],
        "temperature": 0.7, "max_tokens": 512,
    }, {"Authorization": f"Bearer {API_KEY}"})
    txt = extract_text(data)
    if txt:
        return txt
    return local_meaning_fallback(line, song_text)

# --- main ---
inp = parse_input()
refs = search(inp["line"], inp["song_title"], inp["artist"]) if ENABLE_WEB_SEARCH else []
out = {"meaning": meaning(inp["line"], inp["song_text"], inp["song_title"], inp["artist"], inp["model"], refs), "references": refs, "uncertainties": assess_uncertainties(refs, web_search_enabled=ENABLE_WEB_SEARCH)}
print(json.dumps(out, ensure_ascii=False))

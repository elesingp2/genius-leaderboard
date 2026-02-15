#!/usr/bin/env python3
import json, os, sys, select, urllib.request

# --- .env loader ---
if os.path.exists(".env"):
    for row in open(".env"):
        row = row.strip()
        if row and not row.startswith("#") and "=" in row:
            k, v = row.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
SEARCH_API_KEY     = os.getenv("SEARCH_API_KEY", "")
DEFAULT_MODEL      = os.getenv("OPENROUTER_MODEL", "openai/gpt-5-mini")
SYSTEM_PROMPT      = os.getenv("SYSTEM_PROMPT", "Explain lyrics briefly in 1-2 sentences.")
USER_PROMPT        = os.getenv("USER_PROMPT", "Line: {line}\n\nEvidence:\n{evidence}")

def post(url, body, headers={}):
    req = urllib.request.Request(url, json.dumps(body).encode(), {**headers, "Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=25).read())

def parse_input():
    line, model = "", DEFAULT_MODEL
    if not sys.stdin.isatty():
        r, _, _ = select.select([sys.stdin], [], [], 0.2)
        if r:
            d = json.loads(sys.stdin.read().strip())
            if isinstance(d, str): d = json.loads(d)
            line  = d.get("line", "")
            model = d.get("model", model)
    if not line and len(sys.argv) > 1: line = sys.argv[1]
    if len(sys.argv) > 2: model = sys.argv[2]
    return line.strip(), model.strip().removeprefix("openrouter:")

def search(line):
    if not SEARCH_API_KEY: return []
    data = post("https://api.tavily.com/search", {"api_key": SEARCH_API_KEY, "query": line, "max_results": 3})
    return [
        {"claim": "Reference", "url": r["url"], "snippet": r["content"][:300],
         "why_it_supports": "Relevant context", "confidence": 0.5}
        for r in data.get("results", [])[:3] if r.get("url") and r.get("content")
    ]

def meaning(line, model, refs):
    evidence = "\n".join(f"- {r['url']}: {r['snippet']}" for r in refs) or "None"
    data = post("https://openrouter.ai/api/v1/chat/completions", {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": USER_PROMPT.format(line=line, evidence=evidence)},
        ],
        "temperature": 0.2, "max_tokens": 220,
    }, {"Authorization": f"Bearer {OPENROUTER_API_KEY}"})
    return data["choices"][0]["message"]["content"].strip()

line, model = parse_input()
refs = search(line)
out = {"meaning": meaning(line, model, refs), "references": refs, "uncertainties": []}
print(json.dumps(out, ensure_ascii=False))

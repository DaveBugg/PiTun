"""`search`: find API operations and playbooks for a task.

The index is built from the app's own OpenAPI schema, so it never drifts from
the code. Each hit is short: method, path, one-line purpose, parameters and
the request body fields — enough to write a call without the whole schema.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Tuple

from app.assistant.executor import API_PREFIX, denied

PLAYBOOK_DIR = Path(__file__).parent / "playbooks"
_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "to", "of", "and", "or", "for", "in", "on", "by", "with", "is", "it",
         "api", "this", "that", "be", "as", "at", "from", "get", "set"}


def _tokens(text: str) -> List[str]:
    out = []
    for w in _WORD.findall(text.lower()):
        if w in _STOP:
            continue
        out.append(w)
        if len(w) > 4 and w.endswith("s"):
            out.append(w[:-1])  # crude plural folding: nodes -> node
    return out


def _resolve(schema: dict, comps: dict, depth: int = 0) -> dict:
    if not isinstance(schema, dict) or depth > 3:
        return {}
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return _resolve(comps.get(name, {}), comps, depth + 1)
    for key in ("allOf", "anyOf", "oneOf"):
        if key in schema:
            for sub in schema[key]:
                r = _resolve(sub, comps, depth + 1)
                if r.get("properties") or r.get("type") not in (None, "null"):
                    return r
    return schema


def _type_of(schema: dict, comps: dict) -> str:
    s = _resolve(schema, comps)
    if s.get("enum"):
        return "|".join(str(e) for e in s["enum"][:8])
    t = s.get("type") or ("object" if s.get("properties") else "any")
    if t == "array":
        return f"list[{_type_of(s.get('items') or {}, comps)}]"
    return t


def _fields(schema: dict, comps: dict) -> str:
    s = _resolve(schema, comps)
    props = s.get("properties") or {}
    if not props:
        return _type_of(schema, comps)
    req = set(s.get("required") or [])
    parts = []
    for name, sub in list(props.items())[:40]:
        desc = (_resolve(sub, comps).get("description") or sub.get("description") or "").strip().split("\n")[0]
        parts.append(f"{name}{'*' if name in req else ''}: {_type_of(sub, comps)}"
                     + (f" — {desc[:80]}" if desc else ""))
    return "{ " + "; ".join(parts) + " }"


@lru_cache(maxsize=1)
def _index() -> Tuple[List[dict], Dict[str, float], float]:
    from app.main import app
    spec = app.openapi()
    comps = (spec.get("components") or {}).get("schemas") or {}
    docs: List[dict] = []
    for path, ops in (spec.get("paths") or {}).items():
        if not path.startswith(API_PREFIX) or denied(path):
            continue
        for method, op in ops.items():
            if method.upper() not in ("GET", "POST", "PATCH", "PUT", "DELETE"):
                continue
            summary = (op.get("summary") or "").strip()
            desc = (op.get("description") or "").strip().split("\n\n")[0].replace("\n", " ")
            params = [p for p in op.get("parameters") or [] if p.get("in") in ("query", "path")]
            pstr = ", ".join(
                f"{p['name']}{'*' if p.get('required') else ''}({p['in']}): {_type_of(p.get('schema') or {}, comps)}"
                for p in params)
            body = ""
            rb = ((op.get("requestBody") or {}).get("content") or {}).get("application/json")
            if rb:
                body = _fields(rb.get("schema") or {}, comps)
            text = (f"{method.upper()} {path[len(API_PREFIX):]} — {summary}. {desc[:300]}"
                    + (f"\n  params: {pstr}" if pstr else "")
                    + (f"\n  body: {body}" if body else ""))
            tags = " ".join(op.get("tags") or [])
            docs.append({"kind": "api", "text": text,
                         "tokens": Counter(_tokens(f"{path} {path} {summary} {desc} {tags} {body}"))})
    for f in sorted(PLAYBOOK_DIR.glob("*.md")):
        body = f.read_text(encoding="utf-8")
        title = body.splitlines()[0].lstrip("# ").strip() if body else f.stem
        # Title and keywords weigh more than the body.
        head = "\n".join(body.splitlines()[:4])
        docs.append({"kind": "playbook", "name": f.stem, "title": title, "text": body,
                     "tokens": Counter(_tokens(f"{head} {head} {head} {body}"))})
    df: Counter = Counter()
    for d in docs:
        df.update(set(d["tokens"]))
    n = len(docs) or 1
    idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
    avg = sum(sum(d["tokens"].values()) for d in docs) / n
    return docs, idf, avg


def search(query: str, k: int = 8) -> str:
    docs, idf, avg = _index()
    q = _tokens(query)
    if not q:
        return "Empty query. Describe the task in English keywords, e.g. 'routing rule device node'."
    scored = []
    for d in docs:
        tf = d["tokens"]
        dl = sum(tf.values()) or 1
        s = 0.0
        for t in q:
            if t in tf:
                f = tf[t]
                s += idf.get(t, 0) * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * dl / avg))
        if d["kind"] == "playbook":
            s *= 1.3
        if s > 0:
            scored.append((s, d))
    scored.sort(key=lambda x: -x[0])
    top = scored[:max(1, min(int(k or 8), 15))]
    if not top:
        return "No matches. Try other English keywords."
    out = []
    shown_playbook = False
    for _, d in top:
        if d["kind"] == "playbook":
            if not shown_playbook:
                out.append(f"PLAYBOOK {d['name']}:\n{d['text'][:3500]}")
                shown_playbook = True
            else:
                out.append(f"PLAYBOOK {d['name']}: {d['title']} (search its name to read it)")
        else:
            out.append(d["text"])
    return "\n\n".join(out)


def reset_cache() -> None:
    _index.cache_clear()

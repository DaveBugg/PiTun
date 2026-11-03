"""What a subscription-linked circle must NOT take, however often the
subscription refreshes.

Without this a refresh re-adds every node the subscription serves, so a node
the operator unticked in the circle came straight back. Two memories fix it:

* `excluded_node_ids` — nodes unticked by hand; filled automatically when a
  linked circle is saved with fewer subscription nodes than it had.
* `excluded_countries` — ISO codes (e.g. RU) whose nodes never join.

The country of a node is the observed exit (`Node.country`), falling back to
the flag its name starts with.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, List, Optional, Set

_RI_BASE = 0x1F1E6
_RI_LAST = 0x1F1FF


def parse_list(raw: Any) -> list:
    if isinstance(raw, list):
        return raw
    try:
        v = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return v if isinstance(v, list) else []


def parse_ids(raw: Any) -> Set[int]:
    return {int(i) for i in parse_list(raw) if isinstance(i, int) or (isinstance(i, str) and i.isdigit())}


def normalize_countries(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    for v in values or []:
        code = str(v).strip().upper()
        if re.fullmatch(r"[A-Z]{2}", code) and code not in out:
            out.append(code)
    return out


def _flag_code(name: str) -> Optional[str]:
    chars = [ord(c) for c in (name or "")[:2]]
    if len(chars) == 2 and all(_RI_BASE <= c <= _RI_LAST for c in chars):
        return "".join(chr(65 + c - _RI_BASE) for c in chars)
    return None


def node_country(country: Optional[str], name: str) -> Optional[str]:
    """The node's country code: observed exit first, then its name's flag."""
    if country and len(country.strip()) == 2:
        return country.strip().upper()
    return _flag_code(name)


def excluded_here(node_id: int, country: Optional[str], name: str,
                  ex_ids: Set[int], ex_countries: Set[str]) -> bool:
    if node_id in ex_ids:
        return True
    code = node_country(country, name)
    return bool(code and code in ex_countries)

"""과거 유사 요구 검색. 외부 임베딩 API 없이 문자 n-gram TF-IDF 코사인 유사도로 동작(사내 PC에서 완결).
지표·기준일이 같으면 가중치를 더한다."""
import math, re
from collections import Counter
import db

def _grams(text: str, n: int = 2) -> Counter:
    t = re.sub(r"\s+", "", text or "").lower()
    return Counter(t[i:i + n] for i in range(max(len(t) - n + 1, 1)))

def _cos(a: Counter, b: Counter) -> float:
    if not a or not b: return 0.0
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    return dot / (math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values())) or 1)

def similar_items(query_item: dict, top: int = 5) -> list[dict]:
    """새 요구 항목 1개 → 과거 항목들과의 유사도 상위 top"""
    q = _grams(query_item.get("item_text", ""))
    res = []
    for it in db.all_items_with_requests():
        s = _cos(q, _grams(it["item_text"]))
        if query_item.get("indicator") and it.get("indicator") == query_item["indicator"]: s += 0.3
        if query_item.get("base_date") and it.get("base_date") == query_item["base_date"]: s += 0.2
        res.append({"score": round(min(s, 1.0), 2), "request_id": it["request_id"], "requester": it["requester"],
                    "received_date": it["received_date"], "title": it["title"], "item_text": it["item_text"],
                    "indicator": it.get("indicator"), "base_date": it.get("base_date")})
    res.sort(key=lambda x: -x["score"])
    return [x for x in res[:top] if x["score"] > 0.15]

def similar_requests(text: str, top: int = 3) -> list[dict]:
    q = _grams(text)
    res = []
    for r in db.list_requests():
        s = _cos(q, _grams((r.get("title") or "") + " " + (r.get("raw_text") or "")))
        res.append({"score": round(s, 2), **{k: r[k] for k in ("id", "requester", "received_date", "title")}})
    res.sort(key=lambda x: -x["score"])
    return res[:top]

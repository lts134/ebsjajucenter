"""참고 문서(지침·매뉴얼·FAQ) 저장과 검색. 사업 자체에 대한 문의("대상이 누구냐", "비용은", "운영 시간은")에 문서 문구를 근거로 답하기 위한 것.

- ingest(): PDF는 쪽 단위, 그 밖(hwp·hwpx·docx·txt)은 본문을 쪽 크기로 나눠 조각(약 600자, 겹침 80자)으로 저장한다.
- search(): 질문에서 2글자 이상 토큰을 뽑고(조사 붙은 긴 토큰은 앞부분 2~4글자 조각도 함께) 조각마다 맞은 토큰 길이의 합으로 점수를 매긴다. 임베딩·외부 서비스 없이 동작.
- TOOLS/HANDLERS: 모델이 쓰는 도구(search_docs·list_docs). 답은 찾은 문구와 출처(문서·쪽)만 근거로."""
from __future__ import annotations
import io, re
import db, docread

CHUNK, OVERLAP = 600, 80
STOP = {"어떻게", "언제", "무엇", "얼마", "얼마인가요", "얼마나", "있나", "있어", "있나요", "하나요", "인가요", "되나요", "되나", "알려", "알려줘", "그리고", "대해", "대한", "관련", "문의", "질문", "센터", "자기주도학습센터", "EBS", "ebs", "누구", "누구인가", "무엇인가"}

def _pages(name: str, data: bytes) -> list[str]:
    n = name.lower()
    if n.endswith(".pdf"):
        from pypdf import PdfReader
        return [(pg.extract_text() or "") for pg in PdfReader(io.BytesIO(data)).pages]
    text = docread.read(name, data)
    return [text[i:i + 3000] for i in range(0, max(len(text), 1), 3000)]

def _clean(t: str) -> str:
    t = re.sub(r"[ \t ]+", " ", t); t = re.sub(r"\n{3,}", "\n\n", t)
    return "\n".join(l.strip() for l in t.splitlines()).strip()

def chunk_pages(pages: list[str]) -> list[tuple[int, int, str]]:
    """[(쪽, 순번, 글)] — 쪽 안에서 문단·문장 경계를 살려 CHUNK 길이로 자르고 OVERLAP만큼 겹친다."""
    out = []
    for pno, raw in enumerate(pages, 1):
        text = _clean(raw)
        if not text: continue
        if len(text) <= CHUNK: out.append((pno, 0, text)); continue
        seq, start = 0, 0
        while start < len(text):
            end = min(len(text), start + CHUNK)
            if end < len(text):
                cut = max(text.rfind("\n", start + CHUNK // 2, end), text.rfind(". ", start + CHUNK // 2, end), text.rfind("다.", start + CHUNK // 2, end))
                if cut > start: end = cut + 1
            out.append((pno, seq, text[start:end].strip())); seq += 1
            if end >= len(text): break
            start = max(end - OVERLAP, start + 1)
    return out

def ingest(name: str, data: bytes, title: str | None, user: str, note: str = "") -> dict:
    pages = _pages(name, data); chunks = chunk_pages(pages)
    if not chunks: raise ValueError("문서에서 글을 읽지 못했습니다(스캔 이미지 PDF는 글자 추출이 안 됩니다).")
    title = (title or "").strip() or re.sub(r"\.[^.]+$", "", name)
    did = db.add_ref_doc(title, name, len(pages), chunks, user, note)
    return {"doc_id": did, "title": title, "pages": len(pages), "chunks": len(chunks), "chars": sum(len(t) for _, _, t in chunks)}

def tokens(q: str) -> list[str]:
    toks = []
    for t in re.findall(r"[가-힣A-Za-z0-9]{2,}", q or ""):
        if t in STOP: continue
        toks.append(t)
        if re.fullmatch(r"[가-힣]{3,}", t):                        # 조사가 붙은 말('운영시간은' '학부모의')은 앞부분도 후보로
            for k in (len(t) - 1, len(t) - 2):
                if k >= 2: toks.append(t[:k])
    return list(dict.fromkeys(toks))

def search(q: str, k: int = 5, doc_id=None) -> list[dict]:
    """조각 점수 = 맞은 토큰 길이 합(같은 토큰 반복은 1.5배까지) + 모든 토큰이 맞으면 보너스. 상위 k개."""
    toks = tokens(q)
    if not toks: return []
    hits = []
    for c in db.ref_chunks(doc_id):
        text = c["text"]; low = text.lower(); score = 0.0; matched = 0
        for t in toks:
            n = low.count(t.lower())
            if n: matched += 1; score += len(t) * min(1.5, 1 + 0.25 * (n - 1))
        if matched: hits.append((score + (3 if matched == len(toks) else 0), c))
    hits.sort(key=lambda x: -x[0])
    return [{"doc": c["title"], "page": c["page"], "score": round(s, 2), "text": c["text"]} for s, c in hits[:k]]

def answer_lines(q: str, k: int = 3) -> list[str]:
    """규칙 경로(키 없음)용: 찾은 문구를 출처와 함께 그대로 보여 준다."""
    hits = search(q, k)
    if not hits: return []
    return [f"참고 문서에서 찾은 문구({len(hits)}건, 키워드 검색):"] + [f"- 「{h['doc']}」 {h['page']}쪽: {h['text'][:300].replace(chr(10), ' ')}{'…' if len(h['text']) > 300 else ''}" for h in hits]

TOOLS = [
    {"name": "search_docs", "description": "참고 문서(사업 지침·운영 매뉴얼·FAQ)에서 질문과 관련된 문구를 찾는다. 사업 자체에 대한 질문(대상·비용·운영 시간·인원 구성·절차·근거 법령 등)은 이것으로 찾은 문구만 근거로 답하고 문서 이름과 쪽을 밝힌다. 없으면 '참고 문서에 없음'.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "핵심어 2~5개(예: '학부모 상담 주기')"}, "k": {"type": "integer", "description": "가져올 조각 수(기본 5)"}}, "required": ["query"]}},
    {"name": "list_docs", "description": "등록된 참고 문서 목록(제목·쪽수·등록일).", "input_schema": {"type": "object", "properties": {}}},
]
HANDLERS = {
    "search_docs": lambda query, k=5: {"hits": [{"doc": h["doc"], "page": h["page"], "text": h["text"]} for h in search(query, min(int(k or 5), 8))], "note": "문구는 원문 그대로. 답에는 문서 이름과 쪽을 함께 적는다."},
    "list_docs": lambda: [{k: d[k] for k in ("id", "title", "pages", "uploaded_at", "note")} for d in db.list_ref_docs()],
}

"""참고 문서: 조각내기, 등록(같은 제목 교체), 검색 점수, 규칙 경로 답 줄, 도구 표."""
import refdocs, agent, history_qa

TXT = ("4-1. 운영 기간\n연중 평일 · 주 5일 운영(월~금), 14:00 ~ 22:00\n※ 학교 방학기간 중 평일 운영 시간(10:00 ~ 18:00)\n주말 운영은 지역 여건에 따라 탄력 운영\n\n"
       "1. 사업 개요\n목적 사교육 부담 완화, 자기주도학습 역량 강화\n대상 중1 ~ 고3 학생\n비용 무료 (교육부, 지자체 예산 활용)\n" + ("운영 인력 배치 기준 소형 코디 1명 행정 1명.\n" * 40))

def test_chunking_keeps_pages_and_overlaps():
    chunks = refdocs.chunk_pages(["짧은 쪽", TXT, ""])
    assert chunks[0] == (1, 0, "짧은 쪽") and all(len(t) <= refdocs.CHUNK + 5 for _, _, t in chunks) and {p for p, _, _ in chunks} == {1, 2}
    seq2 = [c for c in chunks if c[0] == 2]; assert len(seq2) >= 3 and seq2[1][2][:30] in TXT

def test_ingest_search_and_replace(fresh_db):
    db = fresh_db
    r = refdocs.ingest("지침.txt", TXT.encode("utf-8"), "관리·운영지침 v2.0", "u", "개정")
    assert r["title"] == "관리·운영지침 v2.0" and r["chunks"] >= 2 and db.list_ref_docs()[0]["n_chunks"] == r["chunks"]
    hits = refdocs.search("운영 시간은 어떻게 되나요")
    assert hits and hits[0]["doc"] == "관리·운영지침 v2.0" and "14:00 ~ 22:00" in hits[0]["text"] and hits[0]["page"] == 1
    assert "중1 ~ 고3" in refdocs.search("이용 대상 학생")[0]["text"] and refdocs.search("ㅋ") == [] and refdocs.search("블록체인") == []
    assert refdocs.tokens("운영시간은 얼마인가요")[:3] == ["운영시간은", "운영시간", "운영시"]
    lines = refdocs.answer_lines("비용"); assert lines[0].startswith("참고 문서에서 찾은 문구") and "무료" in lines[1]
    refdocs.ingest("지침.txt", "새 내용 운영 시간 09:00".encode("utf-8"), "관리·운영지침 v2.0", "u")       # 같은 제목 → 교체
    assert len(db.list_ref_docs()) == 1 and "09:00" in refdocs.search("운영 시간")[0]["text"]
    db.delete_ref_doc(db.list_ref_docs()[0]["id"]); assert db.list_ref_docs() == [] and refdocs.search("운영") == []

def test_tools_registered_everywhere(fresh_db):
    names = {t["name"] for t in agent.TOOLS}; assert {"search_docs", "list_docs", "center_info", "list_centers"} <= names
    hs = agent.handlers(agent.Case(), None); assert names == set(hs)
    assert hs["search_docs"](query="운영")["hits"] == [] and hs["list_docs"]() == [] and "없습니다" in hs["center_info"](name="없는센터")["note"] and hs["list_centers"]()["n"] == 0
    assert {t["name"] for t in history_qa.TOOLS} == set(history_qa.HANDLERS) and "search_docs" in history_qa.SYSTEM

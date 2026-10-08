"""가진 자료 맞춤: 요구 이름이 저장 지표와 달라도 뜻이 같거나 포괄하면 연결하고, 설명 항목은 참고 문서 발췌로 초안을 쓴다.
사용자 사례: '1 사업 필요성 2 센터별 투입 인력 현황 3 센터별 월별 관리인원 4 센터별 월별 등원율' + 저장 자료 '현원·퇴소자·학습코디네이터·행정지원인력·등원율'."""
import calendar
import match, agent, docread, normalize


NAMES = ["등원율", "현원", "퇴소자", "학습코디네이터 수", "행정지원인력 수", "등록 학생 수"]

def test_rule_match_synonyms_and_umbrella():
    assert match.rule_match("센터별 월별 등원율", NAMES) == ["등원율"]
    assert match.rule_match("센터별 월별 관리인원", NAMES) == ["현원"]                                  # 사전 동의어(관리인원 ↔ 현원), '학생'으로 등록 학생 수에 걸리지 않음
    assert match.rule_match("센터별 투입 인력 현황", NAMES) == ["학습코디네이터 수", "행정지원인력 수"]   # 포괄 요구 → 관련 지표 둘
    assert match.rule_match("센터별 월별 퇴소자 수", NAMES) == ["퇴소자"] and match.rule_match("센터별 월별 하차 학생 수", NAMES) == ["퇴소자"]
    assert match.rule_match("자기주도학습센터 사업 필요성", NAMES) == [] and match.rule_match("예산 집행률", NAMES) == []
    assert match.rule_match("아무거나", []) == []


def _seed(db):
    months = [f"2026-{m:02d}-{calendar.monthrange(2026, m)[1]:02d}" for m in range(1, 7)]
    rows = []
    for d in months:
        for i in range(1, 4):
            rows += [{"indicator": "등원율", "center": f"센터{i}", "base_date": d, "value": 60 + i}, {"indicator": "현원", "center": f"센터{i}", "base_date": d, "value": 100 + i},
                     {"indicator": "퇴소자", "center": f"센터{i}", "base_date": d, "value": i}]
    rows += [{"indicator": "학습코디네이터 수", "center": f"센터{i}", "base_date": "2026-06-30", "value": 2} for i in range(1, 4)]
    rows += [{"indicator": "행정지원인력 수", "center": f"센터{i}", "base_date": "2026-06-30", "value": 1} for i in range(1, 4)]
    db.add_data_batch(rows, "u", "센터별 월별 현황.xlsx")
    db.add_ref_doc("관리운영지침", "지침.pdf", 10, [(3, 0, "자기주도학습센터 사업 필요성: 지역 간 교육 격차를 줄이고 학생의 자기주도학습 습관 형성을 지원하기 위해 센터를 운영한다."),
                                                 (5, 0, "운영 시간은 평일 14:00~22:00이다.")], "u")


def test_match_items_expands_and_keeps_order(fresh_db):
    db = fresh_db; _seed(db)
    items = normalize.normalize_items([{"item_text": "자기주도학습센터 사업 필요성"}, {"item_text": "센터별 투입 인력 현황"}, {"item_text": "센터별 월별 관리인원"}, {"item_text": "센터별 월별 등원율"}])
    out, notes = match.match_items(items, use_llm=False)
    assert [(it["item_text"], it.get("indicator")) for it in out] == [("자기주도학습센터 사업 필요성", None), ("센터별 투입 인력 현황", "학습코디네이터 수"), ("센터별 투입 인력 현황", "행정지원인력 수"),
                                                                     ("센터별 월별 관리인원", "현원"), ("센터별 월별 등원율", "등원율")]
    assert len(notes) == 2 and notes[0].startswith("'센터별 투입 인력 현황' → 학습코디네이터 수·행정지원인력 수") and out[1]["matched_by"].startswith("가진 자료 맞춤")
    assert db.indicator_catalog()[0]["indicator"] == "등록 학생 수" or {c["indicator"] for c in db.indicator_catalog()} >= {"현원", "등원율"}


def test_user_scenario_end_to_end(fresh_db):
    """파일 없이 말로 받은 4개 항목 → 3개는 가진 자료로, 1개는 참고 문서 발췌로. [확인 필요]가 남지 않는다."""
    db = fresh_db; _seed(db)
    case = agent.Case()
    r = agent.describe_request(case, requester="테스트 의원실", items=["자기주도학습센터 사업 필요성", "센터별 투입 인력 현황", "센터별 월별 관리인원", "센터별 월별 등원율"])
    assert len(r["items"]) == 5 and r["matched"] and r["unknown_indicator"] == ["자기주도학습센터 사업 필요성"]
    lines = agent.autopilot(case, None, register=False)
    assert any("가진 자료에 맞춘 항목" in l for l in lines) and case["plans"][0]["mode"] == "docs" and "자기주도학습센터 사업 필요성" in case["doc_hits"]
    v = case["values"]; assert set(v["indicator"]) == {"학습코디네이터 수", "행정지원인력 수", "현원", "등원율"} and len(v[v["indicator"] == "현원"]) == 18
    body = case["draft"]["본문"]
    assert "참고 문서 「관리운영지침」 3쪽에 따르면" in body and "교육 격차" in body and "[확인 필요" not in body and "보유 자료 '현원'로 작성" in body
    assert case["coverage"]["판정"].tolist() == ["충족"] * 5
    text = docread.read("x.hwpx", case["hwpx"])
    assert "※ 출처: 「관리운영지침」 3쪽" in text and "[확인 필요] 보유 자료 없음" not in text and "□ 현원" in text and "□ 학습코디네이터 수" in text
    assert not any("자료가 없어" in l for l in lines)


def test_llm_match_applies_model_choice(fresh_db, monkeypatch):
    db = fresh_db; _seed(db)
    monkeypatch.setattr(match.llm, "available", lambda: True)
    monkeypatch.setattr(match.llm, "ask_json", lambda *a, **k: {"items": [{"item_text": "센터 이용 학생 변동", "indicators": ["현원", "퇴소자", "없는 지표"], "reason": "현원과 퇴소를 함께 봐야 한다"}]})
    out, notes = match.match_items([{"item_text": "센터 이용 학생 변동", "indicator": None}])
    assert [it["indicator"] for it in out] == ["현원", "퇴소자"] and notes == ["'센터 이용 학생 변동' → 현원·퇴소자 (모델: 현원과 퇴소를 함께 봐야 한다)"]
    monkeypatch.setattr(match.llm, "ask_json", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out2, notes2 = match.match_items([{"item_text": "센터 이용 학생 변동", "indicator": None}])
    assert out2[0].get("indicator") is None and notes2 and "모델 맞춤 실패" in notes2[0]


def test_extraction_sees_catalog_and_keeps_model_judgement(fresh_db, monkeypatch):
    """추출 모델은 가진 자료 목록(이름·정의·기간)을 보고 보유 이름을 직접 고르며, 목록에 없으면 힌트(suggested)를 남긴다. 사전은 더 이상 '드롭다운'이 아니다."""
    import extract
    db = fresh_db; _seed(db)
    cat = extract.stored_catalog(); names = extract.catalog_names(cat)
    assert {"현원", "퇴소자", "학습코디네이터 수"} <= set(names) and "등원율" in names and "현원" in extract.catalog_text(cat) and "센터 3곳" in extract.catalog_text(cat)
    sch = extract.schema(cat)["properties"]["items"]["items"]
    assert "현원" in sch["properties"]["indicator"]["anyOf"][0]["enum"] and sch["properties"]["data_candidates"]["items"]["enum"] == [c["indicator"] for c in cat]
    assert "가진 자료 목록" in (extract.PROMPT % ("a", extract.catalog_text(cat), "2026-10-08", "원문"))
    monkeypatch.setattr(extract.llm, "available", lambda: True)
    monkeypatch.setattr(extract.llm, "ask_json", lambda *a, **k: {"requester": "감사실", "received_date": None, "due_date": None, "title": "t", "items": [
        {"item_text": "센터별 월별 관리인원", "indicator": "현원", "base_date": None, "period": "월별", "unit": "센터별", "data_candidates": ["현원"], "suggested": None},
        {"item_text": "센터별 투입 인력 현황", "indicator": None, "base_date": None, "period": None, "unit": "센터별", "data_candidates": ["학습코디네이터 수", "행정지원인력 수", "없는 이름"], "suggested": None},
        {"item_text": "사업 필요성", "indicator": None, "base_date": None, "period": None, "unit": None, "data_candidates": [], "suggested": "사업 추진 배경·필요성 설명 요구"}]})
    res, how = extract.extract("요구서 본문")
    assert how.startswith("Claude") and res["items"][0]["indicator"] == "현원" and res["items"][1]["data_candidates"] == ["학습코디네이터 수", "행정지원인력 수"] and res["items"][2]["suggested"].startswith("사업 추진")
    items = normalize.normalize_items(res["items"]); assert items[0]["indicator"] == "현원"                      # 보유 이름은 사전 동의어('관리인원')로 바꾸지 않는다(값이 없는 이름이 됨)
    out, notes = match.match_items(items, use_llm=False)
    assert [(it["item_text"], it["indicator"]) for it in out][:3] == [("센터별 월별 관리인원", "현원"), ("센터별 투입 인력 현황", "학습코디네이터 수"), ("센터별 투입 인력 현황", "행정지원인력 수")]
    assert notes == ["'센터별 투입 인력 현황' → 학습코디네이터 수·행정지원인력 수 (모델 판단: 요구서를 읽을 때 가진 자료 목록에서 고름)"] and out[3]["indicator"] is None and out[3]["suggested"]
    kept = normalize.normalize_items([{"item_text": "x", "indicator": "모델이 지은 이름"}]); assert kept[0]["indicator"] is None and kept[0]["indicator_hint"] == "모델이 지은 이름"


def test_agent_can_decide_item_indicator(fresh_db):
    """모델이 자동 맞춤을 고치는 도구: 지표 지정 → 항목 펼침·되돌림 → 다시 처리하면 그 자료로 값이 들어온다."""
    db = fresh_db; _seed(db)
    case = agent.Case(); agent.describe_request(case, requester="감사실", items=["센터 운영 관련 기타 사항", "센터별 월별 등원율"])
    assert case["items"][0]["indicator"] is None and len(case["items"]) == 2
    r = agent.set_item_indicator(case, 1, ["현원", "퇴소자"])
    assert r["n_items"] == 3 and [it["indicator"] for it in case["items"]] == ["현원", "퇴소자", "등원율"] and case["values"] is None and case["match_notes"][-1].endswith("(지정)")
    import pytest
    with pytest.raises(ValueError): agent.set_item_indicator(case, 1, ["없는 지표"])
    with pytest.raises(ValueError): agent.set_item_indicator(case, 9, ["현원"])
    agent.autopilot(case, None, register=False)
    assert set(case["values"]["indicator"]) == {"현원", "퇴소자", "등원율"} and "□ 현원" in docread.read("x.hwpx", case["hwpx"])
    agent.set_item_indicator(case, 1, []); assert [it["indicator"] for it in case["items"]] == [None, "등원율"] and case["draft"] is None
    hs = agent.handlers(agent.Case(), None); assert "set_item_indicator" in hs and {t["name"] for t in agent.TOOLS} == set(hs)
    assert "당신이 판단" in agent.SYSTEM and "set_item_indicator" in agent.SYSTEM

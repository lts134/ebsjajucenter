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

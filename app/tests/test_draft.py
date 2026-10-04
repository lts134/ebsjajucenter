import pandas as pd, draft

REQ = {"requester": "의원실", "received_date": "2026-09-15", "due_date": "2026-09-22", "title": "국감 자료"}
ITEMS = [{"item_text": "2026. 6. 30. 기준 센터별 등원율", "indicator": "등원율", "base_date": "2026-06-30"},
         {"item_text": "최근 3년 운영 예산 및 집행률", "indicator": "운영 예산", "base_date": None},
         {"item_text": "최근 3년 운영 예산 및 집행률", "indicator": "예산 집행률", "base_date": None}]
VALUES = pd.DataFrame({"indicator": ["등원율"] * 2, "center": ["센터A", "센터B"], "base_date": ["2026-06-30"] * 2, "value": [60.3, 59.4]})

def test_rule_draft_marks_missing_and_dedupes_item_text():
    d = draft.rule_draft(REQ, ITEMS, VALUES, {("등원율", "센터A", "2026-06-30"): "보정"}, {"definition": "정의"})
    assert d["본문"].count("최근 3년 운영 예산 및 집행률") == 1          # 지표 2개로 나뉜 같은 원문은 한 번만
    assert "[확인 필요" in d["본문"] and "운영 예산, 예산 집행률" in d["본문"]
    assert "센터A 등원율" in d["차이사유"] and "지표 정의: 정의" in d["산출근거"] and "[확인 필요]" in d["산출근거"]

def test_coverage_check_statuses():
    d = draft.rule_draft(REQ, ITEMS, VALUES, {}, {})
    cov = draft.coverage_check(ITEMS, d, VALUES)
    assert cov["판정"].tolist() == ["충족", "미확인 표시", "미확인 표시"]
    cov2 = draft.coverage_check(ITEMS, {"본문": "아무 말"}, VALUES)
    assert cov2["판정"].tolist() == ["누락", "누락", "누락"]

def test_compare_summary_and_unique_texts():
    assert draft.compare_summary(None) is None
    s = draft.compare_summary([{"판정": "차이", "center": "센터C", "base_date": "2026-06-30"}, {"판정": "일치", "센터": "센터A", "기준일": "2026-06-30"}])
    assert s == {"차이": ["센터C(2026-06-30)"], "일치": ["센터A(2026-06-30)"]}
    assert draft.unique_texts(ITEMS) == ["2026. 6. 30. 기준 센터별 등원율", "최근 3년 운영 예산 및 집행률"]

def test_make_draft_without_api_uses_rules():
    d, how = draft.make_draft(REQ, ITEMS, VALUES, {}, {})
    assert how.startswith("규칙 기반") and d["제목"]

import extract, normalize
from testcases import CASES

def test_norm_date_formats():
    f = extract._norm_date
    assert f("2026. 6. 30.") == "2026-06-30"
    assert f("'26. 9. 26.(금) 18:00") == "2026-09-26"
    assert f("2026-06-30") == "2026-06-30"
    assert f("9/19", 2026) == "2026-09-19"
    assert f("6월 30일", 2025) == "2025-06-30"
    assert f("없음") is None

def test_period_and_unit():
    assert extract._period("2024~2026년 연도별 운영 센터 수") == "2024~2026"
    assert extract._period("2026년 1~6월 센터별 예산") == "2026년 1~6월"
    assert extract._period("최근 3년 운영 예산") == "최근 3년"
    assert extract._unit("센터별 등원율") == "센터별"
    assert extract._unit("전국 센터 수") == "전체"

def test_rule_based_all_cases_hit_must_pairs():
    """testcases 정답표의 필수(지표, 기준일) 쌍을 규칙 경로가 모두 재현하고, 금지 쌍은 내지 않는다."""
    for c in CASES:
        res = extract.rule_based(c["text"])
        res["items"] = normalize.normalize_items(res["items"])
        got = {(i["indicator"], i["base_date"]) for i in res["items"]}
        exp = c["expected"]
        assert set(exp["must"]) <= got, (c["id"], exp["must"], got)
        assert not (set(exp.get("not", [])) & got), c["id"]
        if exp.get("due"): assert res["due_date"] == exp["due"], c["id"]
        if exp.get("received"): assert res["received_date"] == exp["received"], c["id"]
        if exp.get("requester"): assert exp["requester"] in (res["requester"] or ""), c["id"]

def test_rule_based_skips_official_letter_body_numbering():
    text = "1. 관련: 감사실-1100(2026. 7. 30.)\n2. 아래 자료를 2026. 8. 18.까지 제출하여 주시기 바랍니다.\n  ○ 2026. 6. 30. 기준 센터별 등원율"
    res = extract.rule_based(text)
    assert [i["item_text"] for i in res["items"]] == ["2026. 6. 30. 기준 센터별 등원율"]
    assert res["due_date"] == "2026-08-18"

def test_schema_restricts_indicator_to_dictionary():
    sch = extract.schema()
    enum = sch["properties"]["items"]["items"]["properties"]["indicator"]["anyOf"][0]["enum"]
    assert set(enum) == set(normalize.CANON)
    assert sch["additionalProperties"] is False

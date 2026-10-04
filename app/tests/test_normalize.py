import normalize

def test_longest_alias_wins():
    assert normalize.normalize("센터별 예산 집행률 현황") == ("예산 집행률", "예산 집행률")
    assert normalize.normalize("등록 학생 수") == ("등록 학생 수", "등록 학생 수")

def test_normalize_all_splits_multiple_indicators_in_order():
    got = normalize.normalize_all("최근 3년 센터 운영 예산 및 집행률")
    assert [g[0] for g in got] == ["운영 예산", "예산 집행률"]
    got = normalize.normalize_all("2026. 6. 30. 기준 센터별 등원율 및 등록 학생 수")
    assert [g[0] for g in got] == ["등원율", "등록 학생 수"]

def test_normalize_items_drops_invented_indicator():
    items = [{"item_text": "만족도 조사 결과", "indicator": "만족도"}, {"item_text": "출석률", "indicator": None}, {"item_text": "x", "indicator": "집행률"}]
    out = normalize.normalize_items(items)
    assert out[0]["indicator"] is None
    assert out[1]["indicator"] == "등원율" and out[1]["matched_by"] == "출석률"
    assert out[2]["indicator"] == "예산 집행률"

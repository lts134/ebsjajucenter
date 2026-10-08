import search, suggest

def test_similar_items_and_requests_use_history(fresh_db):
    db = fresh_db
    rid = db.add_request("의원실", "2026-07-01", "2026-07-08", "운영 현황", "2026. 6. 30. 기준 센터별 등원율", "a.txt",
                         [{"item_text": "2026. 6. 30. 기준 센터별 등원율", "indicator": "등원율", "base_date": "2026-06-30"}])
    hits = search.similar_items({"item_text": "2026. 6. 30. 기준 센터별 등원율", "indicator": "등원율", "base_date": "2026-06-30"})
    assert hits and hits[0]["request_id"] == rid and hits[0]["score"] == 1.0
    assert search.similar_requests("센터별 등원율 자료 요구")[0]["id"] == rid
    assert not search.similar_items({"item_text": "전혀 다른 내용 zzz"})

def test_suggest_logic(fresh_db):
    db = fresh_db
    assert "인식하지 못함" in suggest.suggest({"indicator": None})["판단"]
    assert "기준일이 없음" in suggest.suggest({"indicator": "운영 예산", "base_date": None})["판단"]
    assert "이력 없음" in suggest.suggest({"indicator": "등원율", "base_date": "2026-06-30"})["판단"]
    rid = db.add_request("감사실", "2026-05-12", "2026-05-20", "t", "r", "f", [])
    db.add_submission(rid, "2026-05-20", "u", "f", "confirmed", "n", [{"indicator": "등원율", "center": "센터A", "base_date": "2026-06-30", "value": 1}])
    s = suggest.suggest({"indicator": "등원율", "base_date": "2026-06-30"})
    assert "재사용" in s["판단"] and "감사실" in s["과거 제출"]
    db.add_data_batch([{"indicator": "등원율", "center": "센터A", "base_date": "2026-06-30", "value": 2}], "u", "집계.xlsx")
    s = suggest.suggest({"indicator": "등원율", "base_date": "2026-06-30"})
    assert s["판단"].startswith("지표 데이터에 있음(1건)") and "대조" in s["판단"] and "감사실" in s["과거 제출"]

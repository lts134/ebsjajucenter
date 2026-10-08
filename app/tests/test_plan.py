import plan

def _load_months(db, ind="등원율", months=("2025-12-31", "2026-01-31", "2026-02-28", "2026-03-31", "2026-06-30", "2026-09-30")):
    db.add_data_batch([{"indicator": ind, "center": c, "base_date": d, "value": 1.0} for d in months for c in ("센터A", "센터B")], "u", "집계.xls")

def test_plan_monthly_without_base_date(fresh_db):
    db = fresh_db; _load_months(db)
    pl = plan.plan_item({"item_text": "센터별 월별 등원율", "indicator": "등원율", "base_date": None, "unit": "센터별"})
    assert pl["mode"] == "all" and pl["source"] == "data" and len(pl["dates"]) == 6 and pl["dates"][-1] == "2026-09-30" and pl["n_rows"] == 12
    assert "월별" in pl["why"] and "2026-09-30" in pl["why"] and "전부 제안" in pl["why"]

def test_plan_latest_nearest_exact_and_ranges(fresh_db):
    db = fresh_db; _load_months(db)
    latest = plan.plan_item({"item_text": "센터별 등원율", "indicator": "등원율", "base_date": None})
    assert latest["mode"] == "latest" and latest["dates"] == ["2026-09-30"] and "최신" in latest["why"]
    exact = plan.plan_item({"item_text": "x", "indicator": "등원율", "base_date": "2026-06-30"}); assert exact["mode"] == "exact" and exact["dates"] == ["2026-06-30"]
    near = plan.plan_item({"item_text": "x", "indicator": "등원율", "base_date": "2026-07-15"}); assert near["mode"] == "nearest" and near["dates"] == ["2026-06-30"] and "기준일 차이" in near["why"]
    none = plan.plan_item({"item_text": "x", "indicator": "등원율", "base_date": "2025-01-31"}); assert none["mode"] == "none"
    rng = plan.plan_item({"item_text": "2026년 1~3월 월별 등원율", "indicator": "등원율", "base_date": None, "period": "2026년 1~3월"})
    assert rng["dates"] == ["2026-01-31", "2026-02-28", "2026-03-31"]
    recent = plan.plan_item({"item_text": "최근 3개월 월별 등원율", "indicator": "등원율", "base_date": None}, anchor_date="2026-09-15")
    assert recent["dates"] == ["2026-09-30"] or recent["dates"][-1] == "2026-09-30"          # 접수일 기준 최근 3개월(7~9월)에 있는 자료
    yearly = plan.plan_item({"item_text": "연도별 등원율", "indicator": "등원율", "base_date": None}); assert yearly["mode"] == "yearly" and yearly["dates"] == ["2025-12-31", "2026-09-30"]
    unknown = plan.plan_item({"item_text": "???", "indicator": None, "base_date": None}); assert unknown["mode"] == "unknown_indicator"
    absent = plan.plan_item({"item_text": "x", "indicator": "운영 예산", "base_date": None}); assert absent["mode"] == "none" and "새로 산출" in absent["why"]

def test_plan_falls_back_to_past_submissions(fresh_db):
    db = fresh_db
    rid = db.add_request("감사실", "2026-05-12", "2026-05-20", "t", "r", "f", [])
    db.add_submission(rid, "2026-05-20", "u", "f", "confirmed", "n", [{"indicator": "등록 학생 수", "center": "센터A", "base_date": "2026-03-31", "value": 10}])
    pl = plan.plan_item({"item_text": "등록 학생 수", "indicator": "등록 학생 수", "base_date": None})
    assert pl["source"] == "past" and pl["mode"] == "latest" and pl["dates"] == ["2026-03-31"]
    rows = plan.rows_for("등록 학생 수", pl["dates"], "past"); assert len(rows) == 1 and rows[0]["source_file"].startswith("기록 제출본 #")
    _load_months(db, "등록 학생 수", ("2026-04-30",))
    pl2 = plan.plan_item({"item_text": "등록 학생 수", "indicator": "등록 학생 수", "base_date": None}); assert pl2["source"] == "data" and pl2["dates"] == ["2026-04-30"]

def test_plan_explicit_range_gives_every_month_and_reads_spoken_years(fresh_db):
    """'25년 12월부터 26년 8월까지' 같은 말로 받은 기간: 두 자리 연도·부터/까지를 읽고, '월별'이 없어도 기간 안의 기준일을 전부 제안."""
    db = fresh_db; _load_months(db, months=("2025-11-30", "2025-12-31", "2026-01-31", "2026-02-28", "2026-08-31", "2026-09-30"))
    assert plan._norm_text("25년 12월부터 26년 8월까지 센터별 등원율") == "2025년 12월 ~ 2026년 8월 센터별 등원율"
    pl = plan.plan_item({"item_text": "25년 12월부터 26년 8월까지 전체 센터의 센터별 등원율", "indicator": "등원율", "base_date": None})
    assert pl["mode"] == "range" and pl["dates"] == ["2025-12-31", "2026-01-31", "2026-02-28", "2026-08-31"] and "2025년 12월 ~ 2026년 8월" in pl["why"] and "기간(" not in pl["why"]
    one = plan.plan_item({"item_text": "2026년 8월 ~ 2026년 9월 등원율", "indicator": "등원율", "base_date": None}); assert one["dates"] == ["2026-08-31", "2026-09-30"]
    year = plan.plan_item({"item_text": "2026년 센터별 등원율", "indicator": "등원율", "base_date": None}); assert year["mode"] == "latest" and year["dates"] == ["2026-09-30"]   # '2026년'만은 연도 → 최신
    outside = plan.plan_item({"item_text": "2024년 1월 ~ 2024년 6월 등원율", "indicator": "등원율", "base_date": None}); assert outside["mode"] == "latest" and "해당하는 자료가 없어" in outside["why"]

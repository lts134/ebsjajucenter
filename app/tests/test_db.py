def test_request_submission_reason_draft_review_roundtrip(fresh_db):
    db = fresh_db
    rid = db.add_request("감사실", "2026-08-11", "2026-08-18", "자체감사", "원문", "a.txt",
                         [{"item_text": "등원율", "indicator": "등원율", "base_date": "2026-06-30", "unit": "센터별", "period": None}])
    sid = db.add_submission(rid, "2026-08-18", "홍담당", "v.xlsx", "confirmed", "n",
                            [{"indicator": "등원율", "center": "센터A", "base_date": "2026-06-30", "value": 60.3, "definition": "d", "calc_period": "p", "extract_date": "e", "source_version": "v1", "source_file": "v.xlsx", "source_sheet": "Sheet1", "source_row": 2}])
    assert db.get_items(rid)[0]["indicator"] == "등원율"
    past = db.past_values_for("등원율", "2026-06-30"); assert len(past) == 1 and past[0]["requester"] == "감사실" and past[0]["source_row"] == 2
    db.add_reason(sid, "등원율", "센터A", "2026-06-30", 60.3, 61.0, "보정", "홍담당")
    assert db.reasons_for("등원율", "센터A", "2026-06-30")[0]["entered_by"] == "홍담당"
    did = db.add_draft(sid, rid, {"제목": "t", "본문": "b", "차이사유": "r", "산출근거": "p"}, "x.hwpx", "review_requested")
    assert len(db.list_drafts("review_requested")) == 1
    db.add_review(did, "김팀장", "approved", "ok")
    assert db.list_drafts()[0]["status"] == "approved" and db.reviews_for(did)[0]["reviewer"] == "김팀장"
    ov = db.request_overview(); assert ov[0]["n_items"] == 1 and ov[0]["n_confirmed"] == 1
    by_req, by_ind = db.requester_stats(); assert by_req[0]["requester"] == "감사실" and by_ind[0]["indicator"] == "등원율"
    s, vals = db.latest_confirmed_values(rid); assert s == sid and vals[0]["value"] == 60.3

def test_migration_adds_period_column(fresh_db, tmp_path):
    import sqlite3
    db = fresh_db
    con = sqlite3.connect(db.DB_PATH)
    con.executescript("CREATE TABLE items(id INTEGER PRIMARY KEY, request_id INTEGER, seq INTEGER, item_text TEXT, indicator TEXT, base_date TEXT, unit TEXT);"
                      "CREATE TABLE submission_values(id INTEGER PRIMARY KEY, submission_id INTEGER, indicator TEXT, center TEXT, base_date TEXT, value REAL, definition TEXT, calc_period TEXT, extract_date TEXT, source_version TEXT);")
    con.commit(); con.close()
    c = db.connect()
    cols = {r[1] for r in c.execute("PRAGMA table_info(items)")}; vcols = {r[1] for r in c.execute("PRAGMA table_info(submission_values)")}
    assert "period" in cols and {"source_file", "source_sheet", "source_row"} <= vcols

def test_edit_and_delete_cascade(fresh_db):
    db = fresh_db
    rid = db.add_request("감사실", "2026-08-11", "2026-08-18", "자체감사", "원문", "a.txt", [{"item_text": "등원율", "indicator": "등원율", "base_date": "2026-06-30"}])
    rid2 = db.add_request("의원실", "2026-09-01", "2026-09-08", "국감", "원문2", "b.txt", [{"item_text": "학생 수"}])
    sid = db.add_submission(rid, "2026-08-18", "홍", "v.xlsx", "confirmed", "n", [{"indicator": "등원율", "center": "센터A", "base_date": "2026-06-30", "value": 60.3}, {"indicator": "등원율", "center": "센터B", "base_date": "2026-06-30", "value": 59.4}])
    db.add_reason(sid, "등원율", "센터A", "2026-06-30", 60.3, 61.0, "보정", "홍")
    did = db.add_draft(sid, rid, {"제목": "t", "본문": "b"}, "x.hwpx", "review_requested"); db.add_review(did, "김", "approved", "ok")
    # 수정
    db.update_request(rid, "감사실(수정)", "2026-08-12", "", "자체감사 2차")
    r = db.get_request(rid); assert r["requester"] == "감사실(수정)" and r["due_date"] is None and r["title"] == "자체감사 2차" and r["raw_text"] == "원문"
    assert db.replace_items(rid, [{"item_text": "등록 학생 수", "indicator": "등록 학생 수"}, {"item_text": ""}, {"item_text": "예산", "indicator": None}]) == 2
    assert [i["seq"] for i in db.get_items(rid)] == [1, 2]
    # 값 교체: 빈 행·값 없는 행은 버림, source_row 문자열 허용
    n = db.replace_values(sid, [{"indicator": "등원율", "center": "센터A", "base_date": "2026-06-30", "value": 61.0, "source_row": "7.0"}, {"indicator": "", "center": "x", "base_date": "y", "value": 1}, {"indicator": "등원율", "center": "센터C", "base_date": "2026-06-30", "value": None}])
    vals = db.get_values(sid); assert n == 1 and len(vals) == 1 and vals[0]["value"] == 61.0 and vals[0]["source_row"] == 7
    # 초안 삭제
    db.delete_draft(did); assert db.list_drafts() == [] and db.reviews_for(did) == []
    # 제출본 삭제 → 값·사유 함께
    db.add_draft(sid, rid, {"제목": "t2"}, None, "draft")
    info = db.delete_submission(sid); assert info == {"values": 1, "drafts": 1}
    assert db.get_values(sid) == [] and db.reasons_for("등원율", "센터A", "2026-06-30") == [] and db.list_drafts() == [] and db.list_submissions(rid) == []
    # 요구서 삭제 → 다른 요구서는 그대로
    sid2 = db.add_submission(rid, "2026-08-19", "홍", "w.xlsx", "confirmed", "", [{"indicator": "등원율", "center": "센터A", "base_date": "2026-06-30", "value": 1}])
    db.add_draft(sid2, rid, {"제목": "t3"}, None, "draft")
    info = db.delete_request(rid); assert info["submissions"] == 1 and info["drafts"] == 1 and info["values"] == 1 and info["items"] == 2
    assert [r["id"] for r in db.list_requests()] == [rid2] and db.get_items(rid) == [] and db.list_submissions() == [] and db.list_drafts() == []
    assert db.past_values_for("등원율", "2026-06-30") == []

def test_indicator_data_store(fresh_db):
    db = fresh_db
    v = lambda c, val, bd="2026-06-30": {"indicator": "등원율", "center": c, "base_date": bd, "value": val, "definition": "d"}
    bid, n = db.add_data_batch([v("센터A", 60.3), v("센터B", 59.4), {"indicator": "", "center": "x", "base_date": "y", "value": 1}, v("센터C", None)], "홍", "6월집계.xlsx", "메모")
    assert n == 2 and [r["center"] for r in db.data_values_for("등원율", "2026-06-30")] == ["센터A", "센터B"]
    # 같은 키 재적재 → 새 값으로 대체, 다른 기준일은 추가
    bid2, n2 = db.add_data_batch([v("센터A", 61.0), v("센터A", 70.0, "2026-07-31")], "홍", "7월집계.xlsx")
    rows = db.data_values_for("등원율", "2026-06-30"); assert len(rows) == 2 and {r["center"]: r["value"] for r in rows} == {"센터A": 61.0, "센터B": 59.4}
    assert db.data_dates_for("등원율") == ["2026-07-31", "2026-06-30"] and db.data_count() == 3
    cov = db.data_coverage(); assert [(c["indicator"], c["base_date"], c["n_centers"]) for c in cov] == [("등원율", "2026-07-31", 1), ("등원율", "2026-06-30", 2)]
    bl = db.list_data_batches(); assert bl[0]["id"] == bid2 and bl[0]["n_rows"] == 2 and bl[1]["n_live"] == 1   # 첫 묶음의 센터A는 대체돼 1건만 남음
    assert db.delete_data_batch(bid2) == 2 and db.data_count() == 1 and db.data_values_for("등원율", "2026-06-30")[0]["center"] == "센터B"

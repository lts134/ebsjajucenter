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

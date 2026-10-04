"""코드 리뷰(2026-10-04)에서 나온 결함의 회귀 테스트와 최적화 항목 검증."""
import sqlite3, pandas as pd, pytest
import compare, pii, search, llm, extract, history_qa, draft, assist

class F:
    def __init__(self, name, data): self.name, self._d = name, data
    def getvalue(self): return self._d

def test_duplicate_key_rows_are_rejected_with_row_numbers():
    csv = "지표명,센터명,기준일,값\n등원율,센터C,2026-06-30,1\n등원율,센터C,2026-06-30,2\n".encode()
    with pytest.raises(ValueError, match="중복된 행: \\[2, 3\\]"): compare.load_values(F("d.csv", csv))

def test_blank_csv_line_keeps_excel_row_numbers_and_pii_uses_them():
    csv = "지표명,센터명,기준일,값,메모\n등원율,센터A,2026-06-30,1,\n\n등원율,센터B,2026-06-30,2,010-1234-5678\n".encode()
    df = compare.load_values(F("b.csv", csv))
    assert df["source_row"].tolist() == [2, 4]
    hits = pii.scan_df(df, "t"); assert hits and hits[0]["위치"].endswith("4행")

def test_excel_datetime_provenance_equals_text_date():
    assert compare.clean_text(pd.Timestamp("2026-07-03")) == "2026-07-03"
    assert compare.clean_text("2026-07-03 00:00:00") == "2026-07-03"
    assert compare.clean_text("  출결  v1 ") == "출결 v1" and compare.clean_text(float("nan")) is None

def test_redact_masks_contact_patterns_but_keeps_dates():
    t, n = pii.redact("담당 010-1234-5678 / a.b@ebs.co.kr / 2026-06-30 기준 / 900101-1234567")
    assert n == 3 and "[전화번호]" in t and "[이메일]" in t and "[주민번호]" in t and "2026-06-30" in t

def test_empty_text_similarity_is_zero():
    assert search._cos(search._grams(""), search._grams("")) == 0.0
    assert search._cos(search._grams("등원율"), search._grams("")) == 0.0

def test_claude_model_preference_beats_resolved(monkeypatch):
    monkeypatch.setenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001"); llm._RESOLVED = "claude-sonnet-4-6"
    assert llm._model_order()[0] == "claude-haiku-4-5-20251001" and llm.model_label() == "claude-haiku-4-5-20251001"
    llm._RESOLVED = None

def test_year_hint_from_due_or_document(monkeypatch):
    monkeypatch.setenv("APP_TODAY", "2031-01-15")
    r = extract.rule_based("제출 기한: 2029. 1. 20.\n1. 12월 31일 기준 센터별 등원율")
    assert r["items"][0]["base_date"] == "2029-12-31"           # 접수일이 없으면 기한의 연도
    r2 = extract.rule_based("2028년 국정감사 자료\n1) 센터 현황 — 9월 1일 기준")
    assert r2["items"][0]["base_date"] == "2028-09-01"          # 문서의 다른 연도
    r3 = extract.rule_based("1) 센터 현황 — 9월 1일 기준")
    assert r3["items"][0]["base_date"] == "2031-09-01"          # 마지막은 오늘(APP_TODAY)

def test_db_lock_is_not_treated_as_corruption(fresh_db, monkeypatch):
    db = fresh_db
    db.connect().close()
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")))
    with pytest.raises(sqlite3.OperationalError): db.connect()
    assert db.DB_PATH.exists() and not (db.DB_PATH.parent / (db.DB_PATH.name + ".broken")).exists()

def test_blank_items_are_not_saved_and_do_not_crash_checks(fresh_db):
    db = fresh_db
    rid = db.add_request("x", "2026-01-01", "2026-01-02", "t", "r", "f", [{"item_text": None, "indicator": None}, {"item_text": float("nan")}, {"item_text": " 등원율 ", "indicator": "등원율", "base_date": "2026-06-30"}])
    its = db.get_items(rid); assert len(its) == 1 and its[0]["seq"] == 1 and its[0]["item_text"] == "등원율"
    items = [{"item_text": None, "indicator": "등원율", "base_date": "2026-06-30"}]
    values = pd.DataFrame({"indicator": ["등원율"], "center": ["센터A"], "base_date": ["2026-06-30"], "value": [1.0]})
    assert draft.coverage_check(items, {"본문": "등원율"}, values)["판정"].tolist() == ["충족"]
    assert assist.foresee_rule({"requester": "x"}, items, values, {}, None, {})

def test_tool_results_strip_staff_names_and_indicator_history(fresh_db):
    db = fresh_db
    rid = db.add_request("감사실", "2026-05-12", "2026-05-20", "t", "r", "f", [])
    sid = db.add_submission(rid, "2026-05-20", "홍담당", "v.xlsx", "confirmed", "n", [{"indicator": "등원율", "center": "센터A", "base_date": "2026-06-30", "value": 60.3, "extract_date": "2026-05-15", "source_version": "v1"}])
    db.add_reason(sid, "등원율", "센터A", "2026-06-30", 60.3, 61.0, "보정", "홍담당")
    H = history_qa.HANDLERS
    d = H["request_detail"](request_id=rid); assert "submitted_by" not in d["submissions"][0]
    assert all("entered_by" not in r for r in H["diff_reasons"](indicator="등원율"))
    h = H["indicator_history"](indicator="등원율")
    assert h[0]["requester"] == "감사실" and h[0]["n_values"] == 1 and h[0]["source_version"] == "v1"
    assert H["indicator_history"](indicator="등원율", base_date="2026-01-01") == []
    assert any(t["name"] == "indicator_history" for t in history_qa.TOOLS)

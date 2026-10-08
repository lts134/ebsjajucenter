"""1차 권장 반영분: 조직 설정 영속화, 문구 서랍, 백업·복원, 머리 정보만 고치기(set_header), 메일 문안 발신 표기."""
import os
import agent, draft


def test_settings_roundtrip_and_env_fallback(fresh_db, monkeypatch):
    db = fresh_db
    monkeypatch.setenv("DEPT_HEAD", "환경부서장"); monkeypatch.setenv("DEPT_PHONE", "02-000-0000")
    s = db.get_settings(); assert s["dept_head"] == "환경부서장" and s["org_name"] == "지역교육협력부" and s["company"] == "한국교육방송공사"
    db.set_settings({"dept_head": "홍길동", "dept_phone": " 1234 ", "org_name": "지역교육협력부"}, "담당자")
    s = db.get_settings(); assert s["dept_head"] == "홍길동" and s["dept_phone"] == "1234" and s["company"] == "한국교육방송공사"
    db.set_settings({"dept_head": ""}, "담당자"); assert db.get_settings()["dept_head"] == "환경부서장"            # 비우면 환경변수 보조값


def test_phrases_upsert_count_candidates(fresh_db):
    db = fresh_db
    a = db.add_phrase("사유", "  출결 사후   보정 반영 ", "u"); b = db.add_phrase("사유", "출결 사후 보정 반영", "u")
    assert a == b and db.list_phrases("사유")[0]["use_count"] == 2 and db.add_phrase("사유", "   ") is None
    db.add_phrase("정의", "월 등원일수 ÷ 운영일수 × 100", "u")
    rid = db.add_request("기관", "2026-09-01", "2026-09-10", "t", "원문", "f.txt", [{"item_text": "등원율", "indicator": "등원율", "base_date": "2026-06-30"}])
    sid = db.add_submission(rid, "2026-09-05", "u", "f.xlsx", "confirmed", "", [{"indicator": "등원율", "center": "A", "base_date": "2026-06-30", "value": 1.0}])
    db.add_reason(sid, "등원율", "A", "2026-06-30", 1.0, 2.0, "추출 시점 차이(재산출)", "u")
    c = db.phrase_candidates("사유"); assert c[0] == "출결 사후 보정 반영" and "추출 시점 차이(재산출)" in c and "월 등원일수" not in " ".join(c)
    db.delete_phrase(a); assert db.list_phrases("사유") == []


def test_backup_snapshot_and_restore(fresh_db):
    db = fresh_db
    r1 = db.add_request("기관A", "2026-09-01", "2026-09-10", "첫 요구", "원문", "a.txt", [])
    snap = db.snapshot_bytes(); assert snap.startswith(b"SQLite format 3\x00") and len(snap) > 10_000
    db.add_request("기관B", "2026-09-02", "2026-09-11", "둘째 요구", "원문", "b.txt", []); assert len(db.list_requests()) == 2
    res = db.restore_from_bytes(snap, "담당자")
    assert res["counts"]["requests"] == 1 and len(db.list_requests()) == 1 and db.get_request(r1)["title"] == "첫 요구" and res["backup"].endswith("_before_restore.db")
    bks = db.list_backups(); assert bks and bks[0]["name"] == res["backup"]
    info = db.db_info(); assert info["tables"]["요구서"] == 1 and info["size_kb"] >= 0
    import pytest
    with pytest.raises(ValueError): db.restore_from_bytes(b"not a database")


def test_set_header_updates_record_and_marks_stale(fresh_db):
    db = fresh_db
    db.add_data_batch([{"indicator": "등원율", "center": "센터A", "base_date": "2026-06-30", "value": 60.0}], "u", "x")
    case = agent.Case(); agent.describe_request(case, items=["2026-06-30 기준 센터별 등원율"]); agent.autopilot(case, None)
    r = agent.set_header(case, requester="○○○ 의원실", due_date="2026. 10. 15.", received_date=None)
    assert r["changed"] == {"requester": "○○○ 의원실", "due_date": "2026-10-15"} and case["draft_stale"] and case["hwpx_stale"]
    assert db.get_request(case["request_id"])["requester"] == "○○○ 의원실" and db.get_request(case["request_id"])["due_date"] == "2026-10-15"
    assert agent.set_header(case, requester="○○○ 의원실")["changed"] == {}                     # 같은 값은 변경 없음
    agent.step_draft(case); agent.step_hwpx(case, None); assert not case["hwpx_stale"] and case["hwpx_name"].startswith("답변자료_○○○ 의원실_")


def test_mail_text_org():
    m = draft.mail_text({"requester": "감사실"}, {"제목": "t"}, "a.hwpx", "담당자", org="EBS 지역교육협력부")
    assert m["body"].endswith("EBS 지역교육협력부 담당자 드림")


def test_requester_dictionary_and_canon(fresh_db):
    db = fresh_db
    assert db.requester_key("○○○ 의원실(교육위원회)") == db.requester_key("○○○의원실") == db.requester_key(" ○○○ 의원실 ")
    rid = db.add_request("○○○ 의원실(교육위원회)", "2026-09-01", "2026-09-10", "t", "원문", "f.txt", [])
    assert db.get_request(rid)["requester"] == "○○○ 의원실(교육위원회)"                   # 사전이 없으면 그대로
    db.upsert_requester("○○○ 의원실", ["○○○의원실", "○○○ 의원"], "의원실")
    assert db.canonical_requester("○○○ 의원실(교육위원회)") == "○○○ 의원실" and db.canonical_requester("○○○  의원") == "○○○ 의원실" and db.canonical_requester("감사실") == "감사실"
    assert db.apply_requester_canon() == 1 and db.get_request(rid)["requester"] == "○○○ 의원실"
    rid2 = db.add_request("○○○의원실", "2026-09-02", "2026-09-11", "t2", "원문", "f.txt", []); assert db.get_request(rid2)["requester"] == "○○○ 의원실"
    assert db.requester_names()[0] == "○○○ 의원실" and len(db.requester_stats()[0]) == 1          # 현황 '요청 주체별'이 한 기관으로 합쳐진다
    db.upsert_requester("○○○ 의원실", ["○○○ 의원실", "새 별칭"], None, "메모"); r = db.list_requesters()[0]; assert r["aliases"] == ["새 별칭"] and r["kind"] == "의원실" and r["note"] == "메모"
    db.delete_requester(r["id"]); assert db.list_requesters() == [] and db.canonical_requester("○○○의원실") == "○○○의원실"


def test_request_status_flow(fresh_db):
    db = fresh_db
    rid = db.add_request("감사실", "2026-09-01", "2026-09-10", "t", "원문", "f.txt", [{"item_text": "등원율", "indicator": "등원율", "base_date": "2026-06-30"}])
    assert db.get_request(rid)["status"] == "접수"
    db.advance_request_status(rid, "처리 중"); assert db.get_request(rid)["status"] == "처리 중"
    db.advance_request_status(rid, "접수"); assert db.get_request(rid)["status"] == "처리 중"                     # 자동 전이는 뒤로 가지 않는다
    sid = db.add_submission(rid, "2026-09-05", "u", "f.xlsx", "confirmed", "", [{"indicator": "등원율", "center": "A", "base_date": "2026-06-30", "value": 1.0}])
    assert db.get_request(rid)["status"] == "확정"
    did = db.add_draft(sid, rid, {"제목": "t", "본문": "b"}, None, status="approved")
    db.add_dispatch(sid, did, "2026-09-06", "감사실", "메일", "u"); assert db.get_request(rid)["status"] == "발송"
    db.set_request_status(rid, "종결", "홍길동", "완료"); r = db.get_request(rid); assert (r["status"], r["assignee"], r["memo"]) == ("종결", "홍길동", "완료")
    db.advance_request_status(rid, "확정"); assert db.get_request(rid)["status"] == "종결"
    db.set_request_status(rid, "보류"); db.advance_request_status(rid, "처리 중"); assert db.get_request(rid)["status"] == "처리 중"   # 보류 건은 일이 움직이면 앞으로
    import pytest
    with pytest.raises(ValueError): db.set_request_status(rid, "없는 상태")
    ov = db.request_overview()[0]; assert ov["status"] == "처리 중" and ov["assignee"] == "홍길동" and ov["memo"] == "완료"


def test_indicator_dict_edit_reload_reset(fresh_db):
    import normalize, extract, suggest
    db = fresh_db
    assert db.get_indicator_dict() == [] and normalize.load_from_db() == 0 and "등원율" in normalize.CANON
    rows = [{"canon": "등원율", "aliases": "등원율, 출석률, 센터 이용률", "source": "출결시스템", "owner": "담당 A", "note": ""},
            {"canon": "야간 이용 학생 수", "aliases": ["야간 이용", "야간 학생"], "source": "", "owner": "", "note": "새 지표"}, {"canon": "", "aliases": "x"}]
    assert db.save_indicator_dict(rows) == 2 and normalize.load_from_db() == 2
    assert set(normalize.CANON) == {"등원율", "야간 이용 학생 수"} and normalize.normalize("센터 야간 이용 현황")[0] == "야간 이용 학생 수" and normalize.normalize("등록 학생 수")[0] is None
    assert extract.INDICATORS == ["등원율", "야간 이용 학생 수"] and suggest.CATALOG["등원율"] == ("출결시스템", "담당 A") and "등록 학생 수" in suggest.DEFAULT_CATALOG
    db.save_indicator_dict([]); normalize.reset_defaults()
    assert normalize.CANON == normalize.DEFAULT_CANON and "등록 학생 수" in extract.INDICATORS and suggest.CATALOG == suggest.DEFAULT_CATALOG


def test_data_matrix_missing_centers_signature(fresh_db):
    db = fresh_db
    s0 = db.signature()
    db.replace_centers([{"center_id": "1", "name": "EBS 계룡 센터", "aliases": [], "year25": 1, "year26": 1, "status": None}, {"center_id": "2", "name": "EBS 영월 센터", "aliases": [], "year25": 1, "year26": 1, "status": "취소"},
                        {"center_id": "3", "name": "EBS 담양 센터", "aliases": [], "year25": 1, "year26": 1, "status": None}], "t")
    db.add_data_batch([{"indicator": "등원율", "center": "EBS계룡센터", "base_date": "2026-06-30", "value": 1.0}, {"indicator": "등원율", "center": "EBS 계룡 센터", "base_date": "2026-07-31", "value": 2.0}], "u", "x")
    assert db.signature() != s0
    assert db.data_matrix() == [{"indicator": "등원율", "base_date": "2026-06-30", "n": 1}, {"indicator": "등원율", "base_date": "2026-07-31", "n": 1}]
    assert db.missing_centers("등원율", "2026-07-31") == ["EBS 담양 센터"]                  # 취소된 센터는 세지 않고, 표기가 달라도 있는 것은 있는 것

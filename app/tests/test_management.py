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

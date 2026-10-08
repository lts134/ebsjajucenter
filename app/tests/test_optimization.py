"""최적화 단계에서 고친 것들의 회귀 테스트: 데이터 계층(날짜·센터 표기), 대조 오차, 개인정보 경계, 지시문 판별, 인코딩, 파일 이름,
값 중복, 산출 근거의 nan, 답변자료 본문 번호·소수 표기·날짜 줄, 기간 표현, 대시보드 숫자, 대화 경계 요약, 메일 문안."""
import datetime as dt
import pandas as pd
import pytest
import db, compare, pii, agent, docread, draft, hwpx_build, plan, dashboard_import


def test_norm_date_shapes():
    f = db.norm_date
    assert f("2026. 6. 30.") == "2026-06-30" and f("'26.6.30") == "2026-06-30" and f("2026년 6월 30일") == "2026-06-30" and f("2026.6") == "2026-06-30"
    assert f(46203) == (dt.date(1899, 12, 30) + dt.timedelta(days=46203)).isoformat() and f(dt.date(2026, 6, 30)) == "2026-06-30"
    assert f(None) is None and f(float("nan")) is None and f("abc") is None and f("abc", keep_unparsed=True) == "abc"


def test_center_spelling_unified_across_batches(fresh_db):
    db_ = fresh_db
    assert db_.center_key("EBS 계룡 자기주도학습센터") == db_.center_key("ebs계룡자기주도학습센터") == db_.center_key("EBS계룡 자기주도 학습센터")
    v = lambda c, val: {"indicator": "등원율", "center": c, "base_date": "2026-06-30", "value": val}
    db_.add_data_batch([v("EBS 계룡 센터", 1.0)], "u", "a")
    bid2, n = db_.add_data_batch([v("EBS계룡센터", 2.0)], "u", "b")                      # 다른 표기 → 같은 행으로(먼저 저장된 표기 유지)
    rows = db_.data_values_for("등원율", "2026-06-30"); assert n == 1 and len(rows) == 1 and rows[0]["center"] == "EBS 계룡 센터" and rows[0]["value"] == 2.0
    db_.delete_data_batch(bid2); assert db_.data_values_for("등원율", "2026-06-30")[0]["value"] == 1.0


def test_compare_tolerance_boundary():
    meta = {"definition": "", "calc_period": "", "extract_date": "", "source_version": ""}
    new = pd.DataFrame([{"indicator": "등원율", "center": "EBS계룡센터", "base_date": "2026-06-30", "value": 67.9, **meta}])
    old = pd.DataFrame([{"indicator": "등원율", "center": "EBS 계룡 센터", "base_date": "2026-06-30", "value": 67.8, **meta}])
    assert compare.compare(new, old, 0.1)["판정"].tolist() == ["일치"]            # 표기가 달라도 같은 센터로 짝을 맞추고, 차이 0.1은 오차 0.1 안
    assert compare.compare(new, old, 0.05)["판정"].tolist() == ["차이"] and compare.compare(new, old, 0.0)["판정"].tolist() == ["차이"]


def test_pii_particle_boundaries_and_dates():
    assert len(pii.scan_text("연락처 010-1234-5678로 주세요")) == 1 and len(pii.scan_text("☎ 02-123-4567, 031.123.4567")) == 2
    assert pii.scan_text("2026-06-30 기준 등원율 61.0, 접수번호 20261234567890") == []


def test_is_do_it_questions_are_not_commands():
    assert agent.is_do_it("이 의뢰서에서 요구하는 것들 작성해 줘") and agent.is_do_it("회신 초안 만들어") and agent.is_do_it("처리해줘")
    assert not agent.is_do_it("회신 기한이 언제야?") and not agent.is_do_it("감사실에 등원율 언제 냈지") and not agent.is_do_it("센터 현황 보여 줘")


def test_decode_text_encodings():
    t = "센터별 등원율 61.0%"
    assert docread.decode_text(t.encode("utf-8")) == t and docread.decode_text(t.encode("cp949")) == t and docread.decode_text(t.encode("utf-16")) == t and docread.decode_text(("\ufeff" + t).encode("utf-8")) == t


def test_safe_filename_strips_forbidden_chars():
    n = agent.safe_filename("답변자료_○○ 의원실/감사:2026*.hwpx"); assert "/" not in n and ":" not in n and "*" not in n and n.endswith(".hwpx")
    assert agent.safe_filename("  ...  ") == "파일"


def test_step_pull_dedupes_same_indicator_items(fresh_db):
    db_ = fresh_db
    db_.add_data_batch([{"indicator": "등원율", "center": f"센터{i}", "base_date": "2026-06-30", "value": 50 + i} for i in range(3)], "u", "x")
    case = agent.Case(); agent.describe_request(case, items=["2026-06-30 기준 센터별 등원율", "등원율 산출 방식"])
    agent.step_plan(case); r = agent.step_pull(case)
    assert len(case["values"]) == 3 and r["n_rows"] >= 3 and not case["values"].duplicated(["indicator", "center", "base_date"]).any()


def test_provenance_skips_nan_and_none_strings():
    vals = pd.DataFrame([{"indicator": "등원율", "center": "A", "base_date": "2026-06-30", "value": 1.0, "definition": "nan", "calc_period": None, "extract_date": "None", "source_version": "v2"},
                         {"indicator": "등원율", "center": "B", "base_date": "2026-06-30", "value": 2.0, "definition": float("nan"), "calc_period": "2026-06", "extract_date": "", "source_version": "v2"}])
    p = draft.provenance_by_indicator(vals)["등원율"]
    assert "정의" not in p and "추출시점" not in p and p["집계기간"] == "2026-06" and p["원자료 버전"] == "v2"


def test_split_body_keeps_date_lines_and_item_regex():
    pre, by = hwpx_build.split_body("1. 2026. 6. 30. 기준 등원율은 아래 표와 같습니다.\n2025. 12. 31. 기준 값은 별지와 같습니다.\n6.30 기준 수치입니다.", 1)
    assert pre == [] and by[1] == ["2026. 6. 30. 기준 등원율은 아래 표와 같습니다.", "2025. 12. 31. 기준 값은 별지와 같습니다.", "6.30 기준 수치입니다."]   # 날짜로 시작하는 줄은 번호가 아니다
    m = hwpx_build.ITEM_RE.match
    assert m("1.등원율") and m("12) 항목") and m("3. [확인 필요]") and not m("6.30 기준") and not m("2025. 12. 31.") and not m("100. 번호 아님")


def test_fmt_keeps_decimals_as_given():
    assert hwpx_build._decimals(pd.Series([61.25, 60.3])) == 2 and hwpx_build._decimals(pd.Series([188.0, 1095.0])) == 0
    hdr, rows = hwpx_build._table_for(pd.DataFrame([{"indicator": "등원율", "center": "A", "base_date": "2026-06-30", "value": 61.25}, {"indicator": "등원율", "center": "B", "base_date": "2026-06-30", "value": 60.3}]))
    assert rows == [["A", "61.25"], ["B", "60.30"]] and hwpx_build._fmt(66.4) == "66.4" and hwpx_build._fmt(1234.0) == "1,234" and hwpx_build._fmt(None) == "-"
    assert hwpx_build._is_month_end("2026-06-30") and not hwpx_build._is_month_end("2026-09-15")


def test_build_reply_numbers_follow_unique_item_text():
    items = [{"item_text": "센터별 등원율 및 등록 학생 수", "indicator": "등원율", "base_date": "2026-06-30"}, {"item_text": "센터별 등원율 및 등록 학생 수", "indicator": "등록 학생 수", "base_date": "2026-06-30"},
             {"item_text": "운영 예산", "indicator": "운영 예산", "base_date": None}]
    assert [t for t, _ in hwpx_build.item_groups(items)] == draft.unique_texts(items) == ["센터별 등원율 및 등록 학생 수", "운영 예산"]
    values = pd.DataFrame([{"indicator": "등원율", "center": "A", "base_date": "2026-06-30", "value": 60.3}, {"indicator": "등록 학생 수", "center": "A", "base_date": "2026-06-30", "value": 188.0}])
    d = draft.rule_draft({"requester": "○○ 의원실", "title": "t"}, items, values, {}, {})
    out = hwpx_build.build_reply({"requester": "○○ 의원실", "title": "t"}, items, values, d)
    text = docread.read("x.hwpx", out)
    assert "1. 센터별 등원율 및 등록 학생 수" in text and "2. 운영 예산" in text and "3. " not in text.split("붙임")[0]      # 지표 둘로 나뉜 항목이 한 번호, 표는 지표마다
    assert text.count("□ ") == 2 and "[확인 필요] 보유 자료 없음" in text


def test_plan_windows_half_quarter_dotted_range():
    assert plan._window("2026년 상반기 센터별 등원율", None)[:2] == ("2026-01-01", "2026-06-30") and plan._window("2026년 하반기", None)[:2] == ("2026-07-01", "2026-12-31")
    assert plan._window("2026년 2분기 등원율", None)[:2] == ("2026-04-01", "2026-06-30") and plan._window("2025. 12. ~ 2026. 8. 월별 등원율", None)[:2] == ("2025-12-01", "2026-08-31")
    assert plan._window("2026. 9. 15. ~ 2026. 9. 30. 기간 이용자", None)[0] is None                 # 일 단위 기간은 읽지 않고(최신 제안) 글자도 망가뜨리지 않는다
    assert not plan.QUARTERLY.search("2026년 2분기") and plan.QUARTERLY.search("분기별 등원율")


def test_dashboard_numbers_tolerate_dashes():
    n = dashboard_import._num
    assert n("-") is None and n("") is None and n(None) is None and n(True) is None and n(float("nan")) is None and n("1,234") == 1234.0 and n("61.3%") == 61.3 and n(7) == 7.0
    rows = dashboard_import._rows_monthly({"months": ["2025.12", "2026.1"], "centers": [{"name": "A", "m": [["-", 5], None]}], "asof": "2026.9.7", "source": "s"}, ["등원예정일수", "등원일수"], {}, {"*": "d"}, True)
    assert [(r["indicator"], r["value"], r["base_date"]) for r in rows] == [("등원일수", 5.0, "2025-12-31")]


def test_carry_note_and_chat_turn_context(monkeypatch):
    case = agent.Case(); case.note("요구서 읽음: 항목 1건"); case.note("값 3건 가져옴")
    note = agent.carry_note(case, [{"role": "assistant", "text": "회신 초안을 썼습니다.  \n확인해 주세요."}], "직전 작업을 접었습니다.")
    assert note.startswith("직전 작업을 접었습니다.") and "마지막 답: 회신 초안을 썼습니다. 확인해 주세요." in note and "값 3건 가져옴" in note
    monkeypatch.setattr(agent.llm, "run_tools_conv", lambda msgs, *a, **k: {"text": msgs[-1]["content"], "trace": [], "turns": 0, "messages": msgs})
    r = agent.chat_turn(case, [], "다음 요구", None, notes=["의뢰서 'x.hwpx'을 받았습니다."], carry="직전 작업 확정 완료")
    assert "[이전 대화 요약] 직전 작업 확정 완료" in r["text"] and "[첨부 처리] 의뢰서" in r["text"] and r["text"].rstrip().endswith("다음 요구") and agent.HISTORY_MAX_BLOCKS >= 20


def test_mail_text_is_code_generated():
    m = draft.mail_text({"requester": "○○○ 의원실", "title": "센터별 등원율"}, {"제목": "센터별 등원율 제출"}, "답변자료_○○○ 의원실_2026-10-08.hwpx", "담당자")
    assert m["subject"] == "[회신] 센터별 등원율 제출" and "○○○ 의원실 담당자님께" in m["body"] and "답변자료_○○○ 의원실_2026-10-08.hwpx 1부." in m["body"] and m["body"].endswith("담당자 드림")
    assert "[확인 필요: 요청 주체]" in draft.mail_text({}, {}, None, "u")["body"]

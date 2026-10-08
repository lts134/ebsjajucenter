"""대화형 에이전트의 결정적 단계(규칙 경로): 의뢰서 본문 → 읽기 → 등록 → 계획 → 값 → 대조 → 사유 → 초안 → HWPX → 승인."""
from pathlib import Path
import agent, compare

SAMPLE = Path(__file__).resolve().parents[1] / "sample_data"
TPL = Path(__file__).resolve().parents[1] / "templates" / "회신서식_샘플.hwpx"

def _seed(db):
    """7월 의원실 제출본(12건, 6/30 등원율) + 지표 데이터(9월 재산출 6/30 + 7/31)"""
    import extract, normalize
    text = (SAMPLE / "요구서_01_의원실_2026-07-01.txt").read_text(encoding="utf-8")
    res = extract.rule_based(text); res["items"] = normalize.normalize_items(res["items"])
    rid = db.add_request(res["requester"], res["received_date"], res["due_date"], res["title"], text, "요구서_01.txt", res["items"])
    vdf = compare.load_values(SAMPLE / "등원율_2026-06-30기준_7월제출본.xlsx")
    db.add_submission(rid, "2026-07-08", "담당자", "7월제출본.xlsx", "confirmed", "", vdf.to_dict("records"))
    new = compare.load_values(SAMPLE / "등원율_2026-06-30기준_9월재산출.xlsx")
    db.add_data_batch(new.to_dict("records"), "담당자", "9월재산출.xlsx")
    db.add_data_batch([{**r, "base_date": "2026-07-31"} for r in new.to_dict("records")], "담당자", "7월집계.xlsx")

def test_autopilot_rule_path(fresh_db):
    db = fresh_db; _seed(db)
    case = agent.Case(request_text=(SAMPLE / "새요구서_의원실_2026-09-15.txt").read_text(encoding="utf-8"), request_name="새요구서.txt")
    lines = agent.autopilot(case, TPL.read_bytes())
    assert case["request_id"] and case["items"] and case["plans"]
    # '6/30 기준 등원율' → 지표 데이터(exact) 12건이 이번 수치가 되고, 7월 제출본과 대조해 센터C·G·K 3건 차이
    assert case["values"] is not None and (case["values"]["indicator"] == "등원율").sum() == 12
    assert case["compare"] is not None and int((case["compare"]["판정"] == "차이").sum()) == 3
    assert len([k for k, v in case["reasons"].items() if v]) == 3            # 규칙 후보 1순위가 제안으로 채워짐
    assert case["draft"] and case["draft"].get("본문") and case["hwpx"] and case["hwpx_name"].endswith(".hwpx")
    assert any("차이 3건" in l for l in lines) and any("HWPX" in l for l in lines) and lines[-1].startswith("아래 검수 화면")
    # 기준일 바꾸기: 등원율 항목을 7/31로 → 과거 제출값과 짝이 없어 대조 불가
    no = next(i + 1 for i, pl in enumerate(case["plans"]) if pl["indicator"] == "등원율")
    agent.set_dates(case, no, ["2026-07-31"]); pulled = agent.step_pull(case); assert pulled["base_dates"] == ["2026-07-31"]
    assert agent.step_compare(case)["compared"] is False
    try: agent.set_dates(case, no, ["2026-01-31"]); raise AssertionError("없는 기준일을 받으면 안 됨")
    except ValueError as e: assert "가진 자료에 없는" in str(e)
    # 승인: 확정 제출본 + 사유 + 승인된 초안
    agent.set_dates(case, no, ["2026-06-30"]); agent.step_pull(case); agent.step_compare(case); agent.step_reasons(case); agent.step_draft(case)
    agent.set_reason(case, "등원율", "센터C", "2026-06-30", "출결 사후 보정 반영(9/10 재산출)")
    out = agent.approve(case, "담당자", "팀장")
    sid, did = out["submission_id"], out["draft_id"]
    assert db.latest_confirmed_values(case["request_id"])[0] == sid and len(db.get_values(sid)) == 12
    assert db.reasons_for("등원율", "센터C", "2026-06-30")[0]["reason"].startswith("출결 사후 보정") and db.list_drafts("approved")[0]["id"] == did

def test_handlers_and_tools_consistent(fresh_db):
    db = fresh_db; _seed(db)
    case = agent.Case(request_text=(SAMPLE / "새요구서_의원실_2026-09-15.txt").read_text(encoding="utf-8"))
    hs = agent.handlers(case, TPL.read_bytes())
    names = {t["name"] for t in agent.TOOLS}
    assert names == set(hs), names ^ set(hs)
    st0 = hs["case_status"](); assert st0["request_id"] is None and st0["has_draft"] is False
    r = hs["run_all"](); assert r["has_draft"] and r["has_hwpx"] and r["n_values"] == 12 and len(r["summary"]) >= 6
    assert hs["set_reason"](indicator="등원율", center="센터G", base_date="2026-06-30", reason="x")["ok"]
    assert hs["edit_draft"](field="제목", text="바뀐 제목")["ok"] and case["draft"]["제목"] == "바뀐 제목" and case["hwpx"] is None
    assert hs["make_hwpx"]()["file_name"].endswith(".hwpx")
    assert agent.is_do_it("이 의뢰서에서 요구하는 것들 작성해줘") and not agent.is_do_it("감사실에 언제 냈지?")


def test_chat_turn_with_fake_model(fresh_db, monkeypatch):
    """Claude 경로: 모델이 run_all을 고르고 → 결과를 보고 → 답을 쓰는 두 턴. 대화(messages)가 이어지고 도구 결과가 콜백으로 전달되는지."""
    import llm, providers
    db = fresh_db; _seed(db)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test"); llm.reset()
    class _U: input_tokens = 500; output_tokens = 100
    class _T:
        def __init__(s, t): s.type, s.text = "text", t
    class _Use:
        def __init__(s, name, inp, id_): s.type, s.name, s.input, s.id = "tool_use", name, inp, id_
    class _M:
        def __init__(s, content, stop): s.content, s.stop_reason, s.usage = content, stop, _U()
    class Fake(providers.AnthropicProvider):
        def __init__(s, script): super().__init__({"api_key": "sk-test"}); s.script, s.calls = list(script), []
        def create_message(s, **kw):
            if not kw.get("tools"): raise RuntimeError("fake: 도구 없는 호출(추출·초안 등)은 규칙 경로로 대체되어야 함")   # 각 모듈이 API 오류를 규칙으로 대체하는지도 함께 확인
            s.calls.append(kw); return s.script.pop(0)
    fake = Fake([_M([_Use("run_all", {}, "t1")], "tool_use"), _M([_T("처리했습니다. 검수 화면에서 승인해 주세요.")], "end_turn"),
                 _M([_Use("set_reason", {"indicator": "등원율", "center": "센터C", "base_date": "2026-06-30", "reason": "사후 보정"}, "t2")], "tool_use"), _M([_T("반영했습니다.")], "end_turn")])
    monkeypatch.setattr(llm, "_provider", lambda: fake)
    case = agent.Case(request_text=(SAMPLE / "새요구서_의원실_2026-09-15.txt").read_text(encoding="utf-8"), request_name="r.txt")
    seen = []
    res = agent.chat_turn(case, [], "이 의뢰서에서 요구하는 것들 작성해 줘", TPL.read_bytes(), on_tool=lambda n, i, o: seen.append(n))
    assert res["text"].startswith("처리했습니다") and seen == ["run_all"] and case["draft"] and case["hwpx"]
    assert fake.calls[0]["system"] == agent.SYSTEM and {t["name"] for t in fake.calls[0]["tools"]} >= {"run_all", "set_dates", "indicator_history"}
    assert "[작업 상태]" in fake.calls[0]["messages"][0]["content"] and len(res["messages"]) == 4         # user, assistant(tool_use), user(tool_result), assistant(text)
    res2 = agent.chat_turn(case, res["messages"], "센터C 사유는 사후 보정으로 적어", TPL.read_bytes())
    assert res2["text"] == "반영했습니다." and case["reasons"][("등원율", "센터C", "2026-06-30")] == "사후 보정" and case["draft_stale"]
    assert len(fake.calls[2]["messages"]) == 5 and len(res2["messages"]) == 8                              # 이전 대화 4 + 이번 사용자 1 → 도구 왕복 후 8


def test_describe_request_without_file_and_deferred_register(fresh_db):
    """의뢰서 파일 없이 말로 받은 요구: 지시어를 뗀 항목 → 지표 데이터 범위로 기간 전부 → 등록 보류(no_register)로 초안·HWPX → 승인 때 등록."""
    import calendar, docread
    db = fresh_db
    months = [f"{y}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}" for y, m in [(2025, 12)] + [(2026, m) for m in range(1, 10)]]
    db.add_data_batch([{"indicator": "등원율", "center": f"센터{i}", "base_date": d, "value": 50 + i} for d in months for i in range(1, 4)], "u", "센터별월간출결.xls")
    assert agent.coverage_line() == "지표 데이터 보유: 등원율 2025-12-31~2026-09-30(10개 기준일·3센터)"
    case = agent.Case()
    r = agent.describe_request(case, items=["센터별 등원율 요청이 들어왔어. 작성해줘"])
    assert r["items"][0]["item_text"] == "센터별 등원율" and r["items"][0]["indicator"] == "등원율" and r["missing_header"] == ["요청 주체", "접수일", "기한"]
    assert "describe_request" in agent.status_line(agent.Case()) and "지표 데이터 보유" in agent.status_line(case)
    lines = agent.autopilot(case, None, register=False)
    assert case["request_id"] is None and case["no_register"] and any("등록하지 않았습니다" in l for l in lines)
    assert len(case["values"]) == 3 and case["values"]["base_date"].unique().tolist() == ["2026-09-30"]        # 기간 말이 없으면 최신
    assert case["extracted"]["requester"] is None and case["hwpx_name"] == f"답변자료_요구_{__import__('datetime').date.today()}.hwpx"   # 다시 읽어도 '미기재'가 값으로 들어가지 않음
    # 나중에 머리 정보와 기간을 말해 주면 고치고 다시
    r2 = agent.describe_request(case, requester="테스트용", received_date="2026-10-08", due_date="2026-10-09", items=["25년 12월부터 26년 8월까지 전체 센터의 센터별 등원율"])
    assert r2["requester"] == "테스트용" and r2["missing_header"] == [] and case["values"] is None and case["draft"] is None
    agent.autopilot(case, None, register=False)
    assert len(case["values"]) == 27 and case["values"]["base_date"].min() == "2025-12-31" and case["values"]["base_date"].max() == "2026-08-31"
    assert case["coverage"]["판정"].tolist() == ["충족"] and "2025-12-31 ~ 2026-08-31 기준(9개 기준일)" in case["draft"]["본문"]
    text = docread.read("x.hwpx", case["hwpx"]); assert text.startswith("테스트용 답변자료") and "2026-08-31" in text and "2. 25년" not in text   # 규칙 초안의 '2.' 항목이 1번 항목 아래로 들어감
    out = agent.approve(case, "담당자", "팀장")
    req = db.get_request(case["request_id"]); assert req["requester"] == "테스트용" and req["due_date"] == "2026-10-09" and len(db.get_values(out["submission_id"])) == 27
    # 등록된 뒤 다시 describe하면 기록도 고쳐진다
    agent.describe_request(case, title="센터별 월별 등원율(수정)"); assert db.get_request(case["request_id"])["title"] == "센터별 월별 등원율(수정)"
    # 도구 표: 새 도구가 핸들러와 맞고 data_coverage가 지표 데이터를 보여 준다
    hs = agent.handlers(agent.Case(), None); assert {t["name"] for t in agent.TOOLS} == set(hs)
    assert hs["data_coverage"]()["indicators"][0]["n_dates"] == 10 and hs["case_status"]()["data_coverage"].startswith("지표 데이터 보유")
    assert hs["describe_request"](items=["센터별 등록 학생 수"], requester="감사실")["unknown_indicator"] == []
    msgs = agent.chat_turn.__code__.co_varnames; assert "notes" in msgs

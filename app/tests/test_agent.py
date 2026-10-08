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

"""기능 1·2·3: 사유 후보, 후속 질문, 이력 질의(도구 루프)."""
import pandas as pd, anthropic
try: import httpx2 as httpx
except ImportError: import httpx
import assist, history_qa, llm

ROW = {"indicator": "등원율", "center": "센터C", "base_date": "2026-06-30", "old_value": 66.4, "new_value": 67.8,
       "단서": "추출시점 변경(2026-07-03→2026-09-10); 원자료 버전 변경(출결 v1→출결 v2(사후 보정))"}

def seed(db):
    rid = db.add_request("감사실", "2026-05-12", "2026-05-20", "상반기 점검", "2026. 3. 31. 기준 센터별 등원율", "a.txt",
                         [{"item_text": "2026. 3. 31. 기준 센터별 등원율", "indicator": "등원율", "base_date": "2026-03-31"}])
    sid = db.add_submission(rid, "2026-05-20", "홍담당", "v.xlsx", "confirmed", "n", [{"indicator": "등원율", "center": "센터A", "base_date": "2026-06-30", "value": 60.3}])
    db.add_reason(sid, "등원율", "센터G", "2026-06-30", 78.6, 80.0, "출결 사후 보정 반영(9/10 재산출)", "홍담당")
    return rid, sid

def test_reason_candidates_rule_uses_clue_and_past(fresh_db):
    seed(fresh_db)
    c, how = assist.reason_candidates([ROW])
    cands = c[("등원율", "센터C", "2026-06-30")]
    assert how.startswith("규칙") and len(cands) == 3
    assert "추출 시점 차이(2026-07-03 → 2026-09-10)" in cands[0]["문구"] and cands[0]["근거"].startswith("대조 단서")
    assert cands[2]["문구"] == "출결 사후 보정 반영(9/10 재산출)" and "과거 입력 사유" in cands[2]["근거"]

def test_reason_candidates_no_clue_marks_check_needed(fresh_db):
    c, _ = assist.reason_candidates([{**ROW, "단서": "근거 필드 동일 — 사유 입력 필요"}])
    assert "[확인 필요]" in c[("등원율", "센터C", "2026-06-30")][0]["문구"]

def test_foresee_rule_questions(fresh_db):
    seed(fresh_db)
    req = {"requester": "의원실", "title": "t"}
    items = [{"item_text": "2026. 6. 30. 기준 센터별 등원율", "indicator": "등원율", "base_date": "2026-06-30"},
             {"item_text": "최근 3년 운영 예산", "indicator": "운영 예산", "base_date": None, "period": "최근 3년"}]
    values = pd.DataFrame({"indicator": ["등원율"], "center": ["센터C"], "base_date": ["2026-06-30"], "value": [67.8]})
    qs, how = assist.foresee(req, items, values, {}, [{"center": "센터C", "판정": "차이", "단서": ROW["단서"]}], {"본문": "x"})
    text = " ".join(q["질문"] for q in qs)
    assert how == "규칙" and "센터C" in text and "감사실" in text and "운영 예산" in text and "기준 시점" in text
    assert all(q["가능성"] in ("높음", "중간", "낮음") for q in qs)

def test_history_handlers_and_keyword_search(fresh_db):
    rid, sid = seed(fresh_db)
    H = history_qa.HANDLERS
    assert H["search_requests"]("감사실")[0]["id"] == rid
    d = H["request_detail"](rid); assert d["submissions"][0]["n_values"] == 1 and d["items"][0]["indicator"] == "등원율"
    assert H["submission_values"](sid, "센터A")[0]["value"] == 60.3 and H["submission_values"](sid, "없음") == []
    assert H["past_values_for"]("등원율", "2026-06-30")[0]["requester"] == "감사실"
    assert H["diff_reasons"]("등원율")[0]["center"] == "센터G"
    assert "requests" in H["overview"]() and "등원율" in H["indicators"]()["지표 사전"]
    kw = history_qa.keyword_search("감사실에 등원율 어떻게 냈지?")
    assert kw["requests"][0]["id"] == rid and kw["indicators"] == ["등원율"] and kw["reasons"]
    assert history_qa.ask("q")["text"] is None            # 키 없음

# ---- run_tools 가짜 클라이언트 ----
class _U: input_tokens = 500; output_tokens = 100
class _T:
    def __init__(s, t): s.type, s.text = "text", t
class _Use:
    def __init__(s, name, inp, id_): s.type, s.name, s.input, s.id = "tool_use", name, inp, id_
class _M:
    def __init__(s, content, stop): s.content, s.stop_reason, s.usage = content, stop, _U()
class Fake:
    def __init__(s, script): s.script, s.calls = list(script), []; s.messages = s
    def create(s, **kw):
        s.calls.append(kw); r = s.script.pop(0)
        if isinstance(r, Exception): raise r
        return r

def test_run_tools_loop_executes_handlers_and_returns_trace(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test"); llm._RESOLVED = None; llm.LOG.clear()
    fake = Fake([_M([_Use("search_requests", {"keyword": "감사실"}, "tu1")], "tool_use"),
                 _M([_Use("boom", {}, "tu2"), _Use("request_detail", {"request_id": 1}, "tu3")], "tool_use"),
                 _M([_T("감사실에 2026-05-20 제출(#1)")], "end_turn")])
    monkeypatch.setattr(llm, "_client", lambda: fake)
    handlers = {"search_requests": lambda keyword: [{"id": 1, "requester": keyword}], "request_detail": lambda request_id: {"id": request_id}}
    out = llm.run_tools("q", "sys", history_qa.TOOLS, handlers, max_turns=5)
    assert out["text"].startswith("감사실에") and out["turns"] == 3
    assert [t["tool"] for t in out["trace"]] == ["search_requests", "boom", "request_detail"] and out["trace"][0]["rows"] == 1
    assert "error" in out["trace"][1]["result"]
    # 두 번째 요청에는 tool_result 2개가 한 user 메시지로 묶여 들어감
    tr = fake.calls[2]["messages"][-1]["content"]; assert len(tr) == 2 and tr[0]["type"] == "tool_result" and tr[0]["is_error"] is True
    assert fake.calls[0]["tools"] is history_qa.TOOLS and llm.LOG[-1]["purpose"] == "도구 질의"

def test_run_tools_turn_limit(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test"); llm._RESOLVED = None
    fake = Fake([_M([_Use("search_requests", {"keyword": "x"}, f"t{i}")], "tool_use") for i in range(3)])
    monkeypatch.setattr(llm, "_client", lambda: fake)
    out = llm.run_tools("q", "s", history_qa.TOOLS, {"search_requests": lambda keyword: []}, max_turns=2)
    assert "한도" in out["text"] and out["turns"] == 2

def test_run_tools_404_moves_to_next_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test"); llm._RESOLVED = None
    req = httpx.Request("POST", "https://x")
    e = anthropic.NotFoundError("no model", response=httpx.Response(404, request=req, json={}), body=None)
    fake = Fake([e, _M([_T("ok")], "end_turn")]); monkeypatch.setattr(llm, "_client", lambda: fake)
    out = llm.run_tools("q", "s", history_qa.TOOLS, {}, max_turns=2)
    assert out["text"] == "ok" and [c["model"] for c in fake.calls] == llm.CANDIDATES[:2]

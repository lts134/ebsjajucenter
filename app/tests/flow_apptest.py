"""화면 흐름을 Streamlit AppTest로 구동해 예외 없이 끝나는지 확인한다(규칙 경로, 시연 모드).
홈 → 과거 답변 등록 → 새 요구서 처리 1·2·3단계 → 검토·승인 → 기록 조회·현황·기록에 묻기·설정. 사용: python tests/flow_apptest.py"""
import os, sys
from pathlib import Path
os.environ["HISTORY_DB"] = str(Path(__file__).parent / ".flow_apptest.db"); os.environ["APP_DEMO"] = "1"
Path(os.environ["HISTORY_DB"]).unlink(missing_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from streamlit.testing.v1 import AppTest
APP = str(Path(__file__).resolve().parents[1] / "app.py")

def app(page, step=None, state=None):
    at = AppTest.from_file(APP, default_timeout=60)
    for k, v in (state or {}).items(): at.session_state[k] = v
    at.session_state["nav"] = page
    if step: at.session_state["step"] = step
    at.run(); assert not at.exception, at.exception
    return at

def button(at, text):
    b = [b for b in at.button if text in b.label]; assert b, f"버튼 없음: {text} / {[x.label for x in at.button]}"
    b[0].click().run(); assert not at.exception, at.exception

def sb(at, key): return next(s for s in at.selectbox if s.key == key)
def st_text(at): return "\n".join([m.value for m in at.markdown] + [c.value for c in at.caption] + [x.value for x in at.success] + [x.value for x in at.info] + [x.value for x in at.warning])
def ms_pick(at, key, *texts):
    """multiselect에서 글자가 포함된 옵션들을 고른다"""
    m = next(m for m in at.multiselect if m.key == key)
    m.set_value([o for o in m.options if any(t in str(o) for t in texts)]).run(); assert not at.exception, at.exception

# 홈(빈 상태)
at = app("홈"); assert at.chat_input and any("요구서를 붙이고" in m.value for m in at.markdown); print("홈(대화, 빈 상태) OK")

# 과거 답변 등록: 시연 요구서 + 시연 제출본(7월) → 저장
at = app("과거 답변 등록")
s = sb(at, "reg_demo"); s.select(next(o for o in s.options if "요구서_01" in str(o))).run(); assert not at.exception
button(at, "요구 항목 읽기"); ms_pick(at, "reg_val_demo", "7월")
button(at, "기록에 저장"); print("등록:", [x.value[:60] for x in at.success])
assert any("값 12건" in x.value for x in at.success)

# 과거 답변 등록 — 가로형 실적표: 표 읽는 법 확인 UI
at = app("과거 답변 등록")
ms_pick(at, "reg_val_demo", "실적표_")
assert next(t for t in at.text_input if t.label.startswith("3. 기준일")).value == "2026-06-30"
assert next(m for m in at.multiselect if m.label.startswith("2. 값 열")).value == [1, 2, 3, 4]
assert any("48건" in c.value for c in at.caption), [c.value for c in at.caption]; print("가로형 실적표 → 열 확인 OK")

# 과거 답변 등록 — 여러 출처 한 번에: 실적표(가로형) + 등원율(긴 형식) → 둘 다 저장 대상, 겹침 경고
at = app("과거 답변 등록")
ms_pick(at, "reg_val_demo", "실적표_", "7월")
assert [c.value for c in at.caption if c.value.startswith("읽음")] == ["읽음: 12건", "읽음: 48건"], [c.value for c in at.caption]
cap = next(c.value for c in at.caption if c.value.startswith("저장될 값"))
# 두 시연 파일은 같은 값(가로형 ↔ 긴 형식)이라 겹침 경고가 뜨고, 나중에 올린 실적표 값만 남아 48건
assert cap.startswith("저장될 값 48건"), cap
assert any("두 곳에" in w.value for w in at.warning), [w.value for w in at.warning]; print("여러 출처 한 번에 OK:", cap)

# 기록 조회 — 수정·값 저장·삭제(요구서 #1은 뒤 단계가 쓰므로 임시 요구서를 하나 더 만들어 지운다)
import db as _db
tmp = _db.add_request("임시 기관", "2026-09-30", "2026-10-07", "임시 요구서", "원문", "t.txt", [{"item_text": "등원율 현황", "indicator": "등원율", "base_date": "2026-06-30"}])
tsid = _db.add_submission(tmp, "2026-10-01", "담당자", "t.xlsx", "confirmed", "", [{"indicator": "등원율", "center": "센터A", "base_date": "2026-06-30", "value": 1.0}])
at = app("기록 조회")
eb = next(b for b in at.button if b.key == f"edit_btn_{tmp}"); eb.click().run(); assert not at.exception
next(t for t in at.text_input if t.key == f"ed_ti_{tmp}").set_value("임시 요구서(수정)").run()
next(b for b in at.button if b.key == f"ed_save_{tmp}").click().run(); assert not at.exception
assert _db.get_request(tmp)["title"] == "임시 요구서(수정)"
next(c for c in at.checkbox if c.key == f"del_sub_{tsid}_ok").check().run()
next(b for b in at.button if b.key == f"del_sub_{tsid}_go").click().run(); assert not at.exception
assert _db.list_submissions(tmp) == [] and _db.get_request(tmp) is not None
next(c for c in at.checkbox if c.key == f"del_req_{tmp}_ok").check().run()
next(b for b in at.button if b.key == f"del_req_{tmp}_go").click().run(); assert not at.exception
assert _db.get_request(tmp) is None and _db.get_request(1) is not None; print("기록 조회 수정·삭제 OK")

# 지표 데이터: 9월 재산출 집계를 미리 넣어 둠 → 2단계에서 '지표 데이터'로 찾혀 자동으로 채워짐
at = app("지표 데이터")
ms_pick(at, "data_in_demo", "9월")
button(at, "지표 데이터에 저장"); assert any("묶음 #1, 12건" in x.value for x in at.success), [x.value for x in at.success]
assert any("등원율" in str(v) for v in at.dataframe[-1].value["지표"].tolist()) if len(at.dataframe) else True; print("지표 데이터 저장 OK")

# 새 요구서 처리 1단계: 시연 새 요구서 → 읽기 → 등록(2단계로)
at = app("새 요구서 처리", 1)
s = sb(at, "new_demo"); s.select(next(o for o in s.options if "새요구서_의원실" in str(o))).run(); assert not at.exception
button(at, "요구 항목 읽기"); print("1단계 정보:", [i.value[:70] for i in at.info])
button(at, "등록하고 2단계로")
assert at.session_state["step"] == 2 and at.session_state.get("target_request"); print("1단계 → 2단계 OK, 요구 #", at.session_state["target_request"])

# 2단계: 시연 9월 재산출 vs 제출본 #1 → 후보 → 적용 → 사유 → 확정(3단계로)
keep = {k: at.session_state[k] for k in ("target_request", "new_registered")}
at = app("새 요구서 처리", 2, keep)
# 자료 계획: '6/30 기준 등원율'은 지표 데이터에 있음(exact) → 계획대로 가져오기 → 시연 9월 파일(자동 선택)이 겹치는 값을 덮어 차이 3 유지
assert any("지표 데이터에 있습니다" in m.value for m in at.markdown), [m.value[:80] for m in at.markdown if "기준일" in m.value]
button(at, "계획대로 가져오기")
assert any("기록에서 12건" in c.value for c in at.caption), [c.value for c in at.caption if "준비됨" in c.value]
print("2단계 metric:", [(m.label, m.value) for m in at.metric])
assert any(m.label == "차이" and m.value == "3" for m in at.metric), "차이 3건이어야 함"
button(at, "문구 후보 받기")
apply = [b for b in at.button if b.label == "적용"]; assert apply; apply[0].click().run(); assert not at.exception
filled = [ti.value for ti in at.text_input if ti.key and str(ti.key).startswith("reason_")]; assert filled[0], "후보 적용 실패"
for ti in at.text_input:
    if ti.key and str(ti.key).startswith("reason_") and not ti.value: ti.set_value("출결 사후 보정 반영(9/10 재산출)")
at.run()
button(at, "확정하고 3단계로")
assert at.session_state["step"] == 3 and at.session_state.get("last_submission"); print("2단계 → 3단계 OK, 제출본 #", at.session_state["last_submission"])

# 3단계: 초안 → 예상 질문 → 검토 요청
keep = {k: at.session_state[k] for k in ("target_request", "last_request", "last_submission", "last_checklist", "last_reasons")}
at = app("새 요구서 처리", 3, keep)
button(at, "초안 만들기"); print("3단계:", [w.value[:60] for w in at.warning] + [s.value[:60] for s in at.success])
button(at, "예상 질문 보기"); assert any("생성 방식" in c.value for c in at.caption)
button(at, "팀장 검토 요청"); print("3단계:", [s.value[:60] for s in at.success])

# 검토·승인
at = app("검토·승인"); button(at, "승인"); print("승인 후 대기:", [(m.label, m.value) for m in at.metric])

# 나머지 화면
for pg in ("홈", "지표 데이터", "참고 문서", "기록 조회", "현황", "기록에 묻기", "설정"):
    at = app(pg); print(pg, "OK")
# 설정 — 조직 설정 저장(기록 DB) → 백업 파일 만들기 → 홈 '오늘 할 일' 띠와 다음 행동 칩
at = app("설정")
next(t for t in at.text_input if t.key == "cfg_dept_head").set_value("홍길동").run(); next(b for b in at.button if b.key == "cfg_org_save").click().run(); assert not at.exception, at.exception
assert _db.get_settings()["dept_head"] == "홍길동"
next(b for b in at.button if b.key == "bk_make").click().run(); assert not at.exception and at.session_state.get("bk_bytes") and at.session_state["bk_bytes"][0][:6] == b"SQLite"
at = app("홈"); txt = st_text(at); assert "팀장 검토 대기" in txt and "확정했지만 미발송" in txt, txt[:300]
print("설정 — 조직 설정·백업, 홈 오늘 할 일 OK")
# 설정 — 공급자 셋(OpenAI 호환·Gemini 포함) 선택 시 입력칸이 뜨고 예외 없음
for pv in ("openai", "gemini", "none"):
    at = app("설정", state={"llm_cfg": {"provider": pv}})
    labels = " ".join(t.label for t in at.text_input)
    assert (pv == "none") or ("API 키" in labels and "기본 주소" in labels), (pv, labels)
print("설정 — 공급자 선택(openai·gemini·none) OK")
at = app("기록에 묻기"); next(t for t in at.text_input if t.key == "qa_q").set_value("감사실에 등원율 어떻게 냈지?").run(); button(at, "물어보기"); print("기록에 묻기(키워드) OK")
# 대화 홈: 시연 요구서를 붙여 '작성해 줘' → 규칙 경로 autopilot → 검수 패널 → 승인
at = AppTest.from_file(APP, default_timeout=120); at.session_state["nav"] = "홈"
at.session_state["chat_pending"] = ("이 요구서에서 요구하는 것들 작성해 줘", [("새요구서_의원실_2026-09-15.txt", (Path(__file__).resolve().parents[1] / "sample_data" / "새요구서_의원실_2026-09-15.txt").read_bytes())])
at.run(); assert not at.exception, at.exception
reply = [m.value for m in at.markdown if "회신 초안을 썼습니다" in m.value]
assert reply and "HWPX" in reply[0], [m.value[:80] for m in at.markdown][-6:]
case = at.session_state["case"]; assert case["values"] is not None and case["draft"] and case["hwpx"]
assert any(b.key == "rv_approve_go" for b in at.button), [b.key for b in at.button][:30]                     # '승인하고 확정' 팝오버 안의 확인 목록 + 확정 버튼
at.session_state["case"]["reasons"][("등원율", "센터A", "2026-06-30")] = "출결 사후 보정 반영(시연)"              # 이 흐름은 직전 단계의 제출본과 값이 같아 차이가 없으므로 사유 하나를 넣어 서랍 적재를 확인
next(b for b in at.button if b.key == "rv_approve_go").click().run(); assert not at.exception, at.exception
assert at.session_state["case"].get("approved") and st_text(at).count("발송 기록") >= 1 and at.session_state["chat_msgs"] == [], "확정 뒤 보내기 카드·모델 기록 비움"
assert len(_db.list_phrases("사유")) >= 1, "확정 때 쓴 사유가 문구 서랍에 쌓여야 함"
_db.add_dispatch(*at.session_state["case"]["approved"], "2026-10-08", "○○○ 의원실", "메일", "담당자", "")      # 발송 기록 → 현황 '발송 완료'
nb = next(b for b in at.button if b.key and str(b.key).startswith("rv_new_")); nb.click().run(); assert not at.exception, at.exception
assert not at.session_state["case"]["draft"] and at.session_state.get("chat_carry") and any("새 요구 시작" in m.value for m in at.markdown); print("대화 홈 → 검수 → 확정 → 보내기 → 새 요구 시작 OK")
at = app("현황"); assert "발송 완료" in str(at.dataframe[0].value["상태"].tolist()), at.dataframe[0].value["상태"].tolist(); print("현황 — 발송 상태 OK")
# 대화 홈 — 요구서 없이 집계 파일 + 말로 받은 요구(규칙 경로): 파일은 지표 데이터로, 문장은 요구 항목으로 → 검수 패널
import io as _io, calendar as _cal, pandas as _pd
_months = [f"{y}-{m:02d}-{_cal.monthrange(y, m)[1]:02d}" for y, m in [(2025, 12)] + [(2026, m) for m in range(1, 10)]]
_buf = _io.BytesIO(); _pd.DataFrame([{"지표": "등원율", "센터": f"센터{i}", "기준일": d, "값": 50 + i} for d in _months for i in range(1, 4)]).to_excel(_buf, index=False)
at = AppTest.from_file(APP, default_timeout=120); at.session_state["nav"] = "홈"
at.session_state["chat_pending"] = ("25년 12월부터 26년 8월까지 센터별 등원율 요청이 들어왔어. 등록은 빼고 파일만 만들어 줘", [("센터별월간출결.xlsx", _buf.getvalue())])
at.run(); assert not at.exception, at.exception
txt = "\n".join(m.value for m in at.markdown)
assert "지표 데이터에 넣었습니다" in txt and "말씀하신 문장을 요구 항목으로" in txt and "기간이 적혀 있습니다(2025년 12월 ~ 2026년 8월)" in txt and "등록하지 않았습니다" in txt, txt[-600:]
case = at.session_state["case"]; v = case["values"]      # 앞 단계에서 넣은 시연 지표 데이터(6/30·7/31)도 기간 안이라 함께 들어온다
assert case["request_id"] is None and len(v) >= 27 and v["base_date"].min() == "2025-12-31" and v["base_date"].max() == "2026-08-31" and case["hwpx"] and any("요구서 미등록" in c.value for c in at.caption)
print("대화 홈 — 파일+말로 받은 요구(등록 보류) OK")
# 대화 홈 — AI 경로(가짜 모델): run_ai가 작업 스레드에서 돌 때 세션 저장소를 건드리지 않는지(KeyError 재발 방지)
import llm, providers
class _U: input_tokens = 10; output_tokens = 5
class _T:
    def __init__(s, t): s.type, s.text = "text", t
class _M:
    def __init__(s, content, stop="end_turn"): s.content, s.stop_reason, s.usage = content, stop, _U()
class _FakeProv(providers.AnthropicProvider):
    def __init__(s): super().__init__({"api_key": "sk-test"})
    def ready(s): return True
    def create_message(s, **kw):
        if not kw.get("tools"): raise RuntimeError("fake")
        return _M([_T("요청을 확인했습니다. 요구서를 붙여 주시면 바로 준비하겠습니다.")])
_orig = llm._provider; llm._provider = lambda: _FakeProv()
try:
    at = AppTest.from_file(APP, default_timeout=120); at.session_state["nav"] = "홈"
    at.session_state["chat_pending"] = ("센터별 등원율 자료에 대한 요청이 들어왔어", [])
    at.run(); assert not at.exception, at.exception
    assert any("요청을 확인했습니다" in m.value for m in at.markdown), [m.value[:60] for m in at.markdown][-5:]
    assert len(at.session_state["chat_msgs"]) == 2; print("대화 홈 AI 경로(가짜 모델) OK")
finally: llm._provider = _orig
print("DONE")

"""화면 흐름을 Streamlit AppTest로 구동해 예외 없이 끝나는지 확인한다(규칙 경로, 시연 모드).
홈 → 과거 답변 등록 → 새 요구서 처리 1·2·3단계 → 검토·승인 → 이력 조회·현황·이력에 묻기·설정. 사용: python tests/flow_apptest.py"""
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

# 홈(빈 상태)
at = app("홈"); assert any("기록이 없습니다" in i.value for i in at.info); print("홈(빈 상태) OK")

# 과거 답변 등록: 시연 요구서 + 시연 제출본(7월) → 저장
at = app("과거 답변 등록")
s = sb(at, "reg_demo"); s.select(next(o for o in s.options if "요구서_01" in str(o))).run(); assert not at.exception
button(at, "요구 항목 읽기")
button(at, "기억에 저장"); print("등록:", [x.value[:60] for x in at.success])

# 과거 답변 등록 — 가로형 실적표: 표 읽는 법 확인 UI
at = app("과거 답변 등록")
s = sb(at, "reg_val_demo"); s.select(next(o for o in s.options if "실적표_" in str(o))).run(); assert not at.exception
assert next(t for t in at.text_input if t.label.startswith("3. 기준일")).value == "2026-06-30"
assert next(m for m in at.multiselect if m.label.startswith("2. 값 열")).value == [1, 2, 3, 4]
assert any("48건" in c.value for c in at.caption), [c.value for c in at.caption]; print("가로형 실적표 → 열 확인 OK")

# 과거 답변 등록 — 여러 출처 묶기: 실적표(가로형) 담기 → 등원율(긴 형식) 추가 → 둘 다 저장 대상
at = app("과거 답변 등록")
s = sb(at, "reg_val_demo"); s.select(next(o for o in s.options if "실적표_" in str(o))).run(); assert not at.exception
button(at, "담고 다른 파일 추가")
assert len(at.session_state["reg_val_pile"]) == 1 and any("담아 둔 표 1개" in m.value for m in at.markdown)
s = sb(at, "reg_val_demo"); s.select(next(o for o in s.options if "등원율_" in str(o) and "7월" in str(o))).run(); assert not at.exception
cap = next(c.value for c in at.caption if c.value.startswith("저장될 값")); assert "출처 2개" in cap, cap
# 두 시연 파일은 같은 값(가로형 ↔ 긴 형식)이라 겹침 경고가 뜨고, 나중 출처만 남아 건수는 그대로다
n_pile = len(at.session_state["reg_val_pile"][0]["df"]); n_total = int(cap.split("건")[0].split()[-1]); assert n_total == n_pile, cap
assert any("두 출처에" in w.value for w in at.warning), [w.value for w in at.warning]
rm = [b for b in at.button if b.label == "빼기"]; rm[0].click().run(); assert not at.exception
assert not at.session_state["reg_val_pile"]; print("여러 출처 묶기 OK:", cap)

# 새 요구서 처리 1단계: 시연 새 요구서 → 읽기 → 등록(2단계로)
at = app("새 요구서 처리", 1)
s = sb(at, "new_demo"); s.select(next(o for o in s.options if "새요구서_의원실" in str(o))).run(); assert not at.exception
button(at, "요구 항목 읽기"); print("1단계 정보:", [i.value[:70] for i in at.info])
button(at, "등록하고 2단계로")
assert at.session_state["step"] == 2 and at.session_state.get("target_request"); print("1단계 → 2단계 OK, 요구 #", at.session_state["target_request"])

# 2단계: 시연 9월 재산출 vs 제출본 #1 → 후보 → 적용 → 사유 → 확정(3단계로)
keep = {k: at.session_state[k] for k in ("target_request", "new_registered")}
at = app("새 요구서 처리", 2, keep)
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
for pg in ("홈", "이력 조회", "현황", "이력에 묻기", "설정"):
    at = app(pg); print(pg, "OK")
at = app("이력에 묻기"); next(t for t in at.text_input if t.key == "qa_q").set_value("감사실에 등원율 어떻게 냈지?").run(); button(at, "물어보기"); print("이력에 묻기(키워드) OK")
print("DONE")

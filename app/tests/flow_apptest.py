"""6단계 시연 흐름을 Streamlit AppTest로 구동해 예외 없이 끝나는지 확인한다(규칙 경로). 사용: python tests/flow_apptest.py"""
import os, sys
from pathlib import Path
os.environ["HISTORY_DB"] = str(Path(__file__).parent / ".flow_apptest.db")
Path(os.environ["HISTORY_DB"]).unlink(missing_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from streamlit.testing.v1 import AppTest

def app(page):
    at = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=60)
    at.run()
    at.sidebar.radio[0].set_value(page).run()
    assert not at.exception, at.exception
    return at

# ① 등록: 요구서_01 + 7월 제출본
at = app("① 과거 자료 등록")
at.button[0].click().run(); assert not at.exception, at.exception
at.button[1].click().run(); assert not at.exception, at.exception
print("①", [s.value for s in at.success])

# ② 새 요구서 분석 → 등록
at = app("② 새 요구서 분석")
at.button[0].click().run(); assert not at.exception, at.exception
print("② info:", [i.value[:80] for i in at.info])
at.button[1].click().run(); assert not at.exception, at.exception
print("②", [s.value for s in at.success])

# ③ 대조·사유·확정
at = app("③ 수치 대조·점검표")
assert not at.exception, at.exception
print("③ metric:", [(m.label, m.value) for m in at.metric])
for ti in at.text_input:
    if ti.key and str(ti.key).startswith("reason_"): ti.set_value("출결 사후 보정 반영(9/10 재산출)")
at.run()
# 대상 요구서 = 새 요구서(#2) 선택: selectbox 2번째 (제출본, 대상 요구서)
sel = at.selectbox[1]; opts = sel.options; print("③ 대상 후보:", opts)
sel.select(opts[0]).run()
btn = [b for b in at.button if "확정" in b.label][0]; btn.click().run(); assert not at.exception, at.exception
print("③", [s.value for s in at.success])

# ④ 초안 → 검토 요청
at = app("④ 회신 초안·HWPX")
at.button[0].click().run(); assert not at.exception, at.exception
print("④ caption:", [c.value for c in at.caption][:3])
print("④ warn/success:", [w.value for w in at.warning], [s.value for s in at.success])
btn = [b for b in at.button if "검토 요청" in b.label][0]; btn.click().run(); assert not at.exception, at.exception
print("④", [s.value for s in at.success])

# ⑤ 승인
at = app("⑤ 검토·승인")
ok = [b for b in at.button if b.label == "승인"]; assert ok, "승인 버튼 없음"
ok[0].click().run(); assert not at.exception, at.exception
print("⑤ metric:", [(m.label, m.value) for m in at.metric])

# ⑥ ⑦ 설정
for p in ("⑥ 이력 조회", "⑦ 현황·통계", "설정"):
    at = app(p); print(p, "OK", [(m.label, m.value) for m in at.metric][:3])
print("ALL OK")

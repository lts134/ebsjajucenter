"""EBS 대외 요구자료 대응 에이전트 — 진입점(실행·접속 잠금·사이드바·화면 라우팅). 화면 자체는 screens/ 모듈에 있다.
실행: python start.py  (또는 streamlit run app.py). 시연 파일 선택칸까지 보이게 하려면 APP_DEMO=1 (run_demo.bat).

화면 구성(사람이 하는 일 순서대로):
  업무  홈(대화·검수·확정·보내기) · 현황(상태 흐름·월간 리포트) · 검토·승인(팀장)
  자료  지표 데이터 · 참고 문서 · 과거 답변 등록
  기록  찾기·고치기 / 물어보기 · 설정
  (새 요구서 처리 1→2→3 단계 화면은 홈 검수의 '자세히 고치기'로만 진입)
역할 경계: AI는 읽기·문안·점검·기록 조회만. 수치 계산·대조는 코드, 사유·확정·발송은 담당자."""
import os, sys, subprocess, time
from pathlib import Path

def _in_streamlit():
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        return get_script_run_ctx() is not None
    except Exception:
        return False

if __name__ == "__main__" and not _in_streamlit():
    subprocess.run([sys.executable, str(Path(__file__).parent / "start.py")]); sys.exit()

import streamlit as st
import db, llm, normalize, ui
from screens.common import ctx, go, _build_info
from screens import home, data, process, records, settings

os.umask(0o077)                                                                     # 이 앱이 만드는 파일(기록 DB·회신·백업)은 실행 계정만 읽게(공용 서버의 다른 계정 차단)
st.set_page_config(page_title="대외 요구자료 대응", page_icon="📁", layout="wide")
ui.inject()
if os.environ.get("APP_PASSWORD") and not st.session_state.get("authed"):          # 외부 서버에 올릴 때의 최소 잠금. 사내망 전용이면 비워 둔다
    import hmac
    from screens.common import auth_locked, auth_fail, auth_ok
    try: _ip = st.context.ip_address or None                                        # 로컬 접속은 None. 프록시 뒤면 위조될 수 있어 세션별 횟수와 함께 쓴다
    except Exception: _ip = None
    st.markdown("# 대외 요구자료 대응"); st.caption("접속 비밀번호를 입력하세요.")
    _left = auth_locked(_ip, st.session_state)
    if _left: st.error(f"비밀번호를 여러 번 틀려 {_left // 60 + 1}분 동안 잠겼습니다."); st.stop()
    pw = st.text_input("비밀번호", type="password", label_visibility="collapsed")
    if pw and hmac.compare_digest(pw.encode("utf-8"), os.environ["APP_PASSWORD"].encode("utf-8")): auth_ok(_ip, st.session_state); st.session_state["authed"] = True; st.rerun()
    elif pw:
        _n, _delay = auth_fail(_ip, st.session_state); time.sleep(1.0 + _delay)
        print(f"[auth] 비밀번호 실패 {_n}회 ip={_ip or '-'}", file=sys.stderr)              # 비밀번호 자체는 기록하지 않는다
        st.error("비밀번호가 맞지 않습니다." + (" 5회 실패로 15분 동안 잠깁니다." if _n >= 5 else ""))
    st.stop()
ctx.DEMO = os.environ.get("APP_DEMO", "") == "1"                  # 시연 파일 선택칸(샘플) 표시 여부. 실제 사용에서는 끔
# ---- 접속자(세션)별 AI 설정: 키·모델·호출 기록은 이 브라우저 세션에만 속한다 ----
st.session_state.setdefault("llm_cfg", {}); st.session_state.setdefault("llm_state", {}); st.session_state.setdefault("llm_log", [])
llm.configure(st.session_state["llm_cfg"], st.session_state["llm_state"], st.session_state["llm_log"])
ctx.HAS_API = llm.available()
ctx.BUILD = _build_info()
ctx.ORG = db.get_settings()                                        # 조직 설정(부서장·내선·발신 표기): 기록 DB, 환경변수는 보조
if not getattr(normalize, "_loaded", False): normalize.load_from_db(); normalize._loaded = True      # 화면에서 편집한 지표 사전이 있으면 프로세스당 1회 적용

NAV = [("업무", [("홈", "home"), ("현황", "monitoring"), ("검토·승인", "task_alt")]),
       ("자료", [("지표 데이터", "database"), ("참고 문서", "menu_book"), ("과거 답변 등록", "library_add")]),
       ("", [("기록", "search"), ("설정", "settings")])]
PAGE_NAMES = [n for _, items in NAV for n, _ in items]
HIDDEN_PAGES = ["새 요구서 처리"]                                   # 메뉴에는 없고 홈 검수의 '자세히 고치기'로만 들어오는 단계 화면
ALIASES = {"기록 조회": ("기록", "찾기·고치기"), "기록에 묻기": ("기록", "물어보기")}   # 옛 이름·바로가기 → 기록 화면의 보기

if "_goto" in st.session_state:
    _p, _s = st.session_state.pop("_goto")
    st.session_state["nav"] = _p
    if _s is not None: st.session_state["step"] = _s
if st.session_state.get("nav") in ALIASES:
    _pg, _view = ALIASES[st.session_state["nav"]]; st.session_state["nav"] = _pg; st.session_state["rec_view"] = _view
page = st.session_state.get("nav") if st.session_state.get("nav") in PAGE_NAMES + HIDDEN_PAGES else "홈"

with st.sidebar:
    ui.brand("대외 요구자료 대응", "국정감사·감사·교육부 요구자료")
    clicked = ui.nav(NAV, page)
    if clicked and clicked != page: go(clicked)
    with st.expander("내 이름(기록용)", expanded=False):
        st.text_input("담당자", st.session_state.get("user_name", "담당자"), key="user_name")
        st.text_input("검토자(팀장)", st.session_state.get("reviewer_name", "팀장"), key="reviewer_name")
    ctx.USER = st.session_state.get("user_name") or "담당자"; ctx.REVIEWER = st.session_state.get("reviewer_name") or "팀장"
    u = llm.usage_summary() if ctx.HAS_API and llm.log() else None
    ui.sidefoot(ctx.USER, ctx.HAS_API, f"AI {llm.model_label()}" if ctx.HAS_API else "AI 연결 안 됨 · 규칙만 동작",
                (f"이 세션 호출 {u['calls']}회 · 추정 ${u['cost_usd']:.3f}" + (f" · 캐시 읽기 {u['cache_read_tokens'] // 1000}k토큰" if u.get("cache_read_tokens") else "") if u else "") + ("<br>시연 모드(샘플 파일 선택칸 표시)" if ctx.DEMO else "") + (f"<br>{ui.esc(ctx.BUILD)}" if ctx.BUILD else ""))

PAGES = {"홈": home.page_chat, "새 요구서 처리": process.page_process, "검토·승인": records.page_review, "과거 답변 등록": data.page_register, "지표 데이터": data.page_data,
         "참고 문서": data.page_refdocs, "기록": records.page_records, "현황": records.page_status, "설정": settings.page_settings}
if not ctx.HAS_API and page != "설정" and not st.session_state.get("ai_banner_seen"):      # 큰 배너는 세션에서 한 번만, 이후는 사이드바 표시와 홈의 한 줄
    ui.ai_banner(lambda: go("설정")); st.session_state["ai_banner_seen"] = True
PAGES[page]()

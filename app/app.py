"""EBS 대외 요구자료 대응 에이전트 — 프로토타입
실행: python start.py  (또는 streamlit run app.py). 시연 파일 선택칸까지 보이게 하려면 APP_DEMO=1 (run_demo.bat).

화면 구성(사람이 하는 일 순서대로):
  홈              — 할 일·현황 한눈에, 세 가지 작업으로 바로 이동
  새 요구서 처리  — 1 요구서 읽기 → 2 수치 맞춰 보기 → 3 회신 초안 (한 흐름, 단계 표시)
  검토·승인       — 팀장이 초안을 승인·반려
  과거 답변 등록  — 예전에 낸 요구서와 그때 제출한 값을 기록에 넣기
  기록 조회 · 현황 · 기록에 묻기 · 설정
역할 경계: AI는 읽기·문안·점검·기록 조회만. 수치 계산·대조는 코드, 사유·확정은 담당자."""
import os, io, re, sys, subprocess, datetime as dt, zipfile, json, threading, queue, time
from pathlib import Path

def _in_streamlit():
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        return get_script_run_ctx() is not None
    except Exception:
        return False

if __name__ == "__main__" and not _in_streamlit():
    subprocess.run([sys.executable, str(Path(__file__).parent / "start.py")]); sys.exit()

import pandas as pd
import streamlit as st
import db, extract, compare, pii, docread, normalize, search, suggest, draft, hwpx_out, hwpx_build, llm, assist, history_qa, tabular, ui, plan, agent, dashboard_import, refdocs, report

st.set_page_config(page_title="대외 요구자료 대응", page_icon="📁", layout="wide")
ui.inject()
if os.environ.get("APP_PASSWORD") and not st.session_state.get("authed"):          # 외부 서버에 올릴 때의 최소 잠금. 사내망 전용이면 비워 둔다
    import hmac
    st.markdown("# 대외 요구자료 대응"); st.caption("접속 비밀번호를 입력하세요.")
    pw = st.text_input("비밀번호", type="password", label_visibility="collapsed")
    if pw and hmac.compare_digest(pw.encode("utf-8"), os.environ["APP_PASSWORD"].encode("utf-8")): st.session_state["authed"] = True; st.rerun()
    elif pw: time.sleep(1.0); st.error("비밀번호가 맞지 않습니다.")
    st.stop()
HERE = Path(__file__).parent
SAMPLE = HERE / "sample_data"
TPL_DIR = HERE / "templates"
OUT_DIR = HERE / "storage" / "out"; OUT_DIR.mkdir(parents=True, exist_ok=True)
DEMO = os.environ.get("APP_DEMO", "") == "1"                      # 시연 파일 선택칸(샘플) 표시 여부. 실제 사용에서는 끔
# ---- 접속자(세션)별 AI 설정: 키·모델·호출 기록은 이 브라우저 세션에만 속한다 ----
st.session_state.setdefault("llm_cfg", {}); st.session_state.setdefault("llm_state", {}); st.session_state.setdefault("llm_log", [])
llm.configure(st.session_state["llm_cfg"], st.session_state["llm_state"], st.session_state["llm_log"])
HAS_API = llm.available()

@st.cache_data(ttl=600, show_spinner=False)
def _build_info() -> str:
    """사이드바 '빌드' 표시: 배포 스크립트가 만든 build_info.json(커밋·시각) → 없으면 로컬 git → 없으면 빈 문자열. '배포가 반영됐나'를 화면에서 확인한다."""
    try:
        p = HERE / "build_info.json"
        if p.exists():
            j = json.loads(p.read_text(encoding="utf-8-sig")); return f"빌드 {j.get('built_at', '')} · {j.get('commit', '')}".strip(" ·")
        out = subprocess.run(["git", "log", "-1", "--format=%h %cd", "--date=format:%Y-%m-%d %H:%M"], cwd=HERE, capture_output=True, text=True, timeout=3)
        if out.returncode == 0 and out.stdout.strip(): h, _, d = out.stdout.strip().partition(" "); return f"빌드 {d} · {h} (로컬)"
    except Exception: pass
    return ""
BUILD = _build_info()
ORG = db.get_settings()                                            # 조직 설정(부서장·내선·발신 표기): 기록 DB, 환경변수는 보조
if not getattr(normalize, "_loaded", False): normalize.load_from_db(); normalize._loaded = True      # 화면에서 편집한 지표 사전이 있으면 프로세스당 1회 적용

@st.cache_data(ttl=120, show_spinner=False)
def _cached(name: str, sig: tuple, *args):
    return getattr(db, name)(*args)
def _q(name: str, *args):
    """자주 읽는 기록 조회를 캐시한다. 키에 DB 변경 신호(파일·WAL 수정 시각)를 넣어 쓰기가 있으면 다음 실행에서 다시 읽는다."""
    return _cached(name, db.signature(), *args)

NAV = [("업무", [("홈", "home"), ("현황", "monitoring"), ("검토·승인", "task_alt")]),
       ("자료", [("지표 데이터", "database"), ("참고 문서", "menu_book"), ("과거 답변 등록", "library_add")]),
       ("", [("기록", "search"), ("설정", "settings")])]
PAGE_NAMES = [n for _, items in NAV for n, _ in items]
HIDDEN_PAGES = ["새 요구서 처리"]                                   # 메뉴에는 없고 홈 검수의 '자세히 고치기'로만 들어오는 단계 화면
ALIASES = {"기록 조회": ("기록", "찾기·고치기"), "기록에 묻기": ("기록", "물어보기")}   # 옛 이름·바로가기 → 기록 화면의 보기

def go(page: str, step: int | None = None):
    """다음 실행에서 메뉴·단계를 바꾼다."""
    st.session_state["_goto"] = (page, step); st.rerun()

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
    USER = st.session_state.get("user_name") or "담당자"; REVIEWER = st.session_state.get("reviewer_name") or "팀장"
    u = llm.usage_summary() if HAS_API and llm.log() else None
    ui.sidefoot(USER, HAS_API, f"AI {llm.model_label()}" if HAS_API else "AI 연결 안 됨 · 규칙만 동작",
                (f"이 세션 호출 {u['calls']}회 · 추정 ${u['cost_usd']:.3f}" + (f" · 캐시 읽기 {u['cache_read_tokens'] // 1000}k토큰" if u.get("cache_read_tokens") else "") if u else "") + ("<br>시연 모드(샘플 파일 선택칸 표시)" if DEMO else "") + (f"<br>{ui.esc(BUILD)}" if BUILD else ""))

@st.cache_data(show_spinner=False, max_entries=20)
def render_hwpx_cached(tpl_bytes: bytes, fill_json: str, rows_json: str) -> bytes:
    return hwpx_out.render(tpl_bytes, json.loads(fill_json), json.loads(rows_json))

def read_doc(uploaded) -> str:
    try:
        return docread.read(uploaded.name, uploaded.getvalue())
    except Exception as e:
        st.error(f"문서를 읽지 못했습니다: {e}"); return ""

# ---------------- 표 입력(엑셀 · 회신 문서의 표 · 직접 입력) ----------------
def _grid_to_values(grid, key: str, source_version_default: str | None = None, base_date_default: str | None = None):
    """격자(엑셀 시트 또는 문서의 표) → 제출값 표. 긴 형식은 바로, 가로 펼침 표는 열 확인을 거친다. (df | None) 반환."""
    info = tabular.analyze(grid)
    if info.shape == "empty":
        st.error("표에서 자료 행을 찾지 못했습니다. 시트(또는 표)를 바꿔 보세요."); return None
    if info.shape == "long":
        try: return tabular.long_table(grid, info)
        except ValueError as e: st.error(f"표를 읽지 못했습니다: {e}"); return None
    st.markdown("**표 읽는 법 확인** — 행이 센터, 열이 지표인 표로 읽었습니다. 아래 세 가지만 확인하면 됩니다. 값은 그대로 옮기고 계산하지 않습니다."
                + (f"  \n표 제목: {info.title}" if info.title else "") + (f"  \n주석: {' / '.join(info.notes[:2])}" if info.notes else ""))
    st.dataframe(tabular.preview(grid, info), height=180, width="stretch")
    sug = st.session_state.get(f"{key}_map")
    if HAS_API and st.button("AI에게 열 구성 물어보기", key=f"{key}_sug", help="표 제목·머리글·앞 5행만 보냅니다(연락처 패턴은 가린 뒤). 제안은 초안이며 아래에서 확정합니다."):
        try: sug = run_ai("표 구조 판단 중", lambda: tabular.suggest_mapping(grid, info), "약 5초"); st.session_state[f"{key}_map"] = sug
        except Exception as e: st.warning(f"AI 제안 실패, 규칙 추정값을 사용합니다: {llm.explain_error(e) or e}")
    if sug and sug.get("note"): st.caption(f"AI 메모: {sug['note']}")
    cols = list(range(grid.width())); label = info.col_label
    c_default = [sug["center_col"]] if sug and sug.get("center_col") is not None else (info.center_cols or ([info.center_col] if info.center_col is not None else []))
    v_default = [c for c, _ in sug["value_cols"]] if sug and sug.get("value_cols") else info.value_cols
    d_default = (sug.get("base_date") if sug else None) or info.base_date_hint or base_date_default or ""
    if len(c_default) > 1: st.caption(f"행 이름이 겹쳐서 {' + '.join(label(c) for c in c_default)} 두 열을 이어 붙여 센터명으로 씁니다.")
    c1, c2, c3 = st.columns([1.3, 2, 1])
    center_col = c1.multiselect("1. 센터(행 이름) 열", cols, default=[c for c in c_default if c in cols], format_func=label, key=f"{key}_cc")
    value_cols = c2.multiselect("2. 값 열", [c for c in cols if c not in center_col], default=[c for c in v_default if c not in center_col], format_func=label, key=f"{key}_vc")
    dated = [c for c in value_cols if c in info.col_dates]; undated = [c for c in value_cols if c not in info.col_dates]
    if dated:                                                  # 월별 펼침 표: 열 머리글의 월이 그 열의 기준일
        ds = sorted({info.col_dates[c] for c in dated})
        bd_label, bd_help = ("3. 기준일 (월이 없는 열에 적용)" if undated else "3. 기준일 (열마다 자동)"), \
            f"월이 적힌 열 {len(dated)}개는 그 월의 말일이 기준일이 됩니다({ds[0]} ~ {ds[-1]}). " + ("월이 없는 열(" + ", ".join(label(c) for c in undated[:4]) + ")에만 이 기준일이 쓰입니다." if undated else "이 칸은 쓰이지 않습니다.")
    else:
        bd_label, bd_help = "3. 기준일 (필수)", "이 표의 값이 '언제 기준' 수치인지. 표 제목·주석에 '… 기준' 날짜가 있으면 자동으로 채워집니다. 지난번 값과 같은 기준일이어야 맞춰 볼 수 있습니다."
    base_date = c3.text_input(bd_label, d_default, placeholder="2026-06-30", key=f"{key}_bd", help=bd_help, disabled=bool(dated) and not undated)
    if not base_date.strip() and (undated or not dated): c3.error("기준일을 넣어야 다음으로 넘어갑니다.")
    if dated: st.caption(f"열 머리글의 월을 기준일로 씁니다: {ds[0]} ~ {ds[-1]} ({len(ds)}개월, 열 {len(dated)}개). 같은 이름의 열은 한 지표로 묶이고 월만 달라집니다.")
    names = dict(sug["value_cols"]) if sug and sug.get("value_cols") else {}
    # 지표명은 '월을 뺀 머리글'별로 한 번만 정한다(월별 표에서 열 20개를 하나씩 고치지 않게)
    groups: dict[str, list[int]] = {}
    for c in value_cols: groups.setdefault(info.col_indicator(c), []).append(c)
    ind_df = pd.DataFrame([{"열": (g_ if len(cs) == 1 else f"{g_} ({len(cs)}개 열)"), "지표명": names.get(cs[0]) or tabular.default_indicator(g_)} for g_, cs in groups.items()])
    if len(ind_df):
        ind_df = st.data_editor(ind_df, hide_index=True, width="stretch", key=f"{key}_ind", disabled=["열"],
                                column_config={"지표명": st.column_config.TextColumn("저장될 지표명 (사전에 있으면 정규 지표명이 기본값)")})
    blank_rows = [r for r in info.data_rows if center_col and not any(tabular.text(grid.cell(r, c)) for c in center_col)]
    drop_totals = st.checkbox("합계·소계·평균 행 제외", value=True, key=f"{key}_dt",
                              help=f"제외 대상 행: {[grid.row_offset + r for r in info.total_rows] or '없음'}" + (f" · 센터 이름이 빈 행 {len(blank_rows)}개(상위 구분의 소계로 보임)는 항상 제외" if blank_rows else ""))
    if blank_rows: st.caption(f"센터 이름이 빈 행 {len(blank_rows)}개는 상위 구분(예: 교육청)의 소계로 보고 넣지 않습니다. 센터별 값만 저장됩니다.")
    try:
        indicators = {c: str(n).strip() for (g_, cs), n in zip(groups.items(), ind_df["지표명"].tolist(), strict=True) for c in cs} if len(ind_df) else {}
        return tabular.wide_to_long(grid, info, center_col, value_cols, base_date.strip(), indicators, drop_totals, source_version_default)
    except ValueError as e:
        st.warning(str(e)); return None

def _file_values(up, key: str, base_date_default: str | None):
    """올린 파일 하나 → 제출값 표 목록. 엑셀/CSV는 시트 하나, 문서는 고른 표마다 하나. (df 목록) 반환."""
    name = up.name.lower()
    if name.endswith((".xlsx", ".xlsm", ".xls", ".csv")):
        try:
            sheets = compare.list_sheets(up)
            sheet = st.selectbox("시트", sheets, key=f"{key}_sheet") if len(sheets) > 1 else None
            grid = tabular.read_grid(up, sheet)
        except Exception as e:
            st.error(f"파일을 읽지 못했습니다: {e}"); return []
        df = _grid_to_values(grid, f"{key}_{sheet}", base_date_default=base_date_default)
        return [df] if df is not None else []
    try: grids, text = tabular.grids_from_document(up.name, up.getvalue())
    except Exception as e:
        st.error(f"문서를 읽지 못했습니다: {e}"); return []
    if not grids:
        st.error("문서에서 표를 찾지 못했습니다. hwp·hwpx·docx는 표 개체여야 하고(탭·공백으로 맞춘 글은 표가 아님), PDF는 글자가 추출되는 파일이어야 합니다(스캔본 불가). 표가 없으면 아래 '직접 입력'을 쓰세요.")
        with st.expander(f"진단: 추출된 본문 {len(text)}자 — 앞부분 보기"):
            st.text("\n".join(text.splitlines()[:40]) or "(본문이 비어 있음 — 배포용 문서이거나 그림으로 된 문서일 수 있음)")
        return []
    best = max(range(len(grids)), key=lambda i: tabular.numeric_cells(grids[i]))
    label = lambda g: f"{g.source_sheet} — {len(g.rows)}행 × {g.width()}열" + (f" (문서 {g.row_offset}번째 줄부터)" if g.row_offset > 1 else "")
    picks = st.multiselect("문서 안의 표 (숫자가 많은 표가 기본 선택 · 여러 개 고를 수 있음)", grids, default=[grids[best]], format_func=label, key=f"{key}_tbl") if len(grids) > 1 else grids
    out = []
    for g in picks:
        if len(picks) > 1: st.markdown(f"**{g.source_sheet}**")
        df = _grid_to_values(g, f"{key}_{g.source_sheet}", f"회신 문서({up.name}) 표에서 추출", base_date_default)
        if df is not None: out.append(df)
    return out

def value_sources(key: str, what: str = "값", base_date_default: str | None = None, demo_files: list[Path] | None = None):
    """값 입력 한 곳: 엑셀·CSV·회신 문서(hwp·hwpx·docx·pdf)를 종류 구분 없이 여러 개 올리면 파일마다 읽어 하나로 모은다. 파일이 없으면 직접 입력.
    (df | None, 출처 이름) 반환. 시연 모드면 시연 파일 선택칸이 추가된다."""
    parts, names = [], []
    ups = st.file_uploader(f"{what} 파일 — 엑셀(xlsx·xls)·CSV, 회신 공문(hwp·hwpx·docx·pdf). 여러 개를 한 번에 올려도 됩니다",
                           type=["xlsx", "xls", "xlsm", "csv", "hwp", "hwpx", "docx", "pdf"], accept_multiple_files=True, key=f"{key}_files",
                           help="긴 형식(지표명·센터명·기준일·값)은 그대로, 실적표 서식(제목 행·병합 머리글·합계 행)은 열 확인을 거쳐 읽습니다. 구형 엑셀(.xls)과 이름만 .xls인 시스템 내려받기(HTML 표·탭 구분)도 읽습니다. 한글 HWP(5.0)는 자동 변환(암호·배포용 문서 제외). 올린 파일마다 결과가 아래에 쌓이고, 전부 합쳐 저장됩니다.")
    if DEMO and demo_files:
        for pick in st.multiselect("시연 파일", demo_files, default=demo_files if len(demo_files) == 1 else None, format_func=lambda p: p.name, key=f"{key}_demo"):
            with st.container(border=True):
                st.markdown(f"**{pick.name}**")
                df = _grid_to_values(tabular.read_grid(pick), f"{key}_demo_{pick.name}", base_date_default=base_date_default) if pick.name.startswith("실적표_") else compare.load_values(pick)
                if df is not None: parts.append(df); names.append(pick.name); st.caption(f"읽음: {len(df)}건")
    for up in ups or []:
        with st.container(border=True):
            st.markdown(f"**{ui.esc(up.name)}**  \n<span class='small-muted'>{up.size / 1024:.0f} KB</span>", unsafe_allow_html=True)
            dfs = _file_values(up, f"{key}_{up.file_id}", base_date_default)
            if dfs: parts += dfs; names.append(up.name); st.caption(f"읽음: {sum(len(d) for d in dfs)}건" + (f" · 표 {len(dfs)}개" if len(dfs) > 1 else ""))
    with st.expander("파일 없이 직접 입력", expanded=not ups and not parts):
        st.caption("행을 추가해 지표명·센터명·기준일·값을 채우세요. 뒤의 네 칸(근거)은 선택입니다. 파일과 함께 써도 됩니다.")
        man = st.data_editor(pd.DataFrame(columns=tabular.MANUAL_COLS), num_rows="dynamic", width="stretch", key=f"{key}_man")
        if not man.dropna(how="all").empty:
            try: parts.append(tabular.from_records(man)); names.append("직접 입력")
            except ValueError as e: st.error(str(e))
    if not parts: return None, ""
    df = pd.concat(parts, ignore_index=True)
    dup = df.duplicated(subset=["indicator", "center", "base_date"], keep=False)
    if dup.any():
        st.warning(f"같은 지표·센터·기준일 값이 두 곳에 있습니다({int(dup.sum())}행). 나중에 올린 파일의 값만 남깁니다. "
                   "둘 다 두려면 한쪽 지표명을 바꾸세요. 예: " + " / ".join(f"{r.indicator}·{r.center}·{r.base_date}" for r in df[dup].head(3).itertuples()))
        df = df.drop_duplicates(subset=["indicator", "center", "base_date"], keep="last").reset_index(drop=True)
    return df, " + ".join(dict.fromkeys(names))

def run_ai(label: str, fn, hint: str = "", rules_label: str | None = None, job: dict | None = None):
    """AI 호출을 진행 상자 안에서 실행한다. 호출은 작업 스레드에서 돌고, 화면은 0.5초마다 경과 시간을 갱신하며
    llm이 보내는 단계 글(모델·입력 크기·응답 시간·잘림 재시도·도구 호출)을 줄줄이 보여 준다. 끝나면 상자가 접히고 걸린 시간이 남는다.
    job(세션에 둔 dict)을 주면 스레드·큐를 거기 보관해, 처리 중 화면이 다시 실행돼도(다른 메뉴 클릭 등) 결과를 잃지 않고 이어서 기다린다.
    AI가 없으면 규칙 기반으로 바로 실행(짧으니 스피너만). fn은 Streamlit 요소를 만지면 안 된다(결과만 반환)."""
    if not HAS_API:
        with st.spinner(rules_label or f"{label} (규칙 기반)"): return fn()
    resumed = bool(job and job.get("q") is not None)
    box = st.status(f"{label} — {llm.model_label()} " + ("이어서 기다리는 중" if resumed else "준비"), expanded=True)
    with box:
        timer = st.empty(); log_box = st.container()
    if resumed: q, t0 = job["q"], job["t0"]
    else:
        q = queue.Queue(); t0 = time.time()
        cfg = (st.session_state["llm_cfg"], st.session_state["llm_state"], st.session_state["llm_log"])
        def worker():
            llm.configure(*cfg); llm.set_progress(lambda m: q.put(m))
            try: q.put(("done", fn()))
            except BaseException as e: q.put(("err", e))
            finally: llm.set_progress(None)
        threading.Thread(target=worker, daemon=True).start()
        if job is not None: job.update(q=q, t0=t0, last="")
    last, outcome = (job or {}).get("last", ""), None
    while outcome is None:
        try: item = q.get(timeout=0.5)
        except queue.Empty: item = None
        if isinstance(item, tuple): outcome = item
        elif isinstance(item, str):
            last = item; log_box.write(f"{dt.datetime.now().strftime('%H:%M:%S')}  {item}"); box.update(label=f"{label} — {item[:90]}", state="running", expanded=True)
            if job is not None: job["last"] = item
        timer.markdown(f"**경과 {time.time() - t0:.0f}초**" + (f" · {hint}" if hint else "") + (f"  \n지금: {last}" if last else ""))
    took = time.time() - t0
    if outcome[0] == "err":
        box.update(label=f"{label} 실패 — {took:.0f}초", state="error", expanded=True); timer.error(llm.explain_error(outcome[1]) or str(outcome[1]))
        raise outcome[1]
    timer.markdown(f"**완료 — {took:.0f}초**"); box.update(label=f"{label} 완료 — {took:.0f}초", state="complete", expanded=False)
    return outcome[1]

def analyze_with_status(text: str):
    """요구서 추출을 진행 상자 안에서. 긴 요구서는 수십 초~수 분."""
    return run_ai(f"요구 항목 읽는 중 (본문 {len(text):,}자)", lambda: analyze(text), "보통 5~10초, 긴 요구서는 수 분", "요구 항목 읽는 중… (규칙 기반)")

def analyze(text: str):
    res, how = extract.extract(text)
    items = normalize.normalize_items(res.get("items") or [])
    res["items"] = items if how.startswith("Claude") else normalize.normalize_llm(items)      # 모델 추출이면 지표 분류를 다시 묻지 않는다(중복 호출 제거)
    return res, how

def request_input(key: str, demo_glob: str | None):
    """요구서 입력: 파일 올리기 또는 붙여 넣기. 시연 모드면 샘플 선택. (원문, 파일명) 반환."""
    text, name = "", ""
    if DEMO and demo_glob:
        files = sorted(SAMPLE.glob(demo_glob))
        pick = st.selectbox("시연 요구서", ["(파일 올리기)"] + files, format_func=lambda p: p if isinstance(p, str) else p.name, key=f"{key}_demo")
        if not isinstance(pick, str): text, name = pick.read_text(encoding="utf-8"), pick.name
    if not text:
        up = st.file_uploader("요구서 파일 (hwp · hwpx · pdf · docx · txt)", type=["txt", "hwp", "hwpx", "pdf", "docx"], key=f"{key}_up")
        if up: text, name = read_doc(up), up.name
    text = st.text_area("요구서 내용 (파일이 없으면 여기에 붙여 넣기 · 고칠 수 있음)", text, height=200, key=f"{key}_text_{name}")
    return text, name

VAL_COLS = ["indicator", "center", "base_date", "value", "definition", "calc_period", "extract_date", "source_version"]
STATUS_KO = {"review_requested": "검토 대기", "approved": "승인", "rejected": "반려", "comment": "의견", "draft": "초안", "confirmed": "확정"}
def ko(status): return STATUS_KO.get(status, status)
VAL_KO = {"indicator": "지표", "center": "센터", "base_date": "기준일", "value": "값", "definition": "지표 정의", "calc_period": "집계기간",
          "extract_date": "추출시점", "source_version": "원자료 버전", "source_file": "출처 파일", "source_sheet": "출처 시트", "source_row": "출처 행"}
ITEM_KO = {"seq": "번호", "item_text": "항목 원문", "indicator": "지표", "base_date": "기준일", "period": "기간", "unit": "단위"}
STEPS = [("1단계", "요구서 읽기"), ("2단계", "수치 맞춰 보기"), ("3단계", "회신 초안")]

def req_label(r): return f"#{r['id']} {r['received_date'] or '접수일 미기재'} · {r['requester'] or '요청 주체 미기재'} — {r['title'] or '(제목 없음)'}"

# ================= 홈 = 대화 =================
REQ_EXT = (".hwp", ".hwpx", ".pdf", ".docx", ".txt"); VAL_EXT = (".xlsx", ".xls", ".xlsm", ".csv"); DASH_EXT = (".html", ".htm")

def _import_dashboard(data: bytes, name: str, include_partial: bool = False, S: dict | None = None) -> tuple[str, int]:
    """대시보드 저장 파일 → 지표 데이터 묶음 + 센터 명부. (설명, 묶음 번호). 파일 자체는 저장하지 않는다(계정 영역이 들어 있을 수 있음)."""
    S = S or dashboard_import.summarize(dashboard_import.parse(data), include_partial)
    if not S["rows"] and not S["centers"]: raise ValueError("지표 자료도 센터 명부도 찾지 못했습니다. 대시보드 페이지를 '웹페이지, 전체'로 저장했는지 확인하세요.")
    n_rep = db.data_replaced_count(S["rows"]) if S["rows"] else 0
    bid, n = db.add_data_batch(S["rows"], USER, f"통합 대시보드({name})", "대시보드 저장 파일 가져오기")
    nc = db.replace_centers(S["centers"], name) if S["centers"] else 0
    span = f", 기준일 {S['dates'][0]}~{S['dates'][-1]}" if S["dates"] else ""
    return (f"대시보드 저장 파일 '{name}'에서 지표 데이터 {n:,}건(지표 {len(S['by_indicator'])}개{span})" + (f"과 센터 명부 {nc}개소" if nc else "") + f"를 가져왔습니다(묶음 #{bid})."
            + (f" 이미 있던 값 {n_rep:,}건은 새 값으로 바뀌었습니다(묶음을 지우면 되돌아갑니다)." if n_rep else "") + " " + " / ".join(S["notes"]) + " 계정·연락처 등 개인정보 영역은 읽지 않았습니다."), bid

def _template_bytes() -> bytes | None:
    """대화 처리의 HWPX 서식: 설정에서 자리표시자 서식을 올렸을 때만 그 서식, 아니면 None(답변자료 양식을 처음부터 생성)."""
    return st.session_state.get("tpl_custom")

def _attach(case: agent.Case, files: list[tuple[str, bytes]]) -> list[str]:
    """대화에 붙인 파일 처리: 요구서 문서는 작업의 요구서로, 집계 파일(엑셀·CSV)은 지표 데이터로 넣는다. 설명 줄을 돌려준다."""
    notes = []
    for name, data in files:
        low = name.lower()
        if low.endswith(REQ_EXT):
            try: text = docread.read(name, data)
            except Exception as e: notes.append(f"'{name}'을 읽지 못했습니다: {e}"); continue
            if case["request_id"] or case["draft"] or case.get("approved"):      # 새 요구서면 새 작업(앞 건의 기록은 그대로 남는다)
                _new_case(say=False); case = st.session_state["case"]
            case["request_text"], case["request_name"] = text, name; case["uploads"].append(name)
            notes.append(f"요구서 '{name}'을 받았습니다({len(text):,}자).")
        elif low.endswith(DASH_EXT):
            try: note, _ = _import_dashboard(data, name); notes.append(note); case["plans"] = []; case["values"] = None
            except Exception as e: notes.append(f"대시보드 저장 파일 '{name}'을 읽지 못했습니다: {e}")
        elif low.endswith(VAL_EXT):
            try:
                grid = tabular.read_grid(type("U", (), {"name": name, "getvalue": lambda self, d=data: d})())
                info = tabular.analyze(grid)
                if info.shape == "long": df = tabular.long_table(grid, info)
                elif info.shape == "wide" and (info.center_cols or info.center_col is not None) and info.value_cols and (info.base_date_hint or info.col_dates):
                    df = tabular.wide_to_long(grid, info, info.center_cols or [info.center_col], info.value_cols, info.base_date_hint or "")
                else: raise ValueError("센터 열·값 열·기준일을 확인해야 합니다. '지표 데이터' 화면에서 올려 주세요.")
                recs = df.to_dict("records"); n_rep = db.data_replaced_count(recs)
                bid, n = db.add_data_batch(recs, USER, name, "대화 첨부")
                notes.append(f"집계 파일 '{name}'을 지표 데이터에 넣었습니다(묶음 #{bid}, {n}건, 지표 {', '.join(sorted(df['indicator'].unique())[:4])})." + (f" 이미 있던 값 {n_rep}건은 새 값으로 바뀌었습니다." if n_rep else "") + " 잘못 넣었으면 '지표 데이터'에서 묶음을 지우면 이전 값으로 돌아갑니다.")
                case["plans"] = []; case["values"] = None
            except Exception as e: notes.append(f"집계 파일 '{name}'을 넣지 못했습니다: {e}")
        else: notes.append(f"'{name}'은 다루지 않는 형식입니다.")
    return notes

def _rule_reply(case: agent.Case, text: str, tpl: bytes | None) -> str:
    """키 없을 때: 요구서가 있고 처리 지시면 표준 순서로 전부, 질문이면 키워드 검색, 그 외 안내."""
    if case["request_text"] and agent.is_do_it(text):
        return "\n\n".join(agent.autopilot(case, tpl))
    if agent.is_do_it(text) and normalize.normalize_all(text):                       # 요구서 없이 "센터별 등원율 요청이 들어왔어. 작성해 줘" → 말한 문장을 요구 항목으로
        agent.describe_request(case, items=[text])
        return "\n\n".join(["요구서 파일이 없어 말씀하신 문장을 요구 항목으로 적었습니다(요청 주체·기한은 미기재)."] + agent.autopilot(case, tpl, register=not re.search(r"등록\s*(은|는)?\s*(빼|제외|말|하지)", text)))
    if re.search(r"지표 데이터|무엇이 들어|뭐가 들어|뭐가 있|어떤 지표|보유 범위|들어 있", text):
        return agent.coverage_line() + "\n\n자세한 범위와 적재 묶음은 '지표 데이터' 화면에서 볼 수 있습니다."
    kw = history_qa.keyword_search(text); doc_lines = refdocs.answer_lines(text)
    if kw["requests"] or kw["reasons"] or doc_lines:
        lines = [f"'{', '.join(kw['tokens'][:4])}'로 기록을 찾았습니다(키워드 검색, AI 연결 없음)."] if (kw["requests"] or kw["reasons"]) else []
        lines += [f"- 요구서 #{r['id']} {r['received_date']} {r['requester']} — {r['title']}" for r in kw["requests"][:8]]
        lines += [f"- 사유 기록: {x['center']} {x['indicator']} {x['base_date']}: {x['reason']}" for x in kw["reasons"][:5]]
        return "\n".join(lines + ([""] if lines and doc_lines else []) + doc_lines)
    return "요구서(hwp·hwpx·pdf·docx·txt)를 붙이고 '요구하는 것들 작성해 줘'라고 하면 끝까지 준비합니다. 기록을 찾으려면 지표나 기관 이름을, 사업 내용을 물으려면 '운영 시간' '이용 대상'처럼 핵심어를 넣어 물어보세요(참고 문서가 등록돼 있어야 합니다). AI를 연결하면 자유로운 지시와 질문에 답합니다."

def _case_label(case: agent.Case) -> str:
    """지금 작업 한 줄: '요구서 #12 · ○○ 의원실 · 제목 · 확정 완료'. 아무 작업도 없으면 빈 문자열."""
    if not (case["request_text"] or case["draft"]): return ""
    req = db.get_request(case["request_id"]) if case["request_id"] else (case["extracted"] or {})
    head = f"요구서 #{case['request_id']}" if case["request_id"] else "미등록 요구"
    tail = " · ".join(str(x) for x in ((req or {}).get("requester"), (req or {}).get("title")) if x)
    return head + (f" · {tail}" if tail else "") + (" · 확정 완료" if case.get("approved") else "")

def _new_case(say: bool = True):
    """지금 작업을 접고 빈 작업으로. 모델 대화 기록은 중간을 자르지 않고 통째로 비우며, 접은 사실 한 줄만 다음 턴에 넘긴다(확정한 기록은 DB에 남는다)."""
    case: agent.Case = st.session_state.get("case") or agent.Case(); prev = _case_label(case)
    case.reset(); st.session_state["case"] = case; st.session_state["chat_msgs"] = []
    if prev: st.session_state["chat_carry"] = f"직전 작업({prev})은 접었고 지금은 새 작업입니다. 앞 작업의 기록은 바꾸지 않습니다."
    if say and prev: st.session_state.setdefault("chat", []).append({"role": "assistant", "text": f"— 새 요구 시작 — 이전 작업({prev})은 접었습니다. 요구서를 붙이거나 요구를 말씀해 주세요."})

def _approve_checks(case: agent.Case, req: dict, d: dict) -> list[tuple[str, str]]:
    """확정 전 확인 목록: (등급 bad·warn·info, 문장). 막는 것은 개인정보 패턴뿐이고 나머지는 알려만 준다."""
    out = []
    hits = [h for k in ("제목", "본문", "차이사유", "산출근거") for h in pii.scan_text(str(d.get(k, "")), f"초안 {k}")]
    if hits: out.append(("bad", f"초안에 개인정보로 보이는 패턴 {len(hits)}건 — 삭제·가명 처리 뒤 확정하세요"))
    n_flag = sum(str(d.get(k, "")).count("[확인 필요]") for k in ("제목", "본문", "차이사유", "산출근거"))
    if n_flag: out.append(("warn", f"[확인 필요] 표시 {n_flag}곳 — 그대로 확정하면 회신에 남습니다"))
    cov = case.get("coverage")
    if cov is not None and len(cov) and "판정" in cov:
        n_bad = int((cov["판정"] != "충족").sum())
        if n_bad: out.append(("warn", f"요구 항목 미충족 {n_bad}건(자료 없음·누락)"))
    if case["compare"] is not None:
        diff = case["compare"][case["compare"]["판정"] == "차이"]
        empty = [r["center"] for _, r in diff.iterrows() if not str(case["reasons"].get((r["indicator"], r["center"], r["base_date"]), "") or "").strip()]
        if empty: out.append(("warn", f"차이 사유가 빈 행 {len(empty)}건: {', '.join(empty[:5])}" + (" …" if len(empty) > 5 else "")))
    if not (req or {}).get("requester"): out.append(("warn", "요청 주체 미기재 — 회신 머리글과 파일 이름에 '요구'로 들어갑니다. 대화에서 알려 주면 반영합니다"))
    if case.get("draft_stale") or case.get("hwpx_stale") or case["hwpx"] is None: out.append(("info", "고친 내용이 한글 파일에 아직 반영되지 않았습니다 — 확정하면 다시 만들어 저장합니다"))
    if not case["request_id"]: out.append(("info", "기록에 등록되지 않은 요구 — 확정하면 요구서·제출본으로 등록됩니다"))
    return out

def _approve_now(case: agent.Case, tpl: bytes | None):
    """확정: 고친 사유를 초안 칸에 반영하고(모델 호출 없이 코드로), 한글 파일을 다시 만든 뒤 저장. 모델 대화 기록은 비우고 확정 사실만 다음 턴에 넘긴다."""
    if case.get("draft_stale") and case["draft"]: case["draft"]["차이사유"] = draft.reasons_text(case["reasons"]); case["draft_stale"] = False
    if case["hwpx"] is None or case.get("hwpx_stale"): agent.step_hwpx(case, tpl)
    out = agent.approve(case, USER, REVIEWER, OUT_DIR)
    for v in case["reasons"].values(): db.add_phrase("사유", v, USER)                     # 쓴 사유는 문구 서랍에 쌓인다(다음 건에서 고를 수 있게)
    st.session_state["chat_msgs"] = []; st.session_state["chat_carry"] = f"직전 작업을 확정했습니다(제출본 #{out['submission_id']}, 초안 #{out['draft_id']}). 새 요구는 새 작업으로 처리합니다."

def _send_card(case: agent.Case, req: dict):
    """확정 뒤: 받을 파일·메일 문안·발송 기록. 메일 발송은 담당자가 한다(이 앱은 보내지 않는다)."""
    sid, did = case["approved"]; d = case["draft"] or {}
    st.success(f"확정했습니다 · 제출본 #{sid} · 초안 #{did} · 값 {len(case['values']) if case['values'] is not None else 0}건. 파일을 받아 보내고, 보낸 뒤 발송을 기록하세요.")
    c1, c2 = st.columns([1.2, 1])
    with c1:
        if case["hwpx"]: st.download_button("회신 HWPX 받기", case["hwpx"], file_name=case["hwpx_name"], key=f"rv_dl_done_{sid}", icon=":material/download:", type="primary")
        mail = draft.mail_text(req or {}, d, case["hwpx_name"], USER, org=f"{ORG['company']} {ORG['org_name']}".strip(), greeting=(case.get("tone") or {}).get("mail_greeting"))
        st.text_input("메일 제목", mail["subject"], key=f"mail_subj_{sid}")
        st.text_area("메일 본문 — 복사해서 메일 프로그램에 붙이세요", mail["body"], height=170, key=f"mail_body_{sid}")
    with c2:
        st.markdown("**발송 기록** — 보낸 뒤 적어 두면 현황에 '발송 완료'로 표시됩니다.")
        sent_to = st.text_input("보낸 곳", (req or {}).get("requester") or "", key=f"disp_to_{sid}")
        method = st.selectbox("방법", ["메일", "공문(전자문서)", "팩스", "직접 전달", "기타"], key=f"disp_m_{sid}")
        sent_at = st.date_input("보낸 날짜", dt.date.today(), key=f"disp_d_{sid}")
        memo = st.text_input("메모(선택)", key=f"disp_n_{sid}", placeholder="예: 담당 보좌관 메일, 공문번호")
        if st.button("발송 기록 저장", key=f"disp_save_{sid}", type="primary", icon=":material/send:", disabled=not sent_to.strip()):
            db.add_dispatch(sid, did, str(sent_at), sent_to.strip(), method, USER, memo.strip()); st.toast("발송을 기록했습니다"); st.rerun()
        for x in db.dispatches_for(sid): st.caption(f"✓ {x['sent_at']} · {x['method']} → {x['sent_to']} · {x['sent_by']}" + (f" · {x['note']}" if x["note"] else ""))
    if st.button("새 요구 시작", key=f"rv_new_{sid}", icon=":material/add:"): _new_case(); st.rerun()

def _review_panel(case: agent.Case, tpl: bytes | None):
    """검수: 위에서 아래로 ① 수치·대조(한 표) → ② 차이 사유 → ③ 회신 초안 → ④ 예상 질문 → 확정. 입력칸 키에 판 번호(rev)를 넣어 모델·코드가 초안을 새로 쓰면 칸도 새 내용으로 바뀐다."""
    rev = int(case.get("rev") or 0); locked = bool(case.get("approved"))
    with st.container(border=True):
        st.markdown("### 확정된 작업" if locked else "### 검수 — 확인하고 승인하면 확정됩니다")
        req = db.get_request(case["request_id"]) if case["request_id"] else (case["extracted"] or {})
        st.caption(f"{('요구서 #' + str(case['request_id'])) if case['request_id'] else '요구서 미등록(승인하면 등록)'} · {req.get('requester') or '요청 주체 미기재'} · {req.get('title') or ''} · 기한 {req.get('due_date') or '미기재'}")
        v = case["values"]; n_vals = len(v) if v is not None else 0
        m = case["compare"]; diff = m[m["판정"] == "차이"] if m is not None else None; n_diff = len(diff) if diff is not None else 0
        n_reason = sum(1 for _, r in (diff.iterrows() if diff is not None else []) if str(case["reasons"].get((r["indicator"], r["center"], r["base_date"]), "") or "").strip())
        ui.progress_chips([("읽기", bool(case["items"]), f"{len(case['items'])}항목"), ("등록", bool(case["request_id"]), f"#{case['request_id']}" if case["request_id"] else ("보류" if case.get("no_register") else "")),
                           ("값", n_vals > 0, f"{n_vals}건"), ("대조", m is not None, f"차이 {n_diff}" if m is not None else "첫 제출"), ("사유", n_diff == 0 or n_reason == n_diff, f"{n_reason}/{n_diff}" if n_diff else ""),
                           ("초안", bool(case["draft"]) and not case.get("draft_stale"), "다시 쓰기 필요" if case.get("draft_stale") else ""), ("HWPX", bool(case["hwpx"]) and not case.get("hwpx_stale"), "반영 전" if case.get("hwpx_stale") else ""),
                           ("확정", locked, "")])
        missing = [case["items"][i]["item_text"] for i, pl in enumerate(case["plans"]) if not pl["options"] or pl["mode"] in ("none", "unknown_indicator")]
        if missing and not locked: st.warning("자료가 없어 새로 산출해야 하는 항목: " + " / ".join(missing) + ". 초안에는 [확인 필요]로 들어가 있습니다. 집계 파일을 대화에 붙이면 지표 데이터에 넣고 다시 처리합니다.")
        st.markdown("**① 이번에 낼 수치와 대조**")
        if n_vals:
            if m is not None:
                t = v[["indicator", "center", "base_date", "value"]].merge(m[["indicator", "center", "base_date", "old_value", "diff", "판정", "단서"]], on=["indicator", "center", "base_date"], how="left")
                t["판정"] = t["판정"].fillna("짝 없음"); t["단서"] = t["단서"].fillna("")
                t["사유"] = [case["reasons"].get((r.indicator, r.center, r.base_date), "") or "" for r in t.itertuples()]
                show = t.rename(columns={"indicator": "지표", "center": "센터", "base_date": "기준일", "value": "이번", "old_value": "지난번", "diff": "차이"})[["지표", "센터", "기준일", "이번", "지난번", "차이", "판정", "단서", "사유"]]
                def _rs(r):
                    if r["판정"] == "차이": return (["background-color: #FFF4D6"] if str(r["사유"]).strip() else ["background-color: #FDE8E8"]) * len(r)
                    return [""] * len(r)
                k1, k2, k3, k4 = st.columns(4); k1.metric("이번 수치", n_vals); k2.metric("짝이 맞는 행", int(m["판정"].isin(["차이", "일치", "값 누락"]).sum())); k3.metric("차이", n_diff); k4.metric("일치", int((m["판정"] == "일치").sum()))
                st.dataframe(show.style.apply(_rs, axis=1), width="stretch", hide_index=True, height=min(60 + 35 * len(show), 380))
                st.caption(f"지난번: 제출본 #{case.get('old_sid')} · 노랑: 지난번과 다름(사유 있음) · 빨강: 다른데 사유가 비어 있음 · 짝 없음: 지난번 제출에 없던 행")
            else:
                st.caption(f"{n_vals}건 · 지표 {', '.join(sorted(v['indicator'].unique())[:5])} · 기준일 {', '.join(sorted(v['base_date'].unique())[:6])}" + (" …" if v["base_date"].nunique() > 6 else "") + " · 같은 지표·기준일의 과거 제출값이 없어 대조할 짝이 없습니다(첫 제출).")
                st.dataframe(v[VAL_COLS + ["source_file"]].rename(columns=VAL_KO), width="stretch", height=min(60 + 35 * len(v), 360), hide_index=True)
        else: st.info("가져온 수치가 없습니다.")
        if n_diff:
            st.markdown("**② 차이 사유** (제안이 채워져 있음 · 고치면 '초안 다시 쓰기'로 반영)")
            cands = db.phrase_candidates("사유") if not locked else []
            for _, r in diff.iterrows():
                k = (r["indicator"], r["center"], r["base_date"]); cur = case["reasons"].get(k, "") or ""
                a, b = st.columns([6, 1], vertical_alignment="bottom")
                new = a.text_input(f"{r['center']} · {r['indicator']} · {r['base_date']} ({r['old_value']} → {r['new_value']})", value=cur, key=f"rv_reason_{k}_{rev}", disabled=locked)
                if new != cur: case["reasons"][k] = new; case["draft_stale"] = True; cur = new
                if not locked:
                    with b.popover("문구", icon=":material/library_books:", width="stretch"):           # 자주 쓰는 사유 문구 서랍
                        if cur.strip() and st.button("지금 문구를 서랍에 저장", key=f"rv_ph_save_{k}_{rev}", type="tertiary", icon=":material/bookmark_add:"): db.add_phrase("사유", cur, USER); st.toast("저장했습니다"); st.rerun()
                        if not cands: st.caption("저장된 문구가 없습니다. 확정하면 쓴 사유가 자동으로 쌓이고, 설정 → 서식·사전에서 직접 넣을 수도 있습니다.")
                        for j, t_ in enumerate(cands):
                            if st.button(t_, key=f"rv_ph_{k}_{j}_{rev}", type="tertiary", width="stretch"): case["reasons"][k] = t_; case["draft_stale"] = True; case.bump(); st.rerun()
            if not locked:
                c1, c2 = st.columns([1.6, 3], vertical_alignment="center")
                if c1.button("사유 반영해 초안 다시 쓰기", key=f"rv_redraft_{rev}", type="primary" if case.get("draft_stale") else "secondary", icon=":material/edit_note:"):
                    run_ai("초안 다시 쓰는 중", lambda: (agent.step_draft(case), agent.step_hwpx(case, tpl)), "약 10~15초", "초안 다시 쓰는 중… (규칙 기반)"); st.rerun()
                if case.get("draft_stale"): c2.warning("사유가 바뀌었습니다. 초안을 다시 써야 회신 본문에 반영됩니다(확정 때는 사유 칸만 자동 반영).", icon=":material/sync_problem:")
        with st.expander("③ 회신 초안" + (" — 다시 쓰기 필요" if case.get("draft_stale") else ""), expanded=True):
            d = case["draft"] or {}
            tone = case.get("tone") or agent.tone_for(case, req); kinds = list(draft.DEFAULT_PRESETS)
            tc1, tc2, tc3 = st.columns([1.1, 3.2, 1.4], vertical_alignment="center")
            pick = tc1.selectbox("회신 유형", kinds, index=kinds.index(tone["kind"]) if tone["kind"] in kinds else 0, key=f"rv_tone_{rev}", disabled=locked,
                                 help="요청 주체 유형에 따라 문서 제목(답변자료·제출자료·설명자료)·확인 줄·문체 지침이 달라집니다. 설정 → 서식·사전에서 고칩니다.")
            if pick != tone["kind"] and not locked:
                case["tone_kind"] = pick; agent.tone_for(case, req); case["draft_stale"] = bool(case["draft"]); case["hwpx_stale"] = True; case.bump(); st.rerun()
            tc2.caption(f"'{tone['doc_label']}' 양식" + ("" if tone.get("show_confirm", True) else " · 【확인】 줄 없음") + (f" · 문체: {tone['style'][:70]}…" if tone.get("style") else " · 문체 지침 없음") + f" · 생성 방식: {case.get('draft_how', '')}")
            if not locked and case.get("draft_stale") and tc3.button("초안 다시 쓰기", key=f"rv_redraft2_{rev}", type="primary", icon=":material/edit_note:"):
                run_ai("초안 다시 쓰는 중", lambda: (agent.step_draft(case), agent.step_hwpx(case, tpl)), "약 10~15초", "초안 다시 쓰는 중… (규칙 기반)"); st.rerun()
            st.caption("고치면 'HWPX 다시 만들기'로 반영됩니다(확정할 때는 자동으로 다시 만듭니다).")
            def field(label, key, widget, **kw):
                new = widget(label, d.get(key, "") or "", key=f"rv_{key}_{rev}", disabled=locked, **kw)
                if new != (d.get(key, "") or ""): d[key] = new; case["hwpx_stale"] = True
            field("제목", "제목", st.text_input); field("본문", "본문", st.text_area, height=240)
            c1, c2 = st.columns(2)
            with c1: field("차이 사유", "차이사유", st.text_area, height=110)
            with c2: field("산출 근거", "산출근거", st.text_area, height=110)
            cov = case.get("coverage")
            if cov is not None and len(cov):
                n_ok = int((cov["판정"] == "충족").sum())
                (st.success if n_ok == len(cov) else st.warning)(f"요구 항목 충족 {n_ok}/{len(cov)}" + ("" if n_ok == len(cov) else " — 나머지는 [확인 필요]"))
                with st.expander("항목별 판정"): st.dataframe(cov, width="stretch", hide_index=True)
            hits = [h for k in ("제목", "본문", "차이사유", "산출근거") for h in pii.scan_text(str(d.get(k, "")), f"초안 {k}")]
            if hits: st.error(f"초안에 개인정보로 보이는 패턴 {len(hits)}건 — 승인 전 삭제·가명 처리"); st.dataframe(pd.DataFrame(hits), width="stretch", hide_index=True)
            if case["draft"] and st.toggle("문서 미리보기 — 한글을 열지 않고 지금 내용으로 답변자료 모양을 봅니다(치수는 근사치)", key=f"rv_preview_{rev}"):
                _, items_ = agent._req_items(case)
                model = hwpx_build.build_model(req, items_, v if v is not None else None, d, dict(case["reasons"]), m, dept_head=case.get("dept_head", ""), phone=case.get("phone", ""),
                                               org=case.get("org") or "지역교육협력부", doc_label=tone.get("doc_label") or "답변자료", show_confirm=bool(tone.get("show_confirm", True)))
                st.html(f'<div style="max-height:600px;overflow:auto;padding:6px 2px">{hwpx_build.preview_html(model)}</div>')
        with st.expander("④ 예상 질문" + (f" — {len(case['foresee'][0])}건" if case.get("foresee") else ""), expanded=bool(case.get("foresee"))):
            if st.button("예상 질문 보기", key=f"rv_foresee_{rev}"):
                run_ai("예상 후속 질문 뽑는 중", lambda: agent.step_foresee(case), "보통 20~30초", "예측 중… (규칙 기반)"); st.rerun()
            if case.get("foresee"):
                qs, how = case["foresee"]; st.caption(f"생성 방식: {how}")
                st.dataframe(pd.DataFrame(qs)[["가능성", "질문", "근거", "준비할 자료"]], width="stretch", hide_index=True)
        st.divider()
        if locked: _send_card(case, req); return
        checks = _approve_checks(case, req, case["draft"] or {})
        b1, b2, b3, b4 = st.columns([1.6, 1.5, 1.7, 1.6])
        with b1.popover("승인하고 확정", icon=":material/task_alt:", disabled=not n_vals, width="stretch", type="primary"):     # help 말풍선은 열린 팝오버를 가려 넣지 않는다
            st.markdown("**확정 전 확인**"); st.caption("이번 수치를 확정 제출본으로, 사유와 초안을 승인 기록으로 저장합니다. 되돌리려면 기록에서 제출본을 지웁니다.")
            for tone, t_ in checks: {"bad": st.error, "warn": st.warning, "info": st.info}[tone](t_)
            if not checks: st.success("걸리는 것이 없습니다.")
            st.caption(f"확정하면 이번 수치 {n_vals}건을 확정 제출본으로, 차이 사유 {n_reason}건을 기록으로, 초안을 승인 상태로 저장하고 한글 파일을 보관합니다.")
            ok = st.checkbox("개인정보로 보이는 부분을 확인했고 그대로 확정합니다", key=f"rv_pii_ok_{rev}") if any(t_ == "bad" for t_, _ in checks) else True
            if st.button("확정", type="primary", key="rv_approve_go", icon=":material/task_alt:", disabled=not ok):
                with st.spinner("확정하는 중…"): _approve_now(case, tpl)
                st.toast("승인·확정했습니다"); st.rerun()
        stale = bool(case.get("hwpx_stale") or case.get("draft_stale") or case["hwpx"] is None)
        if case.get("draft") and b2.button("HWPX 다시 만들기", key=f"rv_rehwpx_{rev}", type="primary" if stale and case.get("draft") else "secondary", icon=":material/refresh:", help="초안·사유를 고쳤으면 눌러 반영"):
            agent.step_hwpx(case, tpl); st.rerun()
        if case["hwpx"]: b3.download_button("회신 HWPX 받기" + (" (고친 내용 반영 전)" if stale else ""), case["hwpx"], file_name=case["hwpx_name"], key=f"rv_dl_{rev}", icon=":material/download:")
        if b4.button("단계 화면에서 자세히 고치기", key="rv_detail", type="tertiary"):
            st.session_state["target_request"] = case["request_id"]; go("새 요구서 처리", 2)

def _todo_strip():
    """홈 상단 '오늘 할 일': 기한 3일 이내(접수·처리 중) · 확정(미발송) · 검토 대기 · 지난달 기준 지표 데이터. 기록이 하나도 없으면 그리지 않는다."""
    ov = _q("request_overview"); review = len(_q("list_drafts", "review_requested")); today = dt.date.today()
    last_me = (today.replace(day=1) - dt.timedelta(days=1)).isoformat()
    n_last = len({c["indicator"] for c in _q("data_coverage") if c["base_date"] == last_me})
    if not ov and not review and not n_last: return
    soon = unsent = 0
    for r in ov:
        stt = r.get("status") or "접수"
        if stt == "확정": unsent += 1
        elif stt in ("접수", "처리 중"):
            try: soon += 0 <= (dt.date.fromisoformat(str(r["due_date"])) - today).days <= 3
            except ValueError: pass
    c1, c2, c3, c4 = st.columns(4)
    ui.kpi(c1, soon, "기한 3일 이내 · 진행 중", "warn" if soon else ""); ui.kpi(c2, unsent, "확정했지만 미발송", "warn" if unsent else "")
    ui.kpi(c3, review, "팀장 검토 대기", "ok" if review else ""); ui.kpi(c4, n_last, f"지난달({last_me[:7]}) 기준 지표", "" if n_last else "bad")
    b1, b2, b3, b4 = st.columns(4)
    if b1.button("현황 보기", key="todo_status", type="tertiary", icon=":material/monitoring:"): go("현황")
    if b2.button("미발송 건 보기", key="todo_unsent", type="tertiary", icon=":material/send:"): go("현황")
    if b3.button("검토·승인 열기", key="todo_review", type="tertiary", icon=":material/task_alt:"): go("검토·승인")
    if b4.button("지표 데이터 넣기" if not n_last else "지표 데이터 보기", key="todo_data", type="tertiary", icon=":material/database:"): go("지표 데이터")

def _requester_input(label: str, value: str, key: str) -> str:
    """요청 주체 입력: 사전·기록의 이름을 고르거나 새로 입력(자동완성)."""
    names = _q("requester_names"); opts = list(dict.fromkeys(([value] if value else []) + names))
    if not opts: return (st.text_input(label, value or "", key=key) or "").strip()
    return (st.selectbox(label, opts, index=0 if value else None, accept_new_options=True, key=key, placeholder="고르거나 새로 입력") or "").strip()

def _head_form(case: agent.Case, tpl: bytes | None, hist: list):
    """칩 '요청 주체·기한 적기': 아는 칸만 적어 반영하면 머리 정보만 고치고 초안·한글 파일을 다시 만든다(모델 호출 없음)."""
    req = db.get_request(case["request_id"]) if case["request_id"] else (case["extracted"] or {})
    with st.container(border=True):
        st.markdown("**요청 주체·접수일·기한** — 아는 것만 적고 반영을 누르세요. 항목과 원문은 그대로 둡니다.")
        c1, c2, c3, c4 = st.columns(4)
        r_ = _requester_input("요청 주체", (req or {}).get("requester") or "", "chip_req")
        rd = c2.text_input("접수일", (req or {}).get("received_date") or "", key="chip_rd", placeholder="YYYY-MM-DD")
        dd = c3.text_input("제출 기한", (req or {}).get("due_date") or "", key="chip_dd", placeholder="YYYY-MM-DD")
        tt = c4.text_input("제목", (req or {}).get("title") or "", key="chip_tt")
        a, b = st.columns([1, 6])
        if a.button("반영", type="primary", key="chip_head_go"):
            r = agent.set_header(case, r_ or None, rd or None, dd or None, tt or None); st.session_state["chip_head"] = False
            if r["changed"]:
                run_ai("초안·한글 파일 다시 만드는 중", lambda: (agent.step_draft(case), agent.step_hwpx(case, tpl)), "약 10~15초", "다시 만드는 중… (규칙 기반)")
                hist.append({"role": "assistant", "text": "요청 정보를 반영했습니다: " + ", ".join(f"{k} {v}" for k, v in r["changed"].items()) + ". 초안과 한글 파일을 다시 만들었습니다."})
                st.session_state["chat_carry"] = "담당자가 화면에서 요청 주체·기한을 고쳤고 초안·한글 파일은 다시 만들어졌습니다."
            st.rerun()
        if b.button("닫기", type="tertiary", key="chip_head_x"): st.session_state["chip_head"] = False; st.rerun()

def _chips(case: agent.Case, hist: list, tpl: bytes | None, job) -> None:
    """다음 행동 칩: 상황에 맞는 2~4개. 누르면 바로 실행(결정적 단계)하거나 대화 문장으로 보낸다. 모델 호출 없이 코드로 고른다."""
    if job: return
    if st.session_state.get("chip_head"): _head_form(case, tpl, hist); return
    req = (db.get_request(case["request_id"]) if case["request_id"] else (case["extracted"] or {})) or {}
    opts: list[tuple[str, tuple]] = []
    if case.get("approved"): opts += [("새 요구 시작", ("fn", _new_case)), ("현황 보기", ("go", "현황"))]
    elif case["draft"]:
        if not req.get("requester") or not req.get("due_date"): opts.append(("요청 주체·기한 적기", ("head", None)))
        if case.get("draft_stale"): opts.append(("사유 반영해 초안 다시 쓰기", ("redraft", None)))
        if not case.get("foresee"): opts.append(("예상 질문 보기", ("foresee", None)))
        if case["compare"] is not None and int((case["compare"]["판정"] == "차이").sum()) and HAS_API: opts.append(("차이 난 행 설명", ("chat", "차이 난 센터마다 지난번 값·이번 값·채워진 사유를 짧게 정리해 줘")))
        if not case["request_id"]: opts.append(("기록에 등록", ("register", None)))
    elif case["request_text"]: opts.append(("요구하는 것들 전부 작성", ("chat", "이 요구서에서 요구하는 것들 작성해 줘")))
    else:
        opts.append(("지표 데이터에 뭐가 있나", ("chat", "지표 데이터에 어떤 지표가 어느 기간까지 들어 있어?")))
        if _q("list_requests"): opts.append(("최근 요구서 보기", ("go", "기록 조회")))
        if _q("list_ref_docs"): opts.append(("운영 시간 찾기", ("chat", "센터 운영 시간은 어떻게 되지?")))
    if not opts: return
    key = f"chips_{case.get('rev', 0)}_{len(hist)}_{st.session_state.get('chips_n', 0)}"
    pick = st.pills("다음 행동", [l for l, _ in opts], key=key, label_visibility="collapsed")
    if not pick: return
    st.session_state["chips_n"] = st.session_state.get("chips_n", 0) + 1                   # 다음 실행에서 칩 선택을 비운다
    kind, arg = dict(opts)[pick]
    if kind == "chat": st.session_state["chat_pending"] = (arg, []); st.rerun()
    elif kind == "go": go(arg)
    elif kind == "fn": arg(); st.rerun()
    elif kind == "head": st.session_state["chip_head"] = True; st.rerun()
    elif kind == "redraft": run_ai("초안 다시 쓰는 중", lambda: (agent.step_draft(case), agent.step_hwpx(case, tpl)), "약 10~15초", "초안 다시 쓰는 중… (규칙 기반)"); st.rerun()
    elif kind == "foresee": run_ai("예상 후속 질문 뽑는 중", lambda: agent.step_foresee(case), "보통 20~30초", "예측 중… (규칙 기반)"); st.rerun()
    elif kind == "register":
        case["no_register"] = False; r = agent.step_register(case)
        hist.append({"role": "assistant", "text": f"요구서 #{r['request_id']}로 기록에 등록했습니다."}); st.session_state["chat_carry"] = f"담당자가 화면에서 요구서를 기록에 등록했습니다(#{r['request_id']})."; st.rerun()

def page_chat():
    ui.page_title(f"{USER}님, 무엇을 할까요", "", "홈")
    case: agent.Case = st.session_state.setdefault("case", agent.Case())
    hist: list = st.session_state.setdefault("chat", []); st.session_state.setdefault("chat_msgs", [])
    tpl = _template_bytes(); case.update(dept_head=ORG["dept_head"], phone=ORG["dept_phone"], org=ORG["org_name"], company=ORG["company"])
    job = st.session_state.get("chat_job")                      # 처리 중이던 작업(화면이 다시 실행돼도 결과를 잃지 않게 세션에 둔다)
    if not HAS_API and st.session_state.get("ai_banner_seen"):
        c1, c2 = st.columns([6, 1], vertical_alignment="center")
        with c1: ui.status_line(False, "AI 연결 안 됨 · 규칙 기반으로 동작합니다(요구서 읽기·초안 정확도 낮음)")
        if c2.button("설정으로", key="home_settings", type="tertiary"): go("설정")
    _todo_strip()
    if not hist:
        with st.container(border=True):
            st.markdown("**요구서를 붙이고 말하면 됩니다.** 예: \"이 요구서에서 요구하는 것들 작성해 줘\"  \n요구 항목을 읽고 → 가진 자료(지표 데이터·과거 제출값)에서 기준일을 정해 값을 모으고 → 과거 제출값과 맞춰 보고 → 차이 사유 후보를 채우고 → 회신 초안과 HWPX를 만듭니다. 사람은 아래 검수 화면에서 확인하고 승인합니다.")
            st.caption("집계 엑셀이나 통합 대시보드 저장 파일(.html)을 함께 붙이면 지표 데이터에 넣고 씁니다. 과거 기록은 그냥 물어보면 됩니다: \"감사실에 등원율 언제 어떤 값으로 냈지?\" 사업 자체 질문(\"운영 시간은?\")은 '참고 문서'에 지침·매뉴얼을 올려 두면 그 문구로 답합니다." + ("" if HAS_API else "  \nAI 연결이 없어 지금은 정해진 순서로 처리하고 질문은 키워드 검색으로 답합니다."))
            if DEMO:
                c1, c2 = st.columns([1, 3])
                if c1.button("시연: 요구서 붙여 시작", key="chat_demo", icon=":material/play_arrow:"):
                    p = SAMPLE / "새요구서_의원실_2026-09-15.txt"
                    st.session_state["chat_pending"] = ("이 요구서에서 요구하는 것들 작성해 줘", [(p.name, p.read_bytes())]); st.rerun()
                c2.caption("가상의 9/15 의원실 요구서를 붙이고 작성을 요청합니다. 먼저 설정 → 시연 데이터 넣기, 지표 데이터에 샘플 적재를 해 두면 결과가 풍부합니다.")
    else:
        label = _case_label(case)
        if label:
            c1, c2 = st.columns([5, 1.4], vertical_alignment="center")
            c1.caption(f"지금 작업: {label}")
            if c2.button("새 요구 시작", key="chat_new", icon=":material/add:", disabled=bool(job), help="지금 작업을 접고 빈 작업으로 시작합니다. 확정한 기록은 남고, 확정하지 않은 초안·수치는 사라집니다."):
                _new_case(); st.rerun()
    for m in hist:
        with st.chat_message(m["role"], avatar=":material/person:" if m["role"] == "user" else ":material/smart_toy:"):
            st.markdown(ui.safe_md(m["text"]))
            if m.get("files"): st.caption("첨부: " + ", ".join(m["files"]))
    if case["draft"]: _review_panel(case, tpl)
    _chips(case, hist, tpl, job)
    prompt = st.chat_input("처리 중입니다… 끝나면 다시 입력할 수 있습니다" if job else "요구서를 붙이고 지시하거나, 기록에 대해 물어보세요", accept_file="multiple",
                           file_type=["hwp", "hwpx", "pdf", "docx", "txt", "xlsx", "xls", "xlsm", "csv", "html", "htm"], key="chat_in", disabled=bool(job))
    pending = st.session_state.pop("chat_pending", None)
    if job is None:
        if prompt is None and pending is None: return
        if pending: text, files = pending
        else: text, files = (prompt.text or "").strip(), [(f.name, f.getvalue()) for f in (prompt.files or [])]
        if not text and not files: return
        notes = _attach(case, files); case = st.session_state["case"]
        hist.append({"role": "user", "text": text or "(파일 첨부)", "files": [n for n, _ in files]})
        if not text: text = "이 요구서에서 요구하는 것들 작성해 줘" if any(n.lower().endswith(REQ_EXT) for n, _ in files) else "붙인 파일을 확인해 줘"   # 말 없이 집계 파일만 붙이면 확인만 한다
        msgs = list(st.session_state.get("chat_msgs") or []); carry = st.session_state.pop("chat_carry", None)
        if len(msgs) > agent.HISTORY_MAX_BLOCKS: carry = agent.carry_note(case, hist, carry); msgs = []     # 긴 대화는 요약 한 줄로 새로 시작(중간을 자르지 않는다)
        job = {"text": text, "notes": notes, "msgs": msgs, "carry": carry}; st.session_state["chat_job"] = job
    case = st.session_state["case"]
    try:
        if HAS_API:
            res = run_ai("처리 중", lambda: agent.chat_turn(case, job["msgs"], job["text"], tpl, notes=job["notes"], carry=job.get("carry")), "요구서 한 건 전체 처리는 보통 30초~2분. 도구 호출이 아래에 찍힙니다", job=job)
            st.session_state["chat_msgs"] = res["messages"]; reply = res["text"] or "(답이 비어 있습니다)"
        else:
            reply = run_ai("처리 중", lambda: _rule_reply(case, job["text"], tpl), "", "처리 중… (규칙 기반)")
    except Exception as e:
        reply = f"처리 중 오류가 났습니다: {llm.explain_error(e) or e}"
    st.session_state.pop("chat_job", None)
    notes = job["notes"]
    hist.append({"role": "assistant", "text": "\n\n".join(notes + [reply]) if notes else reply})
    st.session_state["chat"] = hist[-40:]; st.rerun()

# ================= 지표 데이터 =================
def page_data():
    ui.page_title("지표 데이터", "자주 요구되는 지표의 집계값을 요구서와 상관없이 미리 넣어 둡니다. 새 요구서가 오면 같은 지표·기준일의 값이 2단계에서 자동으로 채워집니다.", "기록")
    cov = db.data_coverage()
    c1, c2 = st.columns([1.1, 1])
    with c1:
        st.markdown("### 넣기")
        st.caption("월별 집계 엑셀, 실적표, 집계 시스템에서 내려받은 표를 그대로 올리면 됩니다. 같은 지표·센터·기준일이 이미 있으면 새 값으로 바뀝니다.")
        vdf, vname = value_sources("data_in", "집계값", demo_files=sorted(SAMPLE.glob("등원율_*.xlsx")) + sorted(SAMPLE.glob("실적표_*.xlsx")))
        note = st.text_input("출처 메모 (선택)", placeholder="예: 학사시스템 월 집계, 2026-07-03 추출", key="data_note")
        if vdf is not None:
            st.dataframe(vdf[VAL_COLS].rename(columns=VAL_KO), height=200, width="stretch", hide_index=True)
            inds = sorted(vdf["indicator"].unique()); dates = sorted(vdf["base_date"].unique())
            st.caption(f"{len(vdf)}건 · 지표 {', '.join(inds[:6])} · 기준일 {', '.join(dates[:4])}" + (" …" if len(dates) > 4 else ""))
            if st.button("지표 데이터에 저장", type="primary", key="data_save", icon=":material/database:"):
                bid, n = db.add_data_batch(vdf.to_dict("records"), USER, vname, note.strip())
                st.session_state["data_last"] = (bid, n); st.rerun()
        if st.session_state.get("data_last"):
            bid, n = st.session_state["data_last"]
            a, b = st.columns([4, 1.4])
            a.success(f"저장했습니다. 묶음 #{bid}, {n}건. 이제 새 요구서 2단계에서 같은 지표·기준일은 자동으로 채워집니다.")
            if b.button("되돌리기", key="data_undo", icon=":material/undo:"):
                db.delete_data_batch(bid); st.session_state.pop("data_last"); st.rerun()
    with c2:
        st.markdown("### 들어 있는 것")
        if not cov:
            st.info("아직 없습니다. 왼쪽에서 집계 파일을 올려 저장하면 지표 × 월 표가 여기 나타납니다.")
        else:
            last_me = (dt.date.today().replace(day=1) - dt.timedelta(days=1)).isoformat()
            if not any(c["base_date"] == last_me for c in cov): st.warning(f"지난달({last_me[:7]}) 기준 자료가 아직 없습니다. 월 집계가 나오면 올려 두세요.", icon=":material/event_busy:")
            mat = pd.DataFrame(_q("data_matrix")); mat["월"] = mat["base_date"].map(lambda d: d[:7] if hwpx_build._is_month_end(str(d)) else str(d))
            piv = mat.pivot_table(index="indicator", columns="월", values="n", aggfunc="max").fillna(0).astype(int); piv = piv[sorted(piv.columns)]; piv.index.name = "지표"
            mx = int(piv.values.max()) if piv.size else 1
            def _heat(v):
                if not v: return "background-color: #FDE8E8; color: #B42318"
                return f"background-color: rgba(37,110,244,{0.12 + 0.55 * v / max(mx, 1):.2f})"
            styled = piv.style.map(_heat) if hasattr(piv.style, "map") else piv.style.applymap(_heat)
            st.dataframe(styled, width="stretch", height=min(60 + 35 * len(piv), 460))
            st.caption(f"칸의 숫자는 센터 수(진할수록 많음, 빨강은 없음) · 지표 {len(piv)}개 · 기준일 {len({c['base_date'] for c in cov})}개 · 값 {db.data_count()}건")
            if _q("center_count"):
                ind_pick = st.selectbox("명부 대비 빠진 센터 보기", list(piv.index), key="miss_ind")
                last_bd = max(c["base_date"] for c in cov if c["indicator"] == ind_pick)
                miss = db.missing_centers(ind_pick, last_bd)
                (st.caption if not miss else st.warning)(f"{ind_pick} · {last_bd} 기준: 명부의 센터 중 값이 없는 곳 {len(miss)}곳" + (" — " + ", ".join(miss[:15]) + (" …" if len(miss) > 15 else "") if miss else ""))
        with st.expander("적재 묶음 (지우기)"):
            for b in db.list_data_batches():
                a, d = st.columns([5, 1])
                a.caption(f"#{b['id']} · {b['loaded_at'][:16]} · {b['loaded_by']} · {b['source']} · {b['n_rows']}건(남은 {b['n_live']}건)" + (f" · {b['note']}" if b['note'] else ""))
                with d.popover("지우기", icon=":material/delete:"):
                    st.caption(f"묶음 #{b['id']}의 남은 {b['n_live']}건을 지웁니다. 이 묶음이 덮어썼던 이전 값은 되살아납니다.")
                    if st.button("정말 지우기", key=f"data_del_{b['id']}", type="primary"): db.delete_data_batch(b["id"]); st.rerun()
        st.caption("[계획] 사내 집계 시스템·DB와 직접 연결(읽기 전용 조회)하면 이 화면에 올리는 일도 없어집니다. 연결 방식은 전산 부서와 협의 필요 [확인 필요].")
    st.divider()
    st.markdown("### 통합 대시보드에서 한 번에 가져오기")
    st.caption("자기주도학습센터 현황 대시보드를 브라우저에서 **페이지 저장(Ctrl+S, '웹페이지, 전체')** 한 .html 파일을 올리면, 안에 들어 있는 월간 출결(등원율·시간달성율·등원일수…)·진단검사·관리인원·상담횟수·이용현황과 센터 명부(지역·유형·개소일·정원·주말 운영)를 지표 데이터로 바로 넣습니다. 계정·연락처 등 개인정보 영역은 읽지 않고 파일도 저장하지 않습니다.")
    st.warning("저장 파일에는 대시보드 '계정정보' 영역의 아이디·비밀번호가 평문으로 들어 있을 수 있습니다. 파일 자체를 메일·메신저로 공유하지 말고, 쓰고 난 사본은 지우세요.", icon=":material/lock:")
    dcol1, dcol2 = st.columns([1.2, 1])
    with dcol1:
        up = st.file_uploader("대시보드 저장 파일(.html)", type=["html", "htm"], key="dash_up")
        inc = st.checkbox("부분 집계 중인 달(예: 이번 달 7일까지)도 넣기", value=False, key="dash_partial", help="대시보드가 '부분 집계'로 표시한 달은 기본으로 제외합니다. 넣으면 최신 기준일로 제안될 수 있습니다.")
        if up is not None:
            try:
                S = dashboard_import.summarize(dashboard_import.parse(up.getvalue()), inc)
                st.session_state["dash_preview"] = (up.name, S)
            except Exception as e:
                st.error(f"읽지 못했습니다: {e}"); st.session_state.pop("dash_preview", None)
        pv = st.session_state.get("dash_preview")
        if pv and up is not None:
            name, S = pv
            st.markdown("\n".join(f"- {n}" for n in S["notes"]) + f"\n- 센터 명부 {len(S['centers'])}개소(2025 운영 {sum(1 for c in S['centers'] if c['year25'])}·2026 선정 {sum(1 for c in S['centers'] if c['year26'])})")
            if S["errors"]: st.warning("읽지 못한 자료: " + ", ".join(f"{k}({v})" for k, v in S["errors"].items()))
            n_rep = db.data_replaced_count(S["rows"]) if S["rows"] else 0
            if n_rep: st.caption(f"이미 있는 값 {n_rep:,}건이 새 값으로 바뀝니다(묶음을 지우면 되돌아갑니다).")
            st.dataframe(pd.DataFrame([{"지표": k, "건수": v} for k, v in sorted(S["by_indicator"].items(), key=lambda x: -x[1])]), width="stretch", hide_index=True, height=min(60 + 35 * len(S["by_indicator"]), 300))
            if st.button("지표 데이터·센터 명부에 저장", type="primary", key="dash_save", icon=":material/database:"):
                note, bid = _import_dashboard(up.getvalue(), name, inc, S); st.session_state["data_last"] = (bid, len(S["rows"])); st.session_state.pop("dash_preview", None); st.toast("가져왔습니다"); st.rerun()
    with dcol2:
        nC = db.center_count()
        st.markdown(f"**센터 명부: {nC}개소**" if nC else "**센터 명부: 없음** — 왼쪽에서 대시보드 저장 파일을 가져오면 채워집니다.")
        if nC:
            q = st.text_input("명부 찾기(이름·지역·유형)", key="center_q", placeholder="예: 경남, 학교 밖, 영월")
            rows = db.list_centers(q.strip() or None, limit=300)
            st.dataframe(pd.DataFrame(rows)[["center_id", "name", "edu", "region", "facility", "type", "size", "open_date", "capacity", "weekend", "year25", "year26", "status"]].rename(
                columns={"center_id": "ID", "name": "센터", "edu": "교육청", "region": "지역", "facility": "시설", "type": "유형", "size": "규모", "open_date": "개소일", "capacity": "정원", "weekend": "주말 운영", "year25": "25운영", "year26": "26선정", "status": "상태"}),
                width="stretch", hide_index=True, height=min(60 + 35 * len(rows), 380))
            st.caption("센터장·담당자 이름과 연락처는 가져오지 않습니다. 대화에서 \"영월 센터 개소일이 언제지?\"처럼 물으면 이 명부로 답합니다.")

# ================= 참고 문서 =================
def page_refdocs():
    ui.page_title("참고 문서", "사업 지침·운영 매뉴얼·FAQ를 올려 두면, 대화나 '기록에 묻기'에서 사업 자체에 대한 질문에 그 문구를 근거로(문서·쪽 표시) 답합니다.", "기록")
    c1, c2 = st.columns([1.1, 1])
    with c1:
        st.markdown("### 올리기")
        ups = st.file_uploader("지침·매뉴얼 파일(pdf·hwp·hwpx·docx·txt, 여러 개 가능)", type=["pdf", "hwp", "hwpx", "docx", "txt"], accept_multiple_files=True, key="ref_up")
        title = st.text_input("문서 제목(선택, 파일 하나일 때) — 비우면 파일 이름", key="ref_title", placeholder="예: 관리·운영지침 v2.0(2026-09-17)")
        note = st.text_input("메모(선택)", key="ref_note", placeholder="예: 2026.9.17 개정판")
        if ups and st.button("등록", type="primary", key="ref_save", icon=":material/menu_book:"):
            done, errs = [], []
            for f in ups:
                try: done.append(refdocs.ingest(f.name, f.getvalue(), title if len(ups) == 1 else None, USER, note.strip()))
                except Exception as e: errs.append(f"{f.name}: {e}")
            if done: st.success("등록했습니다: " + " / ".join(f"「{d['title']}」 {d['pages']}쪽·조각 {d['chunks']}개" for d in done) + ". 같은 제목이 있으면 새 문서로 바뀝니다.")
            for e in errs: st.error(e)
        st.markdown("### 찾아보기(검색 확인)")
        q = st.text_input("질문 또는 핵심어", key="ref_q", placeholder="예: 운영 시간, 이용 대상, 코디네이터 근무")
        if q.strip():
            hits = refdocs.search(q, 5)
            if not hits: st.info("맞는 문구가 없습니다. 다른 낱말로 찾아보세요.")
            for h in hits:
                with st.container(border=True):
                    st.caption(f"「{h['doc']}」 {h['page']}쪽 · 점수 {h['score']}"); st.write(h["text"])
    with c2:
        st.markdown("### 등록된 문서")
        docs = _q("list_ref_docs")
        if not docs: st.info("아직 없습니다. 지침·운영 매뉴얼 PDF를 올려 두세요. 문서 자체는 이 서버의 기록 DB에만 저장됩니다.")
        for d in docs:
            a, b = st.columns([5, 1])
            a.markdown(f"**{d['title']}**  \n{d['file_name']} · {d['pages']}쪽 · 조각 {d['n_chunks']}개 · {d['uploaded_at'][:10]} · {d['uploaded_by']}" + (f" · {d['note']}" if d['note'] else ""))
            with b.popover("삭제", icon=":material/delete:"):
                st.caption(f"「{d['title']}」 조각 {d['n_chunks']}개를 지웁니다. 되돌릴 수 없습니다(다시 올리면 됩니다).")
                if st.button("정말 삭제", key=f"ref_del_{d['id']}", type="primary"): db.delete_ref_doc(d["id"]); st.rerun()
        st.caption("검색은 서버 안에서 키워드로 합니다(외부 전송 없음). AI가 연결돼 있으면 모델이 이 검색 도구를 써서 찾은 문구만 근거로 답하고 쪽을 밝힙니다. 스캔 이미지 PDF는 글자 추출이 안 됩니다.")

# ================= 과거 답변 등록 =================
def page_register():
    ui.page_title("과거 답변 등록", "예전 요구서와 그때 제출한 값을 넣어 두면, 다음에 같은 수치를 물을 때 '언제 누구에게 얼마로 답했는지'가 자동으로 붙습니다.", "기록")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("### 1. 그때 받은 요구서")
        req_text, req_name = request_input("reg", "요구서_*.txt")
        if st.button("요구 항목 읽기", type="primary", disabled=not req_text):
            res, how = analyze_with_status(req_text)
            st.session_state.update(reg_extract=res, reg_how=how, reg_text=req_text, reg_name=req_name)
    with col2:
        st.markdown("### 2. 그때 제출한 값")
        st.caption("집계 엑셀, 그때 보낸 회신 공문(hwp·hwpx·docx·pdf), 직접 입력 중 무엇이든 됩니다. 파일 종류를 가리지 않고 여러 개를 한 번에 올리면 전부 합쳐 저장됩니다.")
        vdf, vname = value_sources("reg_val", "그때 제출한 값", demo_files=sorted(SAMPLE.glob("등원율_*.xlsx")) + sorted(SAMPLE.glob("실적표_*.xlsx")))
        if vdf is not None:
            st.dataframe(vdf[VAL_COLS].rename(columns=VAL_KO), height=200, width="stretch")
            srcs = list(dict.fromkeys(f"{f}{' / ' + str(sh) if sh else ''}" for f, sh in zip(vdf["source_file"], vdf["source_sheet"], strict=True)))
            st.caption(f"저장될 값 {len(vdf)}건 · 출처 {len(srcs)}개: {', '.join(srcs[:4])}" + (" …" if len(srcs) > 4 else ""))
    if "reg_extract" in st.session_state:
        res = st.session_state["reg_extract"]
        st.markdown("### 3. 확인하고 저장")
        st.caption(f"읽은 방식: {st.session_state['reg_how']} · 틀린 곳은 바로 고치면 됩니다.")
        c1, c2, c3, c4 = st.columns(4)
        with c1: requester = _requester_input("요청 주체", res.get("requester") or "", "reg_requester")
        received = c2.text_input("접수일", res.get("received_date") or "")
        due = c3.text_input("제출기한", res.get("due_date") or "")
        title = c4.text_input("제목", res.get("title") or "")
        items_df = pd.DataFrame(res.get("items") or [], columns=extract.ITEM_FIELDS)
        items_df = st.data_editor(items_df, num_rows="dynamic", width="stretch",
                                  column_config={"item_text": "항목 원문", "indicator": st.column_config.SelectboxColumn("지표명(정규화)", options=list(normalize.CANON), required=False),
                                                 "base_date": "기준일", "period": "기간", "unit": "단위"})
        if res.get("_error"): st.warning(f"AI 호출 오류로 규칙 기반 결과입니다: {res['_error']}")
        submitted_date = st.text_input("그때 제출한 날짜", due or "")
        if vdf is None: st.info("제출값 없이 요구서만 저장할 수도 있습니다(나중에 대조 기준으로는 쓰이지 않음).")
        if st.button("기록에 저장", type="primary"):
            rid = db.add_request(requester, received, due, title, st.session_state["reg_text"], st.session_state["reg_name"], items_df.to_dict("records"))
            vals = vdf.to_dict("records") if vdf is not None else []
            sid = db.add_submission(rid, submitted_date, USER, vname, "confirmed", "과거 제출본 등록", vals)
            st.session_state["reg_last"] = (rid, sid, len(vals))
            del st.session_state["reg_extract"]; st.rerun()
    if st.session_state.get("reg_last"):
        rid, sid, n = st.session_state["reg_last"]
        c1, c2 = st.columns([4, 1.4])
        c1.success(f"저장했습니다. 요구서 #{rid}, 제출본 #{sid}, 값 {n}건. 다음에 같은 지표·기준일을 물으면 자동으로 찾아 줍니다.")
        if c2.button("잘못 넣었어요 — 되돌리기", key="reg_undo", icon=":material/undo:", help="방금 저장한 요구서·값을 지웁니다. 나중에 고치려면 기록 조회에서 수정·삭제할 수 있습니다."):
            db.delete_request(rid); st.session_state.pop("reg_last"); st.toast(f"요구서 #{rid} 삭제됨"); st.rerun()

# ================= 새 요구서 처리 (1→2→3) =================
def page_process():
    step = st.session_state.setdefault("step", 1)
    done = 2 if st.session_state.get("last_submission") else (1 if st.session_state.get("new_registered") else 0)
    ui.page_title("새 요구서 처리", "", "업무")
    c1, c2 = st.columns([6, 1.2], vertical_alignment="center")
    c1.caption("홈 검수 화면의 '단계 화면에서 자세히 고치기'로 들어오는 세부 화면입니다. 보통은 홈 대화에서 한 번에 처리합니다.")
    if c2.button("홈으로", key="proc_home", type="tertiary", icon=":material/home:"): go("홈")
    ui.stepper([n for _, n in STEPS], step, done)
    {1: step_read, 2: step_compare, 3: step_draft}[step]()
    st.divider()
    b1, b2, b3 = st.columns([1, 4, 1])
    if step > 1 and b1.button("이전 단계", type="tertiary", icon=":material/arrow_back:"): go("새 요구서 처리", step - 1)
    if step < 3 and b3.button("다음 단계", type="tertiary", icon=":material/arrow_forward:", icon_position="right"): go("새 요구서 처리", step + 1)

def step_read():
    st.markdown("### 요구서 읽기")
    st.caption("요구서를 올리면 항목·지표·기준일을 뽑고, 같은 수치를 전에 낸 적이 있는지 기록에서 찾습니다.")
    text, name = request_input("new", "새요구서_*.txt")
    if st.button("요구 항목 읽기", type="primary", disabled=not text):
        res, how = analyze_with_status(text)
        st.session_state.update(new_req=res, new_how=how, new_text=text, new_name=name)
    if "new_req" not in st.session_state: return
    res = st.session_state["new_req"]
    st.caption(f"읽은 방식: {st.session_state['new_how']}" + (" · 요구서의 전화·이메일 등은 가린 채 전송됨" if res.get("_redacted") else ""))
    if res.get("_error"): st.warning(f"AI 호출 오류로 규칙 기반 결과입니다: {res['_error']}")
    with st.container(border=True):
        st.markdown(f"**{res.get('title') or '(제목 없음)'}**  \n요청 주체 **{res.get('requester') or '-'}** · 접수 {res.get('received_date') or '-'} · 제출기한 **{res.get('due_date') or '-'}** · 요구 항목 {len(res.get('items') or [])}건")
    sim_req = search.similar_requests(st.session_state["new_text"])
    if sim_req and sim_req[0]["score"] > 0.2:
        st.info("비슷한 과거 요구서: " + " / ".join(f"#{r['id']} {r['received_date']} {r['requester']} — {r['title']} (유사도 {r['score']})" for r in sim_req if r["score"] > 0.2))
    st.markdown("#### 항목별로 전에 낸 적이 있는지")
    for it in res.get("items") or []:
        with st.container(border=True):
            mb = f" (인식 근거 '{it['matched_by']}')" if it.get("matched_by") else ""
            st.markdown(f"**{it['item_text']}**")
            st.caption(f"지표 {it.get('indicator') or '사전에 없음'}{mb} · 기준일 {it.get('base_date') or '없음'} · 기간 {it.get('period') or '-'} · 단위 {it.get('unit') or '-'}")
            sg = suggest.suggest(it)
            st.markdown(f"→ **{ui.esc(sg['판단'])}**  \n<span class='small-muted'>출처 {ui.esc(sg['데이터 출처'])} · 담당 {ui.esc(sg['담당'])} · 과거 제출 {ui.esc(sg['과거 제출'])}</span>", unsafe_allow_html=True)
            if it.get("indicator") and it.get("base_date") and (dv := db.data_values_for(it["indicator"], it["base_date"])):
                st.markdown(f"지표 데이터에 있음: **{len(dv)}건** · 적재 {dv[0]['loaded_at'][:10]} · {dv[0]['source']} → 2단계에서 자동으로 채워짐")
            if it.get("indicator") and it.get("base_date"):
                past = db.past_values_for(it["indicator"], it["base_date"])
                subs = {}
                for p_ in past: subs.setdefault((p_["submitted_date"], p_["requester"], p_["request_title"], p_["file_name"]), []).append(p_)
                for (sd, rq, tt, fn), rows in subs.items():
                    st.markdown(f"전에 낸 값: **{ui.esc(sd)}** · {ui.esc(rq)} · {ui.esc(tt)} · {ui.esc(fn)} · {len(rows)}건  \n<span class='small-muted'>근거: 정의 {ui.esc(rows[0]['definition'])} / 집계기간 {ui.esc(rows[0]['calc_period'])} / 추출시점 {ui.esc(rows[0]['extract_date'])} / 원자료 {ui.esc(rows[0]['source_version'])}</span>", unsafe_allow_html=True)
            sims = search.similar_items(it, top=3)
            if sims:
                with st.expander("비슷한 과거 항목"):
                    st.dataframe(pd.DataFrame(sims).rename(columns={"score": "유사도", "request_id": "요구번호", "requester": "요청 주체", "received_date": "접수일", "title": "제목", "item_text": "과거 항목", "indicator": "지표", "base_date": "기준일"}), width="stretch", height=140, hide_index=True)
    done = st.session_state.get("new_registered")
    already = done and done[0] == st.session_state["new_text"]
    if already:
        st.success(f"요구서 #{done[1]}로 등록돼 있습니다.")
        if st.button("2단계로: 수치 맞춰 보기 →", type="primary"): go("새 요구서 처리", 2)
    elif st.button("이 요구서를 등록하고 2단계로 →", type="primary"):
        rid = db.add_request(res.get("requester"), res.get("received_date"), res.get("due_date"), res.get("title"), st.session_state["new_text"], st.session_state["new_name"], res.get("items") or [])
        st.session_state["new_registered"] = (st.session_state["new_text"], rid); st.session_state["target_request"] = rid
        go("새 요구서 처리", 2)

def step_compare():
    st.markdown("### 수치 맞춰 보기")
    st.caption("이번에 낼 수치를 지난번 제출값과 맞춰 봅니다. 맞춰 보는 계산은 코드가 하고, 차이가 난 이유는 담당자가 적습니다.")
    reqs = db.list_requests(); subs = db.list_submissions()
    if not reqs:
        st.warning("등록된 요구서가 없습니다. 1단계에서 요구서를 등록하세요."); return
    tgt_default = next((i for i, r in enumerate(reqs) if r["id"] == st.session_state.get("target_request")), 0)
    target = st.selectbox("이번에 회신할 요구서", reqs, index=tgt_default, format_func=req_label)
    items = db.get_items(target["id"])
    # 1) 자료 계획: 항목의 지표·기준일·기간 표현과 '가진 자료'(지표 데이터 → 과거 제출값)의 범위를 맞춰 무엇을 어느 기준일로 낼지 제안한다
    plans = plan.plan(items, target.get("received_date"))
    hist_key = ("cmp_hist", target["id"]); hit_sids = {}
    for pl in plans:
        if pl["source"] == "past":
            for d in pl["dates"][:1]:
                for p_ in db.past_values_for(pl["indicator"], d)[:1]: hit_sids[p_["submission_id"]] = hit_sids.get(p_["submission_id"], 0) + 1
        elif pl["source"] == "data" and pl["dates"]:
            for p_ in db.past_values_for(pl["indicator"], pl["dates"][-1])[:1]: hit_sids[p_["submission_id"]] = hit_sids.get(p_["submission_id"], 0) + 1
    with st.container(border=True):
        st.markdown("**자료 계획** — 항목마다 가진 자료(지표 데이터 → 과거 제출값)를 보고 어느 기준일 값을 낼지 제안합니다. 기준일이 없는 항목은 요구 문구(월별·최근 3년…)와 자료 범위로 판단합니다. 확인하고 고치면 됩니다.")
        if not items: st.caption("이 요구서에 항목이 없습니다. 기록 조회에서 항목을 넣거나 1단계에서 다시 읽으세요.")
        chosen: dict[int, list[str]] = {}
        for i, (it, pl) in enumerate(zip(items, plans, strict=True)):
            with st.container(border=True):
                a, b = st.columns([3, 2])
                a.markdown(f"**{ui.esc(it['item_text'])}**  \n<span class='small-muted'>지표 {ui.esc(pl['indicator'] or '사전에 없음')} · 요구 기준일 {ui.esc(pl['base_date'] or '없음')}" + (f" · 기간 {ui.esc(it['period'])}" if it.get("period") else "") + "</span>", unsafe_allow_html=True)
                tone = {"exact": "ok", "all": "ok", "latest": "warn", "nearest": "warn", "yearly": "ok", "quarterly": "ok"}.get(pl["mode"], "bad")
                icon = {"ok": ":material/check_circle:", "warn": ":material/help:", "bad": ":material/cancel:"}[tone]
                a.markdown(f"{icon} {pl['why']}")
                if pl["options"] and pl["mode"] not in ("none", "unknown_indicator"):
                    many = len(pl["options"]) > 1
                    picked = b.multiselect("낼 기준일", pl["options"], default=pl["dates"], key=f"cmp_dates_{target['id']}_{i}", help="제안이 기본값입니다. 더하거나 빼면 그대로 가져옵니다.") if many else pl["dates"]
                    chosen[i] = picked
                    b.caption(f"{'지표 데이터' if pl['source'] == 'data' else '과거 제출값'} · {len(picked)}개 기준일 · 약 {plan._count(pl['indicator'], picked, pl['source'])}건")
        pullable = {i: d for i, d in chosen.items() if d}
        missing = [it for it, pl in zip(items, plans, strict=True) if not pl["options"] or pl["mode"] in ("none", "unknown_indicator")]
        n_rows = sum(plan._count(plans[i]["indicator"], d, plans[i]["source"]) for i, d in pullable.items())
        b1, b2, b3 = st.columns([2.4, 1.3, 2.6])
        if pullable and b1.button(f"계획대로 가져오기 ({len(pullable)}개 항목 · {n_rows}건)", type="primary", key="cmp_pull", icon=":material/history:"):
            rows = [r for i, d in pullable.items() for r in plan.rows_for(plans[i]["indicator"], d, plans[i]["source"])]
            df = pd.DataFrame(rows)
            st.session_state[hist_key] = df[VAL_COLS + ["source_file", "source_sheet", "source_row"]].reset_index(drop=True) if len(df) else None; st.rerun()
        if st.session_state.get(hist_key) is not None and b2.button("가져온 값 비우기", key="cmp_pull_clear"):
            st.session_state.pop(hist_key); st.rerun()
        if missing:
            # 새로 산출할 항목의 입력 서식: 지표·센터(기록에 있는 센터 목록)·기준일을 채워 두고 값만 비운 엑셀
            centers = sorted({v["center"] for v in db.all_values_sample(limit=2000) if v["center"]})
            tmpl = pd.DataFrame([{"지표명": it.get("indicator") or it["item_text"][:30], "센터명": c, "기준일": it.get("base_date") or "", "값": None, "지표 정의": "", "집계기간": "", "추출시점": "", "원자료 버전": ""}
                                 for it in missing for c in (centers or [""])])
            buf = io.BytesIO(); pii.excel_safe(tmpl).to_excel(buf, index=False)
            b3.download_button(f"새로 산출할 {len(missing)}개 항목 입력 서식(엑셀)", buf.getvalue(), file_name=f"입력서식_요구{target['id']}_{dt.date.today()}.xlsx", key="cmp_tmpl",
                               help="지표·센터·기준일이 채워진 긴 형식 서식. '값' 칸만 채워 아래에 올리면 바로 읽힙니다.")
        if st.session_state.get(hist_key) is not None:
            h = st.session_state[hist_key]; ds = sorted(h["base_date"].unique())
            st.caption(f"가져온 값 {len(h)}건 · 기준일 {ds[0]}" + (f" ~ {ds[-1]} ({len(ds)}개)" if len(ds) > 1 else "") + ". 아래에 파일을 올리면 같은 지표·센터·기준일은 올린 값이 우선합니다.")
    # 2) 지난번 값: 기록에서 가장 많이 맞은 제출본을 기본 선택
    c1, c2 = st.columns(2)
    with c2:
        st.markdown("**지난번 제출값**")
        if not subs:
            st.info("아직 등록된 과거 답변이 없습니다. '과거 답변 등록'에서 먼저 넣으면 맞춰 볼 수 있습니다. 지금은 이번 수치만 확정하고 넘어갈 수 있습니다."); old_df, s = None, None
        else:
            best_sid = max(hit_sids, key=hit_sids.get) if hit_sids else None
            s_default = next((i for i, x in enumerate(subs) if x["id"] == best_sid), 0)
            s = st.selectbox("어느 답변과 맞춰 볼까요", subs, index=s_default, format_func=lambda s: f"#{s['id']} {s['submitted_date']} {s['requester'] or '(요청 주체 미기재)'} — {s['request_title'] or '(제목 없음)'} ({s['file_name']})")
            old_df = pd.DataFrame(db.get_values(s["id"]))
        old_dates = sorted(old_df["base_date"].dropna().unique().tolist()) if old_df is not None and len(old_df) else []
        if old_df is not None and len(old_df):
            st.caption(f"값 {len(old_df)}건 · 지표 {', '.join(sorted(old_df['indicator'].unique())[:5])} · 기준일 {', '.join(old_dates[:3])}. 같은 지표명·센터명·기준일인 행끼리 맞춰 봅니다.")
    with c1:
        st.markdown("**이번에 낼 수치** — 새로 산출한 것만")
        up_df, _ = value_sources("cmp_new", "이번 수치", base_date_default=old_dates[0] if len(old_dates) == 1 else None, demo_files=sorted(SAMPLE.glob("등원율_*9월*.xlsx")))
        hist_df = st.session_state.get(hist_key)
        parts = [d for d in (hist_df, up_df) if d is not None and len(d)]
        new_df = pd.concat(parts, ignore_index=True).drop_duplicates(subset=["indicator", "center", "base_date"], keep="last").reset_index(drop=True) if parts else None
        if new_df is not None:
            st.caption(f"{len(new_df)}건 준비됨" + (f" (기록에서 {len(hist_df)}건" + (f" + 올린 값 {len(up_df)}건, 겹치면 올린 값" if up_df is not None and len(up_df) else "") + ")" if hist_df is not None and len(hist_df) else "") + ".")
    st.markdown("#### 맞춰 본 결과")
    tol = st.number_input("허용 오차 — 지난번 값과 이번 값의 차이가 이 값 이하면 '일치'로 봅니다. 0이면 완전히 같아야 일치. 단위는 값과 같음(등원율 % 포인트, 학생 수 명)",
                          value=0.0, step=0.1, min_value=0.0, help="예: 지난번 67.8, 이번 67.9 → 차이 0.1. 허용 오차 0이면 '차이', 0.1이면 '일치'. 반올림 자릿수 차이만 무시하려면 0.05~0.1.")
    if new_df is None:
        st.info("이번에 낼 수치가 아직 없습니다. 위 '기록의 값 가져오기'를 누르거나, 새로 산출한 파일을 왼쪽에 올리면 여기 결과가 나타납니다."); return
    reasons, m, ck = {}, None, None
    if old_df is not None and len(old_df):
        m = compare.compare(new_df, old_df, tol)
        n_diff = int((m["판정"] == "차이").sum()); n_both = int(m["판정"].isin(["차이", "일치", "값 누락"]).sum())
        if n_both == 0:
            st.warning("지표명·센터명·기준일이 모두 같은 행이 하나도 없어 맞춰 볼 짝이 없습니다. "
                       f"지난번: 지표 {', '.join(sorted(old_df['indicator'].unique())[:8])} / 기준일 {', '.join(old_dates[:3])} — "
                       f"이번: 지표 {', '.join(sorted(new_df['indicator'].unique())[:8])} / 기준일 {', '.join(sorted(new_df['base_date'].unique())[:3])}. "
                       "표 읽는 법의 '지표명'과 '기준일'을 지난번에 맞추세요.")
        k1, k2, k3 = st.columns(3)
        k1.metric("짝이 맞는 행", n_both); k2.metric("차이", n_diff); k3.metric("일치", int((m["판정"] == "일치").sum()))
        st.dataframe(m[["indicator", "center", "base_date", "old_value", "new_value", "diff", "판정", "단서"]]
                     .rename(columns={"indicator": "지표", "center": "센터", "base_date": "기준일", "old_value": "지난번", "new_value": "이번", "diff": "차이"}), width="stretch", hide_index=True)
        diff_rows = m[m["판정"] == "차이"].to_dict("records")
        if diff_rows:
            st.markdown("#### 차이가 난 이유 (담당자가 적음)")
            cands_key = tuple((r["indicator"], r["center"], r["base_date"]) for r in diff_rows)
            if st.button("문구 후보 받기 (단서·과거에 쓴 사유 근거)"):
                cands, how = run_ai(f"사유 문구 후보 만드는 중 (차이 {len(diff_rows)}건)", lambda: assist.reason_candidates(diff_rows), "보통 10~20초", "후보 만드는 중… (규칙 기반)")
                st.session_state["reason_cands"] = (cands_key, cands, how)
            cands = st.session_state.get("reason_cands")
            cands = cands[1] if cands and cands[0] == cands_key else None
            if cands: st.caption(f"후보 생성 방식: {st.session_state['reason_cands'][2]} · 후보는 제안일 뿐이며 원인을 새로 추측한 문구는 없습니다.")
            for r in diff_rows:
                key = (r["indicator"], r["center"], r["base_date"])
                prev = db.reasons_for(*key)
                if cands and cands.get(key):
                    opts = [c for c in cands[key] if c.get("문구")]
                    a, b = st.columns([5, 1])
                    pick = a.selectbox(f"후보 — {r['center']}", opts, format_func=lambda c: f"{c['문구']}  〔{c['근거']}〕", key=f"cand_{key}", label_visibility="collapsed")
                    if b.button("적용", key=f"apply_{key}"): st.session_state[f"reason_{key}"] = pick["문구"]
                reasons[key] = st.text_input(f"{r['center']} · {r['indicator']} · {r['base_date']}  ({r['old_value']} → {r['new_value']})",
                                             value=prev[0]["reason"] if prev else "", placeholder="예: 8/5 출결 사후 보정 반영", key=f"reason_{key}")
        ck = compare.checklist(m, reasons)
        with st.expander("제출 전 점검표", expanded=False):
            st.dataframe(ck, width="stretch", hide_index=True)
            buf = io.BytesIO(); pii.excel_safe(ck).to_excel(buf, index=False)
            st.download_button("점검표 엑셀", buf.getvalue(), file_name=f"정합성점검표_{dt.date.today()}.xlsx")
    hits = pii.scan_df(new_df.drop(columns=["source_file", "source_sheet", "source_row"], errors="ignore"), "이번 수치")
    if hits: st.error(f"개인정보로 보이는 패턴 {len(hits)}건 — 확정 전에 지우세요"); st.dataframe(pd.DataFrame(hits), width="stretch", hide_index=True)
    else: st.caption("개인정보 패턴 검사: 없음")
    if st.button("이 수치를 확정하고 3단계로 →", type="primary", disabled=target is None):
        sid = db.add_submission(target["id"], str(dt.date.today()), USER, "새 집계값", "confirmed", "대조 후 확정", new_df.to_dict("records"))
        for key, reason in reasons.items():
            if reason.strip() and m is not None:
                row = m[(m["indicator"] == key[0]) & (m["center"] == key[1]) & (m["base_date"] == key[2])].iloc[0]
                db.add_reason(sid, *key, row["old_value"], row["new_value"], reason, USER)
        st.session_state.pop(hist_key, None)
        st.session_state.update(last_submission=sid, last_request=target["id"], target_request=target["id"],
                                last_checklist=ck.to_dict("records") if ck is not None else None, last_reasons={" | ".join(k): v for k, v in reasons.items() if v.strip()})
        go("새 요구서 처리", 3)

def step_draft():
    st.markdown("### 회신 초안")
    st.caption("확정한 수치와 적어 둔 사유만으로 공문체 초안을 만듭니다. 수치가 없는 항목은 지어내지 않고 [확인 필요]로 남깁니다.")
    reqs = db.list_requests()
    if not reqs: st.warning("요구서가 없습니다. 1단계에서 등록하세요."); return
    default = next((i for i, r in enumerate(reqs) if r["id"] == st.session_state.get("last_request", st.session_state.get("target_request"))), 0)
    req = st.selectbox("회신할 요구서", reqs, index=default, format_func=req_label)
    items = db.get_items(req["id"])
    sid, vals = db.latest_confirmed_values(req["id"])
    values = pd.DataFrame(vals) if vals else pd.DataFrame(columns=VAL_COLS)
    if sid: st.caption(f"확정한 수치: 제출본 #{sid} · {len(values)}건")
    else: st.warning("이 요구서에 확정된 수치가 없습니다. 2단계에서 먼저 확정하세요. 지금 만들면 수치 자리는 [확인 필요]가 됩니다.")
    reasons_raw = {}
    for _, v in values.iterrows():
        for rr in db.reasons_for(v["indicator"], v["center"], v["base_date"]):
            reasons_raw[(v["indicator"], v["center"], v["base_date"])] = rr["reason"]; break
    prov = draft.provenance_by_indicator(values)
    ck_now = st.session_state.get("last_checklist") if st.session_state.get("last_request") == req["id"] else None
    if st.button("초안 만들기", type="primary"):
        d, how = run_ai("회신 초안 쓰는 중", lambda: draft.make_draft(req, items, values, reasons_raw, prov, ck_now), "보통 10~15초", "초안 만드는 중… (규칙 기반)")
        st.session_state.update(draft=d, draft_how=how, draft_req=req["id"])
    if not (st.session_state.get("draft_req") == req["id"] and "draft" in st.session_state): return
    d = st.session_state["draft"]
    st.caption(f"생성 방식: {st.session_state['draft_how']} · 고칠 곳은 바로 고치면 됩니다.")
    d["제목"] = st.text_input("제목", d.get("제목", ""))
    d["본문"] = st.text_area("본문", d.get("본문", ""), height=220)
    a, b = st.columns(2)
    d["차이사유"] = a.text_area("차이 사유", d.get("차이사유", ""), height=110)
    d["산출근거"] = b.text_area("산출 근거", d.get("산출근거", ""), height=110)
    st.markdown("#### 빠진 항목은 없는지")
    cov = draft.coverage_check(items, d, values)
    n_ok, n_flag, n_miss = int((cov["판정"] == "충족").sum()), int((cov["판정"] == "미확인 표시").sum()), int((cov["판정"] == "누락").sum())
    if n_miss: st.warning(f"충족 {n_ok} · [확인 필요] {n_flag} · 누락 {n_miss} — 누락 항목은 초안에 언급이 없습니다. 본문을 보완하세요.")
    elif n_flag: st.info(f"충족 {n_ok} · [확인 필요] {n_flag} — 확정한 수치가 없는 항목은 [확인 필요]로 남아 있습니다.")
    else: st.success(f"모든 요구 항목 충족({n_ok}건)")
    with st.expander("항목별 판정"): st.dataframe(cov, width="stretch", hide_index=True)
    if HAS_API and st.button("AI로 한 번 더 점검 (누락·불일치·단정 표현)"):
        st.info(run_ai("초안 점검 중 (누락·불일치·단정 표현)", lambda: draft.coverage_check_llm(items, d, values, ck_now, reasons_raw, prov), "약 5초"))
    st.markdown("#### 이 회신을 받으면 어떤 질문이 올까")
    if st.button("예상 질문 보기 (요구 항목·수치·대조 결과·타 기관 제출 이력 근거)"):
        qs, how = run_ai("예상 후속 질문 뽑는 중", lambda: assist.foresee(req, items, values, reasons_raw, ck_now, d), "보통 20~30초 — 요구 항목·수치·대조 결과·타 기관 제출 이력을 함께 봅니다", "예측 중… (규칙 기반)")
        st.session_state["foresee"] = (req["id"], qs, how)
    fs = st.session_state.get("foresee")
    if fs and fs[0] == req["id"]:
        st.caption(f"생성 방식: {fs[2]} · 질문은 예측이며 수치 해석은 포함하지 않습니다.")
        st.dataframe(pd.DataFrame(fs[1])[["가능성", "질문", "근거", "준비할 자료"]], width="stretch", hide_index=True)
    d_hits = [h for k in ("제목", "본문", "차이사유", "산출근거") for h in pii.scan_text(d.get(k, ""), f"초안 {k}")]
    if d_hits: st.error(f"초안에 개인정보로 보이는 패턴 {len(d_hits)}건 — 제출 전 삭제·가명 처리"); st.dataframe(pd.DataFrame(d_hits), width="stretch", hide_index=True)
    else: st.caption("초안 개인정보 패턴 검사: 없음")
    st.markdown("#### 한글 파일로 내보내기")
    tpls = sorted(TPL_DIR.glob("*.hwpx"))
    tpl = st.selectbox("서식(템플릿)", tpls, format_func=lambda p: p.name) if tpls else None
    up_tpl = st.file_uploader("다른 서식 올리기 ({{키}} 자리표시자가 있는 HWPX)", type=["hwpx"], key="tpl_up")
    tpl_bytes = up_tpl.getvalue() if up_tpl else (tpl.read_bytes() if tpl else None)
    if not tpl_bytes: return
    st.caption("서식의 자리표시자: " + ", ".join(hwpx_out.placeholders(tpl_bytes)))
    fill = {"수신": req["requester"] or "", "발신": f"{ORG['company']} {ORG['org_name']}".strip(), "제목": d["제목"],
            "요구항목": "\n".join(f"{i}. {it['item_text']}" for i, it in enumerate(items, 1)),
            "본문": d["본문"], "차이사유": d["차이사유"], "산출근거": d["산출근거"], "담당자": USER}
    rows = [{"no": i + 1, "indicator": v["indicator"], "center": v["center"], "base_date": v["base_date"], "value": v["value"]} for i, (_, v) in enumerate(values.iterrows())]
    out = render_hwpx_cached(tpl_bytes, json.dumps(fill, ensure_ascii=False, sort_keys=True), json.dumps(rows, ensure_ascii=False, default=str))
    fname = f"회신_{req['id']}_{dt.date.today()}.hwpx"
    c1, c2, c3 = st.columns(3)
    c1.download_button("회신 HWPX 받기", out, file_name=fname)
    ckb = io.BytesIO(); pii.excel_safe(pd.DataFrame(ck_now or [])).to_excel(ckb, index=False)
    zb = io.BytesIO()
    with zipfile.ZipFile(zb, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(fname, out); zf.writestr("정합성점검표.xlsx", ckb.getvalue())
        vb = io.BytesIO(); pii.excel_safe(values).to_excel(vb, index=False); zf.writestr("확정_제출값.xlsx", vb.getvalue())
        zf.writestr("근거_사유_메타.json", json.dumps({"요구": req, "제출본": sid, "차이 사유": {" | ".join(k): v for k, v in reasons_raw.items()}, "산출 근거": prov, "초안": d,
                                                  "예상 후속 질문": fs[1] if fs and fs[0] == req["id"] else None, "생성일": str(dt.date.today())}, ensure_ascii=False, indent=2, default=str))
    c2.download_button("제출 묶음 ZIP (회신+점검표+수치+근거)", zb.getvalue(), file_name=f"제출묶음_{req['id']}_{dt.date.today()}.zip")
    if c3.button("초안 저장하고 팀장 검토 요청", type="primary"):
        (OUT_DIR / fname).write_bytes(out)
        did = db.add_draft(sid, req["id"], d, fname, status="review_requested")
        st.session_state["step"] = 1
        st.success(f"초안 #{did}을 저장하고 검토를 요청했습니다. 팀장은 '검토·승인'에서 봅니다.")

# ================= 검토·승인 =================
def page_review():
    ui.page_title("검토·승인", "담당자가 올린 회신 초안을 팀장이 확인합니다. 승인하면 확정 기록으로 남습니다.", "업무")
    pending = db.list_drafts("review_requested")
    st.metric("검토 대기", len(pending))
    drafts = db.list_drafts()
    if not drafts: st.info("아직 초안이 없습니다."); return
    for d in drafts:
        with st.expander(f"초안 #{d['id']} · {ko(d['status'])} · {d['requester'] or '요청 주체 미기재'} — {d['request_title'] or '(제목 없음)'} (기한 {d['due_date'] or '미기재'}) · {(d['created_at'] or '')[:16]}", expanded=d["status"] == "review_requested"):
            st.markdown(f"**{d['title']}**"); st.text(d["body"]); st.caption("차이 사유"); st.text(d["reasons_text"]); st.caption("산출 근거"); st.text(d["provenance_text"])
            f = OUT_DIR / (d["hwpx_name"] or "")
            if d["hwpx_name"] and f.exists(): st.download_button("회신 HWPX", f.read_bytes(), file_name=d["hwpx_name"], key=f"dl_{d['id']}")
            for rv in db.reviews_for(d["id"]): st.markdown(f"- {rv['created_at'][:16]} {rv['reviewer']} **{ko(rv['decision'])}**: {rv['comment']}")
            if d["status"] == "review_requested":
                cmt = st.text_input("검토 의견", key=f"cmt_{d['id']}")
                c1, c2, c3 = st.columns(3)
                if c1.button("승인", key=f"ok_{d['id']}", type="primary"): db.add_review(d["id"], REVIEWER, "approved", cmt); st.rerun()
                if c2.button("반려", key=f"no_{d['id']}"): db.add_review(d["id"], REVIEWER, "rejected", cmt); st.rerun()
                if c3.button("의견만", key=f"c_{d['id']}"): db.add_review(d["id"], REVIEWER, "comment", cmt); st.rerun()

# ================= 기록 조회 =================
def confirm_delete(label: str, key: str, what: str) -> bool:
    """두 단계 삭제: 확인 체크 → 삭제 버튼. 체크해야 버튼이 나타난다. 눌리면 True."""
    c1, c2 = st.columns([3, 1.2])
    ok = c1.checkbox(f"{label} 삭제 확인 — {what}", key=f"{key}_ok")
    return ok and c2.button(f"{label} 삭제", key=f"{key}_go", type="primary", icon=":material/delete:")

def request_editor(r: dict):
    """요구서 머리 정보·항목 수정 폼. 저장하면 DB 반영 후 닫힘."""
    with st.container(border=True):
        st.markdown("**요구서 수정** — 원문은 그대로 두고 머리 정보와 항목만 바꿉니다.")
        c1, c2, c3, c4 = st.columns(4)
        with c1: requester = _requester_input("요청 주체", r["requester"] or "", f"ed_rq_{r['id']}")
        received = c2.text_input("접수일", r["received_date"] or "", key=f"ed_rc_{r['id']}")
        due = c3.text_input("제출기한", r["due_date"] or "", key=f"ed_du_{r['id']}")
        title = c4.text_input("제목", r["title"] or "", key=f"ed_ti_{r['id']}")
        its = pd.DataFrame(db.get_items(r["id"]), columns=["id", "request_id", "seq"] + extract.ITEM_FIELDS)[extract.ITEM_FIELDS]
        its = st.data_editor(its, num_rows="dynamic", width="stretch", key=f"ed_items_{r['id']}",
                             column_config={"item_text": "항목 원문", "indicator": st.column_config.SelectboxColumn("지표명(정규화)", options=list(normalize.CANON), required=False),
                                            "base_date": "기준일", "period": "기간", "unit": "단위"})
        s1, s2, s3 = st.columns([1, 1, 3])
        cur_st = r.get("status") or "접수"
        new_st = s1.selectbox("상태", db.REQUEST_STATUSES, index=db.REQUEST_STATUSES.index(cur_st) if cur_st in db.REQUEST_STATUSES else 0, key=f"ed_st_{r['id']}")
        assignee = s2.text_input("담당자", r.get("assignee") or "", key=f"ed_as_{r['id']}")
        memo = s3.text_input("메모(전화 내용·특이사항)", r.get("memo") or "", key=f"ed_me_{r['id']}")
        b1, b2, _ = st.columns([1, 1, 4])
        if b1.button("저장", key=f"ed_save_{r['id']}", type="primary"):
            db.update_request(r["id"], requester, received, due, title); n = db.replace_items(r["id"], its.to_dict("records"))
            db.set_request_status(r["id"], new_st, assignee, memo)
            st.session_state.pop(f"editing_{r['id']}", None); st.toast(f"요구서 #{r['id']} 저장 — 항목 {n}건"); st.rerun()
        if b2.button("취소", key=f"ed_cancel_{r['id']}"):
            st.session_state.pop(f"editing_{r['id']}", None); st.rerun()

def page_records():
    ui.page_title("기록", "요구서·제출값·사유·초안·승인을 찾아 고치거나, 말로 물어봅니다.", "기록")
    view = st.radio("보기", ["찾기·고치기", "물어보기"], horizontal=True, label_visibility="collapsed", key="rec_view")
    if view == "물어보기": _records_ask()
    else: _records_find()

def _records_find():
    st.caption("요구서마다 항목 → 제출값 → 사유 → 초안 → 승인이 한 줄로 이어져 있습니다. 잘못 넣은 것은 여기서 고치거나 지웁니다.")
    reqs = _q("list_requests")
    kw = st.text_input("찾기 (요청 주체·제목·항목·본문)", placeholder="예: 등원율, 의원실, 2026-06-30")
    if kw.strip():
        ids = {r["id"] for r in db.search_requests(kw.strip(), limit=200)}
        reqs = [r for r in reqs if r["id"] in ids]
        st.caption(f"검색 결과 {len(reqs)}건")
    if not reqs: st.info("등록된 요구서가 없습니다." if not kw.strip() else "검색 결과가 없습니다.")
    inv = {v: k for k, v in VAL_KO.items()}
    for r in reqs:
        rid = r["id"]
        with st.expander(f"#{rid} {r['received_date'] or '접수일 미기재'} · {r['requester'] or '요청 주체 미기재'} · {r['title'] or '(제목 없음)'} (기한 {r['due_date'] or '미기재'}) · {r.get('status') or '접수'}" + (f" · {r['assignee']}" if r.get("assignee") else "")):
            h1, h2 = st.columns([5, 1])
            h1.caption(f"원문 {len(r['raw_text'] or '')}자 · 파일 {r['source_file'] or '-'} · 등록 {r['created_at'][:16] if r.get('created_at') else '-'}")
            if h2.button("수정", key=f"edit_btn_{rid}", type="tertiary", icon=":material/edit:"):
                st.session_state[f"editing_{rid}"] = not st.session_state.get(f"editing_{rid}")
            if st.session_state.get(f"editing_{rid}"): request_editor(r)
            else:
                its = db.get_items(rid)
                if its: st.dataframe(pd.DataFrame(its)[list(ITEM_KO)].rename(columns=ITEM_KO), width="stretch", hide_index=True)
                else: st.caption("요구 항목 없음")
            for s_ in db.list_submissions(rid):
                sid = s_["id"]
                with st.container(border=True):
                    st.markdown(f"**제출값 #{sid}** · {s_['submitted_date']} · {ko(s_['status'])} · {s_['file_name']} · {s_['note'] or ''}")
                    vals = pd.DataFrame(db.get_values(sid))
                    if len(vals):
                        ed = st.data_editor(vals.drop(columns=["id", "submission_id"]).rename(columns=VAL_KO), num_rows="dynamic", width="stretch", height=min(60 + 35 * len(vals), 320), key=f"vals_{sid}")
                        c1, c2 = st.columns([1, 5])
                        if c1.button("값 저장", key=f"vals_save_{sid}", icon=":material/save:", help="표에서 고친 값·근거를 이 제출본에 그대로 저장합니다. 지표·센터·기준일·값이 빈 행은 버립니다."):
                            n = db.replace_values(sid, ed.rename(columns=inv).to_dict("records")); st.toast(f"제출본 #{sid} 값 {n}건 저장"); st.rerun()
                        c2.caption("표에서 바로 고친 뒤 '값 저장'. 행 삭제는 행을 선택하고 Delete.")
                    else: st.caption("값 없음(요구서만 저장된 제출본)")
                    if confirm_delete(f"제출값 #{sid}", f"del_sub_{sid}", "값·차이 사유·이 제출본으로 만든 초안이 함께 지워집니다"):
                        info = db.delete_submission(sid); st.toast(f"제출값 #{sid} 삭제 — 값 {info['values']}건, 초안 {info['drafts']}건"); st.rerun()
            for d in [x for x in db.list_drafts() if x["request_id"] == rid]:
                c1, c2 = st.columns([5, 1])
                c1.markdown(f"초안 #{d['id']} · {ko(d['status'])} · {d['hwpx_name'] or '-'} · {d['created_at'][:16]}")
                if c2.button("초안 삭제", key=f"del_draft_{d['id']}", type="tertiary", icon=":material/delete:"):
                    db.delete_draft(d["id"]); st.toast(f"초안 #{d['id']} 삭제"); st.rerun()
            st.divider()
            if confirm_delete(f"요구서 #{rid}", f"del_req_{rid}", "항목·제출값·차이 사유·초안·검토 기록이 전부 지워지며 되돌릴 수 없습니다"):
                info = db.delete_request(rid); st.toast(f"요구서 #{rid} 삭제 — 제출본 {info['submissions']}건, 초안 {info['drafts']}건"); st.rerun()

# ================= 현황 =================
def page_status():
    ui.page_title("현황", "요구 건의 상태 흐름(접수 → 처리 중 → 확정 → 발송 → 종결), 기한, 반복 지표, 관리대장.", "업무")
    ov = _q("request_overview")
    if not ov: st.info("등록된 요구서가 없습니다."); return
    df = pd.DataFrame(ov); today = dt.date.today(); df["status"] = df["status"].fillna("접수")
    def _dday(r):
        if r["status"] not in ("접수", "처리 중"): return ""
        try: return (dt.date.fromisoformat(str(r["due_date"])) - today).days
        except (ValueError, TypeError): return ""
    df["D-day"] = df.apply(_dday, axis=1)
    overdue = df["D-day"].map(lambda d: isinstance(d, int) and d < 0); soon = df["D-day"].map(lambda d: isinstance(d, int) and 0 <= d <= 3)
    n_prog = int(df["status"].isin(["접수", "처리 중"]).sum()); n_unsent = int((df["status"] == "확정").sum()); n_sent = int((df["status"] == "발송").sum())
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    ui.kpi(c1, len(df), "요구서"); ui.kpi(c2, n_prog, "접수·처리 중"); ui.kpi(c3, int(soon.sum()), "기한 3일 이내", "warn" if soon.sum() else ""); ui.kpi(c4, int(overdue.sum()), "기한 경과(미확정)", "bad" if overdue.sum() else "")
    ui.kpi(c5, n_unsent, "확정(미발송)", "warn" if n_unsent else ""); ui.kpi(c6, n_sent, "발송", "ok" if n_sent else "")
    picked = st.pills("상태", db.REQUEST_STATUSES, selection_mode="multi", default=[x for x in db.REQUEST_STATUSES if x != "종결"], key="status_filter") or db.REQUEST_STATUSES
    sel = df[df["status"].isin(picked)]
    show = sel.rename(columns={"id": "번호", "status": "상태", "assignee": "담당자", "requester": "요청 주체", "received_date": "접수일", "due_date": "제출기한", "title": "제목", "n_items": "항목 수", "n_confirmed": "확정 제출본", "last_submitted": "최근 제출일", "memo": "메모"})
    view = show[["번호", "상태", "D-day", "담당자", "요청 주체", "접수일", "제출기한", "제목", "항목 수", "확정 제출본", "최근 제출일", "메모"]].fillna({"담당자": "", "요청 주체": "미기재", "접수일": "미기재", "제출기한": "미기재", "제목": "(제목 없음)", "메모": "", "최근 제출일": ""})
    def _row_style(r):
        if r["상태"] in ("접수", "처리 중") and isinstance(r["D-day"], int) and r["D-day"] < 0: return ["background-color: #FDE8E8"] * len(r)
        if r["상태"] in ("접수", "처리 중") and isinstance(r["D-day"], int) and r["D-day"] <= 3: return ["background-color: #FFF4D6"] * len(r)
        if r["상태"] == "확정": return ["background-color: #E8F1FD"] * len(r)
        if r["상태"] in ("종결", "보류"): return ["color: #99A2AC"] * len(r)
        return [""] * len(r)
    st.dataframe(view.style.apply(_row_style, axis=1), width="stretch", hide_index=True, height=min(60 + 35 * len(view), 480))
    st.caption("빨강: 기한이 지났는데 확정 전 · 노랑: 기한 3일 이내 · 파랑: 확정했지만 발송 기록 없음 · 회색: 종결·보류. 상태는 등록·확정·발송 때 자동으로 넘어가고, 종결·보류는 아래에서 직접 바꿉니다.")
    with st.expander("건 관리 — 상태·담당자·메모 바꾸기"):
        tgt = st.selectbox("요구서", ov, format_func=lambda r: f"#{r['id']} · {r.get('status') or '접수'} · {r['requester'] or '요청 주체 미기재'} — {r['title'] or '(제목 없음)'}", key="mg_req")
        if tgt:
            cur_st = tgt.get("status") or "접수"
            a, b, c = st.columns([1, 1, 3])
            new_st = a.selectbox("상태", db.REQUEST_STATUSES, index=db.REQUEST_STATUSES.index(cur_st) if cur_st in db.REQUEST_STATUSES else 0, key=f"mg_st_{tgt['id']}")
            asg = b.text_input("담당자", tgt.get("assignee") or "", key=f"mg_as_{tgt['id']}", placeholder=USER)
            memo = c.text_input("메모", tgt.get("memo") or "", key=f"mg_me_{tgt['id']}", placeholder="예: 10/8 보좌관 통화 — 센터별 표만 원함")
            if st.button("저장", key=f"mg_save_{tgt['id']}", type="primary", icon=":material/save:"):
                db.set_request_status(tgt["id"], new_st, asg, memo); st.toast(f"요구서 #{tgt['id']} 저장"); st.rerun()
    rows = [{"요구번호": r["id"], "상태": r.get("status") or "접수", "담당자": r.get("assignee") or "", "요청 주체": r["requester"], "접수일": r["received_date"], "제출기한": r["due_date"], "제목": r["title"], "항목": it["item_text"], "지표": it["indicator"], "기준일": it["base_date"], "단위": it["unit"], "확정 제출본 수": r["n_confirmed"], "최근 제출일": r["last_submitted"], "메모": r.get("memo") or ""} for r in ov for it in db.get_items(r["id"])]
    lb = io.BytesIO(); pii.excel_safe(pd.DataFrame(rows)).to_excel(lb, index=False)
    st.download_button("관리대장 엑셀 내보내기", lb.getvalue(), file_name=f"요구자료_관리대장_{dt.date.today()}.xlsx")
    by_req, by_ind = _q("requester_stats")
    c1, c2 = st.columns(2)
    with c1: st.markdown("### 요청 주체별"); st.dataframe(pd.DataFrame(by_req).rename(columns={"requester": "요청 주체", "n_requests": "요구 건수", "n_items": "항목 수", "first_date": "최초", "last_date": "최근"}), width="stretch", hide_index=True); st.caption("표기가 갈라져 보이면 설정 → 서식·사전의 요청 주체 사전에 별칭을 넣고 '기록에 표기 통일 적용'.")
    with c2: st.markdown("### 반복 요구 지표"); st.dataframe(pd.DataFrame(by_ind).rename(columns={"indicator": "지표", "n_times": "요구 횟수", "n_requesters": "요청 주체 수", "base_dates": "기준일들"}).fillna("-"), width="stretch", hide_index=True); st.caption("여러 번 요구된 지표는 미리 산출해 두면 좋습니다.")
    st.markdown("### 월간 리포트")
    st.caption("한 달의 접수·확정·발송 건수와 소요일을 기록에서 단순 집계합니다(지표 값은 포함하지 않음). 요약문은 복사해 보고 메일에, 엑셀은 회의 자료에 붙이면 됩니다.")
    months = report.months_available(); mo = st.selectbox("월", months, key="rep_month")
    rp = report.monthly(mo); L = rp["lead"]
    r1, r2, r3, r4, r5 = st.columns(5)
    ui.kpi(r1, rp["received"], "접수"); ui.kpi(r2, rp["confirmed"], "확정"); ui.kpi(r3, rp["sent"], "발송"); ui.kpi(r4, rp["open_end"], "월말 진행 중", "warn" if rp["open_end"] else ""); ui.kpi(r5, rp["overdue_end"], "월말 기한 경과", "bad" if rp["overdue_end"] else "")
    q1, q2 = st.columns(2)
    with q1:
        st.dataframe(pd.DataFrame(rp["by_requester"], columns=["요청 주체", "접수 건수"]), width="stretch", hide_index=True, height=min(60 + 35 * max(len(rp["by_requester"]), 1), 240))
        st.caption(f"접수→확정 평균 {L['접수→확정']['평균'] if L['접수→확정']['평균'] is not None else '-'}일({L['접수→확정']['건수']}건) · 확정→발송 평균 {L['확정→발송']['평균'] if L['확정→발송']['평균'] is not None else '-'}일({L['확정→발송']['건수']}건)")
    with q2: st.dataframe(pd.DataFrame(rp["by_indicator"], columns=["지표", "요구 횟수"]), width="stretch", hide_index=True, height=min(60 + 35 * max(len(rp["by_indicator"]), 1), 240))
    st.text_area("보고용 요약(복사해서 쓰세요)", report.to_text(rp), height=170, key=f"rep_txt_{mo}")
    st.download_button("월간 리포트 엑셀", report.to_excel(rp), file_name=f"월간리포트_{mo}.xlsx", key=f"rep_xlsx_{mo}", icon=":material/download:")

# ================= 기록에 묻기 =================
def _records_ask():
    st.caption("말로 물으면 기록을 찾아 근거(요구번호·제출일·기관)와 함께 답합니다. 평균·증감 같은 계산은 하지 않습니다." + ("" if HAS_API else " 지금은 AI 연결이 없어 키워드 검색으로 동작합니다."))
    examples = ["감사실에 등원율을 언제 어떤 값으로 냈지?", "2026-06-30 기준 등원율을 제출한 기관과 날짜를 전부 보여줘", "센터C 등원율이 달라진 사유로 뭐라고 적었나", "기한이 가장 가까운 요구서는?"]
    q = st.text_input("질문", placeholder=examples[0], key="qa_q")
    c1, c2 = st.columns([1, 4])
    go_ = c1.button("물어보기", type="primary", disabled=not q.strip())
    c2.caption("예: " + " · ".join(examples[1:]))
    if go_:
        res = run_ai("기록 찾는 중", lambda: history_qa.ask(q), "조회 도구를 골라 몇 차례 호출합니다. 보통 5~30초", "기록 찾는 중… (키워드 검색)") if HAS_API else {"text": None, "trace": [], "how": "키 없음 — 키워드 검색"}
        if not (res.get("text") or "").strip(): res["text"] = None
        kw = history_qa.keyword_search(q) if res.get("text") is None else None
        st.session_state.setdefault("qa_log", []).insert(0, {"q": q, "res": res, "kw": kw})
        st.session_state["qa_log"] = st.session_state["qa_log"][:5]
    for i, e in enumerate(st.session_state.get("qa_log", [])):
        with st.container(border=True):
            st.markdown(f"**Q. {e['q']}**")
            res, kw = e["res"], e["kw"]
            if res.get("text"):
                st.markdown(ui.safe_md(res["text"])); st.caption(res.get("how", ""))
                with st.expander(f"근거 기록 — 조회 {len(res['trace'])}회"):
                    for t in res["trace"]:
                        st.markdown(f"{llm.tool_label(t['tool'])} — {ui.esc(', '.join(f'{k}: {v}' for k, v in (t['input'] or {}).items()) or '전체')}" + (f" → {t['rows']}건" if t.get("rows") is not None else ""))
                        r_ = t.get("result")
                        if isinstance(r_, list) and r_ and isinstance(r_[0], dict): st.dataframe(pd.DataFrame(r_), width="stretch", height=min(300, 40 + 35 * len(r_)), hide_index=True)
                        elif isinstance(r_, dict) and "error" in r_: st.error(r_["error"])
            else:
                st.caption(res.get("how", ""))
                if kw:
                    st.markdown(f"검색어: {', '.join(kw['tokens']) or '-'} · 인식 지표: {', '.join(kw['indicators']) or '-'}")
                    if kw["requests"]: st.dataframe(pd.DataFrame(kw["requests"]).rename(columns={"id": "요구번호", "requester": "요청 주체", "received_date": "접수일", "due_date": "제출기한", "title": "제목"}), width="stretch", hide_index=True)
                    else: st.info("일치하는 요구서가 없습니다.")
                    if kw["reasons"]: st.caption("관련 차이 사유 기록"); st.dataframe(pd.DataFrame(kw["reasons"])[["indicator", "center", "base_date", "old_value", "new_value", "reason", "created_at"]].rename(columns={"indicator": "지표", "center": "센터", "base_date": "기준일", "old_value": "지난번", "new_value": "이번", "reason": "사유", "created_at": "입력일"}), width="stretch", hide_index=True)
            if i == 0 and res.get("text") is None and HAS_API: st.warning("AI 응답이 없어 키워드 검색 결과를 표시했습니다.")

# ================= 설정 =================
def page_settings():
    import providers
    ui.page_title("설정", "AI 연결, 서식, 시연용 데이터와 초기화.", "설정")
    tab_ai, tab_data, tab_demo = st.tabs(["AI 연결", "서식·사전", "백업·초기화"])
    with tab_ai:
        st.markdown("키는 이 브라우저 세션에만 보관되고 파일에 저장되지 않습니다. 탭을 닫으면 사라집니다. 혼자 쓰는 PC라면 `set_key.bat`으로 한 번 저장해 두면 매번 넣지 않아도 됩니다.")
        if st.session_state.pop("cfg_clear", False):            # '키 지우기' 다음 실행: 입력칸(위젯) 값도 비운다(칸이 그려지기 전에 지워야 한다)
            for k in [k for k in st.session_state if str(k).startswith("cfg_") and k not in ("cfg_dept_head", "cfg_dept_phone", "cfg_org_name", "cfg_company")]: del st.session_state[k]
        cfg = st.session_state["llm_cfg"]
        pnames = providers.names()
        prov_name = st.selectbox("공급자", pnames, index=pnames.index(cfg.get("provider")) if cfg.get("provider") in pnames else 0,
                                 format_func=lambda n: f"{providers.PROVIDERS[n].label} ({n})")
        pcls = providers.PROVIDERS[prov_name]
        new_cfg = {"provider": prov_name}
        EFFORTS = [("", "기본 — 모델 기본값(Opus 5.5는 medium)"), ("low", "빠르게(low) — 추출·분류 위주, 비용·지연 최소"), ("medium", "보통(medium)"), ("high", "꼼꼼하게(high) — 초안·대화 품질 우선"), ("xhigh", "매우 꼼꼼하게(xhigh)"), ("max", "최대(max) — 느리고 비쌈")]
        for f in pcls.fields:
            env_set = bool(os.environ.get(f.get("env", ""), ""))
            hint = " · 이 PC 환경변수에 값이 있어 비워도 됨" if env_set else ""
            if f["key"] == "effort":
                cur = (cfg.get("effort") or "").strip().lower(); keys = [e for e, _ in EFFORTS]
                new_cfg["effort"] = st.selectbox("추론 강도" + hint, keys, index=keys.index(cur) if cur in keys else 0, format_func=dict(EFFORTS).get, key=f"cfg_{prov_name}_effort",
                                                 help="추출·분류·연결 테스트는 설정과 상관없이 low로 돌고, 이 값은 초안·대화 처리·이력 질의에 쓰입니다. 비우면 모델 기본.")
                continue
            new_cfg[f["key"]] = st.text_input(f["label"] + hint, value=cfg.get(f["key"], "") or "", type="password" if f.get("secret") else "default", key=f"cfg_{prov_name}_{f['key']}").strip()
        models = st.session_state.get("model_list") or []
        model_ids = [m["id"] for m in models]
        pick = st.selectbox("모델 (비우면 기본 후보 순으로 자동: " + " → ".join(pcls.default_models) + ")", ["(자동)"] + model_ids + ["직접 입력"],
                            index=(model_ids.index(cfg["model"]) + 1) if cfg.get("model") in model_ids else (len(model_ids) + 1 if cfg.get("model") else 0))
        if pick == "직접 입력": new_cfg["model"] = st.text_input("모델 id", value=cfg.get("model", "") or "").strip()
        elif pick == "(자동)": new_cfg["model"] = ""
        else: new_cfg["model"] = pick
        c1, c2, c3 = st.columns(3)
        if c1.button("적용", type="primary"):
            changed = {k: v for k, v in new_cfg.items() if v != cfg.get(k)}
            cfg.clear(); cfg.update(new_cfg)
            if "model" in changed or "provider" in changed: st.session_state["llm_state"] = {}
            st.rerun()
        if c2.button("키 지우기", help="이 세션의 키·모델 설정과 입력칸을 모두 비웁니다"):
            cfg.clear(); st.session_state["llm_state"] = {}; st.session_state["model_list"] = []; st.session_state["cfg_clear"] = True; st.rerun()
        if c3.button("연결 테스트", disabled=not llm.available()):
            r = run_ai("연결 테스트", llm.test_connection, "1~3초")
            if r.get("ok"): st.success(f"연결 성공 · 공급자 {r.get('provider')} · 모델 {r['model']} · 왕복 {r['latency_s']}초")
            else: st.error(f"연결 실패: {r.get('error')}")
            if r.get("models"):
                st.session_state["model_list"] = r["models"]
                st.caption("이 키로 쓸 수 있는 모델 — 위 '모델' 목록에 반영됨(다시 적용 필요)"); st.dataframe(pd.DataFrame(r["models"]), width="stretch", height=200, hide_index=True)
            elif r.get("models_error"): st.caption(f"모델 목록 조회 실패: {r['models_error']}")
        ui.status_line(HAS_API, f"현재: 공급자 {llm.provider_name()} · {'키 있음' if HAS_API else '키 없음(규칙 기반)'} · 모델 {llm.model_label()}")
        if providers._errors: st.warning("불러오지 못한 공급자 플러그인: " + "; ".join(f"{k}: {v}" for k, v in providers._errors.items()))
        with st.expander("공급자 추가 방법(모듈 교체)"):
            st.markdown("1. `docs/provider_template.py`를 복사해 `app/provider_<이름>.py`로 저장\n2. `name`·`label`·`fields`·`default_models`·`create_message()`를 채움(응답은 `providers.SimpleMessage`로 감싸면 됨)\n3. 앱을 다시 시작하면 이 목록에 나타남\n4. 서버 환경변수 `LLM_PROVIDER=<이름>`으로 기본 선택")
        if llm.log():
            with st.expander("이 세션 호출 기록"): st.dataframe(pd.DataFrame(llm.log()), width="stretch", height=160, hide_index=True)
    with tab_data:
        st.markdown("**회신 서식**: `templates/` 폴더의 HWPX. 실제 부서 서식을 한글에서 열어 {{수신}} {{제목}} {{본문}} {{row.center}} 같은 자리표시자를 넣고 저장하면 그대로 쓰입니다.")
        st.markdown("**조직 설정** — 답변자료의 【확인 : 부서장 ☎ 내선】 줄, 발신 표기, 메일 문안에 쓰입니다. 기록 DB에 저장되어 접속자 모두에게 적용됩니다(환경변수 DEPT_HEAD·DEPT_PHONE은 비어 있을 때의 보조값).")
        c1, c2, c3, c4 = st.columns(4)
        o_head = c1.text_input("부서장 이름", ORG["dept_head"], key="cfg_dept_head"); o_phone = c2.text_input("내선 번호", ORG["dept_phone"], key="cfg_dept_phone")
        o_org = c3.text_input("부서명(발신)", ORG["org_name"], key="cfg_org_name"); o_co = c4.text_input("기관명", ORG["company"], key="cfg_company")
        if st.button("조직 설정 저장", key="cfg_org_save", type="primary", icon=":material/save:"):
            db.set_settings({"dept_head": o_head, "dept_phone": o_phone, "org_name": o_org or "지역교육협력부", "company": o_co or "한국교육방송공사"}, USER); st.toast("저장했습니다"); st.rerun()
        st.divider()
        st.markdown("**자주 쓰는 문구** — 검수 화면의 사유 입력칸 옆 '문구'에서 고를 수 있습니다. 확정할 때 쓴 사유는 자동으로 쌓이고, 많이 쓴 순으로 보입니다.")
        pk = st.selectbox("종류", ["사유", "정의", "안내"], key="ph_kind")
        a, b = st.columns([5, 1], vertical_alignment="bottom"); new_t = a.text_input("새 문구", key="ph_new", placeholder="예: 출결 사후 보정 반영(재산출)")
        if b.button("추가", key="ph_add", disabled=not new_t.strip(), icon=":material/add:"): db.add_phrase(pk, new_t, USER); st.rerun()
        for ph in db.list_phrases(pk, 100):
            x, y = st.columns([6, 1], vertical_alignment="center"); x.caption(f"{ph['text']} · {ph['use_count']}회" + (f" · {str(ph['last_used'])[:10]}" if ph.get("last_used") else ""))
            if y.button("지우기", key=f"ph_del_{ph['id']}", type="tertiary", icon=":material/delete:"): db.delete_phrase(ph["id"]); st.rerun()
        st.divider()
        st.markdown("**요청 주체 사전** — 표준 이름과 별칭을 두면 요구서를 읽을 때 같은 기관으로 통일되고, 입력칸에서 자동완성됩니다.")
        q1, q2, q3, q4 = st.columns([2, 3, 1.2, 0.8], vertical_alignment="bottom")
        rq_name = q1.text_input("표준 이름", key="rq_name", placeholder="예: ○○○ 의원실"); rq_al = q2.text_input("별칭(쉼표로 구분)", key="rq_al", placeholder="예: ○○○의원실(교육위원회), ○○○ 의원")
        rq_kind = q3.selectbox("유형", ["의원실", "감사", "교육부", "언론", "기타"], key="rq_kind")
        if q4.button("추가", key="rq_add", disabled=not rq_name.strip(), icon=":material/add:"): db.upsert_requester(rq_name, [a for a in re.split(r"[,，;/]", rq_al) if a.strip()], rq_kind); st.rerun()
        for rq in db.list_requesters():
            x, y = st.columns([6, 1], vertical_alignment="center"); x.caption(f"**{rq['name']}** · {rq.get('kind') or '-'}" + (f" · 별칭: {', '.join(rq['aliases'])}" if rq["aliases"] else ""))
            if y.button("지우기", key=f"rq_del_{rq['id']}", type="tertiary", icon=":material/delete:"): db.delete_requester(rq["id"]); st.rerun()
        if db.list_requesters() and st.button("기록에 표기 통일 적용", key="rq_apply", help="기존 요구서의 요청 주체를 사전 표기로 바꿉니다"): st.toast(f"요구서 {db.apply_requester_canon()}건의 표기를 통일했습니다"); st.rerun()
        st.divider()
        st.markdown("**지표 사전·데이터 카탈로그** — 요구서 표현을 같은 지표로 묶는 동의어와, 지표별 자료 출처·담당. 저장하면 바로 적용됩니다(코드 수정·재배포 불필요).")
        drows = db.get_indicator_dict() or [{"canon": k, "aliases": [a for a in v if a != k], "source": suggest.CATALOG.get(k, ("", ""))[0], "owner": suggest.CATALOG.get(k, ("", ""))[1], "note": ""} for k, v in normalize.CANON.items()]
        dfd = pd.DataFrame([{"지표": r["canon"], "동의어(쉼표로 구분)": ", ".join(r.get("aliases") or []), "자료 출처": r.get("source") or "", "담당": r.get("owner") or "", "비고": r.get("note") or ""} for r in drows])
        ed = st.data_editor(dfd, num_rows="dynamic", width="stretch", key="dict_editor", height=min(60 + 35 * len(dfd), 420))
        d1, d2, d3 = st.columns([1.2, 1.6, 4])
        if d1.button("지표 사전 저장", key="dict_save", type="primary", icon=":material/save:"):
            n = db.save_indicator_dict([{"canon": r["지표"], "aliases": r["동의어(쉼표로 구분)"], "source": r["자료 출처"], "owner": r["담당"], "note": r["비고"]} for r in ed.to_dict("records")], USER)
            normalize.load_from_db(); st.toast(f"지표 {n}개를 저장했습니다"); st.rerun()
        if d2.button("코드 기본값으로 되돌리기", key="dict_reset"): db.save_indicator_dict([]); normalize.reset_defaults(); st.rerun()
        d3.caption(f"지금 적용 중: 지표 {len(normalize.CANON)}개" + (" (화면에서 편집한 사전)" if db.get_indicator_dict() else " (코드 기본값)") + ". 지표를 지우면 그 지표로 분류되던 항목은 '사전에 없음'이 됩니다.")
        st.divider()
        st.markdown("**회신 톤(요청 주체 유형별)** — 문서 제목 접미, 【확인】 줄 표시, AI 초안의 문체 지침, 메일 인사말. 유형은 요청 주체 사전의 유형을 먼저 보고, 없으면 이름(의원·감사·교육부·기자…)으로 추정합니다. 검수 화면에서 건마다 바꿀 수도 있습니다.")
        ov_t = db.get_tone_presets()
        tdf = pd.DataFrame([{"유형": k, "문서 제목": draft.preset_for(k, ov_t)["doc_label"], "확인 줄": draft.preset_for(k, ov_t)["show_confirm"], "문체 지침(AI 초안)": draft.preset_for(k, ov_t)["style"], "메일 인사말": draft.preset_for(k, ov_t)["mail_greeting"]} for k in draft.DEFAULT_PRESETS])
        ted = st.data_editor(tdf, width="stretch", key="tone_editor", disabled=["유형"], hide_index=True,
                             column_config={"확인 줄": st.column_config.CheckboxColumn("확인 줄"), "문체 지침(AI 초안)": st.column_config.TextColumn("문체 지침(AI 초안)", width="large")})
        t1, t2, t3 = st.columns([1.2, 1.6, 4])
        if t1.button("톤 저장", key="tone_save", type="primary", icon=":material/save:"):
            db.save_tone_presets([{"kind": r["유형"], "doc_label": r["문서 제목"], "show_confirm": bool(r["확인 줄"]), "style": r["문체 지침(AI 초안)"], "mail_greeting": r["메일 인사말"]} for r in ted.to_dict("records")]); st.toast("저장했습니다"); st.rerun()
        if t2.button("톤 기본값으로", key="tone_reset"): db.save_tone_presets([]); st.rerun()
        t3.caption("{requester}는 요청 주체 이름으로 바뀝니다. 문체 지침은 AI 초안에만 쓰이고 규칙 초안의 문장은 바뀌지 않습니다.")
        st.divider()
        st.markdown("**회신 문서(답변자료) 양식** — 대화 처리의 HWPX는 '○○ 의원실 답변자료 / 날짜 / 번호 항목 / 【확인 : 부서장 ☎ 내선】 / 본문 / 수치표 / ※ 산출 근거' 틀로 만들어집니다.")
        up_tpl2 = st.file_uploader("대신 쓸 자리표시자 서식(HWPX, {{수신}} {{본문}} {{row.center}} …)", type=["hwpx"], key="tpl_custom_up", help="올리면 답변자료 양식 대신 이 서식에 채웁니다. 비우면 기본 양식.")
        if up_tpl2: st.session_state["tpl_custom"] = up_tpl2.getvalue(); st.caption("자리표시자: " + ", ".join(hwpx_out.placeholders(up_tpl2.getvalue())))
        elif st.session_state.get("tpl_custom") and st.button("자리표시자 서식 쓰지 않기(기본 양식으로)", key="tpl_custom_clear"): st.session_state.pop("tpl_custom"); st.rerun()
        st.divider()
        st.markdown("**추출 품질 점검**: 터미널에서 `python check_llm.py` → `storage/llm_check_날짜.md`.")
    with tab_demo:
        st.markdown("### 백업·복원")
        info = db.db_info(); tb = info["tables"]
        st.caption(f"기록 DB {info['size_kb']:,}KB · 마지막 변경 {info['modified']} · " + " · ".join(f"{k} {v:,}건" for k, v in tb.items() if v is not None)
                   + (" · 클라우드 복제 켜짐(Litestream)" if info["replica"] else " · 복제 없음(이 PC의 파일만)"))
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**내려받기** — 지금 기록 전체를 한 파일로. 월 1회 사내 저장소에 보관하세요.")
            if st.button("백업 파일 만들기", key="bk_make", icon=":material/archive:"): st.session_state["bk_bytes"] = (db.snapshot_bytes(), f"history_{dt.datetime.now().strftime('%Y%m%d_%H%M')}.db")
            if st.session_state.get("bk_bytes"):
                data_, name_ = st.session_state["bk_bytes"]; st.download_button(f"내려받기 ({len(data_) // 1024:,}KB)", data_, file_name=name_, key="bk_dl", type="primary", icon=":material/download:")
            bks = db.list_backups(5)
            if bks:
                age = (dt.datetime.now() - dt.datetime.strptime(bks[0]["modified"], "%Y-%m-%d %H:%M")).days
                (st.warning if age > 30 else st.caption)(f"서버 안 자동 사본 {len(bks)}개 · 최근 {bks[0]['modified']}({age}일 전)" + (" — 30일이 지났습니다. 백업 파일을 내려받아 보관하세요." if age > 30 else ""))
            else: st.caption("서버 안 자동 사본 없음(복원·초기화 때 자동으로 생깁니다).")
        with c2:
            st.markdown("**복원** — 내려받았던 .db 파일로 기록을 되돌립니다. 덮어쓰기 전 현재 기록은 자동으로 사본을 남깁니다.")
            up_db = st.file_uploader("기록 DB 파일(.db)", type=["db", "sqlite", "sqlite3"], key="restore_up")
            if up_db is not None:
                ok_ = st.checkbox("현재 기록을 이 파일 내용으로 덮어씁니다", key="restore_ok")
                if st.button("복원", key="restore_go", type="primary", disabled=not ok_, icon=":material/restore:"):
                    try:
                        r = db.restore_from_bytes(up_db.getvalue(), USER)
                        for k in ("case", "chat", "chat_msgs", "chat_job", "chat_carry"): st.session_state.pop(k, None)
                        st.success("복원했습니다: " + ", ".join(f"{k} {v:,}건" for k, v in r["counts"].items()) + f". 이전 기록 사본: {r['backup']}")
                    except Exception as e: st.error(f"복원하지 못했습니다: {e}")
        st.divider()
        st.markdown("### 시연 데이터")
        st.markdown("심사·시연용. 가상의 센터 12곳 데이터를 기록에 넣습니다. 실제 기록이 있는 DB에는 쓰지 마세요.")
        if st.button("시연 데이터 넣기 (과거 요구서 1건 + 그때 제출한 값 12건)"):
            text = (SAMPLE / "요구서_01_의원실_2026-07-01.txt").read_text(encoding="utf-8")
            res = extract.rule_based(text); res["items"] = normalize.normalize_items(res["items"])
            rid = db.add_request(res["requester"], res["received_date"], res["due_date"], res["title"], text, "요구서_01_의원실_2026-07-01.txt", res["items"])
            vdf = compare.load_values(SAMPLE / "등원율_2026-06-30기준_7월제출본.xlsx")
            db.add_submission(rid, "2026-07-08", USER, "등원율_2026-06-30기준_7월제출본.xlsx", "confirmed", "시연 데이터", vdf.to_dict("records"))
            st.success(f"넣었습니다. 요구서 #{rid}. '새 요구서 처리'에서 sample_data/새요구서_의원실_2026-09-15.txt를 올려 보세요.")
        st.caption("시연 파일 선택칸까지 화면에 보이게 하려면 `run_demo.bat`으로 실행(APP_DEMO=1).")
        st.divider()
        sure = st.checkbox("기록 전체를 삭제하는 데 동의합니다(되돌릴 수 없음)")
        if st.button("기록 전체 삭제", disabled=not sure):
            db.reset(); st.session_state.clear(); st.rerun()
        if st.button("샘플 데이터·서식 파일 다시 만들기"):
            import importlib, make_sample_data
            importlib.reload(make_sample_data); st.success("샘플 데이터 생성 완료")
            try:
                import make_template; make_template.main(); st.success("서식 생성 완료")
            except FileNotFoundError:
                st.info("서식 원본이 상위 폴더에 없어 templates/의 기존 샘플을 그대로 씁니다.")

PAGES = {"홈": page_chat, "새 요구서 처리": page_process, "검토·승인": page_review, "과거 답변 등록": page_register, "지표 데이터": page_data, "참고 문서": page_refdocs,
         "기록": page_records, "현황": page_status, "설정": page_settings}
if not HAS_API and page != "설정" and not st.session_state.get("ai_banner_seen"):      # 큰 배너는 세션에서 한 번만, 이후는 사이드바 표시와 홈의 한 줄
    ui.ai_banner(lambda: go("설정")); st.session_state["ai_banner_seen"] = True
PAGES[page]()

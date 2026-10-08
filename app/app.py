"""EBS 대외 요구자료 대응 에이전트 — 프로토타입
실행: python start.py  (또는 streamlit run app.py). 시연 파일 선택칸까지 보이게 하려면 APP_DEMO=1 (run_demo.bat).

화면 구성(사람이 하는 일 순서대로):
  홈              — 할 일·현황 한눈에, 세 가지 작업으로 바로 이동
  새 요구서 처리  — 1 요구서 읽기 → 2 수치 맞춰 보기 → 3 회신 초안 (한 흐름, 단계 표시)
  검토·승인       — 팀장이 초안을 승인·반려
  과거 답변 등록  — 예전에 낸 요구서와 그때 보낸 값을 기억에 넣기
  이력 조회 · 현황 · 이력에 묻기 · 설정
역할 경계: AI는 읽기·문안·점검·기억 조회만. 수치 계산·대조는 코드, 사유·확정은 담당자."""
import os, io, sys, subprocess, datetime as dt, zipfile, json, threading, queue, time
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
import db, extract, compare, pii, docread, normalize, search, suggest, draft, hwpx_out, llm, assist, history_qa, tabular, ui

st.set_page_config(page_title="대외 요구자료 대응", page_icon="📁", layout="wide")
ui.inject()
if os.environ.get("APP_PASSWORD") and not st.session_state.get("authed"):          # 외부 서버에 올릴 때의 최소 잠금. 사내망 전용이면 비워 둔다
    import hmac
    st.markdown("# 대외 요구자료 대응"); st.caption("접속 비밀번호를 입력하세요.")
    pw = st.text_input("비밀번호", type="password", label_visibility="collapsed")
    if pw and hmac.compare_digest(pw, os.environ["APP_PASSWORD"]): st.session_state["authed"] = True; st.rerun()
    elif pw: st.error("비밀번호가 맞지 않습니다.")
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

NAV = [("업무", [("홈", "home"), ("새 요구서 처리", "description"), ("검토·승인", "task_alt")]),
       ("기록", [("지표 데이터", "database"), ("과거 답변 등록", "library_add"), ("이력 조회", "search"), ("현황", "monitoring"), ("이력에 묻기", "forum")]),
       ("", [("설정", "settings")])]
PAGE_NAMES = [n for _, items in NAV for n, _ in items]

def go(page: str, step: int | None = None):
    """다음 실행에서 메뉴·단계를 바꾼다."""
    st.session_state["_goto"] = (page, step); st.rerun()

if "_goto" in st.session_state:
    _p, _s = st.session_state.pop("_goto")
    st.session_state["nav"] = _p
    if _s is not None: st.session_state["step"] = _s
page = st.session_state.get("nav") if st.session_state.get("nav") in PAGE_NAMES else "홈"

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
                (f"이 세션 호출 {u['calls']}회 · 추정 ${u['cost_usd']:.3f}" if u else "") + ("<br>시연 모드(샘플 파일 선택칸 표시)" if DEMO else ""))

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
    base_date = c3.text_input("3. 기준일 (필수)", d_default, placeholder="2026-06-30", key=f"{key}_bd",
                              help="이 표의 값이 '언제 기준' 수치인지. 표 제목·주석에 '… 기준' 날짜가 있으면 자동으로 채워집니다. 지난번 값과 같은 기준일이어야 맞춰 볼 수 있습니다.")
    if not base_date.strip(): c3.error("기준일을 넣어야 다음으로 넘어갑니다.")
    names = dict(sug["value_cols"]) if sug and sug.get("value_cols") else {}
    ind_df = pd.DataFrame([{"열": label(c), "지표명": names.get(c) or tabular.default_indicator(label(c))} for c in value_cols])
    if len(ind_df):
        ind_df = st.data_editor(ind_df, hide_index=True, width="stretch", key=f"{key}_ind", disabled=["열"],
                                column_config={"지표명": st.column_config.TextColumn("저장될 지표명 (사전에 있으면 정규 지표명이 기본값)")})
    drop_totals = st.checkbox("합계·소계·평균 행 제외", value=True, key=f"{key}_dt", help=f"제외 대상 행: {[grid.row_offset + r for r in info.total_rows] or '없음'}")
    try:
        indicators = {c: str(n).strip() for c, n in zip(value_cols, ind_df["지표명"].tolist(), strict=True)} if len(ind_df) else {}
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
    ups = st.file_uploader(f"{what} 파일 — 엑셀·CSV, 회신 공문(hwp·hwpx·docx·pdf). 여러 개를 한 번에 올려도 됩니다",
                           type=["xlsx", "xlsm", "csv", "hwp", "hwpx", "docx", "pdf"], accept_multiple_files=True, key=f"{key}_files",
                           help="긴 형식(지표명·센터명·기준일·값)은 그대로, 실적표 서식(제목 행·병합 머리글·합계 행)은 열 확인을 거쳐 읽습니다. 한글 HWP(5.0)는 자동 변환(암호·배포용 문서 제외). 올린 파일마다 결과가 아래에 쌓이고, 전부 합쳐 저장됩니다.")
    if DEMO and demo_files:
        for pick in st.multiselect("시연 파일", demo_files, default=demo_files if len(demo_files) == 1 else None, format_func=lambda p: p.name, key=f"{key}_demo"):
            with st.container(border=True):
                st.markdown(f"**{pick.name}**")
                df = _grid_to_values(tabular.read_grid(pick), f"{key}_demo_{pick.name}", base_date_default=base_date_default) if pick.name.startswith("실적표_") else compare.load_values(pick)
                if df is not None: parts.append(df); names.append(pick.name); st.caption(f"읽음: {len(df)}건")
    for up in ups or []:
        with st.container(border=True):
            st.markdown(f"**{up.name}**  \n<span class='small-muted'>{up.size / 1024:.0f} KB</span>", unsafe_allow_html=True)
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

def run_ai(label: str, fn, hint: str = "", rules_label: str | None = None):
    """AI 호출을 진행 상자 안에서 실행한다. 호출은 작업 스레드에서 돌고, 화면은 0.5초마다 경과 시간을 갱신하며
    llm이 보내는 단계 글(모델·입력 크기·응답 시간·잘림 재시도·도구 호출)을 줄줄이 보여 준다. 끝나면 상자가 접히고 걸린 시간이 남는다.
    AI가 없으면 규칙 기반으로 바로 실행(짧으니 스피너만). fn은 Streamlit 요소를 만지면 안 된다(결과만 반환)."""
    if not HAS_API:
        with st.spinner(rules_label or f"{label} (규칙 기반)"): return fn()
    box = st.status(f"{label} — {llm.model_label()} 준비", expanded=True)
    with box:
        timer = st.empty(); log_box = st.container()
    q: queue.Queue = queue.Queue()
    cfg = (st.session_state["llm_cfg"], st.session_state["llm_state"], st.session_state["llm_log"])
    def worker():
        llm.configure(*cfg); llm.set_progress(lambda m: q.put(m))
        try: q.put(("done", fn()))
        except BaseException as e: q.put(("err", e))
        finally: llm.set_progress(None)
    threading.Thread(target=worker, daemon=True).start()
    t0, last, outcome = time.time(), "", None
    while outcome is None:
        try: item = q.get(timeout=0.5)
        except queue.Empty: item = None
        if isinstance(item, tuple): outcome = item
        elif isinstance(item, str):
            last = item; log_box.write(f"{dt.datetime.now().strftime('%H:%M:%S')}  {item}"); box.update(label=f"{label} — {item[:90]}", state="running", expanded=True)
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
    res["items"] = normalize.normalize_items(res.get("items") or [])
    res["items"] = normalize.normalize_llm(res["items"])
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

# ================= 홈 =================
def page_home():
    ui.page_title(f"{USER}님, 무엇을 할까요", "", "홈")
    c1, c2, c3 = st.columns(3)
    if ui.action_card(c1, "새 요구서 처리", "요구서 읽기 → 수치 맞춰 보기 → 회신 초안", "시작", "home_new", "upload_file", primary=True): go("새 요구서 처리", 1)
    if ui.action_card(c2, "과거 답변 등록", "예전 요구서와 그때 보낸 값을 기억에 저장", "등록", "home_reg", "library_add"): go("과거 답변 등록")
    if ui.action_card(c3, "기록에 묻기", "언제, 누구에게, 얼마로 냈는지 바로 찾기", "질문", "home_ask", "forum"): go("이력에 묻기")
    ov = db.request_overview(); pending = db.list_drafts("review_requested")
    st.markdown("## 현황")
    if not ov:
        st.info("아직 기록이 없습니다. 첫 작업으로 '과거 답변 등록'에서 예전에 낸 요구서 하나를 넣어 보세요. 시연용 데이터가 필요하면 설정 → 시연·초기화.")
        return
    df = pd.DataFrame(ov); today = pd.Timestamp(dt.date.today())
    df["D-day"] = (pd.to_datetime(df["due_date"], errors="coerce") - today).dt.days
    df["상태"] = df.apply(lambda r: "확정 제출" if r["n_confirmed"] > 0 else ("기한 경과" if pd.notna(r["D-day"]) and r["D-day"] < 0 else "진행 중"), axis=1)
    live = df[df["상태"] == "진행 중"]; soon = live[live["D-day"].between(0, 3)]
    k1, k2, k3, k4 = st.columns(4)
    ui.kpi(k1, len(df), "등록된 요구서", "ok"); ui.kpi(k2, len(live), "진행 중", "ok" if len(live) else ""); ui.kpi(k3, len(soon), "기한 3일 이내", "warn" if len(soon) else ""); ui.kpi(k4, len(pending), "팀장 검토 대기", "warn" if pending else "")
    df["D-day"] = df["D-day"].astype("object"); df.loc[df["상태"] == "확정 제출", "D-day"] = ""      # 끝난 건은 D-day를 비운다
    show = df.sort_values(["상태", "D-day"], ascending=[False, True]).head(8)
    st.dataframe(show.rename(columns={"id": "번호", "requester": "요청 주체", "received_date": "접수일", "due_date": "제출기한", "title": "제목", "n_confirmed": "확정 제출본"})
                 [["번호", "상태", "D-day", "요청 주체", "접수일", "제출기한", "제목", "확정 제출본"]], width="stretch", hide_index=True)
    if pending and st.button("검토 대기 초안 보기"): go("검토·승인")

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
            st.dataframe(vdf[VAL_COLS].rename(columns=VAL_KO), height=200, width="stretch")
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
            st.info("아직 없습니다. 왼쪽에서 집계 파일을 올려 저장하면 지표 × 기준일 표가 여기 나타납니다.")
        else:
            st.dataframe(pd.DataFrame(cov).rename(columns={"indicator": "지표", "base_date": "기준일", "n_centers": "건수(센터)", "loaded_at": "최근 적재", "source": "출처"}), width="stretch", hide_index=True, height=min(60 + 35 * len(cov), 420))
            st.caption(f"지표 {len({c['indicator'] for c in cov})}개 · 기준일 {len({c['base_date'] for c in cov})}개 · 값 {db.data_count()}건")
        with st.expander("적재 묶음 (지우기)"):
            for b in db.list_data_batches():
                a, d = st.columns([5, 1])
                a.caption(f"#{b['id']} · {b['loaded_at'][:16]} · {b['loaded_by']} · {b['source']} · {b['n_rows']}건(남은 {b['n_live']}건)" + (f" · {b['note']}" if b['note'] else ""))
                if d.button("지우기", key=f"data_del_{b['id']}", type="tertiary", icon=":material/delete:"):
                    db.delete_data_batch(b["id"]); st.rerun()
        st.caption("[계획] 사내 집계 시스템·DB와 직접 연결(읽기 전용 조회)하면 이 화면에 올리는 일도 없어집니다. 연결 방식은 전산 부서와 협의 필요 [확인 필요].")

# ================= 과거 답변 등록 =================
def page_register():
    ui.page_title("과거 답변 등록", "예전 요구서와 그때 보낸 값을 넣어 두면, 다음에 같은 수치를 물을 때 '언제 누구에게 얼마로 답했는지'가 자동으로 붙습니다.", "기록")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("### 1. 그때 받은 요구서")
        req_text, req_name = request_input("reg", "요구서_*.txt")
        if st.button("요구 항목 읽기", type="primary", disabled=not req_text):
            res, how = analyze_with_status(req_text)
            st.session_state.update(reg_extract=res, reg_how=how, reg_text=req_text, reg_name=req_name)
    with col2:
        st.markdown("### 2. 그때 보낸 값")
        st.caption("집계 엑셀, 그때 보낸 회신 공문(hwp·hwpx·docx·pdf), 직접 입력 중 무엇이든 됩니다. 파일 종류를 가리지 않고 여러 개를 한 번에 올리면 전부 합쳐 저장됩니다.")
        vdf, vname = value_sources("reg_val", "그때 보낸 값", demo_files=sorted(SAMPLE.glob("등원율_*.xlsx")) + sorted(SAMPLE.glob("실적표_*.xlsx")))
        if vdf is not None:
            st.dataframe(vdf[VAL_COLS].rename(columns=VAL_KO), height=200, width="stretch")
            srcs = list(dict.fromkeys(f"{f}{' / ' + str(sh) if sh else ''}" for f, sh in zip(vdf["source_file"], vdf["source_sheet"], strict=True)))
            st.caption(f"저장될 값 {len(vdf)}건 · 출처 {len(srcs)}개: {', '.join(srcs[:4])}" + (" …" if len(srcs) > 4 else ""))
    if "reg_extract" in st.session_state:
        res = st.session_state["reg_extract"]
        st.markdown("### 3. 확인하고 저장")
        st.caption(f"읽은 방식: {st.session_state['reg_how']} · 틀린 곳은 바로 고치면 됩니다.")
        c1, c2, c3, c4 = st.columns(4)
        requester = c1.text_input("요청 주체", res.get("requester") or "")
        received = c2.text_input("접수일", res.get("received_date") or "")
        due = c3.text_input("제출기한", res.get("due_date") or "")
        title = c4.text_input("제목", res.get("title") or "")
        items_df = pd.DataFrame(res.get("items") or [], columns=extract.ITEM_FIELDS)
        items_df = st.data_editor(items_df, num_rows="dynamic", width="stretch",
                                  column_config={"item_text": "항목 원문", "indicator": st.column_config.SelectboxColumn("지표명(정규화)", options=list(normalize.CANON), required=False),
                                                 "base_date": "기준일", "period": "기간", "unit": "단위"})
        if res.get("_error"): st.warning(f"AI 호출 오류로 규칙 기반 결과입니다: {res['_error']}")
        submitted_date = st.text_input("그때 제출한 날짜", due or "")
        if vdf is None: st.info("보낸 값 없이 요구서만 저장할 수도 있습니다(나중에 대조 기준으로는 쓰이지 않음).")
        if st.button("기억에 저장", type="primary"):
            rid = db.add_request(requester, received, due, title, st.session_state["reg_text"], st.session_state["reg_name"], items_df.to_dict("records"))
            vals = vdf.to_dict("records") if vdf is not None else []
            sid = db.add_submission(rid, submitted_date, USER, vname, "confirmed", "과거 제출본 등록", vals)
            st.session_state["reg_last"] = (rid, sid, len(vals))
            del st.session_state["reg_extract"]; st.rerun()
    if st.session_state.get("reg_last"):
        rid, sid, n = st.session_state["reg_last"]
        c1, c2 = st.columns([4, 1.4])
        c1.success(f"저장했습니다. 요구서 #{rid}, 제출본 #{sid}, 값 {n}건. 다음에 같은 지표·기준일을 물으면 자동으로 찾아 줍니다.")
        if c2.button("잘못 넣었어요 — 되돌리기", key="reg_undo", icon=":material/undo:", help="방금 저장한 요구서·값을 지웁니다. 나중에 고치려면 이력 조회에서 수정·삭제할 수 있습니다."):
            db.delete_request(rid); st.session_state.pop("reg_last"); st.toast(f"요구서 #{rid} 삭제됨"); st.rerun()

# ================= 새 요구서 처리 (1→2→3) =================
def page_process():
    step = st.session_state.setdefault("step", 1)
    done = 2 if st.session_state.get("last_submission") else (1 if st.session_state.get("new_registered") else 0)
    ui.page_title("새 요구서 처리", "", "업무")
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
            st.markdown(f"→ **{sg['판단']}**  \n<span class='small-muted'>출처 {sg['데이터 출처']} · 담당 {sg['담당']} · 과거 제출 {sg['과거 제출']}</span>", unsafe_allow_html=True)
            if it.get("indicator") and it.get("base_date") and (dv := db.data_values_for(it["indicator"], it["base_date"])):
                st.markdown(f"지표 데이터에 있음: **{len(dv)}건** · 적재 {dv[0]['loaded_at'][:10]} · {dv[0]['source']} → 2단계에서 자동으로 채워짐")
            if it.get("indicator") and it.get("base_date"):
                past = db.past_values_for(it["indicator"], it["base_date"])
                subs = {}
                for p_ in past: subs.setdefault((p_["submitted_date"], p_["requester"], p_["request_title"], p_["file_name"]), []).append(p_)
                for (sd, rq, tt, fn), rows in subs.items():
                    st.markdown(f"전에 낸 값: **{sd}** · {rq} · {tt} · {fn} · {len(rows)}건  \n<span class='small-muted'>근거: 정의 {rows[0]['definition']} / 집계기간 {rows[0]['calc_period']} / 추출시점 {rows[0]['extract_date']} / 원자료 {rows[0]['source_version']}</span>", unsafe_allow_html=True)
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
    st.caption("이번에 낼 수치를 지난번에 보낸 값과 맞춰 봅니다. 맞춰 보는 계산은 코드가 하고, 차이가 난 이유는 담당자가 적습니다.")
    reqs = db.list_requests(); subs = db.list_submissions()
    if not reqs:
        st.warning("등록된 요구서가 없습니다. 1단계에서 요구서를 등록하세요."); return
    tgt_default = next((i for i, r in enumerate(reqs) if r["id"] == st.session_state.get("target_request")), 0)
    target = st.selectbox("이번에 회신할 요구서", reqs, index=tgt_default, format_func=req_label)
    items = db.get_items(target["id"])
    # 1) 기록에서 먼저 찾는다: 항목의 지표·기준일이 이미 낸 값과 같으면 그 값을 쓰면 된다(새로 만들 필요 없음)
    found, missing, hit_sids, where = {}, [], {}, {}
    for it in items:
        k = (it.get("indicator"), it.get("base_date"))
        if k[0] and k[1]:
            data = db.data_values_for(*k)                       # 1순위: 미리 넣어 둔 지표 데이터(부서의 현재 집계)
            if data:
                found[k] = (data, None); where[k] = f"지표 데이터 · 적재 {data[0]['loaded_at'][:10]} · {data[0]['source']} · {len(data)}건"
                for p_ in db.past_values_for(*k)[:1]: hit_sids[p_["submission_id"]] = hit_sids.get(p_["submission_id"], 0) + 1
                continue
            past = db.past_values_for(*k)                         # 2순위: 과거에 낸 값
            if past:
                latest_sid = past[0]["submission_id"]; rows = [v for v in past if v["submission_id"] == latest_sid]
                found[k] = (rows, past[0]); where[k] = f"과거 제출본 #{latest_sid} · {past[0]['submitted_date']} · {past[0]['requester']} · {len(rows)}건"
                hit_sids[latest_sid] = hit_sids.get(latest_sid, 0) + 1; continue
        missing.append(it)
    hist_key = ("cmp_hist", target["id"])
    with st.container(border=True):
        st.markdown("**기록에서 찾기** — 항목의 지표·기준일이 '지표 데이터'에 있으면 그 값을, 없으면 과거에 낸 값을 가져옵니다. 새로 만들 것은 아래에만 올리면 됩니다.")
        if items:
            rows_tbl = [{"항목": it["item_text"], "지표": it.get("indicator") or "-", "기준일": it.get("base_date") or "-",
                         "어디에 있나": where.get((it.get("indicator"), it.get("base_date")),
                                             "기준일이 없어 찾을 수 없음" if not it.get("base_date") else ("지표를 모름" if not it.get("indicator") else
                                             (f"없음 → 새로 산출 (지표 데이터에 있는 기준일: {', '.join(db.data_dates_for(it['indicator'])[:3])})" if db.data_dates_for(it["indicator"]) else "없음 → 새로 산출")))} for it in items]
            st.dataframe(pd.DataFrame(rows_tbl), width="stretch", hide_index=True)
        else: st.caption("이 요구서에 항목이 없습니다. 이력 조회에서 항목을 넣거나 1단계에서 다시 읽으세요.")
        b1, b2, b3 = st.columns([2.4, 1.3, 2.6])
        n_found = sum(len(v[0]) for v in found.values())
        if found and b1.button(f"기록의 값 가져오기 ({len(found)}개 항목 · {n_found}건)", type="primary", key="cmp_pull", icon=":material/history:"):
            parts = []
            for rows, meta in found.values():
                df = pd.DataFrame(rows)[VAL_COLS].copy(); df["source_sheet"] = ""; df["source_row"] = None
                df["source_file"] = f"기록 제출본 #{meta['submission_id']} ({meta['submitted_date']} {meta['requester']})" if meta else f"지표 데이터 ({rows[0]['source']}, 적재 {rows[0]['loaded_at'][:10]})"
                parts.append(df)
            st.session_state[hist_key] = pd.concat(parts, ignore_index=True); st.rerun()
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
        if st.session_state.get(hist_key) is not None: st.caption(f"가져온 값 {len(st.session_state[hist_key])}건. 아래에 파일을 올리면 같은 지표·센터·기준일은 올린 값이 우선합니다.")
    # 2) 지난번 값: 기록에서 가장 많이 맞은 제출본을 기본 선택
    c1, c2 = st.columns(2)
    with c2:
        st.markdown("**지난번에 보낸 값**")
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
    if sid: st.caption(f"확정 수치: 제출본 #{sid} · {len(values)}건")
    else: st.warning("이 요구서에 확정된 수치가 없습니다. 2단계에서 먼저 확정하세요. 지금 만들면 수치 자리는 [확인 필요]가 됩니다.")
    reasons_raw = {}
    for _, v in values.iterrows():
        for rr in db.reasons_for(v["indicator"], v["center"], v["base_date"]):
            reasons_raw[(v["indicator"], v["center"], v["base_date"])] = rr["reason"]; break
    prov = {k: values.iloc[0].get(k) for k in ("definition", "calc_period", "extract_date", "source_version")} if len(values) else {}
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
    elif n_flag: st.info(f"충족 {n_ok} · [확인 필요] {n_flag} — 확정 수치가 없는 항목은 [확인 필요]로 남아 있습니다.")
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
    fill = {"수신": req["requester"] or "", "발신": "EBS 지역교육협력부", "제목": d["제목"],
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
        vb = io.BytesIO(); pii.excel_safe(values).to_excel(vb, index=False); zf.writestr("확정수치.xlsx", vb.getvalue())
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
        with st.expander(f"초안 #{d['id']} · {ko(d['status'])} · {d['requester']} — {d['request_title']} (기한 {d['due_date']}) · {d['created_at'][:16]}", expanded=d["status"] == "review_requested"):
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

# ================= 이력 조회 =================
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
        requester = c1.text_input("요청 주체", r["requester"] or "", key=f"ed_rq_{r['id']}")
        received = c2.text_input("접수일", r["received_date"] or "", key=f"ed_rc_{r['id']}")
        due = c3.text_input("제출기한", r["due_date"] or "", key=f"ed_du_{r['id']}")
        title = c4.text_input("제목", r["title"] or "", key=f"ed_ti_{r['id']}")
        its = pd.DataFrame(db.get_items(r["id"]), columns=["id", "request_id", "seq"] + extract.ITEM_FIELDS)[extract.ITEM_FIELDS]
        its = st.data_editor(its, num_rows="dynamic", width="stretch", key=f"ed_items_{r['id']}",
                             column_config={"item_text": "항목 원문", "indicator": st.column_config.SelectboxColumn("지표명(정규화)", options=list(normalize.CANON), required=False),
                                            "base_date": "기준일", "period": "기간", "unit": "단위"})
        b1, b2, _ = st.columns([1, 1, 4])
        if b1.button("저장", key=f"ed_save_{r['id']}", type="primary"):
            db.update_request(r["id"], requester, received, due, title); n = db.replace_items(r["id"], its.to_dict("records"))
            st.session_state.pop(f"editing_{r['id']}", None); st.toast(f"요구서 #{r['id']} 저장 — 항목 {n}건"); st.rerun()
        if b2.button("취소", key=f"ed_cancel_{r['id']}"):
            st.session_state.pop(f"editing_{r['id']}", None); st.rerun()

def page_history():
    ui.page_title("이력 조회", "요구서마다 항목 → 보낸 값 → 사유 → 초안 → 승인이 한 줄로 이어져 있습니다. 잘못 넣은 것은 여기서 고치거나 지웁니다.", "기록")
    reqs = db.list_requests()
    kw = st.text_input("찾기 (요청 주체·제목·항목·본문)", placeholder="예: 등원율, 의원실, 2026-06-30")
    if kw.strip():
        ids = {r["id"] for r in db.search_requests(kw.strip(), limit=200)}
        reqs = [r for r in reqs if r["id"] in ids]
        st.caption(f"검색 결과 {len(reqs)}건")
    if not reqs: st.info("등록된 요구서가 없습니다." if not kw.strip() else "검색 결과가 없습니다.")
    inv = {v: k for k, v in VAL_KO.items()}
    for r in reqs:
        rid = r["id"]
        with st.expander(f"#{rid} {r['received_date']} · {r['requester']} · {r['title']} (기한 {r['due_date']})"):
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
                    st.markdown(f"**보낸 값 #{sid}** · {s_['submitted_date']} · {ko(s_['status'])} · {s_['file_name']} · {s_['note'] or ''}")
                    vals = pd.DataFrame(db.get_values(sid))
                    if len(vals):
                        ed = st.data_editor(vals.drop(columns=["id", "submission_id"]).rename(columns=VAL_KO), num_rows="dynamic", width="stretch", height=min(60 + 35 * len(vals), 320), key=f"vals_{sid}")
                        c1, c2 = st.columns([1, 5])
                        if c1.button("값 저장", key=f"vals_save_{sid}", icon=":material/save:", help="표에서 고친 값·근거를 이 제출본에 그대로 저장합니다. 지표·센터·기준일·값이 빈 행은 버립니다."):
                            n = db.replace_values(sid, ed.rename(columns=inv).to_dict("records")); st.toast(f"제출본 #{sid} 값 {n}건 저장"); st.rerun()
                        c2.caption("표에서 바로 고친 뒤 '값 저장'. 행 삭제는 행을 선택하고 Delete.")
                    else: st.caption("값 없음(요구서만 저장된 제출본)")
                    if confirm_delete(f"보낸 값 #{sid}", f"del_sub_{sid}", "값·차이 사유·이 제출본으로 만든 초안이 함께 지워집니다"):
                        info = db.delete_submission(sid); st.toast(f"보낸 값 #{sid} 삭제 — 값 {info['values']}건, 초안 {info['drafts']}건"); st.rerun()
            for d in [x for x in db.list_drafts() if x["request_id"] == rid]:
                c1, c2 = st.columns([5, 1])
                c1.markdown(f"초안 #{d['id']} · {ko(d['status'])} · {d['hwpx_name'] or '-'} · {d['created_at'][:16]}")
                if c2.button("초안 삭제", key=f"del_draft_{d['id']}", type="tertiary", icon=":material/delete:"):
                    db.delete_draft(d["id"]); st.toast(f"초안 #{d['id']} 삭제"); st.rerun()
            st.divider()
            if confirm_delete(f"요구서 #{rid}", f"del_req_{rid}", "항목·보낸 값·차이 사유·초안·검토 기록이 전부 지워지며 되돌릴 수 없습니다"):
                info = db.delete_request(rid); st.toast(f"요구서 #{rid} 삭제 — 제출본 {info['submissions']}건, 초안 {info['drafts']}건"); st.rerun()

# ================= 현황 =================
def page_status():
    ui.page_title("현황", "기한이 가까운 요구, 반복해서 들어오는 지표, 관리대장.", "기록")
    ov = db.request_overview()
    if not ov: st.info("등록된 요구서가 없습니다."); return
    df = pd.DataFrame(ov); today = pd.Timestamp(dt.date.today())
    df["D-day"] = (pd.to_datetime(df["due_date"], errors="coerce") - today).dt.days
    df["상태"] = df.apply(lambda r: "확정 제출" if r["n_confirmed"] > 0 else ("기한 경과" if pd.notna(r["D-day"]) and r["D-day"] < 0 else "진행 중"), axis=1)
    show = df.rename(columns={"id": "번호", "requester": "요청 주체", "received_date": "접수일", "due_date": "제출기한", "title": "제목", "n_items": "항목 수", "n_confirmed": "확정 제출본", "last_submitted": "최근 제출일"})
    soon = (df["상태"] == "진행 중") & df["D-day"].between(0, 3)
    c1, c2, c3, c4 = st.columns(4)
    ui.kpi(c1, len(df), "요구서"); ui.kpi(c2, int((df["상태"] == "진행 중").sum()), "진행 중"); ui.kpi(c3, int(soon.sum()), "기한 3일 이내", "warn" if soon.sum() else ""); ui.kpi(c4, int((df["상태"] == "기한 경과").sum()), "기한 경과(미제출)", "bad" if (df["상태"] == "기한 경과").sum() else "")
    view = show[["번호", "상태", "D-day", "요청 주체", "접수일", "제출기한", "제목", "항목 수", "확정 제출본", "최근 제출일"]]
    def _row_style(r):
        if r["상태"] == "기한 경과": return ["background-color: #FDE8E8"] * len(r)
        if r["상태"] == "진행 중" and pd.notna(r["D-day"]) and r["D-day"] <= 3: return ["background-color: #FFF4D6"] * len(r)
        return [""] * len(r)
    st.dataframe(view.style.apply(_row_style, axis=1), width="stretch", hide_index=True)
    st.caption("노랑: 진행 중이며 기한 3일 이내 · 빨강: 기한 경과(미제출)")
    rows = [{"요구번호": r["id"], "요청 주체": r["requester"], "접수일": r["received_date"], "제출기한": r["due_date"], "제목": r["title"], "항목": it["item_text"], "지표": it["indicator"], "기준일": it["base_date"], "단위": it["unit"], "확정 제출본 수": r["n_confirmed"], "최근 제출일": r["last_submitted"]} for r in ov for it in db.get_items(r["id"])]
    lb = io.BytesIO(); pii.excel_safe(pd.DataFrame(rows)).to_excel(lb, index=False)
    st.download_button("관리대장 엑셀 내보내기", lb.getvalue(), file_name=f"요구자료_관리대장_{dt.date.today()}.xlsx")
    by_req, by_ind = db.requester_stats()
    c1, c2 = st.columns(2)
    with c1: st.markdown("### 요청 주체별"); st.dataframe(pd.DataFrame(by_req).rename(columns={"requester": "요청 주체", "n_requests": "요구 건수", "n_items": "항목 수", "first_date": "최초", "last_date": "최근"}), width="stretch", hide_index=True)
    with c2: st.markdown("### 반복 요구 지표"); st.dataframe(pd.DataFrame(by_ind).rename(columns={"indicator": "지표", "n_times": "요구 횟수", "n_requesters": "요청 주체 수", "base_dates": "기준일들"}), width="stretch", hide_index=True); st.caption("여러 번 요구된 지표는 미리 산출해 두면 좋습니다.")

# ================= 이력에 묻기 =================
def page_ask():
    ui.page_title("이력에 묻기", "말로 물으면 기록을 찾아 근거(요구번호·제출일·기관)와 함께 답합니다. 평균·증감 같은 계산은 하지 않습니다."
                  + ("" if HAS_API else " 지금은 AI 연결이 없어 키워드 검색으로 동작합니다."), "기록")
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
                st.markdown(res["text"]); st.caption(res.get("how", ""))
                with st.expander(f"근거 기록 — 조회 {len(res['trace'])}회"):
                    for t in res["trace"]:
                        st.markdown(f"`{t['tool']}` {json.dumps(t['input'], ensure_ascii=False)}" + (f" → {t['rows']}건" if t.get("rows") is not None else ""))
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
    tab_ai, tab_data, tab_demo = st.tabs(["AI 연결", "서식·사전", "시연·초기화"])
    with tab_ai:
        st.markdown("키는 이 브라우저 세션에만 보관되고 파일에 저장되지 않습니다. 탭을 닫으면 사라집니다. 혼자 쓰는 PC라면 `set_key.bat`으로 한 번 저장해 두면 매번 넣지 않아도 됩니다.")
        cfg = st.session_state["llm_cfg"]
        pnames = providers.names()
        prov_name = st.selectbox("공급자", pnames, index=pnames.index(cfg.get("provider")) if cfg.get("provider") in pnames else 0,
                                 format_func=lambda n: f"{providers.PROVIDERS[n].label} ({n})")
        pcls = providers.PROVIDERS[prov_name]
        new_cfg = {"provider": prov_name}
        for f in pcls.fields:
            env_set = bool(os.environ.get(f.get("env", ""), ""))
            hint = " · 이 PC 환경변수에 값이 있어 비워도 됨" if env_set else ""
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
        if c2.button("키 지우기"):
            cfg.clear(); st.session_state["llm_state"] = {}; st.session_state["model_list"] = []; st.rerun()
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
        st.markdown("**지표 동의어 사전**: `normalize.py`의 CANON. 요구서의 표현이 달라도 같은 지표로 묶는 기준입니다. **데이터 카탈로그**: `suggest.py`의 CATALOG.")
        st.markdown("**추출 품질 점검**: 터미널에서 `python check_llm.py` → `storage/llm_check_날짜.md`.")
    with tab_demo:
        st.markdown("심사·시연용. 가상의 센터 12곳 데이터를 기억에 넣습니다. 실제 기록이 있는 DB에는 쓰지 마세요.")
        if st.button("시연 데이터 넣기 (과거 요구서 1건 + 그때 보낸 값 12건)"):
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

PAGES = {"홈": page_home, "새 요구서 처리": page_process, "검토·승인": page_review, "과거 답변 등록": page_register, "지표 데이터": page_data,
         "이력 조회": page_history, "현황": page_status, "이력에 묻기": page_ask, "설정": page_settings}
if not HAS_API and page != "설정": ui.ai_banner(lambda: go("설정"))
PAGES[page]()

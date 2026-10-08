"""화면 공통: 실행 문맥(ctx)·경로 상수·조회 캐시·AI 진행 상자·입력 도우미. 화면 모듈(screens/*.py)은 여기서 가져다 쓴다.
ctx는 app.py가 실행마다 채운다(접속자 이름·검토자·AI 연결 여부·시연 모드·조직 설정·빌드 표시)."""
import subprocess, datetime as dt, json, threading, queue, time
from pathlib import Path
import pandas as pd
import streamlit as st
import dashboard_import, db, match, extract, compare, docread, normalize, hwpx_out, llm, tabular, ui

HERE = Path(__file__).resolve().parents[1]
SAMPLE = HERE / "sample_data"
TPL_DIR = HERE / "templates"
OUT_DIR = db.STORAGE_DIR / "out"; OUT_DIR.mkdir(parents=True, exist_ok=True)      # APP_STORAGE_DIR 아래(기록 DB·백업과 같은 곳)

def out_file(name: str | None):
    """회신 파일 이름 → OUT_DIR 안의 경로. 기록 DB 값이라도 폴더 밖('../…', 절대경로)을 가리키면 None(경로 탈출 차단)."""
    if not name or name != Path(name).name: return None
    f = (OUT_DIR / name).resolve()
    try: return f if f.is_relative_to(OUT_DIR.resolve()) else None
    except ValueError: return None

# ---- 접속 비밀번호 잠금(무차별 대입 완화). app.py는 매 실행 다시 돌지만 이 모듈은 프로세스당 한 번 import되므로 여기 둔다.
#  세션(브라우저 탭)별 5회 → 15분 잠금 · IP별 5회 → 15분 잠금(IP는 프록시 뒤에서 모두 같거나 위조될 수 있어 보조 수단) · 전체 15분 안 30회 이상 → 모든 시도에 5초 지연(잠그지는 않음: 전원이 막히는 역효과 방지)
AUTH_FAILS: dict = {}            # ip → (실패 횟수, 잠금 해제 시각)
AUTH_GLOBAL: dict = {"n": 0, "since": 0.0}
LOCK_S, WINDOW_S, PER_KEY_MAX, GLOBAL_MAX = 900, 900, 5, 30

def _auth_prune(now: float) -> None:
    if len(AUTH_FAILS) > 500:
        for k in [k for k, (_, until) in AUTH_FAILS.items() if until < now]: AUTH_FAILS.pop(k, None)

def auth_locked(ip: str | None, ss) -> int:
    """잠겨 있으면 남은 초, 아니면 0."""
    import time
    now = time.time(); until = max(float(ss.get("auth_lock_until") or 0), AUTH_FAILS.get(ip or "", (0, 0.0))[1] if ip else 0.0)
    return int(until - now) + 1 if until > now else 0

def auth_fail(ip: str | None, ss) -> tuple[int, float]:
    """실패 1회 기록. (이 세션의 누적 실패 횟수, 추가 지연 초)를 돌려준다."""
    import time
    now = time.time(); _auth_prune(now)
    n_s = int(ss.get("auth_fails") or 0) + 1; ss["auth_fails"] = n_s
    if n_s >= PER_KEY_MAX: ss["auth_lock_until"] = now + LOCK_S
    if ip:
        n_i, until = AUTH_FAILS.get(ip, (0, 0.0)); n_i += 1
        AUTH_FAILS[ip] = (n_i, now + LOCK_S if n_i >= PER_KEY_MAX else until)
    if now - AUTH_GLOBAL["since"] > WINDOW_S: AUTH_GLOBAL.update(n=0, since=now)
    AUTH_GLOBAL["n"] += 1
    return n_s, (5.0 if AUTH_GLOBAL["n"] >= GLOBAL_MAX else 0.0)

def auth_ok(ip: str | None, ss) -> None:
    ss.pop("auth_fails", None); ss.pop("auth_lock_until", None)
    if ip: AUTH_FAILS.pop(ip, None)

class _Ctx:
    """실행마다 app.py가 채우는 값."""
    USER = "담당자"; REVIEWER = "팀장"; HAS_API = False; DEMO = False; ORG: dict = {}; BUILD = ""
ctx = _Ctx()

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

@st.cache_data(ttl=120, show_spinner=False)
def _cached(name: str, sig: tuple, *args):
    return getattr(db, name)(*args)
def _q(name: str, *args):
    """자주 읽는 기록 조회를 캐시한다. 키에 DB 변경 신호(파일·WAL 수정 시각)를 넣어 쓰기가 있으면 다음 실행에서 다시 읽는다."""
    return _cached(name, db.signature(), *args)

def go(page: str, step: int | None = None):
    """다음 실행에서 메뉴·단계를 바꾼다."""
    st.session_state["_goto"] = (page, step); st.rerun()

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
    if ctx.HAS_API and st.button("AI에게 열 구성 물어보기", key=f"{key}_sug", help="표 제목·머리글·앞 5행만 보냅니다(연락처 패턴은 가린 뒤). 제안은 초안이며 아래에서 확정합니다."):
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
    if ctx.DEMO and demo_files:
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
    if not ctx.HAS_API:
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
    items = items if how.startswith("Claude") else normalize.normalize_llm(items)            # 모델 추출이면 지표 분류를 다시 묻지 않는다(중복 호출 제거)
    res["items"], res["_match_notes"] = match.match_items(items)                             # 이름이 달라도 가진 자료에 맞춘다
    return res, how

def request_input(key: str, demo_glob: str | None):
    """요구서 입력: 파일 올리기 또는 붙여 넣기. 시연 모드면 샘플 선택. (원문, 파일명) 반환."""
    text, name = "", ""
    if ctx.DEMO and demo_glob:
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

def _requester_input(label: str, value: str, key: str) -> str:
    """요청 주체 입력: 사전·기록의 이름을 고르거나 새로 입력(자동완성)."""
    names = _q("requester_names"); opts = list(dict.fromkeys(([value] if value else []) + names))
    if not opts: return (st.text_input(label, value or "", key=key) or "").strip()
    return (st.selectbox(label, opts, index=0 if value else None, accept_new_options=True, key=key, placeholder="고르거나 새로 입력") or "").strip()

def confirm_delete(label: str, key: str, what: str) -> bool:
    """두 단계 삭제: 확인 체크 → 삭제 버튼. 체크해야 버튼이 나타난다. 눌리면 True."""
    c1, c2 = st.columns([3, 1.2])
    ok = c1.checkbox(f"{label} 삭제 확인 — {what}", key=f"{key}_ok")
    return ok and c2.button(f"{label} 삭제", key=f"{key}_go", type="primary", icon=":material/delete:")

def _import_dashboard(data: bytes, name: str, include_partial: bool = False, S: dict | None = None) -> tuple[str, int]:
    """대시보드 저장 파일 → 지표 데이터 묶음 + 센터 명부. (설명, 묶음 번호). 파일 자체는 저장하지 않는다(계정 영역이 들어 있을 수 있음)."""
    S = S or dashboard_import.summarize(dashboard_import.parse(data), include_partial)
    if not S["rows"] and not S["centers"]: raise ValueError("지표 자료도 센터 명부도 찾지 못했습니다. 대시보드 페이지를 '웹페이지, 전체'로 저장했는지 확인하세요.")
    n_rep = db.data_replaced_count(S["rows"]) if S["rows"] else 0
    bid, n = db.add_data_batch(S["rows"], ctx.USER, f"통합 대시보드({name})", "대시보드 저장 파일 가져오기")
    nc = db.replace_centers(S["centers"], name) if S["centers"] else 0
    span = f", 기준일 {S['dates'][0]}~{S['dates'][-1]}" if S["dates"] else ""
    return (f"대시보드 저장 파일 '{name}'에서 지표 데이터 {n:,}건(지표 {len(S['by_indicator'])}개{span})" + (f"과 센터 명부 {nc}개소" if nc else "") + f"를 가져왔습니다(묶음 #{bid})."
            + (f" 이미 있던 값 {n_rep:,}건은 새 값으로 바뀌었습니다(묶음을 지우면 되돌아갑니다)." if n_rep else "") + " " + " / ".join(S["notes"]) + " 계정·연락처 등 개인정보 영역은 읽지 않았습니다."), bid


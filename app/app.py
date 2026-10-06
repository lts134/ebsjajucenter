"""EBS 대외 요구자료 대응 에이전트 — 프로토타입
실행: python start.py  (또는 streamlit run app.py)
층: ①등록(1) ②분석·검색·제안(2) ③대조·점검표·개인정보·출처(3) ④회신 초안·충족 검사·HWPX(4) ⑤묶음·확정(5) ⑥검토·승인(6) ⑦현황·통계(6)
"""
import os, io, sys, subprocess, datetime as dt, zipfile, json
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
import db, extract, compare, pii, docread, normalize, search, suggest, draft, hwpx_out, llm, assist, history_qa, tabular

st.set_page_config(page_title="EBS 대외 요구자료 대응 에이전트", page_icon="📁", layout="wide")
HERE = Path(__file__).parent
SAMPLE = HERE / "sample_data"
TPL_DIR = HERE / "templates"
OUT_DIR = HERE / "storage" / "out"; OUT_DIR.mkdir(parents=True, exist_ok=True)
# ---- 접속자(세션)별 AI 설정: 키·모델·호출 기록은 이 브라우저 세션에만 속한다(다른 접속자와 공유되지 않음) ----
st.session_state.setdefault("llm_cfg", {}); st.session_state.setdefault("llm_state", {}); st.session_state.setdefault("llm_log", [])
llm.configure(st.session_state["llm_cfg"], st.session_state["llm_state"], st.session_state["llm_log"])
HAS_API = llm.available()

with st.sidebar:
    st.title("대외 요구자료 대응 에이전트")
    page = st.radio("메뉴", ["① 과거 자료 등록", "② 새 요구서 분석", "③ 수치 대조·점검표", "④ 회신 초안·HWPX",
                          "⑤ 검토·승인", "⑥ 이력 조회", "⑦ 현황·통계", "⑧ 이력에 묻기", "설정"])
    st.divider()
    USER = st.text_input("담당자 이름(기록용)", st.session_state.get("user_name", "담당자"), key="user_name") or "담당자"
    REVIEWER = st.text_input("검토자(팀장) 이름", st.session_state.get("reviewer_name", "팀장"), key="reviewer_name") or "팀장"
    st.divider()
    st.caption(f"AI: {llm.provider_name()} · {'연결됨 · ' + llm.model_label() if HAS_API else '키 없음 → 규칙 기반'}")
    if HAS_API and llm.log():
        u = llm.usage_summary()
        st.caption(f"이번 세션 호출 {u['calls']}회 · 토큰 {u['input_tokens']}/{u['output_tokens']} · 평균 {u['avg_latency_s']}초")
        st.caption(f"추정 비용 ${u['cost_usd']:.4f}" + (f" (단가 미상 {u['cost_unknown_calls']}회 제외)" if u['cost_unknown_calls'] else "") + " · 구조화 출력 " + f"{u['structured_calls']}회 · 이 세션(내 키)만 집계")
    st.caption(f"이력 DB: {db.DB_PATH.name}")

@st.cache_data(show_spinner=False, max_entries=20)
def render_hwpx_cached(tpl_bytes: bytes, fill_json: str, rows_json: str) -> bytes:
    """같은 템플릿·문안·수치면 다시 만들지 않는다(글자 입력마다 재실행되는 Streamlit 특성 대비)."""
    return hwpx_out.render(tpl_bytes, json.loads(fill_json), json.loads(rows_json))

def read_doc(uploaded) -> str:
    try:
        return docread.read(uploaded.name, uploaded.getvalue())
    except Exception as e:
        st.error(f"본문 추출 실패: {e}"); return ""


def _grid_to_values(grid, key: str, source_version_default: str | None = None):
    """격자(엑셀 시트 또는 문서의 표) → 제출값 표. 긴 형식은 바로, 가로 펼침 표는 열 매핑 UI를 거친다. (df | None) 반환."""
    info = tabular.analyze(grid)
    if info.shape == "empty":
        st.error("표에서 자료 행을 찾지 못했습니다. 시트(또는 표)를 바꿔 보세요."); return None
    if info.shape == "long":
        try: return tabular.long_table(grid, info)
        except ValueError as e: st.error(f"표를 읽지 못했습니다: {e}"); return None
    # ---- 가로 펼침 표: 열 매핑 ----
    st.info("행=센터, 열=지표인 **가로 펼침 표**로 읽었습니다. 센터 열·값 열·기준일을 확인하고 지표명을 정하세요. 값은 그대로 옮기며 계산하지 않습니다."
            + (f"  \n표 제목: {info.title}" if info.title else "") + (f"  \n주석: {' / '.join(info.notes[:2])}" if info.notes else ""))
    st.dataframe(tabular.preview(grid, info), height=200, width="stretch")
    sug = st.session_state.get(f"{key}_map")
    if HAS_API and st.button("Claude로 열 매핑 제안", key=f"{key}_sug", help="표 제목·머리글·앞 5행만 보냅니다(연락처 패턴 마스킹). 제안은 초안이며 아래에서 확정합니다."):
        with st.spinner("표 구조 판단 중… (Claude)"):
            try: sug = tabular.suggest_mapping(grid, info); st.session_state[f"{key}_map"] = sug
            except Exception as e: st.warning(f"Claude 제안 실패, 규칙 추정값을 사용합니다: {llm.explain_error(e) or e}")
    if sug and sug.get("note"): st.caption(f"Claude 메모: {sug['note']}")
    cols = list(range(grid.width())); label = info.col_label
    c_default = sug["center_col"] if sug and sug.get("center_col") is not None else info.center_col
    v_default = [c for c, _ in sug["value_cols"]] if sug and sug.get("value_cols") else info.value_cols
    d_default = (sug.get("base_date") if sug else None) or info.base_date_hint or ""
    c1, c2, c3 = st.columns([1, 2, 1])
    center_col = c1.selectbox("센터(행 이름) 열", cols, index=cols.index(c_default) if c_default in cols else 0, format_func=label, key=f"{key}_cc")
    value_cols = c2.multiselect("값 열", [c for c in cols if c != center_col], default=[c for c in v_default if c != center_col], format_func=label, key=f"{key}_vc")
    base_date = c3.text_input("기준일", d_default, placeholder="2026-06-30", key=f"{key}_bd", help="표 제목·주석의 '… 기준' 날짜를 자동으로 채웁니다. 없으면 공문 본문을 확인해 입력하세요.")
    names = dict(sug["value_cols"]) if sug and sug.get("value_cols") else {}
    ind_df = pd.DataFrame([{"열": label(c), "지표명": names.get(c) or tabular.default_indicator(label(c))} for c in value_cols])
    if len(ind_df):
        ind_df = st.data_editor(ind_df, hide_index=True, width="stretch", key=f"{key}_ind", disabled=["열"],
                                column_config={"지표명": st.column_config.TextColumn("지표명(저장될 이름 · 사전에 있으면 정규 지표명이 기본값)")})
    drop_totals = st.checkbox("합계·소계·평균 행 제외", value=True, key=f"{key}_dt",
                              help=f"제외 대상 행: {[grid.row_offset + r for r in info.total_rows] or '없음'}")
    try:
        indicators = {c: str(n).strip() for c, n in zip(value_cols, ind_df["지표명"].tolist(), strict=True)} if len(ind_df) else {}
        return tabular.wide_to_long(grid, info, center_col, value_cols, base_date.strip(), indicators, drop_totals, source_version_default)
    except ValueError as e:
        st.warning(str(e)); return None

def value_table_input(key: str, what: str = "제출값"):
    """①·③ 공용 값 입력: 집계 엑셀/CSV · 회신 문서(hwpx·docx·pdf)의 표 · 직접 입력. (df | None, 출처 이름) 반환."""
    mode = st.radio("입력 방식", ["집계 엑셀/CSV", "회신 문서의 표 (hwpx·docx·pdf)", "직접 입력"], horizontal=True, key=f"{key}_mode",
                    help="엑셀은 제목 행·병합 머리글·가로 펼침(행=센터, 열=지표) 서식도 읽습니다. 한글 HWP(구형식)는 한글에서 HWPX로 저장해 올리세요.")
    if mode == "집계 엑셀/CSV":
        up = st.file_uploader(f"{what} 엑셀/CSV — 긴 형식(지표명·센터명·기준일·값) 또는 실적표 그대로", type=["xlsx", "xlsm", "csv"], key=f"{key}_xl")
        if not up: return None, ""
        try:
            sheets = compare.list_sheets(up)
            sheet = st.selectbox("시트", sheets, key=f"{key}_sheet") if len(sheets) > 1 else None
            grid = tabular.read_grid(up, sheet)
        except Exception as e:
            st.error(f"파일을 읽지 못했습니다: {e}"); return None, ""
        return _grid_to_values(grid, f"{key}_{sheet}"), up.name
    if mode.startswith("회신 문서"):
        up = st.file_uploader(f"{what}이 들어 있는 회신 문서 (hwpx / docx / pdf)", type=["hwpx", "docx", "pdf"], key=f"{key}_doc")
        if not up: return None, ""
        try: grids, text = tabular.grids_from_document(up.name, up.getvalue())
        except Exception as e:
            st.error(f"문서를 읽지 못했습니다: {e}"); return None, up.name
        if not grids:
            st.error("문서에서 표를 찾지 못했습니다. hwpx·docx는 표 개체여야 하고(탭·공백으로 맞춘 글은 표가 아님), PDF는 글자가 추출되는 파일이어야 합니다(스캔본 불가). 표가 없으면 '직접 입력'을 쓰세요.")
            with st.expander(f"진단: 추출된 본문 {len(text)}자 — 앞부분 보기"):
                st.text("\n".join(text.splitlines()[:40]) or "(본문이 비어 있음 — 배포용 문서이거나 그림으로 된 문서일 수 있음)")
            return None, up.name
        best = max(range(len(grids)), key=lambda i: tabular.numeric_cells(grids[i]))         # 숫자가 가장 많은 표를 기본 선택
        g = st.selectbox("문서 안의 표 (숫자가 많은 표가 기본 선택)", grids, index=best, key=f"{key}_tbl",
                         format_func=lambda g: f"{g.source_sheet} — {len(g.rows)}행 × {g.width()}열" + (f" (문서 {g.row_offset}번째 줄부터)" if g.row_offset > 1 else ""))
        return _grid_to_values(g, f"{key}_{g.source_sheet}", f"회신 문서({up.name}) 표에서 추출"), up.name
    st.caption("표 파일이 없을 때. 행을 추가해 지표명·센터명·기준일·값을 채우세요. 근거 4칸은 선택입니다.")
    man = st.data_editor(pd.DataFrame(columns=tabular.MANUAL_COLS), num_rows="dynamic", width="stretch", key=f"{key}_man")
    if man.dropna(how="all").empty: return None, ""
    try: return tabular.from_records(man), "직접 입력"
    except ValueError as e: st.error(str(e)); return None, ""

def analyze(text: str):
    res, how = extract.extract(text)
    res["items"] = normalize.normalize_items(res.get("items") or [])
    res["items"] = normalize.normalize_llm(res["items"])
    return res, how

VAL_COLS = ["indicator", "center", "base_date", "value", "definition", "calc_period", "extract_date", "source_version"]
STATUS_KO = {"review_requested": "검토 대기", "approved": "승인", "rejected": "반려", "comment": "의견", "draft": "초안", "confirmed": "확정"}
def ko(status): return STATUS_KO.get(status, status)
VAL_KO = {"indicator": "지표", "center": "센터", "base_date": "기준일", "value": "값", "definition": "지표 정의", "calc_period": "집계기간",
          "extract_date": "추출시점", "source_version": "원자료 버전", "source_file": "출처 파일", "source_sheet": "출처 시트", "source_row": "출처 행"}
ITEM_KO = {"seq": "번호", "item_text": "항목 원문", "indicator": "지표", "base_date": "기준일", "period": "기간", "unit": "단위"}

# ================= ① 과거 자료 등록 =================
if page == "① 과거 자료 등록":
    st.header("① 과거 요구서·제출본 등록 → 이력 저장")
    st.caption("에이전트: 요구서 본문 추출(txt·hwpx·pdf·docx) → 항목·지표·기준일 추출 → 제출값과 산출 근거·출처 저장. 담당자: 파일 준비, 추출 결과 확인.")
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("1) 요구서")
        use_sample = st.checkbox("샘플 요구서 사용", value=True)
        if use_sample:
            files = sorted(SAMPLE.glob("요구서_*.txt"))
            pick = st.selectbox("샘플", files, format_func=lambda p: p.name)
            req_text, req_name = pick.read_text(encoding="utf-8"), pick.name
        else:
            up = st.file_uploader("요구서 파일 (txt / hwpx / pdf / docx)", type=["txt", "hwpx", "pdf", "docx"], key="reg_up")
            req_text, req_name = (read_doc(up), up.name) if up else ("", "")
        req_text = st.text_area("요구서 원문 (수정 가능)", req_text, height=220)
        if st.button("요구 항목 추출", type="primary", disabled=not req_text):
            with st.spinner("요구 항목 추출 중…" + (" (Claude)" if HAS_API else "")):
                res, how = analyze(req_text)
            st.session_state.update(reg_extract=res, reg_how=how, reg_text=req_text, reg_name=req_name)
    with col2:
        st.subheader("2) 그때 제출한 값")
        st.caption("집계 엑셀이 없어도 됩니다. 그때 보낸 회신 문서(hwpx·docx·pdf)의 표를 그대로 읽거나, 직접 입력할 수 있습니다.")
        use_sample_v = st.checkbox("샘플 제출본 사용", value=True)
        if use_sample_v:
            vfiles = sorted(SAMPLE.glob("등원율_*.xlsx")) + sorted(SAMPLE.glob("실적표_*.xlsx"))
            vpick = st.selectbox("샘플 제출본 (실적표_가로형: 제목 행·병합 머리글·합계 행이 있는 실무 서식 → 열 매핑으로 읽기)", vfiles, format_func=lambda p: p.name)
            if vpick.name.startswith("실적표_"):
                vdf, vname = _grid_to_values(tabular.read_grid(vpick), f"reg_sample_{vpick.name}"), vpick.name
            else:
                vdf, vname = compare.load_values(vpick), vpick.name
        else:
            vdf, vname = value_table_input("reg_val", "제출값")
        if vdf is not None:
            st.dataframe(vdf[VAL_COLS].rename(columns=VAL_KO), height=220, width="stretch")
            st.caption(f"{len(vdf)}건 · 출처 {vdf['source_file'][0]}" + (f" / {vdf['source_sheet'][0]}" if vdf['source_sheet'][0] else ""))
    if "reg_extract" in st.session_state:
        res = st.session_state["reg_extract"]
        st.subheader(f"추출 결과 — {st.session_state['reg_how']}")
        c1, c2, c3, c4 = st.columns(4)
        requester = c1.text_input("요청 주체", res.get("requester") or "")
        received = c2.text_input("접수일", res.get("received_date") or "")
        due = c3.text_input("제출기한", res.get("due_date") or "")
        title = c4.text_input("제목", res.get("title") or "")
        items_df = pd.DataFrame(res.get("items") or [], columns=extract.ITEM_FIELDS)
        items_df = st.data_editor(items_df, num_rows="dynamic", width="stretch",
                                  column_config={"item_text": "항목 원문", "indicator": st.column_config.SelectboxColumn("지표명(정규화)", options=list(normalize.CANON), required=False),
                                                 "base_date": "기준일", "period": "기간", "unit": "단위"})
        if res.get("_error"): st.warning(f"Claude 호출 오류로 규칙 기반 결과입니다: {res['_error']}")
        submitted_date = st.text_input("제출일(제출본 기준)", due or "")
        if st.button("이력에 저장 (요구서 + 제출본 확정)", type="primary"):
            rid = db.add_request(requester, received, due, title, st.session_state["reg_text"], st.session_state["reg_name"], items_df.to_dict("records"))
            vals = vdf.to_dict("records") if vdf is not None else []
            sid = db.add_submission(rid, submitted_date, USER, vname, "confirmed", "과거 제출본 등록", vals)
            st.success(f"저장 완료: 요구서 #{rid}, 제출본 #{sid}, 제출값 {len(vals)}건")
            del st.session_state["reg_extract"]

# ================= ② 새 요구서 분석 =================
elif page == "② 새 요구서 분석":
    st.header("② 새 요구서 → 항목 추출·지표 정규화 → 유사 요구 검색 → 항목별 데이터 제안")
    use_sample = st.checkbox("샘플 새 요구서 사용", value=True)
    if use_sample:
        sfiles = sorted(SAMPLE.glob("새요구서_*.txt"))
        spick = st.selectbox("샘플 (정형 의원실 / 감사 공문체 / 교육부 메일체 — 뒤의 둘은 규칙 경로와 Claude 경로 차이가 드러나는 서식)", sfiles,
                             index=next((i for i, f in enumerate(sfiles) if "의원실" in f.name), 0), format_func=lambda p: p.name)
        text, name = spick.read_text(encoding="utf-8"), spick.name
    else:
        up = st.file_uploader("새 요구서 (txt / hwpx / pdf / docx)", type=["txt", "hwpx", "pdf", "docx"], key="new_up")
        text, name = (read_doc(up), up.name) if up else ("", "")
    text = st.text_area("요구서 원문", text, height=200)
    if st.button("분석", type="primary", disabled=not text):
        with st.spinner("요구 항목 추출·지표 정규화 중…" + (" (Claude)" if HAS_API else "")):
            res, how = analyze(text)
        st.session_state.update(new_req=res, new_how=how, new_text=text, new_name=name)
    if "new_req" in st.session_state:
        res = st.session_state["new_req"]
        st.caption(f"추출 방식: {st.session_state['new_how']} · 지표 정규화: 동의어 사전{' + Claude' if HAS_API else ''}" + (" · 요구서의 전화·이메일 등은 마스킹된 채 전송됨" if res.get("_redacted") else ""))
        st.write({"요청 주체": res.get("requester"), "접수일": res.get("received_date"), "제출기한": res.get("due_date"), "제목": res.get("title")})
        if res.get("_error"): st.warning(f"Claude 호출 오류로 규칙 기반 결과입니다: {res['_error']}")
        sim_req = search.similar_requests(text)
        if sim_req and sim_req[0]["score"] > 0.2:
            st.info("유사한 과거 요구서: " + " / ".join(f"#{r['id']} {r['received_date']} {r['requester']} — {r['title']} (유사도 {r['score']})" for r in sim_req if r["score"] > 0.2))
        st.subheader("요구 항목별: 과거 이력 · 유사 항목 · 데이터 제안")
        for it in res.get("items") or []:
            with st.container(border=True):
                mb = f" (인식 근거: '{it['matched_by']}')" if it.get("matched_by") else ""
                st.markdown(f"**{it['item_text']}**  \n지표: `{it.get('indicator')}`{mb} · 기준일: `{it.get('base_date')}` · 기간: `{it.get('period')}` · 단위: `{it.get('unit')}`")
                sg = suggest.suggest(it)
                st.markdown(f"데이터 제안: **{sg['판단']}**  \n출처: {sg['데이터 출처']} · 담당: {sg['담당']} · 과거 제출: {sg['과거 제출']}")
                if it.get("indicator") and it.get("base_date"):
                    past = db.past_values_for(it["indicator"], it["base_date"])
                    subs = {}
                    for p_ in past: subs.setdefault((p_["submitted_date"], p_["requester"], p_["request_title"], p_["file_name"]), []).append(p_)
                    for (sd, rq, tt, fn), rows in subs.items():
                        st.markdown(f"과거 제출: **{sd}** · {rq} · {tt} · `{fn}` · {len(rows)}건  \n"
                                    f"산출 근거: 정의 `{rows[0]['definition']}` / 집계기간 `{rows[0]['calc_period']}` / 추출시점 `{rows[0]['extract_date']}` / 원자료 `{rows[0]['source_version']}`")
                sims = search.similar_items(it, top=3)
                if sims:
                    st.dataframe(pd.DataFrame(sims).rename(columns={"score": "유사도", "request_id": "요구번호", "requester": "요청 주체", "received_date": "접수일", "title": "제목", "item_text": "과거 항목", "indicator": "지표", "base_date": "기준일"}), width="stretch", height=140)
        done = st.session_state.get("new_registered")                      # (원문, 요구번호) — 같은 원문 중복 등록 방지
        already = done and done[0] == st.session_state["new_text"]
        if st.button("이 요구서를 이력에 등록(진행 중)", type="primary", disabled=bool(already)):
            rid = db.add_request(res.get("requester"), res.get("received_date"), res.get("due_date"), res.get("title"), st.session_state["new_text"], st.session_state["new_name"], res.get("items") or [])
            st.session_state["new_registered"] = (st.session_state["new_text"], rid); st.rerun()
        if already:
            st.success(f"요구서 #{done[1]} 등록됨. ③ 수치 대조에서 이 요구서를 '회신 대상'으로 고른 뒤 ④에서 초안을 만드세요.")

# ================= ③ 수치 대조 =================
elif page == "③ 수치 대조·점검표":
    st.header("③ 새 집계값 vs 과거 제출값 → 차이·사유 → 정합성 점검표 → 개인정보 검사")
    st.caption("대조는 코드(pandas)만 사용. AI는 사유를 추정하지 않음.")
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("새 집계값")
        use_s = st.checkbox("샘플(9월 재산출) 사용", value=True)
        if use_s: new_df = compare.load_values(SAMPLE / "등원율_2026-06-30기준_9월재산출.xlsx")
        else: new_df, _ = value_table_input("cmp_new", "새 집계값")
    with c2:
        st.subheader("과거 제출값 (이력 DB)")
        subs = db.list_submissions()
        if not subs:
            st.warning("이력이 비어 있습니다. ①에서 과거 제출본을 먼저 등록하세요."); old_df, s = None, None
        else:
            s = st.selectbox("제출본", subs, format_func=lambda s: f"#{s['id']} {s['submitted_date']} {s['requester']} — {s['request_title']} ({s['file_name']})")
            old_df = pd.DataFrame(db.get_values(s["id"]))
        reqs = db.list_requests()
        target = st.selectbox("이번 회신 대상 요구서", reqs, format_func=lambda r: f"#{r['id']} {r['received_date']} {r['requester']} — {r['title']}") if reqs else None
    tol = st.number_input("허용 오차(이하이면 일치)", value=0.0, step=0.1)
    if new_df is not None and old_df is not None and len(old_df):
        m = compare.compare(new_df, old_df, tol)
        n_diff = int((m["판정"] == "차이").sum())
        st.metric("차이 항목", n_diff)
        st.dataframe(m[["indicator", "center", "base_date", "old_value", "new_value", "diff", "판정", "단서"]]
                     .rename(columns={"indicator": "지표", "center": "센터", "base_date": "기준일", "old_value": "과거 제출값", "new_value": "신규 집계값", "diff": "차이"}), width="stretch")
        st.subheader("차이 사유 입력 (담당자)")
        diff_rows = m[m["판정"] == "차이"].to_dict("records")
        cands_key = tuple((r["indicator"], r["center"], r["base_date"]) for r in diff_rows)
        if diff_rows and st.button("사유 문구 후보 제안 (대조 단서·과거 입력 사유 근거)"):
            with st.spinner("후보 생성 중…"):
                cands, how = assist.reason_candidates(diff_rows)
            st.session_state["reason_cands"] = (cands_key, cands, how)
        cands = st.session_state.get("reason_cands")
        cands = cands[1] if cands and cands[0] == cands_key else None
        if cands: st.caption(f"후보 생성 방식: {st.session_state['reason_cands'][2]} · 후보는 제안일 뿐이며 담당자가 고르거나 고쳐 씁니다. 원인을 새로 추정한 문구는 없습니다.")
        reasons = {}
        for r in diff_rows:
            key = (r["indicator"], r["center"], r["base_date"])
            prev = db.reasons_for(*key)
            if cands and cands.get(key):
                opts = [c for c in cands[key] if c.get("문구")]
                c1, c2 = st.columns([5, 1])
                pick = c1.selectbox(f"후보 — {r['center']}", opts, format_func=lambda c: f"{c['문구']}  〔{c['근거']}〕", key=f"cand_{key}", label_visibility="collapsed")
                if c2.button("적용", key=f"apply_{key}"): st.session_state[f"reason_{key}"] = pick["문구"]
            reasons[key] = st.text_input(f"{r['center']} · {r['indicator']} · {r['base_date']}  ({r['old_value']} → {r['new_value']})",
                                         value=prev[0]["reason"] if prev else "", placeholder="예: 8/5 출결 사후 보정 반영", key=f"reason_{key}")
        ck = compare.checklist(m, reasons)
        st.subheader("제출 전 정합성 점검표")
        st.dataframe(ck, width="stretch")
        st.subheader("개인정보 검사")
        hits = pii.scan_df(new_df.drop(columns=["source_file", "source_sheet", "source_row"], errors="ignore"), "새 집계값")
        if hits: st.error(f"개인정보 의심 패턴 {len(hits)}건"); st.dataframe(pd.DataFrame(hits), width="stretch")
        else: st.success("개인정보 의심 패턴 없음")
        buf = io.BytesIO(); pii.excel_safe(ck).to_excel(buf, index=False)
        st.download_button("점검표 엑셀", buf.getvalue(), file_name=f"정합성점검표_{dt.date.today()}.xlsx")
        if st.button("사유 저장 + 새 제출본 확정 → ④로", type="primary", disabled=target is None):
            sid = db.add_submission(target["id"], str(dt.date.today()), USER, "새 집계값", "confirmed", "③ 대조 후 확정", new_df.to_dict("records"))
            for key, reason in reasons.items():
                if reason.strip():
                    row = m[(m["indicator"] == key[0]) & (m["center"] == key[1]) & (m["base_date"] == key[2])].iloc[0]
                    db.add_reason(sid, *key, row["old_value"], row["new_value"], reason, USER)
            st.session_state.update(last_submission=sid, last_request=target["id"], last_checklist=ck.to_dict("records"), last_reasons={" | ".join(k): v for k, v in reasons.items() if v.strip()})
            st.success(f"제출본 #{sid} 확정, 사유 {sum(1 for v in reasons.values() if v.strip())}건 저장. ④ 회신 초안으로 이동하세요.")

# ================= ④ 회신 초안·HWPX =================
elif page == "④ 회신 초안·HWPX":
    st.header("④ 회신 초안 → 요구 항목 충족 검사 → HWPX 출력 → 제출 묶음")
    reqs = db.list_requests()
    if not reqs: st.warning("요구서가 없습니다."); st.stop()
    default = next((i for i, r in enumerate(reqs) if r["id"] == st.session_state.get("last_request")), 0)
    req = st.selectbox("회신 대상 요구서", reqs, index=default, format_func=lambda r: f"#{r['id']} {r['received_date']} {r['requester']} — {r['title']}")
    items = db.get_items(req["id"])
    sid, vals = db.latest_confirmed_values(req["id"])
    values = pd.DataFrame(vals) if vals else pd.DataFrame(columns=VAL_COLS)
    st.caption(f"확정 수치: 제출본 #{sid} · {len(values)}건" if sid else "이 요구서에 확정 수치가 없습니다. ③에서 먼저 확정하세요(초안은 [확인 필요]로 생성됨).")
    reasons_raw = {}
    for _, v in values.iterrows():
        for rr in db.reasons_for(v["indicator"], v["center"], v["base_date"]):
            reasons_raw[(v["indicator"], v["center"], v["base_date"])] = rr["reason"]; break
    prov = {k: values.iloc[0].get(k) for k in ("definition", "calc_period", "extract_date", "source_version")} if len(values) else {}
    if st.button("초안 생성", type="primary"):
        d, how = draft.make_draft(req, items, values, reasons_raw, prov, st.session_state.get("last_checklist") if st.session_state.get("last_request") == req["id"] else None)
        st.session_state.update(draft=d, draft_how=how, draft_req=req["id"])
    if st.session_state.get("draft_req") == req["id"] and "draft" in st.session_state:
        d = st.session_state["draft"]
        st.caption(f"생성 방식: {st.session_state['draft_how']}")
        d["제목"] = st.text_input("제목", d.get("제목", ""))
        d["본문"] = st.text_area("본문", d.get("본문", ""), height=220)
        d["차이사유"] = st.text_area("차이 사유", d.get("차이사유", ""), height=100)
        d["산출근거"] = st.text_area("산출 근거", d.get("산출근거", ""), height=100)
        st.subheader("요구 항목 충족 검사")
        cov = draft.coverage_check(items, d, values)
        st.dataframe(cov, width="stretch")
        n_ok, n_flag, n_miss = int((cov["판정"] == "충족").sum()), int((cov["판정"] == "미확인 표시").sum()), int((cov["판정"] == "누락").sum())
        if n_miss: st.warning(f"충족 {n_ok}건 · [확인 필요] 표시 {n_flag}건 · 누락 {n_miss}건 — 누락 항목은 초안에 언급이 없습니다. 본문을 보완하세요.")
        elif n_flag: st.info(f"충족 {n_ok}건 · [확인 필요] 표시 {n_flag}건 — 확정 수치가 없는 항목은 [확인 필요]로 남아 있습니다. 산출 후 채우거나 사유를 적으세요.")
        else: st.success(f"모든 요구 항목 충족({n_ok}건)")
        if HAS_API and st.button("Claude로 추가 점검(누락·불일치·단정 표현)"):
            st.info(draft.coverage_check_llm(items, d, values, st.session_state.get("last_checklist") if st.session_state.get("last_request") == req["id"] else None, reasons_raw, prov))
        st.subheader("예상 후속 질문·리스크 사전 검토")
        ck_now = st.session_state.get("last_checklist") if st.session_state.get("last_request") == req["id"] else None
        if st.button("이 회신을 받으면 어떤 질문이 올까? (요구 항목·수치·대조 결과·타 기관 제출 이력 근거)"):
            with st.spinner("예측 중…"):
                qs, how = assist.foresee(req, items, values, reasons_raw, ck_now, d)
            st.session_state["foresee"] = (req["id"], qs, how)
        fs = st.session_state.get("foresee")
        if fs and fs[0] == req["id"]:
            st.caption(f"생성 방식: {fs[2]} · 질문은 예측이며 수치 해석은 포함하지 않습니다.")
            st.dataframe(pd.DataFrame(fs[1])[["가능성", "질문", "근거", "준비할 자료"]], width="stretch", hide_index=True)
        d_hits = [h for k in ("제목", "본문", "차이사유", "산출근거") for h in pii.scan_text(d.get(k, ""), f"초안 {k}")]
        if d_hits: st.error(f"초안 문안에 개인정보 의심 패턴 {len(d_hits)}건 — 제출 전 삭제·가명 처리 필요"); st.dataframe(pd.DataFrame(d_hits), width="stretch")
        else: st.caption("초안 문안 개인정보 검사: 의심 패턴 없음")
        st.subheader("HWPX 출력")
        tpls = sorted(TPL_DIR.glob("*.hwpx"))
        tpl = st.selectbox("템플릿", tpls, format_func=lambda p: p.name) if tpls else None
        up_tpl = st.file_uploader("또는 템플릿 업로드 ({{키}} 자리표시자가 있는 HWPX)", type=["hwpx"], key="tpl_up")
        tpl_bytes = up_tpl.getvalue() if up_tpl else (tpl.read_bytes() if tpl else None)
        if tpl_bytes:
            st.caption("템플릿 자리표시자: " + ", ".join(hwpx_out.placeholders(tpl_bytes)))
            fill = {"수신": req["requester"] or "", "발신": "EBS 지역교육협력부", "제목": d["제목"],
                    "요구항목": "\n".join(f"{i}. {it['item_text']}" for i, it in enumerate(items, 1)),
                    "본문": d["본문"], "차이사유": d["차이사유"], "산출근거": d["산출근거"], "담당자": USER}
            rows = [{"no": i + 1, "indicator": v["indicator"], "center": v["center"], "base_date": v["base_date"], "value": v["value"]} for i, (_, v) in enumerate(values.iterrows())]
            out = render_hwpx_cached(tpl_bytes, json.dumps(fill, ensure_ascii=False, sort_keys=True), json.dumps(rows, ensure_ascii=False, default=str))
            fname = f"회신_{req['id']}_{dt.date.today()}.hwpx"
            c1, c2, c3 = st.columns(3)
            c1.download_button("회신 HWPX 다운로드", out, file_name=fname)
            ckb = io.BytesIO(); pii.excel_safe(pd.DataFrame(ck_now or [])).to_excel(ckb, index=False)   # 이 요구서의 점검표만(다른 요구서 것을 섞지 않음)
            zb = io.BytesIO()
            with zipfile.ZipFile(zb, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(fname, out)
                zf.writestr("정합성점검표.xlsx", ckb.getvalue())
                vb = io.BytesIO(); pii.excel_safe(values).to_excel(vb, index=False); zf.writestr("확정수치.xlsx", vb.getvalue())
                fs = st.session_state.get("foresee")
                zf.writestr("근거_사유_메타.json", json.dumps({"요구": req, "제출본": sid, "차이 사유": {" | ".join(k): v for k, v in reasons_raw.items()}, "산출 근거": prov, "초안": d,
                                                          "예상 후속 질문": fs[1] if fs and fs[0] == req["id"] else None, "생성일": str(dt.date.today())}, ensure_ascii=False, indent=2, default=str))
            c2.download_button("제출 묶음 ZIP (회신+점검표+수치+근거)", zb.getvalue(), file_name=f"제출묶음_{req['id']}_{dt.date.today()}.zip")
            if c3.button("초안 저장 + 팀장 검토 요청", type="primary"):
                (OUT_DIR / fname).write_bytes(out)
                did = db.add_draft(sid, req["id"], d, fname, status="review_requested")
                st.success(f"초안 #{did} 저장, 검토 요청됨 → ⑤ 검토·승인")

# ================= ⑤ 검토·승인 =================
elif page == "⑤ 검토·승인":
    st.header("⑤ 팀장 검토·승인 (담당자 → 팀장 → 확정)")
    pending = db.list_drafts("review_requested")
    st.metric("검토 대기", len(pending))
    for d in db.list_drafts():
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

# ================= ⑥ 이력 조회 =================
elif page == "⑥ 이력 조회":
    st.header("⑥ 요구·제출·초안 이력")
    reqs = db.list_requests()
    kw = st.text_input("검색 (요청 주체·제목·항목 원문·요구서 본문에서 찾기)", placeholder="예: 등원율, 의원실, 2026-06-30")
    if kw.strip():
        ids = {r["id"] for r in db.search_requests(kw.strip(), limit=200)}
        reqs = [r for r in reqs if r["id"] in ids]
        st.caption(f"검색 결과 {len(reqs)}건")
    if not reqs: st.info("등록된 요구서가 없습니다." if not kw.strip() else "검색 결과가 없습니다.")
    for r in reqs:
        with st.expander(f"#{r['id']} {r['received_date']} · {r['requester']} · {r['title']} (기한 {r['due_date']})"):
            its = db.get_items(r["id"])
            if its: st.dataframe(pd.DataFrame(its)[list(ITEM_KO)].rename(columns=ITEM_KO), width="stretch", hide_index=True)
            for s in db.list_submissions(r["id"]):
                st.markdown(f"제출본 #{s['id']} · {s['submitted_date']} · {ko(s['status'])} · `{s['file_name']}` · {s['note']}")
                vals = pd.DataFrame(db.get_values(s["id"]))
                if len(vals): st.dataframe(vals.drop(columns=["id", "submission_id"]).rename(columns=VAL_KO), width="stretch", height=180, hide_index=True)
            for d in [x for x in db.list_drafts() if x["request_id"] == r["id"]]:
                st.markdown(f"초안 #{d['id']} · {ko(d['status'])} · `{d['hwpx_name']}` · {d['created_at'][:16]}")

# ================= ⑦ 현황·통계 =================
elif page == "⑦ 현황·통계":
    st.header("⑦ 요구 현황 · 기한 · 요청 주체별 통계")
    ov = db.request_overview()
    if not ov: st.info("등록된 요구서가 없습니다.")
    else:
        df = pd.DataFrame(ov); today = pd.Timestamp(dt.date.today())
        df["D-day"] = (pd.to_datetime(df["due_date"], errors="coerce") - today).dt.days
        df["상태"] = df.apply(lambda r: "확정 제출" if r["n_confirmed"] > 0 else ("기한 경과" if pd.notna(r["D-day"]) and r["D-day"] < 0 else "진행 중"), axis=1)
        show = df.rename(columns={"id": "번호", "requester": "요청 주체", "received_date": "접수일", "due_date": "제출기한", "title": "제목", "n_items": "항목 수", "n_confirmed": "확정 제출본", "last_submitted": "최근 제출일"})
        soon = (df["상태"] == "진행 중") & df["D-day"].between(0, 3)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("요구서", len(df)); c2.metric("진행 중", int((df["상태"] == "진행 중").sum()))
        c3.metric("기한 임박(3일 이내)", int(soon.sum())); c4.metric("기한 경과(미제출)", int((df["상태"] == "기한 경과").sum()))
        view = show[["번호", "상태", "D-day", "요청 주체", "접수일", "제출기한", "제목", "항목 수", "확정 제출본", "최근 제출일"]]
        def _row_style(r):
            if r["상태"] == "기한 경과": return ["background-color: #fde2e2"] * len(r)
            if r["상태"] == "진행 중" and pd.notna(r["D-day"]) and r["D-day"] <= 3: return ["background-color: #fff3cd"] * len(r)
            return [""] * len(r)
        st.dataframe(view.style.apply(_row_style, axis=1), width="stretch", hide_index=True)
        st.caption("노랑: 진행 중이며 기한 3일 이내 · 빨강: 기한 경과(미제출)")
        rows = [{"요구번호": r["id"], "요청 주체": r["requester"], "접수일": r["received_date"], "제출기한": r["due_date"], "제목": r["title"], "항목": it["item_text"], "지표": it["indicator"], "기준일": it["base_date"], "단위": it["unit"], "확정 제출본 수": r["n_confirmed"], "최근 제출일": r["last_submitted"]} for r in ov for it in db.get_items(r["id"])]
        lb = io.BytesIO(); pii.excel_safe(pd.DataFrame(rows)).to_excel(lb, index=False)
        st.download_button("관리대장 엑셀 내보내기", lb.getvalue(), file_name=f"요구자료_관리대장_{dt.date.today()}.xlsx")
        st.divider()
        by_req, by_ind = db.requester_stats()
        c1, c2 = st.columns(2)
        with c1: st.subheader("요청 주체별"); st.dataframe(pd.DataFrame(by_req).rename(columns={"requester": "요청 주체", "n_requests": "요구 건수", "n_items": "항목 수", "first_date": "최초", "last_date": "최근"}), width="stretch")
        with c2: st.subheader("반복 요구 지표"); st.dataframe(pd.DataFrame(by_ind).rename(columns={"indicator": "지표", "n_times": "요구 횟수", "n_requesters": "요청 주체 수", "base_dates": "기준일들"}), width="stretch")
        st.caption("반복 요구 지표는 사전 산출·표준 답변 후보입니다.")

# ================= ⑧ 이력에 묻기 =================
elif page == "⑧ 이력에 묻기":
    st.header("⑧ 이력에 묻기 — 자연어로 질문하면 이력 DB를 조회해 근거와 함께 답합니다")
    st.caption("Claude가 읽기 전용 조회 도구(요구서 검색·상세·제출값·과거 제출·차이 사유·현황·지표 사전)를 골라 호출하고, 조회된 레코드만 근거로 답합니다. 수치를 가공(평균·증감)하지 않습니다."
               + ("" if HAS_API else " 지금은 API 키가 없어 키워드 검색으로 동작합니다."))
    examples = ["감사실에 등원율을 언제 어떤 값으로 냈지?", "2026-06-30 기준 등원율을 제출한 기관과 날짜를 전부 보여줘", "센터C 등원율이 달라진 사유로 뭐라고 적었나", "기한이 가장 가까운 요구서는?", "사전에 없는 지표를 요구한 적이 있나"]
    q = st.text_input("질문", placeholder=examples[0], key="qa_q")
    c1, c2 = st.columns([1, 4])
    go = c1.button("질문", type="primary", disabled=not q.strip())
    c2.caption("예시: " + " · ".join(examples[1:4]))
    if go:
        with st.spinner("이력 조회 중…"):
            res = history_qa.ask(q) if HAS_API else {"text": None, "trace": [], "how": "키 없음 — 키워드 검색"}
            if not (res.get("text") or "").strip(): res["text"] = None           # 빈 응답도 '답 없음'으로 보고 키워드 검색으로 보완
            kw = history_qa.keyword_search(q) if res.get("text") is None else None
        st.session_state.setdefault("qa_log", []).insert(0, {"q": q, "res": res, "kw": kw})
        st.session_state["qa_log"] = st.session_state["qa_log"][:5]
    for i, e in enumerate(st.session_state.get("qa_log", [])):
        with st.container(border=True):
            st.markdown(f"**Q. {e['q']}**")
            res, kw = e["res"], e["kw"]
            if res.get("text"):
                st.markdown(res["text"]); st.caption(res.get("how", ""))
                with st.expander(f"근거 레코드 — 도구 호출 {len(res['trace'])}회"):
                    for t in res["trace"]:
                        st.markdown(f"`{t['tool']}` {json.dumps(t['input'], ensure_ascii=False)}" + (f" → {t['rows']}건" if t.get("rows") is not None else ""))
                        r_ = t.get("result")
                        if isinstance(r_, list) and r_ and isinstance(r_[0], dict): st.dataframe(pd.DataFrame(r_), width="stretch", height=min(300, 40 + 35 * len(r_)))
                        elif isinstance(r_, dict) and "error" in r_: st.error(r_["error"])
            else:
                st.caption(res.get("how", ""))
                if kw:
                    st.markdown(f"검색어: {', '.join(kw['tokens']) or '-'} · 인식 지표: {', '.join(kw['indicators']) or '-'}")
                    if kw["requests"]: st.dataframe(pd.DataFrame(kw["requests"]).rename(columns={"id": "요구번호", "requester": "요청 주체", "received_date": "접수일", "due_date": "제출기한", "title": "제목"}), width="stretch", hide_index=True)
                    else: st.info("일치하는 요구서가 없습니다.")
                    if kw["reasons"]: st.caption("관련 차이 사유 기록"); st.dataframe(pd.DataFrame(kw["reasons"])[["indicator", "center", "base_date", "old_value", "new_value", "reason", "created_at"]].rename(columns={"indicator": "지표", "center": "센터", "base_date": "기준일", "old_value": "과거값", "new_value": "신규값", "reason": "사유", "created_at": "입력일"}), width="stretch", hide_index=True)
            if i == 0 and res.get("text") is None and HAS_API: st.warning("Claude 응답이 없어 키워드 검색 결과를 표시했습니다.")

# ================= 설정 =================
else:
    import providers
    st.header("설정")
    st.subheader("AI 공급자·키 (이 브라우저 세션에만 적용)")
    st.write("접속한 사람마다 자기 키를 넣어 씁니다. 키는 서버 파일이나 다른 접속자에게 저장·공유되지 않고, 브라우저 탭을 닫으면 사라집니다. "
             "운영자가 서버 환경변수에 공통 키를 두었다면 빈 칸으로 두어도 그 키로 동작합니다. 공급자는 모듈로 교체할 수 있습니다(아래 '공급자 추가 방법').")
    cfg = st.session_state["llm_cfg"]
    pnames = providers.names()
    prov_name = st.selectbox("공급자", pnames, index=pnames.index(cfg.get("provider")) if cfg.get("provider") in pnames else 0,
                             format_func=lambda n: f"{providers.PROVIDERS[n].label} ({n})")
    pcls = providers.PROVIDERS[prov_name]
    new_cfg = {"provider": prov_name}
    for f in pcls.fields:
        env_set = bool(os.environ.get(f.get("env", ""), ""))
        hint = " · 서버 환경변수에 값이 있어 비워도 됨" if env_set else ""
        new_cfg[f["key"]] = st.text_input(f["label"] + hint, value=cfg.get(f["key"], "") or "", type="password" if f.get("secret") else "default", key=f"cfg_{prov_name}_{f['key']}").strip()
    models = st.session_state.get("model_list") or []
    model_ids = [m["id"] for m in models]
    pick = st.selectbox("모델 (비우면 공급자 기본 후보 순으로 자동: " + " → ".join(pcls.default_models) + ")", ["(자동)"] + model_ids + ["직접 입력"],
                        index=(model_ids.index(cfg["model"]) + 1) if cfg.get("model") in model_ids else (len(model_ids) + 1 if cfg.get("model") else 0))
    if pick == "직접 입력": new_cfg["model"] = st.text_input("모델 id", value=cfg.get("model", "") or "").strip()
    elif pick == "(자동)": new_cfg["model"] = ""
    else: new_cfg["model"] = pick
    c1, c2, c3 = st.columns(3)
    if c1.button("적용", type="primary"):
        changed = {k: v for k, v in new_cfg.items() if v != cfg.get(k)}
        cfg.clear(); cfg.update(new_cfg)
        if "model" in changed or "provider" in changed: st.session_state["llm_state"] = {}    # 모델·공급자가 바뀌면 '성공한 모델' 기억을 지움
        st.rerun()
    if c2.button("키 지우기"):
        cfg.clear(); st.session_state["llm_state"] = {}; st.session_state["model_list"] = []; st.rerun()
    if c3.button("연결 테스트", disabled=not llm.available()):
        with st.spinner("호출 중…"):
            r = llm.test_connection()
        if r.get("ok"): st.success(f"연결 성공 · 공급자 {r.get('provider')} · 모델 {r['model']} · 왕복 {r['latency_s']}초 · 응답 {r.get('reply')}")
        else: st.error(f"연결 실패: {r.get('error')}")
        if r.get("models"):
            st.session_state["model_list"] = r["models"]
            st.caption("이 키로 쓸 수 있는 모델 — 위 '모델' 목록에 반영됨(다시 적용 필요)"); st.dataframe(pd.DataFrame(r["models"]), width="stretch", height=200)
        elif r.get("models_error"): st.caption(f"모델 목록 조회 실패: {r['models_error']}")
    st.caption(f"현재: 공급자 {llm.provider_name()} · {'키 있음' if HAS_API else '키 없음(규칙 기반)'} · 모델 {llm.model_label()}")
    if providers._errors: st.warning("불러오지 못한 공급자 플러그인: " + "; ".join(f"{k}: {v}" for k, v in providers._errors.items()))
    with st.expander("공급자 추가 방법(모듈 교체)"):
        st.markdown("1. `docs/provider_template.py`를 복사해 `app/provider_<이름>.py`로 저장\n2. `name`·`label`·`fields`(입력칸)·`default_models`·`create_message()`를 채움. 응답은 Anthropic SDK 메시지와 같은 모양(`content` 블록, `stop_reason`, `usage`)이면 됨 — `providers.SimpleMessage`로 감싸면 됨\n3. 앱을 다시 시작하면 이 목록에 나타남. 기존 코드는 수정하지 않음\n4. 운영자가 서버 환경변수 `LLM_PROVIDER=<이름>`을 두면 그 공급자가 기본 선택")
    if llm.log():
        st.caption("이번 세션 호출 기록(내 키로 한 호출만)"); st.dataframe(pd.DataFrame(llm.log()), width="stretch", height=160)
    st.write("추출 품질 점검: 터미널에서 `python check_llm.py` 실행 → `storage/llm_check_날짜.md` (샘플 4건 + 실전형 5건을 규칙/Claude 양쪽으로 채점).")
    st.divider()
    st.write("회신 템플릿: `templates/` 폴더의 HWPX. 실제 부서 서식을 한글에서 열어 {{수신}} {{제목}} {{본문}} {{row.center}} 같은 자리표시자를 넣고 저장하면 그대로 사용됩니다.")
    st.write("지표 동의어 사전: `normalize.py`의 CANON. 데이터 카탈로그: `suggest.py`의 CATALOG.")
    sure = st.checkbox("이력 DB를 전부 삭제하는 데 동의합니다(되돌릴 수 없음)")
    if st.button("이력 DB 초기화(전체 삭제)", disabled=not sure):
        db.reset(); st.session_state.clear(); st.rerun()
    if st.button("샘플 데이터·템플릿 다시 생성"):
        import importlib, make_sample_data
        importlib.reload(make_sample_data); st.success("샘플 데이터 생성 완료")
        try:
            import make_template; make_template.main(); st.success("템플릿 생성 완료")
        except FileNotFoundError:
            st.info("템플릿 원본(테스트_HWPX_치환_행추가_v2.hwpx)이 상위 폴더에 없어 templates/의 기존 샘플을 그대로 씁니다.")

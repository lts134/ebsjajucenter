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
import db, extract, compare, pii, docread, normalize, search, suggest, draft, hwpx_out, llm

st.set_page_config(page_title="EBS 대외 요구자료 대응 에이전트", layout="wide")
HERE = Path(__file__).parent
SAMPLE = HERE / "sample_data"
TPL_DIR = HERE / "templates"
OUT_DIR = HERE / "storage" / "out"; OUT_DIR.mkdir(parents=True, exist_ok=True)
HAS_API = llm.available()

with st.sidebar:
    st.title("대외 요구자료 대응 에이전트")
    page = st.radio("메뉴", ["① 과거 자료 등록", "② 새 요구서 분석", "③ 수치 대조·점검표", "④ 회신 초안·HWPX",
                          "⑤ 검토·승인", "⑥ 이력 조회", "⑦ 현황·통계", "설정"])
    st.divider()
    st.caption(f"Claude API: {'연결됨 · ' + llm.model_label() if HAS_API else '없음 → 규칙 기반'}")
    if HAS_API and llm.LOG:
        u = llm.usage_summary(); st.caption(f"이번 세션 호출 {u['calls']}회 · 토큰 {u['input_tokens']}/{u['output_tokens']} · 평균 {u['avg_latency_s']}초")
    st.caption(f"이력 DB: {db.DB_PATH.name}")

def read_doc(uploaded) -> str:
    try:
        return docread.read(uploaded.name, uploaded.getvalue())
    except Exception as e:
        st.error(f"본문 추출 실패: {e}"); return ""

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
            res, how = analyze(req_text)
            st.session_state.update(reg_extract=res, reg_how=how, reg_text=req_text, reg_name=req_name)
    with col2:
        st.subheader("2) 그때 제출한 값(집계 엑셀)")
        use_sample_v = st.checkbox("샘플 제출본 사용", value=True)
        if use_sample_v:
            vfiles = sorted(SAMPLE.glob("등원율_*.xlsx"))
            vpick = st.selectbox("샘플 제출본", vfiles, format_func=lambda p: p.name)
            vdf, vname = compare.load_values(vpick), vpick.name
        else:
            vup = st.file_uploader("제출값 엑셀/CSV (컬럼: 지표명, 센터명, 기준일, 값, 지표 정의, 집계기간, 추출시점, 원자료 버전)", type=["xlsx", "csv"], key="reg_val")
            vdf, vname = (compare.load_values(vup), vup.name) if vup else (None, "")
        if vdf is not None:
            st.dataframe(vdf[VAL_COLS].rename(columns=VAL_KO), height=220, width="stretch")
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
            sid = db.add_submission(rid, submitted_date, "담당자", vname, "confirmed", "과거 제출본 등록", vals)
            st.success(f"저장 완료: 요구서 #{rid}, 제출본 #{sid}, 제출값 {len(vals)}건")
            del st.session_state["reg_extract"]

# ================= ② 새 요구서 분석 =================
elif page == "② 새 요구서 분석":
    st.header("② 새 요구서 → 항목 추출·지표 정규화 → 유사 요구 검색 → 항목별 데이터 제안")
    use_sample = st.checkbox("샘플 새 요구서 사용", value=True)
    if use_sample:
        text, name = (SAMPLE / "새요구서_의원실_2026-09-15.txt").read_text(encoding="utf-8"), "새요구서_의원실_2026-09-15.txt"
    else:
        up = st.file_uploader("새 요구서 (txt / hwpx / pdf / docx)", type=["txt", "hwpx", "pdf", "docx"], key="new_up")
        text, name = (read_doc(up), up.name) if up else ("", "")
    text = st.text_area("요구서 원문", text, height=200)
    if st.button("분석", type="primary", disabled=not text):
        res, how = analyze(text)
        st.session_state.update(new_req=res, new_how=how, new_text=text, new_name=name)
    if "new_req" in st.session_state:
        res = st.session_state["new_req"]
        st.caption(f"추출 방식: {st.session_state['new_how']} · 지표 정규화: 동의어 사전{' + Claude' if HAS_API else ''}")
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
        else:
            up = st.file_uploader("새 집계 엑셀/CSV", type=["xlsx", "csv"], key="cmp_new"); new_df = compare.load_values(up) if up else None
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
        reasons = {}
        for _, r in m[m["판정"] == "차이"].iterrows():
            key = (r["indicator"], r["center"], r["base_date"])
            prev = db.reasons_for(*key)
            reasons[key] = st.text_input(f"{r['center']} · {r['indicator']} · {r['base_date']}  ({r['old_value']} → {r['new_value']})",
                                         value=prev[0]["reason"] if prev else "", placeholder="예: 8/5 출결 사후 보정 반영", key=f"reason_{key}")
        ck = compare.checklist(m, reasons)
        st.subheader("제출 전 정합성 점검표")
        st.dataframe(ck, width="stretch")
        st.subheader("개인정보 검사")
        hits = pii.scan_df(new_df.drop(columns=["source_file", "source_sheet", "source_row"], errors="ignore"), "새 집계값")
        if hits: st.error(f"개인정보 의심 패턴 {len(hits)}건"); st.dataframe(pd.DataFrame(hits), width="stretch")
        else: st.success("개인정보 의심 패턴 없음")
        buf = io.BytesIO(); ck.to_excel(buf, index=False)
        st.download_button("점검표 엑셀", buf.getvalue(), file_name=f"정합성점검표_{dt.date.today()}.xlsx")
        if st.button("사유 저장 + 새 제출본 확정 → ④로", type="primary", disabled=target is None):
            sid = db.add_submission(target["id"], str(dt.date.today()), "담당자", "새 집계값", "confirmed", "③ 대조 후 확정", new_df.to_dict("records"))
            for key, reason in reasons.items():
                if reason.strip():
                    row = m[(m["indicator"] == key[0]) & (m["center"] == key[1]) & (m["base_date"] == key[2])].iloc[0]
                    db.add_reason(sid, *key, row["old_value"], row["new_value"], reason, "담당자")
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
        st.subheader("HWPX 출력")
        tpls = sorted(TPL_DIR.glob("*.hwpx"))
        tpl = st.selectbox("템플릿", tpls, format_func=lambda p: p.name) if tpls else None
        up_tpl = st.file_uploader("또는 템플릿 업로드 ({{키}} 자리표시자가 있는 HWPX)", type=["hwpx"], key="tpl_up")
        tpl_bytes = up_tpl.getvalue() if up_tpl else (tpl.read_bytes() if tpl else None)
        if tpl_bytes:
            st.caption("템플릿 자리표시자: " + ", ".join(hwpx_out.placeholders(tpl_bytes)))
            fill = {"수신": req["requester"] or "", "발신": "EBS 지역교육협력부", "제목": d["제목"],
                    "요구항목": "\n".join(f"{i}. {it['item_text']}" for i, it in enumerate(items, 1)),
                    "본문": d["본문"], "차이사유": d["차이사유"], "산출근거": d["산출근거"], "담당자": "담당자 [확인 필요]"}
            rows = [{"no": i + 1, "indicator": v["indicator"], "center": v["center"], "base_date": v["base_date"], "value": v["value"]} for i, (_, v) in enumerate(values.iterrows())]
            out = hwpx_out.render(tpl_bytes, fill, rows)
            fname = f"회신_{req['id']}_{dt.date.today()}.hwpx"
            c1, c2, c3 = st.columns(3)
            c1.download_button("회신 HWPX 다운로드", out, file_name=fname)
            ckb = io.BytesIO(); pd.DataFrame(st.session_state.get("last_checklist") or []).to_excel(ckb, index=False)
            zb = io.BytesIO()
            with zipfile.ZipFile(zb, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(fname, out)
                zf.writestr("정합성점검표.xlsx", ckb.getvalue())
                vb = io.BytesIO(); values.to_excel(vb, index=False); zf.writestr("확정수치.xlsx", vb.getvalue())
                zf.writestr("근거_사유_메타.json", json.dumps({"요구": req, "제출본": sid, "차이 사유": {" | ".join(k): v for k, v in reasons_raw.items()}, "산출 근거": prov, "초안": d, "생성일": str(dt.date.today())}, ensure_ascii=False, indent=2, default=str))
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
                if c1.button("승인", key=f"ok_{d['id']}", type="primary"): db.add_review(d["id"], "팀장", "approved", cmt); st.rerun()
                if c2.button("반려", key=f"no_{d['id']}"): db.add_review(d["id"], "팀장", "rejected", cmt); st.rerun()
                if c3.button("의견만", key=f"c_{d['id']}"): db.add_review(d["id"], "팀장", "comment", cmt); st.rerun()

# ================= ⑥ 이력 조회 =================
elif page == "⑥ 이력 조회":
    st.header("⑥ 요구·제출·초안 이력")
    reqs = db.list_requests()
    if not reqs: st.info("등록된 요구서가 없습니다.")
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
        c1, c2, c3 = st.columns(3)
        c1.metric("요구서", len(df)); c2.metric("진행 중", int((df["상태"] == "진행 중").sum())); c3.metric("기한 경과(미제출)", int((df["상태"] == "기한 경과").sum()))
        st.dataframe(show[["번호", "상태", "D-day", "요청 주체", "접수일", "제출기한", "제목", "항목 수", "확정 제출본", "최근 제출일"]], width="stretch")
        rows = [{"요구번호": r["id"], "요청 주체": r["requester"], "접수일": r["received_date"], "제출기한": r["due_date"], "제목": r["title"], "항목": it["item_text"], "지표": it["indicator"], "기준일": it["base_date"], "단위": it["unit"], "확정 제출본 수": r["n_confirmed"], "최근 제출일": r["last_submitted"]} for r in ov for it in db.get_items(r["id"])]
        lb = io.BytesIO(); pd.DataFrame(rows).to_excel(lb, index=False)
        st.download_button("관리대장 엑셀 내보내기", lb.getvalue(), file_name=f"요구자료_관리대장_{dt.date.today()}.xlsx")
        st.divider()
        by_req, by_ind = db.requester_stats()
        c1, c2 = st.columns(2)
        with c1: st.subheader("요청 주체별"); st.dataframe(pd.DataFrame(by_req).rename(columns={"requester": "요청 주체", "n_requests": "요구 건수", "n_items": "항목 수", "first_date": "최초", "last_date": "최근"}), width="stretch")
        with c2: st.subheader("반복 요구 지표"); st.dataframe(pd.DataFrame(by_ind).rename(columns={"indicator": "지표", "n_times": "요구 횟수", "n_requesters": "요청 주체 수", "base_dates": "기준일들"}), width="stretch")
        st.caption("반복 요구 지표는 사전 산출·표준 답변 후보입니다.")

# ================= 설정 =================
else:
    st.header("설정")
    st.subheader("Claude API")
    st.write("키는 환경변수 `ANTHROPIC_API_KEY`로 두거나, 아래에 이 실행 동안만 입력합니다(파일에 저장되지 않음). 없으면 규칙 기반으로 동작합니다.")
    st.code('set ANTHROPIC_API_KEY=sk-ant-...   (cmd)\n$env:ANTHROPIC_API_KEY="sk-ant-..."   (PowerShell)')
    key = st.text_input("API 키(세션 한정)", type="password")
    if key:
        os.environ["ANTHROPIC_API_KEY"] = key.strip()
        if not HAS_API: st.rerun()                                      # 사이드바 상태·④ 'Claude 추가 점검' 버튼을 바로 갱신
        st.success("이 세션에 적용됨. 아래 연결 테스트로 확인하세요.")
    ws = st.text_input("워크스페이스 ID(ANTHROPIC_WORKSPACE_ID, wrkspc_…) — 키가 워크스페이스에 묶여 있지 않을 때만 필요", os.environ.get("ANTHROPIC_WORKSPACE_ID", ""))
    if ws.strip(): os.environ["ANTHROPIC_WORKSPACE_ID"] = ws.strip()
    elif "ANTHROPIC_WORKSPACE_ID" in os.environ: del os.environ["ANTHROPIC_WORKSPACE_ID"]
    model = st.text_input("모델명(CLAUDE_MODEL, 비우면 자동: " + " → ".join(llm.CANDIDATES) + ")", os.environ.get("CLAUDE_MODEL", ""))
    if model.strip(): os.environ["CLAUDE_MODEL"] = model.strip()
    elif "CLAUDE_MODEL" in os.environ: del os.environ["CLAUDE_MODEL"]
    if st.button("연결 테스트", type="primary", disabled=not llm.available()):
        with st.spinner("호출 중…"):
            r = llm.test_connection()
        if r.get("ok"): st.success(f"연결 성공 · 모델 {r['model']} · 왕복 {r['latency_s']}초 · 응답 {r.get('reply')}")
        else: st.error(f"연결 실패: {r.get('error')}")
        if r.get("models"):
            st.caption("이 키로 쓸 수 있는 모델(위 모델명 칸에 id를 넣으면 고정)"); st.dataframe(pd.DataFrame(r["models"]), width="stretch", height=200)
        elif r.get("models_error"): st.caption(f"모델 목록 조회 실패: {r['models_error']}")
    if llm.LOG:
        st.caption("이번 세션 호출 기록"); st.dataframe(pd.DataFrame(llm.LOG), width="stretch", height=160)
    st.write("추출 품질 점검: 터미널에서 `python check_llm.py` 실행 → `storage/llm_check_날짜.md` (샘플 4건 + 실전형 5건을 규칙/Claude 양쪽으로 채점).")
    st.divider()
    st.write("회신 템플릿: `templates/` 폴더의 HWPX. 실제 부서 서식을 한글에서 열어 {{수신}} {{제목}} {{본문}} {{row.center}} 같은 자리표시자를 넣고 저장하면 그대로 사용됩니다.")
    st.write("지표 동의어 사전: `normalize.py`의 CANON. 데이터 카탈로그: `suggest.py`의 CATALOG.")
    if st.button("이력 DB 초기화(전체 삭제)"):
        db.reset(); st.session_state.clear(); st.rerun()
    if st.button("샘플 데이터·템플릿 다시 생성"):
        import importlib, make_sample_data
        importlib.reload(make_sample_data); st.success("샘플 데이터 생성 완료")
        try:
            import make_template; make_template.main(); st.success("템플릿 생성 완료")
        except FileNotFoundError:
            st.info("템플릿 원본(테스트_HWPX_치환_행추가_v2.hwpx)이 상위 폴더에 없어 templates/의 기존 샘플을 그대로 씁니다.")

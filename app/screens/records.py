"""검토·승인(팀장), 기록(찾기·고치기 / 물어보기), 현황(상태 흐름·건 관리·관리대장·월간 리포트)."""
import io, datetime as dt
import pandas as pd
import streamlit as st
import db, extract, pii, normalize, llm, history_qa, ui, report
from screens.common import OUT_DIR, ctx, run_ai, _q, ko, _requester_input, confirm_delete, VAL_KO, ITEM_KO

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
                if c1.button("승인", key=f"ok_{d['id']}", type="primary"): db.add_review(d["id"], ctx.REVIEWER, "approved", cmt); st.rerun()
                if c2.button("반려", key=f"no_{d['id']}"): db.add_review(d["id"], ctx.REVIEWER, "rejected", cmt); st.rerun()
                if c3.button("의견만", key=f"c_{d['id']}"): db.add_review(d["id"], ctx.REVIEWER, "comment", cmt); st.rerun()

# ================= 기록 조회 =================

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
                             column_config={"item_text": "항목 원문", "indicator": st.column_config.SelectboxColumn("지표명(정규화)", options=list(dict.fromkeys(list(normalize.CANON) + [c["indicator"] for c in db.indicator_catalog()])), required=False),
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
            asg = b.text_input("담당자", tgt.get("assignee") or "", key=f"mg_as_{tgt['id']}", placeholder=ctx.USER)
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
    st.caption("말로 물으면 기록을 찾아 근거(요구번호·제출일·기관)와 함께 답합니다. 평균·증감 같은 계산은 하지 않습니다." + ("" if ctx.HAS_API else " 지금은 AI 연결이 없어 키워드 검색으로 동작합니다."))
    examples = ["감사실에 등원율을 언제 어떤 값으로 냈지?", "2026-06-30 기준 등원율을 제출한 기관과 날짜를 전부 보여줘", "센터C 등원율이 달라진 사유로 뭐라고 적었나", "기한이 가장 가까운 요구서는?"]
    q = st.text_input("질문", placeholder=examples[0], key="qa_q")
    c1, c2 = st.columns([1, 4])
    go_ = c1.button("물어보기", type="primary", disabled=not q.strip())
    c2.caption("예: " + " · ".join(examples[1:]))
    if go_:
        res = run_ai("기록 찾는 중", lambda: history_qa.ask(q), "조회 도구를 골라 몇 차례 호출합니다. 보통 5~30초", "기록 찾는 중… (키워드 검색)") if ctx.HAS_API else {"text": None, "trace": [], "how": "키 없음 — 키워드 검색"}
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
            if i == 0 and res.get("text") is None and ctx.HAS_API: st.warning("AI 응답이 없어 키워드 검색 결과를 표시했습니다.")

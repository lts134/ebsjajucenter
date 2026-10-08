"""자료 화면: 지표 데이터(넣기·보유 히트맵·대시보드 가져오기·센터 명부), 참고 문서, 과거 답변 등록."""
import datetime as dt
import pandas as pd
import streamlit as st
import db, extract, normalize, hwpx_build, ui, dashboard_import, refdocs
from screens.common import _import_dashboard, SAMPLE, ctx, analyze_with_status, request_input, value_sources, _q, _requester_input, VAL_COLS, VAL_KO

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
                bid, n = db.add_data_batch(vdf.to_dict("records"), ctx.USER, vname, note.strip())
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
                try: done.append(refdocs.ingest(f.name, f.getvalue(), title if len(ups) == 1 else None, ctx.USER, note.strip()))
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
            sid = db.add_submission(rid, submitted_date, ctx.USER, vname, "confirmed", "과거 제출본 등록", vals)
            st.session_state["reg_last"] = (rid, sid, len(vals))
            del st.session_state["reg_extract"]; st.rerun()
    if st.session_state.get("reg_last"):
        rid, sid, n = st.session_state["reg_last"]
        c1, c2 = st.columns([4, 1.4])
        c1.success(f"저장했습니다. 요구서 #{rid}, 제출본 #{sid}, 값 {n}건. 다음에 같은 지표·기준일을 물으면 자동으로 찾아 줍니다.")
        if c2.button("잘못 넣었어요 — 되돌리기", key="reg_undo", icon=":material/undo:", help="방금 저장한 요구서·값을 지웁니다. 나중에 고치려면 기록 조회에서 수정·삭제할 수 있습니다."):
            db.delete_request(rid); st.session_state.pop("reg_last"); st.toast(f"요구서 #{rid} 삭제됨"); st.rerun()

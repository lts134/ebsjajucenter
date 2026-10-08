"""홈 = 대화: 첨부 처리, 규칙 경로 답변, 검수 패널(수치·대조·사유·초안·미리보기·확정), 보내기 카드, 오늘 할 일 띠, 다음 행동 칩."""
import re, datetime as dt
import pandas as pd
import streamlit as st
import db, pii, docread, normalize, draft, hwpx_build, llm, history_qa, tabular, ui, agent, refdocs
from screens.common import _import_dashboard, SAMPLE, OUT_DIR, ctx, go, run_ai, _q, _requester_input, VAL_COLS, VAL_KO

# ================= 홈 = 대화 =================
REQ_EXT = (".hwp", ".hwpx", ".pdf", ".docx", ".txt"); VAL_EXT = (".xlsx", ".xls", ".xlsm", ".csv"); DASH_EXT = (".html", ".htm")

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
                bid, n = db.add_data_batch(recs, ctx.USER, name, "대화 첨부")
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
    out = agent.approve(case, ctx.USER, ctx.REVIEWER, OUT_DIR)
    for v in case["reasons"].values(): db.add_phrase("사유", v, ctx.USER)                     # 쓴 사유는 문구 서랍에 쌓인다(다음 건에서 고를 수 있게)
    st.session_state["chat_msgs"] = []; st.session_state["chat_carry"] = f"직전 작업을 확정했습니다(제출본 #{out['submission_id']}, 초안 #{out['draft_id']}). 새 요구는 새 작업으로 처리합니다."

def _send_card(case: agent.Case, req: dict):
    """확정 뒤: 받을 파일·메일 문안·발송 기록. 메일 발송은 담당자가 한다(이 앱은 보내지 않는다)."""
    sid, did = case["approved"]; d = case["draft"] or {}
    st.success(f"확정했습니다 · 제출본 #{sid} · 초안 #{did} · 값 {len(case['values']) if case['values'] is not None else 0}건. 파일을 받아 보내고, 보낸 뒤 발송을 기록하세요.")
    c1, c2 = st.columns([1.2, 1])
    with c1:
        if case["hwpx"]: st.download_button("회신 HWPX 받기", case["hwpx"], file_name=case["hwpx_name"], key=f"rv_dl_done_{sid}", icon=":material/download:", type="primary")
        mail = draft.mail_text(req or {}, d, case["hwpx_name"], ctx.USER, org=f"{ctx.ORG['company']} {ctx.ORG['org_name']}".strip(), greeting=(case.get("tone") or {}).get("mail_greeting"))
        st.text_input("메일 제목", mail["subject"], key=f"mail_subj_{sid}")
        st.text_area("메일 본문 — 복사해서 메일 프로그램에 붙이세요", mail["body"], height=170, key=f"mail_body_{sid}")
    with c2:
        st.markdown("**발송 기록** — 보낸 뒤 적어 두면 현황에 '발송 완료'로 표시됩니다.")
        sent_to = st.text_input("보낸 곳", (req or {}).get("requester") or "", key=f"disp_to_{sid}")
        method = st.selectbox("방법", ["메일", "공문(전자문서)", "팩스", "직접 전달", "기타"], key=f"disp_m_{sid}")
        sent_at = st.date_input("보낸 날짜", dt.date.today(), key=f"disp_d_{sid}")
        memo = st.text_input("메모(선택)", key=f"disp_n_{sid}", placeholder="예: 담당 보좌관 메일, 공문번호")
        if st.button("발송 기록 저장", key=f"disp_save_{sid}", type="primary", icon=":material/send:", disabled=not sent_to.strip()):
            db.add_dispatch(sid, did, str(sent_at), sent_to.strip(), method, ctx.USER, memo.strip()); st.toast("발송을 기록했습니다"); st.rerun()
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
        if case.get("match_notes") and not locked: st.info("요구 이름과 보유 자료 이름이 달라 가진 자료에 맞춘 항목: " + " / ".join(case["match_notes"]) + " — 다르면 '단계 화면에서 자세히 고치기'에서 지표를 바꾸세요.", icon=":material/join_inner:")
        if case.get("doc_hits") and not locked: st.caption("설명 항목은 참고 문서 발췌로 썼습니다: " + " / ".join(f"'{t}' ← " + ", ".join(f"「{h['doc']}」 {h['page']}쪽" for h in hs[:2]) for t, hs in case["doc_hits"].items()))
        missing = [case["items"][i]["item_text"] for i, pl in enumerate(case["plans"]) if (not pl["options"] and pl["mode"] != "docs") or pl["mode"] in ("none", "unknown_indicator")]
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
                        if cur.strip() and st.button("지금 문구를 서랍에 저장", key=f"rv_ph_save_{k}_{rev}", type="tertiary", icon=":material/bookmark_add:"): db.add_phrase("사유", cur, ctx.USER); st.toast("저장했습니다"); st.rerun()
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
        if case["compare"] is not None and int((case["compare"]["판정"] == "차이").sum()) and ctx.HAS_API: opts.append(("차이 난 행 설명", ("chat", "차이 난 센터마다 지난번 값·이번 값·채워진 사유를 짧게 정리해 줘")))
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
    ui.page_title(f"{ctx.USER}님, 무엇을 할까요", "", "홈")
    case: agent.Case = st.session_state.setdefault("case", agent.Case())
    hist: list = st.session_state.setdefault("chat", []); st.session_state.setdefault("chat_msgs", [])
    tpl = _template_bytes(); case.update(dept_head=ctx.ORG["dept_head"], phone=ctx.ORG["dept_phone"], org=ctx.ORG["org_name"], company=ctx.ORG["company"])
    job = st.session_state.get("chat_job")                      # 처리 중이던 작업(화면이 다시 실행돼도 결과를 잃지 않게 세션에 둔다)
    if not ctx.HAS_API and st.session_state.get("ai_banner_seen"):
        c1, c2 = st.columns([6, 1], vertical_alignment="center")
        with c1: ui.status_line(False, "AI 연결 안 됨 · 규칙 기반으로 동작합니다(요구서 읽기·초안 정확도 낮음)")
        if c2.button("설정으로", key="home_settings", type="tertiary"): go("설정")
    _todo_strip()
    if not hist:
        with st.container(border=True):
            st.markdown("**요구서를 붙이고 말하면 됩니다.** 예: \"이 요구서에서 요구하는 것들 작성해 줘\"  \n요구 항목을 읽고 → 가진 자료(지표 데이터·과거 제출값)에서 기준일을 정해 값을 모으고 → 과거 제출값과 맞춰 보고 → 차이 사유 후보를 채우고 → 회신 초안과 HWPX를 만듭니다. 사람은 아래 검수 화면에서 확인하고 승인합니다.")
            st.caption("집계 엑셀이나 통합 대시보드 저장 파일(.html)을 함께 붙이면 지표 데이터에 넣고 씁니다. 과거 기록은 그냥 물어보면 됩니다: \"감사실에 등원율 언제 어떤 값으로 냈지?\" 사업 자체 질문(\"운영 시간은?\")은 '참고 문서'에 지침·매뉴얼을 올려 두면 그 문구로 답합니다." + ("" if ctx.HAS_API else "  \nAI 연결이 없어 지금은 정해진 순서로 처리하고 질문은 키워드 검색으로 답합니다."))
            if ctx.DEMO:
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
        if ctx.HAS_API:
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

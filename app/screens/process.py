"""새 요구서 처리 단계 화면(1 읽기 → 2 수치 맞춰 보기 → 3 회신 초안). 메뉴에는 없고 홈 검수의 '자세히 고치기'로만 들어온다."""
import io, datetime as dt, zipfile, json
import pandas as pd
import streamlit as st
import db, compare, pii, search, suggest, draft, hwpx_out, assist, ui, plan
from screens.common import SAMPLE, TPL_DIR, OUT_DIR, ctx, go, run_ai, analyze_with_status, request_input, value_sources, render_hwpx_cached, req_label, VAL_COLS, STEPS

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
    if res.get("_match_notes"): st.info("요구 이름과 보유 자료 이름이 달라 가진 자료에 맞춘 항목: " + " / ".join(res["_match_notes"]) + " — 다르면 아래 항목의 지표를 바꾸세요.", icon=":material/join_inner:")
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
            hint = it.get("suggested") or it.get("indicator_hint")
            st.caption(f"지표 {it.get('indicator') or ('없음 · 모델 판단: ' + hint if hint else '사전에 없음')}{mb} · 기준일 {it.get('base_date') or '없음'} · 기간 {it.get('period') or '-'} · 단위 {it.get('unit') or '-'}")
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
                tone = {"exact": "ok", "all": "ok", "latest": "warn", "nearest": "warn", "yearly": "ok", "quarterly": "ok", "range": "ok", "docs": "ok"}.get(pl["mode"], "bad")
                icon = {"ok": ":material/check_circle:", "warn": ":material/help:", "bad": ":material/cancel:"}[tone]
                a.markdown(f"{icon} {pl['why']}")
                if pl["mode"] == "docs": b.caption("참고 문서 발췌로 초안 작성: " + ", ".join(f"「{h['doc']}」 {h['page']}쪽" for h in pl["doc_hits"][:3]))
                if pl["options"] and pl["mode"] not in ("none", "unknown_indicator"):
                    many = len(pl["options"]) > 1
                    picked = b.multiselect("낼 기준일", pl["options"], default=pl["dates"], key=f"cmp_dates_{target['id']}_{i}", help="제안이 기본값입니다. 더하거나 빼면 그대로 가져옵니다.") if many else pl["dates"]
                    chosen[i] = picked
                    b.caption(f"{'지표 데이터' if pl['source'] == 'data' else '과거 제출값'} · {len(picked)}개 기준일 · 약 {plan._count(pl['indicator'], picked, pl['source'])}건")
        pullable = {i: d for i, d in chosen.items() if d}
        missing = [it for it, pl in zip(items, plans, strict=True) if (not pl["options"] and pl["mode"] != "docs") or pl["mode"] in ("none", "unknown_indicator")]
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
        sid = db.add_submission(target["id"], str(dt.date.today()), ctx.USER, "새 집계값", "confirmed", "대조 후 확정", new_df.to_dict("records"))
        for key, reason in reasons.items():
            if reason.strip() and m is not None:
                row = m[(m["indicator"] == key[0]) & (m["center"] == key[1]) & (m["base_date"] == key[2])].iloc[0]
                db.add_reason(sid, *key, row["old_value"], row["new_value"], reason, ctx.USER)
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
    if ctx.HAS_API and st.button("AI로 한 번 더 점검 (누락·불일치·단정 표현)"):
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
    fill = {"수신": req["requester"] or "", "발신": f"{ctx.ORG['company']} {ctx.ORG['org_name']}".strip(), "제목": d["제목"],
            "요구항목": "\n".join(f"{i}. {it['item_text']}" for i, it in enumerate(items, 1)),
            "본문": d["본문"], "차이사유": d["차이사유"], "산출근거": d["산출근거"], "담당자": ctx.USER}
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

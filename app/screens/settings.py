"""설정: AI 연결, 서식·사전(조직 설정·문구 서랍·요청 주체 사전·지표 사전·회신 톤·서식), 백업·초기화."""
import os, re, datetime as dt
import pandas as pd
import streamlit as st
import db, extract, compare, normalize, suggest, draft, hwpx_out, llm, ui
from screens.common import SAMPLE, ctx, run_ai

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
        ui.status_line(ctx.HAS_API, f"현재: 공급자 {llm.provider_name()} · {'키 있음' if ctx.HAS_API else '키 없음(규칙 기반)'} · 모델 {llm.model_label()}")
        if providers._errors: st.warning("불러오지 못한 공급자 플러그인: " + "; ".join(f"{k}: {v}" for k, v in providers._errors.items()))
        with st.expander("공급자 추가 방법(모듈 교체)"):
            st.markdown("1. `docs/provider_template.py`를 복사해 `app/provider_<이름>.py`로 저장\n2. `name`·`label`·`fields`·`default_models`·`create_message()`를 채움(응답은 `providers.SimpleMessage`로 감싸면 됨)\n3. 앱을 다시 시작하면 이 목록에 나타남\n4. 서버 환경변수 `LLM_PROVIDER=<이름>`으로 기본 선택")
        if llm.log():
            with st.expander("이 세션 호출 기록"): st.dataframe(pd.DataFrame(llm.log()), width="stretch", height=160, hide_index=True)
    with tab_data:
        st.markdown("**회신 서식**: `templates/` 폴더의 HWPX. 실제 부서 서식을 한글에서 열어 {{수신}} {{제목}} {{본문}} {{row.center}} 같은 자리표시자를 넣고 저장하면 그대로 쓰입니다.")
        st.markdown("**조직 설정** — 답변자료의 【확인 : 부서장 ☎ 내선】 줄, 발신 표기, 메일 문안에 쓰입니다. 기록 DB에 저장되어 접속자 모두에게 적용됩니다(환경변수 DEPT_HEAD·DEPT_PHONE은 비어 있을 때의 보조값).")
        c1, c2, c3, c4 = st.columns(4)
        o_head = c1.text_input("부서장 이름", ctx.ORG["dept_head"], key="cfg_dept_head"); o_phone = c2.text_input("내선 번호", ctx.ORG["dept_phone"], key="cfg_dept_phone")
        o_org = c3.text_input("부서명(발신)", ctx.ORG["org_name"], key="cfg_org_name"); o_co = c4.text_input("기관명", ctx.ORG["company"], key="cfg_company")
        if st.button("조직 설정 저장", key="cfg_org_save", type="primary", icon=":material/save:"):
            db.set_settings({"dept_head": o_head, "dept_phone": o_phone, "org_name": o_org or "지역교육협력부", "company": o_co or "한국교육방송공사"}, ctx.USER); st.toast("저장했습니다"); st.rerun()
        st.divider()
        st.markdown("**자주 쓰는 문구** — 검수 화면의 사유 입력칸 옆 '문구'에서 고를 수 있습니다. 확정할 때 쓴 사유는 자동으로 쌓이고, 많이 쓴 순으로 보입니다.")
        pk = st.selectbox("종류", ["사유", "정의", "안내"], key="ph_kind")
        a, b = st.columns([5, 1], vertical_alignment="bottom"); new_t = a.text_input("새 문구", key="ph_new", placeholder="예: 출결 사후 보정 반영(재산출)")
        if b.button("추가", key="ph_add", disabled=not new_t.strip(), icon=":material/add:"): db.add_phrase(pk, new_t, ctx.USER); st.rerun()
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
            n = db.save_indicator_dict([{"canon": r["지표"], "aliases": r["동의어(쉼표로 구분)"], "source": r["자료 출처"], "owner": r["담당"], "note": r["비고"]} for r in ed.to_dict("records")], ctx.USER)
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
                        r = db.restore_from_bytes(up_db.getvalue(), ctx.USER)
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
            db.add_submission(rid, "2026-07-08", ctx.USER, "등원율_2026-06-30기준_7월제출본.xlsx", "confirmed", "시연 데이터", vdf.to_dict("records"))
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

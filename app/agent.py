"""대화형 에이전트: 의뢰서 한 장을 붙이고 "요구하는 것들 작성해 줘"라고 하면 끝까지 준비하고, 사람은 검수·승인만 한다.

구조
- Case: 한 건의 작업 상태(요구서 원문·항목·자료 계획·가져온 값·대조·사유·초안·HWPX). 세션에 둔다.
- 단계 함수(step_*): 기존 모듈(extract·plan·compare·assist·draft·hwpx_out)을 감싼 결정적 코드. 값은 계산하지 않고 가진 자료에서만 가져온다.
- autopilot(): 키가 없거나 '그냥 다 해 줘'일 때 표준 순서로 전부 실행.
- TOOLS/handlers(): Claude가 대화 중 골라 쓰는 도구. 단계 함수 + 조정(기준일 바꾸기·사유 적기·초안 고치기) + 이력 조회(history_qa).
원칙: 확정·승인은 사람만 한다(approve()). 에이전트는 준비하고 설명한다."""
from __future__ import annotations
import datetime as dt, re
import pandas as pd
import db, extract, normalize, plan, compare, assist, draft, hwpx_out, hwpx_build, llm, pii, history_qa

VAL_COLS = ["indicator", "center", "base_date", "value", "definition", "calc_period", "extract_date", "source_version"]
SRC_COLS = ["source_file", "source_sheet", "source_row"]

class Case(dict):
    """한 건의 작업 상태. dict라 세션에 그대로 들어간다."""
    def __init__(self, **kw):
        super().__init__(request_text="", request_name="", extracted=None, request_id=None, items=[], plans=[], choices={},
                         values=None, old_sid=None, compare=None, reasons={}, reason_cands=None, draft=None, draft_how="", coverage=None,
                         foresee=None, hwpx=None, hwpx_name="", log=[], uploads=[])
        self.update(kw)
    def note(self, text: str):
        self["log"].append(f"{dt.datetime.now().strftime('%H:%M:%S')} {text}"); return text

# ---------------- 단계 ----------------
def step_read(case: Case) -> dict:
    """요구서 원문 → 항목·지표·기준일·기간. 이미 읽었으면 그대로."""
    if not case["request_text"]: raise ValueError("요구서가 없습니다. 의뢰서 파일을 붙이거나 본문을 보내 주세요.")
    res, how = extract.extract(case["request_text"])
    res["items"] = normalize.normalize_llm(normalize.normalize_items(res.get("items") or []))
    case["extracted"] = res; case["items"] = res["items"]; case["read_how"] = how
    case.note(f"요구서 읽음: 항목 {len(res['items'])}건 ({how})")
    return {"requester": res.get("requester"), "received_date": res.get("received_date"), "due_date": res.get("due_date"), "title": res.get("title"),
            "items": [{"no": i + 1, "item_text": it["item_text"], "indicator": it.get("indicator"), "base_date": it.get("base_date"), "period": it.get("period")} for i, it in enumerate(res["items"])], "how": how}

def step_register(case: Case) -> dict:
    """요구서를 기록에 등록(이미 등록돼 있으면 그 번호)."""
    if case["request_id"]: return {"request_id": case["request_id"], "already": True}
    if not case["extracted"]: step_read(case)
    r = case["extracted"]
    rid = db.add_request(r.get("requester"), r.get("received_date"), r.get("due_date"), r.get("title"), case["request_text"], case["request_name"], case["items"])
    case["request_id"] = rid; case["items"] = db.get_items(rid)
    case.note(f"요구서 #{rid} 등록"); return {"request_id": rid}

def step_plan(case: Case) -> dict:
    """항목마다 가진 자료(지표 데이터 → 과거 제출값) 범위로 낼 기준일을 제안."""
    if not case["items"]: step_read(case)
    anchor = (case["extracted"] or {}).get("received_date")
    case["plans"] = plan.plan(case["items"], anchor)
    case["choices"] = {i: list(pl["dates"]) for i, pl in enumerate(case["plans"]) if pl["dates"]}
    case.note("자료 계획 " + ", ".join(f"항목{i + 1}:{pl['mode']}" for i, pl in enumerate(case["plans"])))
    return {"items": [{"no": i + 1, "item_text": it["item_text"], "indicator": pl["indicator"], "requested_base_date": pl["base_date"], "mode": pl["mode"],
                       "proposed_dates": pl["dates"], "available_dates": pl["options"], "source": pl["source"], "why": pl["why"], "n_rows": pl["n_rows"]}
                      for i, (it, pl) in enumerate(zip(case["items"], case["plans"], strict=True))]}

def set_dates(case: Case, item_no: int, dates: list[str]) -> dict:
    """항목의 낼 기준일을 바꾼다(제안 대신 담당자/모델이 고른 것). 가진 자료에 있는 날짜만 받는다."""
    if not case["plans"]: step_plan(case)
    i = int(item_no) - 1
    if i < 0 or i >= len(case["plans"]): raise ValueError(f"항목 번호 {item_no}가 없습니다.")
    opts = case["plans"][i]["options"]
    bad = [d for d in dates if d not in opts]
    if bad: raise ValueError(f"가진 자료에 없는 기준일: {bad}. 고를 수 있는 것: {opts}")
    case["choices"][i] = list(dates); case["values"] = None
    case.note(f"항목{item_no} 기준일 변경 → {dates}"); return {"item_no": item_no, "dates": dates}

def step_pull(case: Case) -> dict:
    """계획(또는 바꾼 기준일)대로 값을 모아 '이번에 낼 수치'로."""
    if not case["plans"]: step_plan(case)
    rows = []
    for i, d in case["choices"].items():
        pl = case["plans"][i]
        if pl["source"] and d: rows += plan.rows_for(pl["indicator"], d, pl["source"])
    df = pd.DataFrame(rows)
    case["values"] = df[VAL_COLS + SRC_COLS].reset_index(drop=True) if len(df) else None
    missing = [case["items"][i]["item_text"] for i, pl in enumerate(case["plans"]) if not pl["options"] or pl["mode"] in ("none", "unknown_indicator")]
    case.note(f"값 {len(rows)}건 가져옴" + (f", 자료 없는 항목 {len(missing)}건" if missing else ""))
    return {"n_rows": len(rows), "indicators": sorted(df["indicator"].unique().tolist()) if len(df) else [],
            "base_dates": sorted(df["base_date"].unique().tolist()) if len(df) else [], "items_without_data": missing}

def _best_old_sid(case: Case) -> int | None:
    """맞춰 볼 과거 제출본: 계획에 걸린 지표·기준일을 가장 많이 가진 확정 제출본."""
    hits: dict[int, int] = {}
    for i, pl in enumerate(case["plans"]):
        for d in case["choices"].get(i, []):
            for p_ in db.past_values_for(pl["indicator"], d)[:1]: hits[p_["submission_id"]] = hits.get(p_["submission_id"], 0) + 1
    return max(hits, key=hits.get) if hits else None

def step_compare(case: Case, tol: float = 0.0) -> dict:
    """이번 수치를 과거 제출값과 같은 지표·센터·기준일끼리 대조(코드). 차이 행과 단서."""
    if case["values"] is None: step_pull(case)
    if case["values"] is None or not len(case["values"]): return {"compared": False, "reason": "이번에 낼 수치가 없습니다."}
    sid = _best_old_sid(case)
    case["old_sid"] = sid
    if not sid:
        case["compare"] = None; case.note("대조: 같은 지표·기준일의 과거 제출값 없음(첫 제출)")
        return {"compared": False, "reason": "같은 지표·기준일로 과거에 낸 값이 없어 대조할 짝이 없습니다(첫 제출)."}
    old = pd.DataFrame(db.get_values(sid))
    m = compare.compare(case["values"], old, tol); case["compare"] = m
    diff = m[m["판정"] == "차이"]
    subs = {s["id"]: s for s in db.list_submissions()}
    meta = subs.get(sid, {})
    case.note(f"대조: 제출본 #{sid}와 짝 {int(m['판정'].isin(['차이', '일치', '값 누락']).sum())}건, 차이 {len(diff)}건")
    return {"compared": True, "old_submission": {"id": sid, "submitted_date": meta.get("submitted_date"), "requester": meta.get("requester"), "title": meta.get("request_title")},
            "n_pairs": int(m["판정"].isin(["차이", "일치", "값 누락"]).sum()), "n_diff": len(diff), "n_same": int((m["판정"] == "일치").sum()),
            "diff_rows": [{"indicator": r["indicator"], "center": r["center"], "base_date": r["base_date"], "old": r["old_value"], "new": r["new_value"], "clue": r.get("단서")} for _, r in diff.head(30).iterrows()]}

def step_reasons(case: Case) -> dict:
    """차이 행마다 사유 문구 후보(단서·과거 사유 근거). 규칙 후보 1순위를 '제안'으로 채워 두고 사람이 바꾼다."""
    if case["compare"] is None: return {"candidates": {}, "note": "대조 결과가 없어 사유가 필요 없습니다."}
    diff_rows = case["compare"][case["compare"]["판정"] == "차이"].to_dict("records")
    if not diff_rows: return {"candidates": {}, "note": "차이가 없습니다."}
    cands, how = assist.reason_candidates(diff_rows); case["reason_cands"] = (cands, how)
    out = {}
    for r in diff_rows:
        k = (r["indicator"], r["center"], r["base_date"]); cs = [c for c in (cands.get(k) or []) if c.get("문구")]
        prev = db.reasons_for(*k)
        if k not in case["reasons"] or not case["reasons"][k]:
            case["reasons"][k] = (prev[0]["reason"] if prev else (cs[0]["문구"] if cs else ""))
        out[" | ".join(k)] = {"candidates": [c["문구"] for c in cs[:3]], "grounds": [c.get("근거") for c in cs[:3]], "chosen": case["reasons"][k], "from_record": bool(prev)}
    case.note(f"사유 후보 {len(out)}건 ({how})"); return {"how": how, "candidates": out}

def set_reason(case: Case, indicator: str, center: str, base_date: str, reason: str) -> dict:
    k = (indicator, center, base_date); case["reasons"][k] = reason.strip(); case["draft_stale"] = True      # 초안은 두고 '다시 쓰기 필요' 표시만
    case.note(f"사유 입력: {center} {indicator} {base_date}"); return {"ok": True, "key": " | ".join(k), "reason": reason.strip(), "note": "초안을 다시 쓰면 반영됩니다(write_draft)."}

def step_draft(case: Case) -> dict:
    """확정 전 수치와 사유만으로 회신 초안. 없는 항목은 [확인 필요]."""
    if not case["request_id"]: step_register(case)
    if case["values"] is None: step_pull(case)
    req = db.get_request(case["request_id"]); items = db.get_items(case["request_id"])
    values = case["values"] if case["values"] is not None else pd.DataFrame(columns=VAL_COLS)
    prov = {k: values.iloc[0].get(k) for k in ("definition", "calc_period", "extract_date", "source_version")} if len(values) else {}
    ck = compare.checklist(case["compare"], case["reasons"]).to_dict("records") if case["compare"] is not None else None
    d, how = draft.make_draft(req, items, values, dict(case["reasons"]), prov, ck)
    case["draft"], case["draft_how"], case["checklist"], case["prov"], case["draft_stale"] = d, how, ck, prov, False
    cov = draft.coverage_check(items, d, values); case["coverage"] = cov
    n_ok = int((cov["판정"] == "충족").sum()) if "판정" in cov else None
    case.note(f"초안 작성 ({how}) · 충족 {n_ok}/{len(cov)}")
    return {"how": how, "title": d.get("제목"), "body": d.get("본문"), "reasons_text": d.get("차이사유"), "provenance": d.get("산출근거"),
            "coverage": cov.to_dict("records"), "error": d.get("_error")}

def edit_draft(case: Case, field: str, text: str) -> dict:
    if not case["draft"]: raise ValueError("초안이 아직 없습니다.")
    if field not in ("제목", "본문", "차이사유", "산출근거"): raise ValueError("고칠 수 있는 칸: 제목·본문·차이사유·산출근거")
    case["draft"][field] = text; case["hwpx"] = None; case.note(f"초안 {field} 수정"); return {"ok": True, "field": field}

def step_foresee(case: Case) -> dict:
    if not case["draft"]: step_draft(case)
    req = db.get_request(case["request_id"]); items = db.get_items(case["request_id"])
    qs, how = assist.foresee(req, items, case["values"] if case["values"] is not None else pd.DataFrame(columns=VAL_COLS), dict(case["reasons"]), case.get("checklist"), case["draft"])
    case["foresee"] = (qs, how); case.note(f"예상 질문 {len(qs)}건 ({how})")
    return {"how": how, "questions": qs}

def step_hwpx(case: Case, template_bytes: bytes | None = None, dept_head: str = "", phone: str = "") -> dict:
    """회신 HWPX 생성. 기본은 부서 '답변자료' 양식을 처음부터 만들고(hwpx_build), 자리표시자 서식(template_bytes)이 주어지면 그 서식에 채운다."""
    if not case["draft"]: step_draft(case)
    req = db.get_request(case["request_id"]); d = case["draft"]; values = case["values"] if case["values"] is not None else pd.DataFrame(columns=VAL_COLS)
    items = db.get_items(case["request_id"])
    if template_bytes:
        fill = {"수신": req.get("requester") or "", "제목": d.get("제목", ""), "본문": d.get("본문", ""), "차이사유": d.get("차이사유", "") or "해당 없음",
                "산출근거": d.get("산출근거", ""), "요구항목": "\n".join(f"{i + 1}. {it['item_text']}" for i, it in enumerate(items)), "발신": "한국교육방송공사", "담당자": ""}
        rows = [{"no": i + 1, "indicator": r["indicator"], "center": r["center"], "base_date": r["base_date"], "value": r["value"]} for i, (_, r) in enumerate(values.iterrows())]
        out, kind = hwpx_out.render(template_bytes, fill, rows), "서식 치환"
    else:
        out, kind = hwpx_build.build_reply(req, items, values, d, dict(case["reasons"]), case["compare"], dept_head=dept_head or case.get("dept_head", ""), phone=phone or case.get("phone", "")), "답변자료 양식"
        rows = values
    name = f"답변자료_{req.get('requester') or '요구'}_{dt.date.today()}.hwpx".replace("/", "-")
    case["hwpx"], case["hwpx_name"] = out, name; case.note(f"HWPX 생성 {name} ({len(out) // 1024}KB, {kind})")
    return {"file_name": name, "bytes": len(out), "rows": len(rows), "kind": kind}

def approve(case: Case, user: str, reviewer: str, out_dir=None, comment: str = "검수 승인") -> dict:
    """사람의 승인: 값을 확정 제출본으로 저장, 사유 기록, 초안을 승인 상태로 저장. 에이전트는 이 함수를 호출하지 않는다."""
    if not case["request_id"]: step_register(case)
    if case["values"] is None or not len(case["values"]): raise ValueError("확정할 수치가 없습니다.")
    if not case["draft"]: step_draft(case)
    sid = db.add_submission(case["request_id"], str(dt.date.today()), user, "대화 처리(지표 데이터·기록)", "confirmed", "대화형 처리 후 검수 승인", case["values"].to_dict("records"))
    if case["compare"] is not None:
        m = case["compare"]
        for k, reason in case["reasons"].items():
            if not reason: continue
            row = m[(m["indicator"] == k[0]) & (m["center"] == k[1]) & (m["base_date"] == k[2])]
            if len(row): db.add_reason(sid, *k, row.iloc[0]["old_value"], row.iloc[0]["new_value"], reason, user)
    if case["hwpx"] and out_dir is not None: (out_dir / case["hwpx_name"]).write_bytes(case["hwpx"])
    did = db.add_draft(sid, case["request_id"], case["draft"], case["hwpx_name"] or None, status="approved")
    db.add_review(did, reviewer, "approved", comment)
    case["approved"] = (sid, did); case.note(f"승인: 제출본 #{sid}, 초안 #{did}")
    return {"submission_id": sid, "draft_id": did}

# ---------------- 전체 실행(규칙 경로·'다 해 줘') ----------------
def autopilot(case: Case, template_bytes: bytes | None = None) -> list[str]:
    """표준 순서로 끝까지. 각 단계의 한 줄 설명을 돌려준다(대화에 그대로 보여 줌)."""
    lines = []
    r = step_read(case); lines.append(f"요구서를 읽었습니다. 요청 주체 {r['requester'] or '미기재'} · 접수 {r['received_date'] or '미기재'} · 기한 {r['due_date'] or '미기재'} · 요구 항목 {len(r['items'])}건.")
    step_register(case); lines.append(f"요구서 #{case['request_id']}로 기록했습니다.")
    p = step_plan(case)
    for it in p["items"]: lines.append(f"항목 {it['no']} '{it['item_text'][:40]}': {it['why']}")
    pulled = step_pull(case)
    lines.append(f"가진 자료에서 {pulled['n_rows']}건을 가져왔습니다" + (f" (기준일 {pulled['base_dates'][0]} ~ {pulled['base_dates'][-1]})" if len(pulled["base_dates"]) > 1 else (f" (기준일 {pulled['base_dates'][0]})" if pulled["base_dates"] else "")) + "." +
                 (f" 자료가 없어 새로 산출해야 하는 항목: {', '.join(pulled['items_without_data'])}." if pulled["items_without_data"] else ""))
    c = step_compare(case)
    if c.get("compared"):
        o = c["old_submission"]; lines.append(f"과거 제출본 #{o['id']}({o['submitted_date']} {o['requester']})와 맞춰 봤습니다. 짝 {c['n_pairs']}건 중 차이 {c['n_diff']}건, 일치 {c['n_same']}건.")
        if c["n_diff"]:
            rs = step_reasons(case)
            lines.append("차이 난 행의 사유 후보를 단서와 과거 사유에서 뽑아 채워 두었습니다(" + rs["how"] + "). 검수에서 바꿀 수 있습니다.")
    else: lines.append(c.get("reason", ""))
    d = step_draft(case)
    cov = d["coverage"]; n_ok = sum(1 for x in cov if x.get("판정") == "충족")
    lines.append(f"회신 초안을 썼습니다({d['how']}). 요구 항목 충족 {n_ok}/{len(cov)}" + (", 나머지는 [확인 필요]로 남겼습니다" if n_ok < len(cov) else "") + ".")
    h = step_hwpx(case, template_bytes); lines.append(f"회신 HWPX를 만들었습니다({h['kind']}): {h['file_name']} (값 {h['rows']}건).")
    lines.append("아래 검수 화면에서 수치·대조·사유·초안을 확인하고 승인하면 확정 기록으로 남고 HWPX를 내려받을 수 있습니다. 메일 발송은 담당자가 합니다.")
    return lines

# ---------------- Claude 도구 ----------------
TOOLS = [
    {"name": "read_request", "description": "붙여진 의뢰서(요구서)를 읽어 요청 주체·접수일·기한·제목과 요구 항목(지표·기준일·기간)을 뽑는다. 작업의 첫 단계.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "register_request", "description": "읽은 요구서를 기록(DB)에 등록하고 요구번호를 받는다.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "plan_data", "description": "항목마다 가진 자료(지표 데이터→과거 제출값)의 범위를 보고 낼 기준일을 제안한다. 기준일이 없는 항목은 '월별'이면 있는 달 전부, 아니면 최신을 제안. 이유 문장을 돌려준다.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "set_dates", "description": "항목의 낼 기준일을 바꾼다(사용자가 '9월까지 전부' '최신만' 등으로 지시했을 때). available_dates 안의 날짜만 가능.",
     "input_schema": {"type": "object", "properties": {"item_no": {"type": "integer"}, "dates": {"type": "array", "items": {"type": "string"}}}, "required": ["item_no", "dates"]}},
    {"name": "pull_values", "description": "계획대로 값을 모아 '이번에 낼 수치'를 만든다. 값은 가진 자료에서 그대로 가져오며 계산하지 않는다.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "compare_with_past", "description": "이번 수치를 과거 제출값과 같은 지표·센터·기준일끼리 대조한다(코드). 차이 행과 단서(추출시점·버전 변경)를 돌려준다.",
     "input_schema": {"type": "object", "properties": {"tolerance": {"type": "number", "description": "허용 오차(기본 0)"}}}},
    {"name": "suggest_reasons", "description": "차이 행마다 사유 문구 후보(대조 단서·과거에 쓴 사유 근거)를 뽑고 1순위를 제안으로 채운다. 원인을 새로 추측하지 않는다.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "set_reason", "description": "차이 행 하나의 사유 문구를 사용자가 말한 대로 적는다.",
     "input_schema": {"type": "object", "properties": {"indicator": {"type": "string"}, "center": {"type": "string"}, "base_date": {"type": "string"}, "reason": {"type": "string"}}, "required": ["indicator", "center", "base_date", "reason"]}},
    {"name": "write_draft", "description": "이번 수치와 사유만으로 공문체 회신 초안을 쓰고 요구 항목 충족 검사를 한다. 수치가 없는 항목은 [확인 필요].", "input_schema": {"type": "object", "properties": {}}},
    {"name": "edit_draft", "description": "초안의 한 칸(제목·본문·차이사유·산출근거)을 사용자가 말한 대로 바꾼다.",
     "input_schema": {"type": "object", "properties": {"field": {"type": "string"}, "text": {"type": "string"}}, "required": ["field", "text"]}},
    {"name": "foresee_questions", "description": "이 회신을 받은 쪽이 다음에 물을 만한 질문과 준비할 자료를 예측한다.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "make_hwpx", "description": "회신 HWPX 파일을 만든다. 기본은 부서 '답변자료' 양식(제목·날짜·번호 항목·【확인】·본문·수치표·※ 근거), 설정에 자리표시자 서식이 있으면 그 서식.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "run_all", "description": "표준 순서로 전부 실행: 읽기→등록→계획→값 가져오기→대조→사유 후보→초안→HWPX. 사용자가 '작성해 줘' '다 해 줘'라고 하면 이것 하나로 시작한다.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "case_status", "description": "지금 작업 상태(어디까지 됐는지, 수치 건수, 차이 수, 초안 유무)를 본다.", "input_schema": {"type": "object", "properties": {}}},
] + history_qa.TOOLS

SYSTEM = """당신은 EBS 지역교육협력부의 대외 요구자료 담당자를 돕는 업무 에이전트입니다. 담당자가 의뢰서(요구서)를 붙이고 지시하면, 도구를 골라 써서 회신에 필요한 것을 끝까지 준비합니다. 사람은 검수·승인·발송만 합니다.

하는 일과 순서
1. '작성해 줘' '처리해 줘' 같은 지시에는 run_all을 먼저 호출하고, 결과를 단계별로 짧게 설명합니다(무엇을 읽었고, 어떤 자료를 어느 기준일로 넣었고, 과거 제출값과 어디가 다른지, 초안과 HWPX를 만들었는지).
2. 자료 계획에서 판단이 갈리는 항목(기준일이 없거나 요구 기준일 자료가 없음)은 제안 이유를 그대로 전하고, 다른 기준일을 원하면 set_dates로 바꾼 뒤 pull_values→compare_with_past→write_draft→make_hwpx를 다시 합니다.
3. 자료가 없는 항목은 '지표 데이터에 없음 → 새로 산출 필요'라고 분명히 말하고 지어내지 않습니다.
4. 사용자가 사유나 문안을 말하면 set_reason / edit_draft로 반영하고 make_hwpx를 다시 합니다.
5. 과거 이력 질문은 이력 조회 도구(search_requests, indicator_history 등)로 조회한 결과만 근거로 답합니다.

지켜야 할 것
- 수치를 계산·추정하지 않습니다(평균·합계·증감률도). 값은 도구가 가진 자료에서 가져온 것만 씁니다.
- 차이 사유의 원인을 새로 추측하지 않습니다. 후보는 단서와 과거 사유에서만.
- 확정·승인·발송은 사람이 합니다. "검수 화면에서 승인해 주세요"로 끝맺습니다.
- 한국어 설명체, 간결하게. 도구 이름을 답에 쓰지 않습니다(우리말로). 표가 적절하면 마크다운 표. 사용자에게 되묻기 전에 할 수 있는 도구는 직접 호출합니다."""

def handlers(case: Case, template_bytes: bytes | None):
    """Case에 묶인 도구 함수들. 결과는 개인정보 패턴 마스킹 후 모델로 간다."""
    def status():
        return {"request_id": case["request_id"], "n_items": len(case["items"]), "planned": bool(case["plans"]), "n_values": int(len(case["values"])) if case["values"] is not None else 0,
                "compared": case["compare"] is not None, "n_diff": int((case["compare"]["판정"] == "차이").sum()) if case["compare"] is not None else 0,
                "has_draft": bool(case["draft"]), "has_hwpx": bool(case["hwpx"]), "log": case["log"][-8:]}
    def run_all():
        lines = autopilot(case, template_bytes); return {"summary": lines, **status()}
    def hwpx(): return step_hwpx(case, template_bytes)
    own = {"read_request": lambda: step_read(case), "register_request": lambda: step_register(case), "plan_data": lambda: step_plan(case),
           "set_dates": lambda item_no, dates: set_dates(case, item_no, dates), "pull_values": lambda: step_pull(case),
           "compare_with_past": lambda tolerance=0.0: step_compare(case, float(tolerance or 0)), "suggest_reasons": lambda: step_reasons(case),
           "set_reason": lambda indicator, center, base_date, reason: set_reason(case, indicator, center, base_date, reason),
           "write_draft": lambda: step_draft(case), "edit_draft": lambda field, text: edit_draft(case, field, text),
           "foresee_questions": lambda: step_foresee(case), "make_hwpx": hwpx, "run_all": run_all, "case_status": status}
    def wrap(fn):
        def h(*a, **kw): return pii.redact_obj(history_qa._strip(fn(*a, **kw)))
        return h
    out = {k: wrap(v) for k, v in own.items()}
    out.update(history_qa.HANDLERS)
    return out

def chat_turn(case: Case, messages: list[dict], user_text: str, template_bytes: bytes | None, on_tool=None) -> dict:
    """Claude 경로 한 턴. messages는 이전 대화(도구 호출 포함). 반환: run_tools_conv 결과(text·trace·messages)."""
    ctx = f"[작업 상태] 요구서 {'있음(' + (case['request_name'] or '본문') + ')' if case['request_text'] else '없음'} · 등록 {case['request_id'] or '-'} · 값 {int(len(case['values'])) if case['values'] is not None else 0}건 · 초안 {'있음' if case['draft'] else '없음'}"
    msgs = list(messages) + [{"role": "user", "content": f"{ctx}\n\n{user_text}"}]
    return llm.run_tools_conv(msgs, SYSTEM, TOOLS, handlers(case, template_bytes), max_turns=12, max_tokens=3000, purpose="대화 처리", on_tool=on_tool)

def is_do_it(text: str) -> bool:
    """규칙 경로에서 '전부 처리' 지시로 볼 문장."""
    return bool(re.search(r"작성|처리|만들|준비|회신|답변|초안|해\s*줘|해줘|시작|진행", text or ""))

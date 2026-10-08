"""대화형 에이전트: 요구서 한 장을 붙이고 "요구하는 것들 작성해 줘"라고 하면 끝까지 준비하고, 사람은 검수·승인만 한다.

구조
- Case: 한 건의 작업 상태(요구서 원문·항목·자료 계획·가져온 값·대조·사유·초안·HWPX). 세션에 둔다.
- 단계 함수(step_*): 기존 모듈(extract·plan·compare·assist·draft·hwpx_out)을 감싼 결정적 코드. 값은 계산하지 않고 가진 자료에서만 가져온다.
- autopilot(): 키가 없거나 '그냥 다 해 줘'일 때 표준 순서로 전부 실행.
- TOOLS/handlers(): Claude가 대화 중 골라 쓰는 도구. 단계 함수 + 조정(기준일 바꾸기·사유 적기·초안 고치기) + 기록 조회(history_qa).
원칙: 확정·승인은 사람만 한다(approve()). 에이전트는 준비하고 설명한다."""
from __future__ import annotations
import datetime as dt, re
import pandas as pd
import db, extract, normalize, plan, compare, assist, draft, hwpx_out, hwpx_build, llm, pii, history_qa, refdocs

VAL_COLS = ["indicator", "center", "base_date", "value", "definition", "calc_period", "extract_date", "source_version"]
SRC_COLS = ["source_file", "source_sheet", "source_row"]
HISTORY_MAX_BLOCKS = 30      # 모델 대화 기록이 이보다 길면 다음 턴은 요약 한 줄로 새로 시작한다(도구 턴마다 2블록씩 늘어 30이면 도구 호출 12~15회분). 중간을 자르면 모델의 이어지는 사고 블록이 깨지므로 통째로 접는다

class Case(dict):
    """한 건의 작업 상태. dict라 세션에 그대로 들어간다."""
    def __init__(self, **kw):
        super().__init__(request_text="", request_name="", extracted=None, request_id=None, items=[], plans=[], choices={},
                         values=None, old_sid=None, compare=None, reasons={}, reason_cands=None, draft=None, draft_how="", coverage=None,
                         foresee=None, hwpx=None, hwpx_name="", log=[], uploads=[], described=False, no_register=False, rev=0)
        self.update(kw)
    def note(self, text: str):
        self["log"].append(f"{dt.datetime.now().strftime('%H:%M:%S')} {text}"); return text
    def bump(self):
        """초안·사유·값이 코드나 모델에 의해 새로 만들어졌음을 표시(판 번호). 화면 입력칸은 이 번호를 키에 넣어 이전 입력이 새 내용을 덮지 않게 한다."""
        self["rev"] = int(self.get("rev") or 0) + 1
    def reset(self, keep=("dept_head", "phone", "org", "company")):            # tone_kind는 건마다 다르므로 남기지 않는다
        """새 작업 시작(같은 객체를 비워 세션 참조를 유지). 부서장·내선 같은 설정 값은 남긴다."""
        kept = {k: self.get(k) for k in keep if k in self}
        rev = int(self.get("rev") or 0) + 1
        self.clear(); Case.__init__(self); self.update(kept); self["rev"] = rev

# ---------------- 단계 ----------------
def step_read(case: Case) -> dict:
    """요구서 원문 → 항목·지표·기준일·기간. 이미 읽었으면 그대로."""
    if not case["request_text"]: raise ValueError("요구서가 없습니다. 요구서 파일을 붙이거나, 요청 내용을 말로 알려 주세요(describe_request).")
    key = hash(case["request_text"])
    if case.get("described") and case["extracted"]: res, how = case["extracted"], "대화 입력(규칙)"      # 말로 받은 요구는 describe_request가 이미 구조화해 두었다(다시 읽으면 '미기재'가 값으로 들어감)
    elif case["extracted"] and case.get("read_key") == key: res, how = case["extracted"], case.get("read_how") or "이미 읽음"   # 같은 원문을 다시 추출하지 않는다(run_all·read_request 반복 호출)
    else:
        res, how = extract.extract(case["request_text"])
        items = normalize.normalize_items(res.get("items") or [])
        res["items"] = items if how.startswith("Claude") else normalize.normalize_llm(items)                # 모델이 이미 지표를 분류했으면 다시 묻지 않는다(규칙 경로일 때만 보조 분류)
    if res.get("requester"): res["requester"] = db.canonical_requester(res["requester"])                 # 요청 주체 사전 표기로
    case["extracted"] = res; case["items"] = res["items"]; case["read_how"] = how; case["read_key"] = key
    case.note(f"요구서 읽음: 항목 {len(res['items'])}건 ({how})")
    return {"requester": res.get("requester"), "received_date": res.get("received_date"), "due_date": res.get("due_date"), "title": res.get("title"),
            "items": [{"no": i + 1, "item_text": it["item_text"], "indicator": it.get("indicator"), "base_date": it.get("base_date"), "period": it.get("period")} for i, it in enumerate(res["items"])], "how": how}

def _clean_item(text: str) -> str:
    """말로 받은 항목에서 지시어를 뗀다: '센터별 등원율 요청이 들어왔어. 작성해줘' → '센터별 등원율'."""
    t = re.split(r"\s*(?:요청이|요구가|자료가|자료\s*요청이)?\s*(?:들어왔|왔어요|왔어|왔는데|왔습니다|작성해|처리해|만들어|준비해|회신해|해\s*줘|해줘|해\s*주세요|부탁)", text or "", 1)[0]
    return re.sub(r"[\s,.。]+$", "", t).strip() or (text or "").strip()

def describe_request(case: Case, requester: str | None = None, received_date: str | None = None, due_date: str | None = None, title: str | None = None,
                     items: list[str] | None = None, name: str = "대화로 받은 요구") -> dict:
    """요구서 파일 없이 말로 받은 요구를 작업의 요구서로 만든다. 모르는 칸은 비워 둔다(지어내지 않음).
    이미 적어 둔 요구가 있으면 말한 칸만 바꾼다. 등록된 뒤라면 기록(DB)의 머리 정보·항목도 같이 고친다. 계획 이후 단계는 다시 한다."""
    if case.get("approved"): case.reset()                                   # 확정이 끝난 작업에 새 요구가 오면 앞 건 기록을 고치지 않고 새 작업으로
    prev = case["extracted"] if (case.get("described") and case["extracted"]) else {}
    def pick(new, key): return new if new not in (None, "") else prev.get(key)
    nd = lambda v: (extract._norm_date(v) or (v if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(v)) else None)) if v else None
    requester, received_date, due_date, title = pick(requester, "requester"), nd(pick(received_date, "received_date")), nd(pick(due_date, "due_date")), pick(title, "title")
    item_texts = [_clean_item(t) for t in (items or []) if t and _clean_item(t)] or [it["item_text"] for it in (case["items"] or [])]
    if not item_texts: raise ValueError("요구 항목이 없습니다. 무엇을 달라는 요청인지 한 줄이라도 알려 주세요(예: '2025년 12월 ~ 2026년 8월 센터별 월별 등원율').")
    head = [f"요구 주체: {requester or '미기재'}", f"접수일: {received_date or '미기재'}", f"제출 기한: {due_date or '미기재'}", f"제목: {title or item_texts[0]}"]
    case["request_text"] = "\n".join(head + [f"{i}. {t}" for i, t in enumerate(item_texts, 1)]); case["request_name"] = name; case["described"] = True
    res = extract.rule_based(case["request_text"]); res.update(requester=requester, received_date=received_date, due_date=due_date, title=title or item_texts[0])
    res["items"] = normalize.normalize_items(res["items"]); case["extracted"] = res; case["items"] = res["items"]; case["read_how"] = "대화 입력(규칙)"
    if case["request_id"]:
        db.update_request(case["request_id"], requester, received_date, due_date, res["title"]); db.replace_items(case["request_id"], res["items"]); case["items"] = db.get_items(case["request_id"])
    case.update(plans=[], choices={}, values=None, compare=None, draft=None, hwpx=None, coverage=None, foresee=None); case.bump()
    case.note(f"요구를 말로 받음: 항목 {len(res['items'])}건" + (" (기록도 고침)" if case["request_id"] else ""))
    return {"requester": requester, "received_date": received_date, "due_date": due_date, "title": res["title"], "registered": case["request_id"],
            "items": [{"no": i + 1, "item_text": it["item_text"], "indicator": it.get("indicator"), "base_date": it.get("base_date"), "period": it.get("period"), "unit": it.get("unit")} for i, it in enumerate(res["items"])],
            "missing_header": [k for k, v in (("요청 주체", requester), ("접수일", received_date), ("기한", due_date)) if not v],
            "unknown_indicator": [it["item_text"] for it in res["items"] if not it.get("indicator")]}

def set_header(case: Case, requester=None, received_date=None, due_date=None, title=None) -> dict:
    """요청 주체·접수일·기한·제목만 고친다(항목·원문은 그대로). 등록돼 있으면 기록도 고친다. 초안·한글 파일은 다시 만들어야 반영된다."""
    if not case["extracted"]: step_read(case)
    nd = lambda v: (extract._norm_date(v) or (v if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(v)) else None)) if v else None
    changed = {}
    for k, v in (("requester", requester), ("received_date", nd(received_date)), ("due_date", nd(due_date)), ("title", title)):
        if v not in (None, "") and case["extracted"].get(k) != v: case["extracted"][k] = v; changed[k] = v
    if changed and case["request_id"]:
        e = case["extracted"]; db.update_request(case["request_id"], e.get("requester"), e.get("received_date"), e.get("due_date"), e.get("title"))
    if changed:
        if case["draft"]: case["draft_stale"] = True
        case["hwpx_stale"] = True; case.bump(); case.note("요청 정보 수정: " + ", ".join(changed))
    return {"changed": changed, "registered": case["request_id"]}

def _req_items(case: Case) -> tuple[dict, list[dict]]:
    """초안·HWPX가 쓰는 요구 머리 정보와 항목: 등록돼 있으면 기록에서, 아니면(등록 보류) 작업 상태에서."""
    if case["request_id"]: return db.get_request(case["request_id"]), db.get_items(case["request_id"])
    if not case["extracted"]: step_read(case)
    return dict(case["extracted"]), list(case["items"])

def coverage_summary() -> list[dict]:
    """지표 데이터(미리 넣어 둔 집계값)의 보유 범위를 지표별 한 줄로."""
    by = {}
    for r in db.data_coverage():
        g = by.setdefault(r["indicator"], {"indicator": r["indicator"], "dates": [], "n_centers": 0, "last_loaded": None})
        g["dates"].append(r["base_date"]); g["n_centers"] = max(g["n_centers"], int(r["n_centers"] or 0))
        g["last_loaded"] = max(filter(None, [g["last_loaded"], str(r["loaded_at"] or "")[:10]]), default=None)
    out = []
    for g in by.values():
        ds = sorted(g["dates"]); out.append({**g, "dates": ds, "first": ds[0], "last": ds[-1], "n_dates": len(ds)})
    return out

def coverage_line() -> str:
    """상태줄용 한 줄: 같은 기준일 범위의 지표는 묶어서(대시보드 31개 지표가 한 줄에 다 나열되지 않게)."""
    cov = coverage_summary()
    if not cov: return "지표 데이터 없음"
    groups: dict = {}
    for g in cov: groups.setdefault((g["first"], g["last"], g["n_dates"]), []).append(g)
    parts = []
    for (first, last, n), gs in sorted(groups.items(), key=lambda x: -len(x[1])):
        names = [g["indicator"] for g in gs]; shown = "·".join(names[:4]) + (f" 외 {len(names) - 4}개" if len(names) > 4 else "")
        rng = f"{first}~{last}({n}개 기준일)" if n > 1 else f"{first}"
        parts.append(f"{shown} {rng}·최대 {max(g['n_centers'] for g in gs)}센터")
    return "지표 데이터 보유: " + "; ".join(parts)

def step_register(case: Case) -> dict:
    """요구서를 기록에 등록(이미 등록돼 있으면 그 번호)."""
    if case["request_id"]: return {"request_id": case["request_id"], "already": True}
    if not case["extracted"]: step_read(case)
    r = case["extracted"]
    rid = db.add_request(r.get("requester"), r.get("received_date"), r.get("due_date"), r.get("title"), case["request_text"], case["request_name"], case["items"])
    db.advance_request_status(rid, "처리 중")                       # 대화에서 등록되는 건은 이미 처리가 시작된 것
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
    if len(df): df = df.drop_duplicates(subset=["indicator", "center", "base_date"], keep="last")        # 같은 지표를 묻는 항목이 둘이면 같은 행이 두 번 모인다
    case["values"] = df[VAL_COLS + SRC_COLS].reset_index(drop=True) if len(df) else None; case.bump()
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
        prev_all = db.reasons_for(*k)
        prev = [p for p in prev_all if _same(p.get("old_value"), r.get("old_value")) and _same(p.get("new_value"), r.get("new_value"))]   # 같은 값 쌍(지난번→이번)에 썼던 기록만 자동으로 채운다
        if k not in case["reasons"] or not case["reasons"][k]:
            case["reasons"][k] = (prev[0]["reason"] if prev else (cs[0]["문구"] if cs else ""))
        out[" | ".join(k)] = {"candidates": [c["문구"] for c in cs[:3]], "grounds": [c.get("근거") for c in cs[:3]], "chosen": case["reasons"][k], "from_record": bool(prev),
                              "earlier_reasons_other_values": [p["reason"] for p in prev_all if p not in prev][:2]}
    case.bump(); case.note(f"사유 후보 {len(out)}건 ({how})"); return {"how": how, "candidates": out}

def _same(a, b) -> bool:
    try: return a is not None and b is not None and abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError): return False

def set_reason(case: Case, indicator: str, center: str, base_date: str, reason: str) -> dict:
    k = (indicator, center, base_date); case["reasons"][k] = reason.strip(); case["draft_stale"] = True; case.bump()      # 초안은 두고 '다시 쓰기 필요' 표시만
    case.note(f"사유 입력: {center} {indicator} {base_date}"); return {"ok": True, "key": " | ".join(k), "reason": reason.strip(), "note": "초안 다시 쓰기(write_draft) → 한글 파일 다시 만들기(make_hwpx) 순으로 하면 반영됩니다."}

def tone_for(case: Case, req: dict | None = None) -> dict:
    """이 작업의 회신 톤: 담당자가 고른 유형(tone_kind) → 요청 주체 사전의 유형 → 이름으로 추정. 설정 화면의 수정값을 덮어 쓴다."""
    req = req if req is not None else (_req_items(case)[0] if (case["request_id"] or case["extracted"]) else {})
    name = (req or {}).get("requester")
    kind = case.get("tone_kind") or draft.kind_of(name, db.requester_kind(name) if name else None)
    tone = draft.preset_for(kind, db.get_tone_presets()); case["tone"] = tone; return tone

def step_draft(case: Case) -> dict:
    """확정 전 수치와 사유만으로 회신 초안. 없는 항목은 [확인 필요]. 문체는 요청 주체 유형의 톤 설정을 따른다."""
    if not case["request_id"] and not case.get("no_register"): step_register(case)
    if case["values"] is None: step_pull(case)
    req, items = _req_items(case); tone = tone_for(case, req)
    values = case["values"] if case["values"] is not None else pd.DataFrame(columns=VAL_COLS)
    prov = draft.provenance_by_indicator(values)                                                       # 지표별 근거(첫 행 하나만 쓰던 것을 대체)
    ck = compare.checklist(case["compare"], case["reasons"]).to_dict("records") if case["compare"] is not None else None
    d, how = draft.make_draft(req, items, values, dict(case["reasons"]), prov, ck, tone)
    case["draft"], case["draft_how"], case["checklist"], case["prov"], case["draft_stale"] = d, how, ck, prov, False; case.bump()
    cov = draft.coverage_check(items, d, values); case["coverage"] = cov
    n_ok = int((cov["판정"] == "충족").sum()) if "판정" in cov else None
    case.note(f"초안 작성 ({how}) · 충족 {n_ok}/{len(cov)}")
    return {"how": how, "title": d.get("제목"), "body": d.get("본문"), "reasons_text": d.get("차이사유"), "provenance": d.get("산출근거"),
            "coverage": cov.to_dict("records"), "error": d.get("_error")}

def edit_draft(case: Case, field: str, text: str) -> dict:
    if not case["draft"]: raise ValueError("초안이 아직 없습니다.")
    if field not in ("제목", "본문", "차이사유", "산출근거"): raise ValueError("고칠 수 있는 칸: 제목·본문·차이사유·산출근거")
    case["draft"][field] = text; case["hwpx"] = None; case.bump(); case.note(f"초안 {field} 수정"); return {"ok": True, "field": field}

def step_foresee(case: Case) -> dict:
    if not case["draft"]: step_draft(case)
    req, items = _req_items(case)
    qs, how = assist.foresee(req, items, case["values"] if case["values"] is not None else pd.DataFrame(columns=VAL_COLS), dict(case["reasons"]), case.get("checklist"), case["draft"])
    case["foresee"] = (qs, how); case.note(f"예상 질문 {len(qs)}건 ({how})")
    return {"how": how, "questions": qs}

def step_hwpx(case: Case, template_bytes: bytes | None = None, dept_head: str = "", phone: str = "") -> dict:
    """회신 HWPX 생성. 기본은 부서 '답변자료' 양식을 처음부터 만들고(hwpx_build), 자리표시자 서식(template_bytes)이 주어지면 그 서식에 채운다."""
    if not case["draft"]: step_draft(case)
    req, items = _req_items(case); d = case["draft"]; values = case["values"] if case["values"] is not None else pd.DataFrame(columns=VAL_COLS)
    if template_bytes:
        fill = {"수신": req.get("requester") or "", "제목": d.get("제목", ""), "본문": d.get("본문", ""), "차이사유": d.get("차이사유", "") or "해당 없음",
                "산출근거": d.get("산출근거", ""), "요구항목": "\n".join(f"{i + 1}. {it['item_text']}" for i, it in enumerate(items)), "발신": case.get("company") or "한국교육방송공사", "담당자": ""}
        rows = [{"no": i + 1, "indicator": r["indicator"], "center": r["center"], "base_date": r["base_date"], "value": r["value"]} for i, (_, r) in enumerate(values.iterrows())]
        out, kind = hwpx_out.render(template_bytes, fill, rows), "서식 치환"
    else:
        tone = case.get("tone") or tone_for(case, req)
        out, kind = hwpx_build.build_reply(req, items, values, d, dict(case["reasons"]), case["compare"], dept_head=dept_head or case.get("dept_head", ""), phone=phone or case.get("phone", ""),
                                           org=case.get("org") or "지역교육협력부", doc_label=tone.get("doc_label") or "답변자료", show_confirm=bool(tone.get("show_confirm", True))), f"{tone.get('doc_label') or '답변자료'} 양식"
        rows = values
    name = safe_filename(f"답변자료_{req.get('requester') or '요구'}_{dt.date.today()}" + (f"_요구{case['request_id']}" if case["request_id"] else "") + ".hwpx")
    case["hwpx"], case["hwpx_name"], case["hwpx_stale"] = out, name, False; case.note(f"HWPX 생성 {name} ({len(out) // 1024}KB, {kind})")
    return {"file_name": name, "bytes": len(out), "rows": len(rows), "kind": kind}

def safe_filename(name: str) -> str:
    """Windows·한글에서 못 쓰는 문자(\\ / : * ? " < > |)와 제어 문자를 '_'로."""
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", name).strip(" .") or "파일"

def approve(case: Case, user: str, reviewer: str, out_dir=None, comment: str = "담당자 확정") -> dict:
    """사람의 승인: 값을 확정 제출본으로 저장, 사유 기록, 초안을 승인 상태로 저장. 에이전트는 이 함수를 호출하지 않는다.
    등록을 보류했던(no_register) 작업도 승인하면 요구서부터 기록에 남긴다 — 확정 제출본은 요구서 없이 존재할 수 없다."""
    if not case["request_id"]: case["no_register"] = False; step_register(case)
    if case["values"] is None or not len(case["values"]): raise ValueError("확정할 수치가 없습니다.")
    if not case["draft"]: step_draft(case)
    sid = db.add_submission(case["request_id"], str(dt.date.today()), user, "대화 처리(지표 데이터·기록)", "confirmed", "대화형 처리 후 검수 승인", case["values"].to_dict("records"))
    if case["compare"] is not None:
        m = case["compare"]
        for k, reason in case["reasons"].items():
            if not reason: continue
            row = m[(m["indicator"] == k[0]) & (m["center"] == k[1]) & (m["base_date"] == k[2])]
            if len(row): db.add_reason(sid, *k, row.iloc[0]["old_value"], row.iloc[0]["new_value"], reason, user)
    if case["hwpx"] and out_dir is not None:
        try: (out_dir / case["hwpx_name"]).write_bytes(case["hwpx"])
        except OSError as e: case.note(f"파일 저장 실패(기록은 저장됨): {e}")
    did = db.add_draft(sid, case["request_id"], case["draft"], case["hwpx_name"] or None, status="approved")
    db.add_review(did, user, "approved", comment)                                # 실제로 확정을 누른 사람으로 기록(팀장 이름을 대신 쓰지 않는다)
    case["approved"] = (sid, did); case.note(f"승인: 제출본 #{sid}, 초안 #{did}")
    return {"submission_id": sid, "draft_id": did}

# ---------------- 전체 실행(규칙 경로·'다 해 줘') ----------------
def autopilot(case: Case, template_bytes: bytes | None = None, register: bool = True) -> list[str]:
    """표준 순서로 끝까지. 각 단계의 한 줄 설명을 돌려준다(대화에 그대로 보여 줌). register=False면 기록 등록을 승인 때까지 미룬다."""
    lines = []
    r = step_read(case); lines.append(f"요구서를 읽었습니다. 요청 주체 {r['requester'] or '미기재'} · 접수 {r['received_date'] or '미기재'} · 기한 {r['due_date'] or '미기재'} · 요구 항목 {len(r['items'])}건.")
    if register or case["request_id"]: case["no_register"] = False; step_register(case); lines.append(f"요구서 #{case['request_id']}로 기록했습니다.")
    else: case["no_register"] = True; lines.append("기록에는 등록하지 않았습니다(승인하면 그때 요구서·제출본으로 남습니다).")
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
    {"name": "read_request", "description": "붙여진 요구서를 읽어 요청 기관·접수일·기한·제목과 요구 항목(지표·기준일·기간)을 뽑는다. 보통은 run_all이 이 단계를 포함하므로, 항목만 미리 확인하고 싶을 때만 따로 부른다(이미 읽은 원문은 다시 읽지 않는다).", "input_schema": {"type": "object", "properties": {}}},
    {"name": "register_request", "description": "읽은 요구서를 기록(DB)에 등록하고 요구번호를 받는다.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "plan_data", "description": "항목마다 가진 자료(지표 데이터→과거 제출값)의 범위를 보고 낼 기준일을 제안한다. 요구 기준일이 있으면 그 값(없으면 그 이전 가장 가까운 값), 없으면 기간 표현(월별·연도별·분기별·'1~8월'·'25년 12월부터')에 맞는 기준일 전부, 아무 표현도 없으면 최신. 이유 문장을 돌려준다.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "set_dates", "description": "항목의 낼 기준일을 바꾼다(사용자가 '9월까지 전부' '최신만' 등으로 지시했을 때). available_dates 안의 날짜만 가능.",
     "input_schema": {"type": "object", "properties": {"item_no": {"type": "integer"}, "dates": {"type": "array", "items": {"type": "string"}}}, "required": ["item_no", "dates"]}},
    {"name": "pull_values", "description": "계획대로 값을 모아 '이번에 낼 수치'를 만든다. 값은 가진 자료에서 그대로 가져오며 계산하지 않는다.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "compare_with_past", "description": "이번 수치를 과거 제출값과 같은 지표·센터·기준일끼리 대조한다(코드). 차이 행과 단서(추출시점·버전 변경)를 돌려준다.",
     "input_schema": {"type": "object", "properties": {"tolerance": {"type": "number", "description": "허용 오차(기본 0)"}}}},
    {"name": "suggest_reasons", "description": "차이 행마다 사유 문구 후보(대조 단서·과거에 쓴 사유 근거)를 뽑고 1순위를 제안으로 채운다. 원인을 새로 추측하지 않는다.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "set_reason", "description": "차이 행 하나의 사유 문구를 사용자가 말한 대로 적는다. 적은 뒤에는 write_draft → make_hwpx 순으로 다시 만들어야 회신에 반영된다.",
     "input_schema": {"type": "object", "properties": {"indicator": {"type": "string"}, "center": {"type": "string"}, "base_date": {"type": "string"}, "reason": {"type": "string"}}, "required": ["indicator", "center", "base_date", "reason"]}},
    {"name": "write_draft", "description": "이번 수치와 사유만으로 공문체 회신 초안을 쓰고 요구 항목 충족 검사를 한다. 수치가 없는 항목은 [확인 필요].", "input_schema": {"type": "object", "properties": {}}},
    {"name": "edit_draft", "description": "초안의 한 칸(제목·본문·차이사유·산출근거)을 사용자가 말한 대로 바꾼다.",
     "input_schema": {"type": "object", "properties": {"field": {"type": "string"}, "text": {"type": "string"}}, "required": ["field", "text"]}},
    {"name": "foresee_questions", "description": "이 회신을 받은 쪽이 다음에 물을 만한 질문과 준비할 자료를 예측한다.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "make_hwpx", "description": "회신 HWPX 파일을 만든다. 기본은 부서 '답변자료' 양식(제목·날짜·번호 항목·【확인】·본문·수치표·※ 근거), 설정에 자리표시자 서식이 있으면 그 서식.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "run_all", "description": "표준 순서로 전부 실행: 읽기→등록→계획→값 가져오기→대조→사유 후보→초안→HWPX. 사용자가 '작성해 줘' '다 해 줘'라고 하면 이것 하나로 시작한다. 사용자가 '등록은 하지 말고 파일만' 하면 register=false(승인 때 등록).",
     "input_schema": {"type": "object", "properties": {"register": {"type": "boolean", "description": "기록(DB)에 요구서를 등록할지. 기본 true"}}}},
    {"name": "describe_request", "description": "요구서 파일 없이 사용자가 말로 알려 준 요구를 작업의 요구서로 만든다(또는 고친다). 사용자가 '○○ 요청이 들어왔어' 하면 즉시 이것으로 적고 run_all을 이어서 한다. 모르는 칸은 비워 둔다(묻기 전에 먼저 처리). items는 요구 항목을 한 줄씩, 기간·기준일·단위를 그대로 담아(예: '2025년 12월 ~ 2026년 8월 센터별 월별 등원율'). 날짜는 YYYY-MM-DD(오늘·내일 같은 말은 상태줄의 오늘 날짜로 계산).",
     "input_schema": {"type": "object", "properties": {"requester": {"type": "string"}, "received_date": {"type": "string"}, "due_date": {"type": "string"}, "title": {"type": "string"},
                                                       "items": {"type": "array", "items": {"type": "string"}}}, "required": ["items"]}},
    {"name": "data_coverage", "description": "지표 데이터(담당자가 미리 넣어 둔 집계값 — 과거 제출 이력과 별개)의 보유 범위: 지표별 기준일 범위·센터 수·적재일. indicator를 주면 그 지표의 기준일 목록까지. '그 기간 자료가 있나' 판단은 이것으로.",
     "input_schema": {"type": "object", "properties": {"indicator": {"type": "string", "description": "선택. 지표 이름(일부 가능)"}}}},
    {"name": "case_status", "description": "지금 작업 상태(어디까지 됐는지, 이번에 낼 수치 건수, 차이 수, 초안 유무, 지표 데이터 보유 범위)를 본다.", "input_schema": {"type": "object", "properties": {}}},
] + history_qa.TOOLS

SYSTEM = """당신은 EBS 지역교육협력부의 대외 요구자료 담당자를 돕는 업무 에이전트입니다. 담당자가 요구서를 붙이고 지시하면 도구를 골라 써서 회신에 필요한 것을 끝까지 준비합니다. 사람은 확인·확정·발송만 합니다.

하는 일과 순서
1. '작성해 줘' '처리해 줘' 같은 지시에는 run_all 하나로 시작합니다(읽기부터 한글 파일까지 포함하므로 read_request를 따로 먼저 부르지 않습니다). 끝나면 단계별로 짧게 설명합니다: 무엇을 읽었고, 어떤 자료를 어느 기준일로 넣었고, 과거 제출값과 어디가 다른지, 초안과 한글 파일을 만들었는지.
2. 요구서 파일이 없어도 사용자가 말로 요구를 알려 주면("센터별 등원율 요청이 들어왔어", "25년 12월부터 26년 8월까지") 되묻지 말고 describe_request로 적은 뒤 바로 run_all을 합니다. 요청 기관·기한처럼 모르는 칸은 비워 두고, 결과 끝에 "알려 주시면 반영합니다"라고 한 줄만 덧붙입니다. 나중에 그 칸을 말하면 describe_request로 고치고 필요한 단계를 다시 합니다. 사용자가 "등록은 빼고 파일만" 하면 run_all(register=false)로 합니다(확정할 때 기록됩니다).
3. 자료 계획에서 판단이 갈리는 항목(기준일이 없거나 요구 기준일 자료가 없음)은 제안 이유를 그대로 전하고, 다른 기준일을 원하면 set_dates → pull_values → compare_with_past → write_draft → make_hwpx 순으로 다시 합니다.
4. 자료가 없는 항목은 '지표 데이터에 없음 → 새로 산출 필요'라고 분명히 말하고 지어내지 않습니다. 자료가 있는지는 상태줄의 '지표 데이터 보유'와 data_coverage로 봅니다. indicator_history·past_values_for는 과거에 **제출한** 이력일 뿐이라, 거기에 없어도 지표 데이터에 있으면 낼 수 있습니다. [첨부 처리] 줄에 "지표 데이터에 넣었습니다"가 있으면 그 파일 값은 이미 들어온 것입니다.
5. 사용자가 사유나 문안을 말하면 set_reason / edit_draft로 반영한 뒤 write_draft(사유를 바꾼 경우) → make_hwpx를 다시 합니다.
6. 과거 기록 질문은 기록 조회 도구로 조회한 결과만 근거로 답합니다. 조회되지 않은 것은 '기록에 없음'. 같은 지표·기준일의 제출값이 서로 다른 센터가 보이면 diff_reasons(center 지정)로 기록된 사유를 찾아 그대로 인용하고, 없으면 '사유 기록 없음'. 답에는 요구번호(#id)·제출본번호·제출일·요청 기관을 적습니다.
7. 사업 자체에 대한 질문(이용 대상·비용·운영 시간·인원 구성·절차·근거 법령 등)은 search_docs로 참고 문서(지침·운영 매뉴얼)를 찾아 그 문구만 근거로 답하고 문서 이름과 쪽을 밝힙니다. 없으면 "참고 문서에 없습니다". 센터의 지역·유형·개소일·정원·주말 운영은 center_info / list_centers(센터 명부)로 답합니다.

지켜야 할 것
- 수치를 계산·추정하지 않습니다(평균·합계·증감률도). 값은 도구가 가진 자료에서 가져온 것만 쓰고, 개수는 도구가 돌려준 집계를 그대로 씁니다.
- 차이 사유의 원인을 새로 추측하지 않습니다('~로 보입니다', '~와 연관' 금지). 후보는 단서와 과거 사유에서만.
- 확정·승인·발송은 사람이 합니다. 초안·한글 파일을 만들었을 때만 "아래 화면에서 확인하고 확정해 주세요"로 끝맺고, 이력·사업 질문에는 붙이지 않습니다.
- 도구 결과가 '잘림'으로 표시되면 전체가 아니므로 인자로 범위를 좁혀 다시 조회합니다.
- 한국어 설명체, 간결하게. 도구 이름을 답에 쓰지 않습니다(우리말로). 표가 적절하면 마크다운 표로 하되 20행 이하로 요약하고, 센터별 전체 값 표는 화면에 이미 있으므로 다시 출력하지 않습니다. 사용자에게 되묻기 전에 할 수 있는 도구는 직접 호출합니다."""

def handlers(case: Case, template_bytes: bytes | None):
    """Case에 묶인 도구 함수들. 결과는 개인정보 패턴 마스킹 후 모델로 간다."""
    def status():
        return {"request_id": case["request_id"], "n_items": len(case["items"]), "planned": bool(case["plans"]), "n_values": int(len(case["values"])) if case["values"] is not None else 0,
                "compared": case["compare"] is not None, "n_diff": int((case["compare"]["판정"] == "차이").sum()) if case["compare"] is not None else 0,
                "has_draft": bool(case["draft"]), "has_hwpx": bool(case["hwpx"]), "registered": bool(case["request_id"]), "data_coverage": coverage_line(), "log": case["log"][-8:]}
    def run_all(register=True):
        lines = autopilot(case, template_bytes, register=bool(register) if register is not None else True)
        st_ = status(); return {"summary": lines, **{k: st_[k] for k in ("request_id", "n_items", "n_values", "n_diff", "has_draft", "has_hwpx", "registered")}}
    def hwpx(): return step_hwpx(case, template_bytes)
    own = {"read_request": lambda: step_read(case), "register_request": lambda: step_register(case), "plan_data": lambda: step_plan(case),
           "set_dates": lambda item_no, dates: set_dates(case, item_no, dates), "pull_values": lambda: step_pull(case),
           "compare_with_past": lambda tolerance=0.0: step_compare(case, float(tolerance or 0)), "suggest_reasons": lambda: step_reasons(case),
           "set_reason": lambda indicator, center, base_date, reason: set_reason(case, indicator, center, base_date, reason),
           "write_draft": lambda: step_draft(case), "edit_draft": lambda field, text: edit_draft(case, field, text),
           "foresee_questions": lambda: step_foresee(case), "make_hwpx": hwpx, "run_all": run_all, "case_status": status,
           "describe_request": lambda items, requester=None, received_date=None, due_date=None, title=None: describe_request(case, requester, received_date, due_date, title, items),
           "data_coverage": lambda indicator=None: {"indicators": [({**g, "dates": g["dates"]} if indicator else {k: v for k, v in g.items() if k != "dates"}) for g in coverage_summary() if not indicator or indicator in g["indicator"]],
                                                    "note": "값은 pull_values로 가져온다. 여기 없는 지표·기간은 새로 산출해야 한다."}}
    def wrap(fn):
        def h(*a, **kw): return pii.redact_obj(history_qa._strip(fn(*a, **kw)))
        return h
    out = {k: wrap(v) for k, v in own.items()}
    out.update(history_qa.HANDLERS)
    return out

def status_line(case: Case) -> str:
    """매 턴 모델에 주는 한 줄 상태: 오늘 날짜 · 요구서 유무 · 등록 · 이번에 낼 수치 · 초안 · 지표 데이터 보유 범위 · 참고 문서·명부."""
    req = ("있음(" + (case["request_name"] or "본문") + ")") if case["request_text"] else "없음(사용자가 말로 알려 주면 요구서로 적는다)"
    reg = ("#" + str(case["request_id"])) if case["request_id"] else ("보류" if case.get("no_register") else "-")
    vals = f"{int(len(case['values']))}건" if case["values"] is not None else "0건(아직 안 가져옴)"
    parts = [f"오늘 {dt.date.today()}", f"요구서 {req}", f"등록 {reg}", f"이번에 낼 수치 {vals}", f"초안 {'있음' if case['draft'] else '없음'}"]
    if case.get("approved"): parts.append("확정 완료(새 요구는 새 작업으로)")
    parts += [coverage_line(), f"참고 문서 {len(db.list_ref_docs())}건", f"센터 명부 {db.center_count()}개소"]
    return "[작업 상태] " + " · ".join(parts)

def carry_note(case: Case, hist: list[dict] | None = None, extra: str | None = None) -> str:
    """대화 기록을 통째로 접을 때 다음 턴에 넘기는 한 줄: 접은 이유(extra)·마지막 답 요약·작업 기록 꼬리. 작업 상태 자체는 상태줄이 매 턴 주므로 되풀이하지 않는다."""
    parts = [extra] if extra else []
    last = next((m.get("text") or "" for m in reversed(hist or []) if m.get("role") == "assistant"), "")
    if last: parts.append("마지막 답: " + re.sub(r"\s+", " ", last)[:300])
    if case["log"]: parts.append("작업 기록: " + " / ".join(case["log"][-5:]))
    return " · ".join(parts) or "이전 대화 기록은 접었습니다."

def chat_turn(case: Case, messages: list[dict], user_text: str, template_bytes: bytes | None, on_tool=None, notes: list[str] | None = None, carry: str | None = None) -> dict:
    """Claude 경로 한 턴. messages는 이전 대화(도구 호출 포함). notes는 이번 턴 첨부 파일 처리 결과(모델도 알아야 한다). carry는 접은 이전 대화의 요약 한 줄. 반환: run_tools_conv 결과(text·trace·messages)."""
    ctx = status_line(case) + (f"\n[이전 대화 요약] {carry}" if carry else "") + ("".join(f"\n[첨부 처리] {n}" for n in notes) if notes else "")
    msgs = list(messages) + [{"role": "user", "content": f"{ctx}\n\n{user_text}"}]
    return llm.run_tools_conv(msgs, SYSTEM, TOOLS, handlers(case, template_bytes), max_turns=12, max_tokens=12000, purpose="대화 처리", on_tool=on_tool)

QUESTION_RE = re.compile(r"\?|언제|뭐\s*있|뭐야|무엇|어떻게|어디|얼마|누구|있어\s*\?|있나|했지|했나|냈지|냈나|알려\s*줘|찾아|보여\s*줘|확인해\s*줘|궁금")
DO_RE = re.compile(r"(작성|처리|만들|준비|회신서|답변서|초안)(해|을|를|해\s*줘|해줘|하자|해라|부탁)|작성해|처리해|만들어|준비해|시작해|진행해|해\s*줘$|해줘$")

def is_do_it(text: str) -> bool:
    """규칙 경로에서 '전부 처리' 지시로 볼 문장: 명령형 종결이 있고 질문 표지가 없을 때만. ("회신 기한이 언제야?"는 질문, "회신 작성해 줘"는 지시)"""
    t = (text or "").strip()
    return bool(DO_RE.search(t)) and not QUESTION_RE.search(t)

# 화면 진행 문구용 우리말 단계명
llm.TOOL_LABELS.update({"read_request": "요구서 읽기", "register_request": "요구서 기록", "plan_data": "자료 계획 세우기", "set_dates": "기준일 바꾸기", "pull_values": "값 가져오기",
                        "compare_with_past": "지난 제출값과 맞춰 보기", "suggest_reasons": "차이 사유 추천", "set_reason": "사유 적기", "write_draft": "회신 초안 쓰기", "edit_draft": "초안 고치기",
                        "foresee_questions": "예상 질문 뽑기", "make_hwpx": "한글 파일 만들기", "run_all": "전체 처리(읽기→한글 파일)", "case_status": "작업 상태 보기",
                        "describe_request": "말로 받은 요구 적기", "data_coverage": "지표 데이터 범위 보기"})

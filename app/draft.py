"""회신 초안 생성 + 요구 항목 충족 검사.
- 초안: API 키가 있으면 Claude가 확인된 수치·사유만으로 문안 작성, 없으면 규칙 기반 문안 조립.
- 충족 검사: 요구 항목마다 초안이 다뤘는지(지표·기준일 언급) 코드로 검사. API 키가 있으면 Claude가 누락·불일치를 추가 점검.
AI는 수치를 계산하거나 사유를 만들지 않는다: 입력으로 받은 값만 사용한다."""
import json, re
import pandas as pd
import llm, pii

def _fmt(v):
    return f"{v:,.1f}" if isinstance(v, float) else str(v)

def compare_summary(checklist) -> dict | None:
    """③ 점검표(레코드 리스트) → {판정: [센터…]} 요약. 없으면 None(초안은 '차이 없음'을 단정하지 않음)."""
    if not checklist: return None
    out = {}
    for r in checklist:
        out.setdefault(str(r.get("판정")), []).append(f"{r.get('center') or r.get('센터')}({r.get('base_date') or r.get('기준일')})")
    return out

def unique_texts(items: list[dict]) -> list[str]:
    """지표 분리로 같은 원문이 여러 항목에 걸쳐 있을 수 있어, 문안에는 원문 기준으로 한 번씩만 넘긴다(순서 유지)."""
    return list(dict.fromkeys(it.get("item_text") for it in items if it.get("item_text")))

def _sub(values: pd.DataFrame, ind, bd):
    """항목(지표·기준일)에 해당하는 값 행. 기준일 없는 항목(기간·월별)은 그 지표의 가져온 값 전부."""
    if not ind or not len(values): return values.iloc[0:0]
    return values[(values["indicator"] == ind) & (values["base_date"] == bd)] if bd else values[values["indicator"] == ind]

def item_summaries(items: list[dict], values: pd.DataFrame) -> list[dict]:
    """모델에 보내는 항목 요약(값 행은 보내지 않는다): 원문마다 지표별 건수·센터 수·기준일 범위·자료 없음 여부."""
    out = []
    for i, text in enumerate(unique_texts(items), 1):
        parts = []
        for it in [x for x in items if x.get("item_text") == text]:
            ind, bd = it.get("indicator"), it.get("base_date"); sub = _sub(values, ind, bd)
            if len(sub):
                dates = sorted(sub["base_date"].dropna().unique().tolist())
                parts.append({"지표": ind, "요구 기준일": bd, "값 건수": int(len(sub)), "센터 수": int(sub["center"].nunique()), "기준일": (dates if len(dates) <= 3 else [dates[0], "…", dates[-1]]), "기준일 수": len(dates)})
            else: parts.append({"지표": ind or "지표 미인식", "요구 기준일": bd, "자료 없음": True})
        out.append({"번호": i, "원문": text, "자료": parts})
    return out

def provenance_by_indicator(values: pd.DataFrame) -> dict:
    """지표별 산출 근거(값 행의 정의·집계기간·추출시점·원자료 버전을 모아 범위·집합으로). 첫 행 하나만 쓰던 것을 대체."""
    out = {}
    if values is None or not len(values): return out
    for ind, g in values.groupby("indicator"):
        def vals(col):
            return sorted({str(x) for x in g[col].dropna().tolist() if str(x).strip() and str(x) != "None"}) if col in g.columns else []
        d = {}
        if vals("definition"): d["정의"] = " / ".join(vals("definition")[:2]) + (" 외" if len(vals("definition")) > 2 else "")
        cp = vals("calc_period")
        if cp: d["집계기간"] = cp[0] if len(cp) == 1 else f"{cp[0]} ~ {cp[-1]}"
        if vals("extract_date"): d["추출시점"] = ", ".join(vals("extract_date")[:3])
        if vals("source_version"): d["원자료 버전"] = ", ".join(vals("source_version")[:2])
        out[str(ind)] = d
    return out

def reasons_text(reasons: dict) -> str:
    return "\n".join(f"- {k[1]} {k[0]}({k[2]}): {v}" for k, v in (reasons or {}).items() if v and str(v).strip()) or "- 과거 제출값과 차이 없음"

def provenance_text(prov_by_ind: dict, fallback: dict | None = None) -> str:
    """산출근거 칸(코드 생성): 지표별 정의·집계기간·추출시점·원자료 버전. 값 행에 없으면 호출 쪽이 준 근거(fallback)로, 그것도 없으면 [확인 필요]."""
    keys = (("지표 정의", "정의", "definition"), ("집계기간", "집계기간", "calc_period"), ("원자료 추출 시점", "추출시점", "extract_date"), ("원자료 버전", "원자료 버전", "source_version"))
    p = fallback or {}
    def cell(d, k, col): return d.get(k) or p.get(col) or "[확인 필요]"
    if prov_by_ind:
        return "\n".join(f"- {ind}: " + " · ".join(f"{label}: {cell(d, k, col)}" for label, k, col in keys) for ind, d in prov_by_ind.items())
    return "\n".join(f"- {label}: {p.get(col) or '[확인 필요]'}" for label, _, col in keys)

def compare_counts(checklist) -> dict | None:
    """대조 결과를 건수로(판정별 건수 + 차이 센터 30개까지). 센터 전체 목록을 보내지 않는다."""
    if not checklist: return None
    cnt, diff = {}, []
    for r in checklist:
        j = str(r.get("판정")); cnt[j] = cnt.get(j, 0) + 1
        if j == "차이" and len(diff) < 30: diff.append(f"{r.get('center') or r.get('센터')}({r.get('base_date') or r.get('기준일')})")
    return {"판정별 건수": cnt, "차이 센터": diff}

def rule_draft(req: dict, items: list[dict], values: pd.DataFrame, reasons: dict, provenance: dict) -> dict:
    """규칙 기반 문안. 반환: {제목, 본문, 차이사유, 산출근거}. 값을 계산하지 않는다(평균·최소·최대 없음)."""
    title = f"{req.get('title') or '자료'} 제출"
    lines = [f"1. 귀 {req.get('requester') or '기관'}에서 요구하신 「{req.get('title') or '자료'}」(접수 {req.get('received_date') or '-'})에 대하여 아래와 같이 제출합니다."]
    for i, text in enumerate(unique_texts(items), 1):
        parts, missing = [], []
        for it in [x for x in items if x.get("item_text") == text]:
            ind, bd = it.get("indicator"), it.get("base_date"); sub = _sub(values, ind, bd)
            if len(sub):
                dates = sorted(sub["base_date"].dropna().unique().tolist()); n_c = sub["center"].nunique()
                when = f"{bd} 기준" if bd else (f"{dates[0]} ~ {dates[-1]} 기준({len(dates)}개 기준일)" if len(dates) > 1 else (f"{dates[0]} 기준" if dates else "기준일 미상"))
                parts.append(f"{when} {ind} {n_c}개 센터 자료를 붙임 표와 같이 제출합니다.")
            else: missing.append(ind or "지표 미인식")
        if missing: parts.append(f"[확인 필요 — {', '.join(missing)}: 확정 수치가 없어 별도 산출 후 제출]")
        lines.append(f"{i + 1}. {text}: " + " ".join(parts))
    return {"제목": title, "본문": "\n".join(lines), "차이사유": reasons_text(reasons), "산출근거": provenance_text(provenance_by_indicator(values), provenance)}

SYSTEM = """당신은 EBS 지역교육협력부의 대외 요구자료 회신 문안 작성 보조입니다. 공문체(~합니다/~입니다, 번호 문단)로 씁니다.
원칙: 입력으로 받은 수치·사유·근거만 사용합니다. 수치를 새로 계산·요약·추정하지 않고(평균·증감도 계산하지 않음), 사유를 지어내지 않습니다.
데이터가 없는 항목은 본문에 '[확인 필요]'로 남깁니다. 단정적 평가('우수', '개선됨')를 넣지 않습니다."""

def llm_draft(req, items, values, reasons, provenance, checklist=None) -> dict:
    """모델은 제목·본문만 쓴다. 값 행은 보내지 않고(표가 보여 준다) 항목별 요약·대조 건수·지표별 근거만 보낸다. 차이사유·산출근거 칸은 코드가 만든다."""
    prov_by_ind = provenance_by_indicator(values)
    payload = {"요구": pii.redact_obj({k: req.get(k) for k in ("requester", "received_date", "due_date", "title")}),
               "요구 항목(번호·원문·가진 자료 요약)": pii.redact_obj(item_summaries(items, values)),
               "담당자가 입력한 차이 사유(원문 그대로 인용 가능)": pii.redact_obj({" ".join(k): v for k, v in reasons.items() if v}),
               "과거 제출값 대조(코드 판정, 건수)": compare_counts(checklist) or "대조 결과 없음 — 차이 유무를 언급하지 말 것",
               "산출 근거(지표별, 참고용)": prov_by_ind or provenance}
    n_items = len(unique_texts(items))
    prompt = (f"아래 데이터로 회신 문안의 제목과 본문을 작성하세요. 이 문안은 '답변자료' 양식에 들어갑니다: 양식이 항목 번호 제목(1. 2. …)과 수치 표, 산출 근거, 차이 주석, 붙임, '끝.'을 따로 붙이므로 본문에는 각 항목의 **설명 문장만** 씁니다.\n"
              f"본문 규칙: 요구 항목 {n_items}개에 맞춰 '1.'부터 '{n_items}.'까지만 번호를 쓰고(항목 안의 문장에는 번호·기호를 붙이지 않음), 항목마다 1~3문장. "
              "수치는 '아래 표와 같습니다'로 안내하고 센터 이름·값·평균·건수 비교를 쓰지 않습니다(값은 보내지 않았고 표가 보여 줍니다). 기준일 범위와 센터 수는 요약에 있는 그대로만 적을 수 있습니다. "
              "자료가 없는 지표는 '[확인 필요]'로 남깁니다. 값이 0이거나 비어 있는 이유, 증감의 원인을 추측하지 않습니다. "
              "대조 결과는 건수만 언급할 수 있고('일치' 건수가 있을 때만 '일치'를 말함), '과거 제출값 없음'·'값 누락'은 언급하지 않습니다. 담당자 사유가 있으면 그 문구만 인용합니다. "
              "'붙임', '끝.', 인사말('귀 기관의 요구에 따라…')은 쓰지 않습니다.\n\n"
              + json.dumps(payload, ensure_ascii=False, default=str))
    schema = {"type": "object", "additionalProperties": False, "required": ["제목", "본문"], "properties": {"제목": {"type": "string"}, "본문": {"type": "string"}}}
    d = llm.ask_json(prompt, SYSTEM, 8000, purpose="회신 초안", schema=schema)
    return {"제목": str(d.get("제목", "") or ""), "본문": str(d.get("본문", "") or ""), "차이사유": reasons_text(reasons), "산출근거": provenance_text(prov_by_ind, provenance)}

def make_draft(req, items, values, reasons, provenance, checklist=None) -> tuple[dict, str]:
    if llm.available():
        try: return llm_draft(req, items, values, reasons, provenance, checklist), f"Claude API ({llm.model_label()})"
        except Exception as e:
            d = rule_draft(req, items, values, reasons, provenance); d["_error"] = f"{type(e).__name__}: {e}"; return d, "규칙 기반(API 오류로 대체)"
    return rule_draft(req, items, values, reasons, provenance), "규칙 기반(API 키 없음)"

def coverage_check(items: list[dict], draft: dict, values: pd.DataFrame) -> pd.DataFrame:
    """요구 항목 충족 검사(코드). 항목별: 초안 언급 여부, 기준일 언급 여부, 수치 존재 여부, 미확인 표시 여부"""
    text = " ".join(str(v) for v in draft.values())
    rows = []
    for i, it in enumerate(items, 1):
        ind, bd = it.get("indicator"), it.get("base_date")
        has_val = bool(len(values) and ind and (((values["indicator"] == ind) & (values["base_date"] == bd)).any() if bd else (values["indicator"] == ind).any()))   # 기준일 없는 항목은 지표 값이 있으면 충족
        head = (it.get("item_text") or "")[:10]
        mentioned = bool(ind and ind in text) or (bool(head) and head in text)
        bd_ok = (bd in text) if bd else None
        flagged = "[확인 필요" in text and bool(head) and head in text
        status = "충족" if (mentioned and has_val) else ("미확인 표시" if flagged else "누락")
        rows.append({"번호": i, "요구 항목": it.get("item_text"), "지표": ind, "기준일": bd, "초안 언급": mentioned,
                     "기준일 언급": bd_ok, "확정 수치": has_val, "판정": status})
    return pd.DataFrame(rows)

def stray_numbers(draft: dict, values: pd.DataFrame | None) -> list[str]:
    """코드 점검: 초안 본문의 소수 값(61.0, 88.5 …) 중 확정 수치 표에 없는 것. 정수(센터 수·기준일 수)와 날짜는 보지 않는다."""
    body = str((draft or {}).get("본문", ""))
    nums = {m.group(0) for m in re.finditer(r"(?<![\d.\-])\d{1,3}(?:,\d{3})*\.\d+(?![\d.])", body)}
    have = {f"{v:.1f}" for v in values["value"].dropna().tolist()} | {str(v) for v in values["value"].dropna().tolist()} if values is not None and len(values) else set()
    return sorted(n for n in nums if n not in have and f"{float(n.replace(',', '')):.1f}" not in have)

def coverage_check_llm(items, draft, values: pd.DataFrame | None = None, checklist=None, reasons: dict | None = None, provenance: dict | None = None) -> str | None:
    """추가 점검: (1) 누락 항목 (2) 기준일·단위·센터 범위 불일치 (3) 근거 없는 단정·평가 표현은 모델이, 표에 없는 숫자는 코드(stray_numbers)가 본다. 값 행은 보내지 않는다."""
    if not llm.available(): return None
    try:
        stray = stray_numbers(draft, values)
        prompt = ("아래 요구 항목 요약과 회신 초안을 비교해 (1) 초안이 다루지 않은 항목 (2) 기준일·단위·센터 범위가 요구와 다른 부분 (3) 근거 없는 단정·평가·원인 추측 표현을 번호를 붙여 간단히 지적하세요(항목당 한 줄). "
                  "'[확인 필요]'로 남긴 항목은 담당자가 채울 자리이고, '담당자 입력 사유'는 담당자가 확인해 적은 것이므로 둘 다 지적하지 마세요. 값 자체는 표가 보여 주므로 숫자 검증은 하지 마세요. 없으면 '문제 없음'. 초안을 다시 쓰지는 마세요.\n"
                  "요구 항목 요약: %s\n과거 제출값 대조(건수): %s\n담당자 입력 사유: %s\n초안 제목: %s\n초안 본문: %s"
                  % (json.dumps(pii.redact_obj(item_summaries(items, values if values is not None else pd.DataFrame(columns=["indicator", "center", "base_date", "value"]))), ensure_ascii=False, default=str),
                     json.dumps(compare_counts(checklist) or "없음", ensure_ascii=False),
                     json.dumps(pii.redact_obj({" ".join(k): v for k, v in (reasons or {}).items() if v}), ensure_ascii=False),
                     json.dumps(pii.redact_obj((draft or {}).get("제목", "")), ensure_ascii=False), json.dumps(pii.redact_obj((draft or {}).get("본문", "")), ensure_ascii=False)))
        out = llm.ask(prompt, "당신은 공공기관 회신 문서 검토자입니다. 지적은 구체적으로, 근거 없는 추측은 하지 않습니다.", 4000, purpose="초안 점검")
        if stray: out = f"(코드 점검) 확정 수치 표에 없는 숫자가 본문에 있습니다: {', '.join(stray)}\n" + out
        return out
    except Exception as e:
        return f"(Claude 점검 실패: {type(e).__name__}: {e})"

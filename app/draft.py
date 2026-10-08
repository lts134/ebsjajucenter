"""회신 초안 생성 + 요구 항목 충족 검사.
- 초안: API 키가 있으면 Claude가 확인된 수치·사유만으로 문안 작성, 없으면 규칙 기반 문안 조립.
- 충족 검사: 요구 항목마다 초안이 다뤘는지(지표·기준일 언급) 코드로 검사. API 키가 있으면 Claude가 누락·불일치를 추가 점검.
AI는 수치를 계산하거나 사유를 만들지 않는다: 입력으로 받은 값만 사용한다."""
import json
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

def rule_draft(req: dict, items: list[dict], values: pd.DataFrame, reasons: dict, provenance: dict) -> dict:
    """규칙 기반 문안. 반환: {제목, 본문, 차이사유, 산출근거}"""
    title = f"{req.get('title') or '자료'} 제출"
    lines = [f"1. 귀 {req.get('requester') or '기관'}에서 요구하신 「{req.get('title') or '자료'}」(접수 {req.get('received_date') or '-'})에 대하여 아래와 같이 제출합니다."]
    for i, text in enumerate(unique_texts(items), 1):
        parts, missing = [], []
        for it in [x for x in items if x.get("item_text") == text]:
            ind, bd = it.get("indicator"), it.get("base_date")
            if ind and len(values): sub = values[(values["indicator"] == ind) & (values["base_date"] == bd)] if bd else values[values["indicator"] == ind]   # 기준일 없는 항목(기간·월별)은 그 지표의 가져온 값 전부
            else: sub = values.iloc[0:0]
            if len(sub):
                dates = sorted(sub["base_date"].dropna().unique().tolist()); n_c = sub["center"].nunique()
                when = f"{bd} 기준" if bd else (f"{dates[0]} ~ {dates[-1]} 기준({len(dates)}개 기준일)" if len(dates) > 1 else (f"{dates[0]} 기준" if dates else "기준일 미상"))
                parts.append(f"{when} {ind} {n_c}개 센터 자료를 붙임 표와 같이 제출합니다." + (f" (전체 평균 {sub['value'].mean():.1f}, 최소 {sub['value'].min():.1f}, 최대 {sub['value'].max():.1f})" if len(dates) <= 1 else ""))
            else: missing.append(ind or "지표 미인식")
        if missing: parts.append(f"[확인 필요 — {', '.join(missing)}: 확정 수치가 없어 별도 산출 후 제출]")
        lines.append(f"{i + 1}. {text}: " + " ".join(parts))
    reason_lines = [f"- {k[1]} {k[0]}({k[2]}): {v}" for k, v in reasons.items() if v and v.strip()] or ["- 과거 제출값과 차이 없음"]
    prov = provenance or {}
    prov_lines = [f"- 지표 정의: {prov.get('definition', '[확인 필요]')}", f"- 집계기간: {prov.get('calc_period', '[확인 필요]')}",
                  f"- 원자료 추출 시점: {prov.get('extract_date', '[확인 필요]')}", f"- 원자료 버전: {prov.get('source_version', '[확인 필요]')}"]
    return {"제목": title, "본문": "\n".join(lines), "차이사유": "\n".join(reason_lines), "산출근거": "\n".join(prov_lines)}

SYSTEM = """당신은 EBS 지역교육협력부의 대외 요구자료 회신 문안 작성 보조입니다. 공문체(~합니다/~입니다, 번호 문단)로 씁니다.
원칙: 입력으로 받은 수치·사유·근거만 사용합니다. 수치를 새로 계산·요약·추정하지 않고(평균·증감도 계산하지 않음), 사유를 지어내지 않습니다.
데이터가 없는 항목은 본문에 '[확인 필요]'로 남깁니다. 단정적 평가('우수', '개선됨')를 넣지 않습니다."""

def llm_draft(req, items, values, reasons, provenance, checklist=None) -> dict:
    cs = compare_summary(checklist)
    payload = {"요구": pii.redact_obj({k: req.get(k) for k in ("requester", "received_date", "due_date", "title")}),
               "요구 항목": pii.redact_obj(unique_texts(items)),
               "확정 수치": values[["indicator", "center", "base_date", "value"]].to_dict("records") if len(values) else [],
               "차이 사유(담당자 입력)": {" ".join(k): v for k, v in reasons.items() if v},
               "과거 제출값 대조 결과(코드 판정)": cs or "대조 결과 없음 — 사유가 입력된 센터 외에는 차이 유무를 언급하지 말 것",
               "산출 근거": provenance}
    prompt = ("아래 데이터로 회신 문안을 작성하세요. 본문은 요구 항목 순서대로 번호를 매기고, 수치는 '붙임 표와 같음'으로 안내하되 "
              "표에 있는 센터·값은 그대로 인용해도 됩니다. 차이사유는 입력된 사유만 쓰고, '차이 없음'은 대조 결과에 '일치'로 판정된 센터에 대해서만 쓸 수 있습니다.\n"
              "JSON만 출력: {\"제목\": ..., \"본문\": (번호 매긴 문단, 줄바꿈 구분), \"차이사유\": ..., \"산출근거\": ...}\n\n"
              + json.dumps(payload, ensure_ascii=False, default=str))
    schema = {"type": "object", "additionalProperties": False, "required": ["제목", "본문", "차이사유", "산출근거"],
              "properties": {k: {"type": "string"} for k in ("제목", "본문", "차이사유", "산출근거")}}
    d = llm.ask_json(prompt, SYSTEM, 2500, purpose="회신 초안", schema=schema)
    return {k: str(d.get(k, "") or "") for k in ("제목", "본문", "차이사유", "산출근거")}

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

def coverage_check_llm(items, draft, values: pd.DataFrame | None = None, checklist=None, reasons: dict | None = None, provenance: dict | None = None) -> str | None:
    """Claude 추가 점검: 누락 항목, 기준일·단위 불일치, 확정 수치와 다른 숫자, 근거 없는 단정 표현. 판단만 하고 수정하지 않는다."""
    if not llm.available(): return None
    try:
        vals = values[["indicator", "center", "base_date", "value"]].to_dict("records") if values is not None and len(values) else []
        prompt = ("아래 요구 항목 목록·확정 수치·회신 초안을 비교해 (1) 초안이 다루지 않은 항목 (2) 기준일·단위·센터 범위가 요구와 다른 부분 "
                  "(3) 초안의 숫자 중 확정 수치 표와 다르거나 표에 없는 것 (4) 근거 없는 단정·평가 표현을 번호를 붙여 간단히 지적하세요. "
                  "'[확인 필요]'로 남긴 항목은 담당자가 채울 자리이고, '담당자 입력 사유'는 담당자가 확인해 적은 것이므로 둘 다 지적하지 마세요. 없으면 '문제 없음'. 초안을 다시 쓰지는 마세요.\n"
                  "요구 항목: %s\n확정 수치: %s\n과거 제출값 대조 결과(코드 판정): %s\n담당자 입력 사유: %s\n산출 근거(제출값 파일에 기록된 값): %s\n초안: %s"
                  % (json.dumps(pii.redact_obj(unique_texts(items)), ensure_ascii=False), json.dumps(vals, ensure_ascii=False, default=str),
                     json.dumps(compare_summary(checklist) or "없음", ensure_ascii=False),
                     json.dumps({" ".join(k): v for k, v in (reasons or {}).items() if v}, ensure_ascii=False),
                     json.dumps(provenance or {}, ensure_ascii=False, default=str), json.dumps(draft, ensure_ascii=False)))
        return llm.ask(prompt, "당신은 공공기관 회신 문서 검토자입니다. 지적은 구체적으로, 근거 없는 추측은 하지 않습니다.", 800, purpose="초안 점검")
    except Exception as e:
        return f"(Claude 점검 실패: {type(e).__name__}: {e})"

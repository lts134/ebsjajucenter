"""월간 리포트: 한 달 동안 받은 요구·확정·발송 건수, 요청 주체별·지표별 건수, 처리 소요일(단순 집계). 지표 값은 계산하지 않는다.
사용: d = report.monthly("2026-09") → to_excel(d) / to_text(d)"""
from __future__ import annotations
import io, datetime as dt
import pandas as pd
import db

def _in(month: str, date: str | None) -> bool:
    return bool(date) and str(date)[:7] == month

def _days(a: str | None, b: str | None):
    try: return (dt.date.fromisoformat(str(b)[:10]) - dt.date.fromisoformat(str(a)[:10])).days
    except (ValueError, TypeError): return None

def months_available() -> list[str]:
    """기록에 있는 접수·제출·발송 월 목록(최근 순). 비어 있으면 이번 달."""
    ms = {str(r["received_date"])[:7] for r in db.list_requests() if r.get("received_date")}
    ms |= {str(s["submitted_date"])[:7] for s in db.list_submissions() if s.get("submitted_date")}
    ms |= {str(d["sent_at"])[:7] for d in db.list_dispatches() if d.get("sent_at")}
    ms = sorted((m for m in ms if len(m) == 7 and m[4] == "-"), reverse=True)
    return ms or [dt.date.today().strftime("%Y-%m")]

def monthly(month: str) -> dict:
    reqs = db.list_requests(); subs = [s for s in db.list_submissions() if s.get("status") == "confirmed"]; disp = db.list_dispatches()
    received = [r for r in reqs if _in(month, r.get("received_date"))]
    confirmed = [s for s in subs if _in(month, s.get("submitted_date"))]
    sent = [d for d in disp if _in(month, d.get("sent_at"))]
    first_conf = {}                                                     # 요구서별 첫 확정일·첫 발송일(소요일 집계용)
    for s_ in sorted(subs, key=lambda x: str(x.get("submitted_date"))): first_conf.setdefault(s_["request_id"], s_["submitted_date"])
    first_sent = {}
    for d in sorted(disp, key=lambda x: str(x.get("sent_at"))): first_sent.setdefault(d["request_id"], d["sent_at"])
    by_id = {r["id"]: r for r in reqs}
    lead_conf = [x for x in (_days(by_id[rid]["received_date"], d_) for rid, d_ in first_conf.items() if rid in by_id and _in(month, d_)) if x is not None and x >= 0]
    lead_sent = [x for x in (_days(first_conf.get(rid), d_) for rid, d_ in first_sent.items() if _in(month, d_)) if x is not None and x >= 0]
    end = (dt.date.fromisoformat(month + "-01").replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    open_end = [r for r in reqs if r.get("received_date") and str(r["received_date"])[:10] <= end.isoformat() and (r.get("status") or "접수") in ("접수", "처리 중", "보류")]
    overdue = [r for r in open_end if r.get("due_date") and str(r["due_date"])[:10] < end.isoformat()]
    by_req = {}
    for r in received: by_req[r.get("requester") or "미기재"] = by_req.get(r.get("requester") or "미기재", 0) + 1
    by_ind = {}
    for r in received:
        for it in db.get_items(r["id"]):
            k = it.get("indicator") or "(사전에 없음)"; by_ind[k] = by_ind.get(k, 0) + 1
    rows = [{"요구번호": r["id"], "접수일": r.get("received_date"), "제출기한": r.get("due_date"), "요청 주체": r.get("requester"), "제목": r.get("title"), "상태": r.get("status") or "접수", "담당자": r.get("assignee") or "",
             "첫 확정일": first_conf.get(r["id"]), "첫 발송일": first_sent.get(r["id"]), "접수→확정(일)": _days(r.get("received_date"), first_conf.get(r["id"])), "확정→발송(일)": _days(first_conf.get(r["id"]), first_sent.get(r["id"]))} for r in received]
    return {"month": month, "received": len(received), "confirmed": len(confirmed), "sent": len(sent), "open_end": len(open_end), "overdue_end": len(overdue),
            "by_requester": sorted(by_req.items(), key=lambda x: -x[1]), "by_indicator": sorted(by_ind.items(), key=lambda x: -x[1]),
            "lead": {"접수→확정": {"건수": len(lead_conf), "평균": round(sum(lead_conf) / len(lead_conf), 1) if lead_conf else None, "최대": max(lead_conf) if lead_conf else None},
                     "확정→발송": {"건수": len(lead_sent), "평균": round(sum(lead_sent) / len(lead_sent), 1) if lead_sent else None, "최대": max(lead_sent) if lead_sent else None}},
            "rows": rows}

def to_text(d: dict) -> str:
    """보고 메일·회의 자료에 붙이는 요약문(건수·일수는 기록의 단순 집계)."""
    y, m = d["month"].split("-"); L = d["lead"]
    lines = [f"[{int(y)}년 {int(m)}월 대외 요구자료 대응 현황]", f"- 접수 {d['received']}건 · 확정 {d['confirmed']}건 · 발송 {d['sent']}건 · 월말 진행 중 {d['open_end']}건" + (f"(기한 경과 {d['overdue_end']}건)" if d["overdue_end"] else "")]
    if d["by_requester"]: lines.append("- 요청 주체별 접수: " + ", ".join(f"{k} {v}건" for k, v in d["by_requester"][:8]))
    if d["by_indicator"]: lines.append("- 많이 요구된 지표: " + ", ".join(f"{k} {v}회" for k, v in d["by_indicator"][:8]))
    if L["접수→확정"]["건수"]: lines.append(f"- 접수→확정 소요일: 평균 {L['접수→확정']['평균']}일, 최대 {L['접수→확정']['최대']}일 ({L['접수→확정']['건수']}건)")
    if L["확정→발송"]["건수"]: lines.append(f"- 확정→발송 소요일: 평균 {L['확정→발송']['평균']}일, 최대 {L['확정→발송']['최대']}일 ({L['확정→발송']['건수']}건)")
    lines.append("※ 건수·일수는 기록 DB의 단순 집계이며 지표 값은 포함하지 않음.")
    return "\n".join(lines)

def to_excel(d: dict) -> bytes:
    import pii
    buf = io.BytesIO()
    with pd.ExcelWriter(buf) as xw:
        L = d["lead"]
        pd.DataFrame([{"월": d["month"], "접수": d["received"], "확정": d["confirmed"], "발송": d["sent"], "월말 진행 중": d["open_end"], "월말 기한 경과": d["overdue_end"],
                       "접수→확정 평균(일)": L["접수→확정"]["평균"], "확정→발송 평균(일)": L["확정→발송"]["평균"]}]).to_excel(xw, sheet_name="요약", index=False)
        pii.excel_safe(pd.DataFrame(d["rows"])).to_excel(xw, sheet_name="요구서", index=False)
        pd.DataFrame(d["by_requester"], columns=["요청 주체", "건수"]).to_excel(xw, sheet_name="요청 주체별", index=False)
        pd.DataFrame(d["by_indicator"], columns=["지표", "요구 횟수"]).to_excel(xw, sheet_name="지표별", index=False)
    return buf.getvalue()

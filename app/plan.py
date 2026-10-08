"""자료 계획: 요구 항목(지표·기준일·기간 표현)과 '가진 자료'(지표 데이터·과거 제출값)의 범위를 맞춰 보고,
무엇을 어느 기준일로 낼지 **제안**한다. 담당자가 확인·조정한 뒤 가져온다.

예) 항목 "센터별 월별 등원율", 기준일 없음, 지표 데이터에 2025-12-31 ~ 2026-09-30 → "기준일이 정해지지 않았고 '월별'이라 적혀 있습니다.
    지표 데이터에 10개월치가 있어 전부 제안합니다."  /  기준일 없음·월별 아님 → 최신 기준일 제안  /  요구 기준일이 없으면 그 이전 가장 가까운 것 제안.
규칙으로 판단하고(검증 가능), 값은 계산하지 않는다. 기간 표현을 규칙이 못 읽으면 그대로 '최신'을 제안하고 그 사실을 적는다."""
from __future__ import annotations
import re, datetime as dt
import db

MONTHLY = re.compile(r"월\s*별|매\s*월|월\s*단위|월간|월\s*현황")
YEARLY = re.compile(r"연\s*도\s*별|년\s*도\s*별|연\s*별|매\s*년|연간")
QUARTERLY = re.compile(r"분기\s*별|매\s*분기|분기\s*단위|분기\s*현황")                    # '2분기'(특정 분기)는 기간으로 따로 읽는다
RECENT = re.compile(r"최근\s*(\d+)\s*(년|개년|개월|월)")
RANGE = re.compile(r"(20\d{2})(?:\s*[.\-/년]\s*(\d{1,2})\s*(?:월|\.)?)?\s*[~\-–∼]\s*(20\d{2})(?:\s*[.\-/년]\s*(\d{1,2})\s*(?:월|\.)?)?")   # 2025.12 ~ 2026.8 · 2025. 12. ~ 2026. 8. · 2025년 12월 ~ 2026년 8월
HALF = re.compile(r"(20\d{2})\s*년?\s*(상|하)\s*반기")                                           # 2026년 상반기
QUARTER_N = re.compile(r"(20\d{2})\s*년?\s*([1-4])\s*/?\s*분기")                               # 2026년 2분기
MONTH_RANGE = re.compile(r"(20\d{2})\s*년?\s*(\d{1,2})\s*월?\s*[~\-–∼]\s*(\d{1,2})\s*월")   # 2026년 1~3월
YEAR_ONLY = re.compile(r"(?<!\d)(20\d{2})\s*년(?!\s*[~\-–∼])")
TWO_DIGIT_YEAR = re.compile(r"(?<![\d.])'?(\d{2})\s*년")                                   # 25년 → 2025년 ('26년 포함)

def _norm_text(text: str) -> str:
    """기간 표현을 규칙이 읽는 꼴로: '25년 12월부터 26년 8월까지' → '2025년 12월 ~ 2026년 8월'. 두 자리 연도·부터/까지·사이."""
    t = TWO_DIGIT_YEAR.sub(lambda m: f"20{m.group(1)}년", text or "")
    t = re.sub(r"(월|년|일|\d)\s*(?:부터|에서)\s*", r"\1 ~ ", t)
    t = re.sub(r"\s*(?:까지|사이|간)(?![가-힣])", "", t)
    return t

def _month_end(y: int, m: int) -> str:
    import calendar
    return f"{y:04d}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}"

def _window(text: str, anchor: str | None) -> tuple[str | None, str | None, str]:
    """기간 표현 → (시작일, 끝일, 설명). 못 읽으면 (None, None, '')."""
    text = _norm_text(text)
    m = RANGE.search(text)
    if m:
        y1, m1, y2, m2 = int(m.group(1)), int(m.group(2) or 1), int(m.group(3)), int(m.group(4) or 12)
        return f"{y1:04d}-{m1:02d}-01", _month_end(y2, m2), f"기간 {m.group(0).strip()}"
    m = MONTH_RANGE.search(text)
    if m:
        y, m1, m2 = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{y:04d}-{m1:02d}-01", _month_end(y, m2), f"기간 {m.group(0).strip()}"
    m = HALF.search(text)
    if m:
        y = int(m.group(1)); lo, hi = (1, 6) if m.group(2) == "상" else (7, 12)
        return f"{y:04d}-{lo:02d}-01", _month_end(y, hi), f"기간 {y}년 {m.group(2)}반기"
    m = QUARTER_N.search(text)
    if m:
        y, q = int(m.group(1)), int(m.group(2))
        return f"{y:04d}-{3 * q - 2:02d}-01", _month_end(y, 3 * q), f"기간 {y}년 {q}분기"
    m = RECENT.search(text)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        end = dt.date.fromisoformat(anchor) if anchor else dt.date.today()
        months = n * 12 if unit in ("년", "개년") else n
        y, mo = end.year, end.month - months + 1
        while mo <= 0: y -= 1; mo += 12
        return f"{y:04d}-{mo:02d}-01", end.isoformat(), f"'{m.group(0)}' ({'접수일' if anchor else '오늘'} 기준)"
    m = YEAR_ONLY.search(text)
    if m:
        y = int(m.group(1)); return f"{y}-01-01", f"{y}-12-31", f"{y}년"
    return None, None, ""

def plan_item(item: dict, anchor_date: str | None = None) -> dict:
    """항목 하나의 자료 계획.
    반환: {"indicator","base_date","mode","dates"(제안 기준일들),"options"(고를 수 있는 기준일 전부),"source"('data'|'past'|None),"why"(한 문장),"n_rows"(제안 건수)}"""
    ind, bd = item.get("indicator"), item.get("base_date")
    text = " ".join(str(item.get(k) or "") for k in ("item_text", "period", "unit"))
    out = {"indicator": ind, "base_date": bd, "mode": "none", "dates": [], "options": [], "source": None, "why": "", "n_rows": 0}
    if not ind:
        out["mode"] = "unknown_indicator"; out["why"] = "지표를 인식하지 못해 자료를 찾을 수 없습니다. 항목 문구를 확인하거나 지표 사전에 추가하세요."; return out
    data_dates = sorted(db.data_dates_for(ind)); past_dates = sorted(db.past_dates_for(ind))
    source, options = ("data", data_dates) if data_dates else (("past", past_dates) if past_dates else (None, []))
    out["source"], out["options"] = source, options
    src_name = "지표 데이터" if source == "data" else "과거 제출값"
    if not options:
        out["why"] = f"'{ind}'은(는) 지표 데이터에도 과거 제출값에도 없습니다 → 새로 산출해야 합니다."; return out
    span = f"{options[0]} ~ {options[-1]} ({len(options)}개 기준일)" if len(options) > 1 else options[0]
    if bd:
        if bd in options:
            out.update(mode="exact", dates=[bd], why=f"요구 기준일 {bd} 값이 {src_name}에 있습니다.")
        else:
            earlier = [d for d in options if d <= bd]
            if earlier:
                out.update(mode="nearest", dates=[earlier[-1]], why=f"요구 기준일 {bd} 값은 없고, 그 이전 가장 가까운 {earlier[-1]} 값이 {src_name}에 있습니다. 이 값으로 드리려면 기준일 차이를 회신에 밝혀야 합니다.")
            else:
                out.update(mode="none", why=f"요구 기준일 {bd} 이전 자료가 {src_name}에 없습니다(있는 범위 {span}) → 새로 산출.")
        out["n_rows"] = _count(ind, out["dates"], source); return out
    # 기준일이 없음: 기간 표현을 읽는다
    lo, hi, why_w = _window(text, anchor_date)
    in_window = [d for d in options if (lo is None or d >= lo) and (hi is None or d <= hi)] if (lo or hi) else []
    pool = in_window or options
    clipped = "" if in_window or not (lo or hi) else f" 요구 기간({why_w})에 해당하는 자료가 없어 전체 범위에서 고릅니다."
    explicit_range = bool(in_window) and not re.fullmatch(r"20\d{2}년", why_w)      # '2025년'만 적힌 것은 기간이 아니라 연도로 보고 최신을 제안
    if MONTHLY.search(text):
        out.update(mode="all", dates=pool, why=f"기준일이 정해지지 않았고 '월별'이라 적혀 있습니다. {src_name}에 {pool[0]} ~ {pool[-1]}({len(pool)}개월)이 있어 전부 제안합니다." + (f" ({why_w})" if why_w and not clipped else "") + clipped)
    elif YEARLY.search(text):
        by_year = {}
        for d in pool: by_year[d[:4]] = d                      # 연도별: 각 연도의 마지막 기준일
        picks = sorted(by_year.values())
        out.update(mode="yearly", dates=picks, why=f"기준일이 정해지지 않았고 '연도별'이라 적혀 있습니다. {src_name}에서 연도마다 마지막 기준일({', '.join(picks)})을 제안합니다." + clipped)
    elif QUARTERLY.search(text):
        picks = [d for d in pool if d[5:7] in ("03", "06", "09", "12")] or pool
        out.update(mode="quarterly", dates=picks, why=f"기준일이 정해지지 않았고 '분기별'이라 적혀 있습니다. {src_name}에서 분기 말({len(picks)}개)을 제안합니다." + clipped)
    elif explicit_range and len(pool) > 1:
        out.update(mode="range", dates=pool, why=f"기준일 대신 기간이 적혀 있습니다({why_w.removeprefix('기간 ')}). {src_name}에 그 기간의 기준일 {len(pool)}개({pool[0]} ~ {pool[-1]})가 있어 전부 제안합니다. 끝 달 하나만 내려면 아래에서 바꾸세요.")
    else:
        latest = pool[-1]
        out.update(mode="latest", dates=[latest], why=f"기준일이 정해지지 않았습니다. {src_name}에 {span}이 있어 가장 최신인 {latest} 기준 값을 제안합니다." + (f" ({why_w})" if why_w and not clipped else "") + clipped + " 다른 기준일이 맞으면 아래에서 바꾸세요.")
    out["n_rows"] = _count(ind, out["dates"], source)
    return out

def _count(ind: str, dates: list[str], source: str | None) -> int:
    if not source: return 0
    n = 0
    for d in dates:
        if source == "data": n += len(db.data_values_for(ind, d))
        else:
            past = db.past_values_for(ind, d)
            if past: sid = past[0]["submission_id"]; n += sum(1 for v in past if v["submission_id"] == sid)
    return n

def rows_for(ind: str, dates: list[str], source: str) -> list[dict]:
    """제안(또는 담당자가 고른 기준일)대로 값을 모은다. 지표 데이터는 그대로, 과거 제출값은 기준일마다 최신 제출본 하나."""
    out = []
    for d in dates:
        if source == "data":
            for v in db.data_values_for(ind, d):
                out.append({**v, "source_file": f"지표 데이터 ({v.get('source')}, 적재 {str(v.get('loaded_at'))[:10]})", "source_sheet": "", "source_row": None})
        else:
            past = db.past_values_for(ind, d)
            if not past: continue
            sid = past[0]["submission_id"]
            for v in past:
                if v["submission_id"] == sid:
                    out.append({**v, "source_file": f"기록 제출본 #{sid} ({v['submitted_date']} {v['requester']})", "source_sheet": "", "source_row": None})
    return out

def plan(items: list[dict], anchor_date: str | None = None) -> list[dict]:
    return [plan_item(it, anchor_date) for it in items]

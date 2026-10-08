"""통합 대시보드(자기주도학습센터 현황 대시보드) 저장 파일(.html) → 지표 데이터·센터 명부.

대시보드는 자바스크립트 안에 자료를 상수로 품고 있는 한 장짜리 페이지다(출결시스템 추출 xls를 옮겨 둔 것). 브라우저에서 '페이지 저장'한 HTML을 올리면
그 상수들을 읽어(실행하지 않고 글자로 잘라 json5로 해석) 센터×지표×기준일 값으로 바꾼다. 값은 계산하지 않고 그대로 옮기며, 정의·추출시점·원자료 파일명을 산출 근거로 붙인다.

가져오는 것(허용 목록만): ATT_DATA(월간 출결) · DG_DATA(진단검사) · MG_DATA(관리인원) · CS_DATA(상담횟수) · USAGE_DATA(이용현황 스냅샷) · CENTER_REGISTRY·DATA25·DATA26·WEEKEND_RAW(센터 명부).
가져오지 않는 것: 계정·비밀번호 영역, 연락처(HEAD_PHONE), Firebase 설정, 운영인력 개인 정보(ASSEMBLY_DATA의 이름·자격·경력), 센터장·담당자 이름."""
from __future__ import annotations
import calendar, re
import json5

ALLOWED = ("ATT_DATA", "DG_DATA", "MG_DATA", "CS_DATA", "USAGE_DATA", "CENTER_REGISTRY", "DATA25", "DATA26", "WEEKEND_RAW")
NEVER = ("HEAD_PHONE", "FIREBASE_CONFIG", "MAP_FIREBASE_CONFIG", "ASSEMBLY_DATA")

ATT_COLS = ["등원예정일수", "등원일수", "등원율", "목표시간", "달성시간", "시간달성율", None]          # m[i] 순서. 마지막 '일평균'은 정의가 불분명해 가져오지 않음
MG_COLS = ["관리인원(초등)", "관리인원(중등)", "관리인원(고등)", "관리인원"]
CS_COLS = ["초기 상담 횟수", "주간 상담 횟수", "상담횟수"]
USAGE_COLS = {"quota": "정원", "seats": "좌석 수", "reg": "등록 학생 수", "ing": "이용 중 학생 수", "out": "하차 학생 수", "hours": "이용시간", "consult": "상담횟수(누계)",
              "codi": "코디네이터 수", "mgr": "매니저 수", "codiTO": "코디네이터 정원", "adminTO": "행정인력 정원"}
UNIT = {"등원율": "%", "시간달성율": "%"}

def _scripts(html: str) -> str:
    return "\n".join(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S | re.I))

def _literal(js: str, name: str) -> str | None:
    """`const NAME = {...}` / `[...]`의 리터럴 본문을 괄호 짝으로 잘라 낸다(문자열 안 괄호는 무시)."""
    m = re.search(rf"(?:^|[;\n])\s*(?:const|let|var)\s+{re.escape(name)}\s*=\s*([\[{{])", js)
    if not m: return None
    opener = m.group(1); start = m.end() - 1; close = {"[": "]", "{": "}"}[opener]
    depth, i, in_str = 0, start, None
    while i < len(js):
        ch = js[i]
        if in_str:
            if ch == "\\": i += 2; continue
            if ch == in_str: in_str = None
        elif ch in "'\"`": in_str = ch
        elif ch == opener: depth += 1
        elif ch == close:
            depth -= 1
            if depth == 0: return js[start:i + 1]
        i += 1
    return None

def parse(html: str | bytes) -> dict:
    """저장 파일 → {상수 이름: 해석된 값}. 허용 목록 밖은 읽지 않는다."""
    if isinstance(html, bytes): html = html.decode("utf-8", errors="replace")
    js = _scripts(html); out = {}
    for name in ALLOWED:
        lit = _literal(js, name)
        if lit is None: continue
        try: out[name] = json5.loads(lit)
        except Exception as e: out.setdefault("_errors", {})[name] = f"{type(e).__name__}: {str(e)[:120]}"
    if not out: raise ValueError("대시보드 자료(ATT_DATA 등)를 찾지 못했습니다. 브라우저에서 '페이지 저장(웹페이지, 전체)'으로 저장한 HTML인지 확인하세요.")
    return out

def month_end(label: str) -> str | None:
    """'2025.12' / \"'26.1\" / '2026-03' → 'YYYY-MM-DD'(말일)."""
    m = re.match(r"^\s*'?(\d{2,4})[.\-/](\d{1,2})\s*$", str(label or ""))
    if not m: return None
    y, mo = int(m.group(1)), int(m.group(2)); y = y + 2000 if y < 100 else y
    return f"{y:04d}-{mo:02d}-{calendar.monthrange(y, mo)[1]:02d}"

def _date(s: str) -> str | None:
    m = re.search(r"(\d{4})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})", str(s or ""))
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None

def _num(x):
    """숫자로 읽을 수 있으면 float, 아니면 None('-'·''·'N/A'·'1,234'·'61.3%'). 계산하지 않는다."""
    if x is None or isinstance(x, bool): return None
    if isinstance(x, (int, float)): return None if x != x else float(x)
    s = str(x).strip().replace(",", "").rstrip("%").strip()
    try: return float(s)
    except ValueError: return None

def _norm(name: str) -> str: return re.sub(r"[\s·!()（）\-]", "", str(name or "")).lower().replace("ebs", "")

def name_map(data: dict) -> dict[str, str]:
    """표준 센터명 사전: 정규화한 표기 → 명부의 표준 이름(CENTER_REGISTRY). 없으면 들어온 이름 그대로 쓴다."""
    out = {}
    for row in data.get("CENTER_REGISTRY") or []:
        if len(row) < 2: continue
        std = str(row[1]); out[_norm(std)] = std
        for a in (row[5] if len(row) > 5 and isinstance(row[5], list) else []): out[_norm(a)] = std
    return out

def canonical(name: str, nm: dict[str, str]) -> str:
    return nm.get(_norm(name), str(name or "").strip())

def _rows_monthly(block: dict, cols: list[str | None], nm: dict, defs: dict[str, str], include_partial: bool) -> list[dict]:
    months = block.get("months") or []; partial = block.get("partial"); src = block.get("source") or ""; asof = _date(block.get("asof")) or str(block.get("asof") or "")
    rows = []
    for c in block.get("centers") or []:
        center = canonical(c.get("name"), nm)
        for i, label in enumerate(months):
            if label == partial and not include_partial: continue
            vals = (c.get("m") or [None] * len(months))[i] if i < len(c.get("m") or []) else None
            if vals is None: continue
            bd = month_end(label)
            for j, ind in enumerate(cols):
                if ind is None or j >= len(vals) or (num := _num(vals[j])) is None: continue
                rows.append({"indicator": ind, "center": center, "base_date": bd, "value": num, "definition": defs.get(ind) or defs.get("*"),
                             "calc_period": str(label), "extract_date": asof, "source_version": src, "source_file": "통합 대시보드", "source_sheet": ind, "source_row": None})
    return rows

def indicator_rows(data: dict, include_partial: bool = False) -> tuple[list[dict], list[str]]:
    """대시보드 자료 → 지표 데이터 행. (행들, 설명 줄들). 부분 집계 월(partial)은 기본으로 뺀다."""
    nm = name_map(data); rows, notes = [], []
    A = data.get("ATT_DATA")
    if A:
        n = A.get("notes") or {}
        defs = {"등원율": n.get("rateDef") or "출결시스템 제공 등원율", "시간달성율": "달성시간 ÷ 목표시간 × 100 (출결시스템 제공값)", "등원일수": "해당 월 등원일 수(출결시스템)", "등원예정일수": "해당 월 등원 예정일 수(출결시스템)",
                "목표시간": "해당 월 목표 학습시간(시간)", "달성시간": "해당 월 달성 학습시간(시간)"}
        r = _rows_monthly(A, ATT_COLS, nm, defs, include_partial); rows += r
        notes.append(f"월간 출결: {len(A.get('centers') or [])}개소 × {len(A.get('months') or [])}개월 → {len(r)}건" + (f" (부분 집계 {A.get('partial')} 제외)" if A.get("partial") and not include_partial else ""))
    D = data.get("DG_DATA")
    if D:
        kinds = D.get("kinds") or []; n = D.get("notes") or {}
        base = {"진단검사 건수": n.get("def") or "6종 검사 실시 건수 합계", "진단검사 학생 수": "해당 월 검사를 받은 학생 수(시스템 제공값)"}
        months = D.get("months") or []; src = D.get("source") or ""; asof = _date(D.get("asof")) or ""
        cnt = 0
        for c in D.get("centers") or []:
            center = canonical(c.get("name"), nm)
            for i, label in enumerate(months):
                vals = (c.get("m") or [])[i] if i < len(c.get("m") or []) else None
                if not vals: continue
                bd = month_end(label); nums = [_num(v) for v in vals[:len(kinds)]]; per = [v for v in nums if v is not None]
                stu = _num(vals[len(kinds)]) if len(vals) > len(kinds) else None
                recs = ([("진단검사 건수", float(sum(per)), base["진단검사 건수"])] if per else []) + ([("진단검사 학생 수", stu, base["진단검사 학생 수"])] if stu is not None else [])
                recs += [(f"진단검사 건수({k.get('k')} {k.get('label')})", nums[j], "해당 검사 실시 건수") for j, k in enumerate(kinds) if j < len(nums) and nums[j] is not None]
                for ind, v, d in recs:
                    rows.append({"indicator": ind, "center": center, "base_date": bd, "value": v, "definition": d, "calc_period": str(label), "extract_date": asof, "source_version": src, "source_file": "통합 대시보드", "source_sheet": ind, "source_row": None}); cnt += 1
        notes.append(f"진단검사: {len(D.get('centers') or [])}개소 × {len(months)}개월 → {cnt}건")
    M = data.get("MG_DATA")
    if M:
        n = M.get("notes") or {}; d = n.get("def") or "해당 월 출결시스템에 등록되어 관리 중인 학생 수"
        r = _rows_monthly(M, MG_COLS, nm, {"*": d, "관리인원(초등)": d + " · 초등", "관리인원(중등)": d + " · 중등", "관리인원(고등)": d + " · 고등"}, include_partial); rows += r
        notes.append(f"관리인원: {len(M.get('centers') or [])}개소 → {len(r)}건" + (f" (부분 집계 {M.get('partial')} 제외)" if M.get("partial") and not include_partial else ""))
    C = data.get("CS_DATA")
    if C:
        n = C.get("notes") or {}; d = n.get("def") or "해당 월 출결시스템에 입력된 상담 건수"
        r = _rows_monthly(C, CS_COLS, nm, {"*": d}, include_partial); rows += r
        notes.append(f"상담횟수: {len(C.get('centers') or [])}개소 → {len(r)}건")
    U = data.get("USAGE_DATA")
    if U:
        bd = _date(U.get("asof")); cnt = 0
        if bd:
            for c in U.get("centers") or []:
                center = canonical(c.get("name"), nm)
                for k, ind in USAGE_COLS.items():
                    if (num := _num(c.get(k))) is None: continue
                    rows.append({"indicator": ind, "center": center, "base_date": bd, "value": num, "definition": f"이용현황 집계({U.get('asof')}) · {ind}" + (" — 누계" if k in ("reg", "hours", "consult") else ""),
                                 "calc_period": str(U.get("asof")), "extract_date": bd, "source_version": "이용현황 집계", "source_file": "통합 대시보드", "source_sheet": ind, "source_row": None}); cnt += 1
            notes.append(f"이용현황({U.get('asof')} 기준): {len(U.get('centers') or [])}개소 → {cnt}건")
        else: notes.append("이용현황: 기준일(asof)을 읽지 못해 건너뜀")
    return rows, notes

def centers(data: dict) -> list[dict]:
    """센터 명부: CENTER_REGISTRY를 뼈대로 2025 운영(DATA25)·2026 선정(DATA26)·주말운영(WEEKEND_RAW)을 붙인다. 사람 이름·연락처는 넣지 않는다."""
    d25 = {r.get("no"): r for r in (data.get("DATA25") or []) if isinstance(r, dict)}
    d26 = {r.get("no"): r for r in (data.get("DATA26") or []) if isinstance(r, dict)}
    W = data.get("WEEKEND_RAW") or {}; wc = W.get("centers") or {}; off = set(W.get("markedOff") or [])
    out = []
    for row in data.get("CENTER_REGISTRY") or []:
        if len(row) < 2: continue
        cid, name = str(row[0]), str(row[1]); n25 = row[2] if len(row) > 2 else None; n26 = row[3] if len(row) > 3 else None; status = row[4] if len(row) > 4 else None
        a, b = d25.get(n25) or {}, d26.get(n26) or {}
        w = wc.get(cid) or []
        cap = a.get("students")
        if cap is None and b.get("size"):
            m = re.search(r"(\d+)\s*명", str(b["size"])); cap = int(m.group(1)) if m else None
        out.append({"center_id": cid, "name": name, "aliases": list(row[5]) if len(row) > 5 and isinstance(row[5], list) else [],
                    "year25": n25 is not None, "year26": n26 is not None, "status": status,
                    "edu": a.get("edu") or (str(b.get("region") or "").split(" ")[0] if b else None), "region": a.get("region") or b.get("region"),
                    "facility": a.get("facility") or b.get("facility"), "type": a.get("codi_type") or b.get("type"), "size": a.get("size") or b.get("size"),
                    "open_date": _date(a.get("date") or b.get("date")), "capacity": cap,
                    "weekend": (w[0] if w else None) if cid not in off else "미운영(확정표 제외)", "weekend_days": (w[2] if len(w) > 2 else None)})
    return out

def summarize(data: dict, include_partial: bool = False) -> dict:
    rows, notes = indicator_rows(data, include_partial); cs = centers(data)
    by = {}
    for r in rows: by[r["indicator"]] = by.get(r["indicator"], 0) + 1
    dates = sorted({r["base_date"] for r in rows if r["base_date"]})
    return {"rows": rows, "centers": cs, "notes": notes, "by_indicator": by, "dates": dates, "errors": data.get("_errors") or {}}

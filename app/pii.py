"""개인정보 패턴 검출(제출 전 경고용). 코드만 사용. 검출 = 의심이며 최종 판단은 담당자."""
import re
import pandas as pd

PATTERNS = {
    # 한글은 \w에 속해 '010-1234-5678로'처럼 조사가 붙으면 \b가 성립하지 않으므로 숫자 경계(?<!\d)(?!\d)를 쓴다. 구분자는 -, ., 공백 모두.
    "주민등록번호": re.compile(r"(?<!\d)\d{6}\s*[-.]?\s*[1-8]\d{6}(?!\d)"),
    "휴대전화": re.compile(r"(?<!\d)(?:\+82[\s.-]?1[016789]|01[016789])[\s.-]?\d{3,4}[\s.-]?\d{4}(?!\d)"),
    "일반전화": re.compile(r"(?<![\d)])\(?0(?:2|[3-6]\d)\)?[\s.-]?\d{3,4}[\s.-]?\d{4}(?!\d)"),
    "이메일": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    "계좌번호 의심": re.compile(r"\b\d{3,6}-\d{2,6}-\d{2,6}(-\d{1,6})?\b"),
    "학생 식별 의심": re.compile(r"(학생명|학생 이름|생년월일|학번|보호자)"),
}

DATE_LIKE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$")

def scan_text(text: str, where: str = "") -> list[dict]:
    """패턴 우선순위 순으로 검사하고, 이미 잡힌 구간과 겹치는 매치는 버린다. 날짜 형식은 계좌로 보지 않는다."""
    hits, taken = [], []
    for label, pat in PATTERNS.items():
        for m in pat.finditer(text or ""):
            span = m.span()
            if label == "계좌번호 의심" and DATE_LIKE.match(m.group(0)): continue
            if any(a < span[1] and span[0] < b for a, b in taken): continue
            taken.append(span)
            hits.append({"유형": label, "위치": where, "내용": m.group(0)[:40]})
    return hits

def scan_df(df: pd.DataFrame, name: str = "") -> list[dict]:
    """표의 글자 컬럼을 검사. source_row 컬럼이 있으면 그 엑셀 행 번호로 위치를 표시한다."""
    hits = []
    rows = df["source_row"] if "source_row" in df.columns else None
    for col in df.columns:
        if col in ("source_row", "source_sheet", "source_file"): continue
        hits += scan_text(str(col), f"{name} 컬럼명")
        if pd.api.types.is_string_dtype(df[col]) or df[col].dtype == object:
            for i, v in df[col].dropna().astype(str).items():
                rn = int(rows.loc[i]) if rows is not None and i in rows.index and pd.notna(rows.loc[i]) else i + 2
                hits += scan_text(v, f"{name} {col} {rn}행")
    return hits

FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")

def excel_safe(df: pd.DataFrame) -> pd.DataFrame:
    """엑셀로 내보내기 전: 수식으로 해석될 수 있는 글자('=', '+', '-', '@', 탭, CR)로 시작하는 문자열 셀 앞에 작은따옴표를 붙인다.
    외부에서 온 요구서 문구·업로드 셀 값이 그대로 엑셀 수식이 되는 것(수식 주입)을 막는다. 음수 숫자는 숫자형이라 영향 없음."""
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_string_dtype(out[col]) or out[col].dtype == object:   # pandas 3의 'str' dtype도 포함
            out[col] = out[col].astype(object).map(lambda v: ("'" + v) if isinstance(v, str) and v[:1] in FORMULA_LEAD and not v.lstrip("-+").replace(".", "", 1).isdigit() else v)
    cols = [("'" + str(c)) if str(c)[:1] in FORMULA_LEAD else c for c in out.columns]
    out.columns = cols
    return out

def redact_obj(obj):
    """dict/list 안의 모든 문자열에 redact 적용(API로 보내는 도구 결과·입력용)."""
    if isinstance(obj, str): return redact(obj)[0]
    if isinstance(obj, dict): return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)): return [redact_obj(x) for x in obj]
    return obj

MASK = {"주민등록번호": "[주민번호]", "휴대전화": "[전화번호]", "일반전화": "[전화번호]", "이메일": "[이메일]", "계좌번호 의심": "[계좌번호]"}

def redact(text: str) -> tuple[str, int]:
    """외부(API)로 보내기 전 개인정보 패턴을 치환. (치환된 글, 치환 수). 학생 식별어는 단어 자체는 두고 검출만 한다."""
    out, n = text or "", 0
    for label, pat in PATTERNS.items():
        if label not in MASK: continue
        def _sub(m, label=label):
            nonlocal n
            if label == "계좌번호 의심" and DATE_LIKE.match(m.group(0)): return m.group(0)
            n += 1; return MASK[label]
        out = pat.sub(_sub, out)
    return out, n

"""개인정보 패턴 검출(제출 전 경고용). 코드만 사용. 검출 = 의심이며 최종 판단은 담당자."""
import re
import pandas as pd

PATTERNS = {
    "주민등록번호": re.compile(r"\b\d{6}\s*-\s*[1-8]\d{6}\b"),
    "휴대전화": re.compile(r"\b01[016789]\s*-?\s*\d{3,4}\s*-?\s*\d{4}\b"),
    "일반전화": re.compile(r"\b0\d{1,2}\s*-\s*\d{3,4}\s*-\s*\d{4}\b"),
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
    hits = []
    for col in df.columns:
        hits += scan_text(str(col), f"{name} 컬럼명")
        if pd.api.types.is_string_dtype(df[col]) or df[col].dtype == object:
            for i, v in df[col].dropna().astype(str).items():
                hits += scan_text(v, f"{name} {col} {i+2}행")
    return hits

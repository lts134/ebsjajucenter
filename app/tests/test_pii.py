import pandas as pd, pii

def test_detects_common_patterns_without_overlap():
    hits = pii.scan_text("담당 010-1234-5678, 메일 a.b@ebs.co.kr, 주민 900101-1234567, 계좌 110-123-456789")
    types = [h["유형"] for h in hits]
    assert types.count("휴대전화") == 1 and "이메일" in types and "주민등록번호" in types and "계좌번호 의심" in types

def test_dates_are_not_accounts_and_clean_df_passes():
    assert pii.scan_text("2026-06-30 기준") == []
    df = pd.DataFrame({"센터명": ["센터A"], "기준일": ["2026-06-30"], "값": [60.3]})
    assert pii.scan_df(df, "t") == []

def test_student_identifier_words_flagged():
    df = pd.DataFrame({"학생명": ["홍길동"]})
    assert any(h["유형"] == "학생 식별 의심" for h in pii.scan_df(df, "t"))

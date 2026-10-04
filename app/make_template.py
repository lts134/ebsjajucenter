"""샘플 회신 서식 템플릿 생성: 참가서식(한글 저장본) 1쪽의 표 구조를 재활용해 자리표시자를 넣는다.
실제 운영 시에는 부서 회신 서식 HWPX를 한글에서 열어 같은 자리표시자를 직접 입력해 templates/ 에 저장하면 된다."""
import re, zipfile
from pathlib import Path

SRC = Path(__file__).parent.parent / "테스트_HWPX_치환_행추가_v2.hwpx"
DST = Path(__file__).parent / "templates" / "회신서식_샘플.hwpx"

from hwpx_out import LEAF_P, _para_with_text

def set_text(cell, text):
    """셀 첫 문단만 남기고 텍스트 교체(치환 로직은 hwpx_out과 공유)"""
    paras = list(re.finditer(LEAF_P, cell, flags=re.S))
    return cell[:paras[0].start()] + _para_with_text(paras[0].group(0), text) + cell[paras[-1].end():]

def main():
    z = zipfile.ZipFile(SRC)
    xml = z.read("Contents/section0.xml").decode("utf-8")
    # 1) 첫 쪽(서식1)만 남김: 첫 pageBreak 문단 앞까지
    paras = list(re.finditer(r"<hp:p\b[^>]*>.*?</hp:p>", xml, flags=re.S))
    first_pb = next(p for p in paras if 'pageBreak="1"' in re.match(r"<hp:p\b[^>]*>", p.group(0)).group(0))
    head = xml[:first_pb.start()]
    tail = xml[xml.rfind("</hs:sec>"):]
    body = head
    tbls = list(re.finditer(r"<hp:tbl\b.*?</hp:tbl>", body, flags=re.S))
    # 표0 제목
    t0 = set_text(tbls[0].group(0), "자료 제출 회신")
    # 표1 (3x3): 수신/발신/제목
    t1 = tbls[1].group(0)
    trs = list(re.finditer(r"<hp:tr\b.*?</hp:tr>", t1, flags=re.S))
    labels = [("수    신", "{{수신}}", ""), ("발    신", "{{발신}}", ""), ("제    목", "{{제목}}", None)]
    new_trs = []
    for tr, (a, b, c) in zip(trs, labels, strict=False):
        tcs = list(re.finditer(r"<hp:tc\b.*?</hp:tc>", tr.group(0), flags=re.S))
        r = tr.group(0)
        for tc, val in reversed(list(zip(tcs, [a, b, c], strict=False))):
            if val is None: continue
            r = r[:tc.start()] + set_text(tc.group(0), val) + r[tc.end():]
        new_trs.append(r)
    t1 = t1[:trs[0].start()] + "".join(new_trs) + t1[trs[-1].end():]
    # 표2 (6x5): 헤더 + 행 템플릿 1개만
    t2 = tbls[2].group(0)
    trs = list(re.finditer(r"<hp:tr\b.*?</hp:tr>", t2, flags=re.S))
    hdr, row = trs[0].group(0), trs[1].group(0)
    hdr_vals = ["번호", "지표", "센터", "기준일", "값"]
    row_vals = ["{{row.no}}", "{{row.indicator}}", "{{row.center}}", "{{row.base_date}}", "{{row.value}}"]
    def fill_row(r, vals):
        tcs = list(re.finditer(r"<hp:tc\b.*?</hp:tc>", r, flags=re.S))
        for tc, v in reversed(list(zip(tcs, vals, strict=False))):
            r = r[:tc.start()] + set_text(tc.group(0), v) + r[tc.end():]
        return r
    row_h = int(re.search(r'<hp:cellSz width="\d+" height="(\d+)"', row).group(1))
    removed = len(trs) - 2
    t2 = t2[:trs[0].start()] + fill_row(hdr, hdr_vals) + fill_row(row, row_vals) + t2[trs[-1].end():]
    t2 = re.sub(r'rowCnt="\d+"', 'rowCnt="2"', t2, count=1)
    t2 = re.sub(r'(<hp:sz width="\d+" widthRelTo="ABSOLUTE" height=")(\d+)(")', lambda m: f"{m.group(1)}{int(m.group(2)) - row_h * removed}{m.group(3)}", t2, count=1)
    # 표3 (5x2): 요구 항목 / 답변 내용 / 차이 사유 / 산출 근거 / 담당자
    t3 = tbls[3].group(0)
    trs = list(re.finditer(r"<hp:tr\b.*?</hp:tr>", t3, flags=re.S))
    labels = [("요구 항목", "{{요구항목}}"), ("답변 내용", "{{본문}}"), ("차이 사유", "{{차이사유}}"), ("산출 근거", "{{산출근거}}"), ("담 당 자", "{{담당자}}")]
    new_trs = []
    for tr, (a, b) in zip(trs, labels, strict=False):
        tcs = list(re.finditer(r"<hp:tc\b.*?</hp:tc>", tr.group(0), flags=re.S))
        r = tr.group(0)
        for tc, val in reversed(list(zip(tcs, [a, b], strict=False))):
            r = r[:tc.start()] + set_text(tc.group(0), val) + r[tc.end():]
        new_trs.append(r)
    t3 = t3[:trs[0].start()] + "".join(new_trs) + t3[trs[-1].end():]
    # 표 교체(뒤에서부터)
    for m, new in reversed(list(zip(tbls[:4], [t0, t1, t2, t3], strict=False))):
        body = body[:m.start()] + new + body[m.end():]
    # 표 사이 안내 문단 정리: '■ 참가 부문' 등 → 제목 문구
    body = body.replace("■ 참가 부문", "■ 문서 정보").replace("■ 참가자 정보", "■ 제출 수치").replace("■ 참가 내용", "■ 답변")
    body = re.sub(r"<hp:p\b[^>]*>(?:(?!</hp:p>).)*?※ 개인 참가의 경우.*?</hp:p>", "", body, flags=re.S)
    xml2 = body + tail
    DST.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(DST, "w") as out:
        out.writestr(zipfile.ZipInfo("mimetype"), z.read("mimetype"), compress_type=zipfile.ZIP_STORED)
        for n in z.namelist():
            if n == "mimetype": continue
            out.writestr(n, xml2.encode("utf-8") if n == "Contents/section0.xml" else z.read(n), compress_type=zipfile.ZIP_DEFLATED)
    print("template →", DST)

if __name__ == "__main__":
    main()

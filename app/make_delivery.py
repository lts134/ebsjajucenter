"""과제 제출 ZIP 만들기(요강 V-2): ① 서식5 보고서 PDF ② 시연 영상 MP4 ③ 프로토타입(소스 ZIP + 접속 안내)을 한 ZIP으로.
사용: python make_delivery.py  → ../dist/과제제출_트랙1_EBS대외요구자료대응에이전트_이택수.zip
사전: python make_submission_zip.py(소스 ZIP), node docs/build_report.js + PDF 저장, python docs/record_demo.py(영상)."""
import zipfile, shutil, datetime as dt
from pathlib import Path

APP = Path(__file__).parent; DIST = APP.parent / "dist"; DOCS = APP / "docs"
NAME = "과제제출_트랙1_EBS대외요구자료대응에이전트_이택수"
URL = "https://ebs-request-agent-659902904913.asia-northeast3.run.app"

GUIDE = f"""EBS 대외 요구자료 대응 에이전트 — 프로토타입 접속·실행 안내 (트랙1)

1) 웹 접속(권장)
   URL: {URL}
   접속 비밀번호: 제출 메일 본문 참조
   처음 접속은 10~20초 걸릴 수 있습니다(요청이 있을 때만 서버가 켜지는 구조).
   확인 순서: 설정 → 시연·초기화 → '시연 데이터 넣기' → 홈 → 새 요구서 처리 1·2·3단계 → 검토·승인 → 이력 조회 → 현황 → 이력에 묻기.
   시연용 요구서·집계 파일 선택칸은 '설정'에서 시연 모드가 켜져 있을 때 보입니다. 데이터는 전부 가상(센터A~L)입니다.
   AI 경로를 보려면 설정 → AI 연결에 Claude API 키를 넣고 '적용' → '연결 테스트'. 키는 브라우저 세션에만 보관되며 서버에 저장되지 않습니다.
   키가 없으면 같은 흐름이 규칙 기반으로 동작합니다(화면 상단에 'AI가 연결되지 않았습니다' 안내가 뜸).

2) PC에서 실행(소스 ZIP)
   압축을 풀고 app 폴더의 run_demo.bat 실행(Python 3.11 이상). 필요한 모듈이 자동 설치되고 브라우저가 열립니다.
   여러 사람이 같은 망에서 쓰려면 run_server.bat. 클라우드에 올리는 방법은 docs/배포_클라우드.md.

3) 소스 구성
   app/*.py(화면·AI·대조·문서 모듈), tests/(pytest 85건), sample_data/(가상 샘플), templates/(회신 HWPX 서식), docs/(운영 매뉴얼·시연 시나리오·실측 기록·제출 설명서·발표자료).
   전체 점검: python run_checks.py (ruff → pytest → 추출 품질 → 화면 흐름).

문의: 지역교육협력부 이택수
"""

def main():
    DIST.mkdir(exist_ok=True)
    src = sorted(DIST.glob("EBS_요구자료대응에이전트_*.zip"))
    pdf = DOCS / "서식5_프로젝트개발보고서_EBS요구자료대응에이전트.pdf"; docx = pdf.with_suffix(".docx")
    mp4 = DOCS / "시연영상_EBS_요구자료대응에이전트.mp4"
    missing = [str(p) for p, ok in ((pdf, pdf.exists()), (mp4, mp4.exists()), ("소스 ZIP(make_submission_zip.py)", bool(src))) if not ok]
    if missing: raise SystemExit("없는 파일: " + ", ".join(missing))
    stage = DIST / NAME; shutil.rmtree(stage, ignore_errors=True)
    (stage / "01_보고서").mkdir(parents=True); (stage / "02_시연영상").mkdir(); (stage / "03_프로토타입").mkdir()
    shutil.copy(pdf, stage / "01_보고서" / pdf.name)
    if docx.exists(): shutil.copy(docx, stage / "01_보고서" / docx.name)
    shutil.copy(mp4, stage / "02_시연영상" / mp4.name)
    shutil.copy(src[-1], stage / "03_프로토타입" / src[-1].name)
    (stage / "03_프로토타입" / "접속_안내.txt").write_text(GUIDE, encoding="utf-8")
    shutil.copy(DOCS / "제출_체크리스트.md", stage / "제출_체크리스트.md")
    out = DIST / f"{NAME}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(stage.rglob("*")):
            if p.is_file(): z.write(p, Path(NAME) / p.relative_to(stage))
    print(f"{out} ({out.stat().st_size / 1e6:.1f} MB) — 소스 {src[-1].name}, 보고서 PDF, 영상 포함. 폴더본: {stage}")
    print(f"만든 날짜 {dt.date.today()} · 메일 제목: [과제제출_트랙1] EBS 대외 요구자료 대응 에이전트_이택수")

if __name__ == "__main__":
    main()

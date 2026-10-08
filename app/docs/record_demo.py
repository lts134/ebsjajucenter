"""작동 시연 영상(3분 이내, MP4) 녹화: 시연 모드 앱을 띄우고 화면 흐름을 자동으로 돌리며 자막 띠를 얹어 녹화한다.
사용(앱 폴더에서):  python docs/record_demo.py           → docs/시연영상_EBS_요구자료대응에이전트.mp4
   - ANTHROPIC_API_KEY 환경변수가 있으면 Claude 경로로(권장), 없으면 규칙 기반 경로로 녹화되며 첫 자막에 그 사실을 적는다.
   - 필요: pip install playwright && playwright install chromium (최초 1회), ffmpeg(PATH에 있어야 webm→mp4 변환. 없으면 webm만 남김)
   - 한글 글꼴이 있는 PC에서 실행할 것. 녹화 크기 1440×900, 길이 약 2분 45초.
흐름은 docs/시연_시나리오.md의 순서를 따른다. 자막 문구만 바꾸려면 아래 CAP 상수를 고치면 된다."""
import os, sys, subprocess, time, shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_screens import settle, nav, click, pick, pick_multi, APP, EXE   # 앱 띄우기·화면 조작 도우미 재사용
from playwright.sync_api import sync_playwright

PORT = os.environ.get("RECORD_PORT", "8523"); DBF = APP / "storage" / "record.db"
OUT = APP / "docs" / "시연영상_EBS_요구자료대응에이전트.mp4"
HAS_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))
W, H = 1440, 900

CAP_JS = """(t) => { let el = document.getElementById('demo-cap');
 if (!el) { el = document.createElement('div'); el.id = 'demo-cap';
   el.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:999999;background:rgba(30,33,36,.93);color:#fff;'
     + 'font:600 25px/1.45 Pretendard,"Malgun Gothic","Apple SD Gothic Neo","NanumBarunGothic","Noto Sans KR",sans-serif;padding:18px 32px;letter-spacing:-.01em;';
   document.body.appendChild(el); }
 el.textContent = t; el.style.display = t ? 'block' : 'none'; }"""
CARD_JS = """(a) => { let el = document.getElementById('demo-card');
 if (!el) { el = document.createElement('div'); el.id = 'demo-card';
   el.style.cssText = 'position:fixed;inset:0;z-index:1000000;background:#1E2124;color:#fff;display:flex;flex-direction:column;justify-content:center;'
     + 'padding:0 140px;font-family:Pretendard,"Malgun Gothic","Apple SD Gothic Neo","NanumBarunGothic","Noto Sans KR",sans-serif;letter-spacing:-.01em;';
   document.body.appendChild(el); }
 if (!a) { el.style.display = 'none'; return; }
 el.style.display = 'flex';
 el.innerHTML = '<div style="font-size:22px;color:#9DB8F0;font-weight:600;margin-bottom:18px">' + a[0] + '</div>'
   + '<div style="font-size:50px;font-weight:700;line-height:1.25;margin-bottom:26px">' + a[1] + '</div>'
   + '<div style="font-size:26px;line-height:1.5;color:#D9DEE5;white-space:pre-line">' + a[2] + '</div>'; }"""

def cap(page, text, hold_ms=0):
    page.evaluate(CAP_JS, text)
    if hold_ms: page.wait_for_timeout(hold_ms)

def card(page, eyebrow, title, body, hold_ms):
    page.evaluate(CARD_JS, [eyebrow, title, body]); page.wait_for_timeout(hold_ms); page.evaluate(CARD_JS, None)

def scroll(page, px, steps=8, pause=120):
    for _ in range(steps): page.mouse.wheel(0, px / steps); page.wait_for_timeout(pause)

def main():
    DBF.unlink(missing_ok=True)
    env = dict(os.environ, HISTORY_DB=str(DBF), APP_DEMO="1", APP_TODAY=os.environ.get("APP_TODAY", "2026-10-07"))
    proc = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "app.py", "--server.headless", "true", "--server.port", PORT, "--client.toolbarMode", "minimal"],
                            cwd=APP, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    vdir = APP / "storage" / "record_video"; shutil.rmtree(vdir, ignore_errors=True)
    t0 = None
    try:
        time.sleep(6)
        with sync_playwright() as pw:
            b = pw.chromium.launch(**({"executable_path": EXE} if EXE else {}), args=["--lang=ko-KR"])
            # 1) 녹화하지 않는 준비: 시연 데이터 넣기(7월 의원실 답변이 기록에 있는 상태로 시작)
            prep = b.new_page(viewport={"width": W, "height": H}, locale="ko-KR")
            prep.goto(f"http://localhost:{PORT}"); settle(prep, 3000)
            nav(prep, "설정"); prep.get_by_role("tab", name="백업·초기화").click(); settle(prep); click(prep, "시연 데이터 넣기"); prep.close()
            # 2) 녹화 시작
            ctx = b.new_context(viewport={"width": W, "height": H}, locale="ko-KR", record_video_dir=str(vdir), record_video_size={"width": W, "height": H})
            page = ctx.new_page(); t0 = time.time()
            page.goto(f"http://localhost:{PORT}"); settle(page, 2500)
            path_note = "Claude API 연결 상태로 녹화" if HAS_KEY else "AI 키 없이 규칙 기반 경로로 녹화 — 키를 넣으면 같은 화면에서 AI 경로가 동작"
            card(page, "2026 EBS AI Innovation Challenge · 트랙1 Now Agent", "EBS 대외 요구자료 대응 에이전트",
                 "국감·감사·교육부 요구자료에 \"언제, 누구에게, 어떤 근거로 답했는지\"를 기억하고\n다음 답변의 수치와 문서를 제출 전에 검증합니다.\n" + path_note, 7000)
            # 홈
            cap(page, "홈 — 담당자가 하는 일은 셋입니다. 새 요구서 처리, 과거 답변 등록, 기록에 묻기. 지난 7월 의원실에 낸 답변 1건이 이미 기록에 있습니다.", 5000)
            # 과거 답변 등록
            nav(page, "과거 답변 등록")
            cap(page, "과거 답변 등록 — 집계 엑셀이 없어도 됩니다. 실적표·회신 공문(HWP·HWPX·DOCX·PDF)을 종류 구분 없이 한 번에 올리면 표를 그대로 읽어 합칩니다.", 2500)
            pick(page, "시연 요구서", "요구서_02"); click(page, "요구 항목 읽기", 2000)
            pick_multi(page, "시연 파일", "실적표_가로형")
            cap(page, "제목 행·병합 머리글·합계 행이 있는 실적표도 센터 열·값 열·기준일만 확인하면 48건의 값으로 저장됩니다. 값은 옮기기만 하고 계산하지 않습니다.", 1000)
            scroll(page, 700); page.wait_for_timeout(4500)
            # 1단계
            nav(page, "새 요구서 처리")
            cap(page, "1단계 요구서 읽기 — 새 요구서를 올리면 항목·지표·기준일을 뽑고, 같은 수치를 전에 낸 적이 있는지 기록에서 찾습니다.", 1500)
            pick(page, "시연 요구서", "새요구서_의원실"); click(page, "요구 항목 읽기", 2500)
            cap(page, "비슷한 과거 요구서가 뜨고, '6/30 기준 등원율' 항목에는 7월에 의원실에 낸 값 12건과 산출 근거가 그대로 붙습니다. 담당자가 바뀌어도 이력이 끊기지 않습니다.", 1000)
            scroll(page, 900); page.wait_for_timeout(5500)
            click(page, "등록하고 2단계로", 2500)
            # 2단계
            cap(page, "2단계 수치 맞춰 보기 — 이번에 낼 집계값을 지난번 제출값과 같은 지표·센터·기준일끼리 맞춰 봅니다. 이 계산은 코드만 합니다.", 1500)
            scroll(page, 500); page.wait_for_timeout(3000)
            cap(page, "짝 12건 중 차이 3건(센터C·G·K). 차이가 난 행에는 '추출시점·원자료 버전이 바뀜' 같은 단서가 붙습니다.", 4500)
            click(page, "문구 후보 받기", 2500)
            cap(page, "차이 사유는 AI가 정하지 않습니다. 대조 단서와 과거에 우리가 쓴 사유를 근거로 문구 후보만 내고, 담당자가 고릅니다.", 1500)
            for btn in page.get_by_role("button", name="적용", exact=True).all()[:1]: btn.click(); settle(page)
            for ti in page.get_by_role("textbox").all():
                try:
                    ph = ti.get_attribute("placeholder") or ""
                    if "출결 사후 보정" in ph and not ti.input_value(): ti.fill("출결 사후 보정 반영(9/10 재산출)"); ti.press("Enter"); settle(page, 500)
                except Exception: pass
            page.wait_for_timeout(4000)
            click(page, "확정하고 3단계로", 2500)
            # 3단계
            cap(page, "3단계 회신 초안 — 확정된 수치와 담당자가 적은 사유만으로 공문체 초안을 씁니다. 자료가 없는 항목은 지어내지 않고 [확인 필요]로 남깁니다.", 1500)
            click(page, "초안 만들기", 3000); scroll(page, 500); page.wait_for_timeout(5000)
            cap(page, "예상 질문 — 이 회신을 받은 쪽이 다음에 무엇을 물을지, 준비할 자료는 무엇인지 미리 봅니다. 수치 해석은 하지 않습니다.", 1000)
            click(page, "예상 질문 보기", 3000); scroll(page, 700); page.wait_for_timeout(4500)
            cap(page, "회신은 부서 한글 서식(HWPX)으로 받고, 점검표·확정 수치·근거를 묶은 제출 묶음 ZIP과 함께 팀장 검토를 요청합니다.", 1000)
            scroll(page, 600); page.wait_for_timeout(3000)
            click(page, "팀장 검토 요청", 2000)
            # 검토·승인
            nav(page, "검토·승인")
            cap(page, "검토·승인 — 팀장이 수치·사유·초안을 보고 승인하거나 반려합니다. 누가 언제 승인했는지 기록에 남습니다.", 2500)
            page.get_by_label("검토 의견").first.fill("수치·사유 확인함. 제출 승인"); page.keyboard.press("Enter"); settle(page, 500); click(page, "승인", 3000)
            # 기록 조회
            nav(page, "기록 조회"); page.get_by_test_id("stExpander").first.locator("summary").click(); settle(page)
            cap(page, "기록 조회 — 요구 → 항목 → 제출값 → 차이 사유 → 초안 → 승인이 한 줄로 이어집니다.", 1000)
            scroll(page, 500); page.wait_for_timeout(5000)
            # 현황
            nav(page, "현황")
            cap(page, "현황 — 기한(D-day)과 상태, 요청 주체별 건수, 반복해서 요구된 지표. 반복 지표는 미리 산출해 두는 표준 답변 후보입니다.", 5000)
            # 기록에 묻기
            nav(page, "기록에 묻기")
            cap(page, "기록에 묻기 — \"감사실에 등원율 어떻게 냈지?\" 화면을 돌아다니지 않아도 기록에서 찾아 요구번호·제출일·값을 근거와 함께 답합니다.", 1500)
            page.get_by_role("textbox", name="질문").fill("감사실에 등원율 어떻게 냈지?"); page.keyboard.press("Enter"); settle(page, 500)
            click(page, "물어보기", 3000); page.wait_for_timeout(4500)
            # 설정
            nav(page, "설정")
            cap(page, "설정 — 접속자마다 자기 API 키를 세션에만 넣습니다(서버 저장 없음). 호출마다 모델·시간·비용이 기록됩니다. 키가 없으면 규칙 기반으로 같은 흐름이 돕니다.", 5000)
            card(page, "정리", "AI는 읽고·쓰고·점검하고·기억합니다. 수치 계산은 코드가, 사유와 승인은 사람이 합니다.",
                 "· 요구서 구조화 → 과거 제출 이력 자동 연결 → 수치 대조(코드) → 사유 후보(근거 있는 것만) → 공문체 초안·HWPX\n"
                 "· Cloud Run에 올려 부서 전원이 같은 기록을 공유 · 이력은 Cloud Storage에 실시간 복제\n"
                 "· 데이터는 전부 가상(센터A~L) · 과거 회신 HWP 실파일 표 추출 확인 완료", 7000)
            dur = time.time() - t0
            ctx.close(); b.close()
        webm = next(vdir.glob("*.webm"))
        if shutil.which("ffmpeg"):
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(webm), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", "-crf", "23", "-movflags", "+faststart", "-an", str(OUT)], check=True)
            print(f"DONE {OUT} ({OUT.stat().st_size / 1e6:.1f} MB), 길이 약 {dur:.0f}초")
        else:
            dst = OUT.with_suffix(".webm"); shutil.copy(webm, dst); print(f"ffmpeg 없음 → {dst} (길이 약 {dur:.0f}초). ffmpeg로 mp4 변환 필요")
    finally:
        proc.terminate(); DBF.unlink(missing_ok=True)
        for f in APP.glob("storage/record.db*"): f.unlink(missing_ok=True)
        shutil.rmtree(vdir, ignore_errors=True)

if __name__ == "__main__":
    main()

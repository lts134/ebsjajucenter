"""시연 모드(APP_DEMO=1) 앱을 브라우저로 돌리며 화면을 docs/screens/에 캡처하고, 발표용 자른 그림을 docs/img/에 만든다.
사용(앱 폴더에서):  python docs/capture_screens.py           ← 앱을 직접 띄우고(포트 8521) 찍은 뒤 끈다. 빈 임시 DB 사용.
   Claude 경로로 찍으려면 ANTHROPIC_API_KEY 환경변수를 두고 실행. 한글 폰트가 있는 PC에서 실행할 것.
   playwright와 크로미움이 필요: pip install playwright && playwright install chromium (최초 1회)"""
import os, sys, subprocess, time
from pathlib import Path
from playwright.sync_api import sync_playwright
from PIL import Image

APP = Path(__file__).resolve().parents[1]; OUT = APP / "docs" / "screens"; IMG = APP / "docs" / "img"
OUT.mkdir(parents=True, exist_ok=True); IMG.mkdir(parents=True, exist_ok=True)
PORT = os.environ.get("CAPTURE_PORT", "8521"); DBF = APP / "storage" / "capture.db"
EXE = os.environ.get("CHROMIUM_PATH") or ("/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None)
SIDEBAR_W = 300

def settle(page, ms=1200):
    try: page.wait_for_selector('[data-testid="stStatusWidget"]', state="detached", timeout=120000)
    except Exception: pass
    page.wait_for_timeout(500)
    try: page.wait_for_selector('[data-testid="stSpinner"]', state="detached", timeout=180000)
    except Exception: pass
    page.wait_for_timeout(ms)

def nav(page, label):
    page.get_by_test_id("stSidebar").get_by_text(label, exact=True).first.click(); settle(page)

def click(page, name, ms=2000):
    page.get_by_role("button", name=name, exact=False).first.click(); settle(page, ms)

def pick(page, label_text, option_text):
    """selectbox: 라벨 글자로 찾아 열고 옵션 선택"""
    box = page.locator('[data-testid="stSelectbox"]').filter(has_text=label_text).first
    box.locator("input").first.click(); page.wait_for_timeout(500)
    page.get_by_role("option").filter(has_text=option_text).first.click(); settle(page, 2500)

def pick_multi(page, label_text, option_text):
    """multiselect: 라벨 글자로 찾아 열고 옵션 선택"""
    box = page.locator('[data-testid="stMultiSelect"]').filter(has_text=label_text).first
    box.locator("input").first.click(); page.wait_for_timeout(500)
    page.get_by_role("option").filter(has_text=option_text).first.click(); page.keyboard.press("Escape"); settle(page, 2500)

def shot(page, fname, crop_h=None):
    page.set_viewport_size({"width": 1440, "height": 900}); page.wait_for_timeout(600)
    h, stable = 900, 0
    for _ in range(30):
        page.wait_for_timeout(400)
        h2 = page.evaluate("document.querySelector('[data-testid=\"stMainBlockContainer\"]').getBoundingClientRect().height")
        stable = stable + 1 if abs(h2 - h) < 2 else 0; h = h2
        if stable >= 3: break
    page.set_viewport_size({"width": 1440, "height": int(min(max(h + 60, 900), 3400))}); page.wait_for_timeout(800)
    page.screenshot(path=str(OUT / fname), full_page=True)
    im = Image.open(OUT / fname); w, hh = im.size
    main = im.crop((SIDEBAR_W, 0, w, hh if crop_h is None else min(hh, crop_h)))
    main.save(IMG / fname); print("shot", fname, im.size, "->", main.size)

def main():
    DBF.unlink(missing_ok=True)
    env = dict(os.environ, HISTORY_DB=str(DBF), APP_DEMO="1", APP_TODAY=os.environ.get("APP_TODAY", "2026-10-04"))
    proc = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "app.py", "--server.headless", "true", "--server.port", PORT, "--client.toolbarMode", "minimal"],
                            cwd=APP, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(6)
        with sync_playwright() as pw:
            b = pw.chromium.launch(**({"executable_path": EXE} if EXE else {}), args=["--lang=ko-KR"])
            page = b.new_page(viewport={"width": 1440, "height": 900}, locale="ko-KR")
            page.goto(f"http://localhost:{PORT}"); settle(page, 3000)
            # 설정 → 시연 데이터
            nav(page, "설정"); page.get_by_role("tab", name="시연·초기화").click(); settle(page); click(page, "시연 데이터 넣기")
            page.get_by_role("tab", name="AI 연결").click(); settle(page); shot(page, "09_설정.png", 1500)
            # 홈
            nav(page, "홈"); shot(page, "00_홈.png", 1500)
            # 과거 답변 등록: 요구서 샘플 + 가로형 실적표(열 확인)
            nav(page, "과거 답변 등록")
            pick(page, "시연 요구서", "요구서_02"); click(page, "요구 항목 읽기", 3000)
            pick_multi(page, "시연 파일", "실적표_가로형"); shot(page, "01_과거자료등록.png")
            im = Image.open(OUT / "01_과거자료등록.png"); w, hh = im.size
            im.crop((int(w * 0.52), 420, w - 24, min(hh, 420 + 980))).save(IMG / "01b_열매핑.png")
            # 새 요구서 처리 1단계
            nav(page, "새 요구서 처리")
            pick(page, "시연 요구서", "새요구서_의원실"); click(page, "요구 항목 읽기", 3000); shot(page, "02_새요구서분석.png")
            click(page, "등록하고 2단계로", 3000)
            # 2단계
            click(page, "문구 후보 받기", 3000)
            for btn in page.get_by_role("button", name="적용", exact=True).all()[:1]: btn.click(); settle(page)
            for ti in page.get_by_role("textbox").all():
                try:
                    ph = ti.get_attribute("placeholder") or ""
                    if "출결 사후 보정" in ph and not ti.input_value(): ti.fill("출결 사후 보정 반영(9/10 재산출)"); ti.press("Enter"); settle(page, 600)
                except Exception: pass
            shot(page, "03_수치대조_점검표.png")
            click(page, "확정하고 3단계로", 3000)
            # 3단계
            click(page, "초안 만들기", 3000); click(page, "예상 질문 보기", 4000); shot(page, "04_회신초안_HWPX.png")
            click(page, "팀장 검토 요청", 2500)
            # 검토·승인 → 승인
            nav(page, "검토·승인"); shot(page, "05_검토승인.png")
            page.get_by_label("검토 의견").first.fill("수치·사유 확인함. 제출 승인"); page.keyboard.press("Enter"); settle(page, 600); click(page, "승인")
            nav(page, "이력 조회"); page.get_by_test_id("stExpander").first.locator("summary").click(); settle(page); shot(page, "06_이력조회.png")
            nav(page, "현황"); shot(page, "07_현황통계.png")
            nav(page, "이력에 묻기"); page.get_by_role("textbox", name="질문").fill("감사실에 등원율 어떻게 냈지?"); page.keyboard.press("Enter"); settle(page, 600)
            click(page, "물어보기", 3000); shot(page, "08_이력에묻기.png")
            b.close()
    finally:
        proc.terminate(); DBF.unlink(missing_ok=True)
        for f in APP.glob("storage/capture.db*"): f.unlink(missing_ok=True)
    print("DONE")

if __name__ == "__main__":
    main()

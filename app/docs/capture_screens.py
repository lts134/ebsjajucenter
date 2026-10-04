"""빈 DB에서 시연 순서(①→⑦→설정)를 브라우저로 그대로 진행하며 8개 화면을 docs/screens/에 캡처한다.
사용(앱 폴더에서, 터미널 2개):
  1) set HISTORY_DB=storage\shots.db  (빈 파일로 시작; 기존 이력 DB를 건드리지 않음)
     set ANTHROPIC_API_KEY=...        (Claude 경로로 찍으려면. 없으면 규칙 경로)
     python -m streamlit run app.py --server.headless true --server.port 8501 --client.toolbarMode minimal
  2) pip install playwright && playwright install chromium   (최초 1회)
     python docs\capture_screens.py 8501
한글 폰트가 있는 PC에서 실행할 것. 2026-10-04 캡처본은 규칙 경로(API 키 없음)."""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
PORT = sys.argv[1] if len(sys.argv) > 1 else "8501"
OUT = Path(__file__).parent / "screens"; OUT.mkdir(parents=True, exist_ok=True)
for f in OUT.glob("*.png"): f.unlink()

def settle(page, ms=1500):
    try: page.wait_for_selector('[data-testid="stStatusWidget"]', state="detached", timeout=30000)
    except Exception: pass
    page.wait_for_timeout(ms)
def goto(page, label): page.get_by_test_id("stSidebar").get_by_text(label, exact=True).click(); settle(page)
def click(page, name): page.get_by_role("button", name=name, exact=True).first.click(); settle(page, 2500)
def shot(page, fname, cap=3400):
    page.set_viewport_size({"width": 1440, "height": 900}); page.wait_for_timeout(500)
    h = page.evaluate("document.querySelector('[data-testid=\"stMainBlockContainer\"]').getBoundingClientRect().height")
    page.set_viewport_size({"width": 1440, "height": min(max(900, int(h) + 24), cap)}); page.wait_for_timeout(800)
    page.screenshot(path=str(OUT / fname)); print("saved", fname, "h=", int(h))

with sync_playwright() as p:
    b = p.chromium.launch(args=["--lang=ko-KR"])
    page = b.new_context(viewport={"width": 1440, "height": 900}, locale="ko-KR").new_page()
    page.goto(f"http://localhost:{PORT}", wait_until="networkidle"); settle(page, 3000)
    # ① 등록: 요구서_01 + 7월 제출본 → 추출 → 캡처 → 저장
    goto(page, "① 과거 자료 등록"); click(page, "요구 항목 추출"); shot(page, "01_과거자료등록.png")
    click(page, "이력에 저장 (요구서 + 제출본 확정)")
    # ② 분석: 과거 이력 1건만 있는 상태에서 캡처 → 등록
    goto(page, "② 새 요구서 분석"); click(page, "분석"); shot(page, "02_새요구서분석.png")
    click(page, "이 요구서를 이력에 등록(진행 중)")
    # ③ 대조: 사유 3건 입력 → 캡처 → 확정 (대상 요구서 기본값 = 최신 #2)
    goto(page, "③ 수치 대조·점검표")
    for c in ("센터C", "센터G", "센터K"):
        box = page.get_by_label(f"{c} · 등원율 · 2026-06-30", exact=False).first
        box.fill("출결 사후 보정 반영(9/10 재산출, 원자료 v2)"); box.press("Enter"); settle(page, 800)
    shot(page, "03_수치대조_점검표.png")
    click(page, "사유 저장 + 새 제출본 확정 → ④로")
    # ④ 초안 → 캡처 → 검토 요청
    goto(page, "④ 회신 초안·HWPX"); click(page, "초안 생성"); shot(page, "04_회신초안_HWPX.png")
    click(page, "초안 저장 + 팀장 검토 요청")
    # ⑤ 검토 대기 1건(자동 펼침) → 캡처 → 승인
    goto(page, "⑤ 검토·승인"); shot(page, "05_검토승인.png")
    page.get_by_label("검토 의견").first.fill("수치·사유 확인함. 제출 승인"); page.keyboard.press("Enter"); settle(page, 800)
    click(page, "승인")
    # ⑥ 이력(첫 요구서 펼침) ⑦ 현황 설정
    goto(page, "⑥ 이력 조회"); page.get_by_test_id("stExpander").first.locator("summary").click(); settle(page); shot(page, "06_이력조회.png")
    goto(page, "⑦ 현황·통계"); shot(page, "07_현황통계.png")
    goto(page, "설정"); shot(page, "08_설정.png")
    b.close()
print("DONE")

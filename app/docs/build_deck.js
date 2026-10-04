// EBS 대외 요구자료 대응 에이전트 — 발표자료 생성 (pptxgenjs, 구조화 덱)
// 사용: npm install pptxgenjs → node docs/build_deck.js docs/발표자료_EBS_요구자료대응에이전트.pptx
// 이미지: docs/screens/의 캡처를 사이드바 제외로 잘라 docs/img/에 둔 것(아래 crops 참고). 테마 적용(apply_theme.js)이 없으면 색상만 기본값으로 저장된다.
const pptxgen = require("pptxgenjs");
const path = require("path");
let applyTheme = async () => console.log("(apply_theme.js 없음 — 테마 색상은 PowerPoint 기본값으로 저장됨)");
try { ({ applyTheme } = require(process.env.APPLY_THEME_JS || "./apply_theme.js")); } catch (e) { /* 선택 사항 */ }

const IMG = path.join(__dirname, "img");
const OUT = process.argv[2] || path.join(__dirname, "deck.pptx");

const THEME = {
  name: "EBS Records Teal",
  headFontFace: "맑은 고딕", bodyFontFace: "맑은 고딕",
  colors: {
    dk1: "1B2A41", lt1: "FFFFFF", dk2: "0F5C6E", lt2: "EEF3F4",
    accent1: "0F5C6E", accent2: "E8A33D", accent3: "2E7D5B", accent4: "C0392B", accent5: "6B7A8F", accent6: "D5E6EA",
    hlink: "0F5C6E", folHlink: "6B7A8F",
  },
};
const H = THEME.colors;

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.33 x 7.5
pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
pres.title = "EBS 대외 요구자료 대응 에이전트";
pres.author = "EBS 지역교육협력부";
const C = pres.SchemeColor;
const W = 13.33, SH = 7.5;

// ---------- 레이아웃 ----------
pres.defineSlideMaster({
  title: "TITLE_DARK", background: { color: H.dk1 },
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.8, y: 2.3, w: 11.7, h: 1.4, fontSize: 40, bold: true, color: C.background1, align: "left", margin: 0 }, text: "" } },
    { placeholder: { options: { name: "body", type: "body", x: 0.8, y: 3.8, w: 11.7, h: 1.0, fontSize: 20, color: C.accent6, align: "left", margin: 0 }, text: "" } },
    { text: { text: "EBS 지역교육협력부 · 프로토타입(데이터 전부 가상) · 2026-10", options: { x: 0.8, y: 6.7, w: 10, h: 0.4, fontSize: 11, color: C.accent6, margin: 0 } } },
  ],
});
pres.defineSlideMaster({
  title: "CONTENT", background: { color: H.lt1 },
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.6, y: 0.4, w: 12.1, h: 0.8, fontSize: 28, bold: true, color: C.text1, align: "left", margin: 0 }, text: "" } },
    { text: { text: "EBS 대외 요구자료 대응 에이전트 · 프로토타입(가상 데이터)", options: { x: 0.6, y: 7.0, w: 9, h: 0.3, fontSize: 9, color: C.accent5, margin: 0 } } },
  ],
  slideNumber: { x: 12.3, y: 7.0, w: 0.6, h: 0.3, fontSize: 9, color: H.accent5 },
});
pres.defineSlideMaster({
  title: "SECTION", background: { color: H.dk2 },
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.8, y: 2.2, w: 11.7, h: 2.0, fontSize: 34, bold: true, color: C.background1, align: "left", valign: "bottom", margin: 0 }, text: "" } },
    { placeholder: { options: { name: "body", type: "body", x: 0.8, y: 4.4, w: 11.7, h: 0.8, fontSize: 18, color: C.accent6, align: "left", margin: 0 }, text: "" } },
  ],
});

// ---------- 헬퍼 ----------
function circleNum(slide, x, y, label, d = 0.55, fill = C.accent1) {
  slide.addShape(pres.ShapeType.ellipse, { x, y, w: d, h: d, fill: { color: fill }, line: { color: fill }, objectName: `circle-${label}` });
  slide.addText(label, { x, y, w: d, h: d, fontSize: d >= 0.55 ? 16 : 12, bold: true, color: C.background1, align: "center", valign: "middle", margin: 0, isTextBox: true, objectName: `circle-label-${label}` });
}
function card(slide, x, y, w, h, title, body, opts = {}) {
  slide.addShape(pres.ShapeType.roundRect, { x, y, w, h, rectRadius: 0.12, fill: { color: opts.fill || C.background2 }, line: { color: opts.fill || C.background2 }, objectName: `card-${title}` });
  let ty = y + 0.2;
  if (opts.num !== undefined) { circleNum(slide, x + 0.2, y + 0.2, String(opts.num), 0.45); }
  slide.addText(title, { x: x + (opts.num !== undefined ? 0.75 : 0.25), y: ty, w: w - (opts.num !== undefined ? 0.95 : 0.45), h: 0.45, fontSize: opts.titleSize || 15, bold: true, color: opts.titleColor || C.text2, margin: 0, isTextBox: true, valign: "middle", objectName: `card-title-${title}` });
  if (body) slide.addText(body, { x: x + 0.25, y: y + 0.75, w: w - 0.5, h: h - 0.95, fontSize: opts.bodySize || 12, color: C.text1, margin: 0, isTextBox: true, valign: "top", paraSpaceAfter: 4, objectName: `card-body-${title}` });
}
function bullets(slide, items, x, y, w, h, size = 14) {
  slide.addText(items.map((t, i) => ({ text: t, options: { bullet: true, breakLine: i < items.length - 1 } })),
    { x, y, w, h, fontSize: size, color: C.text1, margin: 0, isTextBox: true, valign: "top", paraSpaceAfter: 8, objectName: "bullets" });
}
function stat(slide, x, y, w, big, label, color = C.accent1, size = 40) {
  slide.addText(big, { x, y, w, h: 0.9, fontSize: size, bold: true, color, align: "left", margin: 0, isTextBox: true, objectName: `stat-${label}` });
  slide.addText(label, { x, y: y + 0.9, w, h: 0.6, fontSize: 12, color: C.accent5, align: "left", margin: 0, isTextBox: true, valign: "top", objectName: `stat-label-${label}` });
}
function tag(slide, x, y, text, fill = C.accent2) {
  slide.addShape(pres.ShapeType.roundRect, { x, y, w: 1.9, h: 0.32, rectRadius: 0.16, fill: { color: fill }, line: { color: fill }, objectName: `tag-${text}` });
  slide.addText(text, { x, y, w: 1.9, h: 0.32, fontSize: 10, bold: true, color: C.background1, align: "center", valign: "middle", margin: 0, isTextBox: true, objectName: `tag-text-${text}` });
}
function shotWithFrame(slide, file, x, y, w, h, caption) {
  slide.addShape(pres.ShapeType.roundRect, { x: x - 0.08, y: y - 0.08, w: w + 0.16, h: h + 0.16, rectRadius: 0.1, fill: { color: C.background2 }, line: { color: C.background2 }, objectName: `frame-${file}` });
  slide.addImage({ path: path.join(IMG, file), x, y, w, h, sizing: { type: "contain", w, h }, objectName: `shot-${file}` });
  if (caption) slide.addText(caption, { x, y: y + h + 0.12, w, h: 0.3, fontSize: 10, color: C.accent5, margin: 0, isTextBox: true, objectName: `cap-${file}` });
}
const NOTE_PREFIX = "";

// ---------- 1. 표지 ----------
pres.addSection({ title: "개요" });
let s = pres.addSlide({ masterName: "TITLE_DARK", sectionTitle: "개요" });
s.addText("EBS 대외 요구자료 대응 에이전트", { placeholder: "title" });
s.addText("언제, 누구에게, 어떤 근거로 답했는지 기억하고 — 다음 답변의 수치와 문서를 검증합니다", { placeholder: "body" });
["①", "②", "③", "④", "⑤", "⑥", "⑦"].forEach((n, i) => circleNum(s, 0.8 + i * 0.75, 1.3, n, 0.55, i % 2 ? C.accent2 : C.accent1));
s.addNotes("가상 데이터 기반 프로토타입입니다. 시연 순서는 ①→⑦이며 약 12분입니다.");

// ---------- 2. 문제 ----------
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "개요" });
s.addText("같은 숫자를 다시 묻고, 그때마다 다른 숫자가 나간다", { placeholder: "title" });
const pains = [
  ["반복 요구", "같은 지표(6/30 기준 등원율)를 시기·요구 주체만 바꿔 다시 요구. 과거 제출값을 찾는 데 시간이 들고, 담당자가 바뀌면 이력이 끊김"],
  ["수치 불일치", "원자료가 사후 보정되어 같은 기준일 수치가 제출 시점마다 달라짐. 의원실과 교육부에 낸 수치가 서로 달라 '왜 다른가' 추가 요구"],
  ["문안 위험", "회신 문안을 매번 새로 작성. 항목 누락·기준일 오기·과장 표현 위험"],
  ["근거 소실", "지표 정의·집계기간·추출시점·원자료 버전이 파일 밖에 남지 않아 사후 소명이 어려움"],
];
pains.forEach((p, i) => card(s, 0.6 + (i % 2) * 6.15, 1.5 + Math.floor(i / 2) * 2.6, 5.95, 2.35, p[0], p[1], { num: i + 1, titleSize: 17, bodySize: 14 }));
s.addNotes("현장 담당자가 겪는 네 가지 상황입니다. 핵심은 '기억'과 '검증'이 사람 머릿속에만 있다는 점입니다.");

// ---------- 3. 배경 근거 ----------
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "개요" });
s.addText("외부에서도 같은 문제가 보고됩니다", { placeholder: "title" });
tag(s, 10.8, 0.55, "출처 원문 확인 필요");
card(s, 0.6, 1.5, 3.95, 4.0, "반복 요구·초과근무", "공무원 설문(884명): 이미 제출한 자료와 유사한 자료를 7회 이상 요구받은 비율 35%(국가직·소방직), 3회 이상 59%(광역시·도). 국감 준비 초과근무 45~56%.\n\n대상은 공무원이며 공영방송 수치는 아닙니다.", { titleSize: 15, bodySize: 12 });
card(s, 4.7, 1.5, 3.95, 4.0, "수치 오류·정의 차이", "국감 제출 자료의 엑셀 셀 오류로 잘못된 통계가 제출된 사례. 데이터는 맞지만 모수 정의가 달라 결과가 잘못 읽힌 사례.\n\n→ 수치와 함께 '정의·집계기간·원자료 버전'을 남겨야 하는 이유", { titleSize: 15, bodySize: 12 });
card(s, 8.8, 1.5, 3.95, 4.0, "AI 활용 보안 원칙", "국정원 생성형 AI 보안 가이드라인(2023): 비공개·개인정보 입력 금지, 생성물 재검증. 행안부는 업무망 내부에 AI 행정지원 서비스 구축(2024).\n\n→ 입력 범위 제한 + 코드·사람 재검증으로 설계", { titleSize: 15, bodySize: 12 });
s.addText("출처 목록: docs/조사_배경자료_2026-10-04.md (검색 요약 기반, 발표 전 원문 확인)", { x: 0.6, y: 5.75, w: 12, h: 0.35, fontSize: 10.5, color: C.accent5, margin: 0, isTextBox: true, objectName: "src-note" });
s.addNotes("외부 수치는 검색 요약에서 가져온 것이라 발표 전에 원문 확인이 필요합니다. 확인 전에는 '출처 확인 필요' 태그를 유지합니다.");

// ---------- 4. 해결 개념: 역할 경계 ----------
pres.addSection({ title: "해결" });
s = pres.addSlide({ masterName: "SECTION", sectionTitle: "해결" });
s.addText("AI는 읽고 쓰고 지적한다. 숫자는 코드가, 판단은 사람이", { placeholder: "title" });
s.addText("역할 경계를 코드로 강제한 담당자 보조 도구", { placeholder: "body" });

s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "해결" });
s.addText("세 주체의 역할 경계", { placeholder: "title" });
const roles = [
  ["AI (Claude API)", C.accent1, ["요구서에서 항목·지표·기준일·기간 추출 — 지표명은 사전 값만(스키마 열거형으로 강제)", "확정 수치·입력된 사유만으로 공문체 초안 작성", "초안의 누락·불일치·단정 표현 지적(수정은 하지 않음)"], "하지 않는 것: 수치 계산·추정, 사유 추정, 원자료 접근"],
  ["코드 (pandas·규칙)", C.accent3, ["새 집계값 vs 과거 제출값 대조, 차이 센터와 '단서'(근거 필드 변경)", "요구 항목 충족 검사, 개인정보 패턴 검사", "HWPX 템플릿 치환·표 행 복제, 제출 묶음 생성"], "API 키가 없어도 같은 흐름이 규칙 기반으로 동작"],
  ["사람 (담당자·팀장)", C.accent2, ["추출 결과 확인·수정", "차이 사유 입력(AI가 추정하지 않음)", "초안 수정, 팀장 승인 → 확정"], "승인 없이는 아무것도 확정되지 않음"],
];
roles.forEach((r, i) => {
  const x = 0.6 + i * 4.15;
  s.addShape(pres.ShapeType.roundRect, { x, y: 1.5, w: 3.95, h: 4.5, rectRadius: 0.12, fill: { color: C.background2 }, line: { color: C.background2 }, objectName: `role-${i}` });
  s.addShape(pres.ShapeType.ellipse, { x: x + 0.25, y: 1.75, w: 0.5, h: 0.5, fill: { color: r[1] }, line: { color: r[1] }, objectName: `role-dot-${i}` });
  s.addText(r[0], { x: x + 0.9, y: 1.75, w: 2.9, h: 0.5, fontSize: 16, bold: true, color: C.text1, margin: 0, isTextBox: true, valign: "middle", objectName: `role-title-${i}` });
  s.addText(r[2].map((t, j) => ({ text: t, options: { bullet: true, breakLine: j < r[2].length - 1 } })), { x: x + 0.25, y: 2.45, w: 3.45, h: 2.9, fontSize: 12, color: C.text1, margin: 0, isTextBox: true, valign: "top", paraSpaceAfter: 6, objectName: `role-body-${i}` });
  s.addText(r[3], { x: x + 0.25, y: 5.05, w: 3.45, h: 0.8, fontSize: 11, italic: true, color: C.text2, margin: 0, isTextBox: true, valign: "top", objectName: `role-foot-${i}` });
});
s.addNotes("가장 중요한 슬라이드입니다. '지어내지 못하게' 하는 장치가 프롬프트가 아니라 스키마·코드·승인 절차에 있다는 점을 강조합니다.");

// ---------- 6. 7개 화면 흐름 ----------
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "해결" });
s.addText("7개 화면, 한 번의 흐름", { placeholder: "title" });
const steps = [
  ["과거 자료 등록", "요구서+제출본 → 항목·지표·기준일 구조화, 산출 근거·출처 저장"],
  ["새 요구서 분석", "같은 기준일·지표의 과거 제출 이력, 유사 요구서, 데이터 출처 제안"],
  ["수치 대조·점검표", "차이 센터·단서 → 사유 입력 → 정합성 점검표 → 개인정보 검사"],
  ["회신 초안·HWPX", "공문체 초안 → 충족 검사 → Claude 추가 점검 → HWPX·제출 묶음"],
  ["검토·승인", "팀장 승인·반려·의견 기록"],
  ["이력 조회", "요구→항목→제출값→사유→초안→승인 한 줄, 키워드 검색"],
  ["현황·통계", "D-day·기한 임박, 관리대장 엑셀, 반복 요구 지표"],
];
steps.forEach((st, i) => {
  const col = i < 4 ? i : i - 4, row = i < 4 ? 0 : 1;
  const x = 0.6 + col * 3.1, y = 1.55 + row * 2.75, w = 2.9, h = 2.45;
  s.addShape(pres.ShapeType.roundRect, { x, y, w, h, rectRadius: 0.12, fill: { color: C.background2 }, line: { color: C.background2 }, objectName: `step-${i}` });
  circleNum(s, x + 0.2, y + 0.2, String(i + 1), 0.5, i === 2 || i === 3 ? C.accent2 : C.accent1);
  s.addText(st[0], { x: x + 0.85, y: y + 0.2, w: w - 1.0, h: 0.5, fontSize: 14, bold: true, color: C.text1, margin: 0, isTextBox: true, valign: "middle", objectName: `step-title-${i}` });
  s.addText(st[1], { x: x + 0.2, y: y + 0.85, w: w - 0.4, h: h - 1.0, fontSize: 11.5, color: C.text1, margin: 0, isTextBox: true, valign: "top", objectName: `step-body-${i}` });
  if (i < 3) s.addShape(pres.ShapeType.rightArrow, { x: x + w + 0.02, y: y + h / 2 - 0.12, w: 0.16, h: 0.24, fill: { color: C.accent5 }, line: { color: C.accent5 }, objectName: `arrow-${i}` });
});
s.addText("주황 번호(③·④)가 '검증'의 핵심 단계입니다. 설정 화면에서 API 연결·모델·비용을 확인합니다.", { x: 9.9, y: 4.3, w: 2.85, h: 2.45, fontSize: 11.5, italic: true, color: C.text2, margin: 0, isTextBox: true, valign: "top", objectName: "flow-note" });

// ---------- 시연 ----------
pres.addSection({ title: "시연" });
s = pres.addSlide({ masterName: "SECTION", sectionTitle: "시연" });
s.addText("시연 — 9월 국감 요구서가 7월 수치를 다시 묻는다", { placeholder: "title" });
s.addText("샘플: 센터 12곳 등원율(6/30 기준), 7월 제출본 vs 9월 재산출본", { placeholder: "body" });

function hdr(t) { return { text: t, options: { bold: true, color: H.lt1, fill: { color: H.dk2 } } }; }
function demoSlide(title, img, imgH, points, note, table) {
  const sl = pres.addSlide({ masterName: "CONTENT", sectionTitle: "시연" });
  sl.addText(title, { placeholder: "title" });
  const iw = 7.6, ih = Math.min(4.85, iw * imgH);
  shotWithFrame(sl, img, 0.6, 1.5, iw, ih, "화면 캡처(규칙 경로, 2026-10-04)");
  bullets(sl, points, 8.6, 1.5, 4.15, table ? 2.9 : 4.6, 12.5);
  if (table) {
    sl.addText(table.title, { x: 8.6, y: 4.45, w: 4.15, h: 0.3, fontSize: 11, bold: true, color: C.text2, margin: 0, isTextBox: true, objectName: `tbl-title-${table.title}` });
    sl.addTable([table.head.map(hdr), ...table.rows], { x: 8.6, y: 4.8, w: 4.15, colW: table.colW, fontSize: 9.5, color: H.dk1, border: { type: "solid", color: H.accent6, pt: 0.75 }, fill: { color: H.lt1 }, valign: "middle", margin: 0.04, objectName: `tbl-${table.title}` });
  }
  sl.addNotes(note);
  return sl;
}
demoSlide("② 새 요구서 분석 — '7월에 의원실에 이 값으로 답했다'가 붙는다", "02_새요구서분석.png", 1100 / 1070,
  ["접수일·기한·제목·항목을 구조화하고 유사한 과거 요구서(#1, 유사도 0.81)를 찾음",
   "'6/30 기준 등원율' 항목에 2026-07-08 제출 12건과 산출 근거(정의·집계기간·추출시점·원자료 v1)가 붙음",
   "'등록 학생 수'는 같은 기준일 이력 없음 → 신규 산출·근거 기록 필요로 제안",
   "'최근 3년 운영 예산·집행률'은 지표 2개로 분리, 기준일 없음 → 확인 필요"],
  "담당자가 바뀌어도 과거 답변이 항목 단위로 따라옵니다.",
  { title: "항목별 데이터 제안 판단(코드)", head: ["상황", "제안"], colW: [1.9, 2.25],
    rows: [["같은 지표·기준일 제출 이력 있음", "새 집계값과 대조 후 재사용"], ["같은 기준일 이력 없음", "신규 산출, 산출 근거 기록 필요"], ["기준일 없음", "요구서에서 기준일 확인 필요"], ["지표 미인식(사전에 없음)", "항목 문구 확인 또는 지표 사전 추가"]] });
demoSlide("③ 수치 대조 — 12개 센터 중 3곳이 다르고, 단서가 보인다", "03_수치대조_점검표.png", 1000 / 1070,
  ["새 집계값(9월 재산출) vs 과거 제출값(7월 제출본)을 코드로 대조",
   "센터C·G·K 차이, 단서: 추출시점·원자료 버전 변경(v1 → v2 사후 보정)",
   "차이 사유는 담당자가 입력. AI는 추정하지 않음",
   "정합성 점검표에 과거·신규 출처(파일명·행 번호) 기록, 개인정보 패턴 검사 후 확정"],
  "대조에는 AI를 쓰지 않습니다. 단서는 제출값과 함께 저장한 근거 필드의 차이에서 나옵니다.",
  { title: "차이 3건(값은 가상)", head: ["센터", "과거", "신규", "차이", "단서"], colW: [0.6, 0.6, 0.6, 0.55, 1.8],
    rows: [["센터C", "66.4", "67.8", "+1.4", "추출시점·원자료 버전 변경"], ["센터G", "78.6", "80.0", "+1.4", "추출시점·원자료 버전 변경"], ["센터K", "57.2", "59.3", "+2.1", "추출시점·원자료 버전 변경"]] });
demoSlide("④ 회신 초안 — 확정 수치·입력 사유만 쓰고, 빠진 건 [확인 필요]", "04_회신초안_HWPX.png", 1240 / 1070,
  ["요구 항목 순서대로 번호 문단, 차이 사유·산출 근거 자동 구성",
   "충족 검사: 등원율 충족 1건, 확정 수치가 없는 3건은 '[확인 필요] 표시'로 분리 집계",
   "Claude 추가 점검(키 있을 때): 누락·기준일 불일치·표에 없는 숫자·단정 표현 지적",
   "초안 개인정보 검사 → HWPX(표 12행 자동 확장) → 제출 묶음 ZIP → 팀장 검토 요청"],
  "초안의 숫자는 확정 수치 표에서만 인용됩니다. 데이터가 없는 항목은 지어내지 않고 [확인 필요]로 남깁니다.",
  { title: "요구 항목 충족 검사(코드)", head: ["요구 항목", "지표", "확정 수치", "판정"], colW: [1.75, 0.95, 0.65, 0.8],
    rows: [["6/30 기준 센터별 등원율", "등원율", "있음", "충족"], ["6/30 기준 센터별 등록 학생 수", "등록 학생 수", "없음", "미확인 표시"], ["최근 3년 운영 예산 및 집행률", "운영 예산", "없음", "미확인 표시"], ["최근 3년 운영 예산 및 집행률", "예산 집행률", "없음", "미확인 표시"]] });

s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "시연" });
s.addText("⑤ 승인 → ⑥ 이력 → ⑦ 현황: 한 줄로 이어지는 기록", { placeholder: "title" });
shotWithFrame(s, "05_검토승인.png", 0.6, 1.55, 3.95, 3.95 * (960 / 1070), "⑤ 팀장 검토·승인(의견 기록)");
shotWithFrame(s, "06_이력조회.png", 4.7, 1.55, 3.95, 3.95 * (720 / 1070), "⑥ 요구→항목→제출본→초안→승인");
shotWithFrame(s, "07_현황통계.png", 8.8, 1.55, 3.95, 3.95 * (880 / 1070), "⑦ D-day·기한 임박·반복 요구 지표");
s.addText("반복 요구 지표(등원율 2회·요청 주체 2곳)는 사전 산출·표준 답변 후보가 됩니다. 관리대장은 엑셀로 내보냅니다.", { x: 0.6, y: 5.9, w: 12.1, h: 0.6, fontSize: 12.5, color: C.text1, margin: 0, isTextBox: true, objectName: "wrap-note" });

// ---------- 아키텍처 ----------
pres.addSection({ title: "구현" });
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "구현" });
s.addText("구조 — PC 한 대에서 끝나고, API는 선택", { placeholder: "title" });
function box(x, y, w, h, title, body, fill = C.background2, tcolor = C.text1) {
  s.addShape(pres.ShapeType.roundRect, { x, y, w, h, rectRadius: 0.1, fill: { color: fill }, line: { color: fill }, objectName: `arch-${title}` });
  s.addText(title, { x: x + 0.15, y: y + 0.1, w: w - 0.3, h: 0.4, fontSize: 13, bold: true, color: tcolor, margin: 0, isTextBox: true, objectName: `arch-t-${title}` });
  if (body) s.addText(body, { x: x + 0.15, y: y + 0.5, w: w - 0.3, h: h - 0.6, fontSize: 10.5, color: tcolor, margin: 0, isTextBox: true, valign: "top", objectName: `arch-b-${title}` });
}
// 좌: PC 로컬 영역
s.addShape(pres.ShapeType.roundRect, { x: 0.6, y: 1.5, w: 8.4, h: 5.2, rectRadius: 0.15, fill: { color: C.background1 }, line: { color: C.accent6, width: 2 }, objectName: "arch-pc" });
s.addText("담당자 PC (Windows) — run.bat 한 번", { x: 0.8, y: 1.6, w: 8, h: 0.4, fontSize: 13, bold: true, color: C.text2, margin: 0, isTextBox: true, objectName: "arch-pc-title" });
box(0.8, 2.1, 2.5, 1.5, "화면 (Streamlit)", "7개 화면 + 설정\n브라우저 로컬 실행");
box(3.5, 2.1, 2.5, 1.5, "추출·정규화", "extract · normalize(지표 사전)\ndocread(txt·HWPX·PDF·DOCX)");
box(6.2, 2.1, 2.6, 1.5, "검색·제안", "search(문자 n-gram, 외부 API 없음)\nsuggest(데이터 카탈로그)");
box(0.8, 3.8, 2.5, 1.5, "대조·점검 (코드)", "compare(pandas) · pii\n차이·단서·점검표·출처");
box(3.5, 3.8, 2.5, 1.5, "초안·출력", "draft(초안·충족 검사)\nhwpx_out(치환·행 복제)");
box(6.2, 3.8, 2.6, 1.5, "이력 DB (SQLite 1파일)", "requests · items · submissions\nsubmission_values(출처) · diff_reasons · drafts · reviews");
box(0.8, 5.5, 8.0, 1.05, "점검 도구", "run_checks.py: ruff → pytest 38건 → 추출 품질 점검(규칙/Claude) → 6단계 화면 흐름  ·  make_submission_zip.py  ·  capture_screens.py");
// 우: API
box(9.3, 1.5, 3.45, 2.3, "Claude API (선택)", "llm.py 공통 호출\n· 모델 자동 대체(404 → 다음 후보)\n· 구조화 출력(JSON 스키마) + 미지원 시 텍스트 파싱\n· 429·5xx 자동 재시도, 타임아웃\n· 호출별 모델·지연·토큰·추정 비용 기록", C.accent1, C.background1);
box(9.3, 4.0, 3.45, 1.5, "밖으로 나가는 것", "요구서 문안 · 센터 단위 확정 집계값 · 담당자 입력 사유 · 산출 근거 메타\n(개인 단위 원자료는 전달하지 않음)", C.background2);
box(9.3, 5.7, 3.45, 1.0, "키가 없으면", "규칙 기반으로 전 기능 동작(추출은 정형 서식 한정)", C.background2);
s.addShape(pres.ShapeType.line, { x: 9.0, y: 2.65, w: 0.3, h: 0, line: { color: C.accent5, width: 1.5, dashType: "dash" }, objectName: "arch-link" });
s.addNotes("서버가 없습니다. SQLite 파일 하나가 이력 전부라서 백업이 곧 운영입니다. API는 설정 화면에서 세션 한정으로 켜고 끕니다.");

// ---------- 검증 결과 ----------
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "구현" });
s.addText("검증 결과 — 실측치", { placeholder: "title" });
stat(s, 0.6, 1.5, 3.0, "25 / 25", "요구서 추출 필수 쌍 재현\nClaude 경로 · 9사례 · 초과·오탐 0\n(2026-09-28, claude-sonnet-4-6)");
stat(s, 3.7, 1.5, 3.0, "3.8초", "요구서 1건 추출 평균 지연\n(9건, 입력 10,872 · 출력 2,711 토큰)");
stat(s, 6.8, 1.5, 3.0, "38", "pytest 단위 테스트 통과\n(추출·대조·개인정보·HWPX·초안·LLM·DB)");
stat(s, 9.9, 1.5, 3.0, "8 / 8", "화면 6단계 흐름 구동\nAppTest + 실제 브라우저, 오류 0");
const rows = [
  [{ text: "항목", options: { bold: true, color: H.lt1, fill: { color: H.dk2 } } }, { text: "결과", options: { bold: true, color: H.lt1, fill: { color: H.dk2 } } }, { text: "비고", options: { bold: true, color: H.lt1, fill: { color: H.dk2 } } }],
  ["추출 품질(규칙 경로)", "9사례 25/25, 초과·오탐 0", "규칙을 사례에 맞춰 조정했으므로 참고치"],
  ["추출 품질(Claude 경로)", "1차 24/25 → 연도 보완 규칙 후 2차 25/25", "실전형 5건 포함(국감 표 형식·감사 공문체·메일체·설명형·사전 밖 지표)"],
  ["초안 원칙 준수", "평균·증감 계산 없음, 12개 센터 값 표와 일치, 없는 항목 [확인 필요]", "2026-09-28 육안 확인, 초안·추가 점검 각 약 8초"],
  ["HWPX 출력", "12행 표 확장, XML 정형성, 자리표시자 잔존 0", "한글에서 타이핑해 run이 쪼개진 자리표시자도 치환(10-04 보강)"],
  ["입력 견고성", "cp949 CSV, '2026. 6. 30.', '67.8%', '1,234', 빈 행, 다중 시트", "오류는 행 번호·허용 컬럼명과 함께 표시"],
];
s.addTable(rows, { x: 0.6, y: 3.4, w: 12.1, colW: [2.6, 5.0, 4.5], fontSize: 10.5, color: H.dk1, border: { type: "solid", color: H.accent6, pt: 0.75 }, fill: { color: H.lt1 }, valign: "middle", margin: 0.06, objectName: "verify-table" });
s.addNotes("규칙 경로 100%는 사례에 맞춘 결과라 자랑거리가 아닙니다. Claude 경로가 실제 점검 대상이고, 1차에서 놓친 연도 없는 날짜를 프롬프트 규칙으로 보완했습니다.");

// ---------- 보안·비용 ----------
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "구현" });
s.addText("보안과 비용", { placeholder: "title" });
card(s, 0.6, 1.5, 5.95, 5.0, "보안 설계", "", { titleSize: 17 });
bullets(s, ["API 키는 환경변수 또는 설정 화면 세션 한정 입력 — 파일에 저장하지 않음",
  "이력 DB는 PC 로컬 SQLite. 외부로는 요구서 문안·센터 단위 확정값·입력 사유·근거 메타만 전송",
  "개인 단위 원자료(출결 명단)는 올리지 않음. ③ 집계값·④ 초안에서 개인정보 패턴 검사(주민번호·전화·이메일·학생 식별어)",
  "국정원 생성형 AI 보안 가이드라인의 '비공개·개인정보 입력 금지, 생성물 재검증' 원칙과 정렬",
  "API가 막힌 망에서도 규칙 경로로 전 기능 동작"], 0.85, 2.2, 5.45, 4.1, 12.5);
card(s, 6.75, 1.5, 5.95, 5.0, "비용 (추정)", "", { titleSize: 17 });
stat(s, 7.0, 2.2, 2.7, "≈ $0.008", "요구서 1건 추출\n(입력 1.2k · 출력 0.3k 토큰)", C.accent1, 30);
stat(s, 9.9, 2.2, 2.7, "≈ $0.21", "시연 1회(호출 약 20회,\n입력 3만 · 출력 8천 토큰)", C.accent1, 30);
bullets(s, ["단가: claude-sonnet-4-6 입력 $3 · 출력 $15 / 100만 토큰(공개 가격표 2026-09-25 기준)",
  "설정 화면 연결 테스트에서 모델별 단가와 세션 누적 추정 비용을 바로 확인",
  "실제 청구액·월 호출량은 [확인 필요] — 콘솔 Usage 기준으로 확정"], 7.0, 4.1, 5.45, 2.3, 11.5);
s.addNotes("비용은 공개 단가표로 추정한 값입니다. 실제 청구는 콘솔에서 확인해야 하며, 월 호출량이 정해지면 다시 계산합니다.");

// ---------- 한계·계획 ----------
pres.addSection({ title: "마무리" });
s = pres.addSlide({ masterName: "CONTENT", sectionTitle: "마무리" });
s.addText("한계와 다음 단계", { placeholder: "title" });
const lim = [
  ["규칙 경로는 정형 서식에 의존", "실제 운영은 Claude 경로. 실제 요구서를 점검 사례에 추가해 재채점"],
  ["유사 검색은 문자 n-gram(임베딩 아님)", "이력이 쌓이면 사내 임베딩 또는 API 임베딩 검토"],
  ["HWP(구형식) 미지원, 자리표시자는 한글에서 삽입", "부서 회신 서식 HWPX 1종 템플릿화(2주 내)"],
  ["충족 검사(코드)는 지표명 언급 수준", "Claude 추가 점검과 병행, 판정 규칙 보강"],
  ["샘플 데이터·지표 사전 7개", "실데이터 컬럼 맞춤, 지표 사전·데이터 카탈로그 확장"],
  ["단일 PC·SQLite", "부서 공유가 필요하면 공유 폴더 DB 또는 소형 서버 검토"],
];
s.addText("한계", { x: 0.6, y: 1.45, w: 5.6, h: 0.4, fontSize: 14, bold: true, color: C.accent4, margin: 0, isTextBox: true, objectName: "lim-h" });
s.addText("다음 단계", { x: 6.7, y: 1.45, w: 6.0, h: 0.4, fontSize: 14, bold: true, color: C.accent3, margin: 0, isTextBox: true, objectName: "plan-h" });
lim.forEach((r, i) => {
  const y = 1.95 + i * 0.78;
  s.addShape(pres.ShapeType.roundRect, { x: 0.6, y, w: 5.6, h: 0.66, rectRadius: 0.08, fill: { color: C.background2 }, line: { color: C.background2 }, objectName: `lim-${i}` });
  s.addText(r[0], { x: 0.8, y, w: 5.2, h: 0.66, fontSize: 12, color: C.text1, margin: 0, isTextBox: true, valign: "middle", objectName: `lim-t-${i}` });
  s.addShape(pres.ShapeType.rightArrow, { x: 6.3, y: y + 0.21, w: 0.3, h: 0.24, fill: { color: C.accent5 }, line: { color: C.accent5 }, objectName: `lim-arrow-${i}` });
  s.addShape(pres.ShapeType.roundRect, { x: 6.7, y, w: 6.0, h: 0.66, rectRadius: 0.08, fill: { color: C.background2 }, line: { color: C.background2 }, objectName: `plan-${i}` });
  s.addText(r[1], { x: 6.9, y, w: 5.6, h: 0.66, fontSize: 12, color: C.text1, margin: 0, isTextBox: true, valign: "middle", objectName: `plan-t-${i}` });
});

// ---------- 마무리 ----------
s = pres.addSlide({ masterName: "TITLE_DARK", sectionTitle: "마무리" });
s.addText("답변을 기억하는 부서, 숫자를 검증하는 절차", { placeholder: "title" });
s.addText("요청 사항: 실데이터 1개 지표(등원율) 시범 적용 · 부서 회신 서식 HWPX 1종 · 사내망 API 허용 여부 확인", { placeholder: "body" });
s.addText([
  { text: "확인 필요 항목  ", options: { bold: true, color: C.accent2 } },
  { text: "운영 모델명 고정 · API 단가·월 호출량 · 사내 PC API 허용 여부 · 라이브러리 라이선스 · EBS 자체 요구자료 건수 · 외부 조사 수치 원문", options: { color: C.accent6 } },
], { x: 0.8, y: 5.2, w: 11.7, h: 0.9, fontSize: 12, margin: 0, isTextBox: true, valign: "top", objectName: "todo" });
s.addNotes("마지막으로 요청 사항 세 가지를 명확히 말합니다. 실데이터 1개 지표부터 시작하면 2주 안에 실사용 검증이 가능합니다.");

(async () => {
  await pres.writeFile({ fileName: OUT });
  await applyTheme(OUT, THEME);
  console.log("written", OUT);
})();

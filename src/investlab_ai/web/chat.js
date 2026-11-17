"use strict";
const $ = (id) => document.getElementById(id);
const categoryNames = {summary:"핵심 요약",business:"어떤 사업을 하나요?",performance:"실적에서 볼 부분",risk:"확인할 위험요인",follow_up:"함께 확인해 주세요"};
const storageKey = "investlab.conversations.v1";
let chats = [];
try { chats = JSON.parse(localStorage.getItem(storageKey) || "[]"); } catch { chats = []; }
if (!Array.isArray(chats)) chats = [];
let current = null;
let busy = false;

function node(tag, className, text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined) result.textContent = text;
  return result;
}
function save() {
  try { localStorage.setItem(storageKey, JSON.stringify(chats.slice(0, 30))); } catch { /* Current chat remains usable. */ }
  renderHistory();
}
function scrollDown() { $("conversation").scrollTop = $("conversation").scrollHeight; }
function renderHistory() {
  $("history").replaceChildren();
  for (const chat of chats) {
    const button = node("button", "history-item" + (current?.id === chat.id ? " active" : ""), chat.title);
    button.title = chat.title;
    button.addEventListener("click", () => { if (!busy) openChat(chat); });
    $("history").append(button);
  }
}
function setContext() {
  $("context").hidden = !current?.reportLabel;
  $("context").textContent = current?.reportLabel ? (current.contextKind === "market" ? "이어서 살펴보는 종목 · " : "이어서 질문하는 공시 · ") + current.reportLabel : "";
}
function newChat() {
  if (busy) return;
  current = null;
  $("messages").replaceChildren();
  $("welcome").hidden = false;
  setContext(); renderHistory(); $("message").focus();
}
function userMessage(text) {
  const article = node("article", "message user-message");
  article.append(node("div", "user-bubble", text)); $("messages").append(article);
}
function assistantShell() {
  const article = node("article", "message assistant-message");
  const label = node("div", "assistant-label");
  label.append(node("span", "mini-logo", "i"), node("span", "", "InvestLab"));
  article.append(label); $("messages").append(article); return article;
}
function safeLink(url, text) {
  const link = node("a", "", text);
  try { const parsed = new URL(url); if (parsed.protocol === "https:" && ["dart.fss.or.kr", "developers.tossinvest.com", "www.sec.gov", "data.sec.gov", "sec.gov"].includes(parsed.hostname)) link.href = parsed.href; } catch { /* No unsafe navigation. */ }
  link.target = "_blank"; link.rel = "noopener noreferrer"; return link;
}
function renderResult(article, data, taskId) {
  article.querySelectorAll(":scope > :not(.assistant-label)").forEach((child) => child.remove());
  if (data.status === "error") { article.append(node("div", "error", data.message || "처리하지 못했어요. 다시 질문해 주세요.")); return; }
  if (data.market) { renderMarket(article, data, taskId); return; }
  if (!data.result) {
    article.append(node("div", "message-text", data.message || "기업 이름을 알려 주세요."));
    if (data.choices?.length) {
      const choices = node("div", "choices");
      for (const company of data.choices) {
        const button = node("button", "", company.corpName);
        button.addEventListener("click", () => sendMessage(data.originalMessage, company.corpCode, company.cik ? company.stockCode : null));
        choices.append(button);
      }
      article.append(choices);
    }
    return;
  }
  const result = data.result;
  if (data.notice) article.append(node("p", "scope-note", data.notice));
  const report = data.disclosure || {...data.secFiling, reportName:data.secFiling.form + (data.secFiling.reportDate ? " · 보고대상일 " + data.secFiling.reportDate : "")};
  const card = node("div", "report-card");
  const title = node("div");
  title.append(node("strong", "", data.company.corpName + " · " + report.reportName), node("small", "", "공시일 " + report.filedOn));
  card.append(title, safeLink(report.sourceUrl, "원문 보기 ↗")); article.append(card);
  if (result.analysisScope === "basic") article.append(node("span", "cached-badge", result.cached ? "저장된 기본 분석을 불러왔어요" : "기본 분석을 저장했어요 · 다음에는 바로 볼 수 있어요"));
  if (result.status === "insufficient_evidence") article.append(node("p", "message-text", result.answer));
  const sources = new Map((result.sources || []).map((item, index) => [item.chunkId, { ...item, index: index + 1 }]));
  const claims = node("div", "claims");
  for (const category of Object.keys(categoryNames)) {
    const matching = (result.claims || []).filter((claim) => claim.category === category);
    if (!matching.length) continue;
    claims.append(node("h3", "", categoryNames[category]));
    for (const claim of matching) {
      const paragraph = node("p", "claim", claim.text);
      for (const id of new Set(claim.evidenceIds)) {
        const source = sources.get(id); if (!source) continue;
        const cite = node("button", "citation", String(source.index));
        cite.setAttribute("aria-label", "원문 근거 " + source.index + " 보기");
        cite.addEventListener("click", () => {
          const details = article.querySelector("details.sources");
          if (details) { details.open = true; article.querySelector(`[data-source-number="${source.index}"]`)?.scrollIntoView({block:"nearest"}); }
        });
        paragraph.append(cite);
      }
      claims.append(paragraph);
    }
  }
  article.append(claims);
  if (sources.size) {
    const details = node("details", "sources"); details.append(node("summary", "", `공시에서 확인한 근거 ${sources.size}개`));
    for (const source of sources.values()) {
      const item = node("div", "source-item"); item.dataset.sourceNumber = String(source.index);
      item.append(safeLink(source.sourceUrl, `${source.index}. ${source.section.split(" / ").slice(1).join(" / ") || "공시 본문"} ↗`), node("blockquote", "", source.text)); details.append(item);
    }
    article.append(details);
  }
  if (result.analysisScope === "basic") article.append(node("p", "scope-note", "사업·실적·위험의 핵심 항목을 살펴본 기본 분석이에요. 궁금한 부분은 이어서 질문해 주세요."));
  for (const warning of result.warnings || []) article.append(node("p", "scope-note", warning));
  current.receipt = report.rceptNo || null;
  current.contextKind = data.secFiling ? "sec" : "disclosure";
  current.previousTaskId = taskId;
  current.reportLabel = data.company.corpName + " · " + report.reportName;
  setContext(); save();
}
function renderMarket(article, data, taskId) {
  const result = data.market;
  const number = (value, digits=2) => value === null || value === undefined ? "자료 부족" : Number(value).toLocaleString("ko-KR", {maximumFractionDigits:digits});
  const money = (value) => result.quote.currency === "USD" ? "$" + Number(value).toLocaleString("en-US", {minimumFractionDigits:2, maximumFractionDigits:4}) + " USD" : number(value, 0) + "원";
  const stamp = (value) => value ? new Date(value).toLocaleString("ko-KR", {timeZone:"Asia/Seoul"}) : "시세 시각 미제공";
  const card = node("div", "report-card market-card");
  const title = node("div");
  title.append(node("strong", "", data.company.corpName + " · " + money(result.quote.lastPrice)),
    node("small", "", "토스 시세 데이터 시각 (한국시간) · " + stamp(result.quote.timestamp)));
  card.append(title, node("span", "cached-badge", result.kind === "quote" ? "현재가 조회" : result.cached ? "같은 자료의 저장된 전망" : "시세·공시 조건부 전망"));
  article.append(card);
  if (result.horizon) article.append(node("p", "scope-note", "분석 기간 · " + result.horizon));
  const metrics = result.indicators;
  if (metrics) {
    const grid = node("dl", "market-metrics");
    for (const [label, value] of [
      ["20거래일 수익률", number(metrics.return20SessionsPct) + "%"],
      ["60거래일 수익률", number(metrics.return60SessionsPct) + "%"],
      ["20일 이동평균", money(metrics.sma20)],
      ["60일 이동평균", money(metrics.sma60)],
      ["RSI(14)", number(metrics.rsi14Wilder)],
      ["직전 20일 평균 대비 거래량", metrics.lastCompletedVolumeVsPrior20Mean === null ? "자료 부족" : number(metrics.lastCompletedVolumeVsPrior20Mean) + "배"],
    ]) { const item = node("div"); item.append(node("dt", "", label), node("dd", "", value)); grid.append(item); }
    article.append(grid, node("p", "scope-note", "지표 기준 거래일 · " + new Date(metrics.completedThrough).toLocaleDateString("ko-KR", {timeZone:metrics.marketTimeZone || "Asia/Seoul"}) + " · " + (metrics.marketTimeZone || "Asia/Seoul") + " · 완료 일봉 " + metrics.observations + "개 · 수정주가 · 현지 당일 봉 제외"));
  }
  const names = {summary:"전망 요약",performance:"가격·거래량 흐름",business:"공시에서 확인한 변화",risk:"하락 요인과 불확실성",follow_up:"상승·하락 시나리오와 확인 조건"};
  const sources = new Map((result.sources || []).map((source, index) => [source.chunkId, {...source,index:index+1}]));
  const claims = node("div", "claims");
  for (const [category, label] of Object.entries(names)) {
    const matching = (result.claims || []).filter((claim) => claim.category === category);
    if (!matching.length) continue;
    claims.append(node("h3", "", label));
    for (const claim of matching) {
      const paragraph = node("p", "claim", claim.text);
      for (const id of new Set(claim.evidenceIds)) {
        const source = sources.get(id); if (!source) continue;
        const cite = node("button", "citation", String(source.index));
        cite.setAttribute("aria-label", "분석 근거 " + source.index + " 보기");
        cite.addEventListener("click", () => {
          const details = article.querySelector("details.sources");
          if (details) { details.open = true; article.querySelector(`[data-source-number="${source.index}"]`)?.scrollIntoView({block:"nearest"}); }
        }); paragraph.append(cite);
      } claims.append(paragraph);
    }
  }
  article.append(claims);
  if (result.status === "insufficient_evidence") article.append(node("p", "message-text", "제공된 자료로는 이 질문의 전망을 판단하기 어려워요. 분석 기간이나 확인할 요인을 구체적으로 알려 주세요."));
  if (sources.size) {
    const details = node("details", "sources"); details.append(node("summary", "", "시세·계산·공시 근거 " + sources.size + "개"));
    for (const source of sources.values()) {
      const item = node("div", "source-item"); item.dataset.sourceNumber = String(source.index);
      item.append(safeLink(source.sourceUrl, source.index + ". " + source.section + " ↗"), node("blockquote", "", source.text)); details.append(item);
    } article.append(details);
  }
  for (const warning of result.warnings || []) article.append(node("p", "scope-note", warning));
  if (result.kind === "outlook") article.append(node("p", "scope-note", "제공된 자료에 근거한 조건부 해석이며 실제 상승 확률이나 수익을 보장하지 않아요."));
  current.receipt = null; current.previousTaskId = taskId; current.contextKind = "market";
  current.reportLabel = data.company.corpName + (result.horizon ? " · " + result.horizon : " · 현재가");
  setContext(); save();
}
async function getTask(id) {
  const response = await fetch("/chat/api/tasks/" + encodeURIComponent(id), {credentials:"same-origin"});
  if (!response.ok) throw new Error("이 대화를 불러오지 못했어요. 새 대화에서 다시 질문해 주세요.");
  return response.json();
}
async function poll(id, article) {
  for (;;) {
    const data = await getTask(id);
    if (data.status !== "running") { renderResult(article, data, id); return; }
    let status = article.querySelector(".status-line");
    if (!status) { status = node("div", "status-line"); status.append(node("span", "loader"), node("span")); article.append(status); }
    status.lastChild.textContent = data.stage || "공시를 살펴보고 있어요";
    scrollDown();
    await new Promise((resolve) => setTimeout(resolve, 1400));
  }
}
async function openChat(chat) {
  current = chat; $("welcome").hidden = true; $("messages").replaceChildren(); renderHistory(); setContext();
  busy = true; $("send").disabled = true;
  try {
    for (const message of chat.messages) {
      if (message.role === "user") userMessage(message.text);
      else { const article = assistantShell(); await poll(message.taskId, article); }
    }
  } catch (error) { assistantShell().append(node("div", "error", error.message)); }
  finally { busy = false; $("send").disabled = false; scrollDown(); }
}
async function sendMessage(text, corpCode, stockSymbol) {
  text = text.trim(); if (!text || busy) return;
  busy = true; $("send").disabled = true; $("welcome").hidden = true;
  if (!current) { current = {id:crypto.randomUUID(),title:text.slice(0,30),messages:[]}; chats.unshift(current); }
  current.messages.push({role:"user",text}); userMessage(text); $("message").value = ""; $("message").style.height = "auto";
  const article = assistantShell(); const line = node("div", "status-line"); line.append(node("span","loader"),node("span","질문에 필요한 자료를 찾고 있어요")); article.append(line); scrollDown(); save();
  try {
    const payload = {message:text,rceptNo:current.receipt || null,corpCode:corpCode || null,previousTaskId:current.previousTaskId || null};
    if (stockSymbol) payload.stockSymbol = stockSymbol;
    const response = await fetch("/chat/api/messages", {method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    if (!response.ok) throw new Error(response.status === 401 ? "화면을 새로고침한 뒤 다시 질문해 주세요." : "질문을 보내지 못했어요. 서버가 실행 중인지 확인해 주세요.");
    const {taskId} = await response.json(); current.messages.push({role:"assistant",taskId}); save(); await poll(taskId, article);
  } catch (error) { line.remove(); article.append(node("div", "error", error.message || "서버 연결을 확인해 주세요.")); }
  finally { busy = false; $("send").disabled = false; $("message").focus(); scrollDown(); await libraryStatus(); }
}
async function libraryStatus() {
  if (document.hidden) return;
  try {
    const response = await fetch("/chat/api/status", {credentials:"same-origin"}); if (!response.ok) return;
    const data = await response.json();
    $("library-status").textContent = data.analyzedCompanies !== undefined ? `국내 ${data.analyzedCompanies.toLocaleString()}개 · 공시 ${data.basicReports.toLocaleString()}건` : `국내 공시 ${data.documents.toLocaleString()}건 · 기본 분석 ${data.analyses.toLocaleString()}건`;
    if (data.overseas) $("library-status").textContent += `\n미국 ${data.overseas.analyzedCompanies.toLocaleString()}개 · SEC 기본 분석 ${data.overseas.analyses.toLocaleString()}건`;
    const secRun = data.overseas?.run;
    if (secRun && secRun.status !== "completed") $("library-status").textContent += `\n미국 수집 ${secRun.collected || 0}/${secRun.total} · 분석 ${secRun.analyzed || 0}/${secRun.total}`;
    if (data.bulk && data.bulk.status !== "completed") $("library-status").textContent += data.bulk.status.startsWith("collect") ? `\n원문 수집 ${data.bulk.collected.toLocaleString()} / ${data.bulk.total.toLocaleString()}건` : `\n기본 분석 ${data.bulk.completed.toLocaleString()} / ${data.bulk.total.toLocaleString()}건`;
    if (!data.ready && data.savedAnalysisAvailable) $("library-status").textContent += "\n저장된 기본 분석을 바로 볼 수 있어요";
    else if (!data.ready) $("library-status").textContent = "새 분석에는 OpenAI와 DART 또는 SEC 연결 설정이 필요해요";
    if ((data.bulk && !["completed", "error", "canceled"].includes(data.bulk.status)) || (secRun && ["collecting", "analyzing"].includes(secRun.status))) setTimeout(libraryStatus, 30000);
  } catch { $("library-status").textContent = "서버 연결을 확인해 주세요"; }
}
$("new-chat").addEventListener("click", newChat);
$("chat-form").addEventListener("submit", (event) => { event.preventDefault(); sendMessage($("message").value); });
$("message").addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); sendMessage($("message").value); } });
$("message").addEventListener("input", () => { $("message").style.height = "auto"; $("message").style.height = Math.min($("message").scrollHeight,160) + "px"; });
document.querySelectorAll("[data-prompt]").forEach((button) => button.addEventListener("click", () => sendMessage(button.dataset.prompt)));
newChat(); libraryStatus();

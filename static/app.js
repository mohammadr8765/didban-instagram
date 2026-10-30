const $ = (s) => document.querySelector(s);
const list = $("#accounts");
const chipsEl = $("#cats");
const checkBtn = $("#checkBtn");
const READ_KEY = "didban_read_v1";
const CAT_KEY = "didban_cat_v1";
let installPrompt = null;
let accountsCache = [];
let lastData = null;
let readMapNow = {};
let categories = {};
let selectedCat = "all";
let lastCheckIso = null;
try { selectedCat = localStorage.getItem(CAT_KEY) || "all"; } catch {}

function loadRead() {
  try { return JSON.parse(localStorage.getItem(READ_KEY) || "null"); } catch { return null; }
}
function saveRead(map) {
  try { localStorage.setItem(READ_KEY, JSON.stringify(map)); } catch {}
}
function latestTs(a) {
  const t = a.latest && a.latest.date_utc ? Date.parse(a.latest.date_utc) : NaN;
  return Number.isNaN(t) ? 0 : t;
}
function isNewAcc(a) {
  return latestTs(a) > readMapNow[a.username];
}
function elapsedText() {
  if (!lastCheckIso) return "";
  const ms = Date.now() - Date.parse(lastCheckIso);
  if (Number.isNaN(ms) || ms < 0) return "";
  const totalMin = Math.floor(ms / 60000);
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  const nf = new Intl.NumberFormat("fa-IR");
  if (totalMin < 1) return "همین الان بررسی شده";
  if (h === 0) return nf.format(m) + " دقیقه از آخرین بررسی گذشته";
  return nf.format(h) + " ساعت و " + nf.format(m) + " دقیقه از آخرین بررسی گذشته";
}
function updateElapsed() {
  const el = $("#elapsed");
  if (el) el.textContent = elapsedText();
}
function timeText(value) {
  if (!value) return "هنوز بررسی نشده";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "زمان نامشخص";
  return "آخرین بررسی: " + d.toLocaleString("fa-IR", {dateStyle:"short", timeStyle:"short"});
}
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function toast(message) {
  const el = $("#toast"); el.textContent = message; el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 3000);
}
function avatarHtml(a) {
  const letter = escapeHtml((a.username || "?").charAt(0).toUpperCase());
  const img = a.avatar
    ? `<img src="${escapeHtml(a.avatar)}" alt="" loading="lazy" onerror="this.remove()">`
    : "";
  return `<div class="avatar"><span>${letter}</span>${img}</div>`;
}
function cardHtml(a) {
  const isNew = isNewAcc(a);
  const isError = a.status === "error";
  const detail = isError ? "بررسی ناموفق بود؛ بعداً دوباره امتحان کن." :
    (a.latest ? ("آخرین محتوای قابل‌شناسایی: " + timeText(a.latest.date_utc)) :
     (a.initialized ? "محتوای قابل‌شناسایی پیدا نشد." : "در انتظار بررسی اولیه…"));
  return `<article class="account ${isNew ? "is-new" : ""}">
    <span class="status-dot ${isNew ? "new" : (isError ? "error" : "")}" title="${isNew ? "محتوای جدید" : (isError ? "خطای بررسی" : "بدون مورد جدید")}"></span>
    ${avatarHtml(a)}
    <div class="account-info">
      <div class="handle">@${escapeHtml(a.username)}</div>
      <div class="subline">${escapeHtml(detail)}</div>
    </div>
    <div class="actions">
      ${isNew ? `<button class="clear-btn" data-clear="${escapeHtml(a.username)}">خواندم</button>` : ""}
      <a class="open-link" href="${escapeHtml(a.url)}" target="_blank" rel="noopener noreferrer">باز کردن ↗</a>
    </div>
  </article>`;
}
async function loadCategories() {
  try {
    const r = await fetch("/static/categories.json?t=" + Date.now(), {cache:"no-store"});
    if (!r.ok) throw new Error("cats");
    const data = await r.json();
    if (!data || typeof data !== "object" || Array.isArray(data)) return {};
    const clean = {};
    Object.keys(data).forEach(k => { clean[k] = Array.isArray(data[k]) ? data[k] : []; });
    return clean;
  } catch {
    return categories;
  }
}
function render() {
  if (!lastData) return;
  const accs = lastData.accounts;
  const names = Object.keys(categories);

  if (selectedCat !== "all" && !categories[selectedCat]) selectedCat = "all";

  const chips = [{key: "all", label: "همه", users: null}].concat(
    names.map(n => ({key: n, label: n, users: new Set(categories[n].map(u => String(u).toLowerCase()))}))
  );

  if (names.length === 0) {
    chipsEl.classList.add("hidden");
    chipsEl.innerHTML = "";
  } else {
    chipsEl.classList.remove("hidden");
    chipsEl.innerHTML = chips.map(c => {
      const inCat = c.users ? accs.filter(a => c.users.has(a.username.toLowerCase())) : accs;
      const hasNew = inCat.some(isNewAcc);
      return `<button class="chip ${c.key === selectedCat ? "on" : ""}" data-cat="${escapeHtml(c.key)}" type="button">${escapeHtml(c.label)}${hasNew ? '<span class="chip-dot"></span>' : ""}</button>`;
    }).join("");
  }

  const sel = chips.find(c => c.key === selectedCat) || chips[0];
  const shown = sel.users ? accs.filter(a => sel.users.has(a.username.toLowerCase())) : accs;
  list.innerHTML = shown.length
    ? shown.map(cardHtml).join("")
    : '<div class="empty">هنوز پیجی برای این دسته مشخص نشده.</div>';
}
async function loadStatus() {
  try {
    const response = await fetch("/api/status", {cache:"no-store"});
    if (!response.ok) throw new Error("status");
    const data = await response.json();
    if (window.Didban) Didban.merge(data);
    accountsCache = data.accounts;

    let readMap = loadRead();
    const firstRun = !readMap;
    if (!readMap) readMap = {};
    const baseline = Date.now() - 24 * 3600 * 1000;
    data.accounts.forEach(a => {
      if (readMap[a.username] === undefined) {
        readMap[a.username] = firstRun ? baseline : latestTs(a);
      }
    });
    saveRead(readMap);
    readMapNow = readMap;
    categories = await loadCategories();
    lastData = data;

    $("#accountCount").textContent = new Intl.NumberFormat("fa-IR").format(data.accounts.length);
    $("#lastCheck").textContent = data.last_check ? timeText(data.last_check) : "هنوز بررسی اولیه انجام نشده";
    lastCheckIso = data.last_check || null;
    updateElapsed();
    $("#statusLine").textContent = data.last_error ? "بعضی پیج‌ها بررسی نشدند؛ اینستاگرام ممکن است محدودیت گذاشته باشد." : "اتصال داشبورد برقرار است.";
    render();
  } catch (e) {
    $("#statusLine").textContent = "به سرور داشبورد وصل نشدیم.";
    list.innerHTML = '<div class="empty">سرور در دسترس نیست. مطمئن شو برنامه روی رایانه/سرور اجراست.</div>';
  }
}
checkBtn.addEventListener("click", async () => {
  checkBtn.disabled = true; checkBtn.textContent = "در حال بررسی…";
  try {
    const body = {};
    if (window.Didban) {
      const last = Didban.lastCheck();
      if (last) body.since_iso = last;
    }
    const r = await fetch("/api/check", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body)
    });
    if (!r.ok) throw new Error("check");
    toast("بررسی تمام شد؛ وضعیت فهرست به‌روز شد.");
    await loadStatus();
  } catch {
    toast("بررسی انجام نشد. ممکن است اینستاگرام درخواست را محدود کرده باشد.");
  } finally {
    checkBtn.disabled = false; checkBtn.textContent = "بررسی دوباره";
  }
});
chipsEl.addEventListener("touchstart", (e) => e.stopPropagation(), {passive: true});
chipsEl.addEventListener("click", (event) => {
  const btn = event.target.closest("[data-cat]");
  if (!btn) return;
  selectedCat = btn.dataset.cat;
  try { localStorage.setItem(CAT_KEY, selectedCat); } catch {}
  render();
});
list.addEventListener("click", async (event) => {
  const btn = event.target.closest("[data-clear]");
  if (!btn) return;
  const username = btn.dataset.clear;
  const acc = accountsCache.find(a => a.username === username);
  const readMap = loadRead() || {};
  readMap[username] = Math.max(readMap[username] || 0, acc ? latestTs(acc) : 0);
  saveRead(readMap);
  fetch("/api/clear/" + encodeURIComponent(username), {method:"POST"}).catch(() => {});
  await loadStatus();
  toast("علامت محتوای جدید پاک شد.");
});
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault(); installPrompt = e; $("#installBtn").classList.remove("hidden");
});
$("#installBtn").addEventListener("click", async () => {
  if (!installPrompt) return;
  installPrompt.prompt(); await installPrompt.userChoice;
  installPrompt = null; $("#installBtn").classList.add("hidden");
});
if ("serviceWorker" in navigator && location.protocol.startsWith("http")) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/static/sw.js").catch(()=>{}));
}
// The "time since last check" is computed from the clock each time, so it is
// always correct after a refresh or after coming back to the app.
setInterval(updateElapsed, 15000);
document.addEventListener("visibilitychange", updateElapsed);
window.addEventListener("focus", updateElapsed);
window.addEventListener("pageshow", updateElapsed);
loadStatus();
setInterval(loadStatus, 60000);
    

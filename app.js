const $ = (s) => document.querySelector(s);
const list = $("#accounts");
const checkBtn = $("#checkBtn");
let installPrompt = null;

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
async function loadStatus() {
  try {
    const response = await fetch("/api/status", {cache:"no-store"});
    if (!response.ok) throw new Error("status");
    const data = await response.json();
    $("#accountCount").textContent = new Intl.NumberFormat("fa-IR").format(data.accounts.length);
    $("#lastCheck").textContent = data.last_check ? timeText(data.last_check) : "هنوز بررسی اولیه انجام نشده";
    $("#statusLine").textContent = data.last_error ? "بعضی پیج‌ها بررسی نشدند؛ اینستاگرام ممکن است محدودیت گذاشته باشد." : "اتصال داشبورد برقرار است.";
    list.innerHTML = data.accounts.map(a => {
      const isNew = !!a.has_new;
      const isError = a.status === "error";
      let detail = isError ? "بررسی ناموفق بود؛ بعداً دوباره امتحان کن." :
        (a.latest ? ("آخرین محتوای قابل‌شناسایی: " + timeText(a.latest.date_utc)) :
         (a.initialized ? "محتوای قابل‌شناسایی پیدا نشد." : "در انتظار بررسی اولیه…"));
      return `<article class="account">
        <span class="status-dot ${isNew ? "new" : (isError ? "error" : "")}" title="${isNew ? "محتوای جدید" : (isError ? "خطای بررسی" : "بدون مورد جدید")}"></span>
        <div class="account-info">
          <div class="handle">@${escapeHtml(a.username)}</div>
          <div class="subline">${escapeHtml(detail)}</div>
        </div>
        <div class="actions">
          ${isNew ? `<button class="clear-btn" data-clear="${escapeHtml(a.username)}">خواندم</button>` : ""}
          <a class="open-link" href="${escapeHtml(a.url)}" target="_blank" rel="noopener noreferrer">باز کردن ↗</a>
        </div>
      </article>`;
    }).join("");
  } catch (e) {
    $("#statusLine").textContent = "به سرور داشبورد وصل نشدیم.";
    list.innerHTML = '<div class="empty">سرور در دسترس نیست. مطمئن شو برنامه روی رایانه/سرور اجراست.</div>';
  }
}
checkBtn.addEventListener("click", async () => {
  checkBtn.disabled = true; checkBtn.textContent = "در حال بررسی…";
  try {
    const r = await fetch("/api/check", {method:"POST"});
    if (!r.ok) throw new Error("check");
    toast("بررسی تمام شد؛ وضعیت فهرست به‌روز شد.");
    await loadStatus();
  } catch {
    toast("بررسی انجام نشد. ممکن است اینستاگرام درخواست را محدود کرده باشد.");
  } finally {
    checkBtn.disabled = false; checkBtn.textContent = "بررسی دوباره";
  }
});
list.addEventListener("click", async (event) => {
  const btn = event.target.closest("[data-clear]");
  if (!btn) return;
  try {
    await fetch("/api/clear/" + encodeURIComponent(btn.dataset.clear), {method:"POST"});
    await loadStatus(); toast("علامت محتوای جدید پاک شد.");
  } catch { toast("پاک‌کردن وضعیت انجام نشد."); }
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
loadStatus();
setInterval(loadStatus, 60000);

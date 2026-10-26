"use strict";
const $ = (id) => document.getElementById(id);
let adminKey = "";
let cursor = null;
let selected = null;
let controller = null;
function status(id, message, kind = "") {
  $(id).textContent = message;
  $(id).className = `status ${kind ? `is-${kind}` : ""}`;
}
async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(path, { ...options, credentials: "omit", headers: {
      ...(options.headers || {}), Authorization: `Bearer ${adminKey}`,
    } });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new Error("연결을 확인하고 다시 시도해 주세요.");
  }
  const data = response.status === 204 ? {} : await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401) logout(false);
    throw new Error(data.error || "요청을 처리하지 못했습니다.");
  }
  return data;
}
function logout(announce = true) {
  adminKey = "";
  if (controller) controller.abort();
  controller = null;
  $("admin-key").value = "";
  $("admin-form").hidden = false;
  $("logout-button").hidden = true;
  $("admin-refresh").hidden = true;
  $("admin-more").hidden = true;
  $("admin-notes").replaceChildren();
  status("admin-feed-status", "인증하면 메시지 목록이 표시됩니다.");
  if (announce) { status("auth-status", "관리를 종료했습니다."); $("admin-key").focus(); }
}
function buildNote(item) {
  const article = document.createElement("article"); article.className = "note";
  const header = document.createElement("div"); header.className = "note-header";
  const name = document.createElement("h3"); name.textContent = item.name;
  const date = document.createElement("time"); date.dateTime = item.created_at;
  date.textContent = new Intl.DateTimeFormat("ko-KR", { dateStyle: "medium", timeZone: "Asia/Seoul" }).format(new Date(item.created_at));
  header.append(name, date);
  const content = document.createElement("p"); content.textContent = item.message;
  const remove = document.createElement("button"); remove.type = "button"; remove.className = "delete-note";
  remove.textContent = "삭제"; remove.setAttribute("aria-label", `${item.name}님의 메시지 삭제`);
  remove.addEventListener("click", () => {
    selected = item;
    $("delete-description").textContent = `${item.name}님의 메시지를 삭제합니다.`;
    $("delete-dialog").showModal(); $("cancel-delete").focus();
  });
  article.append(header, content, remove); return article;
}
async function load(append = false) {
  if (!adminKey || (append && cursor === null)) return;
  if (controller) controller.abort();
  const current = new AbortController(); controller = current;
  $("admin-more").disabled = true;
  $("admin-refresh").disabled = true;
  status("admin-feed-status", "목록을 불러오고 있습니다.");
  try {
    const data = await request(`/api/guestbook?limit=20${append ? `&before=${cursor}` : ""}`, { signal: current.signal });
    if (controller !== current || !adminKey) return;
    if (!append) $("admin-notes").replaceChildren();
    data.items.forEach((item) => $("admin-notes").append(buildNote(item)));
    cursor = data.next_before;
    $("admin-more").hidden = cursor === null;
    status("admin-feed-status", !append && data.items.length === 0 ? "등록된 메시지가 없습니다." : "");
  } catch (error) {
    if (error.name !== "AbortError") status("admin-feed-status", error.message, "error");
  } finally {
    $("admin-more").disabled = false;
    $("admin-refresh").disabled = false;
  }
}
$("admin-form").addEventListener("submit", async (event) => {
  event.preventDefault(); adminKey = $("admin-key").value.trim();
  $("login-button").disabled = true;
  status("auth-status", "인증을 확인하고 있습니다.");
  try {
    await request("/api/admin/check");
    $("admin-key").value = "";
    $("admin-form").hidden = true;
    $("logout-button").hidden = false;
    $("admin-refresh").hidden = false;
    status("auth-status", "관리자 인증이 완료되었습니다.", "success");
    await load(); $("logout-button").focus();
  } catch (error) { adminKey = ""; status("auth-status", error.message, "error"); }
  finally { $("login-button").disabled = false; }
});
$("logout-button").addEventListener("click", () => logout());
$("admin-refresh").addEventListener("click", () => load());
$("admin-more").addEventListener("click", () => load(true));
$("cancel-delete").addEventListener("click", () => $("delete-dialog").close());
$("delete-dialog").addEventListener("close", () => { selected = null; });
$("confirm-delete").addEventListener("click", async () => {
  if (!selected) return;
  const item = selected;
  $("confirm-delete").disabled = true;
  try {
    await request(`/api/guestbook/${item.id}`, { method: "DELETE" });
    $("delete-dialog").close();
    await load(); status("auth-status", "메시지를 삭제했습니다.", "success");
  } catch (error) {
    $("delete-dialog").close(); status("auth-status", error.message, "error");
  } finally { $("confirm-delete").disabled = false; }
});
window.addEventListener("pagehide", () => logout(false));

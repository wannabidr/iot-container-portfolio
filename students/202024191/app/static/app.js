"use strict";

const $ = (id) => document.getElementById(id);
const menu = document.querySelector(".menu-toggle");
menu.addEventListener("click", () => {
  const open = menu.getAttribute("aria-expanded") !== "true";
  menu.setAttribute("aria-expanded", String(open));
  $("main-nav").classList.toggle("is-open", open);
});
$("main-nav").addEventListener("click", (event) => {
  if (event.target.closest("a")) {
    menu.setAttribute("aria-expanded", "false");
    $("main-nav").classList.remove("is-open");
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && menu.getAttribute("aria-expanded") === "true") {
    menu.setAttribute("aria-expanded", "false");
    $("main-nav").classList.remove("is-open");
    menu.focus();
  }
});

function status(id, message, kind = "") {
  $(id).textContent = message;
  $(id).className = `status ${kind ? `is-${kind}` : ""}`;
}

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, { ...options, credentials: "omit" });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new Error("연결을 확인하고 다시 시도해 주세요.");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.");
  return data;
}

function noteElement(item) {
  const note = document.createElement("article");
  note.className = "note";
  const header = document.createElement("div");
  header.className = "note-header";
  const name = document.createElement("h4");
  name.textContent = item.name;
  const time = document.createElement("time");
  time.dateTime = item.created_at;
  time.textContent = new Intl.DateTimeFormat("ko-KR", {
    year: "numeric", month: "2-digit", day: "2-digit", timeZone: "Asia/Seoul",
  }).format(new Date(item.created_at));
  header.append(name, time);
  const body = document.createElement("p");
  body.textContent = item.message; // Guest content is text, never HTML.
  note.append(header, body);
  return note;
}

let cursor = null;
let listController;
async function loadNotes(append = false) {
  if (append && cursor === null) return;
  if (listController) listController.abort();
  const controller = new AbortController();
  listController = controller;
  $("load-more").disabled = true;
  $("refresh-notes").disabled = true;
  status("feed-status", "메시지를 불러오고 있습니다.");
  try {
    const data = await api(`/api/guestbook${append ? `?before=${cursor}` : ""}`, { signal: controller.signal });
    if (listController !== controller) return;
    const list = $("notes-list");
    if (!append) list.replaceChildren();
    if (!append && data.items.length === 0) {
      const empty = document.createElement("div");
      empty.className = "empty-notes";
      const mark = document.createElement("span"); mark.className = "empty-mark"; mark.textContent = "“";
      mark.setAttribute("aria-hidden", "true");
      const title = document.createElement("h4"); title.textContent = "첫 번째 이야기를 기다립니다.";
      const text = document.createElement("p"); text.textContent = "짧은 인사도 반갑습니다. 편하게 남겨 주세요.";
      empty.append(mark, title, text); list.append(empty);
    } else {
      data.items.forEach((item) => list.append(noteElement(item)));
    }
    cursor = data.next_before;
    $("load-more").hidden = cursor === null;
    status("feed-status", "");
  } catch (error) {
    if (error.name !== "AbortError") status("feed-status", `${error.message} 새로고침으로 다시 불러올 수 있습니다.`, "error");
  } finally {
    if (listController === controller) {
      $("load-more").disabled = false;
      $("refresh-notes").disabled = false;
    }
  }
}

$("guest-message").addEventListener("input", () => {
  $("message-count").textContent = `${Array.from($("guest-message").value).length} / 500`;
});
$("guestbook-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = $("guest-name").value.trim();
  const message = $("guest-message").value.trim();
  if (!name || !message) {
    status("form-status", "이름과 메시지를 모두 입력해 주세요.", "error");
    (!name ? $("guest-name") : $("guest-message")).focus();
    return;
  }
  $("submit-note").disabled = true;
  status("form-status", "메시지를 남기고 있습니다.");
  try {
    await api("/api/guestbook", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, message }),
    });
    $("guest-message").value = "";
    $("message-count").textContent = "0 / 500";
    status("form-status", "메시지를 남겼습니다. 이야기해 주셔서 감사합니다.", "success");
    await loadNotes();
  } catch (error) {
    status("form-status", error.message, "error");
  } finally {
    $("submit-note").disabled = false;
  }
});
$("refresh-notes").addEventListener("click", () => loadNotes());
$("load-more").addEventListener("click", () => loadNotes(true));

async function loadConfig() {
  try {
    const config = await api("/api/config");
    document.querySelectorAll("[data-profile-name]").forEach((el) => { el.textContent = config.name; });
    $("guestbook-title").textContent = config.guestbookTitle;
    $("guestbook-description").textContent = config.guestbookDescription;
  } catch (_) {
    // The complete static portfolio remains readable if the API is unavailable.
  }
}
Promise.allSettled([loadConfig(), loadNotes()]);

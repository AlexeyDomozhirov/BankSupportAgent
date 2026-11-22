const log = document.querySelector("#chat-log");
const form = document.querySelector("#chat-form");
const input = document.querySelector("#chat-input");
const sendButton = document.querySelector("#chat-send");
const status = document.querySelector("#chat-status");
const note = document.querySelector("#chat-note");
const caseSelect = document.querySelector("#case-select");
const resetButton = document.querySelector("#chat-reset");

const POLL_INTERVAL_MS = 900;
const POLL_TIMEOUT_MS = 6 * 60 * 1000;
const DEFAULT_NOTE = "Выберите обращение и отправьте сообщение.";
const FOLLOWUP_NOTE = "Ассистент отвечает по выбранному обращению.";

const timeFormat = new Intl.DateTimeFormat("ru", { hour: "2-digit", minute: "2-digit" });

const welcomeTemplate = log.firstElementChild.cloneNode(true);
let cases = [];
let sessionId = newId();
let started = false;
let busy = false;

function newId() {
  if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
  return `s-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function timeNow() {
  return timeFormat.format(new Date());
}

function scrollToEnd() {
  log.scrollTop = log.scrollHeight;
}

function stamp(row) {
  if (!row.classList.contains("msg") || row.querySelector(".msg__time")) return;
  const time = document.createElement("time");
  time.className = "msg__time";
  time.textContent = timeNow();
  row.append(time);
}

function addMessage(text, author) {
  const row = document.createElement("div");
  row.className = `msg msg--${author}`;

  const bubble = document.createElement("div");
  bubble.className = "msg__bubble";

  const paragraph = document.createElement("p");
  paragraph.textContent = text;

  bubble.append(paragraph);
  row.append(bubble);
  stamp(row);
  log.append(row);
  scrollToEnd();
}

function addTyping() {
  const row = document.createElement("div");
  row.className = "msg msg--bot";
  row.dataset.typing = "true";

  const bubble = document.createElement("div");
  bubble.className = "msg__bubble msg__bubble--typing";
  bubble.setAttribute("aria-label", "Ассистент печатает");

  for (let index = 0; index < 3; index += 1) {
    const dot = document.createElement("span");
    dot.className = "dot";
    bubble.append(dot);
  }

  row.append(bubble);
  log.append(row);
  scrollToEnd();
  return row;
}

function resizeInput() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 128)}px`;
}

function setStatus(text, state) {
  status.textContent = text;
  if (state) {
    status.dataset.state = state;
  } else {
    delete status.dataset.state;
  }
}

function setNote(text, state) {
  note.textContent = text;
  if (state) {
    note.dataset.state = state;
  } else {
    delete note.dataset.state;
  }
}

function syncState() {
  const hasText = input.value.trim() !== "";
  const hasCase = started || caseSelect.value !== "";
  sendButton.disabled = busy || !hasText || !hasCase;
}

async function requestJson(url, options) {
  const response = await fetch(url, options);
  let data = null;
  try {
    data = await response.json();
  } catch {
    // ответ не JSON - ниже отдадим ошибку по статусу
  }
  if (!response.ok) {
    throw new Error(data?.error || `Ошибка HTTP ${response.status}.`);
  }
  return data;
}

async function loadCases() {
  try {
    const data = await requestJson("/api/cases");
    cases = data.cases || [];
    if (!cases.length) throw new Error("В песочнице нет доступных обращений.");

    caseSelect.replaceChildren(...cases.map((item) => new Option(item.title, item.id)));
    caseSelect.disabled = false;
    setNote(DEFAULT_NOTE);
  } catch (error) {
    caseSelect.replaceChildren(new Option("Недоступно", ""));
    caseSelect.disabled = true;
    setNote(`Не удалось загрузить обращения. ${error.message}`, "error");
  }
  syncState();
}

function prefillCase() {
  if (input.value.trim() !== "") return;
  const current = cases.find((item) => item.id === caseSelect.value);
  if (!current || !current.customer_message) return;
  input.value = current.customer_message;
  resizeInput();
  syncState();
}

function wait(ms) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

async function pollJob(jobId) {
  const deadline = Date.now() + POLL_TIMEOUT_MS;

  while (Date.now() < deadline) {
    await wait(POLL_INTERVAL_MS);
    const data = await requestJson(`/api/chat/${jobId}`);

    if (data.status === "error") {
      throw new Error(data.error || "Не удалось получить ответ.");
    }
    if (data.status === "done") {
      return data;
    }
    if (data.stage) {
      setNote(data.stage);
    }
  }

  throw new Error("Превышено время ожидания ответа.");
}

async function sendMessage(text) {
  const caseId = started ? null : caseSelect.value;

  busy = true;
  caseSelect.disabled = true;
  syncState();

  addMessage(text, "user");
  input.value = "";
  resizeInput();
  syncState();

  const typing = addTyping();
  setStatus("печатает…", "typing");
  setNote(started ? "Ассистент готовит ответ…" : "Запускаю расследование по обращению…");

  try {
    const data = await requestJson("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, case_id: caseId, message: text }),
    });

    const result = await pollJob(data.job_id);
    typing.remove();
    addMessage(result.answer, "bot");
    started = true;
    setStatus("на связи");
    setNote(FOLLOWUP_NOTE);
  } catch (error) {
    typing.remove();
    setStatus("на связи");
    setNote(error.message, "error");
  } finally {
    busy = false;
    caseSelect.disabled = started;
    syncState();
    input.focus();
  }
}

function resetChat() {
  sessionId = newId();
  started = false;
  busy = false;

  log.replaceChildren(welcomeTemplate.cloneNode(true));
  log.querySelectorAll(".msg").forEach(stamp);

  input.value = "";
  resizeInput();
  caseSelect.disabled = cases.length === 0;
  setStatus("на связи");
  setNote(cases.length ? DEFAULT_NOTE : "Список обращений недоступен.");
  syncState();
  input.focus();
}

form.addEventListener("submit", (event) => {
  event.preventDefault();

  const text = input.value.trim();
  if (text === "" || busy) return;

  if (!started && caseSelect.value === "") {
    setNote("Сначала выберите обращение.", "error");
    return;
  }

  sendMessage(text);
});

input.addEventListener("input", () => {
  resizeInput();
  syncState();
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    form.requestSubmit();
  }
});

caseSelect.addEventListener("change", prefillCase);
resetButton.addEventListener("click", resetChat);

log.querySelectorAll(".msg").forEach(stamp);
syncState();
loadCases();

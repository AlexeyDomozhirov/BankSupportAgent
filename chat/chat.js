const log = document.querySelector("#chat-log");
const form = document.querySelector("#chat-form");
const input = document.querySelector("#chat-input");
const sendButton = document.querySelector("#chat-send");
const status = document.querySelector("#chat-status");

const replies = [
  "Спасибо, сообщение получил. Это демонстрационный ответ: логика ассистента ещё не подключена.",
  "Принял. Пока я работаю в демонстрационном режиме и отвечаю заглушками.",
  "Записал обращение. В рабочей версии здесь появится разбор операций и рекомендации по вашему случаю.",
];

const timeFormat = new Intl.DateTimeFormat("ru", { hour: "2-digit", minute: "2-digit" });

let replyIndex = 0;
let pending = false;

function timeNow() {
  return timeFormat.format(new Date());
}

function scrollToEnd() {
  log.scrollTop = log.scrollHeight;
}

function stamp(row) {
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

function syncState() {
  sendButton.disabled = pending || input.value.trim() === "";
}

function startReply() {
  pending = true;
  status.textContent = "печатает…";
  status.dataset.state = "typing";
  syncState();

  const typing = addTyping();
  const delay = 700 + Math.random() * 700;

  window.setTimeout(() => {
    typing.remove();
    addMessage(replies[replyIndex % replies.length], "bot");
    replyIndex += 1;
    pending = false;
    status.textContent = "на связи";
    delete status.dataset.state;
    syncState();
    input.focus();
  }, delay);
}

form.addEventListener("submit", (event) => {
  event.preventDefault();

  const text = input.value.trim();
  if (text === "" || pending) return;

  addMessage(text, "user");
  input.value = "";
  resizeInput();
  syncState();
  startReply();
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

document.querySelectorAll(".msg").forEach((row) => {
  if (!row.querySelector(".msg__time")) stamp(row);
});

syncState();
scrollToEnd();

const form = document.querySelector("#run-form");
const caseField = document.querySelector("#cases");
const caseOptions = document.querySelector("#case-options");
const reloadButton = document.querySelector("#reload-cases");
const runButton = document.querySelector("#run");
const runAllButton = document.querySelector("#run-all");
const status = document.querySelector("#status");
const clearRunsButton = document.querySelector("#clear-runs");
const stopRunsButton = document.querySelector("#stop-runs");
const runTabs = document.querySelector("#run-tabs");
const queueStatus = document.querySelector("#queue-status");
const runView = document.querySelector("#run-view");
const runStatus = document.querySelector("#run-status");
const resultPanel = document.querySelector("#result");
const toolLog = document.querySelector("#tool-log");
const toolLogStatus = document.querySelector("#tool-log-status");
const toolCalls = document.querySelector("#tool-calls");
const durationFormat = new Intl.NumberFormat("ru", { maximumFractionDigits: 2 });

const statusLabels = {
  queued: "В очереди",
  running: "Выполняется",
  stopping: "Останавливается",
  stopped: "Остановлен",
  completed: "Завершён",
  failed: "Ошибка",
};
const tabs = new Map();
let view;
let nextSelectedId;
let submitting = false;
let updatingRuns = false;
let historyRevision = 0;
let runActionError = "";
const finishedStatuses = new Set(["completed", "failed", "stopped"]);

async function requestJson(url, options) {
  const response = await fetch(url, options);
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error("Откройте панель через debug_server.py.");
  }
  if (!response.ok) {
    throw new Error(data.error || `Ошибка HTTP ${response.status}.`);
  }
  return data;
}

async function loadCases() {
  reloadButton.hidden = true;
  setStatus("Загружаем список кейсов…", "running");
  try {
    const cases = await requestJson("/api/cases");
    if (!cases.length) throw new Error("В песочнице нет доступных кейсов.");

    const options = cases.map(({ id, title }) => {
      const label = document.createElement("label");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.name = "case_ids";
      checkbox.value = id;
      label.append(checkbox, title);
      return label;
    });
    caseOptions.replaceChildren(...options);
    caseField.disabled = false;
    syncSubmit();
    setStatus("Готово к запуску.", "ready");
  } catch (error) {
    setStatus(error.message, "error");
    reloadButton.hidden = false;
  }
}

function renderList(selector, values) {
  const items = (values.length ? values : ["Нет данных"]).map((value) => {
    const item = document.createElement("li");
    item.textContent = value;
    return item;
  });
  document.querySelector(selector).replaceChildren(...items);
}

function renderMetrics(metrics) {
  const nodes = Object.entries(metrics).flatMap(([key, value]) => {
    const label = document.createElement("dt");
    label.textContent = key;
    const entry = document.createElement("dd");
    entry.textContent = typeof value === "object" ? JSON.stringify(value) : String(value);
    return [label, entry];
  });
  document.querySelector("#metrics").replaceChildren(...nodes);
}

function showResult(result) {
  document.querySelector("#run-id").textContent = result.run_id;
  document.querySelector("#case-id").textContent = result.case_id;
  document.querySelector("#duration").textContent = `${durationFormat.format(result.duration_ms / 1000)} с`;
  document.querySelector("#question").textContent = result.question;
  document.querySelector("#answer").textContent = result.answer;
  renderList("#evidence", result.evidence);
  renderList("#actions", result.actions);
  renderMetrics(result.metrics);
  document.querySelector("#raw-result").textContent = JSON.stringify(result, null, 2);
  resultPanel.hidden = false;
}

function setStatus(message, state) {
  status.textContent = message;
  status.dataset.state = state;
}

function syncSubmit() {
  runButton.disabled = submitting || !caseOptions.querySelector(":checked");
  runAllButton.disabled = submitting || !caseOptions.querySelector('input[name="case_ids"]');
}

function selectRun(jobId) {
  if (view?.jobId === jobId) return;
  view = { jobId, seen: new Set(), loading: false, complete: false };
  for (const [id, { button }] of tabs) {
    button.setAttribute("aria-selected", String(id === jobId));
    button.tabIndex = id === jobId ? 0 : -1;
  }
  runView.setAttribute("aria-labelledby", tabs.get(jobId).button.id);
  runView.hidden = false;
  resultPanel.hidden = true;
  resultPanel.querySelector("details").open = false;
  toolLog.hidden = false;
  toolCalls.replaceChildren();
  toolLogStatus.textContent = "Подключаем журнал…";
  runStatus.dataset.state = "";
  runStatus.textContent = "Загружаем данные прогона…";
  refreshRun(view);
}

function renderRuns(runs) {
  const ids = new Set(runs.map((run) => run.job_id));
  for (const [id, { button }] of tabs) {
    if (!ids.has(id)) {
      button.remove();
      tabs.delete(id);
    }
  }
  runTabs.hidden = runs.length === 0;
  clearRunsButton.disabled = updatingRuns || !runs.some((run) => finishedStatuses.has(run.status));
  stopRunsButton.disabled = updatingRuns || !runs.some((run) => run.status === "queued" || run.status === "running");
  queueStatus.textContent = runActionError || (runs.length ? "" : "История прогонов пуста.");
  if (!runs.length) {
    view = undefined;
    runView.hidden = true;
    return;
  }

  runs.forEach(({ job_id: id, title, status: state }, index) => {
    if (!tabs.has(id)) {
      const button = document.createElement("button");
      const caption = document.createTextNode("");
      const label = document.createElement("small");
      button.id = `run-tab-${id}`;
      button.type = "button";
      button.setAttribute("role", "tab");
      button.setAttribute("aria-controls", "run-view");
      button.setAttribute("aria-selected", "false");
      button.tabIndex = -1;
      button.append(caption, label);
      button.addEventListener("click", () => selectRun(id));
      runTabs.append(button);
      tabs.set(id, { button, caption, label });
    }
    const { caption, label } = tabs.get(id);
    const titleText = `№ ${index + 1} · ${title}`;
    if (caption.data !== titleText) caption.data = titleText;
    if (label.textContent !== statusLabels[state]) label.textContent = statusLabels[state];
  });

  if (ids.has(nextSelectedId)) {
    selectRun(nextSelectedId);
    nextSelectedId = undefined;
  } else if (!ids.has(view?.jobId)) {
    selectRun((runs.find((run) => run.status === "running") ?? runs.at(-1)).job_id);
  }
}

function renderToolCalls(run, seen) {
  for (const call of run.tool_calls) {
    if (seen.has(call.id)) continue;
    seen.add(call.id);
    const row = toolCalls.insertRow();
    if (call.status_code >= 400) row.dataset.httpError = "";
    for (const value of [call.method, call.path, call.status_code, `${call.duration_ms} мс`]) {
      row.insertCell().textContent = value;
    }
  }
  const label = run.trace_error || (seen.size
    ? `Записано вызовов: ${seen.size}.`
    : run.status === "running" ? "Ожидаем первый вызов инструмента…" : "Вызовы инструментов не записаны.");
  if (toolLogStatus.textContent !== label) toolLogStatus.textContent = label;
}

async function refreshRun(current) {
  if (!current || current.loading || current.complete) return;
  current.loading = true;
  try {
    const run = await requestJson(`/api/runs/${encodeURIComponent(current.jobId)}`);
    if (current !== view) return;
    toolLog.hidden = run.status === "queued";
    renderToolCalls(run, current.seen);
    const passed = run.result?.evaluation?.passed;
    const outcome = passed === true ? " · Кейс пройден" : passed === false ? " · Кейс не пройден" : "";
    const label = run.error ? `Ошибка. ${run.error}` : statusLabels[run.status] + outcome;
    runStatus.dataset.state = run.status;
    if (runStatus.textContent !== label) runStatus.textContent = label;
    if (run.result) showResult(run.result);
    current.complete = finishedStatuses.has(run.status) && !run.trace_error;
  } catch {
    if (current === view) runStatus.textContent = "Не удалось обновить данные прогона. Повторяем запрос…";
  } finally {
    current.loading = false;
  }
}

async function refreshRuns() {
  const revision = historyRevision;
  try {
    if (updatingRuns) return;
    const runs = await requestJson("/api/runs");
    if (revision !== historyRevision || updatingRuns) return;
    renderRuns(runs);
    refreshRun(view);
  } catch {
    if (revision === historyRevision && !updatingRuns) {
      queueStatus.textContent = "Не удалось обновить список прогонов. Повторяем запрос…";
    }
  } finally {
    setTimeout(refreshRuns, 1000);
  }
}

async function updateRuns(path, errorLabel) {
  if (updatingRuns) return false;
  updatingRuns = true;
  historyRevision += 1;
  const clearWasDisabled = clearRunsButton.disabled;
  const stopWasDisabled = stopRunsButton.disabled;
  clearRunsButton.disabled = true;
  stopRunsButton.disabled = true;
  runActionError = "";
  try {
    const runs = await requestJson(path, { method: "DELETE" });
    updatingRuns = false;
    renderRuns(runs);
    refreshRun(view);
    return true;
  } catch (error) {
    updatingRuns = false;
    runActionError = `${errorLabel} ${error.message}`;
    queueStatus.textContent = runActionError;
    clearRunsButton.disabled = clearWasDisabled;
    stopRunsButton.disabled = stopWasDisabled;
    return false;
  }
}

clearRunsButton.addEventListener("click", async () => {
  if (await updateRuns("/api/runs", "Не удалось очистить историю.")) {
    for (const checkbox of caseOptions.querySelectorAll(":checked")) {
      checkbox.checked = false;
    }
    syncSubmit();
    setStatus("Добавлено прогонов: 0.", "ready");
  }
});
stopRunsButton.addEventListener("click", () => updateRuns("/api/runs/active", "Не удалось остановить прогоны."));

runTabs.addEventListener("keydown", (event) => {
  const keys = ["ArrowLeft", "ArrowRight", "Home", "End"];
  if (!keys.includes(event.key) || !view) return;
  event.preventDefault();
  const ids = [...tabs.keys()];
  const index = ids.indexOf(view.jobId);
  let next = 0;
  if (event.key === "End") next = ids.length - 1;
  if (event.key === "ArrowRight") next = (index + 1) % ids.length;
  if (event.key === "ArrowLeft") next = (index + ids.length - 1) % ids.length;
  selectRun(ids[next]);
  tabs.get(ids[next]).button.focus();
});

form.addEventListener("change", syncSubmit);
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (submitting) return;
  const selector = event.submitter === runAllButton ? 'input[name="case_ids"]' : ":checked";
  const caseIds = [...caseOptions.querySelectorAll(selector)].map(({ value }) => value);
  if (!caseIds.length) return;
  submitting = true;
  syncSubmit();
  setStatus("Добавляем кейсы в очередь…", "running");

  try {
    const { job_ids: jobIds } = await requestJson("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ case_ids: caseIds }),
    });
    nextSelectedId = jobIds[0];
    setStatus(`Добавлено прогонов: ${jobIds.length}.`, "success");
  } catch (error) {
    setStatus(`Ошибка запуска. ${error.message}`, "error");
  } finally {
    submitting = false;
    syncSubmit();
  }
});

reloadButton.addEventListener("click", loadCases);
loadCases();
refreshRuns();

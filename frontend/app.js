const form = document.querySelector("#run-form");
const caseSelect = document.querySelector("#case");
const reloadButton = document.querySelector("#reload-cases");
const runButton = document.querySelector("#run");
const status = document.querySelector("#status");
const resultPanel = document.querySelector("#result");
const durationFormat = new Intl.NumberFormat("ru", { maximumFractionDigits: 2 });

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

    const options = cases.map(({ id, title }) => new Option(title, id));
    caseSelect.replaceChildren(...options);
    caseSelect.disabled = false;
    runButton.disabled = false;
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

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (runButton.disabled) return;

  runButton.disabled = true;
  caseSelect.disabled = true;
  resultPanel.hidden = true;
  setStatus("Кейс выполняется…", "running");

  try {
    const result = await requestJson("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ case_id: caseSelect.value }),
    });
    showResult(result);
    const passed = result.evaluation?.passed;
    const outcome = passed === true ? " Кейс пройден." : passed === false ? " Кейс не пройден." : "";
    setStatus(`Прогон завершён.${outcome}`, "success");
  } catch (error) {
    setStatus(`Ошибка запуска. ${error.message}`, "error");
  } finally {
    runButton.disabled = false;
    caseSelect.disabled = false;
  }
});

reloadButton.addEventListener("click", loadCases);
loadCases();

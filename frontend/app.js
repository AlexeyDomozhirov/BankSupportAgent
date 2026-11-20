const demoResult = {
  run_id: "demo-run-001",
  case_id: "demo-duplicate-charge",
  question: "За одну покупку на 1 490 ₽ у меня списали деньги дважды. Что делать?",
  answer: "За покупку на 1 490 ₽ прошли две операции. В демонстрационном прогоне по повторному списанию оформлен возврат, исходная покупка сохранена.",
  evidence: ["transaction:demo-original", "transaction:demo-duplicate", "knowledge_article:demo-refunds"],
  actions: ["refund_for:demo-duplicate"],
  metrics: { demo_checks_passed: 3, demo_checks_total: 3 },
  export: { mode: "demo" },
};

const form = document.querySelector("#run-form");
const caseSelect = document.querySelector("#case");
const runButton = document.querySelector("#run");
const status = document.querySelector("#status");
const resultPanel = document.querySelector("#result");
const durationFormat = new Intl.NumberFormat("ru", { maximumFractionDigits: 2 });

async function runCase(caseKey) {
  if (caseKey !== "duplicate-charge") {
    throw new Error("Демонстрационный кейс не найден.");
  }
  await new Promise((resolve) => setTimeout(resolve, 500));
  return demoResult;
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

function showResult(result, durationMs) {
  document.querySelector("#run-id").textContent = result.run_id;
  document.querySelector("#case-id").textContent = result.case_id;
  document.querySelector("#duration").textContent = `${durationFormat.format(durationMs / 1000)} с`;
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
  const start = performance.now();

  try {
    const result = await runCase(caseSelect.value);
    showResult(result, performance.now() - start);
    setStatus("Демонстрационный прогон завершён.", "success");
  } catch (error) {
    setStatus(`Ошибка запуска. ${error.message}`, "error");
  } finally {
    runButton.disabled = false;
    caseSelect.disabled = false;
  }
});

runButton.disabled = false;

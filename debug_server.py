import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread
from time import monotonic
from urllib.parse import urlsplit
from uuid import uuid4

import requests

FRONTEND = Path(__file__).resolve().parent / "frontend"
RUN_LOCK = Lock()
STATE_LOCK = Lock()
RUN_HISTORY_LIMIT = 16
LOGGER = logging.getLogger(__name__)


SANDBOX_URL = "http://127.0.0.1:8000"
CASE_HEADERS = {"X-Case-Password": "boss-cases"}


def sandbox_json(method: str, path: str, **kwargs):
    response = requests.request(method, SANDBOX_URL + path, timeout=15, **kwargs)
    response.raise_for_status()
    return response.json()


def get_cases() -> list[dict]:
    return sandbox_json("GET", "/cases", headers=CASE_HEADERS)


def create_run() -> str:
    return sandbox_json("POST", "/runs", json={"team_name": "Kapuchinator"})["id"]


def get_user_id(case: dict) -> str:
    ticket_id = case["intake"]["ticket_id"]
    return sandbox_json("GET", f"/support/tickets/{ticket_id}")["user_id"]


def evaluate_run(
    run_id: str, case_id: str, answer: str, evidence: list[str], actions: list[str]
) -> dict:
    return sandbox_json(
        "POST",
        f"/cases/{case_id}/evaluate",
        headers=CASE_HEADERS,
        json={
            "run_id": run_id,
            "case_id": case_id,
            "answer": answer,
            "evidence": evidence,
            "actions": actions,
        },
    )


def get_metrics(run_id: str) -> dict[str, object]:
    return sandbox_json("GET", f"/runs/{run_id}/metrics", headers=CASE_HEADERS)


def get_export(run_id: str) -> dict[str, object]:
    return sandbox_json("GET", f"/runs/{run_id}/export", headers=CASE_HEADERS)


def run_case(case_id: str, on_run_created: Callable[[str], None]) -> dict[str, object]:
    from agent import exec_agent

    case = sandbox_json("GET", f"/cases/{case_id}", headers=CASE_HEADERS)
    user_id = get_user_id(case)
    question = case["customer_message"]
    run_id = create_run()
    on_run_created(run_id)

    answer, evidence, actions = exec_agent(run_id, question, user_id)
    evaluation = evaluate_run(run_id, case_id, answer, evidence, actions)
    return {
        "run_id": run_id,
        "case_id": case_id,
        "question": question,
        "answer": answer,
        "evidence": evidence,
        "actions": actions,
        "evaluation": evaluation,
        "metrics": get_metrics(run_id),
        "export": get_export(run_id),
    }


@dataclass
class DashboardRun:
    case_id: str
    run_id: str | None = None
    result: dict | None = None
    error: str | None = None


RUNS: dict[str, DashboardRun] = {}


def execute_run(job: DashboardRun) -> None:
    def set_run_id(run_id: str) -> None:
        with STATE_LOCK:
            job.run_id = run_id

    result = None
    error = None
    start = monotonic()
    try:
        result = run_case(job.case_id, set_run_id)
        result["duration_ms"] = round((monotonic() - start) * 1000)
    except requests.RequestException:
        LOGGER.error("Запрос к банковской песочнице завершился ошибкой")
        error = "Ошибка запроса к банковской песочнице."
    except ValueError as exc:
        if str(exc) in (
            "GIGACHAT_CREDENTIALS not set",
            "Переменная окружения CERT_PATH не установлена",
        ):
            error = str(exc)
        else:
            error = "Не удалось обработать данные прогона."
    except Exception:  # noqa: BLE001 — ошибка фонового задания передаётся интерфейсу
        LOGGER.error("Прогон завершился ошибкой")
        error = "Не удалось завершить прогон. Проверьте настройки GigaChat и песочницы."
    finally:
        with STATE_LOCK:
            job.result = result
            job.error = error
            RUN_LOCK.release()


def run_snapshot(job: DashboardRun) -> dict:
    with STATE_LOCK:
        run_id, result, error = job.run_id, job.result, job.error

    snapshot = {
        "status": "failed"
        if error
        else "completed"
        if result is not None
        else "running",
        "run_id": run_id,
        "result": result,
        "error": error,
        "tool_calls": [],
    }
    if run_id is None:
        return snapshot

    try:
        export = result["export"] if result is not None else get_export(run_id)
        snapshot["tool_calls"] = export.get("tool_calls", [])
    except requests.RequestException:
        snapshot["trace_error"] = "Журнал временно недоступен."
    return snapshot


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=FRONTEND, **kwargs)

    def send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path.startswith("/api/runs/"):
            job_id = path.removeprefix("/api/runs/")
            with STATE_LOCK:
                job = RUNS.get(job_id)
            if job is None:
                self.send_json(404, {"error": "Прогон не найден."})
            else:
                self.send_json(200, run_snapshot(job))
            return

        if path != "/api/cases":
            super().do_GET()
            return

        try:
            cases = get_cases()
        except requests.RequestException:
            self.send_json(502, {"error": "Не удалось загрузить кейсы из песочницы."})
            return
        self.send_json(
            200,
            [{key: case[key] for key in ("id", "title")} for case in cases],
        )

    def do_POST(self):
        if urlsplit(self.path).path != "/api/runs":
            self.send_json(404, {"error": "Маршрут не найден."})
            return
        if self.headers.get_content_type() != "application/json":
            self.send_json(415, {"error": "Ожидается запрос в формате JSON."})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ValueError
            data = json.loads(self.rfile.read(length))
            case_id = data["case_id"]
            if not isinstance(case_id, str) or not case_id:
                raise ValueError
        except ValueError, KeyError, TypeError:
            self.send_json(
                400, {"error": "Передайте идентификатор кейса в поле case_id."}
            )
            return

        try:
            cases = get_cases()
        except requests.RequestException:
            self.send_json(502, {"error": "Не удалось загрузить кейсы из песочницы."})
            return
        if not any(case["id"] == case_id for case in cases):
            self.send_json(404, {"error": "Кейс не найден."})
            return

        if not RUN_LOCK.acquire(blocking=False):
            self.send_json(409, {"error": "Другой кейс уже выполняется."})
            return

        job_id = uuid4().hex
        job = DashboardRun(case_id)
        with STATE_LOCK:
            if len(RUNS) >= RUN_HISTORY_LIMIT:
                RUNS.pop(next(iter(RUNS)))
            RUNS[job_id] = job

        worker = Thread(target=execute_run, args=(job,), daemon=True)
        try:
            worker.start()
        except RuntimeError:
            with STATE_LOCK:
                RUNS.pop(job_id)
                RUN_LOCK.release()
            self.send_json(503, {"error": "Не удалось запустить кейс."})
            return
        self.send_json(202, {"job_id": job_id})


if __name__ == "__main__":
    with ThreadingHTTPServer(("127.0.0.1", 8080), DashboardHandler) as server:
        print("Панель прогонов: http://127.0.0.1:8080", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from multiprocessing import get_context
from multiprocessing.connection import Connection
from pathlib import Path
from queue import Queue
from threading import Lock, Thread
from time import monotonic
from urllib.parse import urlsplit
from uuid import uuid4

import requests

FRONTEND = Path(__file__).resolve().parent / "frontend"
STATE_LOCK = Lock()
LOGGER = logging.getLogger(__name__)
PROCESS_CONTEXT = get_context("spawn")
FINISHED_STATUSES = ("completed", "failed", "stopped")


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
    title: str
    status: str = "queued"
    run_id: str | None = None
    result: dict | None = None
    error: str | None = None


RUNS: dict[str, DashboardRun] = {}
RUN_QUEUE: Queue[DashboardRun] = Queue()


def process_queue() -> None:
    while True:
        job = RUN_QUEUE.get()
        try:
            execute_run(job)
        finally:
            RUN_QUEUE.task_done()


def run_in_process(case_id: str, channel: Connection) -> None:
    try:
        result = run_case(case_id, lambda run_id: channel.send(("run_id", run_id)))
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
    except Exception:  # noqa: BLE001 — ошибка дочернего процесса передаётся интерфейсу
        LOGGER.error("Прогон завершился ошибкой")
        error = "Не удалось завершить прогон. Проверьте настройки GigaChat и песочницы."
    else:
        channel.send(("result", result))
        return
    channel.send(("error", error))


def execute_run(job: DashboardRun) -> None:
    with STATE_LOCK:
        if job.status == "stopped":
            return
        job.status = "running"

    start = monotonic()
    result = None
    error = "Процесс прогона завершился без результата."
    process = None
    try:
        receiver, sender = PROCESS_CONTEXT.Pipe(duplex=False)
        with receiver, sender:
            process = PROCESS_CONTEXT.Process(
                target=run_in_process, args=(job.case_id, sender), daemon=True
            )
            process.start()
            sender.close()
            while True:
                with STATE_LOCK:
                    if job.status == "stopping":
                        break
                if not receiver.poll(0.1):
                    if not process.is_alive():
                        break
                    continue
                kind, payload = receiver.recv()
                if kind == "run_id":
                    with STATE_LOCK:
                        job.run_id = payload
                    continue
                if kind == "result":
                    result = payload
                    result["duration_ms"] = round((monotonic() - start) * 1000)
                    error = None
                else:
                    error = payload
                break
    except EOFError:
        pass
    except OSError, RuntimeError:
        LOGGER.error("Не удалось запустить или прочитать процесс прогона.")
        error = "Не удалось выполнить прогон в отдельном процессе."
    finally:
        if process is not None:
            if process.pid is not None:
                if result is not None:
                    process.join(timeout=0.5)
                if process.is_alive():
                    process.kill()
                process.join()
            process.close()

        with STATE_LOCK:
            if job.status == "stopping":
                job.status = "stopped"
            else:
                job.result = result
                job.error = error
                job.status = "failed" if error else "completed"


def run_snapshot(job: DashboardRun) -> dict:
    with STATE_LOCK:
        status, run_id, result, error = job.status, job.run_id, job.result, job.error

    snapshot = {
        "status": status,
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


def run_summaries() -> list[dict]:
    return [
        {
            "job_id": job_id,
            "case_id": job.case_id,
            "title": job.title,
            "status": job.status,
        }
        for job_id, job in RUNS.items()
    ]


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
        if path == "/api/runs":
            with STATE_LOCK:
                runs = run_summaries()
            self.send_json(200, runs)
            return

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

    def do_DELETE(self):
        path = urlsplit(self.path).path
        if path == "/api/runs/active":
            with STATE_LOCK:
                for job in RUNS.values():
                    if job.status == "queued":
                        job.status = "stopped"
                    elif job.status == "running":
                        job.status = "stopping"
                runs = run_summaries()
            self.send_json(200, runs)
            return

        if path != "/api/runs":
            self.send_json(404, {"error": "Маршрут не найден."})
            return

        with STATE_LOCK:
            finished = [
                job_id
                for job_id, job in RUNS.items()
                if job.status in FINISHED_STATUSES
            ]
            for job_id in finished:
                del RUNS[job_id]
            runs = run_summaries()
        self.send_json(200, runs)

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
            if not isinstance(data, dict):
                raise TypeError
            case_ids = data["case_ids"] if "case_ids" in data else [data["case_id"]]
            if not isinstance(case_ids, list) or not case_ids:
                raise ValueError
            if not all(isinstance(case_id, str) and case_id for case_id in case_ids):
                raise ValueError
        except ValueError, KeyError, TypeError:
            self.send_json(
                400, {"error": "Передайте непустой список идентификаторов в case_ids."}
            )
            return

        try:
            cases = {case["id"]: case["title"] for case in get_cases()}
        except requests.RequestException:
            self.send_json(502, {"error": "Не удалось загрузить кейсы из песочницы."})
            return
        if any(case_id not in cases for case_id in case_ids):
            self.send_json(404, {"error": "Кейс не найден."})
            return

        job_ids = []
        with STATE_LOCK:
            for case_id in case_ids:
                job_id = uuid4().hex
                job = DashboardRun(case_id, cases[case_id])
                RUNS[job_id] = job
                RUN_QUEUE.put(job)
                job_ids.append(job_id)
        self.send_json(202, {"job_ids": job_ids, "job_id": job_ids[0]})


if __name__ == "__main__":
    with ThreadingHTTPServer(("127.0.0.1", 8080), DashboardHandler) as server:
        Thread(target=process_queue, daemon=True).start()
        print("Панель прогонов: http://127.0.0.1:8080", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass

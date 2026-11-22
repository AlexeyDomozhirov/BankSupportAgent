"""HTTP-сервер чата: отдаёт chat/ и связывает его с агентом и разговорным слоем.

Существующие файлы проекта не изменяются: agent.py только импортируется.
Порт 8081, чтобы не конфликтовать с debug_server.py на 8080.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import requests

BASE_DIR = Path(__file__).resolve().parent
os.chdir(BASE_DIR)

from chat_layer import CaseReport, ChatSession
from client_snapshot import build_snapshot

HOST = "127.0.0.1"
PORT = 8081
CHAT_DIR = BASE_DIR / "chat"
SANDBOX_URL = "http://127.0.0.1:8000"
CASE_HEADERS = {"X-Case-Password": "boss-cases"}
TEAM_NAME = "Kapuchinator"

MAX_SESSIONS = 50
MAX_JOBS = 200
MAX_MESSAGE_LENGTH = 2000
JOB_TIMEOUT_SECONDS = 6 * 60

LOGGER = logging.getLogger("chat_server")

AGENT_LOCK = threading.Lock()
STATE_LOCK = threading.Lock()
SESSIONS: dict[str, ChatSession] = {}
JOBS: dict[str, dict] = {}


def sandbox_json(method: str, path: str, **kwargs):
    response = requests.request(method, SANDBOX_URL + path, timeout=20, **kwargs)
    response.raise_for_status()
    return response.json()


def list_cases() -> list[dict]:
    cases = sandbox_json("GET", "/cases", headers=CASE_HEADERS)
    return [
        {
            "id": case["id"],
            "number": case.get("number"),
            "title": case["title"],
            "customer_message": case.get("customer_message", ""),
        }
        for case in cases
    ]


def run_agent(case_id: str, question: str) -> CaseReport:
    """Полный прогон агента по кейсу: тот же путь, что в debug_server.py."""
    from agent import exec_agent

    case = sandbox_json("GET", f"/cases/{case_id}", headers=CASE_HEADERS)
    ticket_id = case["intake"]["ticket_id"]
    user_id = sandbox_json("GET", f"/support/tickets/{ticket_id}")["user_id"]
    run_id = sandbox_json("POST", "/runs", json={"team_name": TEAM_NAME})["id"]

    with AGENT_LOCK:
        answer, evidence, actions = exec_agent(run_id, question, user_id)

    facts = ""
    try:
        facts = build_snapshot(SANDBOX_URL, user_id)
    except Exception:
        LOGGER.exception("Не удалось собрать данные клиента")

    LOGGER.info(
        "Кейс %s, run %s: evidence=%d, actions=%d, facts_chars=%d",
        case_id,
        run_id,
        len(evidence),
        len(actions),
        len(facts),
    )
    return CaseReport(
        question=question,
        answer=answer,
        actions=list(actions),
        run_id=run_id,
        case_id=case_id,
        facts=facts,
    )


def _prune_jobs() -> None:
    if len(JOBS) <= MAX_JOBS:
        return
    for job_id in sorted(JOBS, key=lambda key: JOBS[key]["created_at"])[
        : len(JOBS) - MAX_JOBS
    ]:
        JOBS.pop(job_id, None)


def create_job() -> str:
    job_id = "job_" + uuid.uuid4().hex[:12]
    with STATE_LOCK:
        JOBS[job_id] = {
            "status": "queued",
            "stage": "в очереди",
            "answer": None,
            "error": None,
            "created_at": time.time(),
        }
        _prune_jobs()
    return job_id


def update_job(job_id: str, **fields) -> None:
    with STATE_LOCK:
        job = JOBS.get(job_id)
        if job is not None:
            job.update(fields)


def get_job(job_id: str) -> dict | None:
    with STATE_LOCK:
        job = JOBS.get(job_id)
        return dict(job) if job else None


def _finish_job(job_id: str, answer: str, mode: str) -> None:
    update_job(job_id, status="done", stage="готово", answer=answer, mode=mode)


def _fail_job(job_id: str, message: str) -> None:
    update_job(job_id, status="error", stage="ошибка", error=message)


def _job_worker(
    job_id: str, session_id: str, case_id: str | None, message: str
) -> None:
    try:
        with STATE_LOCK:
            session = SESSIONS.get(session_id)

        if session is not None:
            update_job(job_id, status="running", stage="печатает…")
            _finish_job(job_id, session.ask(message), "followup")
            return

        if not case_id:
            _fail_job(job_id, "Не выбрано обращение.")
            return

        update_job(
            job_id, status="running", stage="запускаю расследование по обращению…"
        )
        report = run_agent(case_id, message)

        update_job(job_id, stage="готовлю ответ…")
        session = ChatSession(report)
        answer = session.initial()

        with STATE_LOCK:
            SESSIONS[session_id] = session
            while len(SESSIONS) > MAX_SESSIONS:
                SESSIONS.pop(next(iter(SESSIONS)))

        _finish_job(job_id, answer, "report")
    except Exception:
        LOGGER.exception("Задача %s завершилась ошибкой", job_id)
        _fail_job(
            job_id,
            "Не удалось получить ответ. Проверьте песочницу и настройки GigaChat.",
        )


def start_job(session_id: str, case_id: str | None, message: str) -> str:
    job_id = create_job()
    thread = threading.Thread(
        target=_job_worker,
        args=(job_id, session_id, case_id, message),
        daemon=True,
    )
    thread.start()
    return job_id


class ChatHandler(SimpleHTTPRequestHandler):
    server_version = "BankChat/1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(CHAT_DIR), **kwargs)

    def log_message(self, format: str, *args) -> None:
        LOGGER.info("%s - %s", self.address_string(), format % args)

    def end_headers(self) -> None:
        if self.path.endswith((".html", ".js", ".css")) or self.path == "/":
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path

        if path == "/api/health":
            self.handle_health()
            return

        if path == "/api/cases":
            self.handle_cases()
            return

        if path.startswith("/api/chat/"):
            self.handle_job(path.rsplit("/", 1)[-1])
            return

        if path == "/":
            self.path = "/chat.html"

        super().do_GET()

    def handle_health(self) -> None:
        try:
            sandbox = sandbox_json("GET", "/health")
            sandbox_ok = sandbox.get("status") == "ok"
        except requests.RequestException:
            sandbox_ok = False

        with STATE_LOCK:
            sessions = len(SESSIONS)

        self.send_json(
            200,
            {
                "status": "ok" if sandbox_ok else "degraded",
                "sandbox": sandbox_ok,
                "sessions": sessions,
            },
        )

    def handle_cases(self) -> None:
        try:
            cases = list_cases()
        except requests.RequestException:
            self.send_json(502, {"error": "Песочница недоступна. Запустите контейнер."})
            return
        self.send_json(200, {"cases": cases})

    def handle_job(self, job_id: str) -> None:
        job = get_job(job_id)
        if job is None:
            self.send_json(404, {"error": "Задача не найдена."})
            return

        if (
            job["status"] != "done"
            and time.time() - job["created_at"] > JOB_TIMEOUT_SECONDS
        ):
            _fail_job(job_id, "Превышено время ожидания ответа.")
            job = get_job(job_id) or job

        payload = {"status": job["status"], "stage": job["stage"]}
        if job["status"] == "done":
            payload["answer"] = job["answer"]
            payload["mode"] = job["mode"]
        if job["status"] == "error":
            payload["error"] = job["error"]
        self.send_json(200, payload)

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path != "/api/chat":
            self.send_json(404, {"error": "Маршрут не найден."})
            return

        if self.headers.get_content_type() != "application/json":
            self.send_json(415, {"error": "Ожидается запрос в формате JSON."})
            return

        data = self.read_json_body()
        if data is None:
            return

        message = str(data.get("message") or "").strip()
        session_id = str(data.get("session_id") or "").strip()
        case_id = str(data.get("case_id") or "").strip() or None

        if not message:
            self.send_json(400, {"error": "Сообщение пустое."})
            return
        if len(message) > MAX_MESSAGE_LENGTH:
            self.send_json(400, {"error": "Сообщение слишком длинное."})
            return
        if not session_id or len(session_id) > 64:
            self.send_json(400, {"error": "Не передан идентификатор диалога."})
            return

        with STATE_LOCK:
            known_session = session_id in SESSIONS

        if not known_session:
            if not case_id:
                self.send_json(400, {"error": "Сначала выберите обращение."})
                return
            try:
                available = {case["id"] for case in list_cases()}
            except requests.RequestException:
                self.send_json(
                    502, {"error": "Песочница недоступна. Запустите контейнер."}
                )
                return
            if case_id not in available:
                self.send_json(404, {"error": "Обращение не найдено."})
                return
            case_id = case_id

        job_id = start_job(session_id, case_id, message)
        self.send_json(202, {"job_id": job_id})

    def read_json_body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 0 < length <= 16384:
            self.send_json(400, {"error": "Некорректный размер запроса."})
            return None
        try:
            data = json.loads(self.rfile.read(length))
        except ValueError, UnicodeDecodeError:
            self.send_json(400, {"error": "Некорректный JSON."})
            return None
        if not isinstance(data, dict):
            self.send_json(400, {"error": "Ожидается JSON-объект."})
            return None
        return data


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    with ThreadingHTTPServer((HOST, PORT), ChatHandler) as server:
        print(f"Чат: http://{HOST}:{PORT}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()

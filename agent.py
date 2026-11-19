import os
import re
import json
import requests
from pydantic import BaseModel, Field
from langchain.agents import create_agent
from langchain.tools import BaseTool
from langchain_community.utilities.requests import Requests
from langchain_gigachat.chat_models.gigachat import GigaChat
from dotenv import load_dotenv
from ast import literal_eval

load_dotenv()

CERT_PATH = os.getenv("CERT_PATH")
if not CERT_PATH:
    raise ValueError("Переменная окружения CERT_PATH не установлена")

GIGACHAT_CREDENTIALS = os.getenv("GIGACHAT_CREDENTIALS")
if not GIGACHAT_CREDENTIALS:
    raise ValueError("GIGACHAT_CREDENTIALS not set")

DEBUG = os.getenv("AGENT_DEBUG", "0") == "1"
JSON_TO_PY = ((r"\bnull\b", "None"), (r"\btrue\b", "True"), (r"\bfalse\b", "False"))


def _debug(*args) -> None:
    """Печать только при AGENT_DEBUG=1, чтобы не засорять rich-вывод main.py."""
    if DEBUG:
        print(*args)


def _parse_dict(raw) -> dict:
    """Понимает и JSON (null/true/false), и Python-литерал (None/True/False)."""
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except Exception:
        try:
            value = literal_eval(text)
        except Exception:
            for pattern, repl in JSON_TO_PY:
                text = re.sub(pattern, repl, text)
            value = literal_eval(text)
    return value if isinstance(value, dict) else {}


model = GigaChat(
    credentials=GIGACHAT_CREDENTIALS,
    model="GigaChat-2-Max",
    temperature=0.2,
    verify_ssl_certs=True,
    ca_bundle_file=CERT_PATH,
    max_tokens=2000,
    top_p=None,
    profanity_check=True,
    timeout=30,
)

spec_response = requests.get("http://127.0.0.1:8000/openapi.json")
spec_response.raise_for_status()
spec = spec_response.json()

base_url = spec.get("servers", [{"url": "http://127.0.0.1:8000"}])[0]["url"]

with open("openapi_gigachat", "r", encoding="utf-8") as f:
    endpoints_description = f.read()


class CallApiInput(BaseModel):
    method: str = Field(description="HTTP method: GET, POST")
    url_path: str = Field(
        description="Путь, где параметры уже подставлены. Например, '/clients/123' вместо '/clients/{id}'."
    )
    body: str = Field(description="JSON-тело запроса строкой, пустое: {}")
    run_id: str = Field(
        description="run_id, который прямым текстом указывается в начале запроса пользователя."
    )
    query: str = Field(description="Query-параметры строкой-словарём, пустые: {}")


class CallApiTool(BaseTool):
    name: str = "call_api"
    description: str = "Вызвать любой эндпоинт API из спецификации."
    args_schema: type[BaseModel] = CallApiInput
    handle_tool_error: bool = True
    requests_wrapper: Requests = Requests(headers={"Content-Type": "application/json"})
    base_url: str = base_url

    def _run(
        self, method: str, url_path: str, run_id: str, query: str, body: str
    ) -> str:
        full_url = f"{self.base_url}{url_path}"
        method = method.upper()
        _debug("АГЕНТ ВЫЗЫВАЕТ МЕТОД", method, "ПО URL", full_url)
        _debug("QUERY В ВИДЕ СТРОКИ:", query)
        _debug("BODY В ВИДЕ СТРОКИ:", body)

        try:
            query_params = _parse_dict(query)
            body = _parse_dict(body)
        except Exception as e:
            return (
                f"Не удалось разобрать query/body: {e}. "
                "Передай строку со словарём, например {}"
            )

        self.requests_wrapper.headers["X-Run-Id"] = run_id
        self.requests_wrapper.headers["X-Case-Password"] = "boss-cases"
        _debug("QUERY_PARAMS:", query_params)
        _debug("BODY:", body)

        try:
            if method == "GET":
                resp = self.requests_wrapper.get(
                    full_url, params=query_params, json=body
                )
            elif method == "POST":
                resp = self.requests_wrapper.post(
                    full_url, params=query_params, data=body
                )
            else:
                return f"Неподдерживаемый метод: {method}"
            _debug("ОТВЕТ С СЕРВЕРА RESPONSE:", resp.text)
            try:
                return json.dumps(resp.json(), indent=2, ensure_ascii=False)
            except Exception:
                return resp.text
        except Exception as e:
            _debug("Ошибка", str(e))
            return f"Ошибка вызова API: {str(e)}"


tools = [CallApiTool()]

system_prompt = f"""
Ты — ассистент, который умеет вызывать API. Доступны следующие эндпоинты:

{endpoints_description}

Для вызова API используй инструмент `call_api`. ВАЖНО:
- Параметр `url_path` должен содержать уже подставленные значения. Например, вместо `/clients/{{id}}` передавай `/clients/123`.
- Если пользователь спрашивает, какие API есть, перечисли их, используя описание выше.
- Старайся не пользоваться Legacy и Beta эндпоинтами, если этого можно избежать

Никогда не выдумывай эндпоинты, которых нет в списке.

Анализируй вызываемые функции API, чтобы понять, решена ли проблема пользователя.
ТОЛЬКО если проблема решена, то отправь сообщение следующего вида:
end_of_case: Ответ
Ответ должен представлять из себя описание проделанных действий и отчёт о том, что проблема выполнена (Пример: Здравствуйте, проблема с подпиской возникла на нашей стороне, был оформлен возврат средств на ваш счёт)

"""


agent = create_agent(model=model, tools=tools, system_prompt=system_prompt)

ALLOWED_ACTIONS = ("refund_for", "dispute_opened", "reversal_for")


def _normalize_markers(text: str) -> str:
    """Приводит маркеры к виду промпта: end_of_case без ':' и END_CASE тоже ок."""
    text = re.sub(r"\bEND_CASE\b\s*:?", "end_of_case:", text, flags=re.IGNORECASE)
    text = re.sub(r"\bEND_OF_CASE\b\s*:?", "end_of_case:", text, flags=re.IGNORECASE)
    text = re.sub(r"\bEVIDENCE\b\s*:?", "EVIDENCE:", text, flags=re.IGNORECASE)
    text = re.sub(r"\bACTIONS\b\s*:?", "ACTIONS:", text, flags=re.IGNORECASE)
    return text


def _clean_items(raw: str) -> list[str]:
    """Убирает маркеры списков, бэктики, мусор и дубли; оставляет <тип>:<id>."""
    items: list[str] = []
    for line in raw.splitlines():
        for item in re.findall(r"[A-Za-z_]+:[A-Za-z0-9_.\-]+", line):
            if item not in items:
                items.append(item)
    return items


def exec_agent(run_id, user_question, user_id):
    question = "run_id=" + run_id
    question += (
        "\nПользователь с user_id="
        + user_id
        + " обратился в поддержку со следующим вопросом: \n"
        + user_question
        + "\n Предположи, какую функцию из API нужно вызвать, чтобы найти причину ошибки сервиса"
    )
    result = agent.invoke({"messages": [{"role": "user", "content": question}]})
    temp_ans = _normalize_markers(str(result["messages"][-1].content or ""))
    _debug("\n=== ОТВЕТ АГЕНТА ===\n")
    _debug(temp_ans)

    metrics = temp_ans.split("EVIDENCE:")[-1]
    if "ACTIONS:" in metrics:
        evidence = metrics.split("ACTIONS:")[0]
        actions = metrics.split("ACTIONS:")[-1].split("end_of_case:")[0]
    else:
        evidence = metrics.split("end_of_case:")[0]
        actions = ""
    answer = metrics.split("end_of_case:")[-1]

    if "EVIDENCE:" not in temp_ans:
        evidence = ""
    if "end_of_case:" not in temp_ans:
        answer = temp_ans

    answer = answer.strip().lstrip(":").strip()
    evidence_items = _clean_items(evidence)
    action_items = [
        a for a in _clean_items(actions) if a.split(":")[0] in ALLOWED_ACTIONS
    ]
    _debug("Ответ:", answer)
    _debug("evidence:", evidence_items)
    _debug("actions:", action_items)
    return answer, evidence_items, action_items

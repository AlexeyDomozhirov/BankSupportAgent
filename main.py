import argparse
import json
import sys
from agent import *
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


def is_service_avaliable() -> bool:
    health_url = "http://127.0.0.1:8000/health"
    try:
        avaliability = json.loads(requests.get(health_url).content)["status"]
    except requests.exceptions.ConnectionError:
        return False
    return True if avaliability == "ok" else False


def create_run() -> str:
    url = "http://127.0.0.1:8000/runs"
    headers = {"accept": "application/json", "Content-Type": "application/json"}
    payload = {"team_name": "Kapuchinator"}
    response = requests.post(url, headers=headers, json=payload)
    data = json.loads(response.content)
    return data["id"]


def get_case_id(case_number: int) -> str:
    url = "http://127.0.0.1:8000/cases/"
    cases = json.loads(requests.get(url).content)
    return cases[case_number]["id"]


def get_user_id(case_id: str) -> str:
    url = "http://127.0.0.1:8000/cases/" + case_id
    ticket_id = json.loads(
        requests.get(url, headers={"X-Case-Password": "boss-cases"}).content
    )["intake"]["ticket_id"]
    ticket_url = "http://127.0.0.1:8000/support/tickets/" + ticket_id
    user_id = json.loads(requests.get(ticket_url).content)["user_id"]
    return user_id


def get_user_question(case_id: str) -> str:
    url = "http://127.0.0.1:8000/cases/" + case_id
    user_question = json.loads(
        requests.get(url, headers={"X-Case-Password": "boss-cases"}).content
    )["customer_message"]
    return user_question


def evaluate_run(
    run_id: str, case_id: str, answer: str, evidence: list[str], actions: list[str]
) -> None:
    url = "http://127.0.0.1:8000/cases/" + case_id + "/evaluate"
    headers = {
        "accept": "application/json",
        "Content-Type": "application/json",
        "X-Case-Password": "boss-cases",
    }
    payload = {
        "run_id": run_id,
        "case_id": case_id,
        "answer": answer,
        "evidence": evidence,
        "actions": actions,
    }

    requests.post(url, headers=headers, json=payload)


def get_metrics(run_id: str) -> dict[str, object]:
    url = "http://127.0.0.1:8000/runs/" + run_id + "/metrics"
    metrics = json.loads(
        requests.get(url, headers={"X-Case-Password": "boss-cases"}).content
    )
    return metrics


def get_export(run_id: str) -> dict[str, object]:
    url = "http://127.0.0.1:8000/runs/" + run_id + "/export"
    export = json.loads(
        requests.get(url, headers={"X-Case-Password": "boss-cases"}).content
    )
    return export


def print_export_data(
    data: dict[str, object] | list[object] | str | int | float | bool, indent: int = 0
) -> None:
    """
    Recursively prints nested data structures (dicts, lists) in a readable format.
    """
    indent_str = "  " * indent
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                console.print(f"{indent_str}[cyan]{key}:[/]")
                print_export_data(value, indent + 1)
            else:
                console.print(f"{indent_str}[cyan]{key}:[/] [magenta]{value}[/]")
    elif isinstance(data, list):
        for index, item in enumerate(data):
            if isinstance(item, (dict, list)):
                console.print(f"{indent_str}[cyan][{index}][/]")
                print_export_data(item, indent + 1)
            else:
                console.print(f"{indent_str}[cyan][{index}]:[/] [magenta]{item}[/]")
    else:
        console.print(f"{indent_str}[magenta]{data}[/]")


class Tee:
    """
    A file-like object that writes to multiple streams.
    """

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for stream in self.streams:
            stream.write(data)

    def flush(self):
        for stream in self.streams:
            stream.flush()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run the agent on test cases.",
        epilog=(
            "Примеры:\n"
            "  python main.py                 # прогнать все кейсы (0-9)\n"
            "  python main.py --cases 0 3 5   # прогнать только кейсы 0, 3 и 5\n"
            "  python main.py --log run.log   # писать весь вывод в файл"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--log", type=str, help="Path to a file to log all console output."
    )
    parser.add_argument(
        "--cases",
        nargs="+",
        type=int,
        metavar="N",
        help="Номера кейсов для проверки, например: --cases 0 3 5. По умолчанию: 0-9.",
    )
    args = parser.parse_args()

    case_numbers = args.cases or range(10)

    original_stdout = sys.stdout
    log_file = None

    if args.log:
        log_file = open(args.log, "a", encoding="utf-8")
        sys.stdout = Tee(original_stdout, log_file)

    if not is_service_avaliable():
        console.print("[bold red]Сервис в данный момент недоступен[/bold red]")
        exit()

    for case_number in case_numbers:
        run_id = create_run()
        console.print(f"[bold cyan]Run_id:[/bold cyan] {run_id}")

        case_id = get_case_id(case_number)
        console.print(f"[bold cyan]Case_id:[/bold cyan] {case_id}")

        user_id = get_user_id(case_id)
        console.print(f"[bold cyan]User_id:[/bold cyan] {user_id}")

        user_question = get_user_question(case_id)

        answer, evidence, actions = exec_agent(run_id, user_question, user_id)

        evaluate_run(run_id, case_id, answer, evidence, actions)

        console.print("\n[bold]=== ОТВЕТ ПОЛЬЗОВАТЕЛЮ ===[/bold]")
        console.print(Panel(answer, title="Agent Answer", border_style="green"))

        metrics = get_metrics(run_id)
        export = get_export(run_id)

        console.print("\n[bold]=== МЕТРИКИ ===[/bold]")
        print_export_data(metrics)

        console.print("\n[bold]=== ЭКСПОРТ ДАННЫХ ===[/bold]")
        print_export_data(export)

        console.print("\n\n")

    if log_file:
        log_file.close()
        sys.stdout = original_stdout

import requests
import json
from agent import *
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

def is_service_avaliable():
    health_url = 'http://127.0.0.1:8000/health'
    try:
        avaliability = json.loads(requests.get(health_url).content)['status']
    except requests.exceptions.ConnectionError:
        return False
    return True if avaliability == 'ok' else False


def create_run():
    url = 'http://127.0.0.1:8000/runs'
    headers = {
        'accept': 'application/json',
        'Content-Type': 'application/json'
    }
    payload = {
        'team_name': 'Kapuchinator'
    }
    response = requests.post(url, headers=headers, json=payload)
    data = json.loads(response.content)
    return data['id']


def get_case_id(case_number):
    url = 'http://127.0.0.1:8000/cases/'
    cases = json.loads(requests.get(url).content)
    return cases[case_number]['id']


def get_user_id(case_id):
    url = 'http://127.0.0.1:8000/cases/' + case_id
    ticket_id = json.loads(requests.get(url, headers={'X-Case-Password': 'boss-cases'}).content)['intake']['ticket_id']
    ticket_url = 'http://127.0.0.1:8000/support/tickets/' + ticket_id
    user_id = json.loads(requests.get(ticket_url).content)['user_id']
    return user_id


def get_user_question(case_id):
    url = 'http://127.0.0.1:8000/cases/' + case_id
    user_question = json.loads(requests.get(url, headers={'X-Case-Password': 'boss-cases'}).content)['customer_message']
    return user_question


def evaluate_run(run_id, case_id, answer: str, evidence: list, actions: list):
    url = 'http://127.0.0.1:8000/cases/' + case_id + '/evaluate'
    headers = {
        'accept': 'application/json',
        'Content-Type': 'application/json',
        'X-Case-Password': 'boss-cases'
    }
    payload = {
        'run_id': run_id,
        'case_id': case_id,
        'answer': answer,
        'evidence': evidence,
        'actions': actions
    }

    requests.post(url, headers=headers, json=payload)


def get_metrics(run_id):
    url = 'http://127.0.0.1:8000/runs/' + run_id + '/metrics'
    metrics = json.loads(requests.get(url, headers={'X-Case-Password': 'boss-cases'}).content)
    return metrics


def get_export(run_id):
    url = 'http://127.0.0.1:8000/runs/' + run_id + '/export'
    export = json.loads(requests.get(url, headers={'X-Case-Password': 'boss-cases'}).content)
    return export


if __name__ == '__main__':
    if not is_service_avaliable():
        console.print('[bold red]Сервис в данный момент недоступен[/bold red]')
        exit()

    case_number = 0
    while case_number < 10:
        run_id = create_run()
        console.print(f'[bold cyan]Run_id:[/bold cyan] {run_id}')

        case_id = get_case_id(case_number)
        console.print(f'[bold cyan]Case_id:[/bold cyan] {case_id}')

        user_id = get_user_id(case_id)
        console.print(f'[bold cyan]User_id:[/bold cyan] {user_id}')

        user_question = get_user_question(case_id)

        answer, evidence, actions = exec_agent(run_id, user_question, user_id)

        evaluate_run(run_id, case_id, answer, evidence, actions)
        case_number += 1

        console.print('\n[bold]=== ОТВЕТ ПОЛЬЗОВАТЕЛЮ ===[/bold]')
        console.print(Panel(answer, title='Agent Answer', border_style='green'))

        metrics = get_metrics(run_id)
        export = get_export(run_id)

        console.print('\n[bold]=== МЕТРИКИ ===[/bold]')
        metrics_table = Table(title="Run Metrics")
        metrics_table.add_column("Metric", style="cyan", no_wrap=True)
        metrics_table.add_column("Value", style="magenta")
        for key, value in metrics.items():
            metrics_table.add_row(key, str(value))
        console.print(metrics_table)

        console.print('\n[bold]=== ЭКСПОРТ ДАННЫХ ===[/bold]')
        export_table = Table(title="Run Export")
        export_table.add_column("Key", style="cyan", no_wrap=True)
        export_table.add_column("Value", style="magenta")
        for key, value in export.items():
            export_table.add_row(key, str(value))
        console.print(export_table)

        console.print('\n\n')


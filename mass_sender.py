#!/usr/bin/env python3
"""
Automated mass email sender with round-robin rotation, tag substitution and
concurrent delivery using SMTP credentials specified in local files.
"""

from __future__ import annotations

import concurrent.futures
import random
import re
import smtplib
import socket
import sys
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, format_datetime
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    print("Python 3.9 ou superior é necessário para suporte de fuso horário.", file=sys.stderr)
    raise


BASE_DIR = Path(__file__).resolve().parent
WORKERS = 5
TIMEZONE = ZoneInfo("Asia/Manila")

TAG_KEYS = {
    "time",
    "date",
    "email",
    "location",
    "ip",
    "device",
}

PHILIPPINE_LOCATIONS = [
    "Quezon City",
    "Manila",
    "Davao City",
    "Cebu City",
    "Taguig",
    "Antipolo",
    "Pasig",
    "Cagayan de Oro",
    "Iloilo City",
    "Baguio",
    "Makati",
    "General Santos",
    "Bacolod",
    "Calamba",
    "Lapu-Lapu City",
]

DEVICE_NAMES = [
    "Samsung Galaxy S23",
    "iPhone 15 Pro",
    "Huawei P60",
    "Oppo Reno10",
    "Xiaomi 13 Ultra",
    "Asus ROG Phone 7",
    "Lenovo IdeaPad Slim",
    "HP Pavilion x360",
    "Acer Swift Go",
    "MSI Katana",
    "Dell XPS 13",
    "Google Pixel 8",
    "Realme GT5",
    "Vivo X100",
]


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    username: str
    password: str


def load_lines(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(f"Arquivo obrigatório não encontrado: {path}")

    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    filtered = [line for line in lines if line]
    if not filtered:
        raise ValueError(f"O arquivo {path} não contém entradas válidas.")
    return filtered


def load_smtps(path: Path) -> List[SmtpConfig]:
    raw_lines = load_lines(path)
    configs = []
    for idx, line in enumerate(raw_lines, start=1):
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 4:
            raise ValueError(f"Formato inválido na linha {idx} de {path}: esperado host|port|usuario|senha.")
        host, port_str, username, password = parts
        if not port_str.isdigit():
            raise ValueError(f"Porta inválida na linha {idx} de {path}: {port_str}")
        configs.append(SmtpConfig(host=host, port=int(port_str), username=username, password=password))
    return configs


def strip_html_tags(html: str) -> str:
    text = re.sub(r"<(br|BR)\s*/?>", "\n", html)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def apply_tags(text: str, mapping: Dict[str, str]) -> str:
    result = text
    for key, value in mapping.items():
        token = f"##{key}##"
        result = result.replace(token, value)
    return result


def resolve_ip(host: str) -> str:
    try:
        return socket.gethostbyname(host)
    except OSError:
        return host


def format_time(now: datetime) -> str:
    formatted = now.strftime("%I:%M %p").lstrip("0")
    if not formatted:
        formatted = now.strftime("%H:%M %p")
    return f"{formatted} PHT"


def format_date(now: datetime) -> str:
    return now.strftime("%m/%d/%Y")


def build_message(
    sender_name: str,
    recipient: str,
    subject: str,
    body_html: str,
    smtp_config: SmtpConfig,
    sender_ip: str,
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((sender_name, smtp_config.username))
    message["To"] = recipient
    message["Date"] = format_datetime(datetime.now(tz=TIMEZONE))
    message["X-Originating-IP"] = sender_ip
    message["X-Sender-IP"] = sender_ip

    body_plain = strip_html_tags(body_html)
    if body_plain:
        message.set_content(body_plain)
    else:
        message.set_content("Este e-mail contém conteúdo em HTML. Por favor, utilize um leitor compatível.")

    message.add_alternative(body_html, subtype="html")
    return message


def send_with_smtp(config: SmtpConfig, message: EmailMessage) -> None:
    if config.port == 465:
        smtp_client = smtplib.SMTP_SSL(config.host, config.port, timeout=30)
    else:
        smtp_client = smtplib.SMTP(config.host, config.port, timeout=30)

    with smtp_client as server:
        server.ehlo()
        if config.port not in (25, 465):
            try:
                server.starttls()
                server.ehlo()
            except smtplib.SMTPException:
                pass
        if config.username:
            server.login(config.username, config.password)
        server.send_message(message)


def prepare_context(recipient: str, smtp_config: SmtpConfig) -> Dict[str, str]:
    now = datetime.now(tz=TIMEZONE)
    return {
        "time": format_time(now),
        "date": format_date(now),
        "email": recipient,
        "location": random.choice(PHILIPPINE_LOCATIONS),
        "ip": resolve_ip(smtp_config.host),
        "device": random.choice(DEVICE_NAMES),
    }


def dispatch_email(
    index_email_pair: Tuple[int, str],
    subjects: List[str],
    sender_names: List[str],
    body_template: str,
    smtp_configs: List[SmtpConfig],
) -> Tuple[str, bool, str]:
    index, recipient = index_email_pair
    subject_template = subjects[index % len(subjects)]
    sender_template = sender_names[index % len(sender_names)]
    smtp_config = smtp_configs[index % len(smtp_configs)]

    context = prepare_context(recipient, smtp_config)
    rendered_subject = apply_tags(subject_template, context)
    rendered_sender = apply_tags(sender_template, context)
    rendered_body = apply_tags(body_template, context)
    sender_ip = context["ip"]

    message = build_message(
        sender_name=rendered_sender,
        recipient=recipient,
        subject=rendered_subject,
        body_html=rendered_body,
        smtp_config=smtp_config,
        sender_ip=sender_ip,
    )

    try:
        send_with_smtp(smtp_config, message)
        info = f"SENT -> {recipient} | SMTP {smtp_config.host}:{smtp_config.port} | Assunto: {rendered_subject}"
        return recipient, True, info
    except Exception as exc:  # noqa: BLE001
        error = f"FAILED -> {recipient} | SMTP {smtp_config.host}:{smtp_config.port} | Erro: {exc}"
        return recipient, False, error


def validate_tags(templates: Iterable[str]) -> None:
    for template in templates:
        for token in re.findall(r"##(.*?)##", template):
            if token and token not in TAG_KEYS:
                raise ValueError(f"Tag desconhecida encontrada: ##{token}##")


def main() -> None:
    emails = load_lines(BASE_DIR / "list.txt")
    subjects = load_lines(BASE_DIR / "subject.txt")
    sender_names = load_lines(BASE_DIR / "name.txt")
    body_template = (BASE_DIR / "body.html").read_text(encoding="utf-8")
    smtp_configs = load_smtps(BASE_DIR / "smtp.txt")

    validate_tags(subjects + sender_names + [body_template])

    total = len(emails)
    print(f"Iniciando envio para {total} destinatário(s) com {WORKERS} workers...\n")

    sent = 0
    failed = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = [
            executor.submit(
                dispatch_email,
                (index, recipient),
                subjects,
                sender_names,
                body_template,
                smtp_configs,
            )
            for index, recipient in enumerate(emails)
        ]

        for future in concurrent.futures.as_completed(futures):
            recipient, ok, message = future.result()
            print(message)
            if ok:
                sent += 1
            else:
                failed += 1

    print("\nResumo:")
    print(f"  SENT   : {sent}")
    print(f"  FAILED : {failed}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nEnvio interrompido pelo usuário.")
*** End Patch

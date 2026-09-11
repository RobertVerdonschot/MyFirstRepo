from __future__ import annotations

import requests

_API = "https://api.telegram.org/bot{token}/{method}"


def send_message(token: str, chat_id: int, text: str) -> None:
    url = _API.format(token=token, method="sendMessage")
    resp = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
    resp.raise_for_status()


def set_webhook(token: str, url: str, secret_token: str) -> dict:
    api_url = _API.format(token=token, method="setWebhook")
    resp = requests.post(
        api_url, json={"url": url, "secret_token": secret_token}, timeout=10
    )
    resp.raise_for_status()
    return resp.json()

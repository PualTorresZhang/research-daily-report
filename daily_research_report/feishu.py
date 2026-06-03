from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path

import requests


def push_report(
    title: str,
    markdown_path: Path,
    html_path: Path,
    webhook_url: str | None = None,
    secret: str | None = None,
    public_url: str | None = None,
) -> bool:
    url = webhook_url or os.getenv("FEISHU_WEBHOOK_URL")
    card = build_card(title, markdown_path, html_path, public_url)
    if not url:
        return push_report_with_app(card)

    timestamp = str(int(time.time()))
    payload: dict = {
        "msg_type": "interactive",
        "card": card,
    }
    secret_value = secret if secret is not None else os.getenv("FEISHU_WEBHOOK_SECRET")
    if secret_value:
        payload["timestamp"] = timestamp
        payload["sign"] = sign(timestamp, secret_value)

    response = requests.post(url, json=payload, timeout=20)
    response.raise_for_status()
    data = response.json()
    return data.get("code", 0) == 0


def push_report_with_app(card: dict) -> bool:
    app_id = os.getenv("FEISHU_APP_ID")
    app_secret = os.getenv("FEISHU_APP_SECRET")
    receive_id = os.getenv("FEISHU_RECEIVE_ID")
    receive_id_type = os.getenv("FEISHU_RECEIVE_ID_TYPE", "chat_id")
    if not all([app_id, app_secret, receive_id]):
        return False

    token = get_tenant_access_token(app_id, app_secret)
    response = requests.post(
        "https://open.feishu.cn/open-apis/im/v1/messages",
        params={"receive_id_type": receive_id_type},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "receive_id": receive_id,
            "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False),
        },
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    return data.get("code", 0) == 0


def get_tenant_access_token(app_id: str, app_secret: str) -> str:
    response = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("code", 0) != 0:
        raise RuntimeError(f"Failed to get tenant_access_token: {data}")
    return data["tenant_access_token"]


def build_card(title: str, markdown_path: Path, html_path: Path, public_url: str | None) -> dict:
    elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": build_summary(markdown_path, html_path, public_url),
            },
        }
    ]
    if public_url:
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "查看完整日报"},
                        "type": "primary",
                        "url": public_url,
                    }
                ],
            }
        )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": "blue",
        },
        "elements": elements,
    }


def sign(timestamp: str, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(string_to_sign, b"", digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def build_summary(markdown_path: Path, html_path: Path, public_url: str | None) -> str:
    text = markdown_path.read_text(encoding="utf-8")
    preview_lines = ["**摘要**"]
    for line in text.splitlines():
        if line.startswith("# "):
            continue
        if line.startswith("## "):
            if len(preview_lines) >= 14:
                break
            preview_lines.append(f"\n**{line.replace('## ', '')}**")
        elif line.strip() and len(preview_lines) < 14:
            preview_lines.append(line)
        if len(preview_lines) >= 14:
            break
    if public_url:
        preview_lines.extend(["", f"[查看完整日报]({public_url})"])
    else:
        preview_lines.extend(["", f"完整日报已生成到本地：`{html_path}`"])
    return "\n".join(preview_lines)

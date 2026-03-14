from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib import error, request
from zoneinfo import ZoneInfo

NOTION_BASE_URL = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


@dataclass
class Config:
    notion_api_key: str
    notion_database_id: str
    timezone: str
    title_template: str
    date_label_format: str
    date_property: str | None
    status_property: str | None
    status_value: str | None


class NotionClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{NOTION_BASE_URL}{path}"
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        req = request.Request(url=url, data=body, method=method)
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Notion-Version", NOTION_VERSION)
        req.add_header("Content-Type", "application/json")

        try:
            with request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Notion API error {exc.code}: {details}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"Network error calling Notion API: {exc.reason}") from exc

    def retrieve_database(self, database_id: str) -> dict[str, Any]:
        return self._request("GET", f"/databases/{database_id}")

    def query_database(self, database_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"/databases/{database_id}/query", payload)

    def create_page(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/pages", payload)


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    bad_values = {
        "replace_with_new_secret",
        "replace_with_database_id",
        "your_new_secret_here",
        "your_database_id_here",
    }
    if value in bad_values or value.startswith("replace_with_"):
        raise RuntimeError(f"Env var {name} still has a placeholder value.")
    return value


def build_config() -> Config:
    api_key = required_env("NOTION_API_KEY")
    db_id = required_env("NOTION_DATABASE_ID")
    timezone = os.getenv("TIMEZONE", "Europe/Rome").strip() or "Europe/Rome"

    return Config(
        notion_api_key=api_key,
        notion_database_id=db_id,
        timezone=timezone,
        title_template=os.getenv("TASK_TITLE_TEMPLATE", "Daily Task - {date}").strip() or "Daily Task - {date}",
        date_label_format=os.getenv("DATE_LABEL_FORMAT", "%Y-%m-%d").strip() or "%Y-%m-%d",
        date_property=(os.getenv("NOTION_DATE_PROPERTY") or "").strip() or None,
        status_property=(os.getenv("NOTION_STATUS_PROPERTY") or "").strip() or None,
        status_value=(os.getenv("NOTION_STATUS_VALUE") or "").strip() or None,
    )


def pick_title_property(properties: dict[str, Any]) -> str:
    for name, meta in properties.items():
        if meta.get("type") == "title":
            return name
    raise RuntimeError("No title property found in the Notion database.")


def pick_date_property(properties: dict[str, Any], explicit: str | None) -> str | None:
    if explicit:
        if explicit not in properties:
            raise RuntimeError(f"NOTION_DATE_PROPERTY '{explicit}' does not exist in database schema.")
        if properties[explicit].get("type") != "date":
            raise RuntimeError(f"NOTION_DATE_PROPERTY '{explicit}' exists but is not a date property.")
        return explicit

    preferred = {"date", "due date", "due", "day"}
    for name, meta in properties.items():
        if meta.get("type") == "date" and name.strip().casefold() in preferred:
            return name

    for name, meta in properties.items():
        if meta.get("type") == "date":
            return name
    return None


def pick_status_property(properties: dict[str, Any], explicit: str | None, status_value: str | None) -> tuple[str | None, str | None]:
    if not status_value:
        return None, None

    if explicit:
        if explicit not in properties:
            raise RuntimeError(f"NOTION_STATUS_PROPERTY '{explicit}' does not exist in database schema.")
        kind = properties[explicit].get("type")
        if kind not in {"status", "select"}:
            raise RuntimeError(
                f"NOTION_STATUS_PROPERTY '{explicit}' must be type status/select, got '{kind}'."
            )
        return explicit, kind

    for candidate in ("Status", "status"):
        if candidate in properties and properties[candidate].get("type") in {"status", "select"}:
            return candidate, properties[candidate].get("type")

    for name, meta in properties.items():
        if meta.get("type") in {"status", "select"}:
            return name, meta.get("type")

    raise RuntimeError(
        "NOTION_STATUS_VALUE is set but no status/select property was found. "
        "Set NOTION_STATUS_PROPERTY or remove NOTION_STATUS_VALUE."
    )


def find_existing_page(
    client: NotionClient,
    database_id: str,
    title_property: str,
    title_value: str,
    date_property: str | None,
    date_iso: str,
) -> dict[str, Any] | None:
    if date_property:
        filter_payload: dict[str, Any] = {
            "and": [
                {"property": date_property, "date": {"equals": date_iso}},
                {"property": title_property, "title": {"equals": title_value}},
            ]
        }
    else:
        filter_payload = {"property": title_property, "title": {"equals": title_value}}

    result = client.query_database(
        database_id,
        {
            "filter": filter_payload,
            "page_size": 1,
        },
    )

    items = result.get("results", [])
    return items[0] if items else None


def create_daily_task() -> None:
    load_dotenv()
    config = build_config()

    try:
        now = datetime.now(ZoneInfo(config.timezone))
    except Exception as exc:
        raise RuntimeError(f"Invalid TIMEZONE '{config.timezone}'. Example: Europe/Rome") from exc

    date_iso = now.date().isoformat()
    date_label = now.strftime(config.date_label_format)
    task_title = config.title_template.format(
        date=date_label,
        iso_date=date_iso,
        weekday=now.strftime("%A"),
    )

    client = NotionClient(config.notion_api_key)
    db = client.retrieve_database(config.notion_database_id)
    properties = db.get("properties", {})
    if not properties:
        raise RuntimeError("Could not read database properties. Check database ID and integration access.")

    title_property = pick_title_property(properties)
    date_property = pick_date_property(properties, config.date_property)
    status_property, status_kind = pick_status_property(properties, config.status_property, config.status_value)

    existing = find_existing_page(
        client=client,
        database_id=config.notion_database_id,
        title_property=title_property,
        title_value=task_title,
        date_property=date_property,
        date_iso=date_iso,
    )
    if existing:
        page_id = existing.get("id", "unknown")
        print(f"Skipped: task already exists for today (page_id={page_id})")
        return

    notion_properties: dict[str, Any] = {
        title_property: {
            "title": [
                {
                    "text": {
                        "content": task_title,
                    }
                }
            ]
        }
    }

    if date_property:
        notion_properties[date_property] = {"date": {"start": date_iso}}

    if status_property and status_kind and config.status_value:
        if status_kind == "status":
            notion_properties[status_property] = {"status": {"name": config.status_value}}
        else:
            notion_properties[status_property] = {"select": {"name": config.status_value}}

    created = client.create_page(
        {
            "parent": {"database_id": config.notion_database_id},
            "properties": notion_properties,
        }
    )

    page_id = created.get("id", "unknown")
    print(f"Created: {task_title} (page_id={page_id})")
    print(f"Resolved properties: title='{title_property}', date='{date_property}', status='{status_property}'")


def main() -> int:
    try:
        create_daily_task()
        return 0
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from urllib import error, request
from zoneinfo import ZoneInfo

NOTION_API_URL = "https://api.notion.com/v1"
DEFAULT_NOTION_API_VERSION = "2025-09-03"


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


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default or "").strip()
    if value:
        return value
    raise RuntimeError(f"Missing env var: {name}")


def normalize_notion_id(value: str) -> str:
    value = value.strip()
    if value.startswith("http://") or value.startswith("https://"):
        match = re.search(r"([0-9a-fA-F]{32})", value)
        if match:
            value = match.group(1)

    compact = value.replace("-", "")
    if len(compact) == 32 and all(c in "0123456789abcdefABCDEF" for c in compact):
        return (
            f"{compact[0:8]}-"
            f"{compact[8:12]}-"
            f"{compact[12:16]}-"
            f"{compact[16:20]}-"
            f"{compact[20:32]}"
        ).lower()
    return value


def notion_request(api_key: str, api_version: str, method: str, path: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(f"{NOTION_API_URL}{path}", data=body, method=method)
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Notion-Version", api_version)
    req.add_header("Content-Type", "application/json")

    try:
        with request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
            code = parsed.get("code") or str(exc.code)
            message = parsed.get("message") or raw
            raise RuntimeError(f"{code}: {message}") from exc
        except json.JSONDecodeError:
            raise RuntimeError(f"HTTP {exc.code}: {raw}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Network error: {exc.reason}") from exc


def pick_first_property(properties: dict, kind: str, preferred: tuple[str, ...] = ()) -> str | None:
    for name in preferred:
        meta = properties.get(name)
        if isinstance(meta, dict) and meta.get("type") == kind:
            return name
    for name, meta in properties.items():
        if isinstance(meta, dict) and meta.get("type") == kind:
            return name
    return None


def option_name(prop: dict | None) -> str | None:
    if not isinstance(prop, dict):
        return None
    for kind in ("status", "select"):
        value = prop.get(kind)
        if isinstance(value, dict):
            name = value.get("name")
            if isinstance(name, str) and name:
                return name
    return None


def notion_page_url(page_id: str) -> str:
    return f"https://www.notion.so/{page_id.replace('-', '')}"


def resolve_parent(api_key: str, api_version: str, source_id: str) -> tuple[str, str, dict]:
    source_id = normalize_notion_id(source_id)

    # Try source as data_source_id
    try:
        data_source = notion_request(api_key, api_version, "GET", f"/data_sources/{source_id}")
        return "data_source_id", source_id, data_source.get("properties", {})
    except RuntimeError as exc:
        if "object_not_found" not in str(exc):
            raise

    # Try source as database_id, then map to data_source_id when available
    database = notion_request(api_key, api_version, "GET", f"/databases/{source_id}")
    ds_list = database.get("data_sources", [])
    if isinstance(ds_list, list) and ds_list and isinstance(ds_list[0], dict) and ds_list[0].get("id"):
        ds_id = normalize_notion_id(str(ds_list[0]["id"]))
        schema = notion_request(api_key, api_version, "GET", f"/data_sources/{ds_id}")
        return "data_source_id", ds_id, schema.get("properties", {})

    return "database_id", source_id, database.get("properties", {})


def main() -> int:
    load_dotenv()

    api_key = env("NOTION_API_KEY")
    source_id = env("NOTION_DATABASE_ID")
    api_version = os.getenv("NOTION_API_VERSION", DEFAULT_NOTION_API_VERSION).strip() or DEFAULT_NOTION_API_VERSION
    timezone = os.getenv("TIMEZONE", "Europe/Rome").strip() or "Europe/Rome"
    title_template = os.getenv("TASK_TITLE_TEMPLATE", "Daily Task - {date}").strip() or "Daily Task - {date}"
    date_format = os.getenv("DATE_LABEL_FORMAT", "%Y-%m-%d").strip() or "%Y-%m-%d"
    date_property_env = (os.getenv("NOTION_DATE_PROPERTY") or "").strip() or None
    status_property_env = (os.getenv("NOTION_STATUS_PROPERTY") or "").strip() or None
    status_value = (os.getenv("NOTION_STATUS_VALUE") or "").strip() or None

    now = datetime.now(ZoneInfo(timezone))
    today_iso = now.date().isoformat()
    title = title_template.format(date=now.strftime(date_format), iso_date=today_iso, weekday=now.strftime("%A"))

    parent_key, parent_id, properties = resolve_parent(api_key, api_version, source_id)
    if not isinstance(properties, dict) or not properties:
        raise RuntimeError("Could not read database properties")

    title_property = pick_first_property(properties, "title")
    if not title_property:
        raise RuntimeError("No title property found")

    if date_property_env:
        if properties.get(date_property_env, {}).get("type") != "date":
            raise RuntimeError(f"Invalid NOTION_DATE_PROPERTY: {date_property_env}")
        date_property = date_property_env
    else:
        date_property = pick_first_property(properties, "date", ("Due Date", "Date", "due date", "date"))

    if status_value:
        if status_property_env:
            status_property = status_property_env
            status_type = properties.get(status_property, {}).get("type")
            if status_type not in {"status", "select"}:
                raise RuntimeError(f"Invalid NOTION_STATUS_PROPERTY: {status_property}")
        else:
            status_property = pick_first_property(properties, "status", ("Status", "status"))
            status_type = "status" if status_property else None
            if not status_property:
                status_property = pick_first_property(properties, "select", ("Status", "status"))
                status_type = "select" if status_property else None
            if not status_property:
                raise RuntimeError("No status/select property found")
    else:
        status_property = None
        status_type = None

    if date_property:
        filters: dict = {
            "and": [
                {"property": title_property, "title": {"equals": title}},
                {"property": date_property, "date": {"equals": today_iso}},
            ]
        }
    else:
        filters = {"property": title_property, "title": {"equals": title}}

    query_path = f"/data_sources/{parent_id}/query" if parent_key == "data_source_id" else f"/databases/{parent_id}/query"
    existing = notion_request(api_key, api_version, "POST", query_path, {"filter": filters, "page_size": 1}).get("results", [])

    if existing:
        page = existing[0]
        page_id = str(page.get("id", ""))

        if status_property and status_type and status_value:
            current = option_name(page.get("properties", {}).get(status_property))
            if current != status_value and page_id:
                notion_request(
                    api_key,
                    api_version,
                    "PATCH",
                    f"/pages/{page_id}",
                    {"properties": {status_property: {status_type: {"name": status_value}}}},
                )
                print(f"updated {notion_page_url(page_id)}")
                return 0

        print(f"exists {notion_page_url(page_id) if page_id else 'existing page'}")
        return 0

    new_properties: dict = {
        title_property: {"title": [{"text": {"content": title}}]},
    }
    if date_property:
        new_properties[date_property] = {"date": {"start": today_iso}}
    if status_property and status_type and status_value:
        new_properties[status_property] = {status_type: {"name": status_value}}

    created = notion_request(
        api_key,
        api_version,
        "POST",
        "/pages",
        {"parent": {parent_key: parent_id}, "properties": new_properties},
    )

    page_id = str(created.get("id", ""))
    print(f"created {notion_page_url(page_id) if page_id else 'page'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)

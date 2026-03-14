# Notion Daily Task Automation

Minimal daily task creator for Notion.

## .env

```env
NOTION_API_KEY=your_new_secret_here
NOTION_DATABASE_ID=your_database_or_data_source_id
NOTION_API_VERSION=2025-09-03
TIMEZONE=Europe/Rome
TASK_TITLE_TEMPLATE=Daily Task - {date}
DATE_LABEL_FORMAT=%Y-%m-%d
NOTION_STATUS_PROPERTY=Status
NOTION_STATUS_VALUE=Next Up
```

## Run

```bash
python3 main.py
```

## Behavior

- Creates one task per day.
- If today's task already exists, it updates status to `Next Up` (if configured).
- Auto-resolves `database_id` to `data_source_id` when needed.

## Cron (daily at 08:00)

```cron
0 8 * * * cd /Users/ermiasmulugetateklehaimanot/NEO/notion_A && /usr/bin/python3 main.py >> notion_daily.log 2>&1
```

# Notion Daily Task Automation

This script creates one task per day in your Notion database and skips duplicates for the same date/title.

## 1) Fill your env file

Edit `.env`:

```env
NOTION_API_KEY=your_new_secret_here
NOTION_DATABASE_ID=your_database_id_here
TIMEZONE=Europe/Rome
TASK_TITLE_TEMPLATE=Daily Task - {date}
DATE_LABEL_FORMAT=%Y-%m-%d
```

Optional:

```env
# Force a specific date column name
# NOTION_DATE_PROPERTY=Date

# Set a status when creating the task
# NOTION_STATUS_PROPERTY=Status
# NOTION_STATUS_VALUE=Next Up
```

## 2) Connect integration to database

In Notion:
1. Open your task database page.
2. `...` menu -> `Connections`.
3. Add your integration.

## 3) Test once

```bash
python3 main.py
```

If successful, you'll see `Created: ...` or `Skipped: ...` if today's task already exists.

## 4) Schedule daily (cron example)

Run `crontab -e` and add:

```cron
0 8 * * * cd /Users/ermiasmulugetateklehaimanot/NEO/notion_A && /usr/bin/python3 main.py >> notion_daily.log 2>&1
```

This runs every day at 08:00.

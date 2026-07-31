"""
Run this once after deploying to set up Notion database columns.
python setup_notion.py
"""
import os
from notion_client import Client

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

notion = Client(auth=NOTION_TOKEN)

try:
    notion.databases.update(
        database_id=NOTION_DATABASE_ID,
        properties={
            "Name": {"title": {}},
            "Date": {"date": {}},
            "Score": {"number": {"format": "percent"}},
            "Completed": {"rich_text": {}},
            "Missed": {"rich_text": {}},
            "Resources": {"rich_text": {}},
            "Goal": {"rich_text": {}},
        }
    )
    print("✅ Notion database columns created successfully!")
    print("You're ready to deploy the bot.")
except Exception as e:
    print(f"❌ Error: {e}")
    print("\nMake sure you connected the Daily Tracker integration to the database.")
    print("In Notion: open the database → ... menu → Connections → Add connections → Daily Tracker")

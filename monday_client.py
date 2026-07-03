import os, json, urllib.request
from datetime import date

MONDAY_API_URL = "https://api.monday.com/v2"
BOARD_ID = "5029590799"
PLANNING_GROUP_ID = "topics"

def run_query(token, query):
    data = json.dumps({"query": query}).encode()
    req = urllib.request.Request(
        MONDAY_API_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": token
        },
        method="POST"
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode())

def get_open_items(token):
    query = """
    {
      boards(ids: [""" + BOARD_ID + """]) {
        items_page(limit: 50) {
          items {
            id
            name
          }
        }
      }
    }
    """
    result = run_query(token, query)
    return result["data"]["boards"][0]["items_page"]["items"]

def get_monday_user_id(token, name):
    result = run_query(token, "{ users { id name } }")
    users = result["data"]["users"]
    for user in users:
        if name.lower() in user["name"].lower():
            return user["id"]
    return None

def create_item(token, task_name, owner_name=None, due_date=None, status=None):
    column_values = {}

    if owner_name:
        user_id = get_monday_user_id(token, owner_name)
        if user_id:
            column_values["person"] = {
                "personsAndTeams": [{"id": user_id, "kind": "person"}]
            }
        else:
            print(f"  Warning: no monday.com user found for '{owner_name}'")

    # Use today's date automatically if no due_date provided from message
    task_date = due_date if due_date else str(date.today())
    column_values["date_mm4ttba6"] = {"date": task_date}

    if status:
        column_values["status"] = {"label": status}

    col_values_json = json.dumps(json.dumps(column_values))

    query = """
    mutation {
      create_item(
        board_id: """ + BOARD_ID + """
        group_id: \"""" + PLANNING_GROUP_ID + """\"
        item_name: """ + json.dumps(task_name) + """
        column_values: """ + col_values_json + """
      ) {
        id
        name
      }
    }
    """
    result = run_query(token, query)
    if "errors" in result:
        print("Error creating item:", result["errors"])
        return None
    item = result["data"]["create_item"]
    print(f"  Created: '{item['name']}' (ID: {item['id']}) on date: {task_date}")
    return item

def update_item(token, item_id, owner_name=None, due_date=None, status=None):
    column_values = {}

    if owner_name:
        user_id = get_monday_user_id(token, owner_name)
        if user_id:
            column_values["person"] = {
                "personsAndTeams": [{"id": user_id, "kind": "person"}]
            }

    if due_date:
        column_values["date_mm4ttba6"] = {"date": due_date}

    if status:
        column_values["status"] = {"label": status}

    if not column_values:
        print("  Nothing to update.")
        return

    col_values_json = json.dumps(json.dumps(column_values))

    query = """
    mutation {
      change_multiple_column_values(
        board_id: """ + BOARD_ID + """
        item_id: """ + str(item_id) + """
        column_values: """ + col_values_json + """
      ) {
        id
        name
      }
    }
    """
    result = run_query(token, query)
    if "errors" in result:
        print("Error updating item:", result["errors"])
        return None
    item = result["data"]["change_multiple_column_values"]
    print(f"  Updated: '{item['name']}' (ID: {item['id']})")
    return item

def post_update(token, item_id, text):
    query = """
    mutation {
      create_update(
        item_id: """ + str(item_id) + """
        body: """ + json.dumps(text) + """
      ) {
        id
      }
    }
    """
    result = run_query(token, query)
    if "errors" in result:
        print("Error posting update:", result["errors"])
        return None
    print(f"  Comment posted on item {item_id}")
    return result

if __name__ == "__main__":
    token = os.environ.get("MONDAY_API_TOKEN")
    if not token:
        print("ERROR: Set MONDAY_API_TOKEN first.")
        raise SystemExit(1)

    print("Step 1: Fetching existing items...")
    items = get_open_items(token)
    for item in items:
        print(f"  [{item['id']}] {item['name']}")

    print("\nStep 2: Creating new item in Planning group...")
    new_item = create_item(
        token,
        task_name="Test task from Slack",
        owner_name=None,
        status="Working on it"
        # no due_date passed — will use today automatically
    )

    if new_item:
        print("\nStep 3: Posting a comment on that item...")
        post_update(token, new_item["id"], "This task was auto-created from Slack.")

    print("\nDone. Check your monday.com Planning section.")

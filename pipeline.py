import os, json, urllib.request, re
from datetime import date

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MONDAY_API_URL = "https://api.monday.com/v2"
BOARD_ID = "5029590799"
PLANNING_GROUP_ID = "topics"

def run_monday(token, query):
    data = json.dumps({"query": query}).encode()
    req = urllib.request.Request(MONDAY_API_URL, data=data, headers={"Content-Type": "application/json", "Authorization": token}, method="POST")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode())

def get_open_items(token):
    result = run_monday(token, '{ boards(ids: [' + BOARD_ID + ']) { items_page(limit: 50) { items { id name } } } }')
    return result["data"]["boards"][0]["items_page"]["items"]

def get_user_id(token, name):
    result = run_monday(token, "{ users { id name } }")
    for user in result["data"]["users"]:
        if name.lower() in user["name"].lower():
            return user["id"]
    return None

def create_item(token, task_name, owner_name=None, due_date=None, status=None):
    col = {}
    if owner_name:
        uid = get_user_id(token, owner_name)
        if uid:
            col["person"] = {"personsAndTeams": [{"id": uid, "kind": "person"}]}
    col["date_mm4ttba6"] = {"date": due_date if due_date else str(date.today())}
    if status:
        col["status"] = {"label": status}
    col_json = json.dumps(json.dumps(col))
    result = run_monday(token, 'mutation { create_item(board_id: ' + BOARD_ID + ', group_id: "' + PLANNING_GROUP_ID + '", item_name: ' + json.dumps(task_name) + ', column_values: ' + col_json + ') { id name } }')
    if "errors" in result:
        print("  Error:", result["errors"])
        return None
    item = result["data"]["create_item"]
    print(f"  Created: '{item['name']}' (ID: {item['id']})")
    return item

def update_item(token, item_id, owner_name=None, due_date=None, status=None):
    col = {}
    if owner_name:
        uid = get_user_id(token, owner_name)
        if uid:
            col["person"] = {"personsAndTeams": [{"id": uid, "kind": "person"}]}
    if due_date:
        col["date_mm4ttba6"] = {"date": due_date}
    if status:
        col["status"] = {"label": status}
    if not col:
        print("  Nothing to update.")
        return
    col_json = json.dumps(json.dumps(col))
    result = run_monday(token, 'mutation { change_multiple_column_values(board_id: ' + BOARD_ID + ', item_id: ' + str(item_id) + ', column_values: ' + col_json + ') { id name } }')
    if "errors" in result:
        print("  Error:", result["errors"])
        return None
    item = result["data"]["change_multiple_column_values"]
    print(f"  Updated: '{item['name']}' (ID: {item['id']})")
    return item

def post_comment(token, item_id, text):
    result = run_monday(token, 'mutation { create_update(item_id: ' + str(item_id) + ', body: ' + json.dumps(text) + ') { id } }')
    if "errors" in result:
        print("  Error posting comment:", result["errors"])
        return None
    print(f"  Comment posted on item {item_id}")
    return result

def extract_task(anthropic_token, message, existing_items, is_reply=False):
    items_list = "\n".join([f"- [{i['id']}] {i['name']}" for i in existing_items])
    context = "This message is a REPLY in an existing Slack thread." if is_reply else "This message is a NEW message in a Slack channel."
    prompt = f"""You are a task extraction assistant for a team using Slack and monday.com.
{context}

Existing open tasks:
{items_list}

Slack message: {message}

Rules:
- If REPLY: action must be "update" or "none", never "create"
- If new message with new task: action = "create"
- If new message about existing task: action = "update" with item_id
- If small talk only: action = "none"
- due_date: YYYY-MM-DD or null
- status: "Working on it" or "Done" or "Stuck" or null
- owner: person name or null

Return ONLY JSON, no markdown, no explanation:
{{"action": "create/update/none", "item_id": null, "task_name": null, "owner": null, "due_date": null, "status": null}}"""

    data = json.dumps({"model": "claude-sonnet-4-6", "max_tokens": 500, "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(ANTHROPIC_API_URL, data=data, headers={"Content-Type": "application/json", "x-api-key": anthropic_token, "anthropic-version": "2023-06-01"}, method="POST")
    with urllib.request.urlopen(req) as r:
        result = json.loads(r.read().decode())
    raw = result["content"][0]["text"].strip()
    raw = re.sub(r"^`json\s*|^`\s*|\s*`$", "", raw).strip()
    if not raw:
        return {"action": "none", "item_id": None, "task_name": None, "owner": None, "due_date": None, "status": None}
    return json.loads(raw)

def process_message(monday_token, anthropic_token, message, is_reply=False, parent_item_id=None):
    print(f"\n{'='*55}")
    print(f"Message: {message}")
    print(f"Type:    {'Thread reply' if is_reply else 'New message'}")
    print(f"{'='*55}")
    print("Step 1: Fetching open items...")
    open_items = get_open_items(monday_token)
    print(f"  Found {len(open_items)} items")
    print("Step 2: Asking Claude to extract task info...")
    extracted = extract_task(anthropic_token, message, open_items, is_reply)
    print(f"  Action: {extracted['action']} | Task: {extracted.get('task_name')} | Owner: {extracted.get('owner')} | Date: {extracted.get('due_date')} | Status: {extracted.get('status')}")
    action = extracted["action"]
    if action == "none":
        print("Step 3: Small talk — skipping.")
        return None
    if action == "create":
        print("Step 3: Creating new item on monday.com...")
        return create_item(monday_token, extracted["task_name"], extracted.get("owner"), extracted.get("due_date"), extracted.get("status"))
    if action == "update":
        item_id = parent_item_id or extracted.get("item_id")
        if not item_id:
            print("Step 3: Update requested but no item ID — skipping.")
            return None
        print(f"Step 3: Updating item {item_id}...")
        update_item(monday_token, item_id, extracted.get("owner"), extracted.get("due_date"), extracted.get("status"))
        print("Step 4: Posting reply as comment...")
        post_comment(monday_token, item_id, f"Slack update: {message}")
        return None

monday_token = os.environ.get("MONDAY_API_TOKEN")
anthropic_token = os.environ.get("ANTHROPIC_API_TOKEN")

if not monday_token:
    print("ERROR: Set MONDAY_API_TOKEN first.")
    raise SystemExit(1)
if not anthropic_token:
    print("ERROR: Set ANTHROPIC_API_TOKEN first.")
    raise SystemExit(1)

print("Running pipeline tests...\n")

print("TEST 1: New task message")
new_item = process_message(monday_token, anthropic_token, "John needs to send the proposal to Client A by July 20", is_reply=False)

print("\nTEST 2: Small talk")
process_message(monday_token, anthropic_token, "Good morning everyone!", is_reply=False)

print("\nTEST 3: Thread reply on existing item")
process_message(monday_token, anthropic_token, "Done! Sent the proposal just now", is_reply=True, parent_item_id="2762891739")

print("\n\nAll tests done. Check your monday.com board now.")

import os, json, hmac, hashlib, time, re, urllib.request, threading
from datetime import date, datetime
from flask import Flask, request, jsonify

app = Flask(__name__)

MONDAY_API_URL  = "https://api.monday.com/v2"
MONDAY_WORKSPACE_ID = "2080090"
THREAD_MAP_FILE = "thread_map.json"
CONFIG_FILE     = "config.json"
BOT_USER_ID     = "U0BF0UG9MA4"

MONDAY_TOKEN         = os.environ.get("MONDAY_API_TOKEN")
SLACK_BOT_TOKEN      = os.environ.get("SLACK_BOT_TOKEN")
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET")

with open(CONFIG_FILE) as f:
    CONFIG = json.load(f)

def reload_config():
    global CONFIG
    with open(CONFIG_FILE) as f:
        CONFIG = json.load(f)
    print("  CONFIG reloaded.")

def save_config():
    with open(CONFIG_FILE, "w") as f:
        json.dump(CONFIG, f, indent=2)
    reload_config()
    try:
        import subprocess
        subprocess.run(["git", "add", "config.json"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "auto: update config.json"], check=True, capture_output=True)
        subprocess.run(["git", "push"], check=True, capture_output=True)
        print("  config.json pushed to GitHub.")
    except Exception as e:
        print("  GitHub push failed (non-critical):", e)

def get_board_cfg_by_channel(channel_id):
    for name, cfg in CONFIG["boards"].items():
        if cfg.get("channel_id") == channel_id:
            return name, cfg
    return None, None

REPLY_MAP_FILE = "reply_map.json"

def load_reply_map():
    if os.path.exists(REPLY_MAP_FILE):
        with open(REPLY_MAP_FILE) as f:
            return json.load(f)
    return {}

def save_reply_map(rm):
    with open(REPLY_MAP_FILE, "w") as f:
        json.dump(rm, f, indent=2)

def load_thread_map():
    if os.path.exists(THREAD_MAP_FILE):
        with open(THREAD_MAP_FILE) as f:
            return json.load(f)
    return {}

def save_thread_map(tm):
    with open(THREAD_MAP_FILE, "w") as f:
        json.dump(tm, f, indent=2)

def verify_slack_signature(req):
    timestamp = req.headers.get("X-Slack-Request-Timestamp", "")
    slack_sig = req.headers.get("X-Slack-Signature", "")
    if not timestamp or not slack_sig:
        return False
    if abs(time.time() - int(timestamp)) > 300:
        return False
    base = "v0:" + timestamp + ":" + req.get_data(as_text=True)
    computed = "v0=" + hmac.new(SLACK_SIGNING_SECRET.encode(), base.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, slack_sig)

def get_channel_name(channel_id):
    url = "https://slack.com/api/conversations.info?channel=" + channel_id
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + SLACK_BOT_TOKEN}, method="GET")
    with urllib.request.urlopen(req) as r:
        result = json.loads(r.read().decode())
    if result.get("ok"):
        return result["channel"]["name"]
    print("  Channel name error:", result.get("error"))
    return None

def post_slack_message(channel_id, text, thread_ts=None):
    payload = {"channel": channel_id, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    data = json.dumps(payload).encode()
    req = urllib.request.Request("https://slack.com/api/chat.postMessage", data=data, headers={"Content-Type": "application/json", "Authorization": "Bearer " + SLACK_BOT_TOKEN}, method="POST")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode())

def run_monday(query):
    data = json.dumps({"query": query}).encode()
    req = urllib.request.Request(MONDAY_API_URL, data=data, headers={"Content-Type": "application/json", "Authorization": MONDAY_TOKEN}, method="POST")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode())

def get_user_id(name):
    result = run_monday("{ users { id name } }")
    for user in result["data"]["users"]:
        if name.lower() in user["name"].lower():
            return user["id"]
    return None

def fetch_board_status_labels(board_id):
    result = run_monday("{ boards(ids: [" + board_id + "]) { columns { id title type settings_str } } }")
    labels = []
    for col in result["data"]["boards"][0]["columns"]:
        if col["type"] == "status":
            try:
                settings = json.loads(col["settings_str"])
                for v in settings.get("labels", {}).values():
                    if v:
                        labels.append(v)
            except:
                pass
    print("  Status labels:", labels)
    return labels

def format_board_name(channel_name):
    words = channel_name.replace("-", " ").replace("_", " ").split()
    return " ".join(word.capitalize() for word in words)

def create_monday_board(board_name):
    print("  Creating board:", board_name)
    result = run_monday("mutation { create_board(board_name: " + json.dumps(board_name) + ", board_kind: public, workspace_id: " + MONDAY_WORKSPACE_ID + ") { id name } }")
    if "errors" in result:
        print("  Error:", result["errors"])
        return None
    board_id = result["data"]["create_board"]["id"]
    print("  Board ID:", board_id)
    for g in ["Execution", "Closure"]:
        run_monday("mutation { create_group(board_id: " + board_id + ", group_name: " + json.dumps(g) + ") { id } }")
    for t, c in [("Owner", "people"), ("Status", "status"), ("Task Date", "date"), ("Complete Date", "date"), ("Time Tracking", "time_tracking"), ("Time Spent", "numbers")]:
        run_monday("mutation { create_column(board_id: " + board_id + ", title: " + json.dumps(t) + ", column_type: " + c + ") { id } }")
    ins = run_monday("{ boards(ids: [" + board_id + "]) { columns { id type } groups { id } } }")
    bd = ins["data"]["boards"][0]
    group_id = bd["groups"][0]["id"]
    date_cols = [c["id"] for c in bd["columns"] if c["type"] == "date"]
    date_col_id = date_cols[0] if date_cols else "date0"
    complete_date_col_id = date_cols[1] if len(date_cols) > 1 else None
    status_col_id = next((c["id"] for c in bd["columns"] if c["type"] == "status"), "status")
    owner_col_id = next((c["id"] for c in bd["columns"] if c["type"] == "people"), "person")
    print("  Group:", group_id, "Date col:", date_col_id, "Complete date col:", complete_date_col_id)
    time_col_id = next((c["id"] for c in bd["columns"] if c["type"] == "time_tracking"), None)
    time_spent_col_id = next((c["id"] for c in bd["columns"] if c["type"] == "numbers"), None)
    return {"board_id": board_id, "group_id": group_id, "date_col_id": date_col_id, "complete_date_col_id": complete_date_col_id, "status_col_id": status_col_id, "owner_col_id": owner_col_id, "time_col_id": time_col_id, "time_spent_col_id": time_spent_col_id}

def create_monday_item(board_id, group_id, date_col_id, task_name, owner_name=None, due_date=None, status=None, status_col_id="status", owner_col_id="person", time_spent=None, time_spent_col_id=None):
    col = {}
    if owner_name:
        uid = get_user_id(owner_name)
        if uid:
            col[owner_col_id] = {"personsAndTeams": [{"id": uid, "kind": "person"}]}
    col[date_col_id] = {"date": due_date if due_date else str(date.today())}
    if status:
        col[status_col_id] = {"label": status}
    if time_spent and time_spent_col_id:
        col[time_spent_col_id] = time_spent
    col_json = json.dumps(json.dumps(col))
    result = run_monday("mutation { create_item(board_id: " + board_id + ", group_id: \"" + group_id + "\", item_name: " + json.dumps(task_name) + ", column_values: " + col_json + ") { id name } }")
    if "errors" in result:
        print("  Error creating:", result["errors"])
        return None
    item = result["data"]["create_item"]
    print("  Created:", item["name"], "ID:", item["id"])
    return item

def update_monday_item(board_id, date_col_id, item_id, task_name=None, owner_name=None, due_date=None, status=None, status_col_id="status", owner_col_id="person"):
    if task_name:
        run_monday("mutation { change_simple_column_value(board_id: " + board_id + ", item_id: " + str(item_id) + ", column_id: \"name\", value: " + json.dumps(task_name) + ") { id } }")
        print("  Name updated:", task_name)
    col = {}
    if owner_name:
        uid = get_user_id(owner_name)
        if uid:
            col[owner_col_id] = {"personsAndTeams": [{"id": uid, "kind": "person"}]}
    if due_date:
        col[date_col_id] = {"date": due_date}
    if status:
        col[status_col_id] = {"label": status}
    if not col:
        return None
    col_json = json.dumps(json.dumps(col))
    result = run_monday("mutation { change_multiple_column_values(board_id: " + board_id + ", item_id: " + str(item_id) + ", column_values: " + col_json + ") { id name } }")
    if "errors" in result:
        print("  Error updating:", result["errors"])
        return None
    print("  Updated:", result["data"]["change_multiple_column_values"]["name"])
    return result

def update_monday_status(board_id, item_id, status, status_col_id="status"):
    col_json = json.dumps(json.dumps({status_col_id: {"label": status}}))
    result = run_monday("mutation { change_multiple_column_values(board_id: " + board_id + ", item_id: " + str(item_id) + ", column_values: " + col_json + ") { id name } }")
    if "errors" in result:
        print("  Error status:", result["errors"])
        return None
    print("  Status ->", status)
    return result

def delete_monday_comment(comment_id):
    import json as _json
    payload = {"query": "mutation { delete_update(id: " + str(comment_id) + ") { id } }"}
    import urllib.request as _req
    data = _json.dumps(payload).encode()
    req = _req.Request(MONDAY_API_URL, data=data, headers={"Content-Type": "application/json", "Authorization": MONDAY_TOKEN}, method="POST")
    try:
        with _req.urlopen(req) as r:
            result = _json.loads(r.read().decode())
        if "errors" in result:
            print("  Error deleting comment:", result["errors"])
            return False
        print("  Comment deleted ID:", comment_id)
        return True
    except Exception as e:
        print("  Delete comment failed:", e)
        return False

def post_monday_comment(item_id, text):
    result = run_monday("mutation { create_update(item_id: " + str(item_id) + ", body: " + json.dumps(text) + ") { id } }")
    if "errors" in result:
        print("  Error comment:", result["errors"])
        return None
    comment_id = result["data"]["create_update"]["id"]
    print("  Comment posted ID:", comment_id, "text:", text[:50])
    return comment_id
def clean_slack_text(text):
    import re as _re
    text = _re.sub(r'<(https?://[^|>]+)\|([^>]+)>', r'\2: \1', text)
    text = _re.sub(r'<(https?://[^>]+)>', r'\1', text)
    text = _re.sub(r'<@[A-Z0-9]+>', '', text)
    text = _re.sub(r'<#[A-Z0-9]+\|([^>]+)>', r'#\1', text)
    return text.strip()

def is_task_message(message):
    return bool(re.match(r"^task[:\s]", message.strip(), re.IGNORECASE))

def parse_task_message(message):
    body = re.sub(r"^task[:\s]+", "", message.strip(), flags=re.IGNORECASE).strip()
    parts = [p.strip() for p in body.split("|")]
    task_name = parts[0].strip()
    owner, due_date, status = None, None, None
    for part in parts[1:]:
        if ":" not in part:
            continue
        key, _, value = part.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "owner":
            owner = value
        elif key == "date":
            if value.lower() == "today":
                due_date = str(date.today())
            else:
                try:
                    datetime.strptime(value, "%Y-%m-%d")
                    due_date = value
                except ValueError:
                    due_date = str(date.today())
        elif key == "status":
            val = value.lower()
            if val in ["working", "working on it", "in progress", "wip"]:
                status = "Working on it"
            elif val in ["done", "completed", "finished"]:
                status = "Done"
            elif val in ["stuck", "blocked"]:
                status = "Stuck"
            elif val in ["not started"]:
                status = "Not Started"
            elif val in ["on hold", "hold", "paused"]:
                status = "On Hold"
            else:
                status = "Working on it"
    if not due_date:
        due_date = str(date.today())
    if not status:
        status = "Working on it"
    time_spent = None
    for part in parts[1:]:
        if ":" not in part:
            continue
        k, _, v = part.partition(":")
        k = k.strip().lower()
        v = v.strip()
        if k in ["time", "time spent", "duration"]:
            time_spent = parse_time_from_reply("time: " + v)
    return {"task_name": task_name, "owner": owner, "due_date": due_date, "status": status, "time_spent": time_spent}

def parse_time_from_reply(message):
    import re as _re
    msg = message.lower().strip()
    if not msg.startswith("time:"):
        return None
    time_str = msg.replace("time:", "").strip()
    hours = 0
    minutes = 0
    h_match = _re.search(r"(\d+)\s*h", time_str)
    m_match = _re.search(r"(\d+)\s*m", time_str)
    if h_match:
        hours = int(h_match.group(1))
    if m_match:
        minutes = int(m_match.group(1))
    total_hours = hours + (minutes / 60)
    if total_hours == 0:
        return None
    print("  Time parsed:", hours, "h", minutes, "m =", total_hours, "hours")
    return total_hours

def detect_status_from_reply(message, valid_labels):
    msg = message.lower().strip()
    # map keywords to monday.com label names
    keyword_map = [
        (["done", "completed", "finished", "complete"], "Done"),
        (["working on it", "in progress", "wip", "working", "will do", "will do later"], "Working on it"),
        (["stuck", "blocked", "issue", "problem"], "Stuck"),
        (["not started", "not yet"], "Not Started"),
        (["on hold", "hold", "paused", "pause"], "On Hold"),
    ]
    for keywords, label in keyword_map:
        for keyword in keywords:
            if keyword in msg:
                # check if this label exists on the board
                if label in valid_labels:
                    print("  Keyword matched:", keyword, "->", label)
                    return label
                # try case-insensitive match
                for valid in valid_labels:
                    if valid.lower() == label.lower():
                        print("  Keyword matched (case):", keyword, "->", valid)
                        return valid
    return None

def handle_bot_joined(channel_id):
    print("\n[BOT JOINED] channel:", channel_id)
    _, existing_cfg = get_board_cfg_by_channel(channel_id)
    if existing_cfg:
        print("  Already configured.")
        return
    channel_name = get_channel_name(channel_id)
    if not channel_name:
        print("  Could not fetch channel name.")
        return
    print("  Channel:", channel_name)
    board_name = format_board_name(channel_name)
    board_info = create_monday_board(board_name)
    if not board_info:
        post_slack_message(channel_id, "Sorry, could not create monday.com board.")
        return
    CONFIG["boards"][channel_name] = {
        "channel_id": channel_id,
        "board_id": board_info["board_id"],
        "group_id": board_info["group_id"],
        "date_col_id": board_info["date_col_id"],
        "complete_date_col_id": board_info.get("complete_date_col_id"),
        "status_col_id": board_info["status_col_id"],
        "owner_col_id": board_info["owner_col_id"],
        "time_col_id": board_info.get("time_col_id"),
        "time_spent_col_id": board_info.get("time_spent_col_id")
    }
    save_config()
    print("  Done. CONFIG updated in memory.")
    post_slack_message(channel_id, "Hi! Board *" + board_name + "* created on monday.com and linked to this channel.\n\nCreate a task:\ntask: Task name | owner: John | date: 2026-07-15\n\nReply in thread to update status or add comments.")

def process_event(event, channel_id, channel_name, board_cfg):
    board_id       = board_cfg["board_id"]
    group_id       = board_cfg["group_id"]
    date_col_id    = board_cfg["date_col_id"]
    status_col_id       = board_cfg.get("status_col_id", "status")
    complete_date_col_id = board_cfg.get("complete_date_col_id")
    time_col_id          = board_cfg.get("time_col_id")
    time_spent_col_id    = board_cfg.get("time_spent_col_id")
    owner_col_id   = board_cfg.get("owner_col_id", "person")
    message     = clean_slack_text(event.get("text", "").strip())
    ts          = event.get("ts")
    thread_ts   = event.get("thread_ts")
    subtype     = event.get("subtype")
    thread_map  = load_thread_map()

    if subtype == "message_changed":
        new_message = clean_slack_text(event.get("message", {}).get("text", "").strip())
        original_ts = event.get("message", {}).get("ts")
        thread_ts_of_edit = event.get("message", {}).get("thread_ts")
        print("\n[EDIT]", channel_name, new_message[:60])
        if not original_ts:
            return

        # check if this is a reply edit (has thread_ts different from ts)
        is_reply_edit = thread_ts_of_edit is not None and thread_ts_of_edit != original_ts

        if is_reply_edit:
            parent_item_id = thread_map.get(thread_ts_of_edit)
            if parent_item_id:
                print("  Reply edit detected — deleting old comment and creating new...")
                reply_map = load_reply_map()
                old_comment_id = reply_map.get(original_ts)
                if old_comment_id:
                    delete_monday_comment(old_comment_id)
                new_comment_id = post_monday_comment(parent_item_id, new_message)
                if new_comment_id:
                    reply_map[original_ts] = new_comment_id
                    save_reply_map(reply_map)
                valid_labels = fetch_board_status_labels(board_id)
                detected_status = detect_status_from_reply(new_message, valid_labels)
                if detected_status:
                    print("  Status from edited reply:", detected_status)
                    update_monday_status(board_id, parent_item_id, detected_status, status_col_id=status_col_id)
                    if detected_status == "Done" and complete_date_col_id:
                        today = str(__import__("datetime").date.today())
                        col_json = __import__("json").dumps(__import__("json").dumps({complete_date_col_id: {"date": today}}))
                        run_monday("mutation { change_multiple_column_values(board_id: " + board_id + ", item_id: " + str(parent_item_id) + ", column_values: " + col_json + ") { id } }")
            else:
                print("  Parent not a task — ignoring reply edit.")
            return

        existing_item_id = thread_map.get(original_ts)
        if is_task_message(new_message):
            parsed = parse_task_message(new_message)
            print("  Parsed:", parsed)
            if existing_item_id:
                update_monday_item(board_id, date_col_id, existing_item_id, task_name=parsed["task_name"], owner_name=parsed.get("owner"), due_date=parsed.get("due_date"), status=parsed.get("status"), status_col_id=status_col_id, owner_col_id=owner_col_id)
            else:
                item = create_monday_item(board_id, group_id, date_col_id, task_name=parsed["task_name"], owner_name=parsed.get("owner"), due_date=parsed.get("due_date"), status=parsed.get("status"), status_col_id=status_col_id, owner_col_id=owner_col_id)
                if item:
                    thread_map[original_ts] = item["id"]
                    save_thread_map(thread_map)
                    post_slack_message(channel_id, "Task *" + item["name"] + "* added to monday.com", thread_ts=original_ts)
        else:
            print("  Not a task or prefix removed — ignoring.")
        return

    is_reply = thread_ts is not None and thread_ts != ts
    if is_reply:
        print("\n[REPLY]", channel_name, message[:60])
        parent_item_id = thread_map.get(thread_ts)
        if not parent_item_id:
            print("  Parent not a task — ignoring.")
            return
        # check for time: command first
        time_hours = parse_time_from_reply(message)
        if time_hours and time_spent_col_id:
            print("  Logging time:", time_hours, "hours on item", parent_item_id)
            col_json = __import__("json").dumps(__import__("json").dumps({time_spent_col_id: time_hours}))
            run_monday("mutation { change_multiple_column_values(board_id: " + board_id + ", item_id: " + str(parent_item_id) + ", column_values: " + col_json + ") { id } }")
            comment_id = post_monday_comment(parent_item_id, "Time logged: " + message.split(":", 1)[1].strip())
            if comment_id:
                reply_map = load_reply_map()
                reply_map[ts] = comment_id
                save_reply_map(reply_map)
            return
        reply_map = load_reply_map()
        comment_id = post_monday_comment(parent_item_id, message)
        if comment_id:
            reply_map[ts] = comment_id
            save_reply_map(reply_map)
        valid_labels = fetch_board_status_labels(board_id)
        detected_status = detect_status_from_reply(message, valid_labels)
        if detected_status:
            print("  Status detected:", detected_status)
            update_monday_status(board_id, parent_item_id, detected_status, status_col_id=status_col_id)
            if detected_status == "Done" and complete_date_col_id:
                today = str(__import__("datetime").date.today())
                col_json = __import__("json").dumps(__import__("json").dumps({complete_date_col_id: {"date": today}}))
                run_monday("mutation { change_multiple_column_values(board_id: " + board_id + ", item_id: " + str(parent_item_id) + ", column_values: " + col_json + ") { id } }")
                print("  Complete date set to:", today)
        else:
            print("  No status keyword — comment only.")
        return

    print("\n[NEW]", channel_name, message[:60])
    if not is_task_message(message):
        print("  Not a task — ignoring.")
        return
    parsed = parse_task_message(message)
    print("  Parsed:", parsed)
    item = create_monday_item(board_id, group_id, date_col_id, task_name=parsed["task_name"], owner_name=parsed.get("owner"), due_date=parsed.get("due_date"), status=parsed.get("status"), status_col_id=status_col_id, owner_col_id=owner_col_id, time_spent=parsed.get("time_spent"), time_spent_col_id=time_spent_col_id)
    if item:
        thread_map[ts] = item["id"]
        save_thread_map(thread_map)
        post_slack_message(channel_id, "Task *" + item["name"] + "* added to monday.com", thread_ts=ts)

@app.route("/slack/events", methods=["POST"])
def slack_events():
    if not verify_slack_signature(request):
        return jsonify({"error": "invalid signature"}), 401
    payload = request.json
    if payload.get("type") == "url_verification":
        return jsonify({"challenge": payload["challenge"]})
    event = payload.get("event", {})
    if event.get("type") == "member_joined_channel":
        if event.get("user") == BOT_USER_ID:
            threading.Thread(target=handle_bot_joined, args=(event.get("channel"),)).start()
        return jsonify({"ok": True})
    if event.get("type") != "message":
        return jsonify({"ok": True})
    if event.get("bot_id"):
        return jsonify({"ok": True})
    channel_id = event.get("channel")
    channel_name, board_cfg = get_board_cfg_by_channel(channel_id)
    if not channel_name:
        print("Unknown channel:", channel_id)
        return jsonify({"ok": True})
    subtype = event.get("subtype")
    message = clean_slack_text(event.get("message", {}).get("text", "").strip()) if subtype == "message_changed" else clean_slack_text(event.get("text", "").strip())
    if not message:
        return jsonify({"ok": True})
    threading.Thread(target=process_event, args=(event, channel_id, channel_name, board_cfg)).start()
    return jsonify({"ok": True})

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "running", "boards": list(CONFIG["boards"].keys()), "claude": False, "pure_python": True, "auto_board": True})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    print("Server starting on port", port)
    print("Boards:", list(CONFIG["boards"].keys()))
    print("Bot User ID:", BOT_USER_ID)
    print("Mode: Pure Python | No Claude | Auto board creation ON")
    app.run(host="0.0.0.0", port=port, debug=False)
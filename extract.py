import os, json, urllib.request, re

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"

def extract_task(anthropic_token, message, existing_items, is_reply=False):
    items_list = "\n".join([f"- [{i['id']}] {i['name']}" for i in existing_items])

    if is_reply:
        context = "This message is a REPLY in an existing Slack thread about a task already created on monday.com."
    else:
        context = "This message is a NEW message in a Slack channel."

    prompt = f"""You are a task extraction assistant for a team using Slack and monday.com.
{context}

Existing open tasks on the board:
{items_list}

Slack message:
{message}

Rules:
- If this is a reply, action should almost always be "update" unless it has zero task info
- If new message describes a task not in the list, set action to "create"
- If new message refers to an existing task, set action to "update" with its item_id
- If message is pure small talk with no task info at all, set action to "none"
- due_date must be in YYYY-MM-DD format or null
- status must be one of: "Working on it", "Done", "Stuck", or null
- owner is the person name mentioned, or null if not mentioned

Return ONLY this JSON, no explanation, no markdown, no code blocks, no extra text:
{{
  "action": "create",
  "item_id": null,
  "task_name": "task name or null",
  "owner": "person name or null",
  "due_date": "YYYY-MM-DD or null",
  "status": "Working on it or Done or Stuck or null"
}}"""

    data = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 500,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()

    req = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "x-api-key": anthropic_token,
            "anthropic-version": "2023-06-01"
        },
        method="POST"
    )
    with urllib.request.urlopen(req) as r:
        result = json.loads(r.read().decode())

    raw = result["content"][0]["text"].strip()
    print(f"        raw response: {raw[:100]}")

    # Strip markdown code blocks if Claude wrapped the JSON
    raw = re.sub(r"^`json\s*", "", raw)
    raw = re.sub(r"^`\s*", "", raw)
    raw = re.sub(r"\s*`$", "", raw)
    raw = raw.strip()

    if not raw:
        print("        WARNING: empty response from Claude")
        return {"action": "none", "item_id": None, "task_name": None, "owner": None, "due_date": None, "status": None}

    return json.loads(raw)


token = os.environ.get("ANTHROPIC_API_TOKEN")
if not token:
    print("ERROR: Set ANTHROPIC_API_TOKEN first.")
    raise SystemExit(1)

existing_items = [
    {"id": "2762891739", "name": "Prepare project brief"},
    {"id": "2762891738", "name": "Research"},
    {"id": "2762891740", "name": "Kickoff"},
]

tests = [
    ("John needs to send the proposal to client by July 15", False),
    ("Research task is done, wrapped it up this morning", False),
    ("Good morning everyone, hope you had a great weekend!", False),
    ("Actually deadline moved to July 20, still working on it", True),
    ("Done! Sent the proposal just now", True),
]

print("Testing Claude extraction...\n")
for msg, is_reply in tests:
    kind = "REPLY" if is_reply else "NEW  "
    print(f"[{kind}] {msg}")
    result = extract_task(token, msg, existing_items, is_reply)
    print(f"        action:  {result['action']}")
    print(f"        task:    {result.get('task_name')}")
    print(f"        owner:   {result.get('owner')}")
    print(f"        date:    {result.get('due_date')}")
    print(f"        status:  {result.get('status')}")
    print()

import os, sys, json, urllib.request

MONDAY_API_URL = "https://api.monday.com/v2"

def run_query(token, query, variables=None):
    payload = {"query": query, "variables": variables or {}}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(MONDAY_API_URL, data=data, headers={"Content-Type": "application/json", "Authorization": token}, method="POST")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode("utf-8"))

token = os.environ.get("MONDAY_API_TOKEN")
if not token:
    print("ERROR: token not set")
    sys.exit(1)

board_id = sys.argv[1] if len(sys.argv) > 1 else "5029590800"
print(f"Querying board {board_id}...")

result = run_query(token, '{ boards(ids: [' + board_id + ']) { id name columns { id title type } groups { id title } items_page(limit:10) { items { id name } } } }')

board = result["data"]["boards"][0]
print(f"\nBoard: {board['name']} (ID: {board['id']})\n")
print("Columns:")
for col in board["columns"]:
    print(f"  {col['title']:<20} type: {col['type']:<15} id: {col['id']}")
print("\nGroups:")
for g in board["groups"]:
    print(f"  {g['title']:<25} id: {g['id']}")
print("\nItems:")
for item in board.get("items_page", {}).get("items", []):
    print(f"  {item['id']} - {item['name']}")

import os
import urllib.request
import json

from dotenv import load_dotenv

load_dotenv()

req = urllib.request.Request(
    "https://api.mistral.ai/v1/models",
    headers={"Authorization": f"Bearer {os.environ['MISTRAL_API_KEY']}"},
)
with urllib.request.urlopen(req) as r:
    data = json.load(r)

for m in data["data"]:
    print(m["id"])

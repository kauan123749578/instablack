import json
import re
from pathlib import Path

p = Path(
    r"C:/Users/kauan/.cursor/projects/c-Users-kauan-Downloads-instagram"
    "/agent-transcripts/2374b53b-0fed-4ce4-9a79-164a9290d29a"
    "/2374b53b-0fed-4ce4-9a79-164a9290d29a.jsonl"
)
out = Path(__file__).parent / "_transcript_confirm_snip.txt"
for line in p.read_text(encoding="utf-8").splitlines():
    if "useCAAFBConfirmationFormSubmitMutation" in line and line.startswith('{"role":"user"'):
        t = json.loads(line)["message"]["content"][0]["text"]
        out.write_text(t[:15000], encoding="utf-8")
        print("wrote", out, "len", len(t))
        for m in re.finditer(r"doc_id[^\d]{0,20}(\d{10,})", t):
            print("doc_id", m.group(1))
        break
else:
    print("not found")

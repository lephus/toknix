"""A stand-in for `claude -p PROMPT --output-format json` (P4-T3 tests).

Behaviour comes from the environment so a test can describe each arm of a benchmark:
  FAKE_SCALE         multiplies the token usage (default 1)
  FAKE_OUTPUT_SCALE  multiplies output tokens only (default 1)
  FAKE_NOISE         relative noise, e.g. 0.05 = ±5 % (default 0), deterministic per task and repeat
  FAKE_PASS          "all" (default), "none", or "half" (every other repeat fails)
  FAKE_SLEEP         seconds to sleep first (to trigger the bench timeout)
The task is solved by writing done.txt in the working directory; the bench check looks for it.
  FAKE_SEEN          a file to copy the worktree's CLAUDE.md into (did --rules reach it?)
"""
import hashlib
import json
import os
import sys
import time

args = sys.argv[1:]
prompt = args[args.index("-p") + 1] if "-p" in args else ""
rep = int(os.environ.get("TOKOPT_BENCH_REPEAT", "0"))
seed = int(hashlib.sha256(f"{prompt}|{rep}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF  # 0..1
noise = 1 + float(os.environ.get("FAKE_NOISE", "0")) * (2 * seed - 1)
scale = float(os.environ.get("FAKE_SCALE", "1")) * noise
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))

mode = os.environ.get("FAKE_PASS", "all")
if mode == "all" or (mode == "half" and rep % 2 == 0):
    with open("done.txt", "w") as f:
        f.write("ok\n")
if os.environ.get("FAKE_SEEN") and os.path.exists("CLAUDE.md"):  # outside the worktree, which bench removes
    with open(os.environ["FAKE_SEEN"], "w") as f:
        f.write(open("CLAUDE.md").read())

usage = {"input_tokens": int(2000 * scale), "cache_creation_input_tokens": int(10000 * scale),
         "cache_read_input_tokens": int(80000 * scale),
         "output_tokens": int(3000 * scale * float(os.environ.get("FAKE_OUTPUT_SCALE", "1")))}
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "num_turns": 7,
                  "result": "done", "total_cost_usd": round(0.5 * scale, 4), "usage": usage}))

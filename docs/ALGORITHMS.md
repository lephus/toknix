# tokopt — Thuật toán

Tài liệu này mô tả **cái gì** và **vì sao**. Nó đủ chi tiết để implement mà không phải đoán. Mỗi mục gồm: mục tiêu, input/output, pseudocode, edge case, và cách kiểm chứng.

Quy ước:

- `FACT`: đã kiểm chứng (tài liệu chính thức hoặc fixture thật trong `tests/fixtures/`).
- `[ASSUMPTION]`: giả định hợp lý, cần xác nhận khi implement.
- `[TBD]`: chưa quyết định.

Ràng buộc chung:

- Python ≥ 3.9, **chỉ stdlib**.
- Không gọi mạng, không gửi dữ liệu ra ngoài.
- Một hook lỗi **không bao giờ** được chặn agent. Mọi exception trong hook đều thành "không có ý kiến": exit 0, không in gì.

---

## A1. Ước lượng token và đơn vị ETU

**Mục tiêu:** có một đơn vị so sánh chi phí giữa các loại token.

```
est_tokens(text) = ceil(len(text) / 4)            # chỉ dùng cho output tool, file; sai số ±25%
ETU(u) = w_in·input + w_cw·cache_write + w_cr·cache_read + w_out·output
weights mặc định = [1, 1.25, 0.1, 5]              # FACT: tỉ lệ giá niêm yết Anthropic
```

- Token của model **luôn lấy từ `usage` trong transcript** (số chính xác của provider), không ước lượng.
- Trọng số đọc từ `config.weights`. Codex và Gemini có tỉ lệ giá khác nên người dùng tự đặt.

---

## A2. Bộ lọc output lệnh (`tokopt run -- "<cmd>"`)

**Mục tiêu:** agent nhận đủ thông tin để hành động (lỗi, tóm tắt, exit code), bỏ phần chỉ để lướt (progress, PASSED, download). Output gốc được giữ lại để lấy lại khi cần.

### A2.1 Pipeline

```
run(cmd):
  env = os.environ + QUIET_ENV          # NO_COLOR=1, FORCE_COLOR=0, TERM=dumb, PAGER=cat, GIT_PAGER=cat,
                                         # PIP_PROGRESS_BAR=off, npm_config_progress=false
  p = subprocess.run([bash, "-c", cmd], stdout=PIPE, stderr=STDOUT, env=env)   # gộp stderr để giữ thứ tự
  text = decode(p.stdout, utf-8, errors=replace)
  if TOKOPT_RAW=1: print(text); return p.returncode

  lines = normalize(text)               # A2.2
  if len(lines) ≤ pass_through_lines(40) and len(text) ≤ pass_through_chars(4000):
      print(text as lines); record(kind="passthrough"); return p.returncode     # không đụng output nhỏ

  kind = classify(cmd)                  # A2.3
  out = FILTERS[kind](lines, failed = p.returncode != 0)                         # A2.4
  out = squeeze_blank(collapse_similar(dedupe_runs(out)))                        # A2.5
  if kind != "gitdiff":
      out = budget_cut(out, max_lines × (2 if failed else 1), head_lines)        # A2.6

  if len(join(out)) < 0.9 × len(text):  # chỉ lưu tee khi thật sự cắt
      id = random hex(6); save text → ~/.tokopt/tee/<id>.log
      append footer: "[tokopt: N → M lines; full output: tokopt recall <id>; exit X]"
  print(out); record_run(...)           # A6
  return p.returncode                   # BẮT BUỘC giữ nguyên exit code
```

- **Shell:** luôn chạy bằng `bash` (lấy qua `shutil.which("bash")`), fallback `/bin/sh`. Agent viết cú pháp bash, nên không dùng `$SHELL` vì người dùng có thể đang dùng fish hoặc zsh.
- **Windows:** dùng bash của Git Bash, vì Claude Code trên Windows cũng chạy qua Git Bash. `[ASSUMPTION]`

### A2.2 normalize

```
strip ANSI (CSI, OSC, ESC= ESC>)
"\r\n" → "\n"
mỗi dòng chứa "\r": giữ frame cuối khác rỗng   # progress bar tự ghi đè lên dòng
rstrip mỗi dòng
```

### A2.3 classify(cmd) → kind

1. Lấy câu lệnh đầu tiên, cắt ở `&& || ; |`, rồi tách bằng `shlex.split` (fallback `split()`).
2. Bỏ các tiền tố:
   - `VAR=value`,
   - wrapper `sudo npx pnpx bunx time nice env command exec`,
   - `uv/poetry/pipenv/pdm/hatch run`,
   - `python -m`.
3. Từ còn lại đầu tiên là `prog`. Lấy basename, trừ trường hợp bắt đầu bằng `./`.
4. Tra bảng:

| prog / sub | kind |
|---|---|
| pytest, py.test | pytest |
| jest, vitest, mocha; npm/pnpm/yarn/bun `test`, `run *test*` | jest |
| npm/pnpm/yarn/bun install/i/ci/add/update/remove; pip install; uv pip/add/sync/lock; poetry/composer/bundle install/update/add/lock | install |
| go test | gotest |
| cargo test/nextest | cargotest |
| cargo build/check/clippy | compile |
| tsc, eslint, ruff, mypy, flake8, pylint | lint |
| git status / log / diff, show / push, pull, fetch, clone | gitstatus / gitlog / gitdiff / gitnet |
| ls, find, tree, du | listing |
| docker, mvn, gradle, ./gradlew, make, dotnet, flutter, terraform | build |
| khác | generic |

### A2.4 Bộ lọc theo loại

Bất biến của mọi bộ lọc:

- Không bỏ dòng khớp `ERROR_RE`: error, failed, fail, fatal, exception, traceback, panic, assert, cannot, could not, not found, undefined, denied, refused, segfault, warning, warn, `^E\s`, `^\s*at .+:\d+`.
- Luôn giữ dòng tổng kết cuối.

| kind | Bỏ | Giữ / biến đổi |
|---|---|---|
| pytest | header (platform, cachedir, rootdir, plugins, configfile, collecting), dòng `PASSED/SKIPPED/XFAIL`, dòng progress chỉ có dấu chấm, banner session starts | Từ banner `FAILURES / ERRORS / short test summary / warnings summary` đến hết: giữ nguyên văn |
| jest | dòng bắt đầu bằng `✓ √ PASS ○ ↓` | `FAIL`, khối `●`, `Tests:`, `Test Suites:` |
| gotest | `=== RUN/PAUSE/CONT`, `--- PASS:`, `PASS`, `ok  ` | khối FAIL, panic |
| cargotest | `test x ... ok`, `Compiling/Downloaded/Fresh/Checking` | failures, `test result:` |
| install | Collecting, Downloading, Using cached, Requirement already satisfied, Building wheel, `━`, Resolved, Prepared, Fetching, Progress, Downloaded, Compiling | Gộp mọi dòng `deprecated` thành 1 dòng `[tokopt] N deprecation warnings: a, b, …` (tối đa 8 tên); giữ added/audited/vulnerabilities/error |
| lint, compile | — | Nhóm theo file (regex `^path.ext[(:]`), tối đa 15 diagnostic/file, thêm dòng `[tokopt] N diagnostics in F files` |
| gitstatus | dòng gợi ý `(use "git …")` | Mọi dòng còn lại |
| gitlog | — | Nếu là format mặc định (`commit <sha>`): chuyển thành `sha9 date author: subject`, tối đa 30 commit, kèm dòng báo số commit bị bỏ. Format khác: không đổi |
| gitdiff | — | Giữ **trọn khối file** (`diff --git …`) cho tới khi đủ ngân sách 400 dòng; các file còn lại liệt kê `path (+a -d)` kèm gợi ý `git diff -- <file>`. Không bao giờ cắt giữa hunk |
| gitnet | Enumerating, Counting, Compressing, Receiving, Resolving, Writing objects | Mọi dòng còn lại |
| listing | — | 80 mục đầu, kèm dòng báo số mục còn lại |
| build | layer docker (sha256, extracting, Pulling fs layer, Waiting, Download complete), `> Task … UP-TO-DATE`, dòng chỉ có % | Mọi dòng còn lại |
| generic | — | Không lọc riêng, chỉ qua A2.5 và A2.6 |

### A2.5 Nén chung

- `dedupe_runs`: các dòng giống hệt và liên tiếp → 1 dòng `… [×N]`.
- `collapse_similar(min_run=4)`: `shape(l)` thay số và hex ≥ 7 ký tự bằng `#`. Một chuỗi từ 4 dòng cùng shape trở lên và không khớp `ERROR_RE` → giữ dòng đầu, dòng `… K similar lines …`, dòng cuối.
- `squeeze_blank`: gộp nhiều dòng trống liên tiếp, bỏ dòng trống ở cuối.

### A2.6 budget_cut(lines, max, head)

```
if len ≤ max: return lines
tail = max(max − head − 20, 20)
keep = {0..head−1} ∪ {len−tail..len−1}
room = max − |keep|
duyệt i tăng dần: nếu i ∉ keep và lines[i] khớp ERROR_RE và room > 0 → thêm i, room−−
xuất theo thứ tự tăng; chỗ nhảy cóc chèn "  … k lines omitted …"
```

Lý do: lỗi thường nằm ở cuối (stack trace, summary), còn ngữ cảnh lệnh nằm ở đầu. Dòng lỗi ở giữa được ưu tiên giữ.

### A2.7 recall

```
tokopt recall <id> [--grep REGEX] [--lines a:b]
```

In output gốc, có thể lọc theo regex (kèm số dòng) hoặc theo đoạn dòng. File tee tự xoá sau `tee_keep_days` (7 ngày); việc dọn chạy ngẫu nhiên khoảng 1/50 lần `run`.

### Kiểm chứng A2 (dùng fixture thật trong `tests/fixtures/`)

| Fixture | Điều kiện đạt |
|---|---|
| `pytest_fail.txt` | Output giữ đủ 2 tên test fail, dòng `assert … == 7`, dòng `2 failed, 40 passed`; không còn dòng `PASSED`; số dòng giảm ≥ 50% |
| `pytest_pass.txt` | Đi qua nguyên văn (passthrough) |
| `pip_install.txt` | Không còn dòng `Collecting` / `Downloading`; còn dòng `Successfully installed` |
| `npm_install.txt` | 6 dòng deprecated gộp thành 1 dòng; còn `added 172 packages` và `7 vulnerabilities` |
| `git_log.txt` | ≤ 31 dòng; dòng đầu có dạng `<9 hex> … : <subject>` |
| `git_status.txt` | Không còn dòng `(use "git` |
| Mọi fixture | exit code giữ nguyên; mọi dòng khớp `ERROR_RE` trong input vẫn có mặt trong output (property test) |

---

## A3. Hook Bash cho Claude Code (`tokopt hook bash`, PreToolUse matcher `Bash`)

**Mục tiêu:** tự động bọc các lệnh ồn bằng `tokopt run --` mà agent không cần biết.

```
input (stdin JSON): {session_id, cwd, tool_name:"Bash", tool_input:{command, run_in_background?, ...}}
should_wrap(cmd):
  false nếu: rỗng | đã chứa "tokopt run" | bắt đầu "TOKOPT_RAW=" | run_in_background | kết thúc bằng "&"
             | chứa heredoc "<<"
             | chứa lệnh có trạng thái ở đầu một câu lệnh: cd pushd popd export unset source . alias set ulimit exec
               (Claude Code giữ cwd/env giữa các lần gọi; bọc vào subshell sẽ làm mất trạng thái đó)
  true nếu program_of(cmd) ∈ config.wrap_programs
output nếu wrap:
  {"hookSpecificOutput":{"hookEventName":"PreToolUse",
     "updatedInput": {...tool_input, "command": "tokopt run -- " + shlex.quote(cmd)}}}
```

- **Quyết định bảo mật:** mặc định KHÔNG gửi `permissionDecision`, để luật permission của người dùng vẫn áp dụng. Nếu gửi `"allow"` thì mọi lệnh được bọc (kể cả `git push --force`) sẽ bỏ qua bước hỏi quyền. Config `hook_permission` cho phép bật, kèm cảnh báo trong README.
- `[ASSUMPTION]` Claude Code áp dụng `updatedInput` khi không có `permissionDecision`. Kiểm chứng ở task P2-T4: chạy một session thật, kiểm tra bảng `runs` có bản ghi. Nếu không áp dụng, phải chọn lại giữa hai phương án: (a) dùng `"ask"`, (b) chỉ cài rules để agent tự gõ `tokopt run --`. Đây là `[TBD]`.
- `[ASSUMPTION]` Permission rule dạng `Bash(npm test:*)` có thể không khớp sau khi lệnh bị viết lại. Ghi vào README phần giới hạn.

---

## A4. Read guard (`tokopt hook read`, PreToolUse matcher `Read|Grep|Glob`)

**Mục tiêu:** chặn ba loại đọc lãng phí nhất. Mỗi lần chặn phải chỉ ra cách đọc đúng, để agent không bị kẹt.

```
Read(file_path, offset?, limit?):
  R1 junk:    path khớp junk_patterns (thư mục: chứa "/node_modules/"…; đuôi: endswith ".min.js", lockfile…)
              → deny "… is generated/vendored/lockfile (~T tokens). Read the source or manifest; slice with offset/limit if needed."
              Ngoại lệ: có offset/limit → vẫn deny [TBD: có nên cho phép đọc lát cắt lockfile không]
  R2 bigfile: không có offset/limit và size > big_lines×40 bytes và đếm dòng > big_lines (800)
              → deny "… has N lines (~T tokens). Run `tokopt outline <path>`, then Read the range with offset/limit."
  R3 reread:  key = (session, realpath, f"{offset}:{limit}"); nếu mtime không đổi thì n++, mtime đổi thì n=1
              n > guard_repeat_reads (2) → deny "already read this exact range N times; file unchanged; content is still in context."
Grep/Glob(path):
  path nằm trong junk → deny kèm gợi ý tìm trong thư mục source
mọi lần deny: ghi bảng guard(session, rule, path, est_tokens = size/4)
```

- **Reset ledger:** hook `SessionStart` với `source ∈ {compact, clear}` xoá các dòng `reads` của session đó. Sau compaction, nội dung file cũ không còn trong context nên phải cho phép đọc lại.
- **Vì sao R3 cho phép 2 lần:** lần thứ 2 có thể hợp lệ (đọc lại sau khi tự sửa file nhưng mtime chưa đổi theo giây, hoặc sau một compaction mà hook không bắt được).
- `[ASSUMPTION]` Bản Claude Code mới có thể đã tự trả stub "file unchanged" cho lần Read lặp. Nếu đúng vậy thì R3 thừa. Kiểm chứng ở P2-T4; nếu thừa thì mặc định đặt `guard_repeat_reads = 0`.

---

## A5. Truy xuất code tự viết: outline, symbol, refs, map

**Mục tiêu:** thay "đọc cả file" bằng "đọc đúng hàm cần", không cần LSP hay tree-sitter.

### A5.1 Trích symbol

```
extract(path) → [Symbol(name, kind, start, end, signature, parent)]
Python: ast.parse → FunctionDef/AsyncFunctionDef/ClassDef (kể cả lồng),
        start = lineno (tính cả decorator), end = end_lineno, signature = dòng def gốc (đã cắt "…:")
Ngôn ngữ dùng ngoặc nhọn (js, ts, jsx, tsx, go, java, kt, cs, php, rust, dart, swift, c, cpp):
        regex khởi đầu theo ngôn ngữ (bảng dưới) → start
        end = brace_match(lines, start): đếm { } bỏ qua chuỗi '…' "…" `…` và comment // /* */;
              nếu không gặp { trong 3 dòng (khai báo một dòng, ví dụ type alias) → end = start
Ruby: def/class/module … end (đếm từ khoá mở/đóng theo dòng)
Ngôn ngữ khác: không hỗ trợ → outline trả 200 dòng đầu kèm thông báo
```

| Ngôn ngữ | Regex khởi đầu (rút gọn) |
|---|---|
| JS/TS | `^(export\s+)?(default\s+)?(async\s+)?function\*?\s+(\w+)`, `^(export\s+)?(abstract\s+)?class\s+(\w+)`, `^(export\s+)?(const\|let)\s+(\w+)\s*=\s*(async\s*)?(\(.*\)\|\w+)\s*=>`, `^(export\s+)?(interface\|type\|enum)\s+(\w+)`, method trong class: `^\s+(public\|private\|protected\|static\|async\|get\|set\|\s)*(\w+)\s*\(.*\)\s*(:\s*[^{]+)?\{` (loại if/for/while/switch/catch) |
| Go | `^func\s+(\(\w+\s+\*?\w+\)\s+)?(\w+)`, `^type\s+(\w+)\s+(struct\|interface)` |
| Java/Kotlin/C#/Dart | `(class\|interface\|enum\|record\|object)\s+(\w+)`, method: `^\s+(modifiers)*[\w<>\[\],? ]+\s+(\w+)\s*\(` kết thúc bằng `{` hoặc `=>` |
| Rust | `^\s*(pub(\(\w+\))?\s+)?(async\s+)?fn\s+(\w+)`, `(struct\|enum\|trait\|impl)\s+…` |
| PHP | `function\s+(\w+)`, `class\s+(\w+)` |

### A5.2 Lệnh

- `tokopt outline <file>`: in mỗi symbol một dòng, thụt lề theo parent: `L12-40  def foo(a, b)`. Dòng cuối: `[N lines, ~T tokens; Read with offset=12 limit=29]`.
- `tokopt symbol <name> [--path DIR] [--kind K]`:
  1. Liệt kê file nguồn bằng `git ls-files` nếu là git repo, nếu không thì duyệt thư mục (bỏ junk_patterns, file > 1 MB, file nhị phân).
  2. Tiền lọc nhanh: chỉ giữ file chứa chuỗi `name`.
  3. Chạy `extract` trên các file đó, khớp đúng tên (hoặc `Class.method`).
  4. In code của symbol kèm số dòng. Tối đa 3 kết quả và 300 dòng mỗi kết quả; các kết quả khác chỉ liệt kê vị trí.
- `tokopt refs <name>`: regex `\bname\b` trên các file nguồn, nhóm theo file, tối đa 50 dòng. Bỏ dòng định nghĩa.
- `tokopt map [DIR] [--budget 1500]` (repo map kiểu aider, bản rút gọn):
  ```
  defs = extract mọi file
  ref_count[name] = số file khác (không phải file định nghĩa) chứa \bname\b   # tiền lọc bằng `in`
  score(file) = Σ log(1 + ref_count[s]) với s là top-level symbol của file
  xếp file theo score giảm dần; với mỗi file in "path" + các symbol top-level (signature 1 dòng)
  dừng khi est_tokens(output) ≥ budget
  ```
  Repo > 5.000 file: chỉ quét các file đã thay đổi trong 90 ngày (`git log --since`) cộng các thư mục người dùng chỉ định. `[ASSUMPTION]`

### Kiểm chứng A5

- `tests/fixtures/code/` có một file mẫu cho mỗi ngôn ngữ, kèm file `expected_outline.json` viết tay.
- Đạt khi:
  - Python khớp 100%.
  - Ngôn ngữ dùng regex khớp ≥ 90% symbol và sai `end` tối đa 1 dòng.
  - `tokopt symbol` trên chính repo tokopt tìm đúng `budget_cut` và in đúng thân hàm.

---

## A6. Đo lường

### A6.1 Đọc transcript

- **Claude Code** `~/.claude/projects/*/*.jsonl`:
  - Dòng `type=assistant` có `message.usage`.
  - Khử trùng lặp theo `(message.id, requestId)`, vì các chunk stream lặp lại cùng một usage. `FACT` (ccusage làm như vậy)
  - `tool_use` (id, name, input) nối với `tool_result` (tool_use_id) ở dòng `type=user` để biết tool nào sinh ra bao nhiêu token output.
- **Codex** `~/.codex/sessions/**/rollout-*.jsonl`:
  - `payload.type=token_count` lấy `info.total_token_usage` cuối cùng; `input` thực = `input_tokens − cached_input_tokens`.
  - `function_call` nối với `function_call_output` theo `call_id`.
  - `[ASSUMPTION]` Schema có thể đổi theo phiên bản, nên parser phải phòng thủ: field thiếu thì bỏ qua, không crash.
- **Gemini:** ngoài phạm vi v0.1. Hướng dẫn dùng `npx ccusage gemini`.

### A6.2 Các báo cáo

| Lệnh | Tính gì |
|---|---|
| `report --days N` | sessions, tổng các loại token, ETU/session, ETU/lượt gọi, cache hit = cache_read/(input+cache_write+cache_read), token tool output ước lượng, top 10 session theo ETU |
| `waste --days N` | (1) top 10 họ tool theo token output (`Bash:<prog>`, `Read:<path>`); (2) các output đơn lẻ ≥ 5k token; (3) lần đọc hoặc lệnh chạm junk; (4) file bị Read ≥ 3 lần trong một session; (5) session > 80 lượt gọi. Mỗi nhóm kèm gợi ý sửa |
| `gain` | Từ SQLite: Σ(raw_chars − out_chars)/4 theo kind, Σ est_tokens từ guard theo rule, top 10 lệnh tiết kiệm nhiều nhất, tỉ lệ lệnh bị bọc trên tổng lệnh Bash (lấy từ transcript) |
| `context` | Token ước lượng của các instruction file sẽ được nạp (global/project CLAUDE.md, CLAUDE.local.md, .claude/rules/*.md, AGENTS.md, GEMINI.md) + số MCP server (~/.claude.json, .mcp.json); cảnh báo khi > 1.500 |
| `snapshot --label L` | Lưu bản tóm tắt report vào `~/.tokopt/results/snapshot-L.json` |

### A6.3 Bench A/B (bằng chứng nhân quả)

```
tasks.json: {repo, ref, agent, repeat, timeout, setup[], tasks:[{id, prompt, check, agent_args?}]}
for task, i in tasks × repeat:
  wt = git worktree add --detach <tmp> <ref>; chạy setup
  claude: claude -p PROMPT --output-format json [--permission-mode acceptEdits --allowedTools …]
          → usage, total_cost_usd, num_turns                       # FACT: định dạng JSON của -p
  codex:  codex exec --json --full-auto PROMPT → cộng dồn usage từ event turn.completed   [ASSUMPTION]
  gemini: gemini -p PROMPT --output-format json → stats.models.*.tokens                  [ASSUMPTION]
  passed = (check exit 0); ghi 1 dòng JSONL; xoá worktree (finally)
compare A B:
  chỉ so các task chung
  median ETU, pass rate, cost trung bình, wall time trung vị, bảng theo từng task
  CI 95% bootstrap (2000 lần lấy mẫu, seed 42) cho (median_B − median_A)/median_A
  verdict:
    ACCEPT       nếu pass_B ≥ pass_A − max_drop(0) và mức giảm ≥ min_saving(10%) và cận trên CI < 0
    REJECT       nếu pass rate giảm, hoặc không giảm
    INCONCLUSIVE nếu các trường hợp còn lại
```

---

## A7. Statusline (`tokopt statusline`)

```
stdin: JSON Claude Code gửi cho statusLine (có transcript_path, model.display_name)
ctx = usage của lượt assistant cuối: input + cache_read + cache_write
pct = ctx / context_window (mặc định 200k, đổi trong config)
session_etu = ETU cộng dồn cả transcript (đọc tăng dần, cache offset trong ~/.tokopt/sl-<session>.json)
saved = gain của session này (bảng runs và guard, lọc theo ts ≥ thời điểm bắt đầu session)
in:  "ctx 46% ▮▮▮▮▯▯▯▯▯▯ · 1.2M ETU · saved ~84k · /clear at 60%"   (màu vàng ≥ 60%, đỏ ≥ 80%)
yêu cầu: chạy < 150 ms với transcript 20 MB  →  bắt buộc đọc tăng dần từ offset đã lưu
```

---

## A8. Cài đặt và gỡ (`tokopt install` / `tokopt uninstall`)

- **Cài đặt** idempotent:
  1. Backup sang `~/.tokopt/backups/<ts>/`.
  2. **Claude Code** `~/.claude/settings.json`: thêm hook với `command = "<đường dẫn tuyệt đối tới tokopt> hook …"`, bỏ qua nếu đã có entry chứa `tokopt hook`.
     - PreToolUse `Bash` → `hook bash`
     - PreToolUse `Read|Grep|Glob` → `hook read`
     - SessionStart `compact|clear` → `hook session-start`
     - `statusLine` → `tokopt statusline`, chỉ khi người dùng chưa có statusLine.
  3. Rules block giữa marker `<!-- tokopt:begin vN -->`…`<!-- tokopt:end -->` trong CLAUDE.md, AGENTS.md, GEMINI.md. Thay thế block cũ, giữ nguyên nội dung khác.
  4. **Codex, Gemini:** v0.1 chỉ cài rules block, hướng dẫn agent tự dùng `tokopt run --` và `tokopt outline`. Hook native cho hai agent này là `[TBD]`.
- **Gỡ:** xoá đúng các entry chứa `tokopt hook` hoặc `tokopt statusline`, xoá rules block; không đụng vào gì khác.
- `tokopt init-project`: thêm `permissions.deny` cho các thư mục junk vào `.claude/settings.json` của project, và tạo `.geminiignore`.
- **Script bootstrap một lệnh:**
  - `install.sh`: cần python3 ≥ 3.9. Ưu tiên `uv tool install`; nếu không có thì tạo `python3 -m venv ~/.tokopt/venv`, `pip install`, rồi symlink `~/.local/bin/tokopt`. Cuối cùng chạy `tokopt install`.
  - `install.ps1`: tương tự, dùng `venv\Scripts\tokopt.exe`.

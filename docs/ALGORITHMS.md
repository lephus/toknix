# tokopt — Thuật toán

Tài liệu này mô tả **cái gì** và **vì sao**. Nó đủ chi tiết để implement mà không phải đoán. Mỗi mục gồm: mục tiêu, input/output, pseudocode, edge case, và cách kiểm chứng.

Quy ước:

- `FACT`: đã kiểm chứng (tài liệu chính thức hoặc fixture thật trong `tests/fixtures/`).
- `[ASSUMPTION]`: giả định hợp lý, cần xác nhận khi implement.
- `[TBD]`: chưa quyết định.
- Từ thiết kế 2026-10-18 (`docs/superpowers/specs/2026-10-18-token-optimizations-design.md`): `[ASSUMPTION]` gắn cho **một con số** là giá trị mặc định ban đầu. Người làm đặt đúng giá trị đó vào config và được chỉnh nếu fixture cho thấy giá trị khác tốt hơn (ghi lý do trong commit). Quy tắc "dừng lại và hỏi" chỉ áp dụng cho `[TBD]` và giả định về hành vi của Claude Code.

Ràng buộc chung:

- Python ≥ 3.9, **chỉ stdlib**.
- Không gọi mạng, không gửi dữ liệu ra ngoài.
- Một hook lỗi **không bao giờ** được chặn agent. Mọi exception trong hook đều thành "không có ý kiến": exit 0, không in gì.

---

## A1. Ước lượng token và đơn vị ETU

**Mục tiêu:** có một đơn vị so sánh chi phí giữa các loại token.

```
est_tokens(text) = Σ_lớp ceil(chars_lớp / token_ratios[lớp])     # chỉ dùng cho output tool, file
  lớp hex:       chuỗi [0-9a-fA-F] hoặc base64 liền nhau ≥ 16 ký tự
  lớp non_ascii: ký tự ngoài ASCII (tiếng Việt có dấu, ━ ✓ …)
  lớp default:   phần còn lại
  token_ratios mặc định = {default: 4.0, hex: 2.0, non_ascii: 1.5}   [ASSUMPTION]
ETU(u) = w_in·input + w_cw·cache_write + w_cr·cache_read + w_out·output
weights mặc định = [1, 1.25, 0.1, 5]              # FACT: tỉ lệ giá niêm yết Anthropic
reread_cost(T, R) = T·w_cw + T·w_cr·R              # T token vào context khi phiên còn R request phía sau
                                                   # ví dụ T=5.000, R=50 → ≈ 31.000 ETU
```

- Chuỗi hex: `[0-9a-fA-F]{16,}`. Chuỗi base64: `[A-Za-z0-9+/]{16,}={0,2}` và phải có đủ chữ hoa, chữ thường, chữ số, để từ thường dài (`internationalization`) hay đường dẫn (`usr/lib/python3`) không bị tính vào lớp hex.
- `est_tokens` chạy một lượt regex. Hook read vẫn dùng `size // 4` để giữ tốc độ.
- Bộ lọc (`tokopt run`) **luôn** dùng `token_ratios` trong config. Kết quả hiệu chỉnh (A6.4) chỉ dùng cho báo cáo (`gain`, `report`, `waste`, `statusline`), để lượng output agent nhận được không tự thay đổi.

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
  out = normalize_costly(out, kind)                                              # A2.5b
  out = squeeze_blank(collapse_similar(dedupe_runs(out)))                        # A2.5
  if kind != "gitdiff":
      out = cut_long_lines(out, max_line_chars)                                  # A2.5c
      k = 2 if failed else 1
      out = budget_cut(out, max_lines × k, max_tokens × k, head_lines)           # A2.6

  lossy = normalize_costly hoặc cut_long_lines đã đổi ≥ 1 dòng
  if lossy or len(join(out)) < 0.9 × len(text):   # lưu tee khi thật sự cắt hoặc có biến đổi mất thông tin
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
   - Với `git`: bỏ global option đứng trước subcommand: `-C <dir>`, `-c <k=v>`, `--no-pager`, `--git-dir=…`, `--work-tree=…` (cả dạng `--git-dir <dir>`). Ví dụ `git -C sub log` → `gitlog`.
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
- Bất biến ERROR_RE là **tuyệt đối** (người dùng chốt 2026-10-18): dòng khớp luôn được giữ **nguyên văn**, kể cả khi vượt ngân sách; không bị cắt dòng dài, không bị chuẩn hoá. Hai ngoại lệ không làm mất nội dung: `dedupe_runs` gộp các dòng giống hệt liên tiếp thành `… [×N]`; bộ lọc install gộp các dòng `deprecated`.

| kind | Bỏ | Giữ / biến đổi |
|---|---|---|
| pytest | header (platform, cachedir, rootdir, plugins, configfile, collecting), dòng `PASSED/SKIPPED/XFAIL`, dòng progress chỉ có dấu chấm, banner session starts | Từ banner `FAILURES / ERRORS / short test summary / warnings summary` đến hết: giữ nguyên văn |
| jest | dòng bắt đầu bằng `✓ √ PASS ○ ↓` | `FAIL`, khối `●`, `Tests:`, `Test Suites:` |
| gotest | `=== RUN/PAUSE/CONT`, `--- PASS:`, `PASS`, `ok  ` | khối FAIL, panic |
| cargotest | `test x ... ok`, `Compiling/Downloaded/Fresh/Checking` | failures, `test result:` |
| install | Collecting, Downloading, Using cached, Requirement already satisfied, Building wheel, `━`, Resolved, Prepared, Fetching, Progress, Downloaded, Compiling | Gộp mọi dòng `deprecated` thành 1 dòng `[tokopt] N deprecation warnings: a, b, …` (tối đa 8 tên); giữ added/audited/vulnerabilities/error |
| lint, compile | — | Nhóm theo file (regex `^path.ext[(:]`), tối đa 15 dòng **không** khớp ERROR_RE mỗi file (diagnostic khớp ERROR_RE luôn giữ hết), thêm dòng `[tokopt] N diagnostics in F files` |
| gitstatus | dòng gợi ý `(use "git …")` | Mọi dòng còn lại |
| gitlog | — | Nếu là format mặc định (`commit <sha>`): chuyển thành `sha9 date author: subject`, tối đa 30 commit, kèm dòng báo số commit bị bỏ. Format khác: không đổi |
| gitdiff | — | Giữ **trọn khối file** (`diff --git …`) cho tới khi chạm ngân sách 400 dòng hoặc `max_tokens` (trần nào trước); khối file mà riêng nó đã vượt ngân sách cũng chỉ được liệt kê; các file còn lại liệt kê `path (+a -d)` kèm gợi ý `git diff -- <file>`. Không bao giờ cắt giữa hunk |
| gitnet | Enumerating, Counting, Compressing, Receiving, Resolving, Writing objects | Mọi dòng còn lại |
| listing | — | 80 mục đầu, kèm dòng báo số mục còn lại |
| build | layer docker (sha256, extracting; dòng trạng thái layer dạng `<12 hex>: Pulling fs layer / Waiting / Verifying Checksum / Download complete / Pull complete`), `> Task … UP-TO-DATE`, dòng chỉ có % | Mọi dòng còn lại (kể cả log ứng dụng có chữ `Waiting`) |
| generic | — | Không lọc riêng, chỉ qua A2.5 và A2.6 |

### A2.5 Nén chung

- `dedupe_runs`: các dòng giống hệt và liên tiếp → 1 dòng `… [×N]`.
- `collapse_similar(min_run=4)`: `shape(l)` thay số và hex ≥ 7 ký tự bằng `#`. Một chuỗi từ 4 dòng cùng shape trở lên và không khớp `ERROR_RE` → giữ dòng đầu, dòng `… K similar lines …`, dòng cuối.
- `squeeze_blank`: gộp nhiều dòng trống liên tiếp, bỏ dòng trống ở cuối.

### A2.5b normalize_costly(lines, kind)

Cùng nội dung, chọn cách viết tốn ít token hơn (ý tưởng MCE của bài CNF-MCE). Chỉ áp cho kind `install`, `build`, `gitnet`. Không bao giờ đụng dòng khớp `ERROR_RE`. Không áp cho kind có thể in code nguyên văn hoặc tên file cần giữ nguyên (pytest, jest, gotest, cargotest, lint, compile, gitdiff, gitstatus, gitlog, listing, generic). Mỗi biến đổi có cờ config riêng.

| Biến đổi | Cờ config | Mặc định |
|---|---|---|
| Hex ≥ 32 ký tự → 12 ký tự đầu + `…`; `sha512-<base64>` / `sha256-<base64>` → `sha512-…` / `sha256-…` | `normalize_hashes` | bật |
| ≥ 4 dòng liên tiếp bắt đầu bằng timestamp (ISO 8601 hoặc `HH:MM:SS`, có thể trong `[…]`) → bỏ timestamp ở các dòng giữa, giữ ở dòng đầu và dòng cuối. Dòng khớp `ERROR_RE` vẫn tính vào chuỗi nhưng giữ nguyên timestamp | `normalize_timestamps` | bật |
| Đường dẫn tuyệt đối nằm dưới cwd → tương đối | `normalize_paths` | **tắt** (tool Read của Claude Code cần đường dẫn tuyệt đối) |

### A2.5c cut_long_lines(lines, max_line_chars)

Dòng dài hơn `max_line_chars` (800 `[ASSUMPTION]`) và **không** khớp `ERROR_RE` → `<300 ký tự đầu> …[N chars]… <200 ký tự cuối>`, N là số ký tự bị bỏ. Không áp cho `gitdiff`. Nếu người dùng đặt `max_line_chars` < 520 thì vẫn dùng 520, để việc cắt luôn làm dòng ngắn đi.

### A2.6 budget_cut(lines, max_lines, max_tokens, head)

```
err  = {i : lines[i] khớp ERROR_RE}          # luôn giữ, không tính vào ngân sách (bất biến tuyệt đối)
rest = các chỉ số còn lại
if |rest| ≤ max_lines và Σ est_tokens(rest) ≤ max_tokens: return lines
tail = max(max_lines − head − 20, 20)
chọn từ rest: head phần tử đầu và tail phần tử cuối
bớt dần tail rồi head (mỗi bên tối thiểu 5) cho tới khi |chọn| ≤ max_lines và Σ est_tokens(chọn) + est_tokens(dòng "… k lines omitted …" giữa head và tail) ≤ max_tokens × 1.05
keep = err ∪ chọn
xuất theo thứ tự tăng; chỗ nhảy cóc chèn "  … k lines omitted …"
error_overflow = 1 nếu Σ est_tokens(err) > max_tokens (ghi vào bảng runs)
```

- `max_tokens` mặc định 3.000 `[ASSUMPTION]`; cả `max_lines` và `max_tokens` đều nhân đôi khi lệnh fail.

Lý do: lỗi thường nằm ở cuối (stack trace, summary), còn ngữ cảnh lệnh nằm ở đầu. Dòng lỗi ở giữa được ưu tiên giữ.

### A2.7 recall

```
tokopt recall <id> [--grep REGEX] [--lines a:b]
```

In output gốc, có thể lọc theo regex (kèm số dòng) hoặc theo đoạn dòng. File tee tự xoá sau `tee_keep_days` (7 ngày); việc dọn chạy ngẫu nhiên khoảng 1/50 lần `run`.

Mỗi lần chạy, `recall` ghi một dòng vào bảng `recalls(ts, tee_id)` (best-effort như `record_run`). Đây là dữ liệu cho tỉ lệ recall trong `gain` (A6.2).

### Kiểm chứng A2 (dùng fixture thật trong `tests/fixtures/`)

| Fixture | Điều kiện đạt |
|---|---|
| `pytest_fail.txt` | Output giữ đủ 2 tên test fail, dòng `assert … == 7`, dòng `2 failed, 40 passed`; không còn dòng `PASSED`; số dòng giảm ≥ 50% |
| `pytest_pass.txt` | Đi qua nguyên văn (passthrough) |
| `pip_install.txt` | Không còn dòng `Collecting` / `Downloading`; còn dòng `Successfully installed` |
| `npm_install.txt` | 6 dòng deprecated gộp thành 1 dòng; còn `added 172 packages` và `7 vulnerabilities` |
| `git_log.txt` | ≤ 31 dòng; dòng đầu có dạng `<9 hex> … : <subject>` |
| `git_status.txt` | Không còn dòng `(use "git` |
| Mọi fixture | exit code giữ nguyên; mọi dòng khớp `ERROR_RE` trong input vẫn có mặt **nguyên văn** trong output (property test) |
| Mọi fixture trừ gitdiff | phần không khớp ERROR_RE của output ≤ `max_tokens × 1.05` (tính bằng `est_tokens`) |
| `build_longlines.txt` (viết tay) | dòng > 800 ký tự không khớp ERROR_RE bị cắt còn ≤ 520 ký tự; dòng lỗi dài vẫn nguyên văn; có tee |
| `build_timestamps.txt` (viết tay) | timestamp chỉ còn ở dòng đầu và dòng cuối của mỗi chuỗi ≥ 4 dòng |
| Output sinh trong test có 500 dòng `FAILED …` | cả 500 dòng còn nguyên; `runs.error_overflow = 1` |

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
Grep(output_mode = "content", không có head_limit) và grep_head_limit > 0:
  → updatedInput: {...tool_input, head_limit: grep_head_limit}     # mặc định 200 [ASSUMPTION]
mọi lần deny: ghi bảng guard(session, rule, path, est_tokens = size/4)
```

- `[ASSUMPTION]` Giới hạn Grep cần kiểm chứng ở P2-T4: (1) `updatedInput` có được áp dụng khi không có `permissionDecision`; (2) khi `head_limit` cắt bớt, Claude Code có báo cho agent là kết quả đã bị cắt không. Nếu (2) không có thì đặt mặc định `grep_head_limit = 0`, vì cắt im lặng có thể làm agent kết luận sai ("chỉ có 200 chỗ gọi").
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
| `report --days N` | sessions, tổng các loại token, ETU/session, ETU/lượt gọi, cache hit = cache_read/(input+cache_write+cache_read), token tool output ước lượng, **tỉ trọng output = Σ(output·w_out)/Σ ETU** (theo phiên và tổng), top 10 session theo ETU |
| `waste --days N` | (1) top 10 họ tool theo `reread_cost` (`Bash:<prog>`, `Read:<path>`), hiện cả T và ETU; (2) các output đơn lẻ ≥ 5k token, xếp theo `reread_cost`; (3) lần đọc hoặc lệnh chạm junk; (4) file bị Read ≥ 3 lần trong một session; (5) session > 80 lượt gọi; (6) cache vỡ (A6.5); (7) phiên có tỉ trọng output > 35% `[ASSUMPTION]`, kèm phân rã output (A6.6). Mỗi nhóm kèm gợi ý sửa |
| `gain` | Từ SQLite: tiết kiệm **trực tiếp** theo kind (token ước lượng của raw − out), Σ est_tokens từ guard theo rule, top 10 lệnh tiết kiệm nhiều nhất, tỉ lệ lệnh bị bọc trên tổng lệnh Bash (lấy từ transcript). Thêm: (a) tiết kiệm **tính cả đọc lại (ước tính)** = trực tiếp × (w_cw + w_cr·R̄), R̄ = trung vị số request còn lại sau mỗi tool_result Bash trong transcript N ngày; chưa có transcript thì R̄ = 20 `[ASSUMPTION]` và ghi rõ "dùng giá trị mặc định"; (b) theo kind: số lần cắt, số tee_id bị recall (đếm khác nhau), tỉ lệ recall; tỉ lệ > 20% với ≥ 10 lần cắt `[ASSUMPTION]` → cảnh báo "bộ lọc <kind> có thể cắt quá tay"; (c) số lần chạy có `error_overflow = 1` |
| `context` | Token ước lượng của các instruction file sẽ được nạp (global/project CLAUDE.md, CLAUDE.local.md, .claude/rules/*.md, AGENTS.md, GEMINI.md) + số MCP server (~/.claude.json, .mcp.json); cảnh báo khi > 1.500. Thêm: MCP server đã cấu hình (`~/.claude.json`, `.mcp.json`, settings của project) và skill (người dùng, project, plugin), kèm số lần được gọi trong N ngày (đếm `tool_use` tên `mcp__<server>__*` và input `skill` của tool `Skill`); 0 lần → gợi ý tắt, không tự tắt. Chỉ báo token schema MCP khi đọc được schema `[ASSUMPTION]` |
| `snapshot --label L` | Lưu bản tóm tắt report vào `~/.tokopt/results/snapshot-L.json` |

- Các phép tính theo phiên bỏ qua request của subagent.

### A6.4 Hiệu chỉnh tỉ lệ ký tự/token (chỉ Claude Code, chỉ cho báo cáo)

```
mẫu: hai request liên tiếp k, k+1 trong cùng phiên, không có compaction hay cache vỡ (A6.5) ở giữa
  context = input + cache_read + cache_write
  y = context_{k+1} − context_k − output_k
  x = (chars_default, chars_hex, chars_non_ascii) của các tool_result nằm giữa k và k+1
bỏ mẫu y ≤ 0 và mẫu ngoại lai
hồi quy bình phương tối thiểu không hệ số chặn (giải hệ phương trình chuẩn 3×3), cần ≥ 200 mẫu
ratio_lớp = 1 / hệ số_lớp
lưu ~/.tokopt/calibration.json theo agent; chỉ dùng khi sai số trung vị trên tập kiểm tra < 15% [ASSUMPTION]
```

- Rủi ro: output_k chứa thinking, mà thinking có thể không còn trong context ở request sau. Lọc mẫu và ngưỡng chất lượng ở trên xử lý việc này.
- Tên lệnh (lệnh riêng hay chạy trong `report`) do người làm chọn.

### A6.5 Cache vỡ giữa phiên (chỉ Claude Code)

```
request k là một lần vỡ nếu cache_read_k < 0.5 · (cache_read_{k−1} + cache_write_{k−1})
                         và cache_write_k ≥ 10.000                         [ASSUMPTION]
nguyên nhân (theo thứ tự ưu tiên):
  compact  có ranh giới compaction trước k       → bình thường, không tính lãng phí
  model    message.model khác request trước
  idle     khoảng cách timestamp > cache_ttl_seconds (mặc định 300; nhận biết TTL 1 giờ từ usage nếu có)
  unknown  còn lại (đổi MCP/tool, sửa instruction, cập nhật Claude Code)
ETU mất = cache_write_k · (w_cw − w_cr)
```

Gợi ý trong `waste`: idle → /clear hoặc chấp nhận khi nghỉ lâu; model → tránh đổi model giữa phiên; unknown → kiểm tra MCP/instruction vừa đổi.

### A6.6 Phân rã output

`usage.output_tokens` chỉ có tổng, nên phân rã theo tỉ lệ ký tự của các khối trong message assistant: thinking, văn bản trả lời, input của tool ghi file (Write, Edit, MultiEdit, NotebookEdit), input tool khác. Nếu thinking bị ẩn trong transcript thì phần thinking = output_tokens − ước lượng phần nhìn thấy. Gợi ý theo nhóm lớn nhất: Write → "sửa file có sẵn bằng Edit"; văn bản → rules viết ngắn; thinking → chỉ gợi ý giảm effort cho việc đơn giản (tokopt không tự đổi cấu hình).

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

**Chọn rules bằng bench:**

- `bench --rules <file>`: chèn rules block vào CLAUDE.md / AGENTS.md của **worktree** (giữa marker như A8) trước khi chạy agent. Nếu CLAUDE.md global của người dùng đã có block tokopt thì cảnh báo và ghi trạng thái đó vào kết quả (bench không ghi ra ngoài `~/.tokopt/` và worktree, nên không tự cô lập).
- `compare` thêm cột median output tokens và verdict riêng cho output.
- Nhánh A luôn là rules v1; mỗi biến thể là một nhánh B, so riêng từng cặp với v1. Biến thể thắng khi pass rate không giảm, median output tokens giảm ≥ 10% và cận trên CI 95% bootstrap của mức thay đổi output < 0. Nhiều biến thể cùng thắng thì chọn biến thể giảm output nhiều nhất.

---

## A7. Statusline (`tokopt statusline`)

```
stdin: JSON Claude Code gửi cho statusLine (có transcript_path, model.display_name)
ctx = usage của lượt assistant cuối: input + cache_read + cache_write
pct = ctx / context_window (mặc định 200k, đổi trong config)
session_etu = ETU cộng dồn cả transcript (đọc tăng dần, cache offset trong ~/.tokopt/sl-<session>.json)
saved = gain trực tiếp của session này (bảng runs và guard, lọc theo ts ≥ thời điểm bắt đầu session; không nhân hệ số đọc lại)
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

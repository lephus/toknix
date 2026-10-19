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
      id = random hex(6); save p.stdout (bytes gốc) → ~/.tokopt/tee/<id>.log
      append footer: "[tokopt: N → M lines; full output: tokopt recall <id>; exit X]"
  print(out); record_run(...)           # A6
  return p.returncode                   # BẮT BUỘC giữ nguyên exit code; bị kill bởi signal N → 128 + N như shell
```

- **Shell:** luôn chạy bằng `bash` (lấy qua `shutil.which("bash")`), fallback `/bin/sh`. Agent viết cú pháp bash, nên không dùng `$SHELL` vì người dùng có thể đang dùng fish hoặc zsh. `TOKOPT_SHELL` ghi đè tất cả.
- **Windows:** dùng bash của Git Bash, vì Claude Code trên Windows cũng chạy qua Git Bash. `[ASSUMPTION]` Thứ tự tìm: `CLAUDE_CODE_GIT_BASH_PATH` (biến Claude Code dùng), `bin\bash.exe` cạnh `git.exe` trên PATH, rồi `bash` trên PATH nếu không nằm trong `System32` hay `WindowsApps` (đó là launcher của WSL, chạy lệnh trong hệ điều hành khác).
- **Output:** ghi byte UTF-8 thẳng vào stdout, không phụ thuộc encoding của console và không đổi `\n` thành `\r\n` trên Windows. Footer chỉ in khi lưu tee thành công.
- **Không bao giờ làm mất output:** lỗi bất kỳ trong bước lọc (config hỏng, bug bộ lọc) → in nguyên văn output gốc, giữ exit code, không in gì thêm (trừ khi `TOKOPT_DEBUG=1`). `~/.tokopt` không ghi được → vẫn in output đã lọc, chỉ mất tee và thống kê.
- **Dòng lệnh:** `tokopt run -- "<cmd>"` (một chuỗi, như hook A3 viết) chạy nguyên chuỗi; nhiều từ (`tokopt run -- npm test`) được ghép lại bằng `shlex.join`. `--` là tuỳ chọn.
- **Đếm dòng:** N trong footer là số dòng của output gốc (không tính phần rỗng sau dấu xuống dòng cuối), cùng cách đánh số với `recall --lines` / `--grep`.

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
   - `python -m` (cả khi gọi bằng đường dẫn hoặc có `.exe`: `.venv/bin/python -m`, `python.exe -m`).
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
- Bất biến ERROR_RE là **tuyệt đối** (người dùng chốt 2026-10-18): dòng khớp luôn được giữ **nguyên văn**, kể cả khi vượt ngân sách; không bị cắt dòng dài, không bị chuẩn hoá. Hai ngoại lệ không làm mất nội dung: `dedupe_runs` gộp các dòng giống hệt liên tiếp thành `… [×N]`; bộ lọc install gộp các dòng `deprecated`. Ngoại lệ thứ ba (người dùng chốt 2026-10-19): khi `gitdiff` vượt ngân sách, khối file bị rút gọn thành `path (+a -d)` kể cả khi có dòng khớp `ERROR_RE`, vì đó là code trong diff chứ không phải lỗi lúc chạy; agent lấy lại bằng `git diff -- <file>` hoặc `tokopt recall`.

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

Không có tuỳ chọn: in **nguyên văn từng byte** output gốc. Có `--grep` / `--lines`: in các dòng đã qua `normalize` (bỏ ANSI, frame cuối của progress bar), để regex khớp được cả output có màu. `--lines a:b` đánh số từ 1, gồm cả hai đầu (`a:`, `:b`, `a` đều hợp lệ); dùng cả hai thì `--grep` lọc trong đoạn `--lines` và giữ số dòng gốc. Tham số sai → exit 2; id không tồn tại → exit 1. Id chỉ gồm chữ số hex, nên không đọc được file ngoài `~/.tokopt/tee/`. File tee tự xoá sau `tee_keep_days` (7 ngày); việc dọn chạy ngẫu nhiên khoảng 1/50 lần `run`.

Mỗi lần chạy với id hợp lệ (kể cả id đã hết hạn), `recall` ghi một dòng vào bảng `recalls(ts, tee_id)` (best-effort như `record_run`). Đây là dữ liệu cho tỉ lệ recall trong `gain` (A6.2).

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
               "đầu một câu lệnh" = đầu chuỗi, hoặc sau ; & | hay xuống dòng; có hoặc không có đối số (`npm test; popd`)
             | tool_name khác "Bash" | command không phải chuỗi
  true nếu program_of(cmd) ∈ config.wrap_programs
output nếu wrap:
  {"hookSpecificOutput":{"hookEventName":"PreToolUse",
     "updatedInput": {...tool_input, "command": "tokopt run -- " + shlex.quote(cmd)}}}
```

- **Input:** đọc stdin dưới dạng byte rồi giải mã UTF-8, không dùng code page của console (cp1252 trên Windows sẽ làm hỏng lệnh có tiếng Việt khi ghi lại vào `updatedInput`). Output là JSON ASCII.
- **Lỗi:** JSON hỏng, field sai kiểu, tên hook lạ, kể cả lỗi import → exit 0, không in gì (`TOKOPT_DEBUG=1` in lý do ra stderr).
- **Thời gian:** đo 2026-10-19 trên macOS, Python 3.14, trạng thái warm: trung vị 58 ms, bằng `python -c pass`. Phần tokopt thêm vào nằm trong sai số đo.
- **Quyết định bảo mật:** mặc định KHÔNG gửi `permissionDecision`, để luật permission của người dùng vẫn áp dụng. Nếu gửi `"allow"` thì mọi lệnh được bọc (kể cả `git push --force`) sẽ bỏ qua bước hỏi quyền. Config `hook_permission` (mặc định `null`; nhận `"allow"` hoặc `"ask"`, giá trị khác bị bỏ qua) cho phép bật, kèm cảnh báo trong README.
- `[ASSUMPTION]` Claude Code áp dụng `updatedInput` khi không có `permissionDecision`. Kiểm chứng ở task P2-T4: chạy một session thật, kiểm tra bảng `runs` có bản ghi. Nếu không áp dụng, phải chọn lại giữa hai phương án: (a) dùng `"ask"`, (b) chỉ cài rules để agent tự gõ `tokopt run --`. Đây là `[TBD]`.
- `[ASSUMPTION]` Permission rule dạng `Bash(npm test:*)` có thể không khớp sau khi lệnh bị viết lại. Ghi vào README phần giới hạn.

---

## A4. Read guard (`tokopt hook read`, PreToolUse matcher `Read|Grep|Glob`)

**Mục tiêu:** chặn ba loại đọc lãng phí nhất. Mỗi lần chặn phải chỉ ra cách đọc đúng, để agent không bị kẹt.

```
Read(file_path, offset?, limit?):
  R1 junk:    path khớp junk_patterns (thư mục: chứa "/node_modules/"…; đuôi: endswith ".min.js", lockfile…)
              → deny "… is generated/vendored/lockfile (~T tokens). Read the source or manifest; slice with offset/limit if needed."
              Có offset/limit (đọc một lát cắt) → cho qua (người dùng chốt 2026-10-19: chỉ chặn đọc cả file,
              để lời khuyên "slice with offset/limit" là đường ra thật khi agent cần xem code thư viện)
              So khớp: pattern `/dir/` khớp một đoạn đường dẫn; `.ext` khớp đuôi; còn lại khớp nguyên tên file
              (`package-lock.json` không khớp `my-package-lock.json`). Đường dẫn Windows đổi `\` thành `/` trước.
  R2 bigfile: không có offset/limit và size > big_lines×40 bytes và đếm dòng > big_lines (800)
              → deny "… has N lines (~T tokens). Run `tokopt outline <path>`, then Read the range with offset/limit."
  R3 reread:  key = (session, realpath, f"{offset}:{limit}"); nếu mtime không đổi thì n++, mtime đổi thì n=1
              n > guard_repeat_reads (2) → deny "already read this exact range N times; file unchanged; content is still in context."
Grep/Glob(path):
  path nằm trong junk → deny kèm gợi ý tìm trong thư mục source
Grep(output_mode = "content", không có key head_limit, kể cả head_limit=0) và grep_head_limit > 0:
  → updatedInput: {...tool_input, head_limit: grep_head_limit}     # mặc định 200 [ASSUMPTION]
mọi lần deny: ghi đúng 1 dòng vào bảng guard(session, rule, path, est_tokens = size/4; Grep/Glob junk: 0)
đường dẫn tương đối (Claude Code thường gửi tuyệt đối) → ghép với `cwd` trong JSON của hook, không phải cwd của process
file không tồn tại hoặc là thư mục → không ý kiến (Claude Code tự báo lỗi)
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
Python (.py .pyi .pyw): ast.parse → FunctionDef/AsyncFunctionDef/ClassDef (kể cả lồng, kể cả nằm trong if/try/with),
        start = lineno (tính cả decorator), end = end_lineno, signature = dòng def gốc (đã cắt "…:")
        kind = class | function | method (def nằm ngay trong class, kể cả trong if của class)
        parent = tên đầy đủ có dấu chấm của class/def bao ngoài ("Shop.Item"), top-level → null
        signature = header lấy từ source bằng tokenize, gộp thành 1 dòng: bỏ comment, gộp khoảng trắng,
                    bỏ khoảng trắng sát ngoặc, bỏ dấu phẩy cuối khi ngoặc đóng nằm ở dòng riêng; chuỗi giữ nguyên văn
        đọc file theo coding cookie và BOM (tokenize.detect_encoding); \r\n và \r → \n (cùng cách đánh số với ast)
        cú pháp lỗi → không có outline, in 200 dòng đầu kèm lý do
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

Chi tiết đã chốt khi implement (P3-T2, `src/tokopt/langs.py`):

- **Che trước, so sau.** Lexer thay nội dung chuỗi và comment bằng khoảng trắng (giữ xuống dòng), rồi mới chạy regex và đếm ngoặc. Đặc thù theo ngôn ngữ:
  - template literal JS/TS và interpolation có chuỗi lồng: `${"}"}`, Kotlin/Dart `${…}`, Ruby `#{…}`, C# `$"…{x}…"` với `{{`;
  - chỉ template literal mới interpolate trong JS/TS (`'[${=]'` là chữ thường);
  - regex literal JS/Ruby:
    - là regex khi `/` đứng sau `( , = : [ ! & | ? { } ; + - * % ~ ^`, hoặc sau `return typeof case …`, kể cả bên trong `${…}`;
    - không phải regex khi đứng sau `<` hay `>` (JSX `</b>`, `<p>/path`), hoặc khi là `/>`;
  - JSX: dấu nháy đứng ngay sau chữ (`Don't`) không mở chuỗi;
  - chuỗi đặc biệt từng ngôn ngữ:
    - Go: raw string;
    - Rust: `r#"…"#`, và lifetime `'a` (không phải chuỗi);
    - C#: `@"…"` với `""`;
    - nháy ba của Kotlin/Java/C# và Dart; `r'…'` của Dart;
    - `#` comment của PHP (trừ `#[`) và Ruby; heredoc và `%w(…)` của Ruby;
  - comment khối lồng nhau ở Rust, Kotlin, Dart;
  - chuỗi thường được kéo nhiều dòng ở Rust, PHP, Ruby.
- **start** tính cả các dòng annotation/attribute/decorator ngay phía trên (`@…`, `#[…]`, C# `[…]`), kể cả decorator nhiều dòng, giống Python tính decorator.
- **end:** quét header từ đầu dòng khai báo tới thân hàm. Header được theo dõi `() []`, và `<>` ở mức ngoài cùng cho generic.
  - Gặp `{`: end là dòng có `}` khớp.
  - Gặp `;`: end là dòng đó.
  - Gặp `=` hoặc `=>`: thân là biểu thức, end ở `;` hoặc `,`, hoặc ở cuối một dòng không nối tiếp.
  - Hết dòng mà dòng sau không nối tiếp (`{ : extends implements with where throws …`): end là dòng đó. Đây là khai báo không thân, ví dụ `data class`.
  - `where` của Rust giữ header mở tới `{`.
- **Phạm vi của pattern:**
  - Method kiểu `Type name(…)` (Java, C#, Dart), và method hay field arrow trong class JS/TS, chỉ được thử khi dòng nằm trực tiếp trong thân class. Nếu không, lệnh gọi hàm trong thân method sẽ bị nhận nhầm.
  - C# và Dart thử thêm ở mọi phạm vi (local function), nhưng loại mọi kết quả có từ khoá lệnh trong phần kiểu trả về (`return new T()`, `await x()`…).
  - Constructor phải trùng tên class (Dart: `Cart.empty`).
- **kind:** `function` nằm trực tiếp trong class/interface/struct/trait/impl/enum/record/object/mixin/extension/module thì thành `method`.
- **parent:** tên đầy đủ có dấu chấm của symbol bao ngoài, xác định theo phạm vi dòng. Method Go nằm ngoài struct nên parent là null.
- **Tên:**
  - `impl` của Rust lấy tên kiểu đích (`impl Display for Cart` → `Cart`);
  - `companion object` không tên → `Companion`;
  - Kotlin bỏ backtick (`adds two items`);
  - Ruby `def self.x` → `x`;
  - Go `x := func(…)` là function.
- **Không phải symbol:**
  - method trong object literal JS; member của interface TS;
  - method của anonymous class Java; compact constructor `Point {` của Java;
  - property C#/Kotlin;
  - hàm Dart top-level không có kiểu trả về;
  - `class << self` của Ruby (method bên trong thuộc class bao ngoài).
- **Hiệu năng:** pattern chỉ chạy trên 300 ký tự đầu của dòng, vì khai báo luôn ở đầu dòng. Một dòng dữ liệu 57k ký tự từng làm regex backtrack nhiều phút.
- **Giới hạn đã biết:**
  - Code đã minify (một dòng rất dài) có thể làm lexer đoán sai regex literal và lệch ngoặc. Outline của file một dòng vốn vô ích, và read guard đã chặn `dist/`, `.min.js`.
  - Swift, C, C++ chưa hỗ trợ: A5.1 có liệt kê nhưng chúng không thuộc P3-T2.

### A5.2 Lệnh

- `tokopt outline <file>`: in mỗi symbol một dòng, thụt lề 2 dấu cách mỗi cấp parent: `L12-40  def foo(a, b)`. Dòng cuối: `[N lines, ~T tokens; Read with offset=12 limit=29]`, với ví dụ offset/limit là symbol top-level dài nhất (bằng nhau thì lấy cái đầu); T tính bằng `est_tokens` với `token_ratios` trong config. Không có symbol → `no functions or classes` + `[N lines, ~T tokens]`. Ngôn ngữ chưa hỗ trợ hoặc file không parse được → `[tokopt: <lý do>; first 200 of N lines]` rồi 200 dòng đầu, exit 0. File không tồn tại, thư mục, file nhị phân (có byte NUL trong 8 KB đầu) → exit 1.
- `tokopt symbol <name> [--path DIR] [--kind K]`:
  1. Liệt kê file nguồn bằng `git ls-files` nếu là git repo, nếu không thì duyệt thư mục (bỏ junk_patterns, file > 1 MB, file nhị phân).
  2. Tiền lọc nhanh: chỉ giữ file chứa chuỗi `name`.
  3. Chạy `extract` trên các file đó, khớp đúng tên (hoặc `Class.method`).
  4. In code của symbol kèm số dòng. Tối đa 3 kết quả và 300 dòng mỗi kết quả; các kết quả khác chỉ liệt kê vị trí.
- `tokopt refs <name>`: regex `\bname\b` trên các file nguồn, nhóm theo file, tối đa 50 dòng. Bỏ dòng định nghĩa.
- Chi tiết đã chốt khi implement (P3-T3, `src/tokopt/codesearch.py`):
  - **File nguồn:**
    - lấy từ `git ls-files --cached --others --exclude-standard`, nên gồm cả file mới chưa commit nhưng không file nào bị ignore; không phải git repo thì duyệt thư mục;
    - chỉ các ngôn ngữ `extract` hỗ trợ, bỏ `junk_patterns` (cùng hàm `config.is_junk` với read guard), file > 1 MB, file có byte NUL trong 8 KB đầu;
    - `--path DIR` đổi gốc tìm kiếm.
  - **Đường dẫn in ra:** tương đối với thư mục hiện tại nếu file nằm dưới đó, ngược lại là đường dẫn tuyệt đối.
  - **`symbol`:**
    - `name` khớp mọi symbol cùng tên. `A.b` (sâu bao nhiêu cấp cũng được) khớp phần đuôi của tên đầy đủ; `--kind` lọc theo kind.
    - Mỗi kết quả in một dòng tiêu đề `path:start-end  kind Tên.đầy.đủ`, rồi từng dòng code dạng `{số:>5}  {dòng}`.
    - Quá 300 dòng thì in `… N more lines; Read path with offset=… limit=…`. Từ kết quả thứ 4 trở đi thì in `also: …`.
    - Dòng cuối là `[N matches]`. Không tìm thấy thì báo ra stderr và exit 1.
  - **`refs`:**
    - Chỉ đếm tên trong code: chuỗi và comment bị che, dùng lexer của A5.1 cho regex-language và `tokenize` cho Python. Code trong interpolation (`${total(1)}`, f-string từ Python 3.12) vẫn được đếm.
    - `A.b` tìm theo `b`. Dòng định nghĩa là dòng đầu tiên trong phạm vi symbol cùng tên có chứa tên đó, và được liệt kê riêng ở dòng cuối.
    - Mỗi chỗ dùng in một dòng `{số:>6}: {dòng đã strip}`, nhóm theo file.
    - Quá 50 dòng thì phần còn lại vẫn được liệt kê, dạng `path: N more at lines 11-30, 42`, nên không chỗ dùng nào bị mất.
    - Không có kết quả thì exit 1.
  - **Tiền lọc (để repo lớn vẫn nhanh):** cả hai bước đều trả về tập lớn hơn tập thật, chỉ bỏ những file chắc chắn không có kết quả. Test so kết quả có và không có tiền lọc trên hơn 300 tên, và kiểm thêm trên 631 file thật với 300 tên.
    - `symbol` chỉ parse file có một dòng chứa tên mà khớp một pattern khai báo của ngôn ngữ, với đúng tên đó trong nhóm bắt. Ngoại lệ: `companion object` không tên của Kotlin.
    - `refs` chỉ che chuỗi/comment của file mà byte có chứa tên ở dạng nguyên từ.
  - **Đo 2026-10-19:**
    - repo tổng hợp 2.000 file (`scripts/bench_codesearch.py`): 0,15–0,2 s mỗi lệnh, tính cả khởi động Python;
    - một project thật có 4.678 file nguồn: 0,4–0,9 s.
- `tokopt map [DIR] [--budget 1500]` (repo map kiểu aider, bản rút gọn):
  ```
  defs = extract mọi file
  ref_count[name] = số file khác (không phải file định nghĩa) chứa \bname\b   # tiền lọc bằng `in`
  score(file) = Σ log(1 + ref_count[s]) / definers[s] với s là top-level symbol của file
                definers[s] = số file định nghĩa tên s ở top-level (thường là 1)
  xếp file theo score giảm dần (bằng nhau thì theo đường dẫn); với mỗi file in "path" + các symbol top-level (signature 1 dòng)
  thêm từng dòng, không dòng nào làm output (kể cả dòng cuối) vượt budget
  ```
  Repo > 5.000 file: chỉ quét các file đã thay đổi trong 90 ngày (`git log --since`) cộng các thư mục người dùng chỉ định. `[ASSUMPTION]`
  - Chi tiết đã chốt khi implement (P3-T4, `codesearch.repo_map`):
    - **Chia cho `definers`** (khác bản đầu của A5.2). Khi một tên được định nghĩa ở nhiều file, không thể biết một tham chiếu trỏ về file nào, nên các file đó chia nhau phần điểm. Trên chính repo tokopt, thiếu phép chia thì 5 file mẫu `tests/fixtures/code/sample.*` (cùng định nghĩa `Item`, `Cart`, `Store`, `parse`) đứng đầu map; có phép chia thì `filters.py`, `codesearch.py`, `outline.py` đứng đầu. Tên chỉ có một định nghĩa thì công thức không đổi.
    - **`ref_count`:** mỗi file được tách thành tập identifier đúng một lần (`[A-Za-z_$][\w$]*` trên văn bản gốc, nên tên trong chuỗi và comment cũng được tính, như "tiền lọc bằng `in`"). Như vậy đếm chỉ là tra tập, không phải quét số symbol × số file.
    - **Một dòng symbol:** `  L{start}  {signature}`, signature cắt ở 120 ký tự kèm `…`. File không có symbol top-level thì không in. Một file chỉ còn chỗ cho tên mà không cho symbol nào thì bỏ cả tên.
    - **Ngân sách:** mỗi dòng được tính bằng `est_tokens` riêng. Tổng các phần không bao giờ nhỏ hơn ước lượng của cả văn bản, nên output luôn ≤ `--budget`. Phần cho dòng cuối được giữ trước theo trường hợp xấu nhất.
    - **Dòng cuối:** `[map: shown of N files, ~T tokens; more with --budget 2×B; only files changed in the last D days: k of M]`, trong đó hai phần sau chỉ có khi đúng.
    - **Repo lớn:** ngưỡng 5.000 file và 90 ngày nằm trong config (`map_max_files`, `map_recent_days`). Phần "cộng các thư mục người dùng chỉ định" là đối số `DIR` (map của một thư mục con thì không bị giới hạn nếu thư mục đó ≤ ngưỡng).

### Kiểm chứng A5

- `tests/fixtures/code/` có một file mẫu cho mỗi ngôn ngữ, kèm file `expected_outline.json` viết tay.
- Đạt khi:
  - Python khớp 100%.
  - Ngôn ngữ dùng regex khớp ≥ 90% symbol và sai `end` tối đa 1 dòng. Một symbol khớp khi trùng `name`, `parent`, `start`; test còn đòi precision ≥ 85%. Kết quả P3-T2 (2026-10-19): cả 10 file mẫu đạt 100% recall, 100% precision.
  - Kiểm thêm trên code thật (không có kỳ vọng viết tay): sau khi che, `{` và `}` phải cân bằng.
    - Mẫu: 2026-10-19, khoảng 7.000 file trên máy người dùng (các project, node_modules, pub-cache, crates).
    - Kết quả: 0 crash, 0 file chậm hơn 5 s.
    - Lệch ngoặc: 0% ở mọi ngôn ngữ trừ JS/TS (0,1–0,6%), và toàn bộ số đó là code đã minify.
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
- Chi tiết đã chốt khi implement (P4-T1, `src/tokopt/transcripts.py`):
  - **Claude Code:**
    - `FACT`, đếm trên transcript thật của người dùng ngày 2026-10-19: mỗi dòng `assistant` chứa đúng 1 block; một request trải trên 1–6 dòng, mọi dòng mang cùng một usage. Vậy parser khử trùng lặp theo `(message.id, requestId)` và gộp các block. Đếm mọi dòng sẽ thổi output token lên 2,59 lần (209 session).
    - `type=system`, `subtype=compact_boundary` là ranh giới compaction; `isSidechain=true` là request của subagent; `usage.cache_creation.ephemeral_1h_input_tokens` cho biết TTL 1 giờ.
    - Phần lớn block `thinking` không có nội dung (1.488/1.708), tức thinking bị ẩn là trường hợp thường gặp (A6.6).
  - **Mỗi request mang** usage, model, thời điểm, số ký tự text/thinking, input của tool ghi file và tool khác, các `tool_use` (nối với `tool_result` theo id), và những gì được gửi kèm request đó: các tool_result, số ký tự chữ của người dùng, cờ "có compaction trước đó". Thông tin này tính riêng cho chuỗi chính và chuỗi subagent.
  - **Codex:** mỗi sự kiện `token_count` là một request (`last_token_usage`; input thực = `input_tokens − cached_input_tokens`, phần cached tính là cache_read). `function_call` gắn vào request kế tiếp, output nối theo `call_id`. Máy người dùng chưa có Codex nên phần này chỉ được test bằng fixture.
  - **Phòng thủ:** dòng JSON hỏng, dòng không phải object, file không đọc được hoặc không phải UTF-8, field thiếu hoặc sai kiểu đều bị bỏ qua. Request phải có `message.id` dạng chuỗi và ít nhất một số token hợp lệ.
  - **`sessions(days)`:** liệt kê `<claude>/projects/*/*.jsonl` và `<codex>/sessions/**/rollout-*.jsonl`; session có request cuối trong `days` ngày (bỏ qua nhanh theo mtime).

### A6.2 Các báo cáo

| Lệnh | Tính gì |
|---|---|
| `report --days N` | sessions, tổng các loại token, ETU/session, ETU/lượt gọi, cache hit = cache_read/(input+cache_write+cache_read), token tool output ước lượng, **tỉ trọng output = Σ(output·w_out)/Σ ETU** (theo phiên và tổng), top 10 session theo ETU |
| `waste --days N` | (1) top 10 họ tool theo `reread_cost` (`Bash:<prog>`, `Read:<path>`), hiện cả T và ETU; (2) các output đơn lẻ ≥ 5k token, xếp theo `reread_cost`; (3) lần đọc hoặc lệnh chạm junk; (4) file bị Read ≥ 3 lần trong một session; (5) session > 80 lượt gọi; (6) cache vỡ (A6.5); (7) phiên có tỉ trọng output > 35% `[ASSUMPTION]`, kèm phân rã output (A6.6). Mỗi nhóm kèm gợi ý sửa |
| `gain` | Từ SQLite: tiết kiệm **trực tiếp** theo kind (token ước lượng của raw − out), Σ est_tokens từ guard theo rule, top 10 lệnh tiết kiệm nhiều nhất, tỉ lệ lệnh bị bọc trên tổng lệnh Bash (lấy từ transcript). Thêm: (a) tiết kiệm **tính cả đọc lại (ước tính)** = trực tiếp × (w_cw + w_cr·R̄), R̄ = trung vị số request còn lại sau mỗi tool_result Bash trong transcript N ngày; chưa có transcript thì R̄ = 20 `[ASSUMPTION]` và ghi rõ "dùng giá trị mặc định"; (b) theo kind: số lần cắt, số tee_id bị recall (đếm khác nhau), tỉ lệ recall; tỉ lệ > 20% với ≥ 10 lần cắt `[ASSUMPTION]` → cảnh báo "bộ lọc <kind> có thể cắt quá tay"; (c) số lần chạy có `error_overflow = 1`. `--json`, `--days N` (mặc định: toàn bộ). Chưa có DB hoặc DB rỗng → `no data yet` (không tạo DB). Ngưỡng cảnh báo recall nằm trong config (`recall_warn_rate`, `recall_warn_min_cuts`), R̄ mặc định là `reread_default`. "Số lần cắt" = số lần chạy có lưu tee. Tỉ lệ lệnh bị bọc cần transcript nên thêm ở P4-T2 |
| `context` | Token ước lượng của các instruction file sẽ được nạp (global/project CLAUDE.md, CLAUDE.local.md, .claude/rules/*.md, AGENTS.md, GEMINI.md) + số MCP server (~/.claude.json, .mcp.json); cảnh báo khi > 1.500. Thêm: MCP server đã cấu hình (`~/.claude.json`, `.mcp.json`, settings của project) và skill (người dùng, project, plugin), kèm số lần được gọi trong N ngày (đếm `tool_use` tên `mcp__<server>__*` và input `skill` của tool `Skill`); 0 lần → gợi ý tắt, không tự tắt. Chỉ báo token schema MCP khi đọc được schema `[ASSUMPTION]` |
| `snapshot --label L` | Lưu bản tóm tắt report vào `~/.tokopt/results/snapshot-L.json` |

- Các phép tính theo phiên bỏ qua request của subagent.
- Chi tiết đã chốt khi implement (P4-T2, `src/tokopt/reports.py`):
  - **Mặc định và tuỳ chọn:** `--days` mặc định là 7 (`context`: 30); mọi lệnh có `--json`; `context --path DIR`. Không có transcript → `no transcripts in the last N days`.
  - **Số liệu theo phiên:** chỉ tính chuỗi chính; "lượt gọi" là request.
  - **Token tool output:** `est_tokens` (tỉ lệ của báo cáo) trên text của tool_result.
  - **R của một tool_result:** số request của chuỗi chính sau request mang tool_result đó, dừng ở compaction gần nhất. `reread_cost = T·w_cw + T·w_cr·R` (A1).
  - **`waste`:**
    - Họ tool: `Bash:<prog>` (`program_of`; với Codex thì lấy lệnh cuối của `bash -lc …`), `Read:<path>`, các tool khác theo tên.
    - (3): Read/Grep/Glob có path khớp `junk_patterns`, hoặc lệnh Bash có từ (chứa `/` hoặc `.`) khớp.
    - Ngưỡng nằm trong config: `waste_big_output_tokens` 5.000, `waste_reads_per_session` 3, `waste_max_requests` 80, `waste_output_share` 0,35.
    - Mỗi nhóm có dòng `fix:`. JSON giữ thứ tự 7 nhóm: `tool_families, big_outputs, junk, repeated_reads, long_sessions, cache_breaks, output_heavy`.
  - **Cache vỡ (A6.5):** TTL là `cache_ttl_seconds` (300). Từ khi phiên đã có request ghi cache TTL 1 giờ (`cache_creation.ephemeral_1h_input_tokens > 0`), TTL là 3.600. Ngưỡng `cache_break_min_write` 10.000.
  - **Phân rã output (A6.6):**
    - Thinking bị ẩn (block `thinking` không có nội dung): text, input tool ghi file và input tool khác ước lượng theo ký tự / tỉ lệ `default`; thinking = output_tokens − phần đó. Nếu phần ước lượng vượt output_tokens thì thu nhỏ theo tỉ lệ.
    - Thinking hiện đủ: output_tokens chia theo ký tự của cả bốn loại.
  - **`context`:**
    - File chỉ dẫn: `~/.claude/CLAUDE.md`, `~/.claude/rules/*.md`; ở thư mục hiện tại và mọi thư mục cha (trừ gốc) thì `CLAUDE.md`, `CLAUDE.local.md`, `.claude/rules/*.md`, `AGENTS.md`, `GEMINI.md`; thêm `~/.codex/AGENTS.md`, `~/.gemini/GEMINI.md`.
    - MCP: `mcpServers` của `~/.claude.json` (toàn cục và `projects[<thư mục này>]`), `.mcp.json`, `.claude/settings*.json`.
    - Skill: `~/.claude/skills/*/SKILL.md`, `.claude/skills/*/SKILL.md`, `~/.claude/plugins/**/skills/*/SKILL.md`; tên lấy từ `name:` trong frontmatter.
    - Số lần gọi: `tool_use` tên `mcp__<server>__…`; tool `Skill` với `skill` (`plugin:tên` khớp theo phần sau dấu `:`).
    - `context_warn_tokens` 1.500. Không báo token schema MCP, vì không đọc được schema.
  - **`snapshot --label L`:** nhãn chỉ gồm chữ, số, `.`, `_`, `-`. File lưu tóm tắt report và số mục của từng nhóm waste (P6 so nhóm (2) giữa hai snapshot).
  - **`gain` từ transcript:**
    - R̄ là trung vị R của các tool_result Bash trong `gain_transcript_days` (30) ngày.
    - Lệnh bị bọc: command có `tokopt run`, hoặc kết quả có footer `[tokopt: `. P2-T4 sẽ cho biết transcript giữ lệnh gốc hay lệnh đã viết lại; footer chỉ có khi output bị cắt, nên số này có thể thấp hơn thật.
  - **Đo trên transcript thật** (2026-10-19, 56 session/7 ngày): `report` 4,8 s, `waste` 4,0 s, `context` 3,0 s.
- **Lưu số liệu cho `gain`:** mỗi bản ghi `runs` lưu số ký tự tiết kiệm theo lớp của A1 (`saved_default`, `saved_hex`, `saved_non_ascii` = ký tự của output gốc − ký tự của output agent nhận, kể cả footer). Token = Σ ký tự_lớp / tỉ lệ_lớp, tính lúc báo cáo, nên đổi tỉ lệ (config, hoặc hiệu chỉnh A6.4) thì áp được cho cả dữ liệu cũ. Bản ghi trước P1-T5 chỉ có tổng ký tự → (raw_chars − out_chars) / tỉ lệ `default`. Guard vẫn lưu `est_tokens = size // 4` (A1: giữ tốc độ hook).

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
- Tên lệnh: chạy trong `report` (P4-T2), và `report` in kết quả hiệu chỉnh.
- Chi tiết đã chốt khi implement (P4-T1, `measure.calibrate`):
  - **Chọn mẫu:** chỉ dùng cặp request liên tiếp của chuỗi chính mà giữa chúng chỉ có tool_result. Không có chữ người dùng (vì nó làm `y` tăng thêm), không có compaction, không có cache vỡ.
  - **Fit:** dùng 4/5 số mẫu để fit. Mẫu có phần dư > 3 × trung vị phần dư là ngoại lai, bị bỏ rồi fit lại. Sai số trung vị đo trên 1/5 còn lại. Lớp ký tự không xuất hiện trong mẫu thì giữ tỉ lệ của config; hệ số ≤ 0 thì loại kết quả.
  - **Ngưỡng:** 200 mẫu và 15% là `[ASSUMPTION]`, nằm trong config (`calibration_min_samples`, `calibration_max_error`). Không đạt thì không ghi file (file cũ, nếu có, được giữ). Chỉ `measure.report_ratios` đọc file này; `filters` không bao giờ đọc.
  - **Trên transcript thật của người dùng** (2026-10-19, 29.448 mẫu): fit ra 2,42 / 0,89 / 0,62 ký tự/token, sai số trung vị 24,6%, nên không đạt và không ghi. Context tăng giữa hai request có lẽ còn gồm phần Claude Code tự chèn kèm tool_result. Cần xem lại khi có thêm dữ liệu; ngưỡng không được nới tự động.

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

- Chi tiết đã chốt khi implement (P4-T3, `src/tokopt/bench.py`):
  - **Lệnh:** `tokopt bench TASKS.json --label L [--rules FILE] [--repeat N]`, kết quả ở `~/.tokopt/results/bench-L.jsonl`, mỗi lần chạy một dòng:
    - `label`, `task`, `repeat`, `agent`, `passed`, `timeout`, `agent_exit`;
    - `usage {input, cache_write, cache_read, output}`, `etu`, `cost`, `turns`, `wall`;
    - `rules`, `global_rules`, `ts`.
    Lệnh so sánh là `tokopt compare A B [--json]`.
  - **`tasks.json`:** `check` và `setup` là một chuỗi (chạy bằng bash) hoặc một list argv. `agent_args` ở cấp file hoặc cấp task, ví dụ `["--permission-mode", "acceptEdits"]`. `agent_cmd` thay chương trình gọi agent (agent giả trong test, wrapper). Mặc định: `repeat` 1, `timeout` 900 s.
  - **Môi trường của agent:** có thêm `TOKOPT_BENCH_LABEL`, `TOKOPT_BENCH_TASK`, `TOKOPT_BENCH_REPEAT`.
  - **Mỗi lần chạy:** worktree tạo trong một thư mục tạm riêng. Agent và check chạy trong process group riêng, timeout thì cả nhóm bị kill. `finally` luôn chạy `git worktree remove --force` và xoá thư mục tạm; cuối bench chạy `git worktree prune`. Lần chạy bị timeout thì không chạy check và tính là fail.
  - **`--rules`:** block nằm giữa `<!-- tokopt:begin bench -->` và `<!-- tokopt:end -->` trong `CLAUDE.md` (Codex: `AGENTS.md`, Gemini: `GEMINI.md`) của worktree; block có sẵn thì bị thay. CLAUDE.md toàn cục có block tokopt → cảnh báo ra stderr và ghi `global_rules: true`.
  - **`compare`:**
    - CI dùng bootstrap từng nhánh riêng: lấy mẫu có hoàn lại cho từng nhánh, tính `median(B)/median(A) − 1`, 2.000 lần, seed 42, phân vị 2,5% và 97,5%.
    - Ngưỡng `min_saving` 10% và `max_drop` 0 nằm trong config (`bench_min_saving`, `bench_max_pass_drop`).
    - Verdict output dùng cùng luật trên median output tokens. Vì vậy một biến thể giảm 5% ra INCONCLUSIVE: có giảm, nên không phải REJECT, nhưng không thắng.
  - **Chưa kiểm với agent thật:** định dạng `claude -p` là `FACT`. Định dạng Codex (`turn.completed`) và Gemini (`stats.models.*.tokens`) vẫn là `[ASSUMPTION]`, và sẽ được kiểm khi người dùng chạy P6.

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

- Cho tới P4-T4, `tokopt statusline` là stub: đọc hết stdin, không in gì, exit 0, vì `install` (P2-T3) đã có thể thêm nó vào `statusLine`.

---

## A8. Cài đặt và gỡ (`tokopt install` / `tokopt uninstall`)

- **Cài đặt** idempotent:
  1. Backup sang `~/.tokopt/backups/<ts>/`.
  2. **Claude Code** `~/.claude/settings.json` (hoặc `$CLAUDE_CONFIG_DIR/settings.json`): thêm hook với `command = "<đường dẫn tuyệt đối tới tokopt> hook …"`. Đã có entry chứa `tokopt hook <tên>` thì không thêm entry mới; nếu đường dẫn khác (cài lại ở chỗ khác) thì cập nhật command của entry đó tại chỗ, để hook không trỏ vào file đã mất.
     - Đường dẫn tokopt: console script đang chạy, rồi `tokopt` trên PATH, cuối cùng `<python> -m tokopt` (kèm cảnh báo, vì Claude Code có thể không import được tokopt). Đường dẫn Windows ghi dạng `C:/…`, có dấu cách thì đặt trong nháy kép, để chạy được cả trong Git Bash lẫn cmd `[ASSUMPTION]` (chưa biết Claude Code trên Windows chạy hook bằng shell nào; kiểm ở P5-T2).
     - `settings.json` không phải JSON hợp lệ hoặc không phải object → dừng, exit 1, không ghi đè.
     - Những gì install tự tạo (file, `hooks`, `hooks.<event>`) ghi vào `~/.tokopt/install.json`, để uninstall trả lại đúng như trước, kể cả container rỗng mà người dùng đã có. Ghi file kiểu atomic (file tạm + `os.replace`).
     - PreToolUse `Bash` → `hook bash`
     - PreToolUse `Read|Grep|Glob` → `hook read`
     - SessionStart `compact|clear` → `hook session-start`
     - `statusLine` → `tokopt statusline`, chỉ khi người dùng chưa có statusLine.
  3. (P3-T5) Rules block giữa marker `<!-- tokopt:begin vN -->`…`<!-- tokopt:end -->` trong CLAUDE.md, AGENTS.md, GEMINI.md. Thay thế block cũ, giữ nguyên nội dung khác.
     - File đích là file chỉ dẫn toàn cục của từng agent: `$CLAUDE_CONFIG_DIR` hoặc `~/.claude/CLAUDE.md` (luôn ghi), `$CODEX_HOME` hoặc `~/.codex/AGENTS.md`, `~/.gemini/GEMINI.md`. Hai file sau chỉ được ghi khi thư mục của agent đã tồn tại; tokopt không tạo thư mục cho agent không dùng.
     - Nội dung là `src/tokopt/templates/rules.md` (v1, ≤ 350 token theo A1). Biến thể nằm trong `templates/variants/` để bench chọn (N1.5c). Chưa có bench thì cài v1.
     - Có block (bất kỳ vN) thì thay đúng chỗ đó. Chưa có thì nối vào cuối, sau một dòng trống; file chưa có thì tạo mới. Phần ngăn cách đã thêm được ghi vào `install.json` (`rules:<path>`).
     - File được đọc và ghi dạng byte UTF-8, giữ nguyên CRLF (block cũng dùng CRLF). File không phải UTF-8 → dừng, không ghi.
     - Gỡ: bỏ block và đúng phần ngăn cách đã thêm, nên file trở về đúng từng byte; file do install tạo thì bị xoá. Nội dung người dùng thêm quanh block sau khi cài vẫn được giữ. Không có bản ghi thì bỏ block cùng một dòng trống ngay trước nó.
     - `doctor` liệt kê từng file: `v1 in <path>`, `missing in <path>` (tính là lỗi), hoặc `<agent> not installed`.
  4. **Codex, Gemini:** v0.1 chỉ cài rules block, hướng dẫn agent tự dùng `tokopt run --` và `tokopt outline`. Hook native cho hai agent này là `[TBD]`.
- **Gỡ:** xoá đúng các entry chứa `tokopt hook` hoặc `tokopt statusline`, xoá rules block; không đụng vào gì khác. Group/event/`hooks` bị rỗng do việc gỡ chỉ bị xoá khi install đã tạo chúng (theo `install.json`; không có bản ghi thì xoá). File do install tạo mà gỡ xong còn `{}` thì xoá file. Mỗi lần ghi đều backup trước.
- **`tokopt doctor`:** phiên bản, lệnh tokopt sẽ cài, Python (phiên bản, đường dẫn), đường dẫn settings, từng hook: `installed`/`missing`, chương trình không tồn tại (`not found`) hoặc chạy `<lệnh> --version` qua bash (như Claude Code chạy hook) không ra `tokopt …` (`does not run: <lý do>`), statusLine (`tokopt`/`yours (kept)`/`none`), số bản ghi `runs` và `guard` 7 ngày qua. Exit 1 khi thiếu hook hoặc hook không chạy được.
- `tokopt init-project`: thêm `permissions.deny` cho các thư mục junk vào `.claude/settings.json` của project, và tạo `.geminiignore`.
- **Script bootstrap một lệnh:**
  - `install.sh`: cần python3 ≥ 3.9. Ưu tiên `uv tool install`; nếu không có thì tạo `python3 -m venv ~/.tokopt/venv`, `pip install`, rồi symlink `~/.local/bin/tokopt`. Cuối cùng chạy `tokopt install`.
  - `install.ps1`: tương tự, dùng `venv\Scripts\tokopt.exe`.

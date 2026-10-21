# toknix

**Cắt token mà coding agent lãng phí, và chứng minh được.** · [English](README.md)

toknix là một công cụ dòng lệnh (Python ≥ 3.9, chỉ dùng thư viện chuẩn) cho Claude Code, Codex CLI và
Gemini CLI. Nó làm ba việc:

1. **Cắt phần lãng phí:** output lệnh ồn ào, đọc file sinh tự động, đọc cả file trong khi chỉ cần một hàm.
2. **Đo phần đã cắt**, từ dữ liệu của chính nó và từ transcript của agent.
3. **Chứng minh chất lượng không giảm**, bằng cách chạy A/B một agent thật trên task của chính bạn.

Không có gì rời khỏi máy bạn. Mọi dữ liệu toknix giữ nằm trong `~/.toknix/`.

## Cài đặt

```bash
curl -fsSL https://raw.githubusercontent.com/lephus/toknix/main/install.sh | sh
```

Trên Windows (PowerShell, đã cài Git for Windows):

```powershell
irm https://raw.githubusercontent.com/lephus/toknix/main/install.ps1 | iex
```

Script cài:

- dùng `uv tool install` nếu máy có [uv](https://docs.astral.sh/uv/). Nếu không, nó tạo một venv riêng
  trong `~/.toknix/venv`, nên Python hệ thống bị đánh dấu "externally managed" (PEP 668, ví dụ Ubuntu 24.04)
  không bị đụng tới, rồi tạo `~/.local/bin/toknix` trỏ vào venv đó;
- thêm `~/.local/bin` vào PATH trong profile của shell (`~/.zshrc`, `~/.bash_profile` hoặc `~/.bashrc`,
  `config.fish`, `~/.profile`) nếu PATH chưa có: hai dòng có đánh dấu, không bao giờ thêm trùng. Sau đó mở
  terminal mới, hoặc chạy dòng `export` mà script in ở cuối;
- chạy `toknix install`, rồi `toknix doctor`.

| Muốn | Chạy |
|---|---|
| xem trước sẽ làm gì | `curl -fsSL …/install.sh \| sh -s -- --dry-run` |
| chỉ cài lệnh, không cài hook | `… \| sh -s -- --no-hooks` |
| không sửa profile của shell | `… \| sh -s -- --no-modify-path` |
| gỡ hết | `… \| sh -s -- --uninstall`, hoặc `toknix uninstall` nếu chỉ muốn gỡ hook và rules |
| cài không qua script | `uv tool install git+https://github.com/lephus/toknix && toknix install` |

Trên Windows, `irm | iex` không truyền được tuỳ chọn. Hãy đặt `$env:TOKNIX_DRY_RUN = "1"` hoặc
`$env:TOKNIX_UNINSTALL = "1"` trước khi chạy.

### Cập nhật

Chạy lại lệnh cài:

```bash
curl -fsSL https://raw.githubusercontent.com/lephus/toknix/main/install.sh | sh
```

- Lệnh cài lại toknix từ bản mới nhất trên `main`, kể cả khi số version không đổi.
- Sau đó `toknix install` trỏ các hook vào bản vừa cài và thay rules block bằng bản mới. Nó không bao giờ
  tạo bản trùng.
- Kiểm tra bằng `toknix --version` và `toknix doctor`. `doctor` cũng báo khi rules block cũ hơn bản toknix
  đang cài.

Muốn đứng yên ở một bản phát hành thay vì `main`:
`curl -fsSL …/install.sh | TOKNIX_REF=v0.2.1 sh`.

**Khi phát hành bản mới**:
1. Tăng `__version__` trong `src/toknix/__init__.py` và thêm một mục vào `CHANGELOG.md`.
2. Push lên `main`.
3. Gắn tag cho bản phát hành (`git tag v0.2.2 && git push origin v0.2.2`) để người dùng có thể ghim bản đó.

### Gỡ cài đặt

Chạy lại đúng script đã dùng để cài, thêm `--uninstall`:

```bash
curl -fsSL https://raw.githubusercontent.com/lephus/toknix/main/install.sh | sh -s -- --uninstall
```

Lệnh này làm ba việc:

1. Chạy `toknix uninstall`: bỏ 4 hook và statusLine khỏi `~/.claude/settings.json`, và bỏ rules block khỏi
   `~/.claude/CLAUDE.md` (cùng `AGENTS.md` / `GEMINI.md` nếu có). Các file này trở về đúng như trước khi cài.
2. Gỡ chính lệnh `toknix`: `uv tool uninstall toknix`, hoặc xoá venv `~/.toknix/venv` và link
   `~/.local/bin/toknix`, cùng hai dòng PATH mà script cài đã thêm vào profile của shell.
3. Giữ lại dữ liệu trong `~/.toknix` (thống kê, output đã lưu). Muốn xoá luôn thì chạy `rm -rf ~/.toknix`.

Sau đó **khởi động lại Claude Code** để nó không còn nạp các hook cũ.

| Muốn | Chạy |
|---|---|
| xem trước sẽ gỡ những gì | `curl -fsSL …/install.sh \| sh -s -- --uninstall --dry-run` |
| tắt toknix trong Claude Code nhưng giữ lệnh | `toknix uninstall` (bật lại bằng `toknix install`) |
| gỡ trên Windows (PowerShell) | `$env:TOKNIX_UNINSTALL = "1"; irm https://raw.githubusercontent.com/lephus/toknix/main/install.ps1 \| iex` |

### `toknix install` thay đổi những gì

`~/.claude/settings.json` (hoặc `$CLAUDE_CONFIG_DIR/settings.json`):

| Hook | Lệnh |
|---|---|
| PreToolUse `Bash` | `toknix hook bash` |
| PreToolUse `Read\|Grep\|Glob` | `toknix hook read` |
| SessionStart `compact\|clear` | `toknix hook session-start` |
| UserPromptSubmit | `toknix hook prompt`: nhắc bạn (không phải model) khi context vượt 200k |

Lệnh cũng thêm `statusLine` → `toknix statusline`, nhưng chỉ khi bạn chưa có status line. Mọi thiết lập
khác của bạn giữ nguyên.

Rules block được đặt giữa hai marker `<!-- toknix:begin v1 -->` và `<!-- toknix:end -->`, trong
`~/.claude/CLAUDE.md`. Nó cũng được đặt vào `~/.codex/AGENTS.md` và `~/.gemini/GEMINI.md`, nhưng chỉ khi
máy có cài Codex hoặc Gemini CLI.

Mọi thay đổi đều được backup trước vào `~/.toknix/backups/`. `toknix uninstall` trả các file về đúng như
cũ, từng byte. Sau khi cài, khởi động lại Claude Code rồi chạy `toknix doctor`.

## Trong một phiên làm việc, toknix làm gì

- **Output lệnh:** hook Bash viết lại các lệnh ồn ào (test, build, cài gói, git, liệt kê file) thành
  `toknix run -- '<lệnh>'`.
  - Agent vẫn nhận đủ lỗi, phần tổng kết và exit code. Test pass, thanh tiến độ và log tải về bị bỏ.
  - **Dòng lỗi không bao giờ bị cắt.**
  - Khi có phần bị cắt, output kết thúc bằng
    `[toknix: 84 → 37 lines; full output: toknix recall 851e91; exit 1]`.
  - `toknix recall <id>` in lại output gốc, đúng từng byte. `--grep` và `--lines` chỉ in một phần.
  - Lệnh bắt đầu bằng `TOKNIX_RAW=1` không bao giờ bị viết lại.
- **Read guard:** chặn ba kiểu đọc, và luôn nói cách đọc thay thế.
  - Đọc cả một file sinh tự động, file vendor hoặc lockfile (`node_modules/`, `package-lock.json`,
    `.min.js`…). Đọc một lát cắt bằng offset/limit vẫn được.
  - Đọc cả một file dài hơn 800 dòng. Lời nhắc gợi ý dùng `toknix outline`.
  - Lần đọc thứ ba y hệt của một file không đổi. Bộ đếm reset sau `/compact` hoặc `/clear`.

  Nó cũng thêm `head_limit: 200` cho các lệnh Grep ở chế độ content chưa có giới hạn.
- **Tìm code theo tên:** `toknix outline`, `symbol`, `refs`, `map` chạy được với Python (chính xác, qua
  `ast`) và JS/TS, Go, Java, Rust, PHP, Ruby, Dart, Kotlin, C# (bằng regex, có che chuỗi và comment).
- **Rules block:** nhắc agent dùng các lệnh trên, trả lời bằng diff thay vì tóm tắt, sửa file bằng Edit thay
  vì viết lại cả file, và giữ đầy đủ suy luận khi debug, khi làm bảo mật hay migration. Block dài 282 token.

## Các lệnh

| Lệnh | Làm gì |
|---|---|
| `toknix run -- "<lệnh>"` | chạy lệnh, in output đã lọc, giữ bản gốc để lấy lại; giữ nguyên exit code |
| `toknix recall <id> [--grep RE] [--lines A:B]` | in lại output đầy đủ của một lần chạy đã lọc |
| `toknix gain [--days N] [--json]` | số token tiết kiệm nhờ bộ lọc và read guard |
| `toknix ui [--port N] [--no-open]` | trang `http://127.0.0.1:8765` để xem toknix làm việc: từng lệnh đã lọc với output gốc và output đã lọc cạnh nhau, các lần read guard chặn, token tiết kiệm theo ngày. Chỉ chạy trên máy, chỉ đọc |
| `toknix outline <file>` | các symbol của một file kèm khoảng dòng (`L12-40  def foo(a, b)`) |
| `toknix symbol <tên> [--kind K] [--path DIR]` | in code của một hàm hay class (nhận cả `Class.method`), kèm số dòng |
| `toknix refs <tên> [--path DIR]` | các chỗ dùng một tên trong code (không tính chuỗi hay comment), không gồm dòng định nghĩa |
| `toknix map [DIR] [--budget 1500]` | các file được dùng nhiều nhất và symbol top-level của chúng, trong một ngân sách token |
| `toknix report [--days 7] [--json]` | lượng token đã dùng, đọc từ transcript: ETU và $, chi phí theo model, cache hit, tỉ trọng output, các session tốn nhất |
| `toknix waste [--days 7] [--json]` | 8 nhóm lãng phí (chi phí đọc lại, output lớn, đọc file junk, cache vỡ, subagent theo loại và model…), kèm cách sửa |
| `toknix context [--days 30] [--path DIR] [--json]` | file chỉ dẫn được nạp mỗi session, MCP server và skill, số lần chúng được dùng, và overhead cố định của mỗi request (tool, danh sách skill, MCP…) kèm chi phí của plugin, MCP server và skill không dùng |
| `toknix snapshot --label L [--days 7]` | lưu một bản tóm tắt report để so sánh về sau |
| `toknix bench tasks.json --label L [--rules FILE]` | chạy A/B một agent thật trong các git worktree dùng một lần |
| `toknix compare A B [--json]` | so sánh hai lần bench và đưa ra verdict |
| `toknix statusline` | status line của Claude Code: `ctx 92k/200k ▮▮▮▮▯▯▯▯▯▯ · 1.2M ETU · saved ~84k ETU (6.5%) · /clear at 120k` |
| `toknix install` / `uninstall` / `doctor` | cài, gỡ, và kiểm tra hook cùng rules |
| `toknix init-project [--path DIR]` | chặn Read các thư mục junk trong `.claude/settings.json` của project; tạo `.geminiignore` |

**ETU** là đơn vị để so sánh chi phí: một token input của `etu_model` (Claude Opus 5.5, $4 cho một triệu
token). Mỗi request tính theo giá niêm yết của chính model của nó: input, cache write (5 phút và 1 giờ), cache
read và output. Vì vậy ETU cộng được giữa các model và tỉ lệ với số đô la; báo cáo hiện cả hai. Với Opus 5.5:
`input + 1,25 × cache write 5 phút + 2 × cache write 1 giờ + 0,05 × cache read + 5 × output`. Số đô la là giá
API niêm yết: gói Pro hay Max không trả theo token, nên hãy coi nó là con số để so sánh. Model không có giá
(Codex, Gemini) dùng `weights` và không có $; với hai agent này hãy đặt `weights` trong config.

## Đọc `toknix gain`

```
toknix gain (all time)

Saved directly: 15.1k tokens (filters 15.1k, read guard 0)
With re-reads (estimate): 34.0k ETU ≈ $0.136 = direct × 2.25 (w_cw 1.25 + w_cr 0.05 × R̄ 20, claude-opus-5-5 prices)
  R̄ = 20 is the default value, not measured from transcripts yet

By filter kind
  kind          runs  cuts   saved  recalled  rate
  generic          3     2   15.1k         0    0%
```

- **Saved directly** là phần agent không phải nhận: output gốc trừ output đã lọc, cộng các file mà read guard
  đã chặn. Đây là số token ước lượng. Hãy coi nó là ước tính, không phải hoá đơn.
- **With re-reads** gần với số tiền bạn thật sự trả. Token đã vào context được ghi vào cache một lần, rồi
  được đọc lại ở mọi request sau đó trong session.
  - R̄ là trung vị số request còn lại sau một kết quả Bash, đo từ transcript của bạn. Mỗi kết quả tính theo
    giá model của request mang nó.
  - Khi chưa có transcript, R̄ là 20 theo giá Opus 5.5, và dòng output ghi rõ đó là giá trị mặc định.
- **cuts / recalled / rate:** mỗi bộ lọc đã cắt output bao nhiêu lần, và sau đó bạn hay agent cần đến
  `toknix recall` bao nhiêu lần.
  - Nếu tỉ lệ này trên 20% với từ 10 lần cắt trở lên, `gain` cảnh báo bộ lọc có thể cắt quá tay. Khi đó hãy
    báo lại lệnh đã gặp.
- **Bash calls wrapped** là tỉ lệ lệnh Bash đã đi qua `toknix run`.
- Một lần chạy mà riêng các dòng lỗi đã vượt ngân sách token được đếm là `error_overflow`. Các dòng lỗi đó
  vẫn được giữ đủ.

## Đọc `toknix compare`

`toknix bench` chạy mỗi task trong `tasks.json` nhiều lần. Mỗi lần dùng một `git worktree` mới, và worktree
đó luôn bị xoá sau khi chạy xong, kể cả khi bị timeout:

```json
{"repo": ".", "ref": "HEAD", "agent": "claude", "repeat": 3, "timeout": 900,
 "agent_args": ["--permission-mode", "acceptEdits"],
 "tasks": [{"id": "fix-cart", "prompt": "Sửa các test giỏ hàng đang fail.", "check": "npm test"}]}
```

Chạy một lần khi chưa có toknix và một lần khi đã có, rồi so sánh:

```
toknix compare baseline → v01 (12 vs 12 runs, 3 common tasks)

  pass rate             100% → 100%
  median ETU            37,151 → 24,606  -33.8% (95% CI -36.3% … -31.2%)
  median output tokens  2,972 → 1,712  -42.4% (95% CI -44.6% … -40.2%)
  ...
verdict: ACCEPT
output verdict: ACCEPT
```

| Verdict | Khi nào |
|---|---|
| **ACCEPT** | pass rate không giảm, median ETU giảm ít nhất 10%, và cả khoảng tin cậy 95% (bootstrap, 2.000 lần lấy mẫu) nằm dưới 0 |
| **REJECT** | pass rate giảm, hoặc B không rẻ hơn |
| **INCONCLUSIVE** | B rẻ hơn nhưng chưa rõ ràng: hãy chạy thêm vài lần |

**Output verdict** áp cùng luật đó, nhưng chỉ cho output token. Đây là cách chọn biến thể rules
(`bench --rules variant.md`).

Chỉ các task có ở cả hai lần chạy mới được so. Ví dụ trên là kết quả của agent giả trong bộ test. Chạy với
agent thật thì tốn tiền thật, vì vậy việc đó do bạn tự chạy.

## Cấu hình

Thiết lập nằm trong `~/.toknix/config.json`. Chỉ những key bạn đặt mới ghi đè giá trị mặc định (xem trong
`src/toknix/config.py`). Các key bạn hay cần đổi nhất:

| Key | Mặc định | Ý nghĩa |
|---|---|---|
| `max_lines`, `max_tokens` | 120, 3000 | ngân sách của một output đã lọc (nhân đôi khi lệnh fail) |
| `wrap_programs` | các trình chạy test, trình quản lý gói, git, docker… | các lệnh mà hook Bash viết lại |
| `guard_junk`, `guard_big_file_lines`, `guard_repeat_reads` | true, 800, 2 | các luật của read guard (`0` để tắt một luật) |
| `grep_head_limit` | 200 | giới hạn thêm vào Grep ở chế độ content (`0` = tắt) |
| `context_window` | 200000 | dùng cho status line; model 1M được tự nhận ra |
| `etu_model` | `"claude-opus-5-5"` | 1 ETU = một token input của model này (`""`: mọi request dùng `weights`) |
| `prices` | giá niêm yết của Anthropic | $ cho một triệu token theo model: input, cache write 5 phút, cache write 1 giờ, cache read, output. Mục bạn thêm được cộng vào mặc định |
| `weights` | `[1, 1.25, 0.1, 5]` | trọng số ETU của model không có giá: input, cache write, cache read, output |
| `hook_permission` | `null` | xem cảnh báo bên dưới |

| Biến môi trường | Tác dụng |
|---|---|
| `TOKNIX_RAW=1` | `toknix run` in output gốc, không lọc |
| `TOKNIX_DEBUG=1` | hook in lỗi ra stderr. Mặc định hook không bao giờ chặn agent và im lặng khi lỗi |
| `TOKNIX_HOME` | nơi toknix giữ dữ liệu (mặc định `~/.toknix`) |

### ⚠ `hook_permission`

Mặc định, hook Bash chỉ viết lại lệnh. Sau đó Claude Code vẫn hỏi quyền như với lệnh gốc.

Đặt `"hook_permission": "allow"` thì hook sẽ tự duyệt **mọi** lệnh nó viết lại. Điều đó gồm cả
`git push --force` và bất cứ lệnh nào mà permission rule của bạn lẽ ra đã chặn. Đừng đặt giá trị này, trừ
khi bạn chắc chắn muốn vậy. `"ask"` thì luôn hỏi.

## Giới hạn đã biết

- **Permission rule có thể không khớp với lệnh đã bị viết lại.** Một rule như `Bash(npm test:*)` có thể
  không khớp với `toknix run -- 'npm test'`, nên Claude Code có thể hỏi quyền ở chỗ trước đây không hỏi.
  Điều này chưa được kiểm trên Claude Code; nó là check B trong [docs/VERIFY.md](docs/VERIFY.md).
- **Số token là ước lượng.**
  - toknix đếm số ký tự theo từng lớp: khoảng 4 ký tự/token với văn bản thường, 2 với hex/base64, 1,5 với
    ký tự ngoài ASCII.
  - toknix có thể hiệu chỉnh các tỉ lệ này từ transcript của bạn. Trên những transcript đã thử, phép fit
    chưa đạt ngưỡng chất lượng (sai số trung vị 21–25%, trong khi ngưỡng là 15%), nên tỉ lệ mặc định vẫn
    được dùng.
  - Vì vậy `gain` hiện số ước tính. Còn `report` và `waste` dùng số `usage` chính xác lấy từ transcript.
- **Codex và Gemini CLI chưa có hook.** Hai agent này chỉ nhận rules block, để agent tự gọi `toknix run` và
  `toknix outline`. Transcript của Gemini không được đọc.
- **Một số hành vi của Claude Code chưa được xác nhận trên session thật** ([docs/VERIFY.md](docs/VERIFY.md)):
  - `updatedInput` có được áp dụng khi hook không gửi permission decision không;
  - Claude Code có tự bỏ qua các lần Read lặp lại không;
  - khi Grep bị `head_limit` cắt bớt, agent có được báo không;
  - các rule deny do `init-project` thêm có khớp không.
- **Windows** được hỗ trợ qua Git Bash, giống cách Claude Code chạy trên Windows. CI chạy bộ test và script
  cài trên Windows, nhưng chưa ai dùng toknix với Claude Code trên Windows.
- **Outline:**
  - Với các ngôn ngữ ngoài Python, outline dựa trên regex. Code đã minify có thể làm nó nhầm.
  - Swift, C và C++ chưa được hỗ trợ; với các file đó, `outline` in 200 dòng đầu.
  - `map` tính cả tên xuất hiện trong chuỗi và comment là một lần dùng.
- **Tỉ lệ "Bash calls wrapped" trong `gain` là ước lượng.** Nó đếm các lệnh có chứa `toknix run`, hoặc có
  footer của toknix trong output.

## Phát triển

```bash
python -m unittest discover -s tests -v        # chỉ thư viện chuẩn, không cần pytest
python scripts/bench_codesearch.py 2000        # đo symbol/refs trên một repo sinh tự động 2.000 file
```

- [docs/PLAN.md](docs/PLAN.md): những gì đã làm và thứ tự làm.
- [docs/ALGORITHMS.md](docs/ALGORITHMS.md): mỗi phần hoạt động thế nào. Đây là tài liệu gốc: khi tài liệu
  và code không khớp, tài liệu được sửa trước.
- [docs/VERIFY.md](docs/VERIFY.md): các bước kiểm trên một session Claude Code thật.

## Giấy phép

MIT, xem [LICENSE](LICENSE).

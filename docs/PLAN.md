# tokopt — Kế hoạch thực thi

Đọc `docs/ALGORITHMS.md` trước. Kế hoạch này chỉ nói **làm theo thứ tự nào** và **khi nào thì coi là xong**.

Các mục đánh dấu **(N…)** đến từ thiết kế mở rộng đã duyệt ngày 2026-10-18: `docs/superpowers/specs/2026-10-18-token-optimizations-design.md`. Đọc mục tương ứng trong thiết kế nếu cần biết lý do.

## Mục tiêu sản phẩm

Đây là CLI Python (chỉ stdlib) cho người dùng Claude Code, Codex CLI và Gemini CLI. Nó làm ba việc:

1. Cắt token lãng phí: output lệnh, đọc file thừa, đọc cả file thay vì đọc đúng hàm.
2. Đo được phần đã cắt.
3. Chứng minh chất lượng không giảm, bằng bench A/B.

## Phạm vi v0.1

**Trong phạm vi**

- `tokopt run`, `recall`, `gain`
- Hook Claude Code: bash, read, session-start
- `outline`, `symbol`, `refs`, `map`
- `report`, `waste`, `context`, `snapshot`, `bench`, `compare`
- `statusline`
- `install`, `uninstall`, `init-project`, `doctor`
- Script `install.sh` và `install.ps1`
- CI trên GitHub Actions: Linux, macOS, Windows; Python 3.9 và 3.13

**Ngoài phạm vi**

- Hook native cho Codex và Gemini (v0.2)
- Nén prompt; nén output của model sau khi đã sinh (làm model *viết ít hơn* qua rules thì nằm trong phạm vi, xem P3-T5)
- Huấn luyện hoặc thay tokenizer
- Tự đổi cấu hình model/effort của người dùng
- Tự nới bộ lọc dựa trên tỉ lệ recall (v0.2; v0.1 chỉ đo)
- Định tuyến model
- Proxy API
- Đọc transcript Gemini

## Hiện trạng repo

| File | Trạng thái |
|---|---|
| `src/tokopt/config.py`, `textutil.py`, `store.py` | Bản nháp, cần review và viết test |
| `src/tokopt/filters.py`, `runner.py` | Bản nháp theo A2, chưa có test |
| `src/tokopt/hooks.py` | Bản nháp theo A3 và A4, chưa có test |
| `tests/fixtures/*.txt` | Output thật đã chụp: pytest, npm, pip, tsc, git |
| Các file khác | Chưa có |

## Quy tắc làm việc cho Claude Code

- **Một task = một session.** Mở session mới, đọc PLAN.md, chọn task đầu tiên chưa xong, làm xong rồi đánh dấu `[x]`, commit, `/clear`.
- Mỗi task phải có test kèm theo trong cùng commit. Lệnh test: `python -m unittest discover -s tests -v`.
- Không thêm dependency ngoài stdlib, kể cả trong test (dùng `unittest`, không dùng `pytest`).
- Commit message: `P<phase>-T<n>: <tóm tắt>`.

---

## P1 — Lõi lọc output

**P1-T1 `[x]` Hoàn thiện cấu trúc package**
- Có `pyproject.toml` (build backend: setuptools, khai báo `[project.scripts] tokopt = "tokopt.cli:main"`, `requires-python >= 3.9`).
- Có `src/tokopt/cli.py` dùng argparse, subcommand rỗng cho mọi lệnh ở phạm vi v0.1.
- `pip install -e .` thành công và `tokopt --version` in `0.1.0`.

**P1-T2 `[x]` Test cho `textutil` và `classify` (A2.2, A2.3)**
- Có ≥ 25 test case cho `program_of` và `classify`, bao phủ: `VAR=x cmd`, `npx jest`, `uv run pytest`, `python -m pytest`, `sudo docker build`, `npm run test:unit`, `yarn` không tham số, `git -C dir status` (đã chốt 2026-10-18: hỗ trợ, bỏ global option của git theo A2.3).
- `normalize` xử lý đúng ANSI, `\r` (progress bar) và `\r\n`.
- **(N1.2a)** `est_tokens` theo lớp ký tự (A1): test riêng cho chuỗi ASCII, hex/base64 ≥ 16 ký tự, tiếng Việt có dấu, và chuỗi trộn; tỉ lệ đọc từ `token_ratios` trong config.

**P1-T3 `[x]` Test bộ lọc trên fixture thật (A2.4 đến A2.6)**
- Mọi điều kiện trong bảng "Kiểm chứng A2" đều có test và pass.
- Có property test: với mọi fixture, mọi dòng khớp `ERROR_RE` trong input đều xuất hiện trong output (hoặc nằm trong dòng gộp deprecated).
- Bổ sung fixture tự sinh cho jest, go test, cargo test, eslint (viết tay theo format thật, ghi rõ nguồn trong `tests/fixtures/README.md`).
- Sửa 2 lỗi đã phát hiện trong bản nháp khi chạy thử trên fixture:
  - BUG-1: `f_install` không bỏ được dòng progress `━━━… 46.4/46.4 kB`. Regex dùng `\b` sau `━`, mà `━` không phải ký tự word nên `\b` không khớp.
  - BUG-2: `f_gitlog` trên `git_log.txt` (125 commit) trả về 30 dòng nhưng thiếu dòng `[tokopt] 95 older commits omitted`.
    - Kết quả (2026-10-19): không tái hiện. `f_gitlog` trả 31 dòng có dòng omitted; 30 dòng là do `collapse_similar` gộp 4 commit liền nhau thành 3 dòng. Đã thêm test hồi quy.
- Sửa cho khớp bất biến ERROR_RE tuyệt đối (A2.4): `budget_cut` giữ mọi dòng lỗi bất kể ngân sách; `f_lint` chỉ giới hạn 15 dòng không khớp ERROR_RE mỗi file. Property test kiểm tra dòng lỗi có mặt **nguyên văn**.

**P1-T3b `[x]` Bộ lọc nhận biết chi phí token (N1.2b–d; A2.5b, A2.5c, A2.6)**
- `budget_cut(lines, max_lines, max_tokens, head)` theo A2.6; config thêm `max_tokens` (3000), `max_line_chars` (800), `normalize_hashes`, `normalize_timestamps` (bật), `normalize_paths` (tắt).
- `normalize_costly` và `cut_long_lines` theo A2.5b, A2.5c; ngân sách token của `gitdiff` theo A2.4.
- Bảng `runs` thêm cột `error_overflow`.
- Mọi dòng mới trong bảng "Kiểm chứng A2" pass; fixture viết tay `build_longlines.txt`, `build_timestamps.txt` ghi nguồn trong `tests/fixtures/README.md`.

**P1-T4 `[x]` `tokopt run` và `recall` end-to-end**
- `tokopt run -- "exit 3"` trả exit code 3.
- `tokopt run -- "python -m pytest tests/fixtures_py"` (một suite có 2 test fail) in footer `tokopt recall <id>`; lệnh `tokopt recall <id>` in lại nguyên output gốc.
- `TOKOPT_RAW=1` cho ra output nguyên văn.
- **(N2.1)** `recall` ghi 1 dòng vào bảng `recalls(ts, tee_id)`; tee được lưu cả khi có biến đổi mất thông tin dù kích thước giảm < 10% (A2.1).
- Chạy được trên Windows qua Git Bash (CI xanh trên `windows-latest`).
  - Kết quả (2026-10-19): code đã xử lý Windows (tìm Git Bash, không lấy bash của WSL, output UTF-8 dạng byte, tee nhị phân), có unit test cho việc chọn shell. Chưa có CI nên tiêu chí `windows-latest` được kiểm ở P5-T2.

**P1-T5 `[x]` `tokopt gain`**
- Đọc bảng `runs` và `guard`, in tổng token tiết kiệm ước lượng theo kind và theo rule, cùng top 10 lệnh.
- Có `--json`.
- DB chưa tồn tại thì in "no data yet", không crash.
- **(N1.1)** Hiện hai số: tiết kiệm trực tiếp và tiết kiệm tính cả đọc lại (ước tính, R̄ = 20 kèm nhãn "dùng giá trị mặc định" cho tới khi P4-T2 xong).
- **(N2.1)** Theo kind: số lần cắt, số lần bị recall, tỉ lệ recall; cảnh báo khi tỉ lệ > 20% với ≥ 10 lần cắt. Đếm số lần `error_overflow = 1`.

## P2 — Hook Claude Code

**P2-T1 `[x]` Test `hook bash` (A3)**
- Test bảng: ≥ 20 lệnh, mỗi lệnh có kỳ vọng wrap hoặc không.
  - Bắt buộc có: `cd x && npm test` → không wrap; `npm test` → wrap; `git status` → wrap; `echo hi` → không wrap; lệnh đã có `tokopt run` → không wrap; `run_in_background` → không wrap; heredoc → không wrap.
- Lệnh được wrap phải chạy ra kết quả giống lệnh gốc: test thật với lệnh có dấu nháy đơn, nháy kép, `$VAR`, pipe, `&&`.
- JSON đầu vào lỗi → exit 0, không in gì.

**P2-T2 `[x]` Test `hook read` (A4)**
- R1: `node_modules/x/index.js` và `package-lock.json` bị deny; `src/app.ts` được cho qua.
- R2: file 2.000 dòng không có offset/limit bị deny, thông báo có lệnh `tokopt outline`; cùng file đó có `limit=100` thì được cho qua.
- R3: đọc lần 3 cùng range thì deny; sau khi `touch` file thì cho qua; sau `session-start` với `source=compact` thì cho qua.
- Mỗi lần deny ghi đúng 1 dòng vào bảng `guard`.
- **(N1.4)** Grep `output_mode="content"` không có `head_limit` → `updatedInput` thêm `head_limit=200`; có `head_limit` sẵn hoặc mode khác → không đụng; `grep_head_limit=0` → tắt.

**P2-T3 `[x]` `tokopt install` / `uninstall` / `doctor` cho Claude Code (A8)**
- Cài vào `settings.json` có sẵn nội dung của người dùng: giữ nguyên mọi key cũ, thêm đúng 3 hook và statusLine (chỉ khi chưa có).
- Chạy install 2 lần không tạo entry trùng.
- `uninstall` trả `settings.json` về đúng như trước khi cài (so sánh JSON).
- `doctor` liệt kê: hook nào đã cài, đường dẫn tokopt, phiên bản Python, số bản ghi trong `runs` và `guard` 7 ngày qua.
- Test dùng `HOME` tạm, không đụng `~` thật.

**P2-T4 `[ ]` Kiểm chứng trên Claude Code thật (thủ công, người dùng chạy)**
- Hướng dẫn trong `docs/VERIFY.md`: cài tokopt, mở một repo có test, yêu cầu agent "chạy test và sửa test fail".
- Đạt khi:
  - `tokopt gain` có ≥ 1 bản ghi kind ≠ passthrough.
  - Transcript cho thấy lệnh đã bị viết lại thành `tokopt run -- …`.
  - Agent vẫn sửa được test.
- Ghi kết quả vào `docs/VERIFY.md` và chốt hai `[ASSUMPTION]` của A3 và A4 (updatedInput khi không có permissionDecision; Claude Code có tự dedupe Read không).
- **(N1.4)** Chốt thêm: `updatedInput` cho Grep có được áp dụng không, và khi `head_limit` cắt bớt thì agent có được báo không. Không được báo → đổi mặc định `grep_head_limit` thành 0.
- Chốt thêm (từ P2-T3): matcher `compact|clear` của SessionStart có kích hoạt hook không (sau `/compact`, đọc lại file đã đọc 2 lần phải được cho qua); và hook chạy được khi Claude Code khởi động từ môi trường không có `PYTHONPATH` (`tokopt doctor` không báo `does not run`).

## P3 — Truy xuất code

**P3-T1 `[x]` `extract` cho Python + `tokopt outline` (A5.1, A5.2)**
- Bao gồm class lồng, async def, decorator.
- Khớp 100% với `expected_outline.json`.

**P3-T2 `[x]` `extract` cho JS/TS, Go, Java, Rust, PHP, Ruby, Dart, Kotlin, C#**
- Mỗi ngôn ngữ có một file mẫu ≥ 80 dòng (có class, method, hàm lồng, chuỗi chứa `{`).
- Recall symbol ≥ 90%, sai `end` tối đa 1 dòng.

**P3-T3 `[x]` `tokopt symbol` và `tokopt refs`**
- Trên chính repo tokopt: `tokopt symbol budget_cut` in đúng thân hàm kèm số dòng.
- `tokopt refs classify` liệt kê đủ chỗ gọi, không lẫn dòng định nghĩa.
- Repo 2.000 file: chạy < 2 giây (benchmark bằng script trong `scripts/`).

**P3-T4 `[x]` `tokopt map`**
- Output không vượt `--budget` quá 5%.
- File có nhiều symbol được tham chiếu nhiều đứng đầu (test với một repo fixture nhỏ có thứ hạng biết trước).

**P3-T5 `[x]` Rules block v1**
- Viết `templates/rules.md` (≤ 350 token theo A1), nội dung:
  - dùng `tokopt outline`, `symbol`, `refs` trước khi Read cả file;
  - khi output bị cắt thì dùng `tokopt recall`;
  - chỉ trả về diff, không tóm tắt lại;
  - giữ đầy đủ suy luận khi debug, bảo mật, migration;
  - một task một session.
  - **(N1.5b)** viết ít: không tóm tắt lại thay đổi vừa làm, không dán lại code đã có trong diff, sửa file có sẵn bằng Edit thay vì Write, trả lời ngắn khi không được hỏi giải thích.
- Thêm ít nhất 1 biến thể ngắn gọn hơn trong `templates/variants/`, mỗi bản ≤ 350 token. Mặc định cài v1.
- `install` chèn block vào CLAUDE.md, AGENTS.md, GEMINI.md đúng theo marker.
  - Kết quả (2026-10-19): template nằm trong package ở `src/tokopt/templates/` (khai báo `package-data`), vì `templates/` ở gốc repo không có trong gói khi cài bằng pip/uv. v1: 282 token; `variants/terse.md`: 107 token.

## P4 — Đo lường

**P4-T1 `[x]` Parser transcript Claude Code và Codex (A6.1)**
- Fixture transcript giả lập có chunk trùng lặp: tổng usage phải đếm mỗi request 1 lần.
- Dòng JSON hỏng bị bỏ qua, không crash.
- **(N2.2)** Hiệu chỉnh tỉ lệ ký tự/token theo A6.4: fixture transcript tổng hợp với tỉ lệ biết trước → hồi quy tìm lại đúng tỉ lệ (sai ≤ 5%); < 200 mẫu hoặc sai số trung vị ≥ 15% → không ghi `calibration.json`. Bộ lọc không đọc file này.

**P4-T2 `[x]` `report`, `waste`, `context`, `snapshot` (A6.2)**
- Mỗi lệnh có `--json`.
- `waste` in đủ 7 nhóm, mỗi nhóm kèm dòng gợi ý sửa; nhóm (1), (2) xếp theo `reread_cost`.
- **(N1.1)** R̄ tính từ transcript, thay giá trị mặc định 20 trong `gain`. `gain` thêm tỉ lệ lệnh bị bọc trên tổng lệnh Bash (A6.2; cần transcript nên không làm ở P1-T5).
- **(N1.3)** Cache vỡ theo A6.5: fixture có đủ 4 loại nguyên nhân, phân loại đúng; lần vỡ do compact không tính vào ETU mất. `context` liệt kê MCP server và skill kèm số lần gọi, gợi ý tắt cái 0 lần.
- **(N1.5a)** `report` có tỉ trọng output; phân rã output theo A6.6, kể cả trường hợp thinking bị ẩn.
- Request của subagent không bị tính vào số liệu theo phiên.

**P4-T3 `[x]` `bench` và `compare` (A6.3)**
- Test với agent giả (script trong `tests/fake_agents/` in JSON giống `claude -p`):
  - verdict ACCEPT khi B rẻ hơn 30% và pass như nhau;
  - REJECT khi pass rate giảm;
  - INCONCLUSIVE khi chênh lệch nằm trong nhiễu.
- Worktree luôn được xoá kể cả khi agent bị timeout.
- **(N1.5c)** `bench --rules <file>` chèn block vào CLAUDE.md của worktree; cảnh báo khi CLAUDE.md global đã có block tokopt. `compare` có cột median output tokens và verdict output theo A6.3 (agent giả: biến thể giảm output 30%, pass như nhau → thắng; giảm 5% → không thắng).

**P4-T4 `[x]` `tokopt statusline` (A7)**
- Transcript 20 MB: lần chạy đầu < 1 giây, các lần sau < 150 ms (đọc tăng dần).
- Ngưỡng màu đúng ở 60% và 80%.

## P5 — Phân phối

**P5-T1 `[x]` `install.sh` và `install.ps1` (A8)**
- Một lệnh `curl -fsSL …/install.sh | sh` cài xong trên Ubuntu 24.04 (có PEP 668) và macOS.
- `irm …/install.ps1 | iex` cài xong trên Windows 11.
- Script có `--uninstall` và `--dry-run`.
- Kiểm tra bằng shellcheck và PSScriptAnalyzer trong CI.
  - Kết quả (2026-10-19): macOS cài thật xong cả hai nhánh (uv, venv) với `--from`; shellcheck sạch. Lệnh `curl … | sh` thật chỉ chạy được sau khi code được push lên `lephus/toknix`. Ubuntu 24.04 (PEP 668), Windows 11 và PSScriptAnalyzer được kiểm trong CI (P5-T2).

**P5-T2 `[x]` CI GitHub Actions**
- Ma trận ubuntu, macos, windows × Python 3.9, 3.13.
- Chạy unittest, `install.sh --dry-run`, shellcheck.
  - Kết quả (2026-10-19): `.github/workflows/ci.yml`, sạch actionlint.
    - Python được cài bằng `uv`, vì `setup-python` có thể không có 3.9 cho macOS ARM.
    - Thêm job PSScriptAnalyzer, benchmark `symbol`/`refs`, và job cài thật bằng script: Ubuntu 24.04 (PEP 668, nhánh venv), macOS, Windows. Job này đóng phần còn lại của P5-T1.
    - **Chưa chạy lần nào**, vì repo chưa được push lên `lephus/toknix`. Test suite chưa từng chạy trên Windows, nên lần chạy đầu có thể lộ lỗi riêng của Windows.

**P5-T3 `[x]` README tiếng Việt và tiếng Anh, `docs/VERIFY.md`, CHANGELOG, LICENSE MIT**
- README có:
  - cài 1 lệnh;
  - bảng lệnh;
  - cách đọc `gain` và `compare`;
  - phần "Giới hạn đã biết" (permission rule với lệnh bị viết lại, sai số ước lượng chars/4, Codex/Gemini chưa có hook);
  - cảnh báo về `hook_permission=allow`.

## P6 — Nghiệm thu bằng số liệu thật (người dùng chạy)

**P6-T1 `[ ]` Baseline và A/B**
1. Chọn 5–10 task thật có lệnh check.
2. `tokopt bench --label baseline` khi chưa cài hook.
3. Cài hook, `tokopt bench --label v01`.
4. `tokopt compare baseline v01`.
5. **(N1.5c)** Chạy bench với từng biến thể rules so với v1; nếu có biến thể thắng thì đổi mặc định cài sang biến thể đó.

Đạt khi:
- Verdict là ACCEPT.
- Pass rate không giảm.
- `tokopt waste` sau 1 tuần cho thấy nhóm (2) "output ≥ 5k token" giảm ≥ 50% so với snapshot `before`. `[ASSUMPTION]`: ngưỡng 50% là mục tiêu, chưa có dữ liệu nền.

---

## Rủi ro đã biết

| ID | Rủi ro | Xử lý |
|---|---|---|
| RK-1 | Claude Code bỏ qua `updatedInput` khi không có `permissionDecision` | P2-T4 kiểm chứng; phương án dự phòng là rules để agent tự gõ `tokopt run --` |
| RK-2 | Lọc mất dòng quan trọng nên agent đoán sai | Bất biến ERROR_RE (có property test), footer `recall`, `TOKOPT_RAW=1`, verdict của bench |
| RK-3 | Read guard làm agent kẹt vòng lặp | Mọi lần deny đều nêu cách đọc thay thế; R3 cho phép 2 lần; reset khi compaction; mọi rule tắt được trong config |
| RK-4 | Hook làm chậm mỗi lượt gọi | Đo thời gian khởi động `tokopt hook` (mục tiêu < 80 ms); import lười, không import module đo lường trong hook |
| RK-5 | Schema transcript thay đổi | Parser phòng thủ; test với fixture; `doctor` cảnh báo khi đọc được 0 usage |

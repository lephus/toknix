# tokopt — Kế hoạch thực thi

Đọc `docs/ALGORITHMS.md` trước. Kế hoạch này chỉ nói **làm theo thứ tự nào** và **khi nào thì coi là xong**.

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
- Nén prompt hoặc output của model
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

**P1-T1 `[ ]` Hoàn thiện cấu trúc package**
- Có `pyproject.toml` (build backend: setuptools, khai báo `[project.scripts] tokopt = "tokopt.cli:main"`, `requires-python >= 3.9`).
- Có `src/tokopt/cli.py` dùng argparse, subcommand rỗng cho mọi lệnh ở phạm vi v0.1.
- `pip install -e .` thành công và `tokopt --version` in `0.1.0`.

**P1-T2 `[ ]` Test cho `textutil` và `classify` (A2.2, A2.3)**
- Có ≥ 25 test case cho `program_of` và `classify`, bao phủ: `VAR=x cmd`, `npx jest`, `uv run pytest`, `python -m pytest`, `sudo docker build`, `npm run test:unit`, `yarn` không tham số, `git -C dir status` (`[TBD]`: hiện đang ra generic, quyết định có hỗ trợ không).
- `normalize` xử lý đúng ANSI, `\r` (progress bar) và `\r\n`.

**P1-T3 `[ ]` Test bộ lọc trên fixture thật (A2.4 đến A2.6)**
- Mọi điều kiện trong bảng "Kiểm chứng A2" đều có test và pass.
- Có property test: với mọi fixture, mọi dòng khớp `ERROR_RE` trong input đều xuất hiện trong output (hoặc nằm trong dòng gộp deprecated).
- Bổ sung fixture tự sinh cho jest, go test, cargo test, eslint (viết tay theo format thật, ghi rõ nguồn trong `tests/fixtures/README.md`).

**P1-T4 `[ ]` `tokopt run` và `recall` end-to-end**
- `tokopt run -- "exit 3"` trả exit code 3.
- `tokopt run -- "python -m pytest tests/fixtures_py"` (một suite có 2 test fail) in footer `tokopt recall <id>`; lệnh `tokopt recall <id>` in lại nguyên output gốc.
- `TOKOPT_RAW=1` cho ra output nguyên văn.
- Chạy được trên Windows qua Git Bash (CI xanh trên `windows-latest`).

**P1-T5 `[ ]` `tokopt gain`**
- Đọc bảng `runs` và `guard`, in tổng token tiết kiệm ước lượng theo kind và theo rule, cùng top 10 lệnh.
- Có `--json`.
- DB chưa tồn tại thì in "no data yet", không crash.

## P2 — Hook Claude Code

**P2-T1 `[ ]` Test `hook bash` (A3)**
- Test bảng: ≥ 20 lệnh, mỗi lệnh có kỳ vọng wrap hoặc không.
  - Bắt buộc có: `cd x && npm test` → không wrap; `npm test` → wrap; `git status` → wrap; `echo hi` → không wrap; lệnh đã có `tokopt run` → không wrap; `run_in_background` → không wrap; heredoc → không wrap.
- Lệnh được wrap phải chạy ra kết quả giống lệnh gốc: test thật với lệnh có dấu nháy đơn, nháy kép, `$VAR`, pipe, `&&`.
- JSON đầu vào lỗi → exit 0, không in gì.

**P2-T2 `[ ]` Test `hook read` (A4)**
- R1: `node_modules/x/index.js` và `package-lock.json` bị deny; `src/app.ts` được cho qua.
- R2: file 2.000 dòng không có offset/limit bị deny, thông báo có lệnh `tokopt outline`; cùng file đó có `limit=100` thì được cho qua.
- R3: đọc lần 3 cùng range thì deny; sau khi `touch` file thì cho qua; sau `session-start` với `source=compact` thì cho qua.
- Mỗi lần deny ghi đúng 1 dòng vào bảng `guard`.

**P2-T3 `[ ]` `tokopt install` / `uninstall` / `doctor` cho Claude Code (A8)**
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

## P3 — Truy xuất code

**P3-T1 `[ ]` `extract` cho Python + `tokopt outline` (A5.1, A5.2)**
- Bao gồm class lồng, async def, decorator.
- Khớp 100% với `expected_outline.json`.

**P3-T2 `[ ]` `extract` cho JS/TS, Go, Java, Rust, PHP, Ruby, Dart, Kotlin, C#**
- Mỗi ngôn ngữ có một file mẫu ≥ 80 dòng (có class, method, hàm lồng, chuỗi chứa `{`).
- Recall symbol ≥ 90%, sai `end` tối đa 1 dòng.

**P3-T3 `[ ]` `tokopt symbol` và `tokopt refs`**
- Trên chính repo tokopt: `tokopt symbol budget_cut` in đúng thân hàm kèm số dòng.
- `tokopt refs classify` liệt kê đủ chỗ gọi, không lẫn dòng định nghĩa.
- Repo 2.000 file: chạy < 2 giây (benchmark bằng script trong `scripts/`).

**P3-T4 `[ ]` `tokopt map`**
- Output không vượt `--budget` quá 5%.
- File có nhiều symbol được tham chiếu nhiều đứng đầu (test với một repo fixture nhỏ có thứ hạng biết trước).

**P3-T5 `[ ]` Rules block v1**
- Viết `templates/rules.md` (≤ 350 token theo A1), nội dung:
  - dùng `tokopt outline`, `symbol`, `refs` trước khi Read cả file;
  - khi output bị cắt thì dùng `tokopt recall`;
  - chỉ trả về diff, không tóm tắt lại;
  - giữ đầy đủ suy luận khi debug, bảo mật, migration;
  - một task một session.
- `install` chèn block vào CLAUDE.md, AGENTS.md, GEMINI.md đúng theo marker.

## P4 — Đo lường

**P4-T1 `[ ]` Parser transcript Claude Code và Codex (A6.1)**
- Fixture transcript giả lập có chunk trùng lặp: tổng usage phải đếm mỗi request 1 lần.
- Dòng JSON hỏng bị bỏ qua, không crash.

**P4-T2 `[ ]` `report`, `waste`, `context`, `snapshot` (A6.2)**
- Mỗi lệnh có `--json`.
- `waste` in đủ 5 nhóm, mỗi nhóm kèm dòng gợi ý sửa.

**P4-T3 `[ ]` `bench` và `compare` (A6.3)**
- Test với agent giả (script trong `tests/fake_agents/` in JSON giống `claude -p`):
  - verdict ACCEPT khi B rẻ hơn 30% và pass như nhau;
  - REJECT khi pass rate giảm;
  - INCONCLUSIVE khi chênh lệch nằm trong nhiễu.
- Worktree luôn được xoá kể cả khi agent bị timeout.

**P4-T4 `[ ]` `tokopt statusline` (A7)**
- Transcript 20 MB: lần chạy đầu < 1 giây, các lần sau < 150 ms (đọc tăng dần).
- Ngưỡng màu đúng ở 60% và 80%.

## P5 — Phân phối

**P5-T1 `[ ]` `install.sh` và `install.ps1` (A8)**
- Một lệnh `curl -fsSL …/install.sh | sh` cài xong trên Ubuntu 24.04 (có PEP 668) và macOS.
- `irm …/install.ps1 | iex` cài xong trên Windows 11.
- Script có `--uninstall` và `--dry-run`.
- Kiểm tra bằng shellcheck và PSScriptAnalyzer trong CI.

**P5-T2 `[ ]` CI GitHub Actions**
- Ma trận ubuntu, macos, windows × Python 3.9, 3.13.
- Chạy unittest, `install.sh --dry-run`, shellcheck.

**P5-T3 `[ ]` README tiếng Việt và tiếng Anh, `docs/VERIFY.md`, CHANGELOG, LICENSE MIT**
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

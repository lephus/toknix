# toknix

CLI Python tối ưu token cho các coding agent (Claude Code, Codex CLI, Gemini CLI).

- **Kế hoạch:** `docs/PLAN.md`
- **Thuật toán:** `docs/ALGORITHMS.md` (nguồn sự thật duy nhất, nếu mâu thuẫn thì sửa tài liệu trước rồi mới sửa code)
- **Thiết kế mở rộng (lý do của các mục N…):** `docs/superpowers/specs/2026-10-18-token-optimizations-design.md`

## Cách làm việc

- Mỗi session làm đúng 1 task trong `docs/PLAN.md`: task đầu tiên còn `[ ]`, trừ khi người dùng chỉ định task khác.
  1. Đọc phần thuật toán liên quan (A1–A8) trong `docs/ALGORITHMS.md`, không cần đọc cả file.
  2. Viết test trước, chạy cho fail, rồi implement.
  3. Xong thì đổi `[ ]` thành `[x]` trong PLAN.md và commit `P<n>-T<m>: …`.
- Test: `python -m unittest discover -s tests -v`. Mọi test phải xanh trước khi commit.
- Gặp `[TBD]`, hoặc `[ASSUMPTION]` về hành vi của agent/Claude Code, thì dừng lại và hỏi người dùng, không tự chọn. `[ASSUMPTION]` gắn cho một con số là giá trị mặc định ban đầu, được chỉnh theo fixture (xem phần quy ước đầu `docs/ALGORITHMS.md`).

## Ràng buộc

- Python ≥ 3.9, **chỉ stdlib**, cả trong test. Không thêm dependency.
- Hook không bao giờ được làm hỏng agent: mọi exception đều thành exit 0, không in gì (bật `TOKNIX_DEBUG=1` để in ra stderr).
- `toknix run` phải giữ nguyên exit code của lệnh gốc.
- Không bộ lọc nào được bỏ hay sửa dòng khớp `ERROR_RE`, kể cả khi vượt ngân sách token (A2.4).
- Không ghi ra ngoài `~/.toknix/`, trừ khi người dùng chạy `install` hoặc `init-project`. Test phải dùng `HOME` / `TOKNIX_HOME` tạm.
- Code đa nền tảng: dùng `pathlib`; không giả định `/`; chạy được trên Windows qua Git Bash.

## Cấu trúc

```
src/toknix/      cli.py, config.py, textutil.py, store.py, filters.py, runner.py, hooks.py,
                 outline.py, langs.py (regex-language extract), codesearch.py (symbol, refs),
                 transcripts.py, measure.py (gain, calibration), reports.py (report, waste, context,
                 snapshot), bench.py, statusline.py, installer.py
tests/           test_*.py, fixtures/ (output thật, không sửa tay), fake_agents/
scripts/         bench_codesearch.py (đo symbol/refs trên repo 2.000 file)
src/toknix/templates/  rules.md, variants/ (rules block cài vào CLAUDE.md / AGENTS.md / GEMINI.md; nằm trong package để pip/uv mang theo)
docs/            PLAN.md, ALGORITHMS.md, VERIFY.md
install.sh, install.ps1
```

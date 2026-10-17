# Fixtures

Output thật, chụp ngày 2026-10-17 trên Linux (Python 3.13, Node 22). Không sửa tay.

| File | Lệnh |
|---|---|
| pytest_fail.txt | `python -m pytest -v` — 40 test pass, 2 test fail (test_bad.py) |
| pytest_pass.txt | `python -m pytest -q test_m1.py test_m2.py` |
| npm_install.txt | `npm install express@4.19.2 typescript@5.4.5 eslint@8.57.0 --no-fund` (không có TTY) |
| tsc_errors.txt | `npx tsc --noEmit --strict a.ts` với 3 lỗi kiểu |
| pip_install.txt | `pip install --no-cache-dir --target pipt requests==2.31.0 rich==13.7.1` |
| git_log.txt | `git log` của psf/requests (cắt còn 1.500 dòng đầu) |
| git_status.txt | `git status` sau khi sửa 1 file và thêm 3 file mới |
| git_diff_small.txt | `git diff` 1 file |

Fixture tự viết tay (jest, go test, cargo test, eslint) thêm ở P1-T3 phải ghi nguồn format ở đây.

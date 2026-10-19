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

## Fixture viết tay (P1-T3)

Không phải output chụp; viết tay theo đúng format của công cụ ở phiên bản ghi bên dưới, để có mẫu cho các kind chưa có output thật.

| File | Lệnh giả định | Nguồn format |
|---|---|---|
| jest_fail.txt | `npx jest --verbose` — 3 suite, 1 test fail | Default reporter của Jest 29 (dòng `PASS`/`FAIL`, `✓`/`✕`, khối `●` có code frame, `Test Suites:`/`Tests:`) |
| gotest_fail.txt | `go test -v ./...` — 1 assert fail, 1 panic, 1 package ok | `go test -v` của Go 1.22 (`=== RUN`, `--- PASS:`/`--- FAIL:`, stack trace của panic, dòng `FAIL\t<pkg>` / `ok  \t<pkg>`) |
| cargotest_fail.txt | `cargo test` — 8 test, 2 fail | libtest của Rust 1.79 (`Compiling`, `test x ... ok`, khối `failures:`, `test result:`) |
| eslint.txt | `npx eslint src` — 3 error, 2 warning ở 2 file | Formatter `stylish` (mặc định) của ESLint 8 |
| build_longlines.txt (P1-T3b) | `make build` — esbuild bundle in metafile JSON minified trên 1 dòng (3.442 ký tự), make echo lệnh esbuild dài (863 ký tự), rồi `tsc --noEmit` báo 1 lỗi TS2322 với kiểu dài (916 ký tự, khớp ERROR_RE) kèm dòng giải thích dài (923 ký tự); exit 2 | Make 4 (echo lệnh, `make: *** [Makefile:N: target] Error 2`), esbuild 0.21 (bảng kích thước output, `⚡ Done in`), tsc 5 (`file(line,col): error TSxxxx:`). Sinh bằng script một lần, dữ liệu cố định |
| build_timestamps.txt (P1-T3b) | `./gradlew bootRun` — Spring Boot 3.3 khởi động, 1 request lỗi kèm stack trace, vài request, shutdown khi Ctrl-C | Gradle 8 (`> Task …`, `BUILD FAILED in`), log mặc định của Spring Boot 3.3 (`<ISO 8601 có offset>  LEVEL PID --- [app] [thread] logger : msg`), `spring.jpa.show-sql` in dòng `Hibernate: …` không có timestamp. Ba chuỗi timestamp dài 21, 5 và 3 dòng |

## Suite mẫu `fixtures_py/` (P1-T4)

`test_shop.py`: 40 test pass, 2 test fail (1 assert, 1 exception), viết bằng `unittest.TestCase` để chạy được bằng cả `python -m pytest tests/fixtures_py` và `python -m unittest discover -s tests/fixtures_py`. Không có `__init__.py` nên suite của tokopt không chạy nó. `pytest.ini` bật `-v` và tắt cacheprovider để không ghi `.pytest_cache` vào repo. Test end-to-end với pytest tự bỏ qua khi máy không có pytest (tokopt không phụ thuộc pytest).

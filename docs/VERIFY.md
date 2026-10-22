# Kiểm chứng toknix trên Claude Code thật (P2-T4)

Test tự động đã chứng minh hook trả đúng JSON. Còn câu hỏi Claude Code **làm gì** với JSON đó thì chỉ chạy thật mới biết. Tài liệu này hướng dẫn chạy một phiên Claude Code thật trên một repo mẫu, rồi ghi kết quả vào mục [Kết quả](#kết-quả) ở cuối.

Mất khoảng 30 phút và tốn một ít token Claude Code thật (vài lượt hội thoại ngắn).

## Cần chốt những gì

| # | Câu hỏi | Nguồn | Nếu câu trả lời là "không" |
|---|---|---|---|
| A | Claude Code có áp dụng `updatedInput` của hook Bash khi hook **không** gửi `permissionDecision`? | A3 `[ASSUMPTION]` | `[TBD]` của A3: chọn giữa (a) `hook_permission: "ask"` hoặc (b) chỉ cài rules để agent tự gõ `toknix run --` |
| B | Permission rule như `Bash(.venv/bin/python -m pytest:*)` còn khớp sau khi lệnh bị viết lại? | A3 `[ASSUMPTION]` | Ghi vào phần giới hạn của README |
| C | Claude Code có tự trả stub kiểu "file unchanged" khi Read lặp lại không? | A4 `[ASSUMPTION]` | Có stub → R3 thừa → đổi mặc định `guard_repeat_reads` thành 0 |
| D | `updatedInput` của Grep (thêm `head_limit`) có được áp dụng? Khi bị cắt, agent có được báo không? | N1.4 `[ASSUMPTION]` | Không được báo → đổi mặc định `grep_head_limit` thành 0 |
| E | Matcher `compact\|clear` của SessionStart có chạy hook sau `/compact`? | P2-T3 | Sửa matcher trong `installer.py` |
| F | Hook chạy được khi Claude Code khởi động từ môi trường bình thường (không có `PYTHONPATH`)? | P2-T3 | Sửa cách cài (P5-T1) |
| I | Hook UserPromptSubmit trả `systemMessage`: người dùng có thấy dòng nhắc, và model có **không** thấy nó? | A7 `[ASSUMPTION]` (P7-T2) | Model thấy → đổi sang chỉ statusline; không ai thấy → sửa cách trả |
| H | Rule `Read(./**/node_modules/**)` do `toknix init-project` thêm có chặn Read (và Grep) trong `node_modules/` không? | A8 `[ASSUMPTION]` | Đổi cú pháp rule trong `installer.init_project` |

Ba điều kiện đạt của P2-T4 nằm trong bước 4 (check A và G):

- `toknix gain` có ≥ 1 bản ghi kind ≠ passthrough;
- transcript cho thấy lệnh đã bị viết lại thành `toknix run -- …`;
- agent vẫn sửa được test.

## 0. Chuẩn bị

Cần: macOS hoặc Linux (Windows chưa hỗ trợ, xem P5-T2), Python ≥ 3.9, git, Claude Code đã đăng nhập.

Ghi lại để điền vào bảng kết quả:

```bash
claude --version
python3 --version
git -C <đường-dẫn-repo-toknix> log --oneline -1
```

`toknix install` tự backup `~/.claude/settings.json` vào `~/.toknix/backups/<thời điểm>/` trước khi sửa. Nếu muốn chắc thêm thì tự copy một bản:

```bash
cp ~/.claude/settings.json ~/settings.json.before-toknix 2>/dev/null || true
```

## 1. Cài toknix lên PATH

Hook chạy trong môi trường của Claude Code, nên `toknix` phải gọi được mà không cần `PYTHONPATH`. Dùng script cài, chạy ở thư mục repo toknix:

```bash
sh install.sh --from . --no-hooks   # trước khi code có trên GitHub
# sau khi đã push lên lephus/toknix:
# curl -fsSL https://raw.githubusercontent.com/lephus/toknix/main/install.sh | sh -s -- --no-hooks
```

Script dùng `uv tool install` nếu có uv; nếu không thì tạo venv `~/.toknix/venv` và link `~/.local/bin/toknix`. `--no-hooks` để bước 2 tự chạy `toknix install`. Mở **terminal mới** rồi kiểm tra:

```bash
command -v toknix        # phải ra một đường dẫn, ví dụ ~/.local/bin/toknix
toknix --version         # toknix 0.3.0
```

## 2. Cài hook và kiểm tra (check F)

```bash
toknix install
env -u PYTHONPATH toknix doctor; echo "exit $?"
```

Đạt khi `doctor` exit 0, có 3 dòng `installed:` và không dòng nào có `⚠`. Nếu thấy `does not run` hoặc `not found` thì dừng lại và gửi output cho người phát triển.

Nếu `doctor` báo `statusLine yours (kept)`: toknix không đụng statusLine của bạn. Còn nếu toknix đã thêm statusLine thì lúc này thanh trạng thái sẽ trống; đó là bình thường, statusline thật có ở P4-T4.

## 3. Tạo repo mẫu

Repo mẫu nằm ở `~/toknix-verify/shop`. Nó có 42 test, trong đó 2 test fail do 2 bug thật trong `shop.py`. Ngoài ra có một file 2.400 dòng `big/catalog.py` (600 hàm) để thử R2 và Grep. Script dưới đây cũng tạo `~/toknix-verify/show.py` để xem transcript.

```bash
mkdir -p ~/toknix-verify/shop && cd ~/toknix-verify/shop

cat > shop.py <<'EOF'
def cart_total(prices, discount=0):
    """Sum of prices minus a fixed discount, never below 0."""
    total = sum(prices) - discount * 2
    return max(total, 0)


def parse_qty(text):
    """'3' -> 3; an empty or blank string -> 0."""
    return int(text)
EOF

mkdir -p tests && cat > tests/test_shop.py <<'EOF'
import unittest

from shop import cart_total, parse_qty


class TestCart(unittest.TestCase):
    def test_fixed_discount(self):
        self.assertEqual(cart_total([40, 60], discount=10), 90)

    def test_never_negative(self):
        self.assertEqual(cart_total([5], discount=10), 0)


class TestQty(unittest.TestCase):
    def test_blank_is_zero(self):
        self.assertEqual(parse_qty(""), 0)

    def test_number(self):
        self.assertEqual(parse_qty("3"), 3)


class TestMany(unittest.TestCase):
    pass


for _i in range(38):
    setattr(TestMany, f"test_total_{_i:02d}", lambda self, i=_i: self.assertEqual(cart_total([i, 1]), i + 1))
EOF

printf '[pytest]\naddopts = -v\npythonpath = .\n' > pytest.ini

mkdir -p big && python3 - <<'EOF'
with open("big/catalog.py", "w") as f:
    f.write('"""Generated product catalog (do not edit)."""\n\n')
    for i in range(600):
        f.write(f"def item_{i:03d}():\n    return {{'sku': 'SKU-{i:05d}', 'price': {i * 7 % 500}}}\n\n\n")
EOF

python3 -m venv .venv && .venv/bin/python -m pip install -q pytest
printf '.venv/\n__pycache__/\n' > .gitignore
git init -q && git add . && git commit -qm "shop with two bugs"

cat > ~/toknix-verify/show.py <<'EOF'
"""Print the tool calls and tool results of the newest Claude Code session of a project."""
import glob, json, os, sys

pattern = os.path.expanduser("~/.claude/projects/*toknix-verify-shop*/*.jsonl")
files = sorted(glob.glob(pattern), key=os.path.getmtime)
if not files:
    sys.exit(f"no transcript matches {pattern}")
print(f"# {files[-1]}")
width = int(sys.argv[1]) if len(sys.argv) > 1 else 600
for raw in open(files[-1], encoding="utf-8"):
    try:
        content = (json.loads(raw).get("message") or {}).get("content")
    except ValueError:
        continue
    for c in content if isinstance(content, list) else []:
        if c.get("type") == "tool_use":
            print(f"\n>> {c.get('name')}: {json.dumps(c.get('input'), ensure_ascii=False)[:width]}")
        elif c.get("type") == "tool_result":
            body = c.get("content")
            if isinstance(body, list):
                body = "\n".join(x.get("text", "") for x in body if isinstance(x, dict))
            print(f"<< {str(body)[:width]}")
EOF

.venv/bin/python -m pytest 2>&1 | tail -3   # phải thấy: 2 failed, 40 passed
```

## 4. Hook Bash (check A, B, G)

Trong `~/toknix-verify/shop`, chạy `claude` (chế độ permission mặc định). Gõ `/hooks` và kiểm tra có 5 hook của toknix: PreToolUse `Bash`, PreToolUse `Read|Grep|Glob`, SessionStart `compact|clear`, UserPromptSubmit, PreToolUse `Agent|Task`. Không thấy thì thoát Claude Code, mở lại rồi kiểm tra lại.

Gửi prompt:

> Chạy test bằng đúng lệnh `.venv/bin/python -m pytest`, rồi sửa code trong `shop.py` để mọi test pass. Không sửa file test. Sau khi sửa, chạy lại đúng lệnh đó.

Nếu Claude Code hỏi quyền chạy lệnh thì cho phép. Ghi lại **lệnh hiện trong hộp hỏi quyền** là lệnh gốc hay `toknix run -- …`.

Khi agent xong, mở terminal khác:

```bash
toknix gain --days 1
python3 ~/toknix-verify/show.py | grep -n -B1 -A3 "toknix" | head -60
```

**Check A** đạt khi cả hai điều sau đúng:

- `toknix gain` có dòng `pytest` với `runs ≥ 1`;
- trong output của `show.py`, kết quả của lệnh pytest có footer `[toknix: N → M lines; full output: toknix recall <id>; exit 1]`.

Nếu `gain` in `no data yet` và không có footer thì `updatedInput` **không** được áp dụng. Khi đó ghi lại và dừng: các bước sau phụ thuộc vào check này.

Ghi thêm: dòng `>> Bash:` trong transcript chứa lệnh gốc hay lệnh đã viết lại. Thông tin này giúp P4 (đọc transcript) biết nên tìm lệnh ở đâu.

**Check G** đạt khi lần chạy cuối của agent ra `42 passed` và `git diff` chỉ đổi `shop.py`.

**Check B** (permission rule sau khi viết lại):

1. Thoát Claude Code, rồi tạo `.claude/settings.local.json` trong repo mẫu:
   ```json
   {"permissions": {"allow": ["Bash(.venv/bin/python -m pytest:*)"]}}
   ```
2. Hoàn tác phần sửa: `git checkout shop.py`.
3. Mở lại `claude` và gửi: *"Chạy `.venv/bin/python -m pytest` và cho biết số test fail."*
4. Ghi lại: Claude Code có hỏi quyền không. Có hỏi nghĩa là rule không còn khớp sau khi lệnh bị viết lại.

Xong thì xoá `.claude/settings.local.json`.

## 5. Read guard và Read lặp lại (check C, E)

**Check C** — Claude Code có tự chặn Read lặp lại không. Tạm tắt R3 của toknix để nhìn thấy hành vi gốc của Claude Code:

```bash
mkdir -p ~/.toknix && echo '{"guard_repeat_reads": 0}' > ~/.toknix/config.json
```

Trong một phiên `claude` mới, gửi:

> Để kiểm tra một hook: dùng tool Read đọc `shop.py`, rồi dùng tool Read đọc lại đúng file đó thêm một lần nữa, không đổi gì. Sau đó chỉ trả lời "xong".

Rồi chạy `python3 ~/toknix-verify/show.py 300` và xem kết quả `<<` của lần Read thứ hai. Có hai khả năng:

- nội dung file đầy đủ như lần một → Claude Code **không** tự chặn, R3 có ích;
- một thông báo ngắn kiểu "file unchanged since last read" → Claude Code tự chặn, R3 thừa. Chép nguyên văn thông báo đó.

Bật lại R3:

```bash
rm ~/.toknix/config.json
```

**Check E** — R3 và việc reset sau `/compact`. Trong một phiên `claude` mới, gửi:

> Để kiểm tra một hook: dùng tool Read đọc `shop.py` ba lần liên tiếp, không đổi gì. Báo lại nguyên văn kết quả của lần thứ ba.

Lần thứ ba phải bị chặn với thông báo `toknix: you already read this exact range … still in your context`. Tiếp theo, gõ `/compact`, rồi gửi:

> Dùng tool Read đọc lại `shop.py` một lần.

Đạt khi lần đọc này **không** bị chặn. Nếu bị chặn nghĩa là hook session-start không chạy sau `/compact`.

**Quan sát thêm (R2, không phải check):** gửi *"Đọc toàn bộ big/catalog.py rồi cho biết có bao nhiêu hàm."* Lần Read cả file sẽ bị chặn, kèm gợi ý `toknix outline` (lệnh này chưa có, P3-T1). Ghi lại agent xử lý thế nào, ví dụ đọc theo `offset`/`limit` hay dùng Grep, và có bị kẹt không.

## 6. Grep `head_limit` (check D)

Trong phiên `claude` mới, gửi:

> Để kiểm tra một hook: dùng tool Grep với `output_mode: "content"`, pattern `^def item_`, path `big`, không đặt `head_limit`. Cho biết bạn nhận được bao nhiêu dòng kết quả và kết quả có ghi là bị cắt bớt không.

Rồi chạy `python3 ~/toknix-verify/show.py 100000 | grep -A3 '>> Grep'` để xem kết quả thật. File có đúng 600 dòng khớp.

- Nhận ≤ 200 dòng → `updatedInput` của Grep được áp dụng.
- Nhận đủ 600 dòng → không được áp dụng.
- Nếu bị cắt: kết quả có dòng báo cắt hay báo phân trang không (chép nguyên văn), hay chỉ dừng ở dòng 200 mà không nói gì? Ghi thêm câu agent trả lời về số dòng.

## 6b. Rule của `init-project` (check H)

```bash
cd ~/toknix-verify/shop && mkdir -p node_modules/pkg && echo "module.exports = 1" > node_modules/pkg/index.js
toknix init-project
```

Trong một phiên `claude` mới, gửi: *"Dùng tool Read đọc dòng 1 của `node_modules/pkg/index.js` (offset 1, limit 1)."* Read guard của toknix cho phép đọc lát cắt, nên nếu bị chặn thì chính rule deny đã có tác dụng. Ghi lại thông báo chặn.

## 6c. Nhắc ngân sách context (check I)

Đặt ngân sách thật nhỏ để không phải chờ context lớn:

```bash
echo '{"context_budget": 5000}' > ~/.toknix/config.json   # đã có config thì thêm khoá này vào
```

Mở lại `claude` trong repo mẫu, gửi hai prompt ngắn (ví dụ *"Đọc shop.py"* rồi *"Có bao nhiêu hàm?"*).

**Check I** đạt khi cả hai điều sau đúng:

- Ở prompt thứ hai, giao diện hiện dòng `toknix: context … is over the 5.0k budget …` (chép nguyên văn).
- Hỏi tiếp *"Tin nhắn hệ thống cuối cùng bạn thấy có chữ toknix không? Chép nguyên văn."*: model trả lời là **không** thấy.

Xong thì xoá khoá `context_budget` khỏi `~/.toknix/config.json`.

## 7. Dọn dẹp

```bash
toknix uninstall
toknix doctor; echo "exit $?"          # bây giờ phải báo missing, exit 1
sh install.sh --uninstall              # gỡ cả lệnh toknix (uv tool hoặc venv)
rm -rf ~/toknix-verify                  # repo mẫu
rm -rf ~/.toknix                        # dữ liệu toknix (tuỳ chọn)
```

Kiểm tra `~/.claude/settings.json` đã trở về như trước:

```bash
diff <(python3 -m json.tool ~/settings.json.before-toknix) <(python3 -m json.tool ~/.claude/settings.json) && echo "giống hệt"
```

Nếu trước khi cài bạn chưa có `~/.claude/settings.json` thì không có bản để so; khi đó `uninstall` xoá luôn file mà `install` đã tạo, nên chỉ cần kiểm tra file không còn tồn tại.

## Kết quả

Điền bảng rồi gửi lại cho người phát triển. Họ sẽ chốt các `[ASSUMPTION]` trong `docs/ALGORITHMS.md`, đổi mặc định nếu cần, và đánh dấu P2-T4.

| | |
|---|---|
| Ngày | |
| Hệ điều hành | |
| `claude --version` | |
| `python3 --version` | |
| Commit toknix | |

| Check | Kết quả (đạt / không đạt / ghi chú) | Bằng chứng (dán output ngắn) |
|---|---|---|
| F — `doctor` không `PYTHONPATH` exit 0 | | |
| A — `updatedInput` của Bash được áp dụng (`gain` có `pytest`, có footer) | | |
| A — hộp hỏi quyền hiện lệnh gốc hay lệnh đã viết lại | | |
| A — `>> Bash:` trong transcript là lệnh gốc hay đã viết lại | | |
| G — agent sửa xong, `42 passed`, chỉ đổi `shop.py` | | |
| B — rule `Bash(.venv/bin/python -m pytest:*)` còn khớp (không hỏi quyền) | | |
| C — Read lần 2 khi tắt R3: nội dung đầy đủ hay stub (chép nguyên văn stub) | | |
| E — lần Read thứ 3 bị toknix chặn | | |
| E — sau `/compact`, đọc lại được | | |
| D — Grep nhận bao nhiêu dòng (200 hay 600) | | |
| D — có báo bị cắt / phân trang không (chép nguyên văn) | | |
| I — dòng nhắc hiện cho người dùng (chép nguyên văn) | | |
| I — model không thấy dòng nhắc | | |
| R2 — agent xử lý thế nào khi bị chặn đọc `big/catalog.py` | | |
| H — rule của `init-project` chặn Read một lát cắt trong `node_modules/` | | |
| `settings.json` sau `uninstall` giống hệt bản trước khi cài | | |

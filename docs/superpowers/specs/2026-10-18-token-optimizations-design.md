# Thiết kế: mở rộng tối ưu token cho tokopt v0.1

- Ngày: 2026-10-18
- Trạng thái: đã duyệt 2026-10-18; đã gộp vào `docs/ALGORITHMS.md` và `docs/PLAN.md`
- Quan hệ với tài liệu khác: **bổ sung** cho `docs/PLAN.md` và `docs/ALGORITHMS.md`, không thay thế. Sau khi duyệt, nội dung được gộp vào `ALGORITHMS.md` (nguồn sự thật duy nhất theo `CLAUDE.md`) và thành các task trong `PLAN.md`.

Quy ước giữ như `ALGORITHMS.md`: `FACT`, `[ASSUMPTION]`, `[TBD]`. Thêm nhãn **(quyết định của người viết)** cho các điểm người dùng chưa duyệt riêng, để người dùng soát lại. Khi tài liệu được duyệt, các điểm này coi như đã duyệt.

**Riêng trong tài liệu này**, nhãn `[ASSUMPTION]` gắn cho một con số là **giá trị mặc định ban đầu**: người làm đặt đúng giá trị đó vào config và được chỉnh nếu test trên fixture cho thấy giá trị khác tốt hơn (ghi lý do trong commit). Quy tắc "dừng lại và hỏi" của `CLAUDE.md` chỉ áp dụng cho các mục `[TBD]` và cho giả định về hành vi của Claude Code (kiểm chứng ở P2-T4).

---

## 1. Ý định và lý do

Người dùng muốn "tối ưu được điểm nào thì tối ưu", lấy plan hiện có làm nền, và xem xét áp dụng ý tưởng từ bài báo CNF-MCE ("Counting and Min-Cost Encoding for Tokenization in Large Language Models", arXiv 2610.01127).

**Đối tượng:** chỉ người dùng coding agent (Claude Code, Codex CLI, Gemini CLI). Không phục vụ người viết app gọi API LLM (RAG, chatbot).

### 1.1 Nguyên lý xếp hạng: hệ số đọc lại

Token đã vào context sẽ bị đọc lại (cache_read) ở mọi request sau đó, cho tới khi compaction hoặc hết phiên. Chi phí thật của `T` token vào context, khi phiên còn `R` request phía sau:

```
reread_cost(T, R) = T·w_cw + T·w_cr·R
ví dụ: T = 5.000, R = 50, weights mặc định [1, 1.25, 0.1, 5]
       → 6.250 + 25.000 ≈ 31.000 ETU   (gấp ~5 lần con số chars/4 = 5.000)
```

Hệ quả: chặn token ở cửa vào context có đòn bẩy lớn nhất; số "tiết kiệm" tính theo chars/4 đang báo thấp hơn thực tế.

### 1.2 Những gì lấy từ bài báo CNF-MCE

Phần cốt lõi của bài (huấn luyện tokenizer mới) **không áp dụng được**: người dùng trả tiền theo token do tokenizer của nhà cung cấp đếm, và tokenizer mới chỉ có ích khi sở hữu cả model (bài báo phải huấn luyện lại model 1,8B và 8B). Giữ lại hai ý:

1. **Chi phí token phụ thuộc loại văn bản, không tỉ lệ thuận với số ký tự** (tinh thần MCE: cùng nội dung, chọn cách biểu diễn tốn ít token nhất) → ước lượng token theo loại ký tự (N1.2a, N2.2) và chuẩn hoá chuỗi tốn token (N1.2d).
2. **Chỉ giữ cái thực sự được dùng, không phải cái xuất hiện nhiều** (tinh thần CNF) → đo tỉ lệ recall của bộ lọc (N2.1), tìm MCP server / skill được nạp nhưng không được gọi (N1.3).

---

## 2. Mục tiêu

Bảy hạng mục, chia hai nhóm. Tất cả nằm trong v0.1.

**Nhóm 1: làm đầy đủ trong v0.1**

| ID | Hạng mục |
|---|---|
| N1.1 | Tính chi phí và tiết kiệm theo hệ số đọc lại trong `gain`, `waste`, `report` |
| N1.2 | Bộ lọc: ước lượng token theo loại ký tự, ngân sách cắt theo token, cắt dòng quá dài, chuẩn hoá chuỗi tốn token |
| N1.3 | Phát hiện cache vỡ giữa phiên; MCP server / skill được cấu hình nhưng không được gọi |
| N1.4 | Giới hạn kết quả Grep khi agent không đặt `head_limit` (có điều kiện, xem mục 6) |
| N1.5 | Làm model viết ít hơn: đo tỉ trọng output, rules "viết ít", chọn rules bằng bench |

**Nhóm 2: v0.1 chỉ đo và hiệu chỉnh, chưa tự động thay đổi hành vi**

| ID | Hạng mục |
|---|---|
| N2.1 | Ghi nhận `recall` và hiển thị tỉ lệ recall theo loại bộ lọc (tự nới bộ lọc để v0.2) |
| N2.2 | Hiệu chỉnh tỉ lệ ký tự/token từ transcript Claude Code |

## 3. Ngoài phạm vi (non-goals)

- Huấn luyện hoặc thay tokenizer.
- Nén output của model sau khi đã sinh (đã trả tiền lúc sinh; Claude Code không có hook sửa lịch sử hội thoại).
- Nén prompt kiểu LLMLingua (cần model phụ, không phải stdlib, và làm sai lệch code cần chính xác từng ký tự).
- Proxy API, định tuyến model, tự đổi cấu hình model/effort của người dùng.
- Ca sử dụng của người viết app (RAG, lịch sử chatbot).
- Tự động nới bộ lọc dựa trên recall (v0.2).
- Đo "dòng nào trong CLAUDE.md có được dùng" (không đo tin cậy được) **(quyết định của người viết)**.
- Phần nào plan hiện đã để ngoài phạm vi v0.1 thì vẫn ngoài phạm vi (hook native Codex/Gemini, transcript Gemini).

## 4. Anti-goals (thất bại dù chạy đúng kỹ thuật)

- Agent làm sai hoặc bị kẹt vì thiếu thông tin. Tiết kiệm token mà phải sửa lại nhiều lần là thất bại. *(Người viết suy ra, người dùng chưa phát biểu trực tiếp.)*
- Biến đổi output làm agent chép sai nội dung vào Edit (vì vậy không chuẩn hoá output có thể chứa code nguyên văn, xem N1.2d).
- Báo số tiết kiệm phóng đại: số nào là ước lượng phải ghi rõ, luôn hiện kèm con số trực tiếp.
- Hook chậm hoặc chặn agent (giữ mục tiêu khởi động < 80 ms của RK-4).

## 5. Ràng buộc (giữ nguyên từ plan)

Python ≥ 3.9, chỉ stdlib kể cả test; không gọi mạng; hook lỗi → exit 0 im lặng; `tokopt run` giữ nguyên exit code; không ghi ra ngoài `~/.tokopt/` (trừ `install`, `init-project`); đa nền tảng. Mọi biến đổi mà agent nhìn thấy đều có cờ tắt trong config; `recall` luôn trả output nguyên văn.

---

## 6. Chi tiết từng hạng mục

### N1.1 Chi phí theo hệ số đọc lại

- Định nghĩa `reread_cost(T, R)` như mục 1.1. `R` = số request assistant còn lại trong phiên sau request chứa tool_result đó, dừng ở compaction gần nhất nếu có.
- `waste` nhóm (1) và (2): xếp hạng theo `reread_cost`, hiển thị cả `T` và ETU thật.
- `gain`: hiển thị hai số.
  - **Trực tiếp:** Σ(raw_chars − out_chars) quy ra token (như hiện tại, nhưng dùng `est_tokens` mới của N1.2a).
  - **Tính cả đọc lại (ước tính):** trực tiếp × (w_cw + w_cr·R̄). `R̄` = trung vị số request còn lại sau mỗi tool_result Bash trong transcript N ngày gần nhất. Chưa đọc được transcript thì `R̄ = 20` `[ASSUMPTION]` và ghi rõ "dùng giá trị mặc định".
- Không truyền session_id từ hook vào lệnh `tokopt run` để tính R chính xác từng lần; dùng `R̄` **(quyết định của người viết: giữ lệnh viết lại đơn giản, tránh thêm khác biệt với permission rule)**.
- `statusline` giữ số "saved" trực tiếp (thận trọng, vì R tương lai chưa biết) **(quyết định của người viết)**.

### N1.2 Bộ lọc nhận biết chi phí token

**a. `est_tokens` theo loại ký tự.** Thay `ceil(len/4)` bằng tổng theo lớp:

```
lớp hex:       các chuỗi [0-9a-fA-F] hoặc base64 liền nhau ≥ 16 ký tự
lớp non_ascii: ký tự ngoài ASCII (tiếng Việt có dấu, ký hiệu ━ ✓ …)
lớp default:   phần còn lại
est = Σ chars_lớp / ratio_lớp
```

- Tỉ lệ nằm trong config `token_ratios`, giá trị ban đầu `default 4.0, hex 2.0, non_ascii 1.5` `[ASSUMPTION]`, sẽ được thay bằng số hiệu chỉnh ở N2.2.
- Một lượt regex duy nhất; hook read vẫn dùng `size // 4` để giữ tốc độ.
- **Bộ lọc (`tokopt run`) luôn dùng tỉ lệ trong config, không bao giờ đọc kết quả hiệu chỉnh của N2.2.** Kết quả hiệu chỉnh chỉ dùng cho báo cáo (`gain`, `report`, `waste`, `statusline`). Như vậy lượng output agent nhận được không tự thay đổi sau lưng người dùng, đúng nguyên tắc của Nhóm 2, và tỉ lệ dùng khi Codex chạy `tokopt run` cũng giống Claude Code **(quyết định của người viết)**.

**b. Ngân sách cắt theo token.** Thêm `max_tokens` (mặc định 3.000 `[ASSUMPTION]`, gấp đôi khi lệnh fail). `max_lines` vẫn giữ làm trần phụ. `budget_cut` dừng khi chạm trần nào trước. Mọi dòng khớp `ERROR_RE` luôn được giữ nguyên văn, bất kể ngân sách (xem mục 9). Phần còn lại của đầu ra không vượt `max_tokens` quá 5%; tức là đầu ra = toàn bộ dòng lỗi + phần không phải lỗi trong ngân sách.
- `gitdiff` vẫn không qua `budget_cut` và vẫn không bao giờ cắt giữa hunk, nhưng ngân sách khối file tính theo cả hai trần: 400 dòng **và** `max_tokens`, chạm trần nào trước thì dừng. Một khối file mà riêng nó đã vượt ngân sách (ví dụ file minified hoặc file sinh tự động) được liệt kê `path (+a -d)` kèm gợi ý `git diff -- <file>` như các file còn lại **(quyết định của người viết)**.

**c. Cắt dòng quá dài.** Dòng dài hơn `max_line_chars` (mặc định 800 `[ASSUMPTION]`) thành `<300 ký tự đầu> …[N chars]… <200 ký tự cuối>`. **Không áp dụng cho dòng khớp `ERROR_RE`** (giữ tuyệt đối nguyên văn) và không áp dụng cho `gitdiff`. Property test ERROR_RE giữ nguyên như plan: mọi dòng khớp trong input xuất hiện nguyên văn trong output.

**d. Chuẩn hoá chuỗi tốn token.** Mỗi biến đổi có cờ config riêng.

| Biến đổi | Mặc định | Áp dụng cho kind |
|---|---|---|
| Hex ≥ 32 ký tự → 12 ký tự đầu + `…`; `sha512-<base64>` / `sha256-<base64>` → `sha512-…` | bật | install, build, gitnet |
| ≥ 4 dòng liên tiếp bắt đầu bằng timestamp (ISO 8601, `HH:MM:SS`) → bỏ timestamp ở các dòng giữa, giữ ở dòng đầu và dòng cuối | bật | install, build, gitnet |
| Đường dẫn tuyệt đối nằm dưới cwd → đường dẫn tương đối | **tắt** (`normalize_paths: false`), vì tool Read của Claude Code cần đường dẫn tuyệt đối | mọi kind trừ gitdiff |

- Không bao giờ chuẩn hoá dòng khớp `ERROR_RE` (ví dụ lỗi checksum `expected sha512-… got sha512-…` cần nguyên văn để chẩn đoán). Không chuẩn hoá `listing`, vì tên file có hash (build artifact, pnpm store) phải giữ nguyên để agent Read được **(quyết định của người viết)**.
- Không chuẩn hoá các kind có thể in code nguyên văn: pytest, jest, gotest, cargotest, lint, compile, gitdiff, gitstatus, gitlog; generic cũng không **(quyết định của người viết: agent có thể chép nguyên văn vào Edit)**.

**Kiểm chứng N1.2:** test `est_tokens` theo từng lớp; fixture viết tay có dòng rất dài (JSON minified từ build) và log có timestamp, ghi nguồn trong `tests/fixtures/README.md`; mọi điều kiện "Kiểm chứng A2" hiện có vẫn pass.

### N1.3 Cache vỡ và cấu hình không dùng

**Cache vỡ (chỉ Claude Code trong v0.1)** **(quyết định của người viết: Codex chỉ có tổng cached_input_tokens, không đủ để xác định từng lần vỡ)**

- Request `k` là một lần vỡ khi `cache_read_k < 0.5 · (cache_read_{k−1} + cache_write_{k−1})` và `cache_write_k ≥ 10.000` `[ASSUMPTION: ngưỡng]`.
- Phân loại nguyên nhân, theo thứ tự ưu tiên:
  1. `compact`: có ranh giới compaction trước request `k`. Đây là việc bình thường, không tính là lãng phí.
  2. `model`: `message.model` khác request trước.
  3. `idle`: khoảng cách timestamp với request trước > `cache_ttl_seconds` (mặc định 300; người dùng có TTL 1 giờ tự đặt 3600).
  4. `unknown`: còn lại (thường do đổi MCP/tool, sửa instruction, cập nhật Claude Code).
- ETU mất = `cache_write_k · (w_cw − w_cr)`.
- `waste` thêm nhóm (6) "cache vỡ": số lần và ETU mất theo nguyên nhân, kèm gợi ý (idle → /clear hoặc chấp nhận khi nghỉ lâu; model → tránh đổi model giữa phiên; unknown → kiểm tra MCP/instruction vừa đổi).

**MCP server và skill không dùng**

- `context` liệt kê MCP server đã cấu hình (`~/.claude.json`, `.mcp.json`, settings của project) và skill (của người dùng, của project, của plugin), kèm số lần được gọi trong N ngày: đếm `tool_use` có tên `mcp__<server>__*` và input `skill` của tool `Skill` trong transcript.
- Server/skill 0 lần gọi → dòng gợi ý tắt. Không tự tắt gì.
- Chỉ báo số token của schema MCP khi đọc được schema; Claude Code bản mới có thể hoãn nạp schema MCP, nên mặc định chỉ báo số lượng và "không dùng" `[ASSUMPTION]`.
- Instruction file: giữ như A6.2 hiện tại (chỉ báo kích thước).

### N1.4 Giới hạn Grep

- `hook read` với tool `Grep`: nếu `output_mode == "content"` và không có `head_limit` → trả `updatedInput` thêm `head_limit = grep_head_limit` (mặc định 200 `[ASSUMPTION]`). Kiểm tra junk path vẫn chạy trước như hiện tại.
- Phụ thuộc hai điều cần kiểm chứng ở P2-T4:
  1. `updatedInput` có được áp dụng khi không có `permissionDecision` (cùng giả định RK-1).
  2. Khi `head_limit` cắt bớt kết quả, Claude Code có cho agent biết là kết quả đã bị cắt hay không.
- Quy tắc chốt: nếu (2) **không** có thông báo cắt, mặc định đặt `grep_head_limit: 0` (tắt), vì cắt im lặng có thể làm agent kết luận sai, ví dụ "chỉ có 200 chỗ gọi" **(quyết định của người viết, theo anti-goal)**.

### N1.5 Làm model viết ít hơn

**a. Đo.**

- `report`: tỉ trọng output = Σ(output·w_out) / Σ ETU, theo phiên và tổng.
- `waste` thêm nhóm (7): phiên có tỉ trọng output > 35% `[ASSUMPTION]`, kèm phân rã output theo loại nội dung trong transcript: thinking, văn bản trả lời, input của tool ghi file (Write, Edit, MultiEdit, NotebookEdit), input tool khác. `usage` chỉ có tổng, nên phân rã theo tỉ lệ ký tự; nếu thinking bị ẩn trong transcript thì phần thinking = output_tokens − ước lượng phần nhìn thấy.
- Gợi ý theo nhóm lớn nhất: Write chiếm nhiều → "sửa file có sẵn bằng Edit"; văn bản trả lời → rules viết ngắn; thinking → chỉ gợi ý giảm effort cho việc đơn giản (tokopt không tự đổi).

**b. Rules.** `templates/rules.md` v1 (P3-T5) có thêm phần "viết ít":

- không tóm tắt lại thay đổi vừa làm;
- không dán lại code đã có trong diff;
- sửa file có sẵn bằng Edit, không Write lại cả file;
- trả lời ngắn khi không được hỏi giải thích;
- vẫn giữ đầy đủ suy luận khi debug, bảo mật, migration.

Thêm ít nhất một biến thể ngắn gọn hơn trong `templates/variants/`. Mỗi bản ≤ 350 token.

**c. Chọn bằng bench.**

- `tokopt bench` thêm `--rules <file>`: chèn rules block vào CLAUDE.md / AGENTS.md của **worktree** (giữa marker như A8) trước khi chạy agent.
- Nếu CLAUDE.md global của người dùng đã có block tokopt thì bench cảnh báo, vì block đó ảnh hưởng cả hai nhánh A và B.
- `compare` thêm cột median output tokens và một verdict riêng cho output.
- Cách chọn biến thể **(quyết định của người viết)**:
  - Nhánh A luôn là rules v1; mỗi biến thể là một nhánh B, so riêng từng cặp với v1 (không đấu loại vòng tròn).
  - Biến thể thắng khi: pass rate không giảm, median output tokens giảm ≥ 10% và cận trên CI 95% bootstrap (như A6.3) của mức thay đổi output < 0. Không đòi tổng ETU giảm 10%, vì rules chỉ tác động lên phần output.
  - Nhiều biến thể cùng thắng thì chọn biến thể giảm output nhiều nhất.
- Bench không được ghi ra ngoài `~/.tokopt/` và worktree, nên không tự cô lập CLAUDE.md global hay hook của người dùng; chỉ cảnh báo như trên và ghi trạng thái đó vào kết quả bench.
- Chạy bench tốn tiền thật nên do người dùng chạy (P6). Khi chưa có số liệu, mặc định cài v1.

### N2.1 Ghi nhận recall

- Bảng mới `recalls(ts, tee_id)`. Lệnh `recall` ghi một dòng mỗi lần chạy, kiểu best-effort như `record_run`.
- `gain` thêm, theo kind: số lần cắt, số tee_id bị recall (đếm khác nhau), tỉ lệ recall. Tỉ lệ > 20% với ≥ 10 lần cắt `[ASSUMPTION]` → cảnh báo "bộ lọc <kind> có thể cắt quá tay".
- Không phân biệt được recall do agent hay do người dùng; chấp nhận sai số này.
- Tự nới bộ lọc: v0.2, ghi vào mục "Ngoài phạm vi" của PLAN.

### N2.2 Hiệu chỉnh tỉ lệ ký tự/token (Claude Code)

- Với hai request liên tiếp `k`, `k+1` trong cùng phiên, không có compaction hay cache vỡ ở giữa: `context = input + cache_read + cache_write`, `y = context_{k+1} − context_k − output_k`, `x` = số ký tự theo từng lớp (N1.2a) của các tool_result nằm giữa hai request.
- Hồi quy bình phương tối thiểu không hệ số chặn (giải hệ phương trình chuẩn 3×3 bằng stdlib) trên ≥ 200 mẫu, bỏ mẫu `y ≤ 0` và mẫu ngoại lai.
- Kết quả lưu ở `~/.tokopt/calibration.json`, theo từng agent (v0.1 chỉ Claude Code), và chỉ dùng cho báo cáo (xem N1.2a). `est_tokens` chỉ dùng kết quả hiệu chỉnh khi sai số trung vị trên tập kiểm tra < 15% `[ASSUMPTION]`; nếu không đạt thì giữ mặc định.
- Rủi ro đã biết: output_k có chứa thinking, mà thinking có thể không được giữ lại trong context ở request sau. Vì vậy phải lọc mẫu và đặt ngưỡng chất lượng như trên.

---

## 7. Gắn vào plan hiện có

| Hạng mục | Task trong PLAN.md |
|---|---|
| N1.2a `est_tokens` theo lớp | P1-T2 (cùng test `textutil`) |
| N1.2b–d | P1-T3 (nếu quá lớn thì tách task mới P1-T3b) |
| N2.1 bảng `recalls` | P1-T4; hiển thị ở P1-T5 |
| N1.1 `gain` hai số | P1-T5 (R̄ mặc định); P4-T2 (R̄ từ transcript, `waste` xếp theo `reread_cost`) |
| N1.4 | P2-T2; kiểm chứng ở P2-T4 |
| N1.5b rules + biến thể | P3-T5 |
| N2.2 | P4-T1 |
| N1.3, N1.5a | P4-T2 |
| N1.5c `bench --rules`, cột output | P4-T3 |
| Chọn biến thể rules bằng số liệu thật | P6-T1 |

## 8. Những gì người dùng đã quyết

- Chỉ phục vụ người dùng coding agent.
- Lấy plan hiện có làm nền và mở rộng: "tối ưu được điểm nào thì tối ưu".
- Xếp ưu tiên theo phán đoán, không phân tích transcript trên máy người dùng.
- Không huấn luyện tokenizer; thêm "làm model viết ít hơn" vào Nhóm 1.

## 9. Bất biến ERROR_RE: người dùng đã chốt "giữ tuyệt đối"

Plan cũ có mâu thuẫn: `CLAUDE.md` và A2.4 cấm bỏ dòng khớp `ERROR_RE`, nhưng `budget_cut` (A2.6) chỉ thêm dòng lỗi khi còn chỗ (`room > 0`), và bộ lọc lint/compile giới hạn 15 diagnostic mỗi file. **Người dùng chốt: giữ tuyệt đối mọi dòng lỗi.** Hệ quả cho người làm:

- `budget_cut`: mọi dòng khớp `ERROR_RE` đều được giữ, không phụ thuộc `room`; ngân sách chỉ áp lên các dòng còn lại. Sửa pseudocode A2.6 cho khớp.
- Lint/compile: giới hạn 15 diagnostic/file chỉ áp lên các dòng **không** khớp `ERROR_RE` (ví dụ dòng ngữ cảnh, dòng `^` chỉ vị trí). Diagnostic khớp `ERROR_RE` luôn được giữ. Sửa bảng A2.4 cho khớp.
- Cắt dòng dài (N1.2c) và chuẩn hoá (N1.2d) không đụng dòng lỗi.
- Chấp nhận hệ quả: một lần chạy có hàng nghìn dòng lỗi sẽ đưa hết vào context. Tỉ lệ trường hợp này được theo dõi qua `gain` (đếm lần chạy mà riêng dòng lỗi đã vượt `max_tokens`) để người dùng xem lại quyết định khi có số liệu.
- Hai ngoại lệ đã có sẵn trong plan, được giữ vì không làm mất nội dung **(quyết định của người viết)**: `dedupe_runs` gộp các dòng **giống hệt nhau liên tiếp** thành một dòng kèm `[×N]`; bộ lọc install gộp các dòng `deprecated` thành một dòng liệt kê tên gói (property test hiện đã cho phép).

## 10. Để người làm quyết định

- Ngưỡng và regex chi tiết (giá trị `[ASSUMPTION]` là mặc định ban đầu, đổi được nếu fixture cho thấy giá trị khác tốt hơn, xem phần quy ước).
- Luôn lưu output gốc để `recall` khi có bất kỳ biến đổi làm mất thông tin nào (cắt dòng dài, chuẩn hoá), kể cả khi tổng kích thước giảm chưa tới 10%.
- Lọc request của subagent khỏi các phép tính theo phiên; nhận biết TTL 1 giờ từ các trường usage nếu transcript có.
- Schema bảng chi tiết và cách migrate DB đang có.
- Tên lệnh cho phần hiệu chỉnh (lệnh riêng `tokopt calibrate` hay chạy trong `report`).
- Có tách task mới hay gộp vào task cũ khi một task quá lớn.
- Định dạng chính xác các dòng gợi ý trong `waste` và `context`.

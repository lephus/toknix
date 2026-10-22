# Thiết kế (nháp): phương pháp tối ưu token có cơ sở khoa học

- Ngày: 2026-10-21
- Trạng thái: **BẢN NHÁP, đang brainstorm, CHƯA DUYỆT.** Chưa có dòng code nào cho thiết kế này. Theo quy trình
  brainstorming (superpowers), không build gì cho tới khi người dùng duyệt bản thiết kế đầy đủ.
- File này cũng là **ghi chú bàn giao**: một session trên máy khác đọc file này là đủ để tiếp tục (mục 7).
- Nhãn: **(người dùng)** = người dùng đã nói; **(đề xuất)** = Claude đề xuất, người dùng chưa duyệt;
  **(đoán)** = giả định chưa đo.

---

## 1. Người dùng yêu cầu gì (nguyên văn, theo thứ tự)

1. "tôi đọc readme của bạn viết rồi, nhận thấy toknix chưa thực sự tối ưu token theo một thuật toán cụ thể hoặc
   phương pháp nhất định, Hãy phân tích và chuẩn hoá lại"
2. Khi được hỏi "chuẩn hoá xong thì mong thấy gì khác": "tôi cần là phuơng pháp"
3. Sau đề xuất "chọn cách biểu diễn có chi phí kỳ vọng thấp nhất": "tôi cần một thuật toán gíup tối ưu token có khoa học"
4. Sau bảng thuật toán (mục 4): "thay vì gợi ý thì tool cũng chạy compact để tối ưu khi cần thiết"

## 2. Chẩn đoán (đề xuất; người dùng không bác bỏ)

toknix đã có mô hình chi phí (A1: ETU theo giá từng model, `reread_cost = T·w_cw + T·w_cr·R`), nhưng mô hình chỉ dùng
để **báo cáo**. Mọi quyết định thật là ngưỡng đặt tay, không suy ra từ một hàm mục tiêu:

| Quyết định | Ngưỡng hiện tại | Ở đâu |
|---|---|---|
| Cắt output lệnh | `max_lines` 120, `max_tokens` 3000 (gấp đôi khi lệnh lỗi) | A2.6 |
| Chặn đọc cả file | `guard_big_file_lines` 800 | A4 |
| Giới hạn Grep | `grep_head_limit` 200 | A4 |
| Chặn đọc lặp | lần đọc giống hệt thứ 3 (`guard_repeat_reads` 2) | A4 |
| Nhắc `/clear` | `context_budget` 200k (người dùng chốt 2026-10-19) | A7 |

Phần References của README chỉ "lấy ý tưởng" từ CNF-MCE và CoBPE, nên khoảng trống càng lộ rõ.

## 3. "Khoa học" nghĩa là gì trong thiết kế này (đề xuất)

Mỗi quyết định phải: (a) giải một bài toán phát biểu rõ ràng; (b) dùng thuật toán đã công bố; (c) có bảo đảm hoặc có
số đo kiểm chứng được; (d) **báo trước** mức tiết kiệm nó dự đoán, để `report` so dự đoán với số thật trong transcript.
Một mô hình sai phải lộ ra được.

## 4. Phương pháp (đề xuất)

**Bài toán:** giảm tối thiểu chi phí kỳ vọng của phiên `E[C]`, với ràng buộc tỉ lệ hoàn thành task không kém hơn.

```
k_i  = w_cw + w_cr·R_i            # giá 1 token vào context ở request i, còn R_i request đọc lại phía sau
RT_i ≈ ctx_i·w_cr + output        # giá 1 lượt gọi tool thêm (vd. agent phải recall / đọc lại)
E(cách biểu diễn) = token·k_i + p_thiếu·RT_i      # chọn cách có E nhỏ nhất
C ≈ N·F + Σ T·k + O·w_out + cache vỡ              # bản đồ đòn bẩy
```

Bản đồ đòn bẩy (mỗi lệnh của toknix ứng với một số hạng): `run` và read guard giảm T; rules giảm O; `context` giảm F;
statusline, `/clear` và compact giảm R; `waste` tìm cache vỡ. README và ALGORITHMS sẽ trình bày toknix theo bản đồ này
(đề xuất, chưa bàn chi tiết).

| Quyết định | Bài toán | Thuật toán | Cơ sở |
|---|---|---|---|
| Giữ dòng nào khi lọc output | Chọn tập dòng nhiều thông tin nhất trong ngân sách token (knapsack) | Thông tin của dòng = `−log P(mẫu dòng)`, mẫu gom kiểu Drain; chọn tham lam theo thông tin/token; dòng `ERROR_RE` luôn giữ | Lý thuyết thông tin; Selective Context (Li et al., EMNLP 2023); Drain (He et al., ICWS 2017) |
| Đưa đầy đủ, lọc, outline hay chặn | Quyết định dưới bất định | `E` nhỏ nhất (công thức trên) | Lý thuyết quyết định Bayes |
| Ước lượng `p_thiếu`, R | Thống kê | `p_thiếu`: Beta-Binomial theo số `recall` từng loại bộ lọc; R: phân phối số request còn lại đo từ transcript | Có khoảng tin cậy thay cho số đặt tay |
| Khi nào compact | Thuê hay mua (ski rental) / tối ưu trên dữ liệu | Xem mục 5 | Ski rental: không tệ hơn 2 lần tối ưu (Karlin et al. 1988), nếu chi phí dựng lại ước lượng đúng |
| Chấp nhận một thay đổi | Kiểm định | Bench A/B: kiểm định không-kém-hơn (non-inferiority) cho pass rate, bootstrap CI cho chi phí | Luật hiện nay chỉ là "pass rate không giảm" |

Ví dụ (đề xuất): context 80k, R̄ = 20, Opus 5.5 → chặn một file chỉ có lời khi file lớn hơn khoảng 2–3k token, thay vì
cố định 800 dòng. Context càng lớn thì ngưỡng càng cao, vì một lượt gọi thêm càng đắt.

Liên hệ CNF-MCE: bài báo chọn cách tách token tốn ít nhất; toknix chọn cách biểu diễn context tốn ít nhất.

## 5. Compact tự động (yêu cầu 4 của người dùng)

### 5.1 Claude Code cho phép gì (tra tài liệu code.claude.com ngày 2026-10-21 qua subagent; kiểm lại câu chữ trước khi dựa vào)

- **Không hook nào kích hoạt được compaction.** PreCompact chỉ **chặn** được (exit 2 hoặc `"decision": "block"`); nó nhận
  `trigger` (`manual`/`auto`) và `custom_instructions`. UserPromptSubmit không thay được prompt (chỉ thêm
  `additionalContext`). Nguồn: https://code.claude.com/docs/en/hooks.md
- **Ngưỡng tự compact chỉnh được:**
  - `autoCompactWindow` trong settings.json, ở cấp trên cùng hoặc theo model dưới `modelSettings`; `/autocompact` ghi key
    này. Nguồn: https://code.claude.com/docs/en/model-config.md
  - `CLAUDE_CODE_AUTO_COMPACT_WINDOW`: 100000–1000000 token, ưu tiên hơn `/autocompact`, `--autocompact` và
    `autoCompactWindow`. Cùng nguồn.
  - `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`: 1–100, chỉ hạ được ngưỡng. Nguồn: https://code.claude.com/docs/en/env-vars.md
  - `autoCompactEnabled`: bật/tắt; cú pháp chưa rõ. Nguồn: https://code.claude.com/docs/en/settings-reference.md
- `/compact [instructions]` có tồn tại. Nguồn: https://code.claude.com/docs/en/sessions.md
- **Không có tài liệu:** settings có được đọc lại giữa phiên không; `autoCompactWindow` compact đúng tại cửa sổ hay ở một
  % của nó. Định dạng transcript là nội bộ và đổi theo phiên bản (`compact_boundary`, `compactMetadata.preTokens`);
  toknix vốn đã phụ thuộc vào nó.
- Codex: `model_auto_compact_token_limit` (token). Gemini CLI: `model.chatCompression.contextPercentageThreshold`
  (0–1, mặc định 0,7). **Chưa kiểm chứng**, mới thấy trong đoạn trích kết quả tìm kiếm.

### 5.2 Dữ liệu thật 30 ngày (máy của người dùng, 2026-10-21)

- 51 lần compact: 48 tự động, 3 thủ công.
- Context lúc compact (`preTokens`) p10/p50/p90: 293k / **967k** / 971k, tức là Claude Code tự compact sát giới hạn 1M.
- Context ở request đầu sau compact p10/p50/p90: 58k / **95k** / 262k (82 mẫu, có thể đếm trùng do resume phiên).
- Trước compact, mỗi request chuỗi chính đọc lại thừa ~870k token ≈ 43k ETU (~$0.17).

### 5.3 Mô phỏng ngưỡng (spike, script ở Phụ lục A)

Mô hình: mỗi chuỗi chính dùng Opus 5.x (201 phiên, 34.420 request). Context tăng theo đúng phần tăng thật giữa các
request; mỗi request trả `ctx·w_cr`. Khi `ctx ≥ W` thì compact, trả `ctx·w_cr + 10k·w_out + 95k·w_cw_1h`, rồi context
về 95k. `w_cr` 0,05, `w_cw_1h` 2, `w_out` 5 (Opus 5.5, ETU/token); $ = ETU × 4 / 10⁶.

| Ngưỡng W | Số lần compact | ETU | $ (giá niêm yết) | So với 1M |
|---|---|---|---|---|
| 1M | 33 | 752,6M | $3,010 | — |
| 700k | 79 | 586,7M | $2,347 | −22% |
| 500k | 137 | 484,0M | $1,936 | −36% |
| 400k | 200 | 431,2M | $1,725 | −43% |
| 300k | 313 | 395,8M | $1,583 | −47% |
| **250k** | 440 | **388,6M** | **$1,554** | **−48%** |
| 200k | 667 | 407,8M | $1,631 | −46% |
| 150k | 1.293 | 522,8M | $2,091 | −31% |

Chạy lại script ở Phụ lục A sẽ ra số hơi khác (±0,5%) vì transcript mới được thêm vào; điểm thấp nhất vẫn ở 250k.

Đường chi phí có điểm thấp nhất thật: compact muộn thì tốn đọc lại, compact sớm thì tốn tóm tắt và ghi cache lại.

**Chưa tính / còn đoán:**
- Độ dài bản tóm tắt 10k token (đoán).
- **Chi phí agent đọc lại file sau compact** (chưa tính). Compact càng nhiều thì phần này càng lớn, có thể đẩy điểm tối
  ưu lên cao hơn 250k. Đo được từ transcript: các lần đọc file đã đọc trước compact.
- Chất lượng: compact nhiều có thể làm agent quên chi tiết. Phải kiểm bằng bench.
- Mô phỏng ở 1M ra 33 lần compact, thật là 51 (có phiên dùng model cửa sổ 200k, và có compact thủ công).
- Chỉ tính đọc lại context và chi phí compact; ghi cache nội dung mới và output coi như không đổi theo W.

### 5.4 Cách làm (đề xuất)

- toknix không ra lệnh `/compact` (Claude Code không cho). toknix **tính ngưỡng tối ưu W\* và tự ghi `autoCompactWindow`
  vào settings.json** (sao lưu như `install`); Claude Code tự compact khi chạm ngưỡng.
- W\*: mô phỏng lại các phiên của chính người dùng với từng W (tối ưu trên dữ liệu), chọn W có tổng chi phí thấp nhất,
  kèm bootstrap CI. Chưa đủ dữ liệu thì dùng luật ski rental.
- Tính lại định kỳ từ dữ liệu mới (khi nào và bằng lệnh gì: chưa bàn). Codex và Gemini dùng key tương ứng (chưa kiểm).
- Thay ngưỡng 200k cố định của statusline và hook nhắc bằng W\*.
- Thêm vào `docs/VERIFY.md`: settings có đọc lại giữa phiên không; ngữ nghĩa chính xác của `autoCompactWindow`.

## 6. Câu hỏi đang chờ và chưa bàn

- **Đang chờ người dùng trả lời** (câu cuối Claude hỏi, sau mục 5): "Phần này còn sai hay thiếu gì so với điều bạn
  muốn?". Người dùng chưa trả lời mà yêu cầu commit, push và ghi lại context.
- Chưa bàn: thứ tự và phạm vi của 5 thuật toán ở mục 4; có viết lại README/ALGORITHMS theo bản đồ đòn bẩy không; mức rủi
  ro chất lượng chấp nhận được khi compact sớm; toknix có được tự ghi settings mà không hỏi lại không; tính lại W\* khi
  nào.

## 7. Bàn giao: tiếp tục trên máy khác

1. Đọc file này (đủ context). Memory tự động của Claude Code chỉ nằm ở máy cũ, không đồng bộ; nội dung cần thiết đã chép
   vào đây và mục 8.
2. Tiếp tục brainstorm: hỏi lại câu đang chờ ở mục 6, rồi play back các phần còn lại (lọc output theo thông tin, quyết
   định đọc, ước lượng tham số, kiểm định), mỗi lần một phần, mỗi tin nhắn một câu hỏi.
3. Đây là cỡ **dự án**: viết bản thiết kế đầy đủ (thay file nháp này), cho subagent đọc kiểm như người build
   (builder check), người dùng duyệt.
4. Sau khi duyệt: superpowers:writing-plans → phase P10 trong `docs/PLAN.md`, gộp thuật toán vào `docs/ALGORITHMS.md`
   (nguồn sự thật duy nhất), rồi bỏ dòng "Đang dở" trong `CLAUDE.md`.
5. Chạy lại mô phỏng: `python3 -I` với script ở Phụ lục A (đọc `~/.claude/projects` của máy đang chạy).

## 8. Bối cảnh khác (trước chỉ có trong memory của máy cũ)

- **Repo và phát hành:** thư mục `tokopt/` là repo `lephus/toknix` (public). Làm trên `dev` (trước 2026-10-22 là `v0.1-dev`, đã gộp vào `main` và xoá); phát hành = fast-forward
  `main` rồi push, **không force**. Máy cũ push qua SSH host alias `phule9225.github.com` trong `~/.ssh/config`; máy khác
  cần alias đó hoặc đổi remote. Thư mục `toknix/` bên ngoài là bản clone cũ chỉ có commit README đầu tiên: không push từ đó.
  Phát hành bản mới: tăng `__version__`, thêm CHANGELOG, sửa version trong `tests/test_cli.py`, ví dụ pin trong hai README
  và `docs/VERIFY.md`, push `main`, tạo tag có chú thích `vX.Y.Z` và push tag. Kiểm installer với
  `env -i … PATH=/usr/bin:/bin`. Xoá `build/` cũ trước khi build wheel.
- **Việc chỉ người dùng làm được:** P2-T4 (VERIFY trên Claude Code thật) và P6-T1 (bench A/B tốn tiền), xem `docs/PLAN.md`.
- **P9-T3 chưa chọn:** gợi ý bỏ định nghĩa tool lớn không dùng; cần kiểm `permissions.deny` / `disallowedTools` có bỏ
  định nghĩa khỏi request không.
- **codegraph (tạm dừng 2026-10-21):** người dùng hỏi có nên thêm colbymchenry/codegraph. Kết luận đã đưa: không bundle
  (toknix chỉ stdlib); chỉ làm toknix nhận biết codegraph khi `toknix bench` cho thấy nó thắng. Số đo 7 ngày:
  `outline/symbol/refs/map` được gọi 4 lần so với ~32k lệnh đọc/tìm (vấn đề là agent không dùng, không phải chất lượng
  index); đọc và tìm file ~$700/7 ngày. Khi tiếp tục: hỏi người dùng có cài codegraph không (nó sửa `~/.claude.json` và
  `CLAUDE.md`) và bench repo/task nào (tốn tiền). Nếu khuyên dùng thì đặt `CODEGRAPH_TELEMETRY=0`.
- **Cách làm việc của người dùng:** trao đổi bằng tiếng Việt; một commit cho mỗi task (`P<n>-T<m>: …`); người dùng quyết
  định phạm vi và lúc push.

---

## Phụ lục A. Script mô phỏng ngưỡng compact (spike, không thuộc toknix)

```python
# python3 -I sim_autocompact.py — 30 ngày, chuỗi chính dùng Opus 5.x, trong ~/.claude/projects
import json, time
from pathlib import Path

W_CR, W_CW, W_OUT = 0.05, 2.0, 5.0      # Opus 5.5, ghi cache 1 giờ, ETU/token
POST, SUMMARY_OUT = 95_000, 10_000      # context sau compact (trung vị đo được); độ dài tóm tắt (đoán)

cutoff = time.time() - 30 * 86400
chains = []
for p in Path.home().joinpath(".claude", "projects").rglob("*.jsonl"):
    if "subagents" in p.parts or p.stat().st_mtime < cutoff:
        continue
    seen, incs, last = set(), [], None
    with p.open("rb") as f:
        for raw in f:
            if b'"usage"' not in raw or b'"isSidechain":true' in raw:
                continue
            try:
                m = json.loads(raw).get("message") or {}
            except ValueError:
                continue
            if not str(m.get("model", "")).startswith("claude-opus-5") or m.get("id") in seen:
                continue
            seen.add(m.get("id"))
            u = m.get("usage") or {}
            ctx = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
            incs.append(ctx if last is None else max(ctx - last, 0))   # bỏ lần tụt context ở compact thật
            last = ctx
    if len(incs) > 1:
        chains.append(incs)

def cost(W):
    total, n = 0.0, 0
    for incs in chains:
        ctx = 0
        for d in incs:
            ctx += d
            if ctx >= W:   # compact: đọc context + viết tóm tắt + ghi cache context mới
                total += ctx * W_CR + SUMMARY_OUT * W_OUT + POST * W_CW
                ctx, n = POST, n + 1
            total += ctx * W_CR
    return total, n

print("chains", len(chains), "requests", sum(map(len, chains)))
for W in (150_000, 200_000, 250_000, 300_000, 400_000, 500_000, 700_000, 1_000_000):
    c, n = cost(W)
    print(f"W={W // 1000:>4}k  compactions={n:>4}  ETU={c / 1e6:7.1f}M  ${c * 4 / 1e6:,.0f}")
```

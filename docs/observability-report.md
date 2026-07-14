# Báo cáo: Lớp Observability trong FE Architecture Harness

## 1. Observability là gì?

Observability trong Agent Harness là khả năng theo dõi, ghi nhận và phân tích toàn bộ quá trình thực thi của agent, giúp xác định điều gì đã xảy ra, ở bước nào và dựa trên những dữ liệu nào khi có lỗi hoặc kết quả không mong muốn.

LLM có tính non-deterministic, nghĩa là cùng một input vẫn có thể sinh ra output khác nhau giữa các lần chạy (đặc biệt khi temperature > 0 hoặc có yếu tố ngẫu nhiên). Vì vậy nhiều lỗi rất khó tái hiện, khiến việc debug khác với phần mềm truyền thống. Vậy nên:

- Phải **ghi lại chính xác những gì đã hỏi AI và AI đã trả lời gì** ở từng bước — vì đó là bằng chứng để hiểu vì sao nó quyết định vậy, khi không thể bắt lỗi lặp lại lần hai.
- Không thể chỉ tin AI tự báo làm xong — phải có một khâu kiểm tra độc lập, và khi kiểm tra thấy sai thì phải ghi rõ **sai chỗ nào, vì lý do gì**, chứ không chỉ ghi chung chung "lỗi".
- Phải ghi lại mỗi lần **con người can thiệp** (đồng ý, từ chối, yêu cầu sửa lại) — vì một lần chạy có thể kéo dài 20–40 phút, sau này cần xem lại đã có ai can thiệp, khi nào, vì sao.
- Phải theo dõi được **chi phí, quota** — vì mỗi lần gọi AI là tốn thật.

Có 3 loại thông tin cần ghi lại, mỗi loại trả lời một câu hỏi khác nhau:

| Loại                          | Trả lời câu hỏi                                                                                                    |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| **Dòng thời gian (Traces)**   | Theo dõi luồng thực thi của agent, thời gian của từng bước và mối quan hệ giữa các bước                            |
| **Con số thống kê (Metrics)** | Theo dõi các chỉ số tổng hợp như thời gian xử lý, tỷ lệ lỗi, lượng token và chi phí để đánh giá hiệu năng hệ thống |
| **Nhật ký chi tiết (Logs)**   | Lưu lại chi tiết các sự kiện như prompt, response, tool calls, lỗi và các thông tin phục vụ debug                  |

## 2. Lớp này nằm ở đâu trong kiến trúc harness?

Observability **không phải một bước trong quy trình** mà là **một lớp chức năng xuyên suốt** (không giống các bước `clarification → requirement → … → codegen`). Nó giống như **một chiếc camera an ninh treo phía trên**, quan sát toàn bộ quy trình đang chạy mà không can thiệp vào việc quy trình đó làm gì:

```
CLI (run) bật lên 1 lần khi bắt đầu chạy
        │
        ▼
Orchestrator — người điều phối chạy toàn bộ quy trình
   │
   ├─ Chạy từng bước (gọi AI, kiểm tra kết quả, lưu lại)
   │      → mỗi bước đều được ghi lại: mất bao lâu, tốn bao nhiêu tiền,
   │        đã hỏi AI gì, AI trả lời gì, kiểm tra có pass không
   │
   └─ Human in the loop
          → ghi lại: con người đồng ý hay từ chối, vì sao, có yêu cầu sửa gì không
```

Kết quả được ghi ra 3 nơi (trong thư mục logs của mỗi project):

- Một file **nhật ký chi tiết** — đọc được bằng mắt thường, Nhật ký chi tiết của từng sự kiện.
- Một file **dòng thời gian** — Trace theo cấu trúc cây, thể hiện quan hệ giữa các bước và thời gian thực thi.
- Một file **con số thống kê** — Các chỉ số tổng hợp như số lần gọi model, token, chi phí, thời gian, tỷ lệ lỗi...

## 3. Dùng cho AI Architecture Harness

Bài toán đặt ra là một Agent Harness gồm khoảng 12 bước xử lý, trong đó nhiều bước phải gọi mô hình AI thật. Một lần chạy có thể kéo dài từ 20–40 phút, có thể phải thử lại (retry), có bước cần con người phê duyệt (Human-in-the-loop), và kết quả của AI không hoàn toàn giống nhau giữa các lần chạy do tính non-deterministic. Vì vậy, khi một lần chạy thất bại, việc xác định chính xác nguyên nhân là rất khó nếu chỉ dựa vào các thông báo lỗi đơn giản.

Những câu hỏi và cách lớp Observability giúp:

| Câu hỏi                                       | Trước khi có lớp này                             | Sau khi có lớp này                                                                       |
| --------------------------------------------- | ------------------------------------------------ | ---------------------------------------------------------------------------------------- |
| Bước nào chạy lâu nhất / tốn tiền nhất?       | Phải đoán                                        | Có lệnh xem báo cáo tổng hợp ngay: bước nào lâu, bước nào đắt                            |
| Một lần chạy bị lỗi, vì sao?                  | Chỉ có 1 dòng lỗi ngắn gọn, không rõ nguyên nhân | Xem lại đúng nội dung đã hỏi AI và AI đã trả lời gì lúc đó                               |
| Lỗi nào hay lặp lại nhất?                     | Không tổng hợp được                              | Mỗi lỗi được ghi rõ loại lỗi, mức độ nghiêm trọng, có thể gộp lại xem cái nào hay xảy ra |
| Con người đã can thiệp bao nhiêu lần, vì sao? | Không ghi gì cả                                  | Ghi đầy đủ từng lần, kèm lý do                                                           |

## 4. Code đã làm được tới đâu

### Đã làm xong

**Cuốn nhật ký chi tiết (`core/logger.py`)**
Mỗi lần chạy pipeline sẽ có 1 file nhật ký riêng, mỗi dòng ghi lại 1 sự việc. Các sự việc được ghi:

- Mỗi lần gọi AI: nội dung đã hỏi, AI trả lời gì, tốn bao nhiêu tiền, mất bao lâu.
- Mỗi lần kiểm tra kết quả bị fail: lỗi gì, mức độ nghiêm trọng ra sao.
- Mỗi lần hỏi ý kiến con người: đồng ý hay từ chối, có yêu cầu sửa gì không.
- Mỗi lần một bước bị đánh dấu "phải chạy lại": chạy lại bước nào, kéo theo những bước nào khác, vì lý do gì (con người từ chối / tự động sửa lỗi / thao tác thủ công).

**Dòng thời gian + con số thống kê (`core/tracing.py`, `core/orchestrator.py`)**
Dựng cây "bước lớn chứa bước nhỏ": toàn bộ 1 lần chạy → từng bước riêng lẻ → từng lần hỏi ý kiến con người. Mỗi bước ghi lại: model AI nào được dùng, mất bao lâu, tốn bao nhiêu tiền, pass hay fail. Có thêm 3 con số thống kê tự động cộng dồn: thời gian chạy mỗi bước, số lần thành công/thất bại, tổng chi phí.

**Lệnh xem báo cáo tổng hợp (`stats`)**
Gom tất cả các lần chạy trước đó lại, tính ra: bước nào trung bình lâu nhất, bước nào hay fail nhất, tổng chi phí, số lần con người can thiệp — mà không cần đọc từng file nhật ký thủ công.

### Còn thiếu / hướng làm tiếp theo

- **Chưa có nơi lưu trữ tập trung để xem biểu đồ** — hiện dữ liệu chỉ ghi ra file để chứng minh đúng cấu trúc, chưa có màn hình trực quan thật sự.
- Chưa ghi được "lý do AI dừng trả lời" (hết giới hạn, tự kết thúc, hay bị chặn) — cần kiểm tra thêm khi chạy thật với AI thật.
- Các con số thống kê mới dừng ở 3 loại cơ bản (thời gian, số lần, chi phí) — chưa có thêm loại "đang chạy cùng lúc bao nhiêu bước".
- Test toàn bộ lớp

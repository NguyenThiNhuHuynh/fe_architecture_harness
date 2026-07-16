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

## 3. Dùng cho AI Architecture Harness (v2)

Bài toán đặt ra là một Agent Harness gồm khoảng 12 bước xử lý, trong đó nhiều bước phải gọi mô hình AI thật. Một lần chạy có thể kéo dài từ 20–40 phút, có thể phải thử lại (retry), có bước cần con người phê duyệt (Human-in-the-loop), và kết quả của AI không hoàn toàn giống nhau giữa các lần chạy do tính non-deterministic. Vì vậy, khi một lần chạy thất bại, việc xác định chính xác nguyên nhân là rất khó nếu chỉ dựa vào các thông báo lỗi đơn giản.

Những câu hỏi và cách lớp Observability giúp:

| Câu hỏi                                       | Trước khi có lớp này                             | Sau khi có lớp này                                                                       |
| --------------------------------------------- | ------------------------------------------------ | ---------------------------------------------------------------------------------------- |
| Bước nào chạy lâu nhất / tốn tiền nhất?       | Phải đoán                                        | Có lệnh xem báo cáo tổng hợp ngay: bước nào lâu, bước nào đắt                            |
| Một lần chạy bị lỗi, vì sao?                  | Chỉ có 1 dòng lỗi ngắn gọn, không rõ nguyên nhân | Xem lại đúng nội dung đã hỏi AI và AI đã trả lời gì lúc đó                               |
| Lỗi nào hay lặp lại nhất?                     | Không tổng hợp được                              | Mỗi lỗi được ghi rõ loại lỗi, mức độ nghiêm trọng, có thể gộp lại xem cái nào hay xảy ra |
| Con người đã can thiệp bao nhiêu lần, vì sao? | Không ghi gì cả                                  | Ghi đầy đủ từng lần, kèm lý do                                                           |

Ba loại dữ liệu này chạy song song và có thể đối chiếu qua lại với nhau (cùng một sự kiện thì log, trace, metric đều nhìn thấy). Cụ thể từng phần:

### 3.1. Traces — Dòng thời gian dạng cây

**Trả lời:** bước nào đang nằm trong bước lớn nào, chạy trong bao lâu, và nếu có thử lại hoặc chờ con người duyệt thì việc đó xảy ra ở đâu trong luồng chạy.

Mỗi bước (ví dụ "requirement", "codegen") là một nhánh trên cây. Nếu bước đó thử lại nhiều lần, số lần thử được cộng dồn ngay trên nhánh đó chứ không tách rời — nhìn vào là biết bước này thử mấy lần mới qua hoặc mới bỏ cuộc. Nếu bước cần con người duyệt, việc duyệt đó nằm lồng bên trong nhánh của bước, kèm thông tin: đồng ý hay từ chối, có mấy vấn đề được nêu ra, có yêu cầu sửa lại không. Khi một nhánh kết thúc, hệ thống ghi lại luôn: mất bao lâu, tốn bao nhiêu tiền, thử mấy lần, kết quả cuối là qua hay fail.

Dữ liệu này được ghi theo chuẩn phổ biến (OpenTelemetry), nên sau này nếu muốn xem bằng công cụ trực quan (Jaeger, Tempo...) thì chỉ cần đổi nơi xuất dữ liệu, không phải viết lại phần sinh ra trace.

### 3.2. Metrics — Con số thống kê tổng hợp

**Trả lời:** các câu hỏi kiểu tổng quan — "trung bình bước này mất bao lâu", "tỷ lệ fail bao nhiêu phần trăm", "tốn bao nhiêu tiền rồi" — mà không cần đọc từng dòng log.

Có 3 con số được theo dõi cho mỗi bước:

- **Thời gian chạy** của từng bước, để tính ra trung bình hoặc trường hợp chạy lâu nhất.
- **Số lần hoàn tất / số lần fail** của từng bước.
- **Chi phí** (USD) đã tốn cho từng bước.

Các con số này được cập nhật liên tục trong lúc chạy (vài giây một lần) và ghi ra một file riêng, tách biệt với file trace. Đây là số liệu của **một lần chạy**; muốn xem gộp **nhiều lần chạy** trước đó (ví dụ trung bình xuyên suốt cả tháng) thì dùng lệnh `stats` ở mục 4 — lệnh này gom lại từ file nhật ký chi tiết (mục 3.3), không phải từ file này.

### 3.3. Logs — Nhật ký chi tiết từng sự kiện

**Trả lời:** chính xác thì lúc đó đã hỏi AI câu gì, AI trả lời gì, và ai đã quyết định gì — mức chi tiết sâu nhất, dùng khi cần debug một trường hợp cụ thể.

Có hai loại file song song:

- **Một file đọc được bằng mắt thường** — dạng văn bản bình thường, vừa hiện ra màn hình vừa lưu vào file, dùng để theo dõi trực tiếp khi đang chạy.
- **Một file dạng dữ liệu (mỗi dòng 1 sự kiện)** — để máy đọc lại và tổng hợp. Mỗi dòng đều gắn kèm thời gian và có thể nối ngược lại đúng vị trí trên cây trace ở mục 3.1. Các sự kiện chính được ghi: bước hoàn tất/thất bại (kèm thời gian, chi phí), một lần thử bị kiểm tra không đạt (kèm loại lỗi), một bước bị đánh dấu phải chạy lại (kèm lý do), và con người đồng ý/từ chối ở bước duyệt.

Riêng nội dung hỏi-đáp với AI (system prompt, câu hỏi, câu trả lời) được lưu thành **file riêng, đầy đủ, không cắt bớt** cho mỗi lần gọi — vì phần quan trọng nhất (chỉ dẫn và lỗi cần sửa) thường nằm ở cuối, nếu chỉ lưu bản rút gọn thì đúng phần đó sẽ mất trước tiên.

Toàn bộ các file dạng dữ liệu này, gộp lại qua nhiều lần chạy, chính là nguồn dữ liệu cho lệnh `stats` ở mục 4: tính ra tỷ lệ thành công, thời gian trung bình/lâu nhất, tổng chi phí, và số lần con người can thiệp của từng bước.

## 4. Code đã làm được tới đâu

### Đã làm xong

**Cuốn nhật ký chi tiết (`core/logger.py`)**
Mỗi lần chạy pipeline sẽ có 1 file nhật ký riêng, mỗi dòng ghi lại 1 sự việc. Các sự việc được ghi:

- Mỗi lần gọi AI: nội dung đã hỏi, AI trả lời gì, tốn bao nhiêu tiền, mất bao lâu.
- Mỗi lần kiểm tra kết quả bị fail: lỗi gì, mức độ nghiêm trọng.
- Mỗi lần hỏi ý kiến con người: đồng ý hay từ chối, có yêu cầu sửa gì.
- Mỗi lần một bước bị đánh dấu "phải chạy lại": chạy lại bước nào, kéo theo những bước nào khác, vì lý do gì (con người từ chối / tự động sửa lỗi / thao tác thủ công).

**Dòng thời gian + con số thống kê (`core/tracing.py`, `core/orchestrator.py`)**
Dựng cây "bước lớn chứa bước nhỏ": toàn bộ 1 lần chạy → từng bước riêng lẻ → từng lần hỏi ý kiến con người. Mỗi bước ghi lại: model AI nào được dùng, mất bao lâu, tốn bao nhiêu tiền, pass hay fail. Có thêm 3 con số thống kê tự động cộng dồn: thời gian chạy mỗi bước, số lần thành công/thất bại, tổng chi phí.

**Lệnh xem báo cáo tổng hợp (`stats`)**
Gom tất cả các lần chạy trước đó lại, tính ra: bước nào trung bình lâu nhất, bước nào hay fail nhất, tổng chi phí, số lần con người can thiệp — mà không cần đọc từng file nhật ký thủ công.

### Còn thiếu / hướng làm tiếp theo

- **Chưa có nơi lưu trữ tập trung để xem** — hiện dữ liệu chỉ ghi ra file để chứng minh đúng cấu trúc, chưa có màn hình trực quan thật sự.
- Chưa ghi được "lý do AI dừng trả lời" (hết giới hạn, tự kết thúc, hay bị chặn) — cần kiểm tra thêm khi chạy thật với AI thật.
- Test toàn bộ lớp

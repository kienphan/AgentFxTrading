# 🤖 Chiến Thuật: Asian Range Judas Sweep AI Bot

Tài liệu thiết kế kiến trúc và chiến thuật giao dịch cho **Asian Range Judas Sweep AI Bot** (Săn Quét Thanh Khoản Phiên Á - ICT Judas Swing kết hợp AI Agent Sniper Mode trên cTrader 5.x Native).

---

## 1. 🎯 Tổng Quan & Triết Lý Chiến Thuật
- **Tên cBot**: `Asian Range Judas Sweep AI Bot`
- **Cặp giao dịch mục tiêu**: `XAUUSD` (Vàng)
- **Khung thời gian**: `M15` (Dữ liệu phiên Á: `00:00 - 06:00 UTC`, Phiên săn lệnh: London `07:00 - 10:00 UTC` & New York Overlap `12:30 - 16:00 UTC`)
- **Triết lý giao dịch**:
  - Tận dụng đặc tính nén tích lũy của Vàng trong phiên Á (`Asian Range`).
  - Khi bước vào phiên London hoặc New York, dòng tiền tạo lập (Smart Money) thường tạo ra các đợt phá vỡ giả mạo (**Judas Swing / Liquidity Sweep**) quét qua đỉnh/đáy phiên Á nhằm kích hoạt các lệnh chờ Breakout của retail trader.
  - cBot phát hiện thời điểm giá quét thanh khoản rồi rút râu đóng nến quay trở lại range, mở **Cổng Lọc Trước (Pre-filter Gate)** kích hoạt AI Agent phân tích hành vi nến (Pinbar / Fakeout / Order Block) để vào lệnh Sniper đảo chiều với tỷ lệ Risk:Reward vượt trội.

---

## 2. 📊 Hệ Thống Phân Tích Kỹ Thuật (TA Engine)
- **Theo Dõi Phiên Á (`TrackAsianSession`)**:
  - Ghi nhận mức giá cao nhất (`Asian High`), thấp nhất (`Asian Low`) và biên độ pips (`Asian Range`) trong khoảng `00:00 – 06:00 UTC`.
  - Tự động vẽ các đường biên ngang trực quan trên Chart (`if (Chart != null)`).
- **Khung Giờ Vàng (Golden Killzones)**:
  - **London Open Killzone**: `07:00 – 10:00 UTC` (Thời điểm săn quét thanh khoản mạnh nhất).
  - **New York Overlap Killzone**: `12:30 – 16:00 UTC` (Thời điểm dòng tiền Mỹ gia nhập).
- **Điều Kiện Kích Hoạt Cổng Lọc (Gate Triggers)**:
  - **SELL Gate (`JUDAS_SWEEP_SELL`)**: Trong Killzone, nến M15 tạo râu vượt qua `Asian High + sweepBufferPips (15 pips)`, nhưng đóng nến trở lại bên dưới `Asian High`.
  - **BUY Gate (`JUDAS_SWEEP_BUY`)**: Trong Killzone, nến M15 tạo râu nhúng sâu dưới `Asian Low - sweepBufferPips (15 pips)`, nhưng đóng nến trở lại bên trên `Asian Low`.
  - **MANAGE_ONLY**: Ngoài các khung giờ Killzone hoặc khi không có hiện tượng quét thanh khoản.

---

## 3. 🤖 Tích Hợp Gemini / Qwen AI Agent
- **Truyền Ngữ Cảnh Chuyên Biệt Trong Prompt**:
  - Cung cấp dữ liệu phiên Á: `Asian High`, `Asian Low`, `Asian Range (pips)`, `Active Killzone Window`.
  - Cung cấp 50 nến OHLCV gần nhất, chỉ báo ATR(14) theo pips, Fast/Slow EMA, RSI và Lịch sử 5 lệnh gần nhất (24h).
- **Quy Tắc SMC Của AI**:
  - Xác nhận nến quét thanh khoản (Liquidity Sweep) và vùng Order Block / Fair Value Gap (FVG) hợp lệ.
  - Đưa ra quyết định: `BUY`, `SELL`, `HOLD`, `ADJUST`, `CLOSE_ALL` (Ngưỡng tin cậy tối thiểu `70.0%`).
  - Xác định Stop Loss (SL) sau râu nến quét (sàn tối thiểu `AiSlMinFloorPips = 200.0` pips) và Take Profit (TP) tại biên đối diện của phiên Á.

---

## 4. 🛡️ Quản Lý Vốn & Rủi Ro Cấp Thể Chế
- **Volume Authority**: Quyền kiểm soát khối lượng hoàn toàn thuộc về cBot Risk Engine nội bộ (`CalculateSLTP`).
- **Sàn Stop Loss Tối Thiểu (`AiSlMinFloorPips = 200.0 pips`)**: Chống bị quét bởi spread/noise biến động mạnh trên XAUUSD.
- **Dời Hòa Vốn (Break-Even)**: Tự động dời SL về `Entry + 2 pips` khi lợi nhuận đạt `breakEvenTrigger (250 pips)`.
- **Ngắt Mạch Bảo Vệ Vốn (High-Watermark Circuit Breaker)**: Giảm 50% rủi ro khi mức sụt giảm `Drawdown >= 15%`.

---

## 5. 📰 Bộ Lọc Tin Tức (ForexFactory News Filter)
- Tự động tải lịch kinh tế từ ForexFactory (JSON feed, XML fallback).
- Tạm dừng mở vị thế trước và sau `30 phút` đối với tin tức High Impact của đồng USD.

---

## 6. 📋 Bảng Tham Số Cấu Hình Chuẩn (Parameter Reference)

| Tham Số (Parameter) | Kiểu Dữ Liệu | Giá Trị Mặc Định | Mô Tả & Khuyến Nghị Tối Ưu |
| :--- | :---: | :---: | :--- |
| `UseDirectAiApi` | `bool` | `false` | `false` = Local Server Hub (`127.0.0.1:8181`), `true` = Direct Cloud API |
| `UseAiGateMode` | `bool` | `true` | Cổng lọc 2 tầng: Judas Sweep định hướng → AI chọn điểm vào chính xác |
| `AiSlMinFloorPips` | `double` | `200.0` | Sàn bảo vệ SL tối thiểu (pips, $2.00 USD cho Vàng) |
| `asianStartHour` | `int` | `0` | Giờ bắt đầu phiên Á (UTC Hour) |
| `asianEndHour` | `int` | `6` | Giờ kết thúc phiên Á (UTC Hour) |
| `minAsianRangePips` | `double` | `50.0` | Biên độ phiên Á tối thiểu để coi là hợp lệ |
| `maxAsianRangePips` | `double` | `350.0` | Biên độ phiên Á tối đa (tránh các ngày phiên Á đã chạy sóng quá dài) |
| `londonStartHour` | `int` | `7` | Giờ bắt đầu London Killzone (UTC) |
| `londonEndHour` | `int` | `10` | Giờ kết thúc London Killzone (UTC) |
| `nyStartHour` | `int` | `12` | Giờ bắt đầu New York Overlap Killzone (UTC) |
| `nyEndHour` | `int` | `16` | Giờ kết thúc New York Overlap Killzone (UTC) |
| `sweepBufferPips` | `double` | `15.0` | Biên độ quét râu tối thiểu vượt đỉnh/đáy Á (pips) |
| `drawAsianRangeVisuals` | `bool` | `true` | Vẽ đường biên High/Low phiên Á trực quan lên biểu đồ |
| `riskFactor` | `double` | `10.0` | Tỷ lệ rủi ro (%) tài khoản trên mỗi lệnh |
| `enableBreakEvenPrice` | `bool` | `false` | Dời SL về hòa vốn khi đạt mục tiêu |
| `breakEvenTrigger` | `double` | `250.0` | Điểm kích hoạt hòa vốn (pips) |

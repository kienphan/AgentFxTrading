# Report template (write it in Vietnamese)

Save it to `docs/audits/<DAY>-daily-check.md`. Keep the sections in this order and drop any
section that would be empty. Keep the prose short; the evidence goes in the tables.

```markdown
# Daily check <DAY> (GMT+7)<, đến HH:MM nếu mode today>

> Chỉ đọc: không sửa code, DB hay VPS. Collector: `.claude/skills/daily-audit/scripts/collect_day.py <mode>`.

## 0. Phạm vi

| Mục | Giá trị |
|---|---|
| Cửa sổ | <DAY> 00:00–24:00 GMT+7 = <UTC start> → <UTC end> UTC |
| Code | VPS `<sha>` · local `<sha>` · origin/main `<sha>`; commit/deploy trong ngày: … |
| Lệnh | <n> đóng, <n> mở mới, <n> còn mở cuối ngày (+<n> lệnh ngoài sổ nếu có) |
| P&L | DB: <x> $ (lệnh đóng trong cửa sổ). Nếu biết balance đầu ngày: Δ balance <x> $ (nguồn), phần chênh do partial trên lệnh còn mở / lệnh ngoài sổ |
| Tài khoản | balance <x>, equity <x>; **ledger (balance − Σpnl) = <x>** (dòng này để lần chạy sau so sánh) |
| Test local | pytest: <passed/failed> |

## 1. Tóm tắt

- 3–6 gạch đầu dòng: điều quan trọng nhất trước.

| # | Mức | Vấn đề | Bằng chứng chính | Code | Trạng thái |
|---|---|---|---|---|---|
| 1 | P0 | … | id/ctrader_id/log line | file:line | mới / đã biết (<report>) / tái phát |

Mức: **P0** mất tiền hoặc vượt hạn mức rủi ro · **P1** sai logic giao dịch hay số liệu ra quyết định ·
**P2** sai trong điều kiện cụ thể (restart, mất mạng, fallback) · **P3** chất lượng/hiệu năng/vận hành nhẹ.

## 2. Chi tiết

### <#>. <Vấn đề>
- **Bằng chứng:** dòng DB / log (có giờ GMT+7) / số liệu.
- **Nguyên nhân:** cơ chế, `file:line` tại commit VPS.
- **Ảnh hưởng:** bao nhiêu bot/lệnh, tiền, tần suất.
- **Hướng sửa:** đề xuất; nếu cần sửa DB thì kèm SQL để user tự chạy.

## 3. Thống kê lệnh trong ngày

Theo chiến lược (số lệnh, thắng, P&L), theo lý do đóng, danh sách lệnh còn mở cuối ngày
(SL/TP, rủi ro tại SL, tuổi lệnh).

## 4. Đã xem, không phải bug

Nhiễu đã loại và lý do (vd. WebSocket reconnect trùng giờ restart).

## 5. Tái hiện

Lệnh collector và các truy vấn/grep follow-up đã dùng.
```

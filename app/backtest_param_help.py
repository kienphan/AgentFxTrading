"""
Help text behind the "?" icon of each parameter on the Backtest page, per strategy and cBot PropertyName.

Written from what cBot/FlowRsiBot.cs does with each parameter, not from its label: a few are declared
but never read (EnablePremiumDiscountFilter, AtrTpMultiplier, FixedTpPips) or only read on the AI path a
backtest turns off (MaxPositionsAllowed), and their text says so. Plain text, one paragraph per line;
the page escapes it. tests/test_backtest_param_help.py fails when a parameter the form shows has none.
"""
from typing import Dict, Optional

_NOT_READ = "⚠ Hiện cBot KHÔNG đọc tham số này — đổi giá trị không ảnh hưởng kết quả backtest."

FLOWRSI_HELP: Dict[str, str] = {
    # --- General ---------------------------------------------------------------------------------
    "ShowLogs": (
        "In thêm log chi tiết của bot: tín hiệu bị bộ lọc Macro TMS chặn, spread quá rộng, dời SL, lỗi...\n"
        "Không ảnh hưởng đến lệnh hay kết quả backtest, chỉ thay đổi lượng log. Bật khi cần tìm hiểu vì sao "
        "một tín hiệu không thành lệnh."),

    # --- Nested RSI Engine -----------------------------------------------------------------------
    "FastRsiPeriod": (
        "Chu kỳ của RSI nhanh, tính trên giá đóng cửa của khung bot đang chạy. Tín hiệu gốc của chiến lược "
        "là lúc RSI nhanh cắt RSI chậm.\n"
        "Nhỏ hơn → RSI nhạy hơn, cắt nhau thường xuyên hơn: nhiều tín hiệu hơn nhưng nhiều nhiễu hơn. "
        "Lớn hơn → ít tín hiệu và chậm hơn.\n"
        "Phải nhỏ hơn Slow RSI Period, nếu không hai đường đổi vai trò và tín hiệu mất ý nghĩa."),
    "SlowRsiPeriod": (
        "Chu kỳ của RSI chậm, đường nền mà RSI nhanh cắt qua. Giá trị RSI chậm tại nến có giao cắt cũng là "
        "giá trị được so với các ngưỡng Bullish/Bearish Cross Min/Max.\n"
        "Lớn hơn → đường nền mượt, ít dao động quanh 50, nên ít giao cắt lọt vào các ngưỡng; nhỏ hơn → "
        "nhiều tín hiệu hơn."),
    "RsiCrossLookbackBars": (
        "Số nến gần nhất (tính cả nến vừa đóng) mà bot tìm giao cắt RSI. Với 3, một giao cắt xảy ra 1–2 nến "
        "trước vẫn là tín hiệu, miễn ở nến vừa đóng RSI nhanh vẫn nằm đúng phía (trên RSI chậm cho BUY, "
        "dưới cho SELL).\n"
        "Tăng → bắt thêm các lệnh mà bộ lọc SMC chỉ thỏa vài nến sau giao cắt, nhưng vào lệnh muộn hơn. "
        "1 = chỉ tính giao cắt ở đúng nến vừa đóng.\n"
        "Cửa sổ này cũng được dùng để dò Liquidity Sweep."),
    "RsiBullishCrossMin": (
        "Cận dưới cho tín hiệu BUY: giao cắt lên (RSI nhanh vượt lên RSI chậm) chỉ được tính khi RSI chậm "
        "tại nến đó ≥ giá trị này.\n"
        "Dùng để bỏ qua các giao cắt khi giá còn đang rơi quá sâu (quá bán cực độ, dễ giảm tiếp). Giảm → "
        "nhận cả tín hiệu ở vùng quá bán sâu; tăng → chỉ nhận khi RSI đã hồi lên.\n"
        "Cửa sổ BUY = [Bullish Cross Min, Bullish Cross Max], mặc định 25–50."),
    "RsiBullishCrossMax": (
        "Cận trên cho tín hiệu BUY: giao cắt lên chỉ được tính khi RSI chậm ≤ giá trị này, tức cú bật phải "
        "xuất phát từ nửa dưới (giá còn 'rẻ'), không mua đuổi khi RSI đã cao.\n"
        "Cửa sổ [Bullish Cross Min, Bullish Cross Max] càng rộng thì càng nhiều tín hiệu BUY."),
    "RsiBearishCrossMin": (
        "Cận dưới cho tín hiệu SELL: giao cắt xuống (RSI nhanh cắt xuống dưới RSI chậm) chỉ được tính khi "
        "RSI chậm ≥ giá trị này, tức cú quay đầu phải xuất phát từ nửa trên (giá đang 'đắt'), không bán "
        "đuổi khi RSI đã thấp.\n"
        "Cửa sổ SELL = [Bearish Cross Min, Bearish Cross Max], mặc định 50–75; càng rộng càng nhiều tín hiệu."),
    "RsiBearishCrossMax": (
        "Cận trên cho tín hiệu SELL: giao cắt xuống chỉ được tính khi RSI chậm ≤ giá trị này.\n"
        "Dùng để bỏ qua các giao cắt khi giá còn đang tăng quá mạnh (quá mua cực độ, dễ tăng tiếp). Tăng → "
        "nhận cả tín hiệu ở vùng quá mua sâu; giảm → chỉ nhận khi RSI đã hạ nhiệt."),

    # --- SMC & ICT Market Structure --------------------------------------------------------------
    "EnableSmcFilter": (
        "Bật: tín hiệu RSI chỉ thành lệnh khi có thêm ít nhất MỘT xác nhận cấu trúc:\n"
        "• BUY: giá ở vùng Discount, HOẶC nến vừa đóng tạo FVG tăng, HOẶC vừa quét thanh khoản đáy (SSL).\n"
        "• SELL: giá ở vùng Premium, HOẶC FVG giảm, HOẶC vừa quét thanh khoản đỉnh (BSL).\n"
        "Tắt: mọi giao cắt RSI hợp lệ đều vào lệnh (vẫn qua bộ lọc Macro TMS); các tham số FVG, Sweep và "
        "Equilibrium không còn tác dụng."),
    "SwingLookback": (
        "Xác định đỉnh/đáy swing gần nhất: giá cao nhất và thấp nhất của (giá trị này × 3) nến trước nến "
        "vừa đóng, mặc định 5 → 15 nến. Đây là đỉnh/đáy của cả cửa sổ, không phải fractal.\n"
        "Khoảng đỉnh–đáy này dùng cho vùng Premium/Discount, Liquidity Sweep, SL kiểu Technical_Swing và TP "
        "kiểu Technical_Liquidity.\n"
        "Tăng → swing rộng hơn: SL kỹ thuật xa hơn (lot nhỏ hơn), sweep hiếm hơn. Giảm → swing sát giá, "
        "SL gần hơn."),
    "EnableFvgDetection": (
        "Cho Fair Value Gap làm một xác nhận của bộ lọc SMC. FVG tăng: đáy của nến vừa đóng cao hơn đỉnh "
        "của nến trước đó 2 cây (khoảng trống giá do lực mua mạnh); FVG giảm thì ngược lại.\n"
        "Tắt → bộ lọc SMC chỉ còn Discount/Premium và Sweep, nên ít lệnh hơn. Chỉ có tác dụng khi Enable SMC "
        "Structural Filter bật."),
    "FvgMinPips": (
        "Độ rộng tối thiểu (pip) của khoảng trống để được tính là FVG.\n"
        "Tăng → chỉ nhận FVG lớn (xung lực mạnh), ít lệnh hơn; giảm → cả khoảng trống nhỏ cũng được tính.\n"
        "Pip tính theo từng symbol: với vàng và chỉ số, 1 pip rất nhỏ so với biến động giá, nên cần chỉnh "
        "riêng cho từng symbol."),
    "EnablePremiumDiscountFilter": (
        f"{_NOT_READ}\n"
        "Vùng Premium/Discount luôn được tính và luôn là một xác nhận của bộ lọc SMC (khi Enable SMC "
        "Structural Filter bật). Muốn thay đổi vùng này, hãy chỉnh Equilibrium Threshold."),
    "EquilibriumThreshold": (
        "Ranh giới vùng Discount/Premium trong khoảng đỉnh–đáy swing (xem Swing Lookback):\n"
        "• Discount (cho BUY): giá đóng cửa ≤ đáy + range × giá trị này.\n"
        "• Premium (cho SELL): giá đóng cửa ≥ đáy + range × (1 − giá trị này).\n"
        "0.5 = chia đôi tại điểm cân bằng. Tăng (vd 0.6) → cả hai vùng rộng ra, dễ thỏa hơn, nhiều lệnh hơn; "
        "giảm (vd 0.4) → chỉ mua ở vùng thật rẻ, chỉ bán ở vùng thật đắt."),
    "EnableLiquiditySweepFilter": (
        "Cho 'quét thanh khoản' làm một xác nhận của bộ lọc SMC: một nến trong cửa sổ RSI Cross Lookback "
        "chọc thủng đáy swing rồi đóng cửa lại phía trên (quét SSL → ủng hộ BUY), hoặc chọc thủng đỉnh swing "
        "rồi đóng cửa lại phía dưới (quét BSL → ủng hộ SELL).\n"
        "Tắt → bộ lọc SMC chỉ còn Discount/Premium và FVG, ít lệnh hơn. Chỉ có tác dụng khi Enable SMC "
        "Structural Filter bật."),

    # --- Macro TMS Trend Filter ------------------------------------------------------------------
    "EnableMacroTmsFilter": (
        "Bộ lọc xu hướng khung lớn: tính bias BULLISH / BEARISH / NEUTRAL trên Macro TimeFrame và chặn lệnh "
        "ngược xu hướng: BUY bị hủy khi bias BEARISH, SELL bị hủy khi bias BULLISH, NEUTRAL cho qua cả hai.\n"
        "Tắt → vào lệnh cả hai chiều bất kể xu hướng lớn; mọi tham số Macro bên dưới không còn tác dụng."),
    "MacroTimeFrame": (
        "Khung thời gian dùng để tính xu hướng lớn, mặc định H1. Nên lớn hơn khung bot đang chạy (vd bot M15 "
        "→ H1 hoặc H4).\n"
        "Khung lớn hơn → xu hướng ổn định, ít đổi chiều nhưng phản ứng chậm khi thị trường đảo chiều.\n"
        "Cần ít nhất (Macro RSI + Macro Red + 35) nến của khung này trước khi có bias; trước đó bias là "
        "NEUTRAL, không chặn lệnh nào."),
    "MinTdiFlipSeparation": (
        "Chống đổi chiều giả: khi xuất hiện giao cắt ngược với bias đang giữ, bias chỉ được đổi nếu khoảng "
        "cách giữa Macro RSI (đường xanh TDI) và đường Red ≥ giá trị này (điểm RSI).\n"
        "Tăng → đòi giao cắt dứt khoát hơn, bias đổi ít và chậm hơn; 0 = không yêu cầu khoảng cách."),
    "MinBarsBetweenFlips": (
        "Số nến khung macro tối thiểu giữa hai lần đổi chiều bias. Giao cắt ngược chiều đến sớm hơn khoảng "
        "này bị bỏ qua, bias giữ nguyên.\n"
        "Tăng → bias 'lì' hơn, tránh lật qua lật lại khi thị trường đi ngang; giảm → bám sát từng lần đổi chiều."),
    "MaxMacroLockAgeBars": (
        "Tuổi tối đa (tính bằng nến khung macro) của giao cắt đang giữ bias. Quá tuổi này, bias lấy theo EMA "
        "(xem Macro EMA Period) thay vì giao cắt cũ, tránh giữ BULLISH hàng chục nến trong khi giá đã rơi.\n"
        "Giảm → chuyển sang EMA sớm hơn. 0 = tắt: giữ bias theo giao cắt cho đến khi có giao cắt ngược lại."),
    "MacroEmaPeriod": (
        "Chu kỳ EMA trên khung macro, dùng khi chưa có giao cắt nào được xác nhận hoặc giao cắt đã quá Max "
        "Lock Age:\n"
        "• BULLISH: giá đóng > EMA và EMA đang dốc lên (so với 3 nến trước).\n"
        "• BEARISH: giá đóng < EMA và EMA đang dốc xuống.\n"
        "• Còn lại: NEUTRAL, không chặn lệnh.\n"
        "EMA dài hơn → xu hướng chậm và ổn định; ngắn hơn → nhạy hơn."),
    "MacroRsiPeriod": (
        "Chu kỳ RSI (đường xanh của TDI) tính trên giá đóng Heikin Ashi của khung macro. Bias BULLISH được "
        "khóa khi RSI này cắt lên đường Red, kèm nến Heikin Ashi tăng và Stoch %K > %D; BEARISH thì ngược lại.\n"
        "Nhỏ hơn → nhạy, bias đổi thường xuyên hơn. Mặc định 6, giống bot TMS+ORB để hai bot hiểu xu hướng "
        "như nhau."),
    "MacroRedPeriod": (
        "Chu kỳ đường Red (đường tín hiệu) của TDI: trung bình đơn giản (SMA) của Macro RSI qua số nến này. "
        "Giao cắt giữa Macro RSI và đường Red quyết định bias.\n"
        "Lớn hơn → đường Red mượt hơn, giao cắt ít hơn nhưng chắc chắn hơn."),
    "MacroStochKPeriod": (
        "Số nến dùng để tính Stochastic %K trên Heikin Ashi khung macro. Stochastic là xác nhận thứ ba của "
        "một giao cắt: BULLISH cần %K > %D, BEARISH cần %K < %D tại nến cắt.\n"
        "Nhỏ hơn → Stochastic nhạy hơn."),
    "MacroStochDPeriod": (
        "Số nến làm mượt %K thành đường %D (SMA của %K). %D là đường so sánh trong điều kiện xác nhận "
        "%K > %D (BULLISH) hoặc %K < %D (BEARISH).\n"
        "Lớn hơn → %D chậm và mượt hơn."),
    "MacroStochSlowing": (
        "Hệ số làm chậm: %K là trung bình của %K thô qua số nến này, trước khi tính %D.\n"
        "Lớn hơn → Stochastic mượt hơn, ít nhiễu nhưng phản ứng chậm hơn."),

    # --- Risk Management -------------------------------------------------------------------------
    "RiskPercentage": (
        "Rủi ro mỗi lệnh tính bằng % equity hiện tại. Lot được tính để nếu chạm SL thì lỗ đúng số tiền này: "
        "lot = tiền rủi ro ÷ (khoảng SL tính bằng pip × giá trị 1 pip).\n"
        "Số tiền này bị chặn bởi Max Dollar Risk Per Trade. Ví dụ balance 10.000 $ × 0,5% = 50 $, vừa bằng "
        "trần mặc định 50 $: tăng % mà không tăng trần thì lot không đổi."),
    "MaxRiskPerTradeMoney": (
        "Trần tuyệt đối ($) cho tiền rủi ro mỗi lệnh: rủi ro thực = min(% equity, trần này).\n"
        "Nếu ngay cả lot nhỏ nhất của broker với SL hiện tại đã rủi ro vượt trần thì lệnh bị TỪ CHỐI (hay gặp "
        "với chỉ số, vàng, SL rộng).\n"
        "Backtest với balance lớn: nhớ tăng trần nếu muốn lot tăng theo vốn."),
    "MaxMinLotRiskMultiple": (
        "Bỏ lệnh khi lot nhỏ nhất của broker làm rủi ro vượt (rủi ro mục tiêu × hệ số này). Ví dụ mục tiêu "
        "50 $, hệ số 1.5 → lot nhỏ nhất đã rủi ro hơn 75 $ thì bỏ lệnh.\n"
        "Xảy ra khi SL rất rộng so với vốn (thường là chỉ số, vàng). Tăng → chấp nhận vượt mục tiêu nhiều "
        "hơn; 0 = tắt kiểm tra này (trần Max Dollar Risk vẫn áp dụng)."),
    "SlMode": (
        "Cách đặt Stop Loss:\n"
        "• Technical_Swing: dưới đáy swing gần nhất − 0,5 × ATR (BUY), trên đỉnh swing + 0,5 × ATR (SELL).\n"
        "• ATR_Multiplier: cách giá ATR × ATR SL Multiplier.\n"
        "• Fixed_Pips: cách giá đúng Fixed SL Distance.\n"
        "Sau đó SL luôn được nới ra tối thiểu bằng sàn SL (xem Min SL Floor). SL xa hơn → lot nhỏ hơn (rủi "
        "ro $ giữ nguyên) và TP cũng xa hơn theo R:R."),
    "TpMode": (
        "Cách đặt Take Profit:\n"
        "• Risk_Reward_Ratio: TP = khoảng SL × Target Risk-to-Reward Ratio.\n"
        "• Technical_Liquidity: như trên, nhưng nếu đỉnh swing (BUY) / đáy swing (SELL) nằm xa hơn thì dời TP "
        "ra đó; không bao giờ gần hơn mức R:R.\n"
        "• Fixed_Pips: ⚠ cBot chưa xử lý chế độ này, chạy y như Risk_Reward_Ratio."),
    "AtrPeriod": (
        "Chu kỳ ATR (biên độ dao động trung bình) trên khung bot đang chạy. ATR được dùng cho SL kiểu "
        "ATR_Multiplier, đệm 0,5 × ATR của SL Technical_Swing, và sàn SL tối thiểu = 1 × ATR.\n"
        "Nhỏ hơn → ATR phản ứng nhanh với biến động gần đây; lớn hơn → ổn định hơn."),
    "AtrSlMultiplier": (
        "Hệ số nhân ATR cho SL: khoảng SL = ATR × hệ số. Dùng khi Stop Loss Mode = ATR_Multiplier, và làm SL "
        "dự phòng của Technical_Swing khi không có swing hợp lệ.\n"
        "Lớn hơn → SL xa, ít bị quét nhưng lot nhỏ hơn và TP xa hơn."),
    "AtrTpMultiplier": (
        f"{_NOT_READ}\n"
        "TP luôn tính theo Target Risk-to-Reward Ratio (và swing khi chọn Technical_Liquidity)."),
    "TargetRiskReward": (
        "Tỷ lệ lời/rủi ro mục tiêu: TP = khoảng SL × giá trị này (1.5 → chạm TP lời 1,5R, với R là khoảng "
        "SL ban đầu).\n"
        "Tăng → lệnh thắng lời nhiều hơn nhưng tỷ lệ thắng thường giảm.\n"
        "Nên giữ Trailing Stop Trigger (R:R) nhỏ hơn giá trị này; nếu không, bot tự hạ ngưỡng trailing xuống "
        "80% R:R của TP."),
    "MinSlFloorPips": (
        "Khoảng SL tối thiểu (pip). SL tính theo bất kỳ Stop Loss Mode nào mà gần hơn sẽ được nới ra, và TP "
        "nới theo để giữ R:R.\n"
        "Sàn thực tế = lớn nhất của: giá trị này, 1 × ATR, và mức cứng theo symbol (JPY ≥ 18 pip; vàng ≥ 800 "
        "pip ở khung ≤ M5, ≥ 1500 pip ở khung lớn hơn).\n"
        "Tăng → SL rộng hơn, lot nhỏ hơn."),
    "FixedSlPips": (
        "Khoảng SL cố định (pip), chỉ dùng khi Stop Loss Mode = Fixed_Pips. Vẫn được nới ra nếu nhỏ hơn sàn "
        "SL (Min SL Floor, 1 × ATR, mức cứng theo symbol)."),
    "FixedTpPips": (
        f"{_NOT_READ}\n"
        "Kể cả khi Take Profit Mode = Fixed_Pips, TP vẫn tính theo Target Risk-to-Reward Ratio."),
    "MaxSpreadPips": (
        "Bỏ qua tín hiệu nếu spread lúc đóng nến lớn hơn giá trị này (pip).\n"
        "Backtest dữ liệu m1 dùng spread cố định ở ô Spread (pips) của form: nếu nó lớn hơn giá trị này thì "
        "bot không vào lệnh nào. Dữ liệu tick dùng spread lịch sử thật."),
    "MaxPositionsAllowed": (
        "Số lệnh mở đồng thời tối đa, chỉ được kiểm tra khi lệnh đi qua AI gate (bot chạy thật).\n"
        "⚠ Backtest tắt AI gate, và khi đó bot chỉ vào lệnh lúc chưa có lệnh nào mở, nên luôn tối đa 1 lệnh: "
        "đổi tham số này không ảnh hưởng kết quả backtest."),
    "EnableHighWatermarkCut": (
        "Cầu dao ngắt theo ngày: theo dõi equity cao nhất trong ngày (UTC). Khi equity sụt từ đỉnh đó ≥ Max "
        "Daily Drawdown Threshold, bot đóng mọi lệnh đang mở và không vào lệnh mới đến hết ngày; sang ngày "
        "mới tự hoạt động lại.\n"
        "Mặc định tắt."),
    "HighWatermarkCutThreshold": (
        "Mức sụt (%) tính từ đỉnh equity trong ngày để kích hoạt cầu dao. Chỉ có tác dụng khi Enable "
        "High-Watermark Circuit Breaker bật.\n"
        "Nhỏ hơn → ngắt sớm, bảo vệ vốn chặt hơn nhưng dễ cắt mất các lệnh đang hồi."),

    # --- Position Protection ---------------------------------------------------------------------
    "EnableBreakEven": (
        "Khi lệnh đủ lời (theo Break-Even Trigger Mode), dời SL về 'hòa vốn thật': giá vào + phí (commission "
        "hai chiều + swap âm, quy ra pip) + Zero-Loss Safety Buffer. Chạm SL lúc này vẫn không lỗ sau phí.\n"
        "Chỉ dời một lần. Partial Close cũng chỉ chạy đúng lúc dời BE này."),
    "BeMode": (
        "Cách xác định lúc dời SL về hòa vốn:\n"
        "• Risk_Reward_Ratio: khi lời ≥ Break-Even Trigger (R:R) × khoảng SL ban đầu.\n"
        "• Fixed_Pips: khi lời ≥ Break-Even Trigger (pips).\n"
        "Cả hai chế độ đều phải đạt thêm Min Break-Even Distance."),
    "BreakEvenTriggerRr": (
        "Mức lời, tính bằng R (bội số của khoảng SL ban đầu), để dời SL về hòa vốn. Chỉ dùng khi Break-Even "
        "Trigger Mode = Risk_Reward_Ratio; 1.0 = lời bằng đúng khoảng SL.\n"
        "Nhỏ hơn → bảo vệ sớm nhưng dễ bị quét về hòa vốn rồi giá mới chạy; lớn hơn → cho lệnh nhiều không "
        "gian hơn nhưng lệnh đang lời có thể quay về lỗ."),
    "BreakEvenTriggerPips": (
        "Mức lời (pip) để dời SL về hòa vốn, chỉ dùng khi Break-Even Trigger Mode = Fixed_Pips. Mức thực tế "
        "không nhỏ hơn Min Break-Even Distance."),
    "MinBreakEvenPips": (
        "Số pip lời tối thiểu trước khi được dời SL về hòa vốn, áp dụng cho cả hai chế độ, để không dời BE "
        "quá sớm khi SL ban đầu rất hẹp.\n"
        "Mức thực tế còn được nâng theo symbol: JPY ≥ 15 pip; vàng ≥ 500 pip ở khung ≤ M5, ≥ 1000 pip ở "
        "khung lớn hơn."),
    "BreakEvenExtraPips": (
        "Số pip cộng thêm trên mức hòa vốn-sau-phí khi dời SL, để bù trượt giá lúc SL khớp.\n"
        "Mức này cũng là sàn của trailing stop: SL trailing không bao giờ kém hơn giá vào + phí + buffer."),
    "EnableTrailingStop": (
        "Bật trailing stop: khi lời đạt Trailing Stop Trigger (R:R), SL bắt đầu bám theo giá, chỉ dời theo "
        "chiều có lợi và theo từng bước (khoảng 10% khoảng trailing) chứ không dời mỗi tick.\n"
        "Chạy độc lập với Break-Even; SL trailing luôn ít nhất ở mức hòa vốn-sau-phí."),
    "TrailingStopTriggerRr": (
        "Mức lời (R) để bắt đầu trailing. Nếu giá trị này ≥ R:R của TP (lệnh chạm TP trước khi trailing kịp "
        "chạy), bot tự hạ ngưỡng xuống 80% R:R của TP.\n"
        "Nên đặt giữa Break-Even Trigger (1.0) và Target Risk-to-Reward (1.5). Nhỏ hơn → khóa lời sớm; lớn "
        "hơn → ít lệnh được trailing."),
    "TrailingStopDistancePips": (
        "Khoảng cách tối thiểu (pip) giữa giá và SL khi trailing. Khoảng thực tế = lớn nhất của: giá trị này, "
        "sàn theo symbol (forex 20, JPY 25, chỉ số 350, vàng 400/800 pip), và khoảng SL ban đầu (100%, còn "
        "60% khi lời ≥ 2,5R).\n"
        "Vì vậy trailing thường bám đúng khoảng SL ban đầu; tham số này chỉ có tác dụng khi lớn hơn khoảng đó."),
    "EnablePartialClose": (
        "Đúng lúc dời SL về hòa vốn, chốt lời ngay một phần khối lượng (theo Partial Close Ratio); phần còn "
        "lại chạy tiếp như 'runner'.\n"
        "Chỉ hoạt động khi Enable True Break-Even bật, và khi cả phần đóng lẫn phần còn lại đều ≥ lot nhỏ "
        "nhất của broker (lệnh 0.01 lot không chia được)."),
    "PartialCloseRatio": (
        "Tỷ lệ khối lượng đóng khi chốt lời một phần: 0.5 = đóng 50%. 0 hoặc 1 = không chốt một phần.\n"
        "Lớn hơn → lời chắc chắn hơn nhưng phần runner nhỏ hơn."),
    "RemoveTpOnTrailing": (
        "Khi trailing đã chạy và lệnh đã chốt lời một phần, bỏ TP của phần còn lại để nó chạy theo xu hướng, "
        "chỉ thoát khi chạm SL trailing.\n"
        "Lệnh chưa chốt một phần (vd lot quá nhỏ không chia được) vẫn giữ TP."),
}

PARAM_HELP: Dict[str, Dict[str, str]] = {"flowrsi": FLOWRSI_HELP}


def param_help(strategy: str, key: str) -> Optional[str]:
    return PARAM_HELP.get(strategy, {}).get(key)

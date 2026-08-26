# Trung tâm tài liệu tiếng Việt

> Trạng thái: **HIỆN HÀNH** · Kiểm tra: 2026-08-26 · Mainnet: **NO-GO**

Đây là lối đọc tiếng Việt cho Trading Agent System. Tài liệu giữ nguyên tên
class, module, artifact và tham số CLI để người đọc có thể đối chiếu trực tiếp
với mã nguồn.

Nếu bản tiếng Việt và contract trong code khác nhau, contract đã được kiểm thử
trong code là nguồn quyết định. Hãy cập nhật lại cả hai bản trong cùng thay đổi.

## Quy ước trạng thái

| Nhãn tiếng Việt | Nhãn gốc | Ý nghĩa |
| --- | --- | --- |
| **HIỆN HÀNH** | `CURRENT` | Đang tồn tại trong code hoặc luồng vận hành; vẫn cần bằng chứng đi kèm |
| **MỤC TIÊU** | `TARGET` | Thiết kế hoặc backlog chưa được phép xem là năng lực đã hoàn tất |
| **LỊCH SỬ** | `HISTORICAL` | Phase, báo cáo hoặc thiết kế cũ được giữ để truy vết |

## Chọn tài liệu theo nhu cầu

| Bạn muốn làm gì? | Đọc trước | Kết quả cần đạt |
| --- | --- | --- |
| Hiểu toàn bộ hệ thống | [Vòng đời chiến lược](VONG_DOI_CHIEN_LUOC.md) | Phân biệt research, selection, promotion, runtime và execution |
| Đánh giá một strategy | [Từ nghiên cứu đến vận hành](NGHIEN_CUU_DEN_VAN_HANH.md) | Chạy đúng baseline/tournament và biết khi nào không được promote |
| Hiểu report và artifact | [Bằng chứng và artifact](BANG_CHUNG_VA_ARTIFACT.md) | Truy được data, code, params, cost và promotion identity |
| Kiểm tra luồng chính | [Runbook kiểm tra luồng chính](KIEM_TRA_LUONG_CHINH.md) | Thực hiện kiểm tra L0–L5 theo mức rủi ro |
| Tự học có thứ tự | [Khóa học V2 tiếng Việt](KHOA_HOC_V2.md) | Học theo contract và bằng chứng, không học thuộc số dòng code |
| Tra thuật ngữ | [Thuật ngữ song ngữ](THUAT_NGU.md) | Dùng thống nhất từ chuyên môn trong code và tài liệu |

## Luồng hệ thống chuẩn

```text
Dữ liệu thị trường
  ↓ kiểm tra chất lượng + đặc trưng point-in-time
Canonical strategy registry
  ↓ evaluation cell cô lập
Tournament + mô phỏng execution thực tế
  ↓ kiểm định OOS/thống kê
Selection policy + promotion artifact bất biến
  ↓ runtime resolver fail-closed
Strategy/router đang được phép chạy
  ↓ ràng buộc portfolio và risk
Order intent → lifecycle → broker → fill → reconciliation
  ↓
Attribution → monitoring → rollback → audit
```

Mỗi mũi tên là một ranh giới authority. Thiếu dữ liệu, thiếu identity hoặc thiếu
promotion hợp lệ phải dẫn đến từ chối, giảm exposure hoặc `NO_TRADE`; không được
âm thầm chọn một strategy mặc định.

## Tài liệu nguồn chuẩn liên quan

- [Cổng tài liệu chính](../README.md)
- [Kiến trúc hệ thống](../ARCHITECTURE.md)
- [Capability Matrix](../CAPABILITY_MATRIX.md)
- [Research Evidence](../RESEARCH_EVIDENCE.md)
- [Live Readiness](../LIVE_TRADING_TODO.md)
- [Quy chuẩn tài liệu](../DOCUMENTATION_STANDARD.md)

## Nguyên tắc an toàn

1. `Implemented` không đồng nghĩa `production validated`.
2. Backtest đẹp chỉ là một giả thuyết cho đến khi có OOS, stress và execution evidence.
3. Hoàn thành phase phát triển không tự động cho phép mainnet.
4. Không đưa secret, account ID hoặc broker payload riêng tư vào tài liệu.
5. Không chạy lệnh live chỉ vì nó xuất hiện trong ví dụ; luôn kiểm tra environment và approval gate.


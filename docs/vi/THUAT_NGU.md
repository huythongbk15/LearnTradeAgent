# Thuật ngữ trading systems song ngữ

> Trạng thái: **HIỆN HÀNH** · Nguồn đối chiếu: code và tài liệu contract

Tài liệu dùng tiếng Việt để giải thích, nhưng giữ thuật ngữ tiếng Anh khi đó là
tên contract hoặc khái niệm đã phổ biến trong code.

## Trạng thái và độ trưởng thành

| Thuật ngữ | Cách hiểu trong dự án |
| --- | --- |
| `CURRENT` / Hiện hành | Đã có trong code hoặc vận hành, chưa mặc nhiên an toàn production |
| `TARGET` / Mục tiêu | Thiết kế cần đạt, không phải bằng chứng hoàn thành |
| `HISTORICAL` / Lịch sử | Tài liệu cũ giữ để truy vết quyết định |
| Implemented | Đã viết code |
| Tested | Có kiểm thử tương ứng và kiểm thử đang đạt |
| Research validated | Có bằng chứng nghiên cứu đủ điều kiện đã định |
| Paper validated | Đã quan sát qua paper execution trong thời gian/mẫu yêu cầu |
| Testnet validated | Đã qua broker testnet và reconciliation tương ứng |
| Production validated | Đã qua canary/soak và được phê duyệt cho production |

## Strategy và selection

| Thuật ngữ | Giải thích |
| --- | --- |
| Strategy descriptor | Contract bất biến mô tả strategy ID, features, warmup và parameter schema |
| Canonical strategy | Strategy tuân thủ contract chung, có identity và fail-closed behavior |
| Registry | Danh sách allowlist ánh xạ identity sang implementation đã biết |
| Adapter / bridge | Lớp tương thích để đưa implementation cũ qua canonical/runtime contract |
| Warmup | Số bar lịch sử tối thiểu trước khi strategy được phép quyết định |
| `NO_TRADE` / abstain | Chủ động không giao dịch vì không đủ điều kiện hoặc bằng chứng |
| Tournament | Ma trận đánh giá strategy × pair × params × scenario/fault |
| Incumbent | Strategy/policy hiện đang được chọn và đủ điều kiện |
| Challenger | Candidate mới đang được đánh giá so với incumbent |
| Selection policy | Artifact quy định strategy nào đủ điều kiện theo pair/regime và risk limit |
| Promotion | Quyết định cho phép artifact chạy ở một environment cụ thể |
| Regime routing | Chọn strategy theo trạng thái thị trường với uncertainty và hysteresis |
| Hysteresis | Điều kiện chống chuyển strategy liên tục khi tín hiệu regime dao động |

## Backtest và thống kê

| Thuật ngữ | Giải thích |
| --- | --- |
| In-sample | Dữ liệu dùng để fit hoặc lựa chọn |
| Out-of-sample (OOS) | Dữ liệu không được dùng trực tiếp để fit candidate đó |
| Holdout | Phần dữ liệu bị khóa, chỉ mở theo policy đã định |
| Walk-forward optimization (WFO) | Đánh giá lặp theo trục thời gian với train/test tách biệt |
| Nested WFO | WFO có vòng trong để chọn và vòng ngoài để ước lượng hiệu năng |
| Multiple testing | Rủi ro tìm thấy “winner” giả khi thử quá nhiều candidate |
| Parameter stability | Kết quả không chỉ tốt tại một điểm tham số mong manh |
| Effective sample size | Lượng thông tin độc lập thực sự, không chỉ số bar danh nghĩa |
| Benchmark | Phương án tham chiếu như buy-and-hold hoặc incumbent |
| Profit factor | Gross profit chia gross loss; phải đọc cùng trade count và drawdown |
| Drawdown | Mức giảm từ đỉnh equity đến đáy tiếp theo |

## Execution và vận hành

| Thuật ngữ | Giải thích |
| --- | --- |
| Order intent | Ý định giao dịch trước khi risk/authority phê duyệt |
| Authorization | Bằng chứng cho phép order intent đi tiếp |
| Order lifecycle | Chuỗi submit, ACK, partial fill, fill, cancel hoặc reject |
| Fill ledger | Sổ cái immutable của các execution fill |
| Reconciliation | Đối chiếu state nội bộ với state từ broker/exchange |
| Protective order | Stop/protection giảm rủi ro sau khi có exposure |
| Fail closed | Thiếu dữ liệu/quyền/bằng chứng thì từ chối, không tự cho phép |
| Kill switch | Cơ chế chặn hoạt động giao dịch theo policy khẩn cấp |
| Shadow | Tạo decision nhưng không gửi order |
| Canary | Chạy phạm vi/vốn rất hạn chế để thu bằng chứng production gần thực tế |
| Soak | Chạy đủ thời gian và số lifecycle để kiểm tra ổn định |
| Reality gap | Chênh lệch giữa mô phỏng/paper và execution thực tế |

## Artifact và provenance

| Thuật ngữ | Giải thích |
| --- | --- |
| Artifact | Kết quả có schema và identity dùng làm bằng chứng giữa các stage |
| Content-addressed | ID được suy ra từ nội dung canonical; sửa nội dung làm đổi ID |
| Manifest | Mô tả dữ liệu/config/code tạo ra một run |
| Provenance | Chuỗi nguồn gốc giúp truy từ decision về code, data và approval |
| Lineage | Quan hệ cha-con giữa evaluation, selection, promotion và runtime artifact |
| Attestation | Chứng thực có chữ ký về cách artifact được tạo hoặc kiểm tra |
| Schema version | Phiên bản contract để migration có kiểm soát |


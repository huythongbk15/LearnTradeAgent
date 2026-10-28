# Thảo luận đa agent: thông tin nào giúp giao dịch altcoin?

Ngày: 27/10/2026. Trạng thái: **OPEN_FOR_DISCUSSION — chưa có quyền chạy nghiên cứu thị trường**.

## 1. Mục tiêu chung

Xây dựng một quy trình nghiên cứu đủ rộng để tìm thông tin hữu ích, nhưng đủ
chặt để chứng minh thông tin nào thực sự cải thiện quyết định cho từng altcoin
sau phí và dưới cơ chế thực thi hiện có. Kết quả mong muốn không phải danh sách
indicator dài hoặc một hệ thống mới: đó là một đề xuất nghiên cứu có thể thực
hiện, có đối chứng, ngân sách và điều kiện dừng rõ ràng.

User muốn xem xét nhiều thông tin có cơ chế liên quan, không giới hạn sáu feature.
Không mặc định mọi thông tin đều hữu ích; không ép các pair dùng cùng policy.
Policy cuối chỉ giữ những đầu vào có bằng chứng phù hợp với vai trò của chúng.

Tài liệu này là **brief và nơi ghi quyết định thảo luận**, không thay tracker,
không tự mở phase và không cấp quyền cho agent con. Người điều phối giao vai trò
khi thực sự mở phiên làm việc. Không tự sửa code, chạy campaign hoặc tải dữ liệu
chỉ vì được giao đọc tài liệu này.

## 2. Đọc gì trước và tin điều gì?

| Nguồn | Dùng để làm gì |
| --- | --- |
| [Kế hoạch nghiên cứu §10, đặc biệt §10.8](MULTI_STRATEGY_RESEARCH_PLAN.md#10-kế-hoạch-btc-context-và-policy-riêng-theo-pair) | Phạm vi BTC context, ETH/BNB, danh mục G0–G11, phases X0–X6 và phương pháp kiểm chứng |
| [Campaign status](../CAMPAIGN_STATUS.md) | Trạng thái hiện tại và quyền thực thi; đọc mục mới nhất, không lấy snapshot cũ làm hiện trạng |
| [Research evidence](../RESEARCH_EVIDENCE.md) | Kết quả đã đóng, giới hạn suy luận và các hướng đã từ chối |
| Inventory hiện có — local evidence `data/confirmation_design_20261026_01/RESULTS.md` | Metadata/exposure; không phải chứng nhận dữ liệu ETH/BNB đủ điều kiện |
| `AGENTS.md` (workspace local) | Quy tắc công cụ, an toàn, verification của workspace |

Brief dựa trên kế hoạch hiện có, **không phải một audit source mới**. Khi phát biểu
về implementation, agent phải đọc file thật và chỉ rõ revision/file:line.
Mỗi nhận định gắn một nhãn: **FACT / HYPOTHESIS / PROPOSAL / UNKNOWN**.
FACT cần nguồn trực tiếp; dẫn lại bản tổng kết của agent khác chưa đủ.

Hiện trạng dùng làm điểm xuất phát:

- Chưa có strategy qualified; mainnet NO-GO. Các phép thử BTC đã đóng không được
  mở lại hoặc đổi tên để xóa search history.
- Scope đề xuất: Binance Spot, long-only, USDT; BTC làm context, ETH và BNB là
  target nghiên cứu ban đầu; cadence 1h. Data eligibility và horizon còn phải chốt.
- Market PAUSED. Không đọc giá mới, mở holdout, tải nguồn mới hoặc chạy screening
  từ quyền thảo luận này. News metadata pilot đã DECLINED, vẫn giữ nguyên.
- Sáu feature seed là baseline, không phải giới hạn discovery. Nghiên cứu rộng
  không có nghĩa phải thu thập đủ mọi nhóm trước khi bắt đầu.

## 3. Câu hỏi trung tâm và thước đo thành công

**Thông tin nào cải thiện quyết định cho ETH/BNB ngoài lịch sử riêng của pair,
ở horizon nào, trong điều kiện nào, và có còn hữu ích sau chi phí không?**

Tách bốn mức bằng chứng: liên hệ quan sát → dự báo bổ sung → giá trị kinh tế
dưới native policy → khả năng khái quát trên confirmation hợp lệ.
Không gọi bất kỳ mức nào ở trên là nhân quả nếu thiếu thiết kế nhận diện nhân quả.
Giảm một feature khỏi model không phải can thiệp lên thị trường.

Một phiên thảo luận hoàn tất khi chốt được:

1. Một wave admission cụ thể, kèm lý do đưa vào và trì hoãn từng nhóm.
2. Câu hỏi, baseline, đối chứng và tiêu chí bác bỏ của wave đó.
3. Data gaps, phần code cần kiểm tra, chi phí và quyền cần có trước execution.
4. Bất đồng chưa giải quyết, owner và bằng chứng tối thiểu để giải quyết.

Không cần mọi agent đồng ý; không cần đưa ra một strategy mới trong phiên này.

## 4. Các vai trò và đầu ra riêng

Một người có thể kiêm vai khi ít agent, nhưng phải công bố. Reviewer không tự
review phần mình vừa thiết kế/sửa rồi gọi đó là review độc lập.

| Vai trò | Nhiệm vụ | Đầu ra ngắn bắt buộc |
| --- | --- | --- |
| Điều phối | Đọc trạng thái, khóa câu hỏi, phân công, tổng hợp bất đồng | Một decision log và đề xuất wave; không tự chọn bằng biểu quyết |
| Cơ chế thị trường | Giải thích tại sao thông tin có thể dẫn tới payoff hoặc giảm rủi ro; nêu giải thích cạnh tranh | Tối đa ba hypothesis ưu tiên, hướng tác động có điều kiện và cách bác bỏ |
| Data/PIT | Kiểm nguồn, thời điểm thực sự biết được, universe, revisions, quyền đọc và coverage từ metadata được phép | Admission matrix; UNKNOWN phải giữ UNKNOWN |
| Thống kê/phương pháp | Đối chứng, nhóm tương quan, interactions, time splits, trial burden, uncertainty và sample limits | Thiết kế phép kiểm tối thiểu, cách tránh leakage và selection bias |
| Engineering/execution | Xác định đường input → forecast → authority → fill; mức tái sử dụng code và chi phí | Gap map có file:line; không triển khai trong vòng thảo luận |
| Reviewer phản biện | Tìm giả định không được chứng minh, phép kiểm rỗng, lợi ích giả do cost/exposure/calendar | Findings theo mức chặn và một verdict có giới hạn |

Đọc nhận xét của nhau sau lượt phân tích đầu để giảm việc lặp lại cùng một giả định.
Agent không có quyền truy cập nguồn phải báo thiếu, không suy diễn rằng nguồn đã có.

## 5. Danh mục thông tin để thảo luận

Chi tiết định nghĩa và hạn chế ở §10.8 của kế hoạch; bảng này là câu hỏi phản biện.

| Nhóm | Câu hỏi phải trả lời trước khi ưu tiên |
| --- | --- |
| G0 Lịch sử riêng pair | Baseline đủ mạnh chưa; feature mới có chỉ là biến đổi của cùng trend? |
| G1 BTC context | BTC dẫn target hay cùng phản ứng; độ trễ còn khai thác được sau thời điểm fill? |
| G2 ETH/benchmark | ETH bổ sung gì cho BNB sau khi đã biết BTC? |
| G3 Quan hệ tương đối | Dự báo lợi nhuận USDT hay chỉ outperform BTC trong thị trường cùng giảm? |
| G4 Market breadth | Universe có đúng point-in-time; có nhầm ba coin với toàn thị trường? |
| G5 Thanh khoản/order flow | Có dữ liệu thực hay proxy; giúp hướng giá, chi phí hay khả năng thực thi? |
| G6 Funding/OI/basis/liquidation | Thông tin có sẵn lúc quyết định không; thêm gì ngoài price/volume? |
| G7 Dominance/cấu trúc vốn | Có market cap vintage; có nhầm thay đổi tỷ trọng với dòng tiền? |
| G8 Sự kiện riêng coin | First announcement hay lịch đã sửa; tác động đã được giá phản ánh chưa? |
| G9 On-chain/flow estimates | Độ trễ và sai attribution; transfer có bị gọi nhầm là mua/bán? |
| G10 Macro/calendar | Giá trị actual/expected nào đã biết; ngoài giờ và revision xử lý thế nào? |
| G11 News/attention | Tin dẫn giá hay tường thuật giá; hiện chỉ catalogue vì pilot bị từ chối? |

Phân biệt feature dự báo direction, risk filter, sizing input và execution input.
Không buộc mọi biến phải sinh BUY. Không cho đề xuất sizing tự thay native protection.

## 6. Mẫu contribution của mỗi agent

Copy mẫu này vào phản hồi; không tạo thêm tracker hoặc hàng loạt file riêng.

```text
Vai trò / agent / thời điểm / revision hoặc nguồn đã đọc:
Câu hỏi đang trả lời:
FACT (nguồn trực tiếp và phạm vi):
HYPOTHESIS (cơ chế và giải thích cạnh tranh):
PROPOSAL (thay đổi tối thiểu hoặc nhóm thông tin):
UNKNOWN (chưa thể chứng minh từ nguồn được phép):
Vai trò input: direction / risk / sizing / execution
Pair, horizon, available-at và giới hạn áp dụng:
Đối chứng cần thiết và kết quả nào sẽ bác bỏ đề xuất:
Rủi ro leakage, redundancy, search burden và khả năng thiếu mẫu:
Data/code/compute cần thêm; phạm vi quyền hiện có:
Đề xuất: ADMIT_FOR_DESIGN / DEFER / REJECT_PROPOSAL
Phản biện mạnh nhất chống lại chính đề xuất của tôi:
```

ADMIT_FOR_DESIGN chỉ là đưa vào thiết kế wave, không phải DATA_ADMITTED,
không phải có edge và không phải quyền chạy.

## 7. Những quyết định phải thảo luận thực chất

| ID | Quyết định mở | Bằng chứng cần để chốt |
| --- | --- | --- |
| D1 | Wave đầu dùng nhóm nào; nhóm nào hoãn? | Metadata sẵn có, cơ chế, PIT feasibility, giá trị câu hỏi và chi phí; không chọn bằng Sharpe đã nhìn |
| D2 | Mục tiêu absolute return, tail risk hay execution cost? | Vai trò input; horizon và thời điểm native fill; không tối ưu proxy rồi gọi là PnL |
| D3 | Dùng deterministic contrast hay reference model đơn giản? | Đủ đo conditional value; khả năng fold-local fit thực tế; độ phức tạp cần thiết |
| D4 | Shared policy so với pair-specific ở mức nào? | Đối chứng cùng budget và calendar; khác tham số tối ưu không tự chứng minh cá thể hóa tốt |
| D5 | Tương tác nào đáng kiểm dù marginal value thấp? | Cơ chế ghi trước outcome, số tương tác hữu hạn và cách tính trials |
| D6 | Điều kiện KEEP/DROP/INCONCLUSIVE là gì? | Minimum useful improvement, uncertainty, sample dependence và costs; ngưỡng chốt trước run |
| D7 | Cần sửa core nào, hay chỉ contract/input? | Gap map từ source; lựa chọn ít thay đổi nhất vẫn kiểm đúng giả thuyết |
| D8 | Khi nào dừng nghiên cứu hướng này? | Cap source/feature/interaction/compute; kill criteria và quyền cho attempt mới |

Không lấy majority vote thay evidence. Bất đồng thực nghiệm phải chuyển thành
một phép kiểm nhỏ có thể bác bỏ; nếu chưa được phép chạy thì ghi proposal,
không tự thực thi. Một nhóm input có thể hữu ích với ETH nhưng không với BNB.

## 8. Vòng thảo luận có giới hạn

**Vòng 0 — điều phối:** đọc tracker và quyền hiện tại; ghi scope, revision, vai trò.
Không tự coi source đã sạch hoặc dữ liệu đã eligible từ lịch sử hội thoại.

**Vòng 1 — phân tích độc lập:** mỗi vai trả một contribution, tối đa ba đề xuất
ưu tiên. Bao phủ catalogue rộng nhưng không thiết kế mọi biến thể.

**Vòng 2 — phản biện chéo:** mỗi đề xuất nhận ít nhất một phản biện về data/PIT
và một về phương pháp/economics. Trả lời bằng nguồn hoặc đổi claim thành UNKNOWN.

**Vòng 3 — tổng hợp:** chốt D1–D8 hoặc ghi BLOCKED cùng bằng chứng còn thiếu.
Một vòng sửa bổ sung chỉ dành cho finding cụ thể; không lặp để ép đồng thuận
hay tới khi tìm thấy chiến thuật có lãi. Sau đó bàn giao cho operator.

Không chạy suite/market để giải quyết tranh luận trong quyền docs-only.
Ngân sách execution, data-read và collection là mục cần đề xuất riêng.

## 9. Thiết kế kiểm chứng tối thiểu cần bảo vệ

- Baseline pair-only phải hợp lệ, có hành vi thực tế; so với thêm BTC, rồi thêm
  quan hệ tương đối. Nhóm khác có add/drop-group contrast được đăng ký trước.
- Cùng calendar, thời điểm khả dụng, cost/native policy và giới hạn lựa chọn.
  Không làm kết quả đẹp bằng bỏ đoạn thiếu dữ liệu hoặc chỉ giữ regime thắng.
- Selection, scaling, lag/horizon search và clustering đều trong train/inner.
  Không chọn feature toàn dataset rồi mới walk-forward; không random split hourly rows.
- Kiểm theo nhóm tương quan và interactions hữu hạn. SHAP/importance và p-value
  riêng lẻ không đủ chứng minh economic value hoặc nhân quả.
- Ghi mọi scan/evaluation thực sự chạy vào search history, kể cả ngoài SQL.
  Confirmation không trở thành vòng chọn feature tiếp theo.
- Native timing và signal → action → fill → closed trade/inventory phải rõ.
  Biến giúp tăng số trade nhưng net âm không đạt mục tiêu.
- Context thiếu/stale phải có policy; block entry không được khóa protective exit.
- Quyền đọc dữ liệu, tài nguyên và triển khai phải được chốt trước execution.
  Sửa gate files ở giai đoạn triển khai phải tuân thủ AC01–AC15 trong AGENTS.md.

## 10. Output cuối phiên và điều kiện bàn giao

Điều phối điền bảng dưới sau khi có contributions thật. **Hiện chưa có verdict
đa agent; không được ghi tên reviewer hay consensus giả.**

| Mục | Kết quả hiện tại |
| --- | --- |
| Contributions và nguồn trực tiếp | Một agent, 28/10, đọc tracker, packet timer và §12. Không có hội đồng thứ hai |
| Findings chặn / người chịu trách nhiệm | Timer synthetic đã nối driver/snapshot; H-OWN24 không được chạy lại từ việc đó |
| D1–D8, lý do và bất đồng còn lại | Chốt ở §13. Nhóm thông tin mới vẫn chưa được chọn |
| Wave đề xuất: nhóm, target, scope, thứ tự | W-INFORMATION-GATE-01, design only, ETH và BNB, không holdout |
| Nguồn sẵn có / chưa admit / cần nguồn mới | Pre-holdout ETH/BNB/BTC đã đọc, không phải confirmation. Không nguồn mới |
| Baseline, contrasts, tiêu chí bác bỏ | Một baseline và một predicate thêm. D6 ở §13 |
| Công việc tối thiểu và ngân sách đề xuất | 0 cell bây giờ. Nếu được duyệt sau: 12 cell, xem §13 |
| Verdict reviewer | PROPOSAL_ONLY. Không phải independent panel |
| Execution authority | NONE |

Verdict thảo luận: **PROPOSAL_ONLY**. Không dùng READY_FOR_RUN hay QUALIFIED.
Không dùng ACCEPTED_FOR_RUN hoặc QUALIFIED cho một cuộc thảo luận.

Bàn giao tối đa một proposal wave vào kế hoạch hiện có, cập nhật một dòng tiến độ
trong CAMPAIGN_STATUS khi thực sự chốt. Giữ findings chưa giải quyết và kết quả âm.
Không lập roadmap song song hoặc thêm platform để thay cho việc trả lời câu hỏi.

## 11. Prompt khởi động cho agent điều phối

> Đọc tài liệu này và các nguồn ở mục 2. Mục tiêu là tổ chức thảo luận về giá trị
> thông tin cho altcoin, chưa triển khai hoặc chạy thị trường. Xác minh quyền hiện
> tại trước khi phân công; nếu được giao điều phối nhiều agent, giao các vai ở mục 4,
> yêu cầu lượt phân tích đầu độc lập rồi phản biện chéo. Không tự tạo consensus.
> Mọi claim phải phân loại FACT/HYPOTHESIS/PROPOSAL/UNKNOWN và dẫn primary material.
> Dùng catalogue G0–G11, không giới hạn sáu seed nhưng cũng không mở toàn bộ sources.
> Hoàn tất D1–D8, đề xuất một wave admission khả thi, điều kiện dừng và ngân sách cần
> xin cho bước thực thi. Giữ market PAUSED, không đọc giá mới/download/holdout/code
> changes từ nhiệm vụ này. Dừng sau bản tổng hợp có finding và next action cụ thể;
> không lặp để ép ra edge. Báo rõ phần nào chưa biết và cần bằng chứng gì.

## 12. Đầu vào review sau các lần chạy X0–X2

Đã có phép đo H-OWN24-CONTEXT-01 trên ETH/BNB. Đọc
review source và raw reports — local evidence `data/x2_context_review_20261027_01/RESULTS.md`
trước khi dùng kết quả làm contribution. Đây là một review trực tiếp của phiên
tiếp theo, không phải hội đồng đa agent đã hoàn tất D1–D8.

- FACT: 23 report được đối chiếu hash; 6 context reports cũ không trade, 17
  reports có trade đều return/Sharpe âm. Không có candidate qualified.
- FACT: exit scheduling dùng opportunity24bar trước, không timer từ fill;
  ETH A0/1x có 742/890 signal exits dưới24h. ETH A1/slip có residual inventory
  kéo dài và ngăn hàng nghìn BUY. Không gán toàn bộ anomaly cho circuit breaker.
- FACT: A0 và các context arms dùng hai source identities; labels nhập tay
  chưa thay thế được kiểm comparability. Rule chỉ kiểm dấu BTC return/residual,
  chưa đo giá trị từng feature hoặc broad information pool.
- FIXED: NaN/inf metrics không còn được gán IMPROVES; 46 targeted PASS.
  Guard mới do người sửa tự verify; chưa gọi là independent acceptance.
- UNKNOWN: payoff của policy giữ24h theo actual fills; exact residual root cause;
  conditional predictive/economic value của các nhóm G0–G11.

Contribution tiếp theo cần giải quyết D1–D8 ở phạm vi thiết kế: đề xuất wave từ
nguồn đủ điều kiện, nêu cơ chế/đối chứng/giới hạn và phản biện. Hai nhiệm vụ
engineering trước market là exit contract và residual reproduction bằng
synthetic. Không tự đổi holding policy của packet đã đóng, không lặp rule hoặc
nới threshold để tìm kết quả dương. Verdict ở mục 10 và §13 là của một agent,
không phải hội đồng thứ hai.

**Execution follow-up:** stale-stop fix và holding contract — local evidence `data/x2_execution_followup_20261027_01/RESULTS.md`.
Tồn dư ETH đã truy được tới stop của cycle cũ sống qua emergency close. Bản sửa
có69 targeted PASS, gồm7 regression mới; chưa independent review. Holding timer
từ fill vẫn OPEN, đã ghi rõ gap runtime/sentinel và ma trận synthetic phải đạt.
Không tiếp tục báo hai việc này như cùng trạng thái: stale-stop đã sửa, timer
chưa tích hợp. Các kết luận economic/feature-value và quyền market vẫn giữ giới hạn.

**Cập nhật28/10 — timer đã nối đến native single-engine runtime:**
packet synthetic và giới hạn — local evidence `data/x2_fill_timer_synthetic_20261027_01/RESULTS.md`.
Policy riêng chưa registered, view fill-cycle immutable,118 targeted PASS trên
source69f2a648… ổn định trước/sau. Đã kiểm actual fill→24h exit, reject/delayed/
partial fill, partial exit, stop/re-entry, context missing và restart; wrapper
broker không còn báo partial thành full. Chưa có historical cycle snapshots,
driver sentinel/full-launcher proof hoặc independent review, nên chưa market-ready.
Không dùng engineering progress này để claim thông tin BTC/altcoin đã tạo edge.

**Cập nhật cùng ngày — driver synthetic:** expiry được hỏi mỗi chu kỳ kể cả HOLD,
snapshot lịch sử mang first-fill và entry-order identity, và một lần chạy
launcher → worker → simulator → report → summary trên dữ liệu synthetic đã thấy
một vòng 24 giờ. Đó là phép đo đường đi, không phải edge. D1–D8 của một agent
nằm ở §13. Market vẫn PAUSED.

## 13. D1–D8 — một agent, 28/10/2026

Cùng một agent đóng vai review rồi vai triển khai, sau đó mới ghi các quyết định
này. Đây không phải phản biện chéo của nhiều người.

| ID | Quyết định | Lý do |
| --- | --- | --- |
| D1 | Wave thiết kế là W-INFORMATION-GATE-01. Không chọn lại G0/G1 và không mở G2–G11 | G0 và G1 đã có phép đo âm trên đồng hồ tín hiệu. G2 còn chờ một target đã admit. G4 không phải breadth. G5/G6 thiếu store. G7 và pilot G11 đã đóng. Chọn lại G1 là chạy lại H-OWN24 |
| D2 | Mục tiêu là marked net sau phí native, cùng drawdown và Sharpe. Horizon là 24 giờ từ fill đầu | Timer mới là đồng hồ của phép đo, không phải mục tiêu lợi nhuận |
| D3 | Đối chứng deterministic: một baseline và đúng một predicate thêm. Không reference-model search | Đủ để hỏi giá trị có điều kiện của một nhóm. Grid làm phình số trial |
| D4 | Cùng predicate cho ETH và BNB. Chưa claim cá thể hóa | H-OWN24 dùng chung và không chứng minh personalization. Khác tham số tối ưu không được dùng làm bằng chứng |
| D5 | Một tương tác: predicate được thêm. Không có interaction thứ hai trong wave này | Số trial bằng số cell đã khai |
| D6 | Trên từng symbol và từng phí: cải thiện marked net, drawdown không tệ hơn, và Sharpe > 0 thì mới được gọi là IMPROVES. Dưới 30 lệnh là INCONCLUSIVE. Không gộp ETH với BNB hay ba phí | IMPROVES vẫn không phải qualification. Một phí gãy đường thực thi thì cả contrast đó là INCONCLUSIVE |
| D7 | Sửa nằm ở contract/driver của timer. Không sửa gate AC01–AC15, không thêm tham số own24_context | Policy đã đo giữ nguyên. Policy fill-cycle vẫn chưa vào registry |
| D8 | Dừng khi predicate thêm không đạt D6 trên một symbol. Không nới ngưỡng, không mở holdout, không attempt thứ hai cùng cơ chế | Attempt sau cần cơ chế khác và một authorization mới |

**Wave W-INFORMATION-GATE-01, cửa sổ đã khóa, luật vẫn UNSET, chưa được chạy.**
Target ETH/USDT và BNB/USDT, 1h, cửa sổ `216, 25198)` trên đồng hồ pre-holdout.
Holdout không mở. BASE và PLUS chưa có predicate. H-OWN24-CONTEXT-01 nằm trong
danh sách đóng. Ngân sách nếu sau này có luật khác: 12 cell, hai phiên 6 cell,
mỗi phiên 3600s. 12 × 600 bằng 7200 nên không đứng trên trần một phiên.
Ngân sách đã dùng cho bước khóa này: **0 cell**.
[Khóa — local evidence `data/w_information_gate_20261028_01/RESULTS.md`.

Phản biện mạnh nhất: timer đúng làm phép đo sau đáng tin hơn, nhưng không tạo
ra một nhóm thông tin mới. Xin chạy trước khi có luật khác H-OWN24 sẽ chỉ lặp
câu hỏi đã đóng.

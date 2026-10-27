# Research Evidence

**Current decision — 2026-10-27: PAUSED / NO_SUPPORTED_WINNER.** Chưa có candidate qualified. [Tracker](CAMPAIGN_STATUS.md) giữ nguyên các verdict của những phép đo đã đóng. Memo dưới đây chỉ là tổng hợp evidence và nghiên cứu trên giấy; không chọn strategy mới, không cấp quyền đọc giá mới, chạy cell hoặc kết nối sàn. Các số liệu và đề xuất testnet 08/2026 phía dưới là lịch sử. Không relabel dữ liệu đã expose thành untouched.

## Memo quyết định sau các phép đo đã đóng — 27/10/2026

### Những điều đã học và giới hạn

Các raw packet trong tracker phân biệt được thiếu mẫu, net không đủ sau
cost, cost-fragile positive inner, và gross âm. Không pool Sharpe hoặc xếp
hạng các rule khác cửa sổ; các cost và window chồng lấn không phải samples
độc lập. Commit sạch sau run không đổi provenance của measurements lịch sử.

- H-PULLBACK-01 thiếu mẫu; không biết kéo dài window có đủ hoặc có lãi không.
- H-PULLBACK-TEMPORAL-01 có gross dương nhưng chi phí vượt phần đó trên đường
  đã thực thi; đây không phải zero-cost counterfactual của một engine run khác.
- H-VOLBREAK-01 chỉ có diagnostic positive ở1x; không đáp ứng cost robustness.
- H-LIQ-REVERSAL-01 thua trước lẫn sau phí dưới policy/timing đã đo. Không
  suy từ nhóm stop thua rằng nới/bỏ stop sẽ cải thiện tổng payoff. Stress
  còn đổi circuit-breaker path, không chỉ trừ thêm cùng một khoản phí.

Các outcome này bác rule cụ thể trong scope đo, chưa bác mọi member của cả
họ MA/momentum/reversal. Việc không retune hoặc mở lại các họ lúc này là
**quyết định quản lý search budget**, không phải định lý kinh tế. Theo
operator, trạng thái vẫn PAUSED và lần đo mới cần cơ chế ngoài các họ đã đóng.

### Nghiên cứu gốc nói gì, và chưa áp dụng được điều gì

Đọc ngày27/10/2026. Đây là đánh giá tính phù hợp, không replication. Không
tải dataset hoặc mở market evaluation. Không dùng Sharpe đã công bố của
paper làm prior cho chính rule, costs, timing hoặc sample của repo.

| Nguồn gốc | Điều có căn cứ từ nguồn | Khoảng cách tới BTC spot1h long-only hiện tại | Quyết định |
| --- | --- | --- | --- |
| [Liu & Tsyvinski, Risks and Returns of Cryptocurrency, NBER24877 (2018)](https://www.nber.org/papers/w24877) | Có kết quả momentum ở daily/weekly và investor attention ở horizon tuần | Không chứng minh alpha hourly của MA đã đo; attention cần dữ liệu ngoài OHLCV, availability timestamps và kiểm revision/publication lag | Không mở lại MA; attention chỉ là hướng đọc nếu sau này được duyệt đổi information set/horizon |
| [Liu, Tsyvinski & Wu, Common Risk Factors in Cryptocurrency, NBER25882 (2019)](https://www.nber.org/papers/w25882) | Market/size/momentum mô tả cross-sectional expected returns | Cross-section cần universe nhiều coin và thường portfolio construction khác; factor return không phải expected return của single BTC rule | Không mở universe/router/short từ paper này |
| [Fieberg, Liedtke & Zaremba, Cryptocurrency Anomalies and Economic Constraints (2024)](https://doi.org/10.1016/j.irfa.2024.103218) | Khả năng giao dịch bị ảnh hưởng bởi costs, long/short legs, microcaps và thời kỳ; momentum alpha phần lớn từ short legs trong thiết kế nghiên cứu đó | Không thể lấy long-short premium hay microcap anomaly làm edge cho BTC long-only; kết quả dữ liệu2014–2022 không xác nhận hiệu lực hôm nay | Cost/leg/universe/horizon phải được xét trước code, không mở thêm rule để tìm Sharpe dương |
| [Bailey et al., Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf) và [Bailey & López de Prado, Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf) | Cần xử lý selection/search burden; PBO/CSCV và DSR có thiết kế, giả định và inputs riêng | SQL counts không tự bao gồm mọi lần thử; một singleton hay một terminal packet không đủ để tự chứng nhận phương pháp | Khóa method trước outer; không sửa gate, không tự điền PBO/DSR bằng convention để đạt |

Độ sâu truy cập: NBER abstract và đoạn nội dung primary working paper qua
search; preview của publisher cho bài2024; full PDF PBO trên website tác giả
và abstract PDF DSR. Chưa replicate hoặc audit đầy đủ appendices/cost assumptions
của các paper kinh tế; không gọi đây là systematic review đầy đủ.
Một publisher preview về hourly reversal mang issue November2026 cũng xuất
hiện, nhưng chưa xác minh first-online date so với27/10/2026: **không dùng
làm prior đủ điều kiện hoặc căn cứ mở lại H-LIQ**.

### Ba lựa chọn và khuyến nghị hiện tại

| Lựa chọn | Việc có thể đem lại | Đổi phạm vi cần operator quyết | Trạng thái |
| --- | --- | --- | --- |
| A — Giữ profile và PAUSED | Giữ kết luận âm, không tiêu thêm search budget; chờ một thesis mới có cơ sở | Không đổi pair/TF/data/risk; không có lời hứa ngày live | **Khuyến nghị hiện tại** |
| B — Nghiên cứu thông tin ngoài giá | Có thể tạo cơ chế khác OHLCV thuần, ví dụ attention/information arrival | Chỉ khi được duyệt: xác định nguồn có timestamp công bố/ingestion/revision và cohort phù hợp; horizon trong paper chưa khớp1h | Chưa có candidate đủ điều kiện, chưa xin/tải dữ liệu, chưa code |
| C — Xét lại profile sản phẩm | Cho phép đặt câu hỏi horizon dài hơn, universe/leg khác nếu mục tiêu cá nhân thay đổi | Là thay đổi thesis/phạm vi mới; phải so chi phí và lợi ích trước, không coi thayTF đơn thuần là cơ chế mới | Không khuyến nghị mở rộng ngay; không được làm tự động |

Trong những nguồn đã đối chiếu, **chưa xác lập một cơ chế mới đủ căn cứ và
phù hợp mọi ràng buộc hiện hành**. Đây là giới hạn của desk review này, không
phải chứng minh không tồn tại edge. Không chọn B/C chỉ để tránh kết luận PAUSED.
Không mở funding/perp, ML, thêm sàn hoặc portfolio để đi vòng scope spot.

### Điều kiện rời PAUSED — một quyết định, không một batch search

Một đề xuất tương lai chỉ đáng đưa cho operator khi trả lời đủ sáu mục:

1. **Nguồn payoff:** ai có thể trả phần lợi nhuận và vì sao nó có thể tồn tại
   sau friction; nêu cơ chế cạnh tranh và điều gì sẽ bác bỏ. Narrative không
   là bằng chứng; kết quả riêng của paper không chứng minh nhân quả.
2. **Khớp sản phẩm:** long-only spot/BTC/1h, native latency k+2 và protections
   có thực sự phù hợp không; mọi thay đổi profile được nêu trước, không tự đổi.
3. **Thông tin đủ điều kiện:** dữ liệu nào mới, available-at khi nào, exposure
   và confirmation cohort là gì. Có file không đồng nghĩa eligible evidence.
4. **Khả năng trả cost:** nói rõ turnover/horizon/friction assumptions và
   quan sát nào còn UNKNOWN; không hạ fee/stop/threshold để mở cửa.
5. **Thiết kế có thể bác bỏ:** một candidate cố định, sample/dependence,
   historical trial burden, terminal/kill criteria, budget và không mở rộng
   hậu nghiệm. Outer chỉ sau method và data admission riêng.
6. **Quyền và review:** reviewer khác author cho scope áp dụng; operator
   duyệt đúng proposal/input/source/budget/packet mới. Không dùng một lời
   “tiếp tục” trong PAUSED để tự tạo quyền market.

Nếu một mục chưa có câu trả lời quyết định được, ghi rõ thiếu gì và giữ
PAUSED. Không lập thêm tracker, score chủ quan hoặc vòng backtest để lấp
ô trống. Phần research-on-paper hiện tại hoàn tất; next action là operator
giữ profile/PAUSED hoặc duyệt một scope desk research mới cụ thể khi có lý do.

### Feasibility của thông tin ngoài giá — đọc tài liệu/source, chưa lấy dữ liệu

Phần này cụ thể hóa lựa chọn B, không chuyển từ PAUSED sang chạy nghiên cứu.
Một nguồn dễ tải không đồng nghĩa có edge hoặc là đường live nhanh nhất.
Đã kiểm tài liệu provider ngày27/10/2026; endpoint uptime, account access,
quota và khả năng tái lập lịch sử **chưa được kiểm bằng data request**.

| Nguồn/đường | Fact từ provider | Khoảng thiếu quyết định | Kết luận hiện tại |
| --- | --- | --- | --- |
| Google Trends Explore | Dữ liệu sample, normalized/indexed theo query/time range; không phải absolute search counts | Query/window changes có thể đổi scale; snapshot tải hôm nay không chứng minh vintage mà trader từng thấy | Không ghép export khác window thành chuỗi point-in-time để backtest |
| Google Trends API chính thức | Công bố consistently scaled across requests, rolling1800days, daily/weekly/monthly/yearly, dữ liệu tới hai ngày trước; trang access hiện vẫn alpha application | Chưa có quyền truy cập được xác minh; resolution/lag khác một feed hourly tức thời; consistent scaling không bảo đảm vintage bất biến | Không dựa unofficial scraper hoặc giả API có sẵn; không chọn làm nguồn gần nhất cho BTC1h |
| GDELT GKG/raw archive | Provider mô tả update15phút và kho archive | Publication/first-seen/batch-availability/local-ingest khác nhau; dedup/revision/filter/coverage chưa xác minh cho BTC | Có thể là đối tượng **data-feasibility** sau approval; chưa là trading hypothesis |
| GDELT DOC API | Official launch docs mô tả rolling3month và result caps cho article lists | Không suy từ DOC query hiện tại rằng lấy được archive2023; API query kết quả hiện tại không chứng minh tập thông tin từng khả dụng | Không dùng DOC backfill để ghép vào frozen25198 rồi gọi confirmation; archive là một route khác |

Nguồn trực tiếp: [Google Trends data FAQ](https://support.google.com/trends/answer/4365533?hl=en),
[Google Trends API announcement](https://developers.google.com/search/blog/2025/07/trends-api),
[API alpha access](https://developers.google.com/search/apis/trends),
[GDELT data](https://gdeltproject.org/data.html),
[DOC2.0 specification](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/),
[GDELT article-change tracking](https://blog.gdeltproject.org/announcing-the-gdelt-global-difference-graph-gdg-planetary-scale-change-detection-for-the-global-news-media/).
Tài liệu DOC là mô tả launch2017; current service behavior phải xác minh riêng
nếu được phép, không coi đây là một live API test. Việc provider có theo dõi
article edits không chứng minh mọi vintage của dataset được bảo toàn.

**Tái sử dụng code đang có:**

- [agents/sentiment.py](../src/trading_agent/agents/sentiment.py) tự ghi không
  có news feed, suy sentiment từ technical data; tên sentiment không tạo
  information set mới. Optional funding/OI/CVD fields không chứng minh có
  dữ liệu lịch sử point-in-time hoặc quyền dùng cho scope hiện tại.
- [ml/sentiment.py](../src/trading_agent/ml/sentiment.py): NewsAggregator
  dry_run tạo tin synthetic, nhánh fetch thật raise NotImplementedError;
  SentimentComposite cố định hai provider dry_run. Không bật module này để
  biến synthetic thành live news evidence.
- [features/llm/news.py](../src/trading_agent/features/llm/news.py) nhận articles
  từ caller, không tự chứng minh nguồn/availability. Schema có published_at
  nhưng chưa có đầy đủ provider-first-seen/ingest/vintage. Không gọi LLM,
  thêm dependency hoặc sửa module trong lượt này. Đây là ba module đã đọc,
  không phải tuyên bố audit mọi upstream của repo.

**Nếu operator sau này duyệt data-feasibility, giới hạn tối thiểu đề xuất:**

1. Chọn đúng một nguồn và một query/entity specification; mô tả thông tin
   mới so với OHLCV, mục tiêu kiểm availability/quality và quyền read/storage.
   Không có nguồn payoff/thesis đủ căn cứ thì không tiến tới strategy.
2. Một pilot metadata-only: tối đa20HTTPrequests,30s/request,500records,
   25MiB tổng,0paidAPI/0LLM/0broker/0price-file read,0retry; dừng khi chạm
   bất kỳ cap hoặc access error. Đây là **envelope đề xuất chưa duyệt**, không
   là benchmark chi phí/thời gian và không đảm bảo đủ mẫu hay đủ tin BTC.
   Không polling/background service. Không tải full article bodies.
3. Mỗi record cần provider+record/URL identity, entity/query version,
   event/published time nếu có, provider-first-seen/batch-release nếu có,
   local-ingested-at UTC, processing-finished-at, payload hash/version,
   duplicate/revision flags, missing-field flags và retention scope.
4. available-at cho prospective dùng thời điểm bảo thủ không sớm hơn lúc
   thu thập+xử lý xong tại máy; chỉ join feature available-at≤decision-time.
   Không backdate ingestion hôm nay thành published_at năm2023. Nếu historical
   availability không chứng minh được thì historical inference BLOCKED;
   một pilot mới không phục hồi được thông tin đó hoặc tạo confirmation.
5. Báo records attempted/retained/rejected/dedup/revised và timestamp gaps;
   unknown timestamp giữ UNKNOWN, không fill theo giả định thuận lợi. Metadata
   pilot chỉ có verdict ACCESS_OR_SCHEMA_BLOCKED / FEASIBLE_FOR_FURTHER_DESIGN /
   NO_DECISION_FROM_SAMPLE, không có Sharpe, SELECT hoặc strategy-qualified.
6. Dừng sau pilot, không nối sang capture liên tục, classifier, strategy,
   backtest hay account. Muốn bước đó cần thesis, cohort, forecast method và
   authorization riêng. Một ngày metadata không thay thế30/100 operational gates.

**Quyết định 27/10:** không đầu tư pilot metadata. Giữ lựa chọn A và PAUSED.
GDELT chỉ hơn Trends ở mô tả cadence nội ngày; 20 request không chứng minh
vintage 2023, không tạo cohort confirmation, và không trả lời payoff sau cost.
DOC rolling ba tháng không phủ cửa sổ đã đo. Pilot vẫn NOT_AUTHORIZED,
data requests=0.

**Khuyến nghị giữ nguyên:** chưa đầu tư collector/sentiment engine. GDELT là
lựa chọn *khả thi để kiểm dữ liệu* hơn Trends cho cadence nội ngày theo tài
liệu, **không phải xếp hạng alpha hoặc bằng chứng nó dẫn tới live sớm hơn**.
Độ trễ k+2, payoff sau costs và cohort confirmation vẫn chưa được giải quyết
bằng đổi nguồn thông tin. Feasibility trên giấy đã hoàn tất, source changes=0,
candidate mới=0, market evaluation=0, run_authority NONE. Không tự lặp thêm
desk review hoặc implementation nếu chưa có câu hỏi/scope mới.

## Snapshot lịch sử 08/2026 — không phải trạng thái admission hiện tại

> Tài liệu này phân biệt rõ từng loại bằng chứng nghiên cứu và mức độ tin cậy của chúng.
> **Backtest không phải production validation.** Một config "tốt trong backtest" chỉ là
> *candidate research configuration* cho tới khi có statistical validation + testnet/paper
> evidence.
>
> Snapshot historical cũ đã được loại khỏi repository; không dùng số liệu cũ làm
> evidence hiện tại. Mọi kết luận mới phải trỏ tới artifact có manifest và identity đầy đủ.

## Phân loại bằng chứng

| Loại | Định nghĩa | Độ tin cậy cho production |
| --- | --- | --- |
| In-sample | Huấn luyện + đánh giá trên cùng dữ liệu | Thấp (overfit risk cao) |
| Out-of-sample (OOS) | Đánh giá trên dữ liệu không dùng để chọn tham số | Trung bình |
| Walk-forward (WFO) | Rolling train/test qua nhiều fold | Trung bình–cao nếu đủ fold & min trades |
| Final untouched holdout | 6 tháng cuối đã freeze trong manifest và chưa được dùng để tune | Cao nhất khi chỉ mở một lần sau khi protocol được khóa |
| Paper / Testnet | Thực thi mô phỏng với dữ liệu thật | Cao (nhưng không = live) |
| Live | Vốn thật | Chỉ sau release gates |

## Bằng chứng hiện có (tổng hợp)

### 1. Multi-symbol multi-timeframe backtest — enhanced_ma (snapshot 2026-08-06)

- **Loại:** In-sample (params tối ưu trên toàn bộ chuỗi) — **KHÔNG phải OOS**.
- **Provenance:** commit lịch sử trước `e7f51ba`; dataset 2023-01-01 → 2026-08-05 (~31,500 hourly bars/symbol).
- **Fee/slippage:** chưa model đầy đủ (transaction costs ghi chú là chưa tính).
- **Kết quả nổi bật:** BTC 4h +164.44% (Sharpe 1.05, PF 2.82, 32 trades); SOL 1h +772.49% (Sharpe 1.27, 147 trades); SOL 1h DD -63.63%.
- **Cảnh báo:** các fold/chuỗi có ít trade (32-40 trades/3.5 năm) có Sharpe không đáng tin;
  win rate/PF của full-system backtest báo 0% / 0.00 (calculation issue — không dùng làm bằng chứng).
- **Kết luận:** `CANDIDATE RESEARCH CONFIGURATION` — không phải production config.

### 2. Walk-forward & multi-symbol benchmark (2026-08-05/06)

- **Loại:** WFO (expanding window) — nhưng **OOS Sharpe ≈ 0** trên nhiều fold: strategy
  MA+RSI **không generalize** ngoài in-sample.
- **Kết luận:** `FAIL` cho mục đích production; chỉ giữ lại làm baseline research.

### 3. Full-system 3-year simulation

- **Loại:** Paper simulation với risk controls (event-driven, không vectorized).
- **Kết quả:** +22.26% → +36.28% (tùy snapshot), PF 1.53 (một snapshot), nhưng **win rate / PF
  có calculation issue** ở snapshot sau; 2025-2026 lợi nhuận ≈ 0 (regime dependence).
- **Kết luận:** `INSUFFICIENT EVIDENCE` cho production.

### 4. Alpaca Paper live (từ 2026-08-08)

- **Loại:** Paper validated (không phải live vốn thật).
- **Evidence:** equity ~$95-100k, position sync BTC/SOL/AVAX, fills real qua Alpaca paper endpoint.
- **Kết luận:** xác nhận execution path chạy được ở paper; không chứng minh edge.

### 5. Cost stress test (2026-08-11) — P2 statistical hardening

- **Loại:** Parametrized evaluation of live strategy evidence pipeline at 1x/2x/3x cost multipliers.
- **Provenance:** commit `69904e2`; `scripts/stress_evidence_costs.py`; data 2023-01-01 → 2026-08-11 (~31,600 hourly bars/symbol).
- **Symbols:** BTC/USDT (50%), SOL/USDT (50%), 6 folds × 180 days.
- **Kết quả:**
  - 1x: median Sharpe -0.44, worst DD 0.1%, trades/fold 15-21
  - 2x: median Sharpe -1.18, worst DD 0.1%, trades/fold 15-21
  - 3x: median Sharpe -2.06, worst DD 0.2%, trades/fold 15-21
- **Kết luận:** **`FAIL`** — strategy có **edge âm tại mọi mức cost**, không pass acceptance gate. Xác nhận `NO-GO` cho mainnet.

## Trạng thái statistical hardening

| Yêu cầu (prompt mục 6) | Trạng thái |
| --- | --- |
| Minimum trades per fold | ✅ **Done** — enforced via `min_trades_check` (tests/test_research_stats.py::TestMinTrades) |
| 6-12 tháng final untouched holdout | ✅ **Frozen** — immutable manifest exists; intentionally not opened for iterative tuning |
| Regime breakdown | Một phần (ghi chú regime dependence) |
| Block bootstrap confidence intervals | ✅ **Done** — `block_bootstrap_sharpe_ci` + tests |
| Probabilistic / Deflated Sharpe | ✅ **Done** — `probabilistic_sharpe_ratio`, `deflated_sharpe_ratio` + tests (DSR accounts for ~8000 explored trials) |
| Fee stress 1x/2x/3x | ✅ **Done** — `scripts/stress_evidence_costs.py`: med Sharpe -0.44/-1.18/-2.06 (1x/2x/3x), **no edge at any level** |
| Spread / slippage stress | ✅ **Done** — included in cost stress multipliers |
| Gap / latency / missing candle / partial fill / outage / correlated drawdown scenarios | ✅ **Done** — property tests in `tests/test_live_safety.py` (fail-closed + hypothesis) |

→ Kết luận chung: **`INSUFFICIENT EVIDENCE`** — không config nào được gọi là
`RECOMMENDED PRODUCTION CONFIG`. Đề xuất enhanced_ma (fast15/slow100/ADX>40,
ATR SL2.0/TP6.0) thuộc snapshot lịch sử này, đã bị thay thế bởi trạng thái
PAUSED/NO_SUPPORTED_WINNER ở đầu tài liệu; không còn là khuyến nghị testnet.

## Provenance template (dùng cho mọi result mới)

Mỗi kết quả research mới phải ghi: commit SHA · dataset hash/manifest · date range ·
timeframe · fees · spread · slippage · trade count · Sharpe · max DD · profit factor ·
benchmark · OOS? · exact command.

## Methodology hardening snapshot (2026-08-17)

The executable methodology now uses actual gross/net returns, target-position
turnover/cost, train-only robust transforms, nested expanding outer/inner folds,
purge/embargo, one-shot outer testing, registry-derived trial counts and full-space
PBO/CSCV. Calibration, conformal, drift, simulator provenance and promotion
evidence are immutable artifacts with explicit source/status.

The deterministic benchmark in `scripts/benchmark_methodology.py` is classified
`SYNTHETIC_DIAGNOSTIC`: selected alpha and oracle-assisted regime mixture win their
fixtures; adaptive experts lose fixed baselines; MPC is not empirically
benchmarkable; Platt calibration improves an independent synthetic test. None of
these results demonstrates a market edge. Details and exact values are in
[`RESEARCH_METHODOLOGY.md`](RESEARCH_METHODOLOGY.md).

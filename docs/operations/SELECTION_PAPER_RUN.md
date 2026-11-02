# Vòng chọn giấy hằng tuần

Status: CURRENT
Owner: operator
Verified: 2026-11-01, chưa chạy forward lần nào.
Evidence: `data/selector_20261101_01/RESULTS.md` (git-ignored), [System Overview](../SYSTEM_OVERVIEW.md)

Trang này chốt quy tắc của vòng chọn giấy trước danh sách đầu tiên. Vòng này biến ý "chạy backtest để thấy thị trường đang như nào rồi chọn" thành một đầu ra lặp lại được mỗi thứ Hai. Nó chỉ tạo bản ghi: không đặt lệnh, không ghi vào promotion store, không nối router, không đổi cổng nào. Mainnet vẫn NO-GO.

## Thẩm quyền

Operator ngày 2026-11-01: "Ok build vòng chọn giấy option B", sau khi được báo vòng này đọc giá sống, chỉ tạo bản ghi, và phía thật giữ `NO_TRADE`. Bản đo nền là `selector_20261101_01`: 0 trong 12 biến thể đạt giữ theo dõi, tất cả chưa đủ dữ liệu. Vòng này không phải bằng chứng lợi thế.

## Phán quyết seal, option B

- BTC được coi là ngoài seal của H-XSEC-01 cho vòng này và cho nhật ký tín hiệu.
- 12 coin còn lại của universe H-XSEC-01 bị che hoàn toàn: ADA, AVAX, BNB, DOGE, DOT, ETH, LINK, NEAR, SOL, TRX, XRP, ZEC. Vòng này không tải, không lưu, không xếp hạng, không tính return cho chúng ở bất kỳ ngày nào, kể cả lịch sử trước seal.
- BNSOL, SOL staking của Binance, bị che như 12 coin trên, vì nó mang giá SOL. Phán quyết chỉ nêu 12 tên; nó được thêm vì bar đã lưu không thu lại được, còn mặt nạ thì bỏ được. Nó chưa từng vào list: hạng volume 155 đến 294, 0 trong 333 tuần đóng băng. Tên bọc của coin che niêm yết sau này thêm vào `SEAL_PROXIES` trong `tools/selection/config.py`.
- Không đụng một byte của `docs/proposals/H_XSEC_01_RELATIVE_STRENGTH.md`, không tính xếp hạng 21 ngày hay top 4 của giả thuyết đó.
- Book có mặt nạ là biến thể mới. Số đo của nó trên W1 đến W4 ghi ở phần kết quả nghiệm thu, không thay kết quả của biến thể đã đăng ký.

## Quy tắc đóng băng

- **Ứng viên:** 32 ứng viên của `selector_20261101_01`, định nghĩa không đổi, tính trên vũ trụ có mặt nạ.
- **List:** tới 2026-08-03 là các list đã đóng băng bỏ 12 coin che và BTC, không bù tên. Từ 2026-08-10 là quy tắc cũ (bar ngày thứ Hai trước đó, tổng quote volume 28 ngày, ít nhất 60 bar ngày, có bar chủ nhật và bar 21 ngày trước, lọc độ biến động, loại stablecoin và token đòn bẩy) trên toàn bộ cặp USDT spot đang giao dịch, bỏ 12 coin che và BTC trước khi xếp hạng, giữ 18 tên đầu. 18 là số tên ngoài universe trên list cuối 2026-08-03.
- **Bộ chọn:** biến thể chính đã đăng ký: nhìn lại 90 ngày, top 3, Sharpe theo khối 6 stamp kết thúc trước stamp chọn, ô điểm không dương là cash, giữ 4 tuần.
- **Bốn book lệch pha:** book p chọn ở các thứ Hai có số tuần tính từ 2020-07-13 chia 4 dư p. Mỗi thứ Hai có đúng một book chọn lại. Danh mục giấy là trung bình bốn book.
- **Mốc so sánh:** EW 32 ứng viên, BEST_SO_FAR, hold BTC. 11 biến thể còn lại không chạy.

## Cách đọc forward

Các mục dưới là mặc định do tôi đặt, operator đổi được trước danh sách đầu tiên.

- **Forward chứng minh được gì:** vòng chạy có sống không, và có bắt được lỗ lớn không. Nó không xác nhận được lợi thế trong thời gian hữu ích: mức +47 bps/tuần của khám phá cần 271 đến 513 tuần.
- **Cohort:** 60 tuần từ thứ Hai đầu tiên chạy đúng giờ. Đọc một lần vào ngày kết thúc cohort.
- **Thống kê chính:** trung bình tuần của danh mục giấy trừ EW cùng exposure. k mỗi tuần là gross exposure trung bình của danh mục chia cho của EW trong tuần đó.
- **Chi phí chính:** bậc cộng 10 bps phí spot, tính cả drift. Hai cơ sở in cạnh: bậc, và 30 bps phẳng.
- **Đồng hồ chính:** khớp ở open một giờ sau stamp, vì job chạy sau stamp và runner chạy ở phút :01 giờ lẻ. Cơ sở khớp ở open của stamp in cạnh.
- **Tripwire:** từ tuần 8, z của tổng hiệu số tuần dưới -2,5, với độ lệch chuẩn tuần 510 bps, là sd của chính dòng sổ trên W1 đến W4 (508 bps). Số 302 bps của bản rà soát thuộc một thống kê khác. Bắn thì ghi `BOOK_TRIPWIRE`, hiện trên trang, book vẫn ghi, operator quyết định.
- **Trang:** chỉ hiện quyết định, lý do, sức khỏe dữ liệu và trạng thái tripwire. Không hiện lợi nhuận trước ngày đọc, nếu không phép đọc trở thành theo dõi.

## NO_TRADE

Phía thật giữ `NO_TRADE` mỗi tuần tới khi operator ghi phán quyết khác. Vòng này không tự rời `NO_TRADE`. Quy tắc điểm không dương không bao giờ bắn trên thư viện này, nên lý do nằm ở mức policy. Mỗi tuần ghi mọi lý do khớp, theo thứ tự, lý do đầu là lý do chính:

| Lý do | Khi nào |
| --- | --- |
| `NO_KEPT_SELECTOR` | Không biến thể nào đạt giữ theo dõi. Đúng từ hôm nay |
| `DATA_STALE_OR_GAP` | Bar của BTC hoặc của tên trong list thiếu hoặc cũ |
| `LIST_INCOMPLETE` | Không đủ 18 tên, hoặc tải bar ngày thất bại quá 5% cặp |
| `LATE_RUN` | Chạy trễ quá 6 giờ sau stamp |
| `SEAL_SCOPE` | Một coin che xuất hiện ở bất kỳ đầu vào hay đầu ra nào |
| `ALL_SLOTS_NON_POSITIVE` | Cả ba ô đều cash, quy tắc đã đăng ký |
| `POLICY_EXPIRED` | Tuần trước không có lần chạy hợp lệ |
| `BOOK_TRIPWIRE` | Tripwire đã bắn |

Khi `DATA_STALE_OR_GAP`, `LIST_INCOMPLETE` hoặc `SEAL_SCOPE` khớp, book tới lượt giữ danh sách cũ và không chọn lại.

## Không làm

- Không đặt lệnh, không gọi endpoint riêng tư, không đọc khóa. Chỉ dữ liệu thị trường công khai.
- Không ghi vào `data/promotion_store`. Policy chỉ ở mức DRAFT, trong thư mục riêng của vòng này.
- Không nối router, không nối runner testnet. Selection track là quyết định riêng sau M3.
- Không đổi nhịp, không thêm ngưỡng điểm, không thêm biến thể.

## Kết quả nghiệm thu

Số đo của 2026-11-01. Phần này không đổi luật nào và không phải bằng chứng lợi thế.

**Port.** `tools/selection/acceptance.py` dựng lại nghiên cứu trên discovery bằng code của vòng này rồi so từng ô với file của nghiên cứu. Khác 0 ô trên 104.598.208 ô trọng số của 32 ứng viên, 3.268.694 ô Mem, R và chi phí, và cả 7 chuỗi của mỗi ứng viên. 65 list của biến thể chính và của `BEST_SO_FAR` khác 0 list. Điểm, gross, chi phí và net của hai book và EW trùng hash. Hai lần cắt ở thứ Hai 2022-10-31 và 2025-06-09 cho đúng list. Store dựng từ seed trùng từng ô với 274 cột không che.

**Book có mặt nạ trên W1 đến W4.** Một lần đọc trên một biến thể mới, trên dữ liệu đã đọc nhiều lần. Cơ sở của nghiên cứu: bậc, không drift, khớp ở open của stamp, 2020-07-13 đến 2025-06-30, 260 tuần.

| Book | Tổng | Sharpe | Max DD theo tuần | Gross exposure | Hơn EW có mặt nạ cùng exposure, bps/tuần |
| --- | ---: | ---: | ---: | ---: | ---: |
| Bốn book có mặt nạ | +388,4% | 1,00 | -30,1% | 0,65 | +24,8 |
| Book phase 0 có mặt nạ | +458,7% | 0,99 | -33,5% | 0,72 | +34,1 |
| EW có mặt nạ | +63,7% | 0,64 | -37,7% | 0,31 | |
| Hold BTC | +1.058,1% | 1,13 | -75,2% | 1,00 | +73,0 |

| Hơn EW có mặt nạ cùng exposure, bps/tuần | W1, 77 tuần | W2, 53 tuần | W3, 79 tuần | W4, 53 tuần |
| --- | ---: | ---: | ---: | ---: |
| Bốn book | -49,7 | +244,8 | -53,8 | +27,5 |
| Book phase 0 | -13,1 | +283,6 | -33,1 | -49,4 |

Phần dương gần như đến từ W2: ba cửa sổ còn lại trung bình khoảng -32 bps/tuần với bốn book và -30 với book phase 0. Book thua hold BTC cả về tổng lẫn Sharpe. EW có mặt nạ yếu hơn EW gốc (+109,7%, Sharpe 0,96), nên khoảng hơn EW không đọc được là lợi thế. Không mặt nạ cùng cơ sở, book phase 0 là +390,7%, Sharpe 0,96, khớp RESULTS, và bốn book là +370,8%, Sharpe 0,97. Gross thực của book có mặt nạ bằng 92,0% book không mặt nạ, vì phần che được phân lại cho tên còn lại.

**Diễn tập trên dữ liệu công khai thật.** Chạy trước khi sửa theo review, tới thứ Hai 2026-10-26. Lần đầu, với 12 list bù và 11 coin mới: 716 lời gọi, 81 MB, 9 phút 39 giây, peak 1,32 GB. Một tuần thường: 437 lời gọi, 19,6 MB, 4 phút 31 giây, peak 1,33 GB. Mọi lời gọi tới `data-api.binance.vision`, 388 cặp, 0 symbol che. Sau khi sửa, một lần chạy thật qua wrapper từ shell WSL, tới cùng thứ Hai: 715 lời gọi, 9 phút 43 giây, peak 1,3 GB, 0 lỗi. Chưa có thứ Hai nào chạy đúng lịch, và job chưa từng được Task Scheduler khởi động.

**Ngoại lệ của đường không che.** Để chứng minh port, `acceptance.py` dựng panel 286 coin gồm cả 12 coin che và tính return, trọng số, điểm cho chúng trên discovery, tức trước 2025-07-01. Đó là ngoại lệ của câu "không tính return cho chúng ở bất kỳ ngày nào" ở mục seal. Chỉ `acceptance.py` đi đường này, có test khóa, và `run_weekly` không gọi nó. Báo cáo nằm ở `data/selector_20261101_01/acceptance.json`, không bao giờ nằm trong `data/selection`.

## Chờ operator quyết trước list đầu tiên

Code đang chạy theo mục "Hiện tại" của từng việc. Không việc nào đổi nếu chưa có phán quyết ghi lên trang này.

- **Đổi mệnh giá ở forward.** Seed đã sửa tay 4 lần (COCOS, BTCST, BNX, STRAX). Đường forward chỉ nối bar thô, nên một lần đổi mệnh giá trên coin đang giữ sẽ thành một bước nhảy trong sổ và đi vào z. Hiện tại không có luật và không có lý do nào cho việc này. Sau phần seed có 0 bước giá trên 15% trên 43 coin.
- **Ngưỡng 6 giờ của `DATA_STALE_OR_GAP`.** Bảng lý do không nêu số. Hiện tại code đọc giới hạn của thư viện: bar cuối cũ quá 6 giờ, hoặc lỗ trên 6 giờ trong tuần cuối. Trên bar thật ngưỡng này khớp 0 trong 326 thứ Hai có list.
- **Coin của list cũ mà tải hỏng.** `DATA_STALE_OR_GAP` đọc list của thứ Hai đó, còn điểm đọc mọi list trong 90 ngày. Coin vừa rời list mà tải hỏng thì điểm tính trên bar thiếu và book vẫn nhận list. Hiện tại trang hiện `trễ lịch` và tên coin nằm trong `manifest.json`. Nếu muốn giữ book: tính `hourly_failed` lớn hơn 0 là `DATA_STALE_OR_GAP`.
- **Số cặp co lại.** `exchangeInfo` trả ít cặp hơn mà vẫn đọc được thì list vẫn đủ 18 tên, không lý do nào khớp. Với bảo trì thật đây có thể đúng luật. Hiện tại không có mức sàn. Thêm mức sàn là thêm luật.
- **Ngoại lệ của đường không che.** Xem phần kết quả nghiệm thu. Hiện tại ngoại lệ đang được dùng, operator chưa xác nhận.

## Vận hành

Code nằm ở `tools/selection/`, job là `scripts/ops/run_selection_weekly.sh`, mọi bản ghi nằm ở `data/selection/`. Task Windows chạy trên checkout chính, nên các lệnh dưới chạy ở đó, theo thứ tự của mục kế tiếp. Job dùng nice 19 và tự ghi `logs/selection_weekly.log`, nên kết quả của lệnh tay cũng nằm trong log đó. Có `SELECTION_ROOT` thì ghi `logs/selection_rehearsal.log`, file trang không đọc.

### Đưa vào chạy

Checkout chính không đi theo `origin/master`: code vào master chưa đưa code vào checkout đó. Thứ tự:

1. Chép thư mục seed sang checkout chính, phần `cp -a` ở mục "Cài seed". Làm trước khi merge: đây là bản duy nhất và worktree có thể bị dọn sau đó. Bước này chỉ thêm một thư mục git bỏ qua.
2. Merge code vào `origin/master`. `git add` từng file, không add `docs/proposals/SIGNAL_JOURNAL_DESIGN.md`. Chạy `python3 scripts/generate_project_map.py` và add `docs/PROJECT_MAP.md`. Bước này không đổi checkout chính.
3. Operator chốt các việc ở mục trên.
4. Trong checkout chính, chạy `.venv/bin/python scripts/ops/daily_check.py`. Dòng M1 phải kết thúc bằng PASSED, số chu kỳ từ 24 trở lên, và phải in `No issues.` Chưa PASSED thì dừng ở đây, không fast forward. Fast forward vào chính sáng thứ Hai thì report tự động lúc 01:10Z đến sau giờ chạy nên không dùng được.
5. Fast forward, ngoài cửa sổ phút :55 giờ UTC chẵn đến phút :06 giờ UTC lẻ, và khi không có gói research đang chạy, vì bước này đổi file `.py` dưới `scripts/`. Cycle testnet kế tiếp phải rc=0.

    ```bash
    cd /home/huythong/.qwenpaw/workspaces/trading
    git fetch origin
    git merge --ff-only origin/master
    ls -l scripts/ops/run_selection_weekly.sh tools/selection/run_weekly.py
    ```

6. Chạy `--seed-only` theo mục "Cài seed".
7. Trang đọc code mới khi server khởi động lại. Server là `.venv/bin/python scripts/ops/workspace_server.py`, cổng 8765. Đang chạy thì Ctrl+C trong terminal của nó rồi chạy lại từ checkout chính.
8. Đăng ký lịch theo mục dưới, sau bước 5 và bước 6. Không bỏ `-Only`. `Get-ScheduledTask -TaskName "TradingAgent-SelectionPaper"` phải thấy task.
9. Không chạy tay trên `data/selection`. Giữ máy bật và user đăng nhập lúc 00:07Z thứ Hai, tức 07:07 giờ máy.
10. Sau 00:20Z thứ Hai, đọc `logs/selection_weekly.log`, `data/selection/status.json` và `data/selection/cohort.json`. Log chưa có dòng `selection end` thì job còn đang chạy. Không dựa vào `LastTaskResult`: `conhost --headless` không trả exit code của job. Task không chạy thì chạy tay `scripts/ops/run_selection_weekly.sh`, bắt đầu trước 06:00Z, tức 13:00 giờ máy, mới còn đúng giờ và mở được cohort.

Nếu M1 chưa PASSED trước khoảng 05:30Z thứ Hai thì không fast forward: cohort lùi một tuần, không mất gì khác.

### Cài seed

Seed là ma trận của `selector_20261101_01`. Nó bị git bỏ qua và hiện chỉ có trong worktree `doc-sau-phan-tich-du-an-cc1551`, nên checkout chính phải có bản sao trước lần chạy đầu. Chép cả thư mục, vì `RESULTS.md`, bằng chứng và báo cáo nghiệm thu cũng chỉ có ở đó:

```bash
cd /home/huythong/.qwenpaw/workspaces/trading
mkdir -p data
cp -a .claude/worktrees/doc-sau-phan-tich-du-an-cc1551/data/selector_20261101_01 data/
```

Sau fast forward:

```bash
cd /home/huythong/.qwenpaw/workspaces/trading
scripts/ops/run_selection_weekly.sh --seed-only
tail -n 3 logs/selection_weekly.log
```

`--seed-only` kiểm hash theo `SEED.sha256`, rồi đổi seed sang store và bỏ cột của 12 coin che. Đúng thì log in `coins` 274, `dropped` 12 và kết thúc `rc=0`. Lệnh này chạy lại an toàn. Không chạy lệnh tay từ phút :55 giờ UTC chẵn đến phút :06 giờ UTC lẻ, vì đó là cửa sổ của chu kỳ testnet.

### Đăng ký lịch

Trong Windows PowerShell, không cần quyền admin. Dòng đầu chỉ in ra, dòng sau mới đăng ký và không đụng ba task còn lại:

```powershell
powershell -ExecutionPolicy Bypass -File \\wsl.localhost\Ubuntu\home\huythong\.qwenpaw\workspaces\trading\scripts\ops\windows\register_tasks.ps1 -Only Selection -WhatIf
powershell -ExecutionPolicy Bypass -File \\wsl.localhost\Ubuntu\home\huythong\.qwenpaw\workspaces\trading\scripts\ops\windows\register_tasks.ps1 -Only Selection
```

Lỗi `does not belong to the set "Runner,Health,DailyCheck"` nghĩa là checkout chính chưa fast forward, nên chưa có script mới.

Task `TradingAgent-SelectionPaper` chạy thứ Hai 00:07 UTC. Script đổi giờ đó sang giờ máy lúc đăng ký, trên máy này là 07:07 thứ Hai. Lần chạy đầu luôn ở phía trước: đăng ký sau 00:07 UTC thứ Hai thì lần đầu là thứ Hai tuần sau, và dòng `-WhatIf` in ngày đó. Task chỉ chạy khi user đang đăng nhập và không đánh thức máy. Máy ngủ lúc đó thì Task Scheduler chạy bù sau khi máy thức, theo Microsoft là trễ khoảng 10 phút, và trễ quá 6 giờ sau stamp thì bản ghi có `LATE_RUN`. User chưa đăng nhập lúc đó thì coi như task không chạy và làm theo bước 10.

### Lần chạy đầu

Lần chạy đầu là lần task tự chạy vào thứ Hai kế tiếp. Lần đó tự seed nếu store còn mới, dựng các list quy tắc từ 2026-08-10 rồi tải bar, nên lâu hơn các tuần sau.

Các list bù đó có survivorship: `exchangeInfo` chỉ biết cặp đang giao dịch ở ngày chạy, nên một cặp đã dừng giữa 2026-08-10 và ngày chạy vắng khỏi chúng. Mức ảnh hưởng chưa đo được, vì store không có bar của cặp đã dừng. Các list sau đó dựng đúng ngày nên không có việc này. Một cặp tải lỗi ở lần đầu, trong giới hạn 5%, cũng vắng khỏi các list bù: tên nằm trong `manifest.json`.

Lần đầu mà hỏng thì thứ Hai đó vẫn có bản ghi, không có list, và cohort lùi một tuần. Các list trước nó không mất: lần chạy tốt kế tiếp dựng mọi thứ Hai chưa có list, trừ thứ Hai đã có bản ghi xong của riêng nó.

Không chạy tay trên `data/selection` trước thứ Hai đó. Không có `--as-of`, lệnh lấy thứ Hai gần nhất, ghi `LATE_RUN` và book tới lượt vẫn nhận list, tức là thêm một lần chọn trước cohort vào sổ thật. Muốn diễn tập thì trỏ sang thư mục khác:

```bash
SELECTION_ROOT=/tmp/selection_rehearsal scripts/ops/run_selection_weekly.sh --no-fetch
```

`SELECTION_ROOT` trong repo chỉ nhận đúng `data/selection`. Ngoài repo thì tự do, trừ các kho runner đọc. `--no-fetch` không hỏi sàn nên không biết coin nào đã dừng: tuần có coin thiếu bar sẽ chờ, và trang hiện số tuần chờ.

Exit code 0 nghĩa là đã có bản ghi, kể cả khi tải dữ liệu hỏng. Exit code 2 nghĩa là bị từ chối và không ghi gì: ngày không phải thứ Hai, ngày chưa tới, ngày trước lần chạy đã xong, `SELECTION_ROOT` sai chỗ, hoặc một lần chạy khác đang giữ thư mục. Exit code 1 nghĩa là lần chạy chết giữa chừng, ví dụ đĩa đầy: chưa có `manifest.json` nên chưa tính là xong, log có traceback. Exit code 124 hoặc 137 nghĩa là job quá 50 phút và bị dừng, xử lý như exit code 1. Sau exit code 1, 124 hoặc 137, chạy lại `scripts/ops/run_selection_weekly.sh` trong 6 giờ sau stamp thì không có `LATE_RUN`.

`data/selection/cohort.json` được ghi một lần, ở thứ Hai đầu tiên chạy đúng giờ và không có lý do chặn. Ngày đọc nằm trong file đó. Không sửa file này.

### Đọc trạng thái

Trang đọc `data/selection/status.json`: một dòng `Vòng chọn giấy` trong nhóm Tự động và một thẻ trên bản đồ module. Trang chỉ quan sát, không có nút, không khởi chạy gì.

| Trang hiện | Nghĩa |
| --- | --- |
| `chưa tới lúc`, "chưa chạy lần nào" | Chưa có `status.json`, log không ghi job hỏng, và thứ Hai đầu sau `--seed-only` chưa quá 1 giờ. Đây không phải lỗi, nhưng trang không biết task đã đăng ký chưa. Chưa có log thì trang không có mốc nào và luôn hiện thứ Hai kế |
| `chạy tốt` | Lần chạy cuối sạch. `NO_TRADE` với `NO_KEPT_SELECTOR` là trạng thái thường |
| `trễ lịch` | Quá 1 giờ sau lịch mà chưa có bản ghi mới, kể cả thứ Hai đầu tiên: khi đó kiểm tra task và log. Hoặc lần cuối có `LATE_RUN` hay `POLICY_EXPIRED`, có coin tải bar giờ lỗi, hoặc có tuần sổ đang chờ bar |
| `lỗi` | Tripwire đã bắn, book tới lượt bị giữ, có bước lỗi, quyết định khác `NO_TRADE`, không đọc được `status.json`, hoặc job kết thúc rc khác 0 mà chưa có bản ghi nào |

Mỗi lần chạy có một thư mục `data/selection/runs/<ngày>/`: `decision.json`, `policy_draft.json`, `target.json` (`paper_only` true, `consumer` none) và `manifest.json` (hash, các lỗi, tên coin tải hỏng, giờ thật). `manifest.json` ghi sau cùng, sau cả `status.json`, nên thư mục có file này là lần chạy đã xong.

Tên coin trong list có ở `status.json` nhưng không lên trang. `data/selection/ledger.jsonl` chứa lợi nhuận: không mở trước ngày đọc, và đọc một lần.

### Tuần của sổ

Một dòng sổ là 42 stamp từ chủ nhật 20:00 tới chủ nhật 16:00, đặt tên theo thứ Hai đóng nó. Tuần đầu bắt đầu ở lần chọn đầu tiên nên có 41 stamp. Lần chạy thứ Hai đóng tuần vừa hết sáng đó, nên dòng của ngày đọc có mặt đúng ngày đọc.

Dòng sổ là cuối cùng, nên một tuần chờ cho tới khi mọi coin giữ trong tuần hoặc có trên list của tuần có bar ở giờ cuối tuần đọc, tức 21:00 chủ nhật, trừ khi sàn nói coin đã dừng: cặp không còn `TRADING` hoặc sàn trả -1121. Coin còn giao dịch mà thiếu bar thì tuần chờ, kể cả khi sàn trả 200. Lần chạy sau ghi các tuần chờ theo thứ tự. Trang hiện số tuần chờ.

### Xử lý từng lý do

Bản ghi của một thứ Hai đã xong không đổi nữa. Chạy lại không làm gì, và không xóa `runs/<ngày>` để chạy lại, vì bản ghi nói đúng điều lần chạy đó biết. Ngoại lệ duy nhất là lần chạy bị giết giữa chừng: chưa có `manifest.json` nên chưa tính là xong, chạy lại `scripts/ops/run_selection_weekly.sh` trong 6 giờ sau stamp thì không có `LATE_RUN`.

- `NO_KEPT_SELECTOR`: không làm gì. Lý do này có mỗi tuần, chỉ phán quyết của operator đổi được.
- `DATA_STALE_OR_GAP`: xem số thiếu, cũ, hở trên trang và `errors` trong `manifest.json`. Book đã giữ list cũ. Thứ Hai kế tải bù từ bar cuối đã lưu.
- `LIST_INCOMPLETE`: xem số bar ngày lỗi và số tên trong `decision.json`. Lặp lại nhiều tuần thì kiểm tra mạng hoặc bộ lọc cặp, không tự thêm tên.
- `LATE_RUN`: giữ máy thức lúc 00:07 UTC thứ Hai. Bản ghi vẫn có giá trị, nhưng thứ Hai đó không mở cohort.
- `SEAL_SCOPE`: coi là sự cố seal. Tắt task, tìm bước trong `errors` của `manifest.json`, không sửa store bằng tay, và operator quyết trước thứ Hai kế.
- `ALL_SLOTS_NON_POSITIVE`: không làm gì. Đây là quy tắc đã đăng ký: cả ba ô là cash.
- `POLICY_EXPIRED`: tự hết sau một lần chạy hợp lệ. Xem vì sao tuần trước không có lần nào.
- `BOOK_TRIPWIRE`: operator quyết định. Book vẫn ghi và lý do này không tự hết.

Hai lỗi không có lý do riêng:

- `ledger_read: JSONDecodeError` trong `errors`: `ledger.jsonl` có dòng rách. Mỗi lần ghi thay cả file nên việc này chỉ đến từ lỗi đĩa. Lần chạy giữ tripwire và số tuần của `status.json` trước đó, không ghi dòng mới, và trang ở `lỗi`. Tắt task, giữ một bản sao, bỏ dòng cuối bằng `sed -i '$ d' data/selection/ledger.jsonl` mà không mở file, rồi bật lại. Lần chạy sau ghi lại các tuần thiếu từ bar đã lưu.
- `cohort_read: JSONDecodeError`: `cohort.json` hỏng. Sổ chờ, không dòng nào ghi thiếu số tuần cohort, và file không bị ghi đè. Khôi phục từ bản sao, operator quyết nếu không có.

### Dừng

Trong Windows PowerShell:

```powershell
Disable-ScheduledTask -TaskName "TradingAgent-SelectionPaper"
Enable-ScheduledTask -TaskName "TradingAgent-SelectionPaper"
Unregister-ScheduledTask -TaskName "TradingAgent-SelectionPaper" -Confirm:$false
```

Dòng đầu tắt, dòng hai bật lại, dòng ba gỡ hẳn. Ba lệnh này không dừng lần đang chạy. Dừng task giữa lúc job chạy thì chưa kiểm được job Linux còn chạy tiếp hay không, nên đừng dựa vào nó. Giới hạn thật là 50 phút của `timeout` trong wrapper. Log có dòng `selection got SIG...` nghĩa là wrapper nhận tín hiệu còn job vẫn chạy. Muốn dừng ngay: `pkill -f tools.selection.run_weekly`, rồi xử lý như exit code 1.

Dừng không ảnh hưởng phần còn lại: không có gì đọc `target.json`, runner testnet không biết vòng này tồn tại. Bản ghi giữ nguyên trong `data/selection/`, ngày đọc của cohort không đổi. Sau khi bật lại, thứ Hai đầu tiên có thể ghi `POLICY_EXPIRED`, và trang hiện `trễ lịch` tới khi có bản ghi mới. Cả hai đều đúng luật.

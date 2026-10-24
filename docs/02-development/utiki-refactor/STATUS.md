# uTiki 現版交接與重構歷史

## 接手摘要（2026-10-25）

- 重構施工已完成；`944e934` 及[封存計畫](../archive/utiki-refactor/PLAN.md)是當時的起點與紀錄，**不是目前版本的功能上限、待辦或驗收依據**。目前以現行程式、測試、實際網站證據與使用者當次需求判斷正確性。
- 正式 source 入口為 `src/nodriver_tixcraft.py` → `platforms.utiki.nodriver_utiki_main` → `workflow`。`workflow` 持有唯一 per-tab runtime registry；`submission` 持有購票提交結果、未知結果處理與 checkout 通知去重；`refresh`、`rate_limit` 保留共用刷新及 429 協調。責任與模組索引見[程式碼邊界](../code-boundaries.md)和[結構索引](../structure.md)。
- uTiki 只在明列的精確 HTTPS 租戶主機啟用：中信 `tix.ctbcsports.com` 與味全 `tix.wdragons.com`。兩站共用購票流程，Cookie 設定與名稱政策各自獨立；只有中信支援頁內帳密及 fast 區域預取，只有味全啟用網站電腦配位。目前兩站各自明確使用官方 OCR 政策；新增站點須依其實證設定能力，未知主機不默認味全。
- uTiki 行為只讀 current `utiki` 設定；其他平台的設定與 migration、共用 SQLite refresh／429 coordinator 保持各自既有責任。付款仍由使用者手動完成。已完成重構當時採 source-only 交付，後續工作按當次需求決定驗證與交付範圍。
- 本工作樹在提交 `e7013bb0ebda3a968b0932d2356efe654f9ac392` 之後有多項**尚未提交**的安全修正、重構後產品改善、測試改名，以及本輪 checkout／租戶判斷／OCR／文件清理；接手時先查 `git status`，不要依本段假設 HEAD 已包含這些內容。本輪結果見下節。

## 本輪架構收斂與驗證（2026-10-25，未提交）

- Checkout 通知只在目前網址仍是該租戶結帳頁時處理；只有提交來源 URL 與目前 checkout 同源且符合頁型契約，才用提交 nonce 去重，其他情況用目前 location key。`workflow` 在等待健康檢查、提交協調與取消刷新後重核網址；`submission` 讀取對話框快照後也重核網址及 runtime，避免舊 tick 把已轉頁的提交誤記成功。通知測試一律 mock 聲音、Discord、Telegram。
- 正式主迴圈對已識別的 uTiki 頁面只呼叫 uTiki handler；首頁啟動避免 uTiki 查詢參數中的其他平台網域觸發其他平台初始化。設定頁以網址主機辨識 uTiki 租戶與各平台欄位，TixCraft soft-block 欄位亦以主機判斷。依使用者指定，本輪沒有改寫其他平台的正式主迴圈路由。
- 租戶 profile 使用明確的 OCR 模型政策；中信與味全各自指定現行官方模型。OCR factory 接收模型選擇，不再從原始 homepage 子字串推定租戶。租戶資料以 typed profile 傳遞，移除中間 legacy dict 轉換；Cookie 登入政策要求同一租戶的必要欄位完整，兩站現有登入方式維持。新增租戶仍須另外確認其可支援能力，不由中信或味全預設值代替。
- `STATUS.md` 留在原位作快速交接；已完成的 `PLAN.md`、`BATCH6_WORKPLAN.md` 及更早提案移至 `docs/02-development/archive/utiki-refactor/`，活躍文件改指向目前程式與本頁，歷史計畫不再設定現版驗收門檻。受改文件的本機相對連結檢查無缺失。
- 使用現有 Windows `.venv`：聚焦案例 **245 passed、184 subtests passed**；預設完整 `pytest -q` **404 passed、1 skipped、1 deselected、265 subtests passed**；合成本機 browser fixture **1 passed in 9.57s**。Touched Python `compileall -q`、`node --check src/www/settings.js`、系統 `pyflakes`（uTiki 核心改動模組）、`git diff --check` 均通過；最後一項僅有既有檔案的 LF/CRLF 提示。subagent 唯讀交叉審查所報的快照等待競態、設定欄位查詢字串碰撞、OCR 假主機匹配及文件歧義已修正並聚焦重驗。
- 本輪未操作真站、日常 profile、macOS 或 GitHub CI，亦未做打包。跨平台原始碼可用性仍待 macOS 實證；其他平台主迴圈目前仍採原有的網址子字串路由，依使用者指定留在本輪範圍外。沒有 commit 或 push。

## 歷史紀錄

以下批次、固定 SHA、當次測試數字及「本次／下一步」用語，均描述已結束施工階段的當時狀態，保留供追溯，不對目前版本設定門檻。

### 當時狀態與下一步

- 唯一產品行為基準是 `944e934546f3dd36b1b4891a5a4fa359b8ade17f`；`a47fd07106eae598c8b44972282c6621bf7f493c` 只作文件整理參考，不沿用其功能或歷史 PASS。
- 工作分支是 `codex/utiki-architecture-944e934`。批次 0–5 及批次 6 前架構清理已完成；最近提交並推送的清理點是 `e7013bb0ebda3a968b0932d2356efe654f9ac392`。該點之後的獨立設定服務安全修正、批次 6 與後續共用命名清理仍在同一未提交工作樹，回報時須區分。
- 批次 6 已按[修訂施工指令與執行計畫](../archive/utiki-refactor/BATCH6_WORKPLAN.md)完成本機 current 設定及正式 source 入口收斂；其後另獲授權進行共用命名清理及租戶對等收斂，結果見下列各節。原 PLAN 的下一項未完成驗收是取得 macOS arm64 原始碼路徑實證；在此之前不能宣稱當時的雙平台最終驗收全部完成。
- 使用者另行授權的味全龍 Cookie 登入已接線，使用獨立 `accounts.wdragons_cookie`。2026-10-24 在專用暫存瀏覽器以現有設定實跑，已確認登入及 10/25 日期頁原生點擊；日期頁修正及驗證見下節。這些是重構後新增改善，不屬 `944e934` 等價範圍。
- 使用者另行授權的劃位衝突恢復優化已完成本機驗證：網站先換成已清空的新劃位 document 時，可省略一次重複的衝突恢復 reload；條件與未驗證範圍見下節。這是 `944e934` 之後的刻意改善，不能回寫成原版行為。
- 最近另行授權的中立命名與文件收斂：Cookie 刷新理由已由各租戶登入政策明確提供，保留兩站原字串；共用測試檔與測試類名改為 `utiki`，中信來源的 fixture 資料仍標示 `ctbc`。結果見下節；這不是新增租戶或購票功能。
- 尚未驗證 macOS arm64 原始碼使用與 GitHub CI；使用者曾回報味全龍流程到達 checkout。2026-10-24 的雙暫存實例真站測試已取得同座位先成功加車、後送出座位衝突及網站先換成已清空新 document 的證據；日誌未出現衝突恢復 reload，後續因該區無座位而按原間隔正常刷新。當時發現的成功彈窗字樣差異已於 2026-10-25 做局部分類修正及本機驗證，尚未重新真站送出。味全龍電腦配位至票數填入已於專用暫存瀏覽器實測。Windows x64 使用既有 `.venv`（CPython 3.11.9、pytest 9.0.3）。

## 批次 6 後：Cookie 刷新理由與共用測試命名（另行授權）

- 主入口原以「中信，否則味全」選 Cookie 注入後的刷新理由。`TenantAuthProfile` 現在要求每個租戶明確提供 reason；中信仍為 `apply CTBC cookie session`，味全仍為 `apply WDragons cookie session`。正式入口在原注入成功時點傳入該值，`apply_local_interval=False`、`allow_unknown_document=True` 與後續登入觀察未變。reason 同時參與同 tab 未完成刷新請求的比對，並非只有日誌；沒有改 `refresh.py`、coordinator、429 或 reload 時序，也未加入第三站。
- 五個跨租戶共用測試／support 檔及四個共用測試類名改用 `utiki`，相關 import 與可執行文件命令同步更新。`CTBC_TEST_*`、`make_ctbc_*`、`tests/fixtures/ctbc/` 仍標明原中信樣本來源；舊跨頁 marker 字面值與 944 固定 commit 的來源連結未改。STATUS 中過往執行結果保留當時檔名與數字。
- 修正文件現況：中信支援自己的 Cookie 與頁內帳密，味全支援自己的 Cookie 而無帳密備援；共用 `utiki.ticket_type_keyword` 不再描述成中信專屬，OCR 說明列出目前兩站。純政策／正式首頁聚焦 **77 passed、109 subtests passed**；改名後四份 uTiki 測試 **303 passed、189 subtests passed**；預設完整 pytest **397 passed、1 skipped、1 deselected、263 subtests passed**；合成本機 browser fixture **1 passed in 9.21s**。Touched Python `py_compile` 與 `git diff --check` 退出碼 0。未操作真站或日常 profile，未執行 macOS、CI 或打包。

## 批次 6 後：劃位衝突新文件恢復（另行授權）

- 先前味全龍實站延遲送出曾觀察到座位衝突彈窗，網站回應流程先換到選位、票數與四碼皆空的新 document，而程式仍排入一次按原間隔的衝突恢復刷新。該次未記錄網路請求發起者，不能只憑時間斷言後一次載入的發起端；本輪以原始碼與合成案例確認現有分支在網站先換頁時確會排刷新。
- `submission` 保留彈窗實際所在 URL 與 document token。僅在原送出紀錄**沒有自動提交 nonce**、目前設定仍為人工送出的劃位頁、同一購票 identity、無進行中刷新／probe，且下一輪原健康檢查已確認新 document 正常後，才以唯讀觀察核對同一 URL、新 token、完整頁面、無可見彈窗、選位／票數計數／四碼皆空，解除該筆衝突 blocker，省略**這一次**恢復用 reload。若網站在本輪健康檢查後才換頁，先保留 blocker 至下一輪分類，不在舊健康結果下繼續選位。解除 blocker 的本輪不再選位；下一輪仍由原購票模組決定選位或按原間隔刷新。合成案例確認新頁座位為零時不發衝突恢復 reload，下一輪仍排正常缺位刷新。來源不明、同頁衝突、未清空、票數頁、自動送出（即使設定隨後熱更新為人工）及已發出的刷新仍沿原流程；CAPTCHA 拒絕、未知提交及共用 429 協調未改。
- 本輪聚焦 `test_ctbc_dom.py`、`test_utiki_dom_contract.py`、`test_ctbc_refresh.py` **234 passed、89 subtests passed**；預設完整 pytest **396 passed、1 skipped、1 deselected、260 subtests passed**；合成本機 browser fixture **1 passed in 9.13s**。Touched Python `py_compile` 與 `git diff --check` 通過；系統 `pyflakes` 只有測試檔兩項既有 unused import 診斷，沒有本輪新增行診斷。未操作真站、日常 profile、macOS、CI 或產品打包；實站節省的時間仍待後續使用者操作驗證。
- 2026-10-24 後續真站單次驗證：使用 `wdragon` 登入資料衍生的暫存設定與程式新開的專用瀏覽器，將選位改為 manual、OCR 保留填碼但關閉自動提交，並停用這次測試的外部通知。原偏好的 223 區進入後已無可選位；改選當下列表顯示餘 1 張的 219 區，實際選中 1 位、票種計數 1、四碼已填。保持同一 document 等候超過 5 分鐘後，導覽遮罩攔截第一次滑鼠事件，未觀察到提交請求或彈窗；關閉導覽並核對按鈕實際可點後，單次送出得到網站「加入購物車完成，請於 15 分鐘內完成結帳」回應。這次**沒有座位衝突**，因此不能視為新文件衝突快路的真站驗證通過。測試購物車已在網站按「清空購物車」，網站回覆「購物車內無任何票券」並返回首頁；未付款。獨立程式已停止，暫存設定與診斷腳本已清理，原 `wdragon` 設定未改。
- 2026-10-24 雙實例真站驗證：使用更新後 `wdragon` Cookie 衍生的兩份暫存設定、兩個專用瀏覽器 profile；測試覆寫為 manual 劃位、一般「全票」、OCR 填碼但不自動送出，停用外部通知。兩個分頁經原選區動作進入 221 區，均核對為 18 排 24 號、四碼已填。B 人工點擊加入購物車一次，網站顯示「加入購物車完成，請於 15 分鐘內完成結帳！」，購物車頁也列出同座位。接著 A 人工點擊一次，程式讀到「有部分座位已被訂走，請再重新選取座位！」；瀏覽器導航事件顯示 `scriptInitiated` 的同 URL 載入，新 document 完整且選位與四碼皆空。程式記錄彈窗在關閉前已換頁，隨後沒有記錄 `selected seats were taken` 衝突恢復刷新；因該區當時已無座位，轉而記錄 `requested seats are unavailable`，約 5.6 秒後按原間隔執行正常缺位刷新。這次有真站衝突與新文件證據；一般缺位刷新仍會發生，不能宣稱不再刷新或量化節省時間。
- B 的成功彈窗使用「加入購物車完成」而沒有「成功」二字；當時的 `classify_submit_dialog()` 對該實際字樣回傳 `rejected`，B 程式反覆記錄 `Unknown or ambiguous dialog preserved`，未自行關閉成功彈窗。這是該次另外發現的問題，當輪未更改 production 或測試預期；後續局部修正見下一項。測試後人工確認清空 B 購物車，網站回覆「購物車內無任何票券」並返回首頁；兩個暫存程式已停止，未付款，原 `wdragon` 設定未改。
- 2026-10-25 成功彈窗分類局部修正：以 2026-10-24 真站取得的「加入購物車完成，請於 15 分鐘內完成結帳！」字樣加入成功分類，原「成功加入購物車」仍接受；未符合該格式的「完成失敗」或「請稍後再試」仍維持拒絕及保留彈窗。延遲成功與直接處理彈窗案例使用真站字樣，核對成功結果、關閉彈窗及不重送。受影響三檔 **228 passed、173 subtests passed**；預設完整 pytest **397 passed、1 skipped、1 deselected、263 subtests passed**；合成本機 browser fixture **1 passed**。Touched Python `py_compile` 及 `git diff --check` 通過。此次未重新真站送出，因此真站對修正後彈窗自動關閉與 checkout 的端到端結果仍未驗證；未操作付款或發送外部通知。

## 批次 6 後：味全龍電腦配位（另行授權）

- 使用者授權以 `wdragon` current 設定操作真站並啟用電腦選位。設定原已有 `utiki.seat_selection_mode=computer`，但味全龍租戶的 `supports_server_assignment` 原為 False；中信仍維持 False。專用暫存 Chrome profile 的實站診斷確認登入後可從 10/25 日期頁到選區頁；目標區域在地圖上有符合 `Send` 的動作，銷售列表對應列卻沒有 `onclick`，因此原電腦配位提交被拒絕。現在只在地圖動作、區域名稱／價格／餘位及唯一銷售列互相吻合，且該列**沒有** `onclick` 時點擊已核對的地圖區域；有列動作時保留原列點擊及動作核對，空白或不符的列動作仍拒絕。
- 真站進入 `/UTK0201_001` 票數頁後，頁面沒有 `#GROUP_ID` hidden input，但網址有唯一且符合選區目標的 `GROUP_ID`，其餘隱藏 ID 與票數表存在。目的頁 attest、票數填入與送出前 DOM 稽核現在允許這一種「快照時即不存在」的 group hidden input；若 hidden input 原本存在而後消失、出現不符值或網址 group 不符，仍拒絕或視為狀態過期。該頁也沒有 `<form>`；原提交核對只接受 POST form。實站按鈕使用固定 `addShoppingCart();return false;`，其函式透過同源 `_vr` 路徑及 POST helper 將資料送至該頁的 `ADD_SHOPPING_CAR`。現在在原 form 分支之外，只接受此已觀察到的按鈕／函式／POST 目的地組合，其他提交目標仍拒絕。
- 修正後使用獨立暫存 profile 實跑正式選區 workflow：電腦配位提交、跨文件目的頁核對與票數頁正式流程均成功，選中 1 張票並建立有效提交計畫。CAPTCHA／加購物車在工作流程測試中被 mock；另於該實站頁放入合成四碼並覆寫按鈕的 `click()` 為只計數 stub，完整執行送出前 DOM 稽核，回傳 `DISPATCHED` 且僅呼叫 stub 一次。**沒有真實加購物車、checkout 或付款**；因此不以此聲稱完整購票已驗證。未操作日常 browser profile，未輸出 Cookie 或改變刷新／429 時序。這是 `944e934` 之後經授權擴充味全龍租戶能力，不是原架構重構的等價要求。
- 驗證：純邏輯與 contract 聚焦 **107 passed、110 subtests passed**；預設完整 pytest **388 passed、1 skipped、1 deselected、252 subtests passed**；合成本機 browser fixture **1 passed in 9.02s**。fixture 覆蓋缺少 group hidden、無 form 的固定 POST handler、錯誤 onclick／目的地的拒絕及既有 form 路徑。Touched Python `py_compile` 與 `git diff --check` 退出碼 0。現有 `.venv` 未安裝 pyflakes；系統 `pyflakes` 的兩項診斷是既有測試檔 unused import／unused local，與本次新增行無關。

## 批次 6 後：味全龍 Cookie 登入（另行授權）

- 先前另一工作目錄的味全龍 Cookie 診斷曾觀察到 `WDRAGONS|...` Cookie、頁面 `_ul=1` 與一次獨立瀏覽器登入成功。它是本輪站點政策的線索，不視為目前版本或網站現況已重新驗證。舊做法使用中信共用欄位，未搬入本輪。
- 在 current `accounts` 新增獨立的 `wdragons_cookie`，中信 `ctbc_cookie` 原值及帳密欄位不變。設定頁依精確 HTTPS 租戶網址顯示各自 Cookie 欄位，讀取、儲存與未存標記已接線；現有 profile 缺新鍵時由通用預設補空值，不增加專用設定 upgrader。快速建立按鈕與其複製整份設定的既有行為未改。
- 味全龍使用既有 uTiki Cookie 解析／清除／注入函式，僅接受 `WDRAGONS|` 名稱，注入 host 固定為 `tix.wdragons.com`；為避免 parent／host 同名 Cookie 同時送往售票站，清除範圍涵蓋 `wdragons.com` 及其子網域中符合名稱規則的 Cookie。這是依 Cookie 範圍作的設計選擇，不表示已確認實站原始 Cookie 的 Domain 屬性。正式首頁入口須確認設定首頁和當前 tab 屬於同一支援租戶才注入。中信仍用原 `CTBC_` 規則及原 reload reason；味全沿相同 reload 等待時點與 `_ul` 觀察。`_ul` 不是購票成功證明，未登入不自動重試。味全帳密登入仍不支援，也不回退中信帳密。注入及重新載入例外日誌只記錯誤類型，不記可能包含 Cookie 值的例外文字。
- 當次驗證：五份聚焦測試 **199 passed、148 subtests passed**；預設完整 pytest **385 passed、1 skipped、1 deselected、242 subtests passed**；合成本機 browser fixture **1 passed in 9.07s**。Touched Python `py_compile`、兩份 JS `node --check` 與 `git diff --check` 退出碼 0。全域 `pyflakes` 對 touched 檔仍有主入口星號 import、`settings.py` 未使用 import 等既有診斷；本輪新增行無診斷。合成案例覆蓋兩站 Cookie 名稱／網域隔離、正式首頁路由與 reload、設定 API 保存、欄位可見性和錯誤日誌不含合成 Cookie 值。當時未執行真站、macOS 或 CI；味全原始 Cookie 的 Domain 屬性未在該輪查得。

## 批次 6 後：味全龍日期頁 10/25 購票按鈕（真站定位與另行修正）

- 2026-10-24 使用者授權真站診斷。沿用 `wdragon` current 設定，以獨立實例及暫存 browser profile 實跑；Cookie 注入與 `_ul` 登入觀察成功，但程式持續記錄 `no purchasable date matched`，沒有點選 10/25。只讀 DOM 快照確認日期表有 4 列，10/25 列有唯一可見、未停用的購票按鈕，日期關鍵字符合、排除關鍵字未命中。原選場器把按鈕分類為不支援，導致候選數為 0。
- 該按鈕的 `onclick` 是兩段固定形狀的 `appier(…); location.href=…`，後段目的地為同站 HTTPS 的 `/UTK0204_` 選區頁。原解析器只接受單獨的 `location.href=…` 或其他既有安全格式；`944e934` 已有此限制，所以這是目前味全頁面形式與原支援範圍的落差，不能記成架構搬移造成的回歸。當時修正只在味全日期頁辨識「兩個字串參數、兩組字串鍵值的 Appier 呼叫，接著單一 `location.href`」形狀；原同來源、選區頁型、查詢鍵與商品 ID 核對仍由 `_parse_date_target_url` 執行。提交仍按原 DOM 指紋核對後原生點擊；當時中信與其他腳本形式不放寬。後續共通化見下節。
- 聚焦日期／DOM／contract 測試 **217 passed、157 subtests passed**；預設完整 pytest **386 passed、1 skipped、1 deselected、247 subtests passed**；合成本機 browser fixture **1 passed in 8.90s**。新增案例覆蓋 10/25 選場、味全原生點擊及跨站、錯誤商品、額外腳本、非 Appier 呼叫的拒絕。修正後以另一個暫存 profile 只執行一次正式 `nodriver_utiki_main` 日期頁 tick：真站登入成功、快照 4 列、選場計畫存在，選中 10/25 並從 `/UTK0201_` 進入 `/UTK0204_`。該次刻意停在選區頁，未驗證後續選區、票數／座位、提交、checkout 或付款。未輸出 Cookie／完整設定、未操作日常 browser profile；未調整刷新間隔或 429 保護。

## 批次 6 後：共通日期按鈕格式與租戶限制盤點（另行授權）

- 使用者後續確認，固定形狀的 `appier(…); location.href=…` 是已支援 uTiki 租戶共用的日期按鈕格式，不應以中信或味全名稱限用。移除上節當時的味全限定後，仍由既有解析器核對已登錄租戶、HTTPS 同來源、日期／選區頁型、唯一查詢鍵及相同商品 ID；未把未登錄的網站自動加入支援清單，也未接受任意 JavaScript 前綴。
- 中信原有的 `fast_area_prefetch` 可略過按鈕原生點擊。帶 Appier 前置事件的日期按鈕在所有租戶均以原生點擊提交，保留其事件執行；單獨 `location.href` 的中信快速路徑與租戶 profile 能力不變。這是對按鈕格式的共同處理，不是新增站點專屬設定。
- 唯讀跨模組盤點未找到另一個已證實的共通購票格式被兩站名稱誤擋。站點 allowlist／路徑、Cookie 與帳密憑證、fast 與 server-assignment 能力仍是實際租戶差異。共用 OCR factory 目前以兩站 host 字串選官方模型；這是尚待新站 CAPTCHA 證據的模型政策，不能從共通導頁格式直接推定新站應使用同模型，本輪不變更。主入口「中信／否則味全」的 Cookie reload reason 當時未改動；它除診斷外，也是同一分頁未完成刷新請求的 reason 比對值，新增租戶不能誤用其他站字串。後續另行修正見本文件最新一節。
- 驗證：相關純邏輯、DOM、contract、refresh 測試 **293 passed、178 subtests passed**；預設完整 pytest **387 passed、1 skipped、1 deselected、252 subtests passed**；本機合成 browser fixture **1 passed in 8.93s**。未再操作真站或日常 browser profile；新格式在中信實站與未登錄的新租戶仍待各站實際頁面證據。
- 後續技術債清理將租戶 profile 的快速預取能力、日期按鈕可否略過原生點擊，與場次計畫的最終快速預取資格分開表示。URL 解析只回傳已核對的目的地；按鈕分類記錄格式語意；選場及提交前核對均由兩者計算同一資格。移除導覽目標中未使用的重複旗標，並把 native 流程日誌改為描述按鈕需求。中信 bare `location.href`、兩站 Appier 原生點擊及原目的地核對行為不變；新增案例確認改寫 Appier 計畫資格不能通過快照核對。此次清理的相關測試 **293 passed、178 subtests passed**，預設完整 pytest **387 passed、1 skipped、1 deselected、252 subtests passed**，本機合成 browser fixture **1 passed in 9.00s**；未再操作真站。

## 批次 6 後：uTiki 租戶對等與中立命名收斂（另行授權）

- 中信與味全仍由 `profiles.py` 的各自 allowlist 與政策辨識，正式共用執行路徑不以中信命名。Cookie Header／JSON 解析、Cookie 清除／注入、頁內帳密填寫改為中立的 `parse_utiki_cookie_string`、`nodriver_utiki_inject_cookies`、`_fill_utiki_inline_login`；解析時以租戶 profile 指定名稱前綴與分隔條件。當次中信規則仍是 `CTBC_` 且須含 `|`，`accounts.ctbc_*` 與啟動、reload、`_ul` 觀察仍只用於中信；味全 profile 當時尚未啟用 Cookie 或帳密自動登入，也沒有新增設定鍵。舊 `platforms.ctbc.nodriver_ctbc_inject_cookies` 只核對中信租戶後轉接共用函式。
- 跨站共用的 fast marker 讀取／刪除／還原 helper、原生導航及提交診斷名稱改為 `utiki`；只在中信啟用的 fast 預取／dispatch 保留 `ctbc`。既有 `ticketsHunter.ctbc.fast-navigation.v1` sessionStorage key 與 `data-tickets-hunter-ctbc-area-commit` DOM marker 字面值保留，避免改變既有 tab 的重入／對帳協議。移除無 caller 的 `CTBC_MAP_DOMAIN` 常數。測試 fake 的腳本辨識標記同步改為中立名稱。
- 當次設定頁將兩站的共用欄位歸入 `utiki` 顯示群組，另按精確 HTTPS hostname 顯示中信專屬 Cookie／帳密欄位；切換中信／味全時會重算租戶可見性。實際 current `utiki` 行為鍵、`accounts.ctbc_*` 讀寫及其他平台欄位未改。共用 checkout 仍沿原通知去重時點，但 Discord／Telegram 平台名稱改由已路由的租戶 profile 提供，味全不再收到「CTBC Sports」標籤。
- 驗證：七份聚焦測試 **303 passed、173 subtests passed**；預設完整 pytest **376 passed、1 skipped、1 deselected、238 subtests passed**；合成本機 browser fixture **1 passed in 9.55s**。Touched Python `py_compile`、uTiki production `pyflakes`、`node --check src/www/settings.js` 及 `git diff --check` 退出碼 0。新增的設定頁可見性測試使用 Node VM 執行實際 JavaScript 並以合成 DOM 列驗證切站，不會讀取真實設定。未操作真站或日常 profile，未做 macOS／CI 驗證。
- 該輪是經使用者另行授權的命名、呈現與通知站名修正，並非 `944e934` 原重構的等價要求；當時沒有新增味全登入、富邦站點辨識、永久通知去重或新的購票資格。

## 批次 6 後：共用命名清理（另行授權）

- 當次將共用的 workflow、日期／區域、導航、購票、提交與 CAPTCHA Python 識別名稱由 `ctbc` 改成 `utiki` 或實際職責；正式 source 入口與 `workflow.nodriver_utiki_main` 同名接線。舊公開 `platforms.ctbc.nodriver_ctbc_main` 只作無狀態轉接。當時中信專用 Cookie／帳號、inline login、目前僅中信啟用的 fast 預取及其既有 marker 格式、CTBC 網域／tenant 名稱保留專屬命名；後續租戶對等收斂見上節。共用 native／目的頁流程仍沿用原 marker 核對。支援網站仍須經既有 profile allowlist，不因共用名稱改變而自動支援新網站。
- 當次設定頁三個共用欄位的 DOM id／JavaScript 變數／說明 key 改為 `utiki_*`，瀏覽器快取版本同步更新；實際 current JSON 仍是原 `utiki.seat_selection_mode`、`utiki.ticket_type_keyword`、`utiki.seat_sorting`，沒有建立新設定格式。當時 `PLATFORM_MAP` 的 `ctbc` 顯示分組、帳號鍵、跨頁 marker key、reload reason、log 與通知文案仍保留原值；後續已依上節收斂共用顯示、診斷與通知站名。
- 驗證：四份 uTiki 相鄰測試 **285 passed、168 subtests passed**；設定測試 **10 passed**；寫檔收尾後重跑完整預設 pytest **372 passed、1 skipped、1 deselected、238 subtests passed**；合成本機 browser fixture **1 passed in 9.59s**。另以專用暫存 browser session 和 `127.0.0.1` 合成設定服務檢查味全龍設定頁：三欄載入 current 值、修改後顯示未儲存狀態、Save payload 只更新原三個 current JSON 鍵，中英文排序說明可開啟；舊 `ctbc` 欄位被動保留，帳號鍵未變。這是合成 UI 證據，並未操作真站或日常 profile。Touched Python `py_compile`、uTiki production `pyflakes`、兩個 touched JS 的 `node --check`、正式 source CLI `--help` 與 `git diff --check` 均退出碼 0。三位 subagent 分別審查命名範圍、行為邊界及測試／前端接線；發現的 STATUS marker 範圍敘述已修正並重審，沒有剩餘 finding。
- 本輪僅做命名與說明精確化，沒有擴大 tenant allowlist、把味全龍使用中信 Cookie、改 fast 導航適用站、改購票資格／導航／OCR／429 判斷或新增持久相容層。macOS arm64、GitHub CI、真站流程仍未驗證。

## 批次 6：current 設定與正式 source 入口

- 唯一 uTiki 行為設定來源是 current `utiki`。`settings.py` 不再建立 `ctbc` 行為鏡像，不再用舊鍵補值或雙寫；`settings.js` 只從 current 鍵顯示並寫回；workflow 的原查詢點只讀 current／原預設；主迴圈熱更新名單只移除舊 `ctbc` 行為 section。原 allowlist 的其他欄位、指定 `advanced` 子鍵及讀取時點未改。`accounts.ctbc_*`、其他平台的 normalization、共用 refresh／429 SQLite coordinator 都保留。
- 輸入的歷史 JSON 若仍有 `ctbc` 欄位，通用設定處理可被動保留該未知欄位，但它不再補入 current、控制 uTiki runtime、被 UI 鏡像寫入或作為熱更新來源。這不是舊設定 upgrader 或主動清理器；重新填寫 current 設定是本次明確允許的交付範圍。
- 批次 6 當時的正式 source 呼叫鏈是 `nodriver_tixcraft.py` → `platforms.utiki.nodriver_utiki_main` → `workflow.nodriver_ctbc_main` → 各責任模組。後續獲授權的共用命名清理已把 workflow 的實作入口改為 `nodriver_utiki_main`；`platforms/ctbc.py` 僅保留舊名 `nodriver_ctbc_main` 作無狀態 facade，沒有正式 source caller。workflow 的 `_state` 仍是唯一 tab runtime registry；`is_utk_sports_url` 及中信專用啟動條件下的中立 Cookie 注入函式仍有正式 caller。既有 build spec 仍列 facade 為 hidden import，打包不在本次範圍，本輪保留既有配置與公開名稱。
- 目前 `src/platforms/utiki/` 為 15 個 Python 檔、10,718 實體行（含空白與註解；944 原起點為 7 檔、9,718 行）。批次 6 未新增模組、runtime record 或第二套 owner，只收斂既有設定接線；數量用於規模回報，不作每檔配額。
- 測試支援設定改為只提供 current `utiki`；舊相容測試改驗 current default、讀取／儲存、缺鍵／非法值預設、legacy-only 不補值、`--input` 載入與原熱更新、帳號不熱切換。原有設定服務 POST/token 安全測試保留，未把獨立安全變更寫成 944 等價。正式 source 在 Windows x64 以 `src/nodriver_tixcraft.py --help` 檢查 CLI 與匯入鏈；這是離線入口檢查，不代表真站購票流程。
- 設定頁另以專用 Playwright CLI session、`127.0.0.1` 合成靜態服務及 mock 設定 API 執行一次 UI probe：頁面從 current `utiki` 載入三欄，沒有採用衝突的舊 `ctbc` 值；操作三欄後按 Save，擷取的 payload 只有 current 三欄變為新值，輸入中原有的舊 `ctbc` 值被動保留而未鏡像更新，`accounts.ctbc_*` 鍵仍在。這是合成設定頁讀寫證據，不是實際設定服務或真站完整驗收；probe 使用的暫存 browser session 與本機服務已關閉。
- 驗證：設定／純邏輯聚焦 **75 passed、87 subtests passed**；受影響 DOM／refresh 聚焦 **220 passed、81 subtests passed**；完整預設 pytest **372 passed、1 skipped、1 deselected**；合成本機 browser fixture **1 passed in 8.95s**。Touched Python `py_compile`、`node --check`、Windows CLI `--help`、`git diff --check` 均退出碼 0。`pyflakes` 可執行，但全檔有既存 unused import／star import 診斷；本次沒有把它們當作批次 6 功能改動。未執行產品打包、真站、macOS 或 CI。

| 同情境組 | 944 基準與既有斷言 | 本次可重複證據 | 結論 |
| --- | --- | --- | --- |
| 日期／區域、native／fast、人工接手 | 944 原分支及批次 0／1 characterization；後續模組搬移未改預期 | `test_ctbc_pure.py`、`test_ctbc_dom.py`、`test_utiki_dom_contract.py` 聚焦通過 | 批次 6 未改購票分支；既有案例持續通過 |
| 座位／票數、CAPTCHA、未知提交、checkout | 944 原分支及批次 1 必要案例；後續同情境斷言 | 同上 DOM／contract 測試及合成 browser fixture 通過 | 批次 6 未改提交或通知流程；現有案例持續通過 |
| 刷新、暫停、429 | 944 原分支與既有 refresh 測試 | `test_ctbc_refresh.py` 聚焦通過 | 批次 6 未改 refresh／coordinator；現有案例持續通過 |
| 舊 `ctbc` 行為設定 | 944 會補值、鏡像及 fallback | 新 current-only 設定案例通過 | 使用者明確縮減相容範圍，非購票重構的等價宣稱 |
| 設定服務安全介面 | 944 的 `/sendkey` 與六控制 GET 行為 | 獨立安全測試在批次 6 前已通過，本批聚焦仍通過 | 另行授權的刻意安全變更，不列入本次 uTiki 等價結論 |

表中購票與刷新結果沿用已在 944 確認的行為案例及此次通過的現行測試；沒有為本輪另起 944 工作樹做新的雙版本同步執行。macOS／真站結果仍未驗證。

## 批次 6 前獨立設定服務安全工作（非批次 6）

- 使用者授權的本輪範圍是局部修正 `/sendkey` 的目錄逃逸與寫入假成功，並有界評估六個副作用 GET；不做全域 XSRF／Host 改造或批次 6。Repository 搜尋只找到 `/sendkey` handler／route，未找到內部 producer／consumer；外部 caller 與合法 token 全集仍未知，不自行限定為 UUID。
- `/sendkey` 保留合法 JSON object、原本不限定 Content-Type 的解析、`<token>.tmp` 與成功時 `{"return": true}` 的介面。token 須是可作單一跨平台檔名片段的非空字串，拒絕路徑分隔、drive／UNC、Windows 保留檔名及控制字元；既有 symlink 目標也不能把寫入導向其他路徑。無效輸入回 400，寫入 I/O 失敗回 500／`return: false`。寫入只在此 handler 改為可觀察失敗；共用 `util.save_json`、CORS 與其他設定端點未改。這不等於已為 `/sendkey` 加上請求來源授權，也不宣稱防止有檔案系統權限的本機程序在檢查與寫入之間競態替換目標。
- 六個控制 GET 在隔離 Tornado HTTP 測試中，帶 `Origin: https://external.invalid` 均得到 200 並執行被 mock 的副作用；沒有啟動實際 bot、重設真設定或送真訊息。另以 Headless Chromium 155 從合成 `https://public.test/` 頁面對 loopback 暫存服務發出 fetch 與圖片請求，該次均被 Local Network Access 權限限制擋下，伺服器未收到；直接本機 HTTP 請求得 204，證明測試服務可達。這只描述該瀏覽器／來源／權限條件，不推論其他瀏覽器或導航方式，也不把瀏覽器防護當成伺服器授權。此階段仍只評估，當時六個 GET 的方法、正式 UI caller 與 XSRF 設定未改；後續修正見下節。
- `/sendkey` 新測試先在舊 handler 證實無效輸入與路徑逃逸斷言失敗；修正後聚焦設定測試 **24 passed、1 skipped、21 subtests passed**，預設完整 pytest **365 passed、1 skipped、1 deselected、196 subtests passed**。Windows 本機無建立 symlink 權限，對應測試以 `WinError 1314` 跳過；其他有效／拒絕／I/O 失敗案例已執行。Touched Python `py_compile` 與 `git diff --check` 均退出碼 0；`pyflakes` 未新增診斷，`settings.py` 原有未使用 import／變數診斷仍在。未操作真站、日常 browser profile 或產品打包；未執行 macOS／CI，也未 commit／push。

### 六個控制端點的後續獨立修正

- 使用者後續授權先修六個副作用 GET，再繼續批次 6。`/shutdown`、`/pause`、`/resume`、`/stop`、`/run`、`/reset` 改為只在 POST 執行；舊 GET／HEAD 回 405。這是對 `944e934` HTTP 介面的刻意安全變更，不列作 uTiki 等價重構。已找到的正式設定頁 caller 同步改用 POST；未版控的個人 GET 腳本若存在，須自行改用新介面。
- 六個控制 handler 在副作用前局部檢查本機 Host、存在時的同源 Origin 及 Tornado XSRF cookie／`X-XSRFToken`。靜態設定頁從無 CORS、不可快取的 `/control-token` 取得 token。沒有全域啟用 `xsrf_cookies`，其他設定 POST／DELETE、`/sendkey` CORS 與 uTiki runtime 均未變。前端保留儲存成功後才 `/run`、重設成功後才更新表單、停止確認及逐實例暫停；「全部暫停」改為等該次各請求結束後再刷新，失敗不自動重送。
- 聚焦設定測試 **29 passed、1 skipped、63 subtests passed**；預設完整 pytest **370 passed、1 skipped、1 deselected、238 subtests passed**。新增隔離 HTTP 案例以 mock 證明六個舊 GET／HEAD、缺少或錯誤 token、外部 Origin／Host 均零副作用，合法 POST 保留原參數與回應。專用 Headless Chromium 對合成設定頁的五項前端流程檢查通過：token 失敗不送動作、pause 使用帶 token 的 POST、run 等儲存 callback、reset 成功才更新、全部暫停等請求完成才刷新；它不是實際設定服務的完整瀏覽器驗收。`py_compile`、`node --check`、`git diff --check` 退出碼 0；`pyflakes` 可由全域命令執行，新測試無診斷，`settings.py` 僅有原本未使用 import／變數的診斷。未操作真站、日常 profile、產品打包、macOS 或 CI；未 commit／push。
- 此修正只處理六個控制動作的跨來源誘發路徑；`/sendkey` 及其他設定 POST、讀取端點、點擊劫持與有權限的本機程序不因此宣稱已全面防護。當時批次 6 的 PLAN 責任不變。

## 本次施工範圍

- 本次僅交付原始碼使用：正式 source caller 最終須在 Windows x64、macOS arm64 可用；PyInstaller、exe／安裝包、frozen runtime、hidden-import／spec、`build_scripts/build_local.py`、release packaging 與發布不屬本次驗收。既有 build scripts 與 release infrastructure 保留。
- uTiki 最終只維護一份 current 設定格式，正常讀取、儲存並等價保留 `944e934` 已支援的 hot reload 欄位與時點；不為舊 uTiki／CTBC 設定新增 upgrader、runtime fallback、dual read／dual write 或 DB／serialized runtime migration。這是使用者確認的範圍縮減，不移除其他平台仍使用的 migration／normalization／SQLite 機制；uTiki shared refresh／429 SQLite coordinator 保持正式功能。當時完整行為與驗收依原 PLAN。

## 原始基準與 fixture 根因

- 批次 0 在 production、tests 尚未修改時，用 `.\.venv\Scripts\python.exe -m pytest` 收集 356 項，結果 **355 passed、1 deselected**（退出碼 0，14.08 秒）。這是唯一原版實跑基準，不引用 a47 歷史 PASS。
- 原版本機 browser fixture 暫時設定 `TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1`，執行 `.\.venv\Scripts\python.exe -m pytest -m 'browser and not live' tests/test_ctbc_dom_fixtures.py`，結果 **1 failed**（退出碼 1）：seat 的 `commit_seat` 回傳 `REJECTED`，預期 `COMMITTED`。斷言當時中止，quantity、server-assigned quantity、dialog 與 fast-marker 情境**當時未執行**。沒有為此放寬 production 核對或改掉原預期。
- 後續定位為 Windows 測試 adapter 路徑不一致：原 route 使用 `Path.resolve()` 的 `E:\...`，瀏覽器 `file://` 頁面的 `location.pathname` 是 `/E:/...`；改由 fixture URI pathname 指定 route 後，seat 回到原預期的 `COMMITTED` 與兩次 click。再往後的 loopback fixture 使用 POSIX 字面路徑判斷 HTML，遇到 Windows `\UTK0201_001` 時誤回 `application/octet-stream`；改以 `Path(path).name` 判斷後，完整本機 runner 通過。兩項都是測試 adapter 修正，沒有新增重試。

## 各批實際進度

以下是各批**當時**的結果；後來的通過不改寫上列原版失敗。表內預設套件均以 `.\.venv\Scripts\python.exe -m pytest` 執行；browser fixture 均使用合成頁面、本機 loopback、暫存 profile 與暫時旗標，命令見 [測試指南](../testing_execution_guide.md)。

| 批次 | 已完成工作 | 當次驗證結果 |
| --- | --- | --- |
| 0 | 建立工作區、環境及文件入口；記錄原版基準。 | 預設套件 **355 passed、1 deselected**；browser fixture **1 failed**，詳見上節。 |
| 1 | 補足 OCR 關閉時人工四碼、一般票數頁 manual handoff、checkout 聲音與通知整組去重的原行為案例；修正 fixture 的 Windows route／HTML adapter。 | 4 個新增案例 passed；`test_ctbc_dom.py` **107 passed、52 subtests passed**；預設套件 **358 passed、1 deselected**；browser fixture **1 passed in 9.15s**。 |
| 2 | `runtime.py` 承接分頁狀態型別與 constructor，workflow 保有唯一 registry、identity、get-or-create／existing-only 及 exact cleanup。當時保留的空 `action` record 已於批次 6 前清理移除。 | 四份相鄰測試 **285 passed、168 subtests passed**；預設套件 **358 passed、1 deselected**；browser fixture **1 passed in 8.91s**。 |
| 3 | `inputs.py` 承接登入與 CAPTCHA 輸入，`submission.py` 承接提交／恢復／checkout 去重，`purchase.py` 串接原座位與票數頁流程；正式 caller 保留。 | 四份相鄰測試 **285 passed、168 subtests passed**；預設套件 **358 passed、1 deselected**；browser fixture **1 passed in 9.54s**。 |
| 4 | `date_area.py` 承接日期／區域候選及 fast 預取，`navigation.py` 承接 native／fast dispatch、pending、marker、目的頁核對與原電腦配位來源；原 manual handoff、unknown 結果與 await 後核對保留。 | 日期／區域／DOM 聚焦 **210 passed、152 subtests passed**；預設套件 **358 passed、1 deselected**；browser fixture **1 passed in 8.92s**。 |
| 5 | `document_health.py` 承接 document 觀察與分類，`rate_limit.py` 承接 429／probe，`refresh.py` 保留 reload request 生命週期與共用 coordinator 接線。 | refresh 聚焦 **75 passed、16 subtests passed**；預設套件 **358 passed、1 deselected**；browser fixture **1 passed in 9.90s**。 |
| 批次 6 前清理 | 移除 navigation access dict、rate-limit `coordinator_factory`、無 production consumer 的 `runtime["action"]` 及無正式 caller 的 workflow private surface；測試 patch 指向實際 owner，保留原查詢與結果時點。 | 四份相鄰測試 **285 passed、168 subtests passed**；預設套件 **358 passed、1 deselected**；browser fixture **1 passed in 9.41s**；touched Python `py_compile`、production `pyflakes` 與 `git diff --check` 均退出碼 0。 |

批次 5 初次聚焦有 4 個案例因虛擬時鐘仍 patch 舊 owner 而失敗，本機 fixture 初次因舊 patch 路徑在 setup 失敗；改到搬移後的真正 lookup 後，原斷言全數通過。批次 6 前清理又將 refresh／rate-limit coordinator lookup patch 到同一 mock，direct probe 與 emergency marker 的時鐘／app-root patch 改到 rate-limit owner；沒有更改產品預期。這些都是中途定位與修正，不能代稱原版基準結果。

批次 0 的文件分工、upstream 公開 PR 與本機開發適用範圍，以及合成／已脫敏 fixture 規則已收斂到 [AGENTS.md](../../../AGENTS.md)、[開發導航](../README.md) 與[測試指南](../testing_execution_guide.md)。歷史 [舊計畫](../archive/utiki-refactor/utiki-refactor-plan.md) 的 Phase、功能待辦與 gate 不屬現行施工依據；通用安全文字也不自動增加購票拒絕條件。

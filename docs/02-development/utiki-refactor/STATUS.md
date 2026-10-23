# uTiki 重構狀態

- 唯一行為與程式起點：`944e934546f3dd36b1b4891a5a4fa359b8ade17f`。`a47fd07106eae598c8b44972282c6621bf7f493c` 只作文件整理參考，不沿用其功能、完成狀態或測試 PASS。
- 工作目錄／分支：`E:\P3\xx\ticket\tickets_hunter_944`／`codex/utiki-architecture-944e934`。本輪批次 6 前清理開始 HEAD 為 `835433fbc9b8624535a0737d9e2710c03a292376`，工作樹乾淨；施工期間未切換分支或合併上游。
- 階段：批次 0/1/2/3/4/5 已完成；本輪只做批次 6 前的架構清理，尚未進入 current／legacy settings、正式 source caller 或 cleanup 批次。
- 程式範圍：移除 navigation access dict、rate-limit coordinator factory 測試接縫、無 production consumer 的空 `action` runtime record 及無正式 caller 的 workflow private surface；保留 workflow refresh facade、reload request lifecycle、共用 SQLite coordinator 與原購票／導航行為。未修改 `settings.py`、依賴、`pytest.ini`、DB schema／migration、build scripts 或 CI。
- 環境：沿用現有 Windows x64 `.venv`（CPython 3.11.9、pytest 9.0.3），本輪未重建。批次 0 建立環境時 `uv venv --python 3.11.9 .venv` 及 `uv pip install --python .\.venv\Scripts\python.exe -r requirements-dev.txt` 均退出碼 0；需求取自 944 原檔。

## 前輪使用者確認的 scope 校正

- **交付僅限原始碼使用**：正式 source caller 最終須在 Windows x64、macOS arm64 可用；本次不要求 PyInstaller、exe／安裝包、frozen runtime、hidden-import／spec、`build_scripts/build_local.py`、release packaging 或發布。現有 build scripts／release infrastructure 保留，原始碼版本穩定使用後另行處理打包。
- **僅維護 current uTiki 設定格式**：current 設定仍須讀取、儲存並保留 `944e934` 已支援的 hot reload 欄位及時點；完成後使用者可重新填寫 current 設定。正式切換後不維護只為舊 uTiki／CTBC 設定相容的自動升級、runtime fallback、dual read／dual write，也不新增 DB schema／serialized runtime migration、version 欄位或 persistence upgrader。其他平台及共用 migration／normalization／SQLite 機制不在本輪移除範圍；uTiki shared refresh／429 SQLite coordinator 保持正式功能。
- 以上是前輪使用者確認的**交付與 compatibility scope 縮減**，不是新增產品功能。批次 0/1 的實際結果不重做；通用打包指南與歷史計畫保留，僅以正式 PLAN 限定本次施工與驗收。

## 批次 6 前架構清理（非產品功能批次）

- `workflow.py` 移除 `_navigation_access()`；`date_area.py`／`navigation.py` 以顯式參數取得已知 identity、runtime／transition，原 await 後的正式 registry 核對保留 `runtime_for_identity`，建立 pending 時於原驗證後查詢點使用窄 `document_for_identity`。`purchase_key_for_tab`、`behavior_for_key`、`reload_with_interval` 各只傳給實際使用的流程；get-or-create、existing-only、pending／marker／`DISPATCH_UNKNOWN` 與電腦配位來源核對時點保持。
- `rate_limit.py` 在原查詢時點直接取得共享 coordinator，移除 `coordinator_factory` 及 `refresh.py` 的固定轉送。測試同時 patch refresh／rate-limit 的真正 coordinator lookup 到同一 mock，直接 probe 與 emergency marker 案例將時鐘及 app-root patch 指向實際 owner。
- Repository-wide Python caller 搜尋確認 `runtime["action"]` 沒有 production consumer；只由兩個 cleanup 測試當 sentinel 使用，現改以 runtime object identity 與 registry reference 驗證 replacement，並從 `TabRuntime`／constructor 移除空 record。`UTK_SPORTS_PROFILES`、`_ctbc_purchase_runtime`、`_is_ctbc_url` 無 caller；`_utk_refresh_identity` 只有一個私有測試 caller，該測試改由正式 refresh identity owner 建立，再移除 wrapper。公開 `nodriver_ctbc_*` 入口保留。
- 驗證：`.\.venv\Scripts\python.exe -m py_compile` 對所有 touched Python 檔退出碼 0；現有 `pyflakes` 對 touched production 模組退出碼 0。refresh 聚焦 **75 passed、16 subtests passed**；日期／區域／DOM 聚焦 **210 passed、152 subtests passed**；四份相鄰整合測試 **285 passed、168 subtests passed**；預設完整 `pytest` **358 passed、1 deselected**；本機合成 browser fixture **1 passed in 9.41s**。`git diff --check` 退出碼 0。
- 未驗證：macOS arm64 source 使用、GitHub CI、真站手動流程；本輪不做產品打包。`944e934` 仍是唯一產品行為基準，本輪沒有修改 current／legacy settings 行為或進入批次 6。

## 批次 5：刷新觀察與 429 管理

- `document_health.py` 承接原 document inspect、快取、canonical token 與 normal／blocked／unknown／unverified 分類；沿原 DOM 證據要求判斷 429，不發起 reload。`rate_limit.py` 承接原設定讀取、跨程序 emergency marker、429 report／retry 與 probe 建立／續期／完成／放棄／lease 清理；只使用同一份 runtime、cancel-task 集合及原 coordinator，不建立新 registry 或直接呼叫 browser reload。
- `refresh.py` 保留同一 reload request 的 token／reason／eligible_at／deadline、queued／reserved／awaiting_document／completed、reservation／preflight／commit、browser reload dispatch、pause／resume／cancel、reload event／wait 與完成消費。`_utk_document_guard` 仍在原時點組合 document 觀察、probe／429 保護與 request 對帳；workflow 的正式 refresh facade 不變。
- 原 canonical document token 仍要求有效非零 performance time origin；history URL drift 或 browser reload 指令返回不等於新文件。blocked 仍要求 code＋message 且沒有 normal root；persistent unknown 不回報 shared cooldown。probe success 仍等待新文件及分類，舊 request／probe 與 replacement 的身分核對、lease task exact cleanup、三階段 pause／resume、不同 reason 的 completion owner 與 coordinator enabled／disabled hot switch 均沿原分支。未發現需改變產品語意的 `944e934` 歧義。
- `test_ctbc_refresh.py` 將搬移函式 import、設定／marker patch 與模組虛擬時鐘對準真正 lookup；`test_ctbc_dom_fixtures.py` 的 document 頁型與設定 patch 亦改到新 owner。初次聚焦有 4 個測試因虛擬時鐘仍只 patch 舊模組而失敗，改到新 owner 後原斷言全過；初次 browser fixture 因舊 patch 路徑無該屬性而在 setup 失敗，修正 patch 後完整 runner 通過。
- 語法：`.\.venv\Scripts\python.exe -m py_compile src/platforms/utiki/document_health.py src/platforms/utiki/rate_limit.py src/platforms/utiki/refresh.py tests/test_ctbc_refresh.py`，退出碼 0；現有全域工具 `pyflakes` 對三個 touched production 模組退出碼 0。
- 聚焦：`.\.venv\Scripts\python.exe -m pytest -q tests/test_ctbc_refresh.py`，退出碼 0，**75 passed、16 subtests passed**。鄰近回歸：`.\.venv\Scripts\python.exe -m pytest -q tests/test_ctbc_dom.py tests/test_utiki_dom_contract.py tests/test_ctbc_refresh.py`，退出碼 0，**220 passed、81 subtests passed**。
- 整合：`.\.venv\Scripts\python.exe -m pytest`，退出碼 0，**358 passed、1 deselected**。
- 本機 browser fixture：暫時設定 `TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1`，執行 `.\.venv\Scripts\python.exe -m pytest -q --tb=short -m 'browser and not live' tests/test_ctbc_dom_fixtures.py`，修正測試 lookup 後退出碼 0，**1 passed in 9.90s**；執行後還原原環境旗標。使用合成頁面、本機 loopback 與暫存 profile。
- 未驗證：macOS arm64 source 使用、GitHub CI、真站手動流程；本批不執行產品打包。批次 0 的原始 browser fixture 失敗仍保留下方原始基準。

## 批次 4：日期／區域選擇與導航

- `date_area.py` 承接日期／區域 snapshot、原選擇函式、fast 預取與候選選擇；預取不適用、失敗、逾期或無合格候選時，依原條件返回 native。`navigation.py` 承接已選目標的 native／fast dispatch、pending、marker、目的頁核對、return-to-native 與原電腦配位來源紀錄；它使用 workflow 傳入的同一 registry／runtime callback，不建立第二套狀態或擴張設定 fingerprint 的用途。
- `workflow.py` 保留唯一 `_state`、tab identity／lifecycle、refresh、頂層頁型路由、座位／一般票數頁的既有核對入口、購票與提交協調。正式日期／區域入口直接呼叫 `date_area.py`，選定後由 `navigation.py` 送出；server-assigned 入口只接回原 provenance 判定與既有票數準備。原完整 date／area／navigation 實作已移出，保留有正式 caller 的薄入口。
- Fast 尚未送出時的原 native fallback、確定 `REJECTED` 的 cleanup、可能已送出的 `DISPATCH_UNKNOWN` 保留 pending 而不重點、native date unknown 的重入阻止、marker 與 document／identity 核對、storage 不可用時原 native／manual 路徑、一般票數與座位頁 manual handoff 均沿原分支。server-assigned provenance 只服務原 computer mode；沒有發現需變更產品行為的 `944e934` 歧義。
- 測試沿用原有行為斷言，將搬移函式的私有 import／patch 指到真正 lookup；測試 helper 對直接呼叫 navigation 的原案例傳入 workflow callback。未為測試保留完整舊實作或增加新購票 gate。
- 語法：`.\.venv\Scripts\python.exe -m py_compile src/platforms/utiki/date_area.py src/platforms/utiki/navigation.py src/platforms/utiki/workflow.py`，退出碼 0；以既有全域工具對三個 touched production 模組執行 `pyflakes`，退出碼 0。
- 聚焦：`.\.venv\Scripts\python.exe -m pytest -q tests/test_ctbc_pure.py tests/test_ctbc_dom.py tests/test_utiki_dom_contract.py`，退出碼 0，**210 passed、152 subtests passed**。
- 整合：`.\.venv\Scripts\python.exe -m pytest -q`，退出碼 0，**358 passed、1 deselected、180 subtests passed**。
- 本機 browser fixture：暫時設定 `TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1`，執行 `.\.venv\Scripts\python.exe -m pytest -q --tb=short -m 'browser and not live' tests/test_ctbc_dom_fixtures.py`，退出碼 0，**1 passed in 8.92s**；結束後清除環境旗標。使用合成頁面、本機 loopback 與暫存 profile。
- 未驗證：macOS arm64 source 使用、GitHub CI、真站手動流程；本批不執行產品打包。批次 0 原始 browser fixture 失敗仍保留下方原始基準。

## 批次 3：輸入、提交與購票準備

- `workflow.py` 繼續擁有唯一 registry／tab lifecycle、頁面路由與原有 fast destination、manual mode、server-assigned pending navigation／authorization gate；座位與票數頁通過原 gate 後直接呼叫 `purchase.py`。外層結果協調與 checkout 路由直接呼叫 `submission.py`，沒有保留另一套購票／提交實作。
- `inputs.py` 承接 Cookie 注入、inline login、fingerprint、CAPTCHA 圖片／OCR／欄位與換圖 browser 操作；`submission.py` 承接 purchase key、active submit／outcome／last submit、dialog、CAPTCHA／衝突／auth 恢復、未知結果與 checkout 去重；`purchase.py` 組合原 selection／DOM commit／submission，不建立 registry 或鏡像 purchase state。submission／purchase 透過 workflow 傳入的窄 callback 使用同一 runtime 與受控 reload。
- 測試沿用既有行為斷言；只把被搬移私有函式的 import／patch 改到實際模組，並在測試 helper 為直接呼叫 submission 的案例注入現有 workflow callback。公開 `platforms.utiki`、`platforms.ctbc` 與主入口 API 未變。未發現必須重新選擇產品行為的 944 歧義。
- 語法：`.\.venv\Scripts\python.exe -m py_compile src/platforms/utiki/inputs.py src/platforms/utiki/submission.py src/platforms/utiki/purchase.py src/platforms/utiki/workflow.py tests/ctbc_support.py tests/test_ctbc_dom.py tests/test_utiki_dom_contract.py tests/test_ctbc_pure.py`，退出碼 0。
- 聚焦：`.\.venv\Scripts\python.exe -m pytest -q tests/test_ctbc_dom.py tests/test_utiki_dom_contract.py tests/test_ctbc_pure.py tests/test_ctbc_refresh.py --tb=short -x`，退出碼 0，**285 passed、168 subtests passed**。
- 整合：`.\.venv\Scripts\python.exe -m pytest`，退出碼 0，**358 passed、1 deselected**（收集 359 項，8.80 秒）。
- 本機 browser fixture：暫時設定 `TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1`，執行 `.\.venv\Scripts\python.exe -m pytest -q --tb=short -m 'browser and not live' tests/test_ctbc_dom_fixtures.py`，退出碼 0，**1 passed in 9.54s**；同一命令結束後清除環境旗標。fixture 使用暫存 profile、合成頁面與本機 loopback。
- 未驗證：macOS arm64 source 使用、GitHub CI、真站手動流程；本批不執行產品打包。批次 0 的原始 browser fixture 失敗紀錄仍以原始基準節為準，不由本批成功結果改寫。

## 批次 2：runtime 資料與依賴

- 批次 2 當時，`workflow.py` 仍持有唯一 `_state["tabs"]` 與 `_state["cancel_tasks"]`；tab identity、get-or-create、existing-only 與 cleanup 路徑保留原碼。`runtime.py` 當時有五份 mutable child record 的 TypedDict／constructor，沒有 registry 或 manager；無 consumer 的 `action` 已在上列批次 6 前清理移除。`refresh.py` 仍由 workflow 傳入同一 runtime 與 cancel-task set。原 await 後對 registry／record 的重查、purchase／navigation／refresh 狀態建立與清理時點未改。
- 批次 2 當時 `action` 在 production 無欄位讀寫，因而保留原空 dict；其餘 document、transition、purchase、refresh 的預設值與 `944e934` 相同。既有測試已涵蓋雙 tab 隔離、同 target wrapper、晚出現 target id、未知 tab cleanup 不配置、exact cleanup、await 期間 replacement、submit／outcome／pending navigation 身分及 checkout 去重；本批未增加重複案例。
- 靜態：`.\.venv\Scripts\python.exe -m py_compile src/platforms/utiki/runtime.py src/platforms/utiki/workflow.py src/platforms/utiki/refresh.py` 退出碼 0；`git diff --check` 退出碼 0。
- 聚焦：`.\.venv\Scripts\python.exe -m pytest -q tests/test_ctbc_dom.py tests/test_utiki_dom_contract.py tests/test_ctbc_refresh.py tests/test_ctbc_pure.py` 退出碼 0，**285 passed、168 subtests passed**。
- 整合：`.\.venv\Scripts\python.exe -m pytest` 退出碼 0，**358 passed、1 deselected**（收集 359 項，8.84 秒）。
- 本機 browser fixture：因本批更換 runtime constructor，驗證 production DOM caller 的建立路徑；暫時設定 `TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1` 執行 `.\.venv\Scripts\python.exe -m pytest -q --tb=short -m 'browser and not live' tests/test_ctbc_dom_fixtures.py`，退出碼 0，**1 passed in 8.91s**，執行後清除環境旗標。
- 未驗證：macOS arm64 source 使用、GitHub CI、真站手動流程；本批不執行產品打包。沒有發現需選擇新產品語意的 944 runtime 欄位歧義。

## 原始基準（production 與 tests 尚未修改時）

- 預設套件：`.\.venv\Scripts\python.exe -m pytest`，退出碼 0；收集 356 項，**355 passed、1 deselected**，耗時 14.08 秒。這是批次 0 實跑結果，不引用 a47 歷史 PASS。
- 本機 browser fixture：暫時設定 `TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1` 後執行 `.\.venv\Scripts\python.exe -m pytest -m 'browser and not live' tests/test_ctbc_dom_fixtures.py`，退出碼 1；收集 1 項、1 failed。`test_production_dom_contracts_in_local_browser` 的 seat 情境中，`commit_seat` 回傳 `CommitResult.REJECTED`，原預期 `COMMITTED`。斷言在 `_assert_seat_fixture` 中止，後續 quantity、server-assigned quantity、dialog、fast-marker 情境**當時未執行**。原版 production 與測試預期未為此更改。
- GitHub CI／打包／真站手動驗收未執行；不以本機 pytest 代稱其他驗證層級。

## 已完成的批次 0 文件與批次 1

- 選擇性比較 a47 與當時文件後，收斂 [CONTRIBUTING](../../../CONTRIBUTING.md) 的 upstream 公開 PR 適用範圍；本機／fork 工作不以 Issue 或維護者確認為前置條件，commit／push 仍須使用者明確下令。開發導航、通用十二階段、範本、工程審查與測試指南只標示各自職責與適用範圍，不帶回 a47 的單 Run、typed policy、authority／claim、舊 gate 或歷史 PASS；本輪 source-only 決策另由使用者明確確認。
- [AGENTS.md](../../../AGENTS.md) 與測試指南允許合成或已脫敏 fixture，保留一致的合成席位／商品／payload 關聯；需要瀏覽器時使用專用／暫存 profile。實際擷取才記錄擷取日期。真站與日常 profile 仍須授權，秘密不得輸出。測試指南集中開發聚焦驗證、整合收集失敗、按共同根因處理、聚焦重驗及必要完整驗證的節奏。
- 在原有 production 尚未重構時，`tests/test_ctbc_dom.py` 補足：OCR 關閉且原讀取時點已有人工四碼，即使 `force_submit=True` 仍 ready、不呼叫 OCR、不自動送出；一般票數頁 manual 模式、無 pending／marker 時沿正式 caller 與真實 `_ctbc_attest_fast_destination` 分支接手；checkout 聲音開／關採獨立 runtime/key，Discord／Telegram 與聲音呼叫均 mock，重複 tick 整組去重，處理後才開聲音不補播或重發。
- 聚焦案例：`.\.venv\Scripts\python.exe -m pytest -q tests/test_ctbc_dom.py::CtbcDomFlowTest::test_manual_four_character_captcha_with_ocr_off_stays_ready tests/test_ctbc_dom.py::CtbcDomFlowTest::test_manual_general_admission_without_navigation_records_reaches_quantity tests/test_ctbc_dom.py::CtbcDomFlowTest::test_checkout_two_ticks_notifies_once_and_retains_runtime tests/test_ctbc_dom.py::CtbcDomFlowTest::test_checkout_sound_on_notifies_once_across_two_ticks`，**4 passed**。受影響檔：`.\.venv\Scripts\python.exe -m pytest -q tests/test_ctbc_dom.py`，**107 passed、52 subtests passed**。預設整合套件：`.\.venv\Scripts\python.exe -m pytest`，退出碼 0；收集 359 項，**358 passed、1 deselected**，耗時 9.34 秒。

## 本機 browser fixture 定位與重驗

- 原 seat 失敗為 Windows 測試 adapter 的路徑表示不一致：原 route 使用 `Path.resolve()` 的 `E:\...`，本機 `file://` fixture 在瀏覽器的 `location.pathname` 為 `/E:/...`。診斷時原 route 不匹配且回傳 `REJECTED`；改以 fixture 檔案 URI 的 pathname 指定同一路由後，同一 seat commit 回傳 `COMMITTED` 且觸發原預期的兩次座位 click。`tests/test_ctbc_dom_fixtures.py` 只新增 `_fixture_pathname()` 並用於 seat 與一般票數 route，未放寬 production 核對或原斷言。
- 路由修正後 runner 進入後續情境，server-assigned fixture 在本機 loopback `browser.get` 逾時。其 handler 原用 POSIX 字面 `path.endswith("/UTK0201_001")` 判斷 HTML；Windows 原生路徑尾段為 `\UTK0201_001`，導致 fallback `application/octet-stream`。測試 adapter 改用 `Path(path).name == "UTK0201_001"`，回復 `text/html`；未新增重試。
- 修正後以暫時環境旗標執行 `.\.venv\Scripts\python.exe -m pytest -q --tb=short -m 'browser and not live' tests/test_ctbc_dom_fixtures.py`，退出碼 0，**1 passed in 9.15s**；原 runner 的 seat、一般票數、server-assigned、dialog、fast-marker 情境依序完成。此結果是本輪測試 adapter 修正後的本機 fixture 結果，不改寫上列原始基準。
- 文件連結、現況一致性及 `git diff --check` 已檢查；GitHub CI、產品打包、真站與日常 browser profile 均未執行。後續進入批次 2 時仍須依 PLAN 比對舊新行為，不能把本輪新增案例代稱整體重構完成。

## 下一步

本輪停在批次 6 前架構清理，等待使用者審查。獲另行下令後，批次 6 可從 current 設定、正式 source caller 與只為舊設定相容保留且已無正式 caller 的轉接邊界開始；本輪未預先修改設定或入口。

| 文件或 PLAN 情境 | 原衝突／缺口 | 目前狀態 |
| --- | --- | --- |
| 舊計畫與文件入口 | 舊 `utiki-refactor-plan.md` 的 Phase 與 gate 容易冒充現行計畫。 | 已標示為歷史；入口指向 PLAN／STATUS，批次 2–4 已接線模組按實況更新索引。 |
| 一般安全文字與 uTiki 庫存判斷 | 通用 `fail closed` 若擴張到 944 非空庫存文字會新增拒絕條件。 | AGENTS／通用文件限定適用範圍；production 未變。 |
| 人工四碼、OCR 關閉 | 既有案例未覆蓋 OCR 關閉＋人工四碼＋`force_submit=True`。 | 批次 1 案例已在舊 production 上通過。 |
| 一般票數頁人工接手 | 既有案例只直接驗證劃位頁人工進入。 | 批次 1 新案例經正式 caller 及既有 attest 分支通過。 |
| Checkout 聲音與通知去重 | 原測試未斷言聲音開關與同一 key 重複 tick。 | 批次 1 聲音開／關、通知 mock、重複 tick 及處理後開聲音案例通過。 |
| 本機 browser fixture | 原 seat `REJECTED`，後續情境中止。 | Windows 測試 route 與 loopback HTML 類型問題均定位並小範圍修正；完整本機 runner 1 passed。 |

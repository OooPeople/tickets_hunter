# uTiki 重構狀態

- 唯一行為與程式起點：`944e934546f3dd36b1b4891a5a4fa359b8ade17f`。`a47fd07106eae598c8b44972282c6621bf7f493c` 只作文件整理參考，不沿用其功能、完成狀態或測試 PASS。
- 工作目錄／分支：`E:\P3\xx\ticket\tickets_hunter_944`／`codex/utiki-architecture-944e934`。批次 3 開始 HEAD 為 `33c168a42237dcdfcb57d95db69775ea32bcfb82`，工作樹乾淨；目前 HEAD 相同，未切換分支或合併上游。本輪修改尚未提交或推送。
- 階段：批次 0/1/2 已完成；本輪依 [PLAN.md](PLAN.md) 完成批次 3 購票與提交責任拆分，停在批次 3，不進入 date／area／navigation 或 document-health 拆分。
- 程式範圍：新增 `inputs.py`、`submission.py`、`purchase.py` 並接正式 workflow；既有 facade 與設定流程保持。測試僅更新內部 import／patch 的實際 lookup，未改產品預期；未修改 `settings.py`、依賴、`pytest.ini`、DB schema／migration、build scripts 或 CI，未新增購票資格、拒絕條件或人工出口。
- 環境：沿用現有 Windows x64 `.venv`（CPython 3.11.9、pytest 9.0.3），本輪未重建。批次 0 建立環境時 `uv venv --python 3.11.9 .venv` 及 `uv pip install --python .\.venv\Scripts\python.exe -r requirements-dev.txt` 均退出碼 0；需求取自 944 原檔。

## 前輪使用者確認的 scope 校正

- **交付僅限原始碼使用**：正式 source caller 最終須在 Windows x64、macOS arm64 可用；本次不要求 PyInstaller、exe／安裝包、frozen runtime、hidden-import／spec、`build_scripts/build_local.py`、release packaging 或發布。現有 build scripts／release infrastructure 保留，原始碼版本穩定使用後另行處理打包。
- **僅維護 current uTiki 設定格式**：current 設定仍須讀取、儲存並保留 `944e934` 已支援的 hot reload 欄位及時點；完成後使用者可重新填寫 current 設定。正式切換後不維護只為舊 uTiki／CTBC 設定相容的自動升級、runtime fallback、dual read／dual write，也不新增 DB schema／serialized runtime migration、version 欄位或 persistence upgrader。其他平台及共用 migration／normalization／SQLite 機制不在本輪移除範圍；uTiki shared refresh／429 SQLite coordinator 保持正式功能。
- 以上是前輪使用者確認的**交付與 compatibility scope 縮減**，不是新增產品功能。批次 0/1 的實際結果不重做；通用打包指南與歷史計畫保留，僅以正式 PLAN 限定本次施工與驗收。

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

- `workflow.py` 仍持有唯一 `_state["tabs"]` 與 `_state["cancel_tasks"]`；tab identity、get-or-create、existing-only 與 cleanup 路徑保留原碼。`runtime.py` 僅有目前五份 mutable child record 的 TypedDict／constructor，沒有 registry 或 manager。`refresh.py` 仍由 workflow 傳入同一 runtime 與 cancel-task set。原 await 後對 registry／record 的重查、purchase／navigation／refresh 狀態建立與清理時點未改。
- `action` 在現有 production 尚無欄位讀寫，保留原本空 dict；document、transition、purchase、refresh 的預設值與 `944e934` 相同。既有測試已涵蓋雙 tab 隔離、同 target wrapper、晚出現 target id、未知 tab cleanup 不配置、exact cleanup、await 期間 replacement、submit／outcome／pending navigation 身分及 checkout 去重；本批未增加重複案例。
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

本輪停在批次 3，等待使用者審查。獲另行下令後，批次 4 可從 `workflow.py` 現存的 date／area action 與 fast destination／server-assigned navigation gate 邊界開始；本批沒有預先建立 navigation framework。

| 文件或 PLAN 情境 | 原衝突／缺口 | 目前狀態 |
| --- | --- | --- |
| 舊計畫與文件入口 | 舊 `utiki-refactor-plan.md` 的 Phase 與 gate 容易冒充現行計畫。 | 已標示為歷史；入口指向 PLAN／STATUS，目標模組尚未宣稱實作。 |
| 一般安全文字與 uTiki 庫存判斷 | 通用 `fail closed` 若擴張到 944 非空庫存文字會新增拒絕條件。 | AGENTS／通用文件限定適用範圍；production 未變。 |
| 人工四碼、OCR 關閉 | 既有案例未覆蓋 OCR 關閉＋人工四碼＋`force_submit=True`。 | 批次 1 案例已在舊 production 上通過。 |
| 一般票數頁人工接手 | 既有案例只直接驗證劃位頁人工進入。 | 批次 1 新案例經正式 caller 及既有 attest 分支通過。 |
| Checkout 聲音與通知去重 | 原測試未斷言聲音開關與同一 key 重複 tick。 | 批次 1 聲音開／關、通知 mock、重複 tick 及處理後開聲音案例通過。 |
| 本機 browser fixture | 原 seat `REJECTED`，後續情境中止。 | Windows 測試 route 與 loopback HTML 類型問題均定位並小範圍修正；完整本機 runner 1 passed。 |

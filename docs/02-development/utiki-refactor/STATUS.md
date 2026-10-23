# uTiki 重構狀態

- 唯一行為與程式起點：`944e934546f3dd36b1b4891a5a4fa359b8ade17f`。`a47fd07106eae598c8b44972282c6621bf7f493c` 只作文件整理參考，不沿用其功能、完成狀態或測試 PASS。
- 工作目錄／分支：`E:\P3\xx\ticket\tickets_hunter_944`／`codex/utiki-architecture-944e934`。本輪開始時 HEAD 為已知準備提交 `09d1fcff6498eae2ff22ee9e15d9d722defde981`；本輪只有未提交的文件與測試修改，未切換分支或合併上游。
- 階段：批次 0 的直接相關文件收尾及批次 1 的指定行為案例已完成本機修改與驗證；**停在批次 1**。批次 2 production 架構拆分尚未開始。目標設計與功能範圍仍由 [PLAN.md](PLAN.md) 定義。
- 程式範圍：production、`requirement.txt`、`requirements-dev.txt`、`pytest.ini` 均未修改；未接入目標新模組，未新增購票資格、拒絕條件或人工出口。未 commit、push、建立 Issue／PR。
- 環境：沿用現有 Windows x64 `.venv`（CPython 3.11.9、pytest 9.0.3），本輪未重建。批次 0 建立環境時 `uv venv --python 3.11.9 .venv` 及 `uv pip install --python .\.venv\Scripts\python.exe -r requirements-dev.txt` 均退出碼 0；需求取自 944 原檔。

## 原始基準（production 與 tests 尚未修改時）

- 預設套件：`.\.venv\Scripts\python.exe -m pytest`，退出碼 0；收集 356 項，**355 passed、1 deselected**，耗時 14.08 秒。這是批次 0 實跑結果，不引用 a47 歷史 PASS。
- 本機 browser fixture：暫時設定 `TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1` 後執行 `.\.venv\Scripts\python.exe -m pytest -m 'browser and not live' tests/test_ctbc_dom_fixtures.py`，退出碼 1；收集 1 項、1 failed。`test_production_dom_contracts_in_local_browser` 的 seat 情境中，`commit_seat` 回傳 `CommitResult.REJECTED`，原預期 `COMMITTED`。斷言在 `_assert_seat_fixture` 中止，後續 quantity、server-assigned quantity、dialog、fast-marker 情境**當時未執行**。原版 production 與測試預期未為此更改。
- GitHub CI／打包／真站手動驗收未執行；不以本機 pytest 代稱其他驗證層級。

## 本輪文件與批次 1

- 選擇性比較 a47 與目前文件後，收斂 [CONTRIBUTING](../../../CONTRIBUTING.md) 的 upstream 公開 PR 適用範圍；本機／fork 工作不以 Issue 或維護者確認為前置條件，commit／push 仍須使用者明確下令。開發導航、通用十二階段、範本、工程審查與測試指南只標示各自職責與適用範圍，不把 a47 的單 Run、typed policy、authority／claim、舊 gate、歷史 PASS 或 source-only 決策帶回來。
- [AGENTS.md](../../../AGENTS.md) 與測試指南允許合成或已脫敏 fixture，保留一致的合成席位／商品／payload 關聯；需要瀏覽器時使用專用／暫存 profile。實際擷取才記錄擷取日期。真站與日常 profile 仍須授權，秘密不得輸出。測試指南集中開發聚焦驗證、整合收集失敗、按共同根因處理、聚焦重驗及必要完整驗證的節奏。
- 在原有 production 尚未重構時，`tests/test_ctbc_dom.py` 補足：OCR 關閉且原讀取時點已有人工四碼，即使 `force_submit=True` 仍 ready、不呼叫 OCR、不自動送出；一般票數頁 manual 模式、無 pending／marker 時沿正式 caller 與真實 `_ctbc_attest_fast_destination` 分支接手；checkout 聲音開／關採獨立 runtime/key，Discord／Telegram 與聲音呼叫均 mock，重複 tick 整組去重，處理後才開聲音不補播或重發。
- 聚焦案例：`.\.venv\Scripts\python.exe -m pytest -q tests/test_ctbc_dom.py::CtbcDomFlowTest::test_manual_four_character_captcha_with_ocr_off_stays_ready tests/test_ctbc_dom.py::CtbcDomFlowTest::test_manual_general_admission_without_navigation_records_reaches_quantity tests/test_ctbc_dom.py::CtbcDomFlowTest::test_checkout_two_ticks_notifies_once_and_retains_runtime tests/test_ctbc_dom.py::CtbcDomFlowTest::test_checkout_sound_on_notifies_once_across_two_ticks`，**4 passed**。受影響檔：`.\.venv\Scripts\python.exe -m pytest -q tests/test_ctbc_dom.py`，**107 passed、52 subtests passed**。預設整合套件：`.\.venv\Scripts\python.exe -m pytest`，退出碼 0；收集 359 項，**358 passed、1 deselected**，耗時 9.34 秒。

## 本機 browser fixture 定位與重驗

- 原 seat 失敗為 Windows 測試 adapter 的路徑表示不一致：原 route 使用 `Path.resolve()` 的 `E:\...`，本機 `file://` fixture 在瀏覽器的 `location.pathname` 為 `/E:/...`。診斷時原 route 不匹配且回傳 `REJECTED`；改以 fixture 檔案 URI 的 pathname 指定同一路由後，同一 seat commit 回傳 `COMMITTED` 且觸發原預期的兩次座位 click。`tests/test_ctbc_dom_fixtures.py` 只新增 `_fixture_pathname()` 並用於 seat 與一般票數 route，未放寬 production 核對或原斷言。
- 路由修正後 runner 進入後續情境，server-assigned fixture 在本機 loopback `browser.get` 逾時。其 handler 原用 POSIX 字面 `path.endswith("/UTK0201_001")` 判斷 HTML；Windows 原生路徑尾段為 `\UTK0201_001`，導致 fallback `application/octet-stream`。測試 adapter 改用 `Path(path).name == "UTK0201_001"`，回復 `text/html`；未新增重試。
- 修正後以暫時環境旗標執行 `.\.venv\Scripts\python.exe -m pytest -q --tb=short -m 'browser and not live' tests/test_ctbc_dom_fixtures.py`，退出碼 0，**1 passed in 9.15s**；原 runner 的 seat、一般票數、server-assigned、dialog、fast-marker 情境依序完成。此結果是本輪測試 adapter 修正後的本機 fixture 結果，不改寫上列原始基準。
- 文件連結、現況一致性及 `git diff --check` 已檢查；GitHub CI、產品打包、真站與日常 browser profile 均未執行。後續進入批次 2 時仍須依 PLAN 比對舊新行為，不能把本輪新增案例代稱整體重構完成。

## 下一步

依使用者本輪指令停在批次 1，保留未提交變更。待另行下令才進入批次 2；屆時依 PLAN 將目標模組逐步接入正式 caller，並隨實際接線更新 code-boundaries、structure 與驗證紀錄。

| 文件或 PLAN 情境 | 原衝突／缺口 | 目前狀態 |
| --- | --- | --- |
| 舊計畫與文件入口 | 舊 `utiki-refactor-plan.md` 的 Phase 與 gate 容易冒充現行計畫。 | 已標示為歷史；入口指向 PLAN／STATUS，目標模組尚未宣稱實作。 |
| 一般安全文字與 uTiki 庫存判斷 | 通用 `fail closed` 若擴張到 944 非空庫存文字會新增拒絕條件。 | AGENTS／通用文件限定適用範圍；production 未變。 |
| 人工四碼、OCR 關閉 | 既有案例未覆蓋 OCR 關閉＋人工四碼＋`force_submit=True`。 | 批次 1 案例已在舊 production 上通過。 |
| 一般票數頁人工接手 | 既有案例只直接驗證劃位頁人工進入。 | 批次 1 新案例經正式 caller 及既有 attest 分支通過。 |
| Checkout 聲音與通知去重 | 原測試未斷言聲音開關與同一 key 重複 tick。 | 批次 1 聲音開／關、通知 mock、重複 tick 及處理後開聲音案例通過。 |
| 本機 browser fixture | 原 seat `REJECTED`，後續情境中止。 | Windows 測試 route 與 loopback HTML 類型問題均定位並小範圍修正；完整本機 runner 1 passed。 |

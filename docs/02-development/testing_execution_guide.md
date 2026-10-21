# 測試與驗證執行指南

**文件說明**：定義目前 repository 可執行的驗證層級、命令與結果陳述方式。

## 1. 原則

- 驗證原則、commit 邊界與完成判斷以根目錄 [`AGENTS.md`](../../AGENTS.md) 為準；
  本文件保存各層級的執行時機、命令、失敗處理及結果說法。
- 任何新測試必須實際執行；不能只建立測試檔就宣告完成。
- 平台流程可以拆成純邏輯、DOM 契約與瀏覽器流程測試，不應一概視為「無法測試」。
- 單元測試通過不代表真實網站流程通過；實機測試也不能取代可重複的自動測試。
- 測試不得提交或輸出 Cookie、帳號、驗證碼、付款資料、webhook URL 或完整頁面個資。
- 不得用廣泛的程序終止指令關閉所有 Python 或 Chrome；只停止本次測試啟動的程序。
- 實機測試的刷新頻率必須使用使用者設定，不得為加速測試而私自縮短。沒有明確設定時，人工調查預設至少間隔 5 秒。

### 1.1 uTiki 重構的驗證來源

uTiki 的目標與完成條件由 [重構計畫](utiki-refactor/PLAN.md) 維護，實際接線狀態由
[進度摘要](utiki-refactor/STATUS.md) 維護。本指南保存驗證分級、執行節奏與命令；舊 gate
的退役條件由計畫維護。

## 2. 測試入口與環境

本專案以 pytest 作為主要測試入口，也可收集既有 `unittest.TestCase`。
實際測試檔以 `tests/` 為準；`pytest.ini` 定義預設收集與 marker，
`requirements-dev.txt` 固定開發工具版本。舊快照的測試數不作為完成門檻。

安裝開發相依套件：

```bash
python -m pip install -r requirements-dev.txt
```

若既有專案 `.venv` 沒有內建 pip，但本機已安裝 uv，可使用：

```bash
uv pip install --python .venv/bin/python -r requirements-dev.txt
```

Windows 的既有 `.venv` 對應命令為：

```powershell
uv pip install --python .venv\Scripts\python.exe -r requirements-dev.txt
```

## 3. 驗證層級

先啟用已安裝 `requirement.txt` 的專案虛擬環境，並確認 active interpreter 符合專案 Python 版本。
下列命令的 `python` 一律指該 interpreter，不是未安裝相依套件的系統 Python。

| 層級 | 何時使用 | 要做的事 |
|------|----------|----------|
| 聚焦檢查 | 一個有意義的修改單位完成後，以及批次修復中的重驗 | `git diff --check`、修改過的 Python 語法、受影響 pytest；適用時加 mypy／JS syntax |
| 本機完整檢查 | 完整流程切片接通、跨模組接線、切換正式路徑或準備交付候選 | 完整 pytest、型別與語法範圍、設定／資源測試，以及環境允許的隔離瀏覽器 fixture；先收集全部失敗再批次修復 |
| GitHub CI | 使用者下令推送候選後，由 push／PR 事件觸發 | 以 `.github/workflows/tests.yml` 和過渡中的 uTiki 隔離 job 結果為準；紅燈列原因，不把本機結果寫成 CI 通過 |
| 真站手動驗收 | 使用者之後有可用活動時 | 由使用者以專用設定核對網站 DOM、導頁、提交邊界；單獨記錄，不由離線測試代替 |

產品打包另依本文件 4.5 節與當次交付範圍驗證。

目前重構分支每次推送都會自動觸發 `Tests` 與過渡中的 `uTiki P1 Gate`；PR 事件也會觸發檢查。
開發中先完成本機批次驗證並回報結果；階段完成與測試通過不構成 commit 或推送授權，
也不為了查找下一個失敗而反覆推送。

## 4. 常用命令

所有命令從 repository 根目錄執行。

### 4.1 文件或一般變更

```bash
git diff --check
```

文件變更另需確認：

- 相對連結指向存在的檔案。
- 文件描述的命令、檔名、設定鍵與目錄確實存在。
- 沒有把未執行的驗證寫成已通過。

### 4.2 Python 語法

只檢查本次修改過的 Python 檔案，例如：

```bash
python -m py_compile src/platforms/ctbc.py tests/ctbc_support.py tests/test_ctbc_pure.py tests/test_ctbc_dom.py tests/test_ctbc_refresh.py
```

`py_compile` 只證明語法與匯入前編譯階段可通過，不證明行為正確。

### 4.3 聚焦測試

```bash
python -m pytest tests/test_ctbc_pure.py tests/test_ctbc_dom.py tests/test_ctbc_refresh.py
python -m pytest tests/test_refresh_coordinator.py
```

也可以執行單一 test class 或 method：

```bash
python -m pytest tests/test_ctbc_pure.py::CtbcPureSelectionTest
python -m pytest tests/test_ctbc_pure.py::CtbcPureSelectionTest::test_name
```

### 4.4 完整測試

本節只列完整套件命令；何時到期由 `AGENTS.md`、已採用計劃及本次變更影響決定。完整檢查應讓可執行的測試跑到結束，不加 `-x` 或 `--maxfail=1` 只看第一個失敗；若收集或環境錯誤擋住後續測試，先排除阻礙再重新收集。

```bash
python -m pytest --strict-config --strict-markers
```

需要核對收集範圍或遷移相容性時：

```bash
python -m pytest --collect-only -q
python -m unittest discover -s tests -p 'test_*.py'
```

pytest 可以執行現有 `unittest.TestCase`。日後若新增 pytest 原生函式或 fixture 測試，unittest 不會收集它們；屆時兩個 runner 的測試數不同是預期結果，不得把 unittest 當成完整 suite。

CTBC 的脫敏瀏覽器 fixture 位於 `tests/fixtures/ctbc/`，runner 是
`tests/test_ctbc_dom_fixtures.py`。它只開啟本機 `file://` HTML，不連線真實售票網站，
並同時受 `browser` marker 與 `TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1` 環境變數保護；
因此預設 pytest 會 deselect；unittest discover 不會收集 pytest-only runner，也不會
啟動瀏覽器。執行前需在本機安裝
Chrome、Chromium、Edge 或 Brave 等 zendriver 支援的瀏覽器。

macOS／Linux 明確執行命令：

```bash
TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1 python -m pytest -m "browser and not live" tests/test_ctbc_dom_fixtures.py tests/test_utiki_engine_browser_flow.py
```

Windows PowerShell 明確執行命令：

```powershell
$env:TICKETS_HUNTER_RUN_BROWSER_FIXTURES = "1"
python -m pytest -m "browser and not live" tests/test_ctbc_dom_fixtures.py tests/test_utiki_engine_browser_flow.py
Remove-Item Env:TICKETS_HUNTER_RUN_BROWSER_FIXTURES
```

兩個 runner 均只開啟 `tests/fixtures/ctbc/` 的合成 `file://` 頁面；新 owner 的案例位於
`tests/test_utiki_engine_browser_flow.py`。Windows 上 pytest 預設的暫存
路徑若超出 `MAX_PATH`，使用短且專用的 `--basetemp C:\utiki-pytest-run`；每次執行
前先確認路徑不含需保留的資料，避免 pytest 清除舊 basetemp 內容。

runner 每次建立暫存 browser profile，並只在 `finally` 停止該次測試自行啟動的
browser；不得改成共用私人 profile 或廣泛終止 Chrome／Python 程序。

#### uTiki 離線瀏覽器與效能驗證

- `tests/test_ctbc_dom_fixtures.py` 使用本機 HTML，適合檢查 selector、原生事件與 production script 的瀏覽器行為；它不證明真站相容，也不證明網路硬隔離。
- 需要 production-origin 或外部網路不可達證據時，使用隔離容器中的合成頁面與專用 browser profile。現有 `utiki-p1-gate.yml` 包含隔離工作；`utiki-p2-gate.yml` 仍在倉庫，但自動推送觸發只指向已刪除的舊分支，不會在目前重構分支執行。替代流程可執行之前不關閉隔離。後續驗收只需回報測試、隔離結果與失敗原因，不再建立每案例文件核准紀錄。
- Windows 與 macOS 原始碼啟動、腳本資源路徑與設定 UI 在正式入口切換時分別驗證；Linux 容器結果不能代替兩個使用平台。
- 效能基線和修改後數值在相同頁面、設定與執行環境比較，分開記本機決策、CDP 往返與站點等待。保留必要的即時核對與 429 節流，沒有量測收益的優化可撤回。
- 目前舊 v1.6 gate 的失敗要明列；它們既不能冒充功能失敗，也不能在新流程完成前被靜默略過。

#### uTiki 目標 CI（待實作）

後續將以清楚的測試結果取代 catalog／manifest／逐義務 evidence 框架：

1. **原始碼 job**：Windows 與 Linux 跑 strict pytest、修改過的 Python 語法、適用的 mypy 與 JavaScript syntax。完整流程切換時加入 macOS source 啟動與資源檢查。
2. **隔離瀏覽器 job**：Linux 容器禁止外部網路，使用合成頁面、production scripts 與專用 profile，驗證選擇、填值、未知提交及刷新競態。失敗顯示具體測試與原因。
3. **效能檢查**：在固定 fixture 中比較同一流程的等待與瀏覽器往返；先記錄基線，再設定可合理重現的回歸界線。coverage 作為缺口提示，不直接移植其他專案的百分比。

這是目標流程，現有 workflow 尚未改造。替代檢查實際可執行且涵蓋安全情境後，再移除舊 gate 及其兩份機器依賴文件；不把刪除檢查當成修復 CI。

### 4.5 建置驗證

本節保留給產品打包仍在已採用計劃及當次授權範圍內的工作；不能因新增 import、資源或 package
就自行取得產品建置、發布或遠端 runner 權限。uTiki 本輪個人原始碼需求明確不交付產品打包、
frozen 成品或安裝包；測試 image 與 hard-isolation 準備不屬於產品打包。

產品打包適用時，常見觸發條件包括：

- 新增或移除 import。
- 修改 `requirement.txt`。
- 修改 `build_scripts/*.spec` 或打包腳本。
- 新增平台模組或 package。
- 準備發行。

```bash
python build_scripts/build_local.py
```

本地建置成功不等同 GitHub Actions 的 Windows 與 macOS release job 都已成功；兩者要分開陳述。

`.github/workflows/tests.yml` 會在 Ubuntu 與 Windows 的 Python 3.11.9 執行預設 pytest suite 與 coverage 報告；依上述 marker 約定排除 browser/live 測試。只有 workflow 實際完成後才可宣稱這兩個平台的 CI 通過。

## 5. 依變更類型選擇驗證

| 變更 | 最低要求 |
|------|----------|
| 純文件 | `git diff --check`、連結與現況核對 |
| 純函式／選擇演算法 | 語法與相關測試；到期時依正式範圍跑完整單元套件 |
| 平台 DOM reader 或 selector | 上述項目、脫敏 fixture／契約測試；可用時加實機煙霧測試 |
| 點擊、填值、送出或 dialog recovery | 上述項目、重入／失敗案例、實機煙霧測試 |
| 刷新／429 協調 | 聚焦協調器、多實例或時間控制測試；到期時依正式範圍跑完整套件 |
| 設定 schema／載入儲存 | default、嚴格驗證、儲存回讀、UI 顯示與相關流程；只有契約要求時才驗遷移 |
| import／相依／spec | 語法與 import focused tests；到期時依正式範圍跑完整套件，產品建置僅在適用時執行 |

## 6. 測試設計準則

### 6.1 純邏輯測試

適合驗證：

- 設定正規化與遷移。
- URL 與頁型分類。
- 關鍵字優先順序、排除與 fallback。
- 座位排序、連座選擇、票數 audit。
- 彈窗分類與狀態轉換。

輸入與輸出應明確，不讀真實瀏覽器、時間或全域狀態。

### 6.2 瀏覽器契約測試

fake 應表達操作語意，例如 `read_snapshot()`、`commit_selection()`，並確認呼叫順序與參數。
不要靠搜尋 production JavaScript 字串中的 marker 來假裝執行 DOM；那只能證明字串存在，不能證明瀏覽器會正確執行。

最低要覆蓋：

- inspect → decide → commit 的順序。
- URL、document 或元素身分在 commit 前改變時 fail closed。
- 50 ms 重複 tick 不會重複 click、reload 或 submit。
- commit 失敗後不會把狀態標記成成功。

### 6.3 DOM fixture 測試

fixture 應來自脫敏後的真實頁面片段，並由 production reader 在真正 DOM 環境中解析。
不得由待測程式產生 fixture 再驗證自己。

目前 runner 位於 `tests/test_ctbc_dom_fixtures.py`，並依 4.4 節的環境變數與
`browser` marker 明確啟用；瀏覽器測試不得在一般 unittest discover 中無提示啟動。

fixture 必須：

- 移除 Cookie、token、帳號、姓名、電話、訂單與座位識別資料。
- 記錄來源頁型與擷取日期，不保存完整私人頁面。
- 用同一份 fixture 加 DOM mutation 覆蓋常見負例，避免大量近似檔案。

### 6.4 狀態隔離

- 每個測試建立自己的設定與狀態；不得依賴執行順序。
- 修改模組 `_state` 的測試必須在 tear down 還原。
- 時間、亂數、檔案與網路邊界使用可控制的 seam。
- fake 的回傳值要和操作名稱綁定，不要用容易因多一次呼叫就錯位的匿名 FIFO。

## 7. 實機煙霧測試

本節不是執行授權。實機測試只在有合法活動、使用者明確授權測試範圍、帳號／profile、停止點，
且風險可控時執行；預設使用專用合成或隔離 profile，不得自行使用日常 profile、真實帳密或私人設定。
目前 uTiki 真站由使用者另行手動驗收，`LIVE_NOT_VERIFIED` 與 `SOURCE_REFACTOR_COMPLETE`
分開記錄，不能把本節當成代理可操作真站或 legacy 刪除前必須真站成功的依據。

建議流程：

1. 記錄 branch、commit、平台、活動與預計走到的頁面。
2. 確認票數、關鍵字、登入方式與「自動刷新頁面間隔」。
3. 啟動 `python -u src/nodriver_tixcraft.py --input src/settings.json`。
4. 只觀察本次修改涉及的階段；到達付款頁面前停止自動流程。
5. 用 `Ctrl-C` 或程式自身停止機制終止本次程序，不關閉其他 Python／Chrome 工作。
6. 記錄實際結果、彈窗文字、URL 類型、刷新間隔與未走到的分支。

對有副作用的流程，至少確認：

- 點擊後狀態真的改變。
- 欄位填值 audit 通過後才送出。
- 同一 document 不會重複送出。
- 衝突或額度彈窗可正確分類；未知彈窗不會被當成可忽略錯誤。
- 重新整理經過刷新協調器並遵守設定間隔。
- 進入 checkout 後只通知，不自動付款。

## 8. 如何回報結果

使用精確層級，不把局部驗證誇大成完整驗證。

正確範例：

```text
- `git diff --check`：通過。
- `python -m pytest tests/test_ctbc_pure.py tests/test_ctbc_dom.py tests/test_ctbc_refresh.py`：94 tests passed（此數字是當次輸出，不是永久門檻）。
- 完整 pytest：未執行，原因是本次只修改文件。
- 實機：未執行；售票入口已關閉，因此 dialog recovery 仍待下一次可用活動驗證。
```

不正確範例：

```text
- 全部測試完成。（實際只跑了 py_compile）
- 功能正常。（實際只閱讀程式碼）
- CI 通過。（實際只在本機執行 unittest）
```

## 9. 失敗處理

完整檢查失敗時，先保留原命令與完整結果，列出所有失敗的測試及被前置錯誤阻擋的項目；
每組共同根因保留一份代表性完整 traceback。區分 production regression、測試耦合、
fixture 過期、結果收集錯誤及環境／權限不足，不把同一根因造成的多個失敗當成多個獨立問題。

先用最小重現定位各組根因，集中修正受影響路徑；修復期間重跑該組與相鄰的受影響測試。
若收集或環境錯誤使後續測試無法執行，排除阻礙後提早重跑完整檢查以補齊失敗清單。
整批問題處理完畢後再跑完整檢查一次；新的失敗再按根因分組，不為了尋找下一個問題
而每修一項就重跑全套或推送觸發 CI。仍未解決的問題記在 uTiki [STATUS.md](utiki-refactor/STATUS.md)。

工程審查的嚴重度與輸出格式見 [engineering-review.md](engineering-review.md)。

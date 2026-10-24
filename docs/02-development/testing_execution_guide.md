# 測試與驗證執行指南

**文件說明**：定義目前 repository 可執行的驗證層級、命令與結果陳述方式。

**最後更新**：2026-10-25

## 1. 原則

- 驗證強度要與風險相稱：先跑最相關的測試，再擴大範圍。
- 任何新測試必須實際執行；不能只建立測試檔就宣告完成。
- 平台流程可以拆成純邏輯、DOM 契約與瀏覽器流程測試，不應一概視為「無法測試」。
- 單元測試通過不代表真實網站流程通過；實機測試也不能取代可重複的自動測試。
- 測試不得提交或輸出 Cookie、帳號、驗證碼、付款資料、webhook URL 或完整頁面個資。
- 不得用廣泛的程序終止指令關閉所有 Python 或 Chrome；只停止本次測試啟動的程序。
- 實機測試的刷新頻率必須使用使用者設定，不得為加速測試而私自縮短。沒有明確設定時，人工調查預設至少間隔 5 秒。

## 2. 目前測試基線

本專案以 pytest 作為主要測試入口，並保留既有 `unittest.TestCase` 測試的相容性。測試目前位於：

```text
tests/
  test_cloudflare_handling.py
  utiki_support.py
  test_utiki_pure.py
  test_utiki_dom.py
  test_utiki_dom_fixtures.py
  test_utiki_refresh.py
  test_global_settings.py
  test_ibon_date_selection.py
  test_refresh_coordinator.py
  test_runtime_output.py
  test_settings_config.py
  test_utiki_dom_contract.py
```

Phase 1 導入時的基線是 pytest 收集並通過 155 個測試（其中 CTBC 94 個）；這是用來偵測遷移差異的起始快照，不是永久數量門檻。`pytest.ini` 定義預設收集與 marker，`requirements-dev.txt` 固定直接使用的測試工具版本。

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

| 層級 | 目的 | 典型命令／方式 |
|------|------|----------------|
| A. 靜態檢查 | 發現語法、空白與文件問題 | `py_compile`、`git diff --check`、連結檢查 |
| B. 聚焦測試 | 快速驗證本次修改的模組或案例 | `python -m pytest tests/test_<module>.py` |
| C. 完整單元套件 | 發現跨模組回歸 | `python -m pytest` |
| D. 建置驗證 | 驗證 import、相依套件與 PyInstaller | `python build_scripts/build_local.py` |
| E. 實機煙霧測試 | 驗證真實網站 DOM、導頁與瀏覽器行為 | 使用測試設定啟動，人工觀察並記錄範圍 |

此表保留全專案的打包驗證層級。已完成的 uTiki 重構當時以原始碼交付，故其歷史結果不包含 PyInstaller／frozen build；後續修改按當次交付目標選擇驗證層級。uTiki 目前只讀 current 行為設定，其讀取、儲存及熱更新應依現行程式和變更範圍驗證，不從舊計畫推導升級或 migration 義務。

開發中先跑受影響的聚焦案例。完整切片接線或整合時跑必要的預設套件，讓可執行案例完成並收集失敗；按共同根因集中處理，修復期間聚焦重驗，整批處理後再做必要的完整驗證。只有收集或環境錯誤阻擋後續案例時，排除阻礙後提早重跑完整套件以補齊結果。不訂額外固定次數，也不靠反覆推送觸發 CI 尋找下一個錯誤。

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
python -m py_compile src/platforms/utiki/auth.py tests/utiki_support.py tests/test_utiki_pure.py tests/test_utiki_dom.py tests/test_utiki_refresh.py
```

`py_compile` 只證明語法與匯入前編譯階段可通過，不證明行為正確。

### 4.3 聚焦測試

```bash
python -m pytest tests/test_utiki_pure.py tests/test_utiki_dom.py tests/test_utiki_refresh.py
python -m pytest tests/test_refresh_coordinator.py
```

也可以執行單一 test class 或 method：

```bash
python -m pytest tests/test_utiki_pure.py::UtikiPureSelectionTest
python -m pytest tests/test_utiki_pure.py::UtikiPureSelectionTest::test_tenant_auth_capabilities_keep_ctbc_credentials_scoped
```

### 4.4 完整測試

```bash
python -m pytest
```

需要核對收集範圍或遷移相容性時：

```bash
python -m pytest --collect-only -q
python -m unittest discover -s tests -p 'test_*.py'
```

pytest 可以執行現有 `unittest.TestCase`。日後若新增 pytest 原生函式或 fixture 測試，unittest 不會收集它們；屆時兩個 runner 的測試數不同是預期結果，不得把 unittest 當成完整 suite。

以中信脫敏樣本驗證共用 uTiki DOM 契約的 fixture 位於 `tests/fixtures/ctbc/`，runner 是
`tests/test_utiki_dom_fixtures.py`。它使用本機 `file://` HTML 與 `127.0.0.1` loopback server，不連線真實售票網站，
並同時受 `browser` marker 與 `TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1` 環境變數保護；
因此預設 pytest 會 deselect；unittest discover 不會收集 pytest-only runner，也不會
啟動瀏覽器。執行前需在本機安裝
Chrome、Chromium、Edge 或 Brave 等 zendriver 支援的瀏覽器。

macOS／Linux 明確執行命令：

```bash
TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1 python -m pytest -m "browser and not live" tests/test_utiki_dom_fixtures.py
```

Windows PowerShell 明確執行命令：

```powershell
$env:TICKETS_HUNTER_RUN_BROWSER_FIXTURES = "1"
python -m pytest -m "browser and not live" tests/test_utiki_dom_fixtures.py
Remove-Item Env:TICKETS_HUNTER_RUN_BROWSER_FIXTURES
```

runner 每次建立暫存 browser profile，並只在 `finally` 停止該次測試自行啟動的
browser；不得改成共用私人 profile 或廣泛終止 Chrome／Python 程序。

需要觀察覆蓋範圍時：

```bash
python -m pytest --cov=src --cov-report=term-missing
```

導入時在 macOS／Python 3.11 的當次整體 coverage 是 15%，僅作為技術債與後續補測的觀察值，目前不設失敗門檻。

依本專案測試約定，所有會啟動瀏覽器或連線真實售票網站的測試都必須分別標記 `browser` 或 `live`；預設 pytest 命令排除這兩個 marker。本機、非真站的瀏覽器測試使用 `python -m pytest -m "browser and not live"`，避免同時選中雙重標記的 live 案例。`live` 測試必須另有明確授權與就緒環境才能執行。Marker 在 collection 後才篩選，因此測試模組匯入本身不得產生瀏覽器、網路或其他外部副作用。

若完整套件因環境相依而失敗，要保存第一個可處理的失敗訊息，區分：

- 本次修改造成的回歸。
- 本機缺少相依套件或瀏覽器。
- 測試本身依賴未提供的私人設定。
- 與本次修改無關、原本已存在的失敗。

不得只寫「環境問題」而省略證據。

### 4.5 建置驗證

本節供交付範圍包含打包的工作使用；已完成的 uTiki 重構當時採 source-only。後續是否建置或列為驗收，依當次交付範圍決定。適用工作在以下情況執行：

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
| 純函式／選擇演算法 | 語法、相關測試、完整單元套件 |
| 平台 DOM reader 或 selector | 上述項目、合成／脫敏 fixture 或契約測試；真站可用且獲授權時另記煙霧測試 |
| 點擊、填值、送出或 dialog recovery | 上述項目、重入／失敗案例；真站範圍依使用者授權與適用計畫 |
| 刷新／429 協調 | 聚焦協調器測試、完整套件、多實例或時間控制測試 |
| 設定格式／UI | 適用的 default、current 讀取與儲存回讀、UI 顯示、熱更新及正式入口流程；只有當次需求或現行相容承諾包含舊格式遷移時才驗證 migration |
| import／相依／spec | 語法、完整套件與正式 source 入口；交付範圍包含打包時才做本地建置，跨平台範圍依當次需求 |

## 6. 測試設計準則

### 6.1 純邏輯測試

適合驗證：

- 設定正規化；只有當次需求或現行相容承諾包含舊格式遷移時才測遷移。
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
- 短間隔或重複 tick 不會重複 click、reload 或 submit。
- commit 失敗後不會把狀態標記成成功。

### 6.3 DOM fixture 測試

fixture 可由已脫敏的真實頁面片段，或依頁型契約手工建立的合成頁面構成，並由 production reader 在真正 DOM 環境中解析。
不得由待測程式產生 fixture 再驗證自己。

目前 runner 位於 `tests/test_utiki_dom_fixtures.py`，並依 4.4 節的環境變數與
`browser` marker 明確啟用；瀏覽器測試不得在一般 unittest discover 中無提示啟動。

fixture 必須：

- 移除 Cookie、token、帳號、姓名、電話、訂單與私人真實座位識別值；以一致的合成商品、席位及 payload key 取代私人值，保留跨快照、選擇、回讀及提交的對應。
- 記錄來源頁型；只有實際擷取時才記擷取日期，不保存完整私人頁面。
- 用同一份 fixture 加 DOM mutation 覆蓋常見負例，避免大量近似檔案。

### 6.4 狀態隔離

- 每個測試建立自己的設定與狀態；不得依賴執行順序。
- 修改模組 `_state` 的測試必須在 tear down 還原。
- 時間、亂數、檔案與網路邊界使用可控制的 seam。
- fake 的回傳值要和操作名稱綁定，不要用容易因多一次呼叫就錯位的匿名 FIFO。

## 7. 實機煙霧測試

本節不是執行授權。實機測試只在有合法活動、使用者明確授權測試範圍、設定與 profile，且風險可控時執行；使用指定的測試設定與專用 profile，不自行使用日常 profile，也不把內容複製到命令輸出或文件。uTiki 真站驗證記於[交接狀態](utiki-refactor/STATUS.md)，未執行就如實標示。

建議流程：

1. 記錄 branch、commit、平台、活動與預計走到的頁面。
2. 確認票數、關鍵字、登入方式與「自動刷新頁面間隔」。
3. 使用經授權的測試設定啟動 `python -u src/nodriver_tixcraft.py --input <測試設定路徑>`。
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
- `python -m pytest tests/test_utiki_pure.py tests/test_utiki_dom.py tests/test_utiki_refresh.py`：填入本次實際的 tests／subtests 結果；Phase 1 的 94 個 CTBC 測試是導入時快照，不是現行門檻。
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

完整檢查失敗時，保留原命令、全部失敗案例及被前置錯誤擋住的範圍；每組共同根因至少保留一份具代表性的完整 traceback。區分 production regression、測試耦合、fixture 過期與環境問題，不把同一根因造成的多個失敗當成不同問題。

先以最小案例定位各組根因，集中修正受影響路徑；修復期間跑該組及相鄰的聚焦測試。整批處理後再做必要的完整驗證。若無法安全修正，保留證據與未解範圍，不為了全綠擴張行為或修改原預期。

工程審查的嚴重度與輸出格式見 [engineering-review.md](engineering-review.md)。

# 測試與驗證執行指南

**文件說明**：定義目前 repository 可執行的驗證層級、命令與結果陳述方式。

**最後更新**：2026-10-15

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
  test_ctbc_selection.py
  test_global_settings.py
  test_ibon_date_selection.py
  test_refresh_coordinator.py
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
python -m py_compile src/platforms/ctbc.py tests/test_ctbc_selection.py
```

`py_compile` 只證明語法與匯入前編譯階段可通過，不證明行為正確。

### 4.3 聚焦測試

```bash
python -m pytest tests/test_ctbc_selection.py
python -m pytest tests/test_refresh_coordinator.py
```

也可以執行單一 test class 或 method：

```bash
python -m pytest tests/test_ctbc_selection.py::CtbcPureSelectionTest
python -m pytest tests/test_ctbc_selection.py::CtbcPureSelectionTest::test_name
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

在以下情況執行：

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
| 平台 DOM reader 或 selector | 上述項目、脫敏 fixture／契約測試；可用時加實機煙霧測試 |
| 點擊、填值、送出或 dialog recovery | 上述項目、重入／失敗案例、實機煙霧測試 |
| 刷新／429 協調 | 聚焦協調器測試、完整套件、多實例或時間控制測試 |
| 設定 schema／遷移 | default、舊設定遷移、儲存回讀、UI 顯示、相關流程測試 |
| import／相依／spec | 語法、完整套件、本地建置 |

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

目前 repository 尚未提供 DOM fixture runner，因此本節是新 runner 的驗收要求，不是現行可宣稱已通過的測試。
引入 runner 的變更必須同時記錄目錄、啟動／skip 規則、精確命令與環境需求；瀏覽器測試不得在一般 unittest discover 中無提示啟動。

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

實機測試只在有合法活動、使用者授權且風險可控時執行。使用既有私人設定與瀏覽器 profile，不把內容複製到命令輸出或文件。

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
- `python -m pytest tests/test_ctbc_selection.py`：94 tests passed（此數字是當次輸出，不是永久門檻）。
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

測試失敗時：

1. 保留第一個完整 traceback 與執行命令。
2. 以最小案例重跑，確認是否穩定重現。
3. 判斷是 production regression、測試耦合、fixture 過期或環境問題。
4. 修正後先跑失敗案例，再跑受影響模組，最後視風險跑完整套件。
5. 若無法安全修正，停止擴張修改範圍並在交付說明未解問題。

工程審查的嚴重度與輸出格式見 [engineering-review.md](engineering-review.md)。

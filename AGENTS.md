# AGENTS.md

本檔是 Tickets Hunter 對 AI 編碼工具與自動化代理的唯一權威入口。
`CLAUDE.md` 僅保留為相容性指標；規則若有衝突，以本檔及本檔指向的專責文件為準。

**專案**：Tickets Hunter — 多平台購票輔助系統

**授權**：GNU GPL

**人類貢獻流程**：[CONTRIBUTING.md](CONTRIBUTING.md)

## 1. 不可違反的邊界

- 本軟體僅供教育與研究用途；先遵守 [LEGAL_NOTICE.md](LEGAL_NOTICE.md)。
- 禁止用於加價轉售、商業牟利、惡意囤票或其他違法用途。
- 台灣地區不得用於《文化創意產業發展法》涵蓋的票券；適用範圍以法律聲明及現行法規為準。
- 不得實作自動付款。到達付款頁面只能通知，付款必須由使用者手動完成。
- 不得為繞過票務平台風控而新增功能。
- 不得在程式碼、測試資料、記錄、Issue、PR 或 commit 中洩漏密碼、Cookie、token、webhook URL、瀏覽器 profile 或完整個人資料。
- `src/settings.json` 是私人設定檔，禁止提交或在輸出中原樣展示。

## 2. 開始工作前

1. 確認目標 repository、branch 與 `git status`；既有未提交變更一律視為使用者工作，不得覆寫。
2. 準備形成 PR 的修改必須有對應 Issue。若沒有，先確認使用者已授權在正確 repository 建立；未授權時請使用者或維護者建立。PR 連結方式依 [CONTRIBUTING.md](CONTRIBUTING.md)。
3. 閱讀 [程式碼邊界](docs/02-development/code-boundaries.md)，並搜尋同類平台的既有實作。
4. 涉及搶票流程時，對照 [12 階段標準](docs/02-development/ticket_automation_standard.md)。
5. 先定義可觀察的預期行為與驗證方式，再修改程式碼。

架構重寫、設定格式遷移、跨平台共用抽取等高風險變更，必須先有經維護者同意的正式計劃；不得因整理程式碼而順便改變行為。

## 3. 文件責任

| 文件 | 主要責任 |
|------|----------|
| `AGENTS.md` | 代理起手式、絕對限制、完成條件與文件路由 |
| `CONTRIBUTING.md` | Issue、branch、commit、PR 等人類貢獻流程 |
| `docs/02-development/code-boundaries.md` | 模組職責、依賴方向、命名與禁止模式 |
| `docs/02-development/ticket_automation_standard.md` | 12 階段流程及其語意 |
| `docs/02-development/testing_execution_guide.md` | 驗證層級、實際命令與結果陳述方式 |
| `docs/02-development/engineering-review.md` | 工程審查範圍、嚴重度與交付格式 |
| `docs/06-api-reference/zendriver_api_guide.md` | zendriver 與 CDP 使用方式 |
| `docs/07-deployment/` | PyInstaller 與發行打包 |

穩定架構與平台語意應寫在專責文件，不在本檔複製長篇內容。若行為與文件不一致，修改時一併修正文件。

## 4. 架構與依賴

- 瀏覽器自動化只使用 `zendriver`，慣例為 `import zendriver as uc`。
- 主入口為 `src/nodriver_tixcraft.py`；平台邏輯位於 `src/platforms/`。
- 共用瀏覽器基礎設施位於 `src/nodriver_common.py`；純工具位於 `src/util.py`。
- 平台不得匯入其他平台或 `nodriver_tixcraft`；`nodriver_common` 不得反向匯入平台。
- 使用者可調整的行為必須由 `config_dict` 控制；新增設定鍵必須在 `src/settings.py` 宣告並評估遷移。
- 家族平台是否共用單檔或 package，以 [程式碼邊界](docs/02-development/code-boundaries.md) 及已核准的架構計劃為準；不得自行建立全專案抽象框架。
- 發行版由 PyInstaller 建置。新增 import 或相依套件時，必須同步檢查 `requirement.txt` 與 spec hidden imports。

## 5. 瀏覽器自動化守則

- 優先使用 CDP 原生方法；JavaScript 只作小範圍輔助。
- `tab.query_selector()` 找不到元素時回傳 `None`，必須明確檢查。
- `click()` 成功呼叫不代表頁面狀態已改變；點擊後要驗證 checkbox、dialog、URL 或 DOM 狀態。
- `tab.reload()` 不等待頁面載入完成；重新整理後必須等待可辨識的新 document／頁面狀態。
- 主迴圈約每 50 ms 執行一次。填值、點擊、送出、重新整理與通知都必須可重入且有防重複機制。
- 等待使用 `sleep_with_pause_check` 或 `asyncio_sleep_with_pause_check`，讓暫停功能持續有效。
- 輸入框使用 `nodriver_common.CONST_NATIVE_INPUT_SETTER_JS`，並檢查回傳結果；填值失敗不得送出。
- `querySelector('A, B')` 是文件順序，不是 A 優先。需要優先級時分開查詢。
- 注入腳本使用 IIFE，不寫入 `window.*`；Python 資料以 `json.dumps()` 傳入，不直接拼接。
- Shadow DOM 使用 CDP `DOM.getDocument(pierce=True)`。
- 無法確認頁面、欄位、彈窗或庫存身分時，必須 fail closed，交還使用者，不可猜測後執行副作用。
- 所有自動刷新都必須遵守使用者設定與既有刷新協調器；測試不得私自提高頻率。

完整差異與範例見 [zendriver API 指南](docs/06-api-reference/zendriver_api_guide.md)。

## 6. 狀態、輸入與記錄

- 除錯輸出使用 `util.create_debug_logger(config_dict)`，不得新增 `if verbose: print()`。
- `.py` 檔案禁止 emoji，避免 Windows cp950 編碼失敗。
- 新增工具函式前先搜尋 `src/util.py`；修改共用函式前評估所有呼叫端。
- 可選設定使用 `.get()` 及安全預設值；面向使用者的訊息使用設定介面的實際名稱。
- 關鍵字比對沿用現有語意；套用排除關鍵字前先確認候選文字非空。
- 網址、DOM、網路回應與設定內容都是不可信輸入。禁止 `eval`，也禁止將使用者衍生字串交給 `shell=True`。
- 付款、帳號、驗證碼、Cookie 與通知端點不得出現在一般 debug 記錄。

## 7. 驗證與審查

測試工作先啟用已安裝 `requirements-dev.txt` 的專案虛擬環境；只執行 production／build 工作時至少安裝 `requirement.txt`。再依
[測試執行指南](docs/02-development/testing_execution_guide.md) 選擇與變更風險相稱的層級。最低要求：

- 文件變更：檢查連結、格式、`git diff --check`，並確認描述符合 repository 現況。
- Python 變更：對修改過的檔案執行 `python -m py_compile`。
- 可單元測試的邏輯：先跑相關 pytest，再跑 `python -m pytest`；過渡期間亦維持既有 unittest suite 相容。
- import、相依套件或 spec 變更：執行本地打包驗證。
- 即時售票流程：單元測試不能取代實機煙霧測試；記錄實際平台、頁面、操作範圍與刷新間隔。

不得把「閱讀過」、「語法通過」、「單元測試通過」寫成「完整流程已驗證」。審查依 [工程審查指南](docs/02-development/engineering-review.md) 採 findings-first，先列可重現問題，再寫摘要。

## 8. 完成條件

只有在以下條件成立時才可宣告完成：

- 修改範圍符合 Issue／核准計劃，沒有夾帶無關重構。
- 既有使用者變更與敏感資料未被覆寫或納入提交。
- 相關程式碼、測試、設定、文件與 PyInstaller 設定保持一致。
- 已執行適當驗證並如實記錄命令、結果與未驗證項目。
- 送出、填值、刷新、通知等副作用具備防重入、失敗邊界與必要的回復路徑。
- 已依變更風險完成自我審查；高風險變更另由第二位審查者覆核。

## 9. 語言與提交

- Markdown、既有中文註解及使用者訊息使用繁體中文（台灣用語）；新增 `.py` 註解優先使用英文，既有中文註解不必改寫。程式碼識別字、log 標籤與 commit 訊息使用英文。
- 設定名稱沿用 UI 既有文字，例如「刷新」與「無圖形界面模式」。
- Commit 與 branch 命名依 [CONTRIBUTING.md](CONTRIBUTING.md)，不含 emoji。

## 10. 常用入口

- [開發文件索引](docs/02-development/README.md)
- [程式碼邊界](docs/02-development/code-boundaries.md)
- [測試執行指南](docs/02-development/testing_execution_guide.md)
- [工程審查指南](docs/02-development/engineering-review.md)
- [12 階段標準](docs/02-development/ticket_automation_standard.md)
- [zendriver API 指南](docs/06-api-reference/zendriver_api_guide.md)
- [PyInstaller 打包指南](docs/07-deployment/pyinstaller_packaging_guide.md)

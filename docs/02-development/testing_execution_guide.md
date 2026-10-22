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

先啟用已安裝 `requirement.txt` 與 `requirements-dev.txt` 的專案虛擬環境，並確認 active interpreter 符合專案 Python 版本。
下列命令的 `python` 一律指該 interpreter，不是未安裝相依套件的系統 Python。

| 層級 | 何時使用 | 要做的事 |
|------|----------|----------|
| 聚焦檢查 | 一個有意義的修改單位完成後，以及批次修復中的重驗 | `git diff --check`、修改過的 Python 語法、受影響 pytest；適用時加 mypy／JS syntax |
| 本機完整檢查 | 完整流程切片接通、跨模組接線、切換正式路徑或準備交付候選 | 完整 pytest、型別與語法範圍、設定／資源測試，以及環境允許的隔離瀏覽器 fixture；先收集全部失敗再批次修復 |
| GitHub CI | 使用者下令推送候選後，由 push／PR 事件觸發 | 以 `.github/workflows/tests.yml` 的 unit、uTiki types 與隔離 browser job 結果為準；紅燈列原因，不把本機結果寫成 CI 通過 |
| 真站手動驗收 | 使用者之後有可用活動時 | 由使用者以專用設定核對網站 DOM、導頁、提交邊界；單獨記錄，不由離線測試代替 |

產品打包另依本文件 4.5 節與當次交付範圍驗證。

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
python -m py_compile src/platforms/utiki/production_run.py src/platforms/utiki/dom.py src/platforms/utiki/profiles.py tests/ctbc_support.py tests/test_ctbc_pure.py
```

`py_compile` 只證明語法與匯入前編譯階段可通過，不證明行為正確。

### 4.3 聚焦測試

```bash
python -m pytest tests/test_ctbc_pure.py tests/unit/utiki/test_p2_selection_flow.py tests/unit/utiki/test_refresh_owner.py
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

開發中若只需先確認一般功能、單元與升級器測試，可明確執行較快的聚焦範圍：

```bash
python -m pytest --strict-config --strict-markers -q --ignore=tests/architecture --ignore=tests/performance
```

此命令排除 `tests/architecture` 與 `tests/performance`，因此不是完整驗證；修改其契約時仍須跑受影響的測試，完整流程切片與交付候選仍依上方完整命令及第 3 節驗證。要找出當次最慢的案例與 setup／call／teardown，可對實際執行範圍加上 `--durations=20`；不要只根據檔案大小判斷耗時。

需要核對收集範圍或遷移相容性時：

```bash
python -m pytest --collect-only -q
python -m unittest discover -s tests -p 'test_*.py'
```

pytest 可以執行現有 `unittest.TestCase`。日後若新增 pytest 原生函式或 fixture 測試，unittest 不會收集它們；屆時兩個 runner 的測試數不同是預期結果，不得把 unittest 當成完整 suite。

合成瀏覽器頁面位於 `tests/fixtures/ctbc/`，由 `tests/test_utiki_engine_browser_flow.py`
以現行 engine、adapter 與 production script 執行 `file://` 流程。runner 受 `browser` marker
與 `TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1` 控制；預設 pytest 會 deselect，unittest
discover 不收集 pytest-only runner。執行前需安裝 zendriver 支援的 browser。

macOS／Linux 明確執行命令：

```bash
TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1 python -m pytest -m "browser and not live" tests/test_utiki_engine_browser_flow.py
```

Windows PowerShell 明確執行命令：

```powershell
$env:TICKETS_HUNTER_RUN_BROWSER_FIXTURES = "1"
python -m pytest -m "browser and not live" tests/test_utiki_engine_browser_flow.py
Remove-Item Env:TICKETS_HUNTER_RUN_BROWSER_FIXTURES
```

此 runner 只開啟 `tests/fixtures/ctbc/` 的合成 `file://` 頁面，驗證 DOM／腳本及隔離 engine 切片，不涵蓋正式 origin 上的完整 classifier、tenant、bootstrap、auth 與 host caller 接續。Windows 上 pytest 預設的暫存
路徑若超出 `MAX_PATH`，使用短且專用的 `--basetemp C:\utiki-pytest-run`；每次執行
前先確認路徑不含需保留的資料，避免 pytest 清除舊 basetemp 內容。

runner 每次建立暫存 browser profile，並只在 `finally` 停止該次測試自行啟動的
browser；不得改成共用私人 profile 或廣泛終止 Chrome／Python 程序。

#### uTiki 離線瀏覽器與效能驗證

| 測試入口 | 可證明的範圍 | 不能代替 |
|---|---|---|
| `tests/test_utiki_engine_browser_flow.py` | `file://` 合成頁中的實際 browser 輸入／點擊、`isTrusted` 來源、OCR、DOM 回讀與隔離 engine 切片 | production-origin 完整 classifier／tenant／provider 接線 |
| `tests/browser/utiki/test_production_origin_contract.py`、`tests/browser/utiki/test_batch0_production_origin_flow.py` | 硬隔離下的合成 production-origin 多頁流程，經實際 classifier、tenant、bootstrap、session provider、adapter、scripts、SQLite coordinator、新文件接續及正式 `main` | 目標 Windows／macOS 原始碼啟動、CI 或真站 |
| `tests/browser/utiki/test_batch5_settings_ui.py` | 同一硬隔離容器內從 loopback 設定頁操作舊格式提示、欄位儲存與重新載入，對照正式 current validator | 真站頁面或使用者日常 profile |

人工來源測試須使用 browser 的可信輸入／點擊；程式呼叫 `dispatchEvent()` 或 `click()` 不算人工證據。各批實際通過數、缺口及舊 gate 的歷史失敗見 [STATUS](utiki-refactor/STATUS.md)。

執行限制：

- 網路隔離須在 browser 導航前實際生效，未知或對外請求預設拒絕，並由測試／容器證據確認；手動設定 `TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1` 只啟用 runner，不能替代防火牆、CDP 攔截或容器層的硬隔離。現有隔離是否符合該條件須按實際執行證據回報。
- Windows 與 macOS 原始碼啟動、腳本資源路徑與設定 UI 在正式入口切換時分別驗證；Linux 容器結果不能代替兩個使用平台。
- 效能基線和修改後數值在相同頁面、設定與執行環境比較，分開記本機決策、CDP 往返與站點等待。保留必要的即時核對與 429 節流，沒有量測收益的優化可撤回。
- 舊 v1.6 gate 與固定 SHA 斷言已依計畫第 11.2 節從目前 source 退役。先前失敗保留在 STATUS 的歷史驗證紀錄；目前產品行為與資源完整性由現行測試及 runtime 自檢驗證。

#### uTiki 目前 CI 工作流程

`.github/workflows/tests.yml` 包含以下工作；本機執行結果與遠端 CI 結果須分開記錄：

1. **原始碼 job**：Windows 與 Linux 執行預設 pytest／coverage、Python source 語法、正式入口資源自檢及 JavaScript syntax。
2. **型別與邊界 job**：Linux 對 `ci/utiki-typed-scope.txt` 跑 strict mypy，並執行現行架構 checker。
3. **隔離瀏覽器 job**：Linux Docker `--network none` 在 browser 啟動前生效，合成 production origin、實際 scripts 與專用 profile 跑正式 caller、三模式與控制；OS 網路邊界由獨立案例確認。

效能以同 fixture、設定及工作量量測；實際數值與可比性記於 [STATUS](utiki-refactor/STATUS.md)，不以未經驗證的硬性 CI 門檻代替判讀。

可在專案 Python 3.11 虛擬環境執行的 uTiki 聚焦命令如下；完整 suite 仍依 4.4 節。依當次改動縮小受影響測試，完成整個切片時再完整驗證。

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/utiki tests/test_ctbc_pure.py tests/architecture/test_utiki_p2_port_types.py
.venv\Scripts\python.exe -m pytest -q tests/unit/utiki/test_refresh_owner.py tests/test_refresh_coordinator.py tests/architecture/test_utiki_architecture_gate.py
Get-ChildItem src/platforms/utiki/scripts -Filter *.js | ForEach-Object { node --check $_.FullName; if ($LASTEXITCODE -ne 0) { throw "JS syntax failed: $($_.FullName)" } }
$env:TICKETS_HUNTER_RUN_BROWSER_FIXTURES='1'
.venv\Scripts\python.exe -m pytest -q -m browser tests/test_utiki_engine_browser_flow.py
Remove-Item Env:TICKETS_HUNTER_RUN_BROWSER_FIXTURES
```

production-origin 合成流程使用由 `ci/utiki-isolation/Dockerfile`、目前 `requirement.txt`／`requirements-dev.txt` 及 digest 固定的 Python 基底建好的 Linux amd64 映像；建置 context 只傳入兩份需求檔。下列命令先建置現行映像，再以映像內容 ID 與目前工作樹執行，容器層在瀏覽器啟動前禁止網路；先核對介面及路由，再執行測試。映像版本及既有結果見 [STATUS](utiki-refactor/STATUS.md)。

```powershell
$repo = (Get-Location).Path
$context = Join-Path $env:TEMP 'utiki-ci-image-input'
New-Item -ItemType Directory -Path $context -Force | Out-Null
Copy-Item -LiteralPath requirement.txt -Destination $context
Copy-Item -LiteralPath requirements-dev.txt -Destination $context
docker build --pull=false --build-arg BASE_IMAGE=python:3.11-slim@sha256:cce12316475d7d56d605f76e1d811d0dc4e7227f2d4afd29951521264f504ff8 -f ci/utiki-isolation/Dockerfile -t utiki-current-browser:local $context
if ($LASTEXITCODE -ne 0) { throw 'uTiki image build failed' }
$image = docker image inspect utiki-current-browser:local --format '{{.Id}}'
if ($LASTEXITCODE -ne 0) { throw 'uTiki image inspection failed' }
$limits = @('--rm', '--pull', 'never', '--network', 'none', '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--shm-size', '1g', '--tmpfs', '/tmp:rw,exec,nosuid,size=512m', '--env', 'HOME=/tmp', '--env', 'PYTHONDONTWRITEBYTECODE=1', '--mount', "type=bind,src=$repo,dst=/workspace,readonly")
docker run @limits $image sh -c 'ls /sys/class/net && cat /proc/net/route'
docker run @limits --env TICKETS_HUNTER_HARD_EGRESS_ACTIVE=1 --env TICKETS_HUNTER_PRODUCTION_ORIGIN_REPORT=/tmp/utiki-origin-contract.json $image python -m pytest -q -o addopts= -p no:cacheprovider tests/browser/utiki/test_hard_egress_os_contract.py tests/browser/utiki/test_production_origin_contract.py tests/browser/utiki/test_batch0_production_origin_flow.py tests/browser/utiki/test_batch5_settings_ui.py
```

前置檢查須只有 `lo` 且沒有非 loopback 的預設路由；若不符便不要執行第二條命令。售票流程案例由 CDP 在第一次 production-origin 導航前啟用 Request 階段攔截，未知請求實測拒絕；設定 UI 案例只連容器內的 loopback server。正式 `main` 案例使用實際 route 與組裝，隔離 browser 啟動、合成頁導航、外部控制檔及測試用 SQLite 路徑等環境接點，沒有替換新 owner 的選擇。這只能證明該 Linux 容器中的合成流程；Windows／macOS 原生啟動、CI 與真站仍按各自層級驗證。

目標 OS 的原生原始碼與 `file://` browser 檢查另行執行。請先安裝 Python 3.11、專案開發相依套件及 Chrome／Chromium；`test_native_source_startup.py` 會以專用暫存 profile 和本地合成首頁啟動正式 `main`，下一個 tick 走停止路徑，並不導航售票 origin。這與上面的 production-origin 硬隔離是不同證據。

```powershell
# Windows x64，於 repository 根目錄
.venv\Scripts\python.exe src/nodriver_tixcraft.py --utiki-resource-self-test
$env:TICKETS_HUNTER_RUN_BROWSER_FIXTURES='1'
.venv\Scripts\python.exe -m pytest -q -o addopts= -m 'browser and not live' tests/browser/utiki/test_native_source_startup.py tests/test_utiki_engine_browser_flow.py
Remove-Item Env:TICKETS_HUNTER_RUN_BROWSER_FIXTURES
```

```bash
# macOS arm64，於 repository 根目錄；目前待在該 OS 實際執行
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python src/nodriver_tixcraft.py --utiki-resource-self-test
TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1 .venv/bin/python -m pytest -q -o addopts= -m 'browser and not live' tests/browser/utiki/test_native_source_startup.py tests/test_utiki_engine_browser_flow.py
.venv/bin/python -m pytest --cov=src --cov-report=term -q --tb=short
```

Mac 驗證須固定含批次 0–6 完整內容的提交 SHA；只 checkout 基準 `9e1b202` 會缺少重構成果。提交前另有工作樹旁的一次性 `utiki-b6-mac-source-20261023.zip`，其中 `SOURCE_VERSION.json` 含 base HEAD、檔案雜湊與刪除清單，並附同名 `.sha256`。若使用 ZIP，Mac 上先用 `shasum -a 256 utiki-b6-mac-source-20261023.zip` 比對，再 `unzip -q utiki-b6-mac-source-20261023.zip -d utiki-b6-mac-source-20261023`，切換到解壓目錄執行上列命令。快照不含 Windows `.venv`、私人設定、profile、cookies 或 session；它不是產品或 CI 依賴。

預期資源自檢回報 PASS，原生 smoke 建立並關閉 browser，`file://` fixture 完成正常日期／區域／票數與人工出口相關案例；完整一般 pytest 另記 passed／failed／skipped／deselected 與 coverage。macOS 未執行前維持待驗；如要在目標 OS 驗 production-origin，仍須先提供導航前生效的硬隔離與 CDP 預設拒絕，不以本地頁或環境旗標取代。

刷新／429 的真實 SQLite 與多程序案例分別在 `tests/unit/utiki/test_refresh_owner.py`、`tests/test_refresh_coordinator.py`；已知拒絕、三模式恢復、部分失敗及終局案例在 `tests/unit/utiki/test_p2_selection_flow.py`。選擇受影響的檔案聚焦重驗；需要 browser 證明的人工輸入、清理及新文件接續使用上列隔離 browser 命令。正式 auth 能力與人工出口狀態見 [STATUS](utiki-refactor/STATUS.md)。

### 4.5 建置驗證

產品打包只在計畫及當次工作範圍包含交付成品時驗證；新增 import、資源或 package
本身不構成產品建置或發布需求。uTiki 原始碼重構的隔離測試 image 與網路隔離準備不屬於產品打包。

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

依 marker 約定，預設 pytest 排除 browser/live 測試；CI 的 browser job 另外明確執行。只有 workflow 實際完成後才可宣稱對應 CI 通過。

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

fixture 可使用脫敏後的真實頁面片段或依頁型契約手工建立的合成頁面，並由 production reader 在真正 DOM 環境中解析；uTiki 新版驗收不要求先到真站擷取。不得由待測程式產生 fixture 再驗證自己。

目前 runner 位於 `tests/test_utiki_engine_browser_flow.py`，並依 4.4 節的環境變數與
`browser` marker 明確啟用；瀏覽器測試不得在一般 unittest discover 中無提示啟動。

fixture 必須：

- 移除 Cookie、token、帳號、姓名、電話、訂單與私人真實座位識別值；以一致的合成商品、席位與 payload key 取代私人值，保留跨 snapshot、選擇、回讀及提交的對應，不能把待驗證欄位整個刪掉。
- 記錄來源頁型；實際擷取時才記擷取日期，不保存完整私人頁面。
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
- `python -m pytest tests/test_ctbc_pure.py tests/unit/utiki/test_p2_selection_flow.py tests/unit/utiki/test_refresh_owner.py`：回報該次實際 passed／failed／skipped；不以歷史通過數作門檻。
- 完整 pytest：未執行，原因是本次只修改文件。
- 真站：未執行；此結果與離線 browser 驗證分開記錄。
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

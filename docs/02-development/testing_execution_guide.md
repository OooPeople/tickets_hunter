# 測試與驗證執行指南

**文件說明**：定義目前 repository 可執行的驗證層級、命令與結果陳述方式。

**最後更新**：2026-10-16

## 1. 原則

- 驗證強度要與風險相稱，但驗證範圍不得無條件逐次擴大：實作切片期間只跑最相關的測試，完整 suite 與正式 CI 留到 phase 出站。
- 任何新測試必須實際執行；不能只建立測試檔就宣告完成。
- 平台流程可以拆成純邏輯、DOM 契約與瀏覽器流程測試，不應一概視為「無法測試」。
- 單元測試通過不代表真實網站流程通過；實機測試也不能取代可重複的自動測試。
- 測試不得提交或輸出 Cookie、帳號、驗證碼、付款資料、webhook URL 或完整頁面個資。
- 不得用廣泛的程序終止指令關閉所有 Python 或 Chrome；只停止本次測試啟動的程序。
- 實機測試的刷新頻率必須使用使用者設定，不得為加速測試而私自縮短。沒有明確設定時，人工調查預設至少間隔 5 秒。

### 1.1 Phase 驗證節奏

大型計劃以核准文件定義的 phase 為驗證單位；一般修改若沒有正式 phase，則以一個可獨立交付、可回退的工作批次視為 phase。

1. **切片迭代**：修改哪個模組，就執行該模組的精確 pytest node、直接相依契約與修改檔 `py_compile`。同一失敗未修好前，不擴大到完整 suite。
2. **切片收斂**：相關測試通過後完成自我審查；高風險變更先完成第二位審查者覆核。審查修改仍以 focused tests 驗證。
3. **Phase 出站**：全部切片完成、檔案固定、審查無阻擋 finding 後，才執行一次完整 pytest；需要維持 unittest runner 相容時，在同一出站批次執行一次 unittest。
4. **正式 CI**：本機出站驗證通過後才推送並觸發一次 phase gate。不得以連續 push／rerun 逐一發現本機可重現的錯誤。
5. **CI 特有失敗**：僅限 runner、OS、容器隔離或 artifact 搬移等本機原先無法重現的差異。修正前先下載真實 artifact 或建立等價 fixture；用 focused regression 收斂後，再重新執行 phase 出站驗證與一次正式 CI。

完整 suite、coverage、打包與正式 CI 的必要義務沒有被取消；本節只限制執行時機，避免把高成本驗證當成開發內迴圈。

## 2. 目前測試基線

本專案以 pytest 作為主要測試入口，並保留既有 `unittest.TestCase` 測試的相容性。測試目前位於：

```text
tests/
  test_cloudflare_handling.py
  ctbc_support.py
  test_ctbc_pure.py
  test_ctbc_dom.py
  test_ctbc_dom_fixtures.py
  test_ctbc_refresh.py
  test_global_settings.py
  test_ibon_date_selection.py
  test_refresh_coordinator.py
  test_runtime_output.py
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
| C. Phase 出站完整套件 | 階段完成後發現跨模組回歸；切片期間不執行 | `python -m pytest` |
| D. Phase 出站建置 | 涉及 import、相依或打包時於階段完成後驗證 | `python build_scripts/build_local.py` |
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

本節命令只在 phase 出站執行一次，不是每個 commit 或 focused test 通過後的固定下一步。

```bash
python -m pytest
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
TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1 python -m pytest -m "browser and not live" tests/test_ctbc_dom_fixtures.py
```

Windows PowerShell 明確執行命令：

```powershell
$env:TICKETS_HUNTER_RUN_BROWSER_FIXTURES = "1"
python -m pytest -m "browser and not live" tests/test_ctbc_dom_fixtures.py
Remove-Item Env:TICKETS_HUNTER_RUN_BROWSER_FIXTURES
```

runner 每次建立暫存 browser profile，並只在 `finally` 停止該次測試自行啟動的
browser；不得改成共用私人 profile 或廣泛終止 Chrome／Python 程序。

#### uTiki production-origin fixture 與 hard-egress

`file://` fixture 只驗 DOM/browser contract，不是 production-origin 或網路硬隔離證據。
uTiki 的 production-origin fixture 必須由
`tools/run_utiki_browser_isolation.py` 啟動；launcher 只接受已存在本機、以
repository `@sha256:` 或 exact content-addressed `sha256:` image ID 固定的 Linux
image，且不會在測試期間 pull、build 或安裝依賴。browser、driver
與其子程序都在 `--network none`、read-only root、drop-all-capabilities 的同一個
container 中。另一個 internal-only network 的受控 peer 只用於正向控制；隔離
container 不加入該 network。

```bash
python tools/run_utiki_browser_isolation.py \
  --image 'sha256:64_HEX_IMAGE_ID' \
  --image-build-evidence artifacts/utiki-isolation/IMAGE_BUILD.json \
  --execution-manifest tests/fixtures/utiki/manifest.json \
  --run-dir artifacts/utiki-isolation/RUN_ID/evidence \
  --report artifacts/utiki-isolation/RUN_ID/evidence/browser-isolation-raw.json \
  --run-id RUN_ID \
  --attempt 1 \
  --phase P1 \
  --catalog-sha256 APPROVED_64_HEX_CATALOG_DIGEST \
  --candidate-sha EXACT_40_HEX_GIT_SHA
```

上述 image、build evidence 與 SHA 必須由當次受審查的 CI context 代入，範例字串
本身不能執行。GitHub Actions 的正式 P1 workflow 另要求 repository variable
`UTIKI_P1_BASE_IMAGE`；其值只能是維護者已審查的 `repository@sha256:<64-hex>`。
repository 的正式 P1 gate 已配置此變數；workflow 仍須逐次驗證它是 digest-pinned
reference。若變數缺失或格式不符，browser／performance required job 必須在 build 前
明確以 `BLOCKED` prerequisite 失敗；不得改用 mutable tag、臨時解析 tag，或把空值視為
skip／PASS。build evidence 要綁定 digest-pinned base image、實際 image ID，以及
`ci/utiki-isolation/Dockerfile`、`requirement.txt`、`requirements-dev.txt` 的當次
SHA-256；builder 只會把這三個檔案複製到暫存的最小 Docker build context，不會把
checkout 或私人設定送入 Docker daemon。image 必須先在隔離測試開始前完成 build/load。
正式執行前，launcher 另會要求 `HEAD` 等於 candidate SHA 且 working tree 乾淨，並以
該 SHA 的 `git archive` 建立暫存 workspace。Container 只會唯讀掛載這份 tracked-file
snapshot，不會掛載原 checkout，因此 ignored／untracked 的 `src/settings.json`、browser
profile 或 artifacts 不會進入隔離測試域；archive digest 會寫入 outer evidence。
launcher exit code `0` 才是當次 PASS，`1` 是已建立隔離後發現違規，`2` 是
`BLOCKED`（例如 runtime／image／權限／證據不足）。`BLOCKED` 不得轉成 skip 或
等效 PASS。raw report 只寫到明示的 `--report`；正式 job 另須由
`tools/run_utiki_evidence.py` 封裝成 execution manifest 所列的
`evidence/browser-isolation-report.json`。raw report 綁定 managed JUnit、reporter result、
inner canary、production-origin report，以及 container 內實際執行的 12 份 negative
probe envelope digest；缺任一項都不能封裝為 PASS。

production-origin 測試保留真實 origin/path，於 CDP request stage 僅允許已核准的
脫敏 fixture 並拒絕未知 request；故意未安裝 interception 的 browser/driver canary
則驗證 OS 邊界仍會攔截。兩者都禁止以連線真售票站作 canary。proxy、hosts、
host-resolver、socket monkeypatch 或 browser flag 都不能單獨充當 hard-egress 證據。

目前 launcher 的可執行參考環境是 Linux container。Windows/macOS native job 必須
另有涵蓋整個 process tree 的 VM／防火牆證據；沒有證據時維持 `BLOCKED`，不能借用
Linux 結果宣稱 native browser 已通過。

#### uTiki P1 performance-contract

P1 的五個 V12／V13 效能義務不是 benchmark 工具的單元測試。正式節點位於
`tests/performance/utiki/test_p1_performance_contract.py`，只能由
`tools/run_utiki_performance_isolation.py` 在 `isolated-linux-x64`、digest-pinned image、
核對過的 image build evidence 與 `--network none` 中執行：

```bash
python tools/run_utiki_performance_isolation.py \
  --image 'sha256:64_HEX_IMAGE_ID' \
  --image-build-evidence artifacts/utiki-isolation/IMAGE_BUILD.json \
  --execution-manifest tests/fixtures/utiki/manifest.json \
  --run-dir artifacts/utiki-ci/RUN_ID/performance-contract/isolated-linux-x64/evidence \
  --report artifacts/utiki-ci/RUN_ID/performance-contract/isolated-linux-x64/evidence/performance-isolation-raw.json \
  --run-id RUN_ID \
  --attempt 1 \
  --phase P1 \
  --catalog-sha256 APPROVED_64_HEX_CATALOG_DIGEST \
  --candidate-sha EXACT_40_HEX_GIT_SHA
```

這個命令和 browser isolation 共用預先建好的 image 與 build evidence，但以獨立 container
執行效能量測，避免只證明另一個 browser process 被隔離。固定方法由
`ci/utiki-performance-isolation.json` 定義：source cold 3 samples／case、warm 300、
event-loop 300，以及 production-origin date fixture 的 5 次 warm-up 與 30 次 reader
量測。每次 reader 直接計數實際 `tab.send` protocol command、ticketing request 與 latency；
fixture 只透過 CDP Request-stage interception fulfillment，不連真站。source 的四個
`case_results` 也必須逐一等於 P0 基線的 semantic digest，不能只以樣本數與零失敗冒充
行為一致。效能 container 同樣只讀 sanitized `git archive`；outer launcher 另產生並綁定
candidate workspace attestation，inner harness 逐檔核對 tree／subject digests，source
benchmark 由該 attestation 取得 candidate SHA 與 clean-tree metadata，不需要把 `.git`
或原 checkout 掛入 container。

inner report 在 controlled-peer 尚未由外層核對前只能是 `PENDING_OUTER_AUDIT`。outer report
確認 image/build、container security、peer positive controls、outbound count 0，且綁定 raw
samples、五個 managed JUnit nodes及 reporter digest 後，才可成為
`browser-isolation-report` 的 raw producer。接著
`tools/benchmarks/verify_utiki_p1_performance.py` 同時核對 outer PASS、raw benchmark、JUnit、
source/fixture/production subject manifest digests，產生 `benchmark-report` 的 raw producer。
兩者仍須分別由正式 evidence launcher封裝；缺 Docker、image、build evidence、Linux x64、
Chrome、完整樣本或任一 digest 時為 `BLOCKED`／nonzero，不能降級成 source-only PASS。

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
| 純函式／選擇演算法 | 切片期間跑語法與相關測試；phase 出站跑完整單元套件 |
| 平台 DOM reader 或 selector | 上述項目、脫敏 fixture／契約測試；可用時加實機煙霧測試 |
| 點擊、填值、送出或 dialog recovery | 上述項目、重入／失敗案例、實機煙霧測試 |
| 刷新／429 協調 | 切片期間跑聚焦協調器、多實例或時間控制測試；phase 出站跑完整套件 |
| 設定 schema／遷移 | default、舊設定遷移、儲存回讀、UI 顯示、相關流程測試 |
| import／相依／spec | 切片期間跑語法與 import focused tests；phase 出站跑完整套件與本地建置 |

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

測試失敗時：

1. 保留第一個完整 traceback 與執行命令。
2. 以最小案例重跑，確認是否穩定重現。
3. 判斷是 production regression、測試耦合、fixture 過期或環境問題。
4. 修正後先跑失敗案例，再跑受影響模組；完整套件只在該 phase 再次收斂並完成審查後執行。
5. 若無法安全修正，停止擴張修改範圍並在交付說明未解問題。

工程審查的嚴重度與輸出格式見 [engineering-review.md](engineering-review.md)。

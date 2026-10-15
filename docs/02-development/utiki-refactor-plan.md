# uTiki2 職棒售票引擎重構計劃

**文件狀態**：已核准，依 Phase 執行中

**建立日期**：2026-10-15

**追蹤 Issue**：[OooPeople/tickets_hunter#4](https://github.com/OooPeople/tickets_hunter/issues/4)

**相關功能 Issue**：[OooPeople/tickets_hunter#3](https://github.com/OooPeople/tickets_hunter/issues/3)

> 本文件是重構的執行契約，不是用完成結果回填計畫的日誌。每個 Phase 仍須通過其驗收與審查 gate 才能繼續。

## 1. 決策摘要

目前的 `src/platforms/ctbc.py` 已不是單純的「中信兄弟平台模組」，而是同時處理中信育樂與味全龍、共用 uTiki2 路由、選區／劃位／票數、登入、CAPTCHA、送出、彈窗恢復、刷新與 429 協調的家族引擎。

本計劃採用以下方向：

1. 程式碼家族名稱改為 `utiki`，表示可驗證的 uTiki2 page contract；不以球隊或「baseball」命名，避免把技術邊界綁死在目前業務用途。
2. 使用者介面仍顯示容易理解的「職棒售票網站」，品牌與 hostname 由 tenant profile 呈現。
3. 將單一 3,248 行模組逐步收斂成一個小型 package，而不是建立全專案通用框架。
4. 保留既有公開入口與設定相容期，使用 strangler migration；每階段都能獨立驗證及回退。
5. 先建立 characterization tests 與 DOM seam，再搬移行為；新舊實作不得同時執行點擊、送出或刷新。
6. 網站原生「電腦選位」在 area／quantity seam 完成後接入，不必等待整個家族重構完成，也不直接向舊 inline 流程加補丁。
7. 富邦悍將只列為候選 tenant；在登入、Cookie、路由、DOM、限流與結帳邊界完成驗證前，不宣告支援。
8. Phase 1 導入 pytest 作主要 runner，但不全面改寫現有 unittest；純函式測試隨拆檔逐步轉換，async／Tornado 測試保留相容形式直到有實際收益。

## 2. 背景與現況證據

### 2.1 模組已超出原命名與責任

- `src/platforms/ctbc.py`：3,248 行。
- 同時辨識 `tix.ctbcsports.com` 與 `tix.wdragons.com`。
- 公開函式已使用 `utk_sports` 名稱處理跨站刷新與健康檢查，但大量內部符號仍以 `ctbc` 命名。
- 單檔包含 URL parsing、tenant profile、DOM snapshot、純選擇演算法、瀏覽器 commit、登入、CAPTCHA、dialog、checkout 與 rate-limit recovery。
- 模組 `_state` 內有多組不同生命週期的資料；分頁、document、購買嘗試與刷新狀態彼此耦合。

### 2.2 測試保護存在，但耦合過深

- `tests/test_ctbc_selection.py`：計劃初稿時 2,390 行、82 個測試；Issue #2 的 submit/dialog safety 回歸加入後為 94 個。
- 測試直接匯入大量 private helper，也直接操作 `_state` 內部 schema。
- 部分 fake 以比對 JavaScript 字串／marker 模擬 DOM 結果，沒有真正執行 production script，可能出現假綠燈。
- 純選擇、URL、票數 audit、座位 audit、dialog classifier 與刷新協調已具備可保留的行為基準。
- general admission 與完整 seat orchestration 的端到端契約仍不足。

### 2.3 後續需求已需要清楚 seam

- 手動選區要維持精確關鍵字、可售來源與 commit-time 驗證。
- 網站原生電腦選位仍要用區域關鍵字選中目標列；它會先切換網站模式，再由該列進入 quantity page，並和手動劃位共用 CAPTCHA、送出與 dialog recovery，但不應走 date-page fast direct shortcut。
- 座位被選走、超出可購買額度、登入失效、CAPTCHA 錯誤與未知警告需要不同 recovery。
- 自動刷新同時受使用者間隔、跨程序協調與 429 cooldown 影響，不能散落在各 handler。

## 3. 目標與非目標

### 3.1 目標

- 用名稱與目錄表達「同一 uTiki2 引擎家族，多個 tenant」的真實架構。
- 將讀取頁面、純決策、執行副作用與狀態轉換分離，使核心規則可測。
- 保留目前 CTBC／味全龍行為與設定，避免大改期間影響既有搶票流程。
- 所有 click、input、submit、reload 都能 audit、去重、失敗回復。
- 讓新頁型或新 tenant 透過明確 contract 接入，不再複製整段主流程。
- 降低測試對 private helper 與 JavaScript 字串的耦合。
- 建立可在本機與 CI 重複執行的 pytest 基線，同時保留一個重構階段的 unittest 對照。
- 為 Issue #3 的電腦選位提供單一、可維護的接入點。

### 3.2 非目標

- 不建立所有售票平台共用的抽象 framework。
- 不改寫 `nodriver_tixcraft.py` 的全專案主迴圈。
- 不改用新的 GUI、瀏覽器 driver 或 dependency injection 套件；pytest 只作測試 runner 與 fixture 工具，不改變 production runtime。
- 不以導入 pytest 為理由全面改寫 143 個既有測試，或把 assert 語法替換冒充測試品質改善。
- 不處理自動付款、繞過風控、CAPTCHA 破解或提高刷新頻率。
- 不在沒有真實證據時宣告富邦或其他 hostname 已支援。
- 不為極低機率情境建立大型抽象；只有跨邊界、具副作用或已有重複錯誤的地方才建型別／介面。

## 4. 命名決策

### 4.1 家族名稱

| 層級 | 名稱 | 理由 |
|------|------|------|
| Python package | `platforms.utiki` | 對應共同技術引擎，不綁特定球隊或運動 |
| 公開 route helper | `is_utiki_sports_url` | 說明目前支援範圍仍是已驗證的職棒站點 |
| UI 類別 | 職棒售票網站 | 對一般使用者比引擎品牌清楚 |
| tenant key | `ctbc_brothers`、`wei_chuan_dragons` | 品牌／hostname 差異由 profile 管理 |
| 候選 tenant | `fubon_guardians` | 只在證據與契約測試通過後啟用 |

不把 package 命名為 `baseball`：相同引擎未必永遠只服務棒球；也不延續 `ctbc`：它會誤導味全與未來 tenant 的程式責任。

### 4.2 設定名稱

共同行為逐步移到 `utiki`：

```json
{
  "utiki": {
    "ticket_type_keyword": "全票",
    "seat_sorting": "row_seat",
    "seat_selection_mode": "manual"
  }
}
```

- `seat_selection_mode` 只允許 `manual` 或 `computer`，預設 `manual`，確保舊設定行為不變。
- `computer` 還必須通過 tenant capability gate；未驗證支援 server assignment 的 tenant 顯示明確訊息並停在原頁，不得 fallback 猜測。
- `accounts.ctbc_cookie`、`accounts.ctbc_account`、`accounts.ctbc_password` 暫不泛化；不同 tenant 的憑證不能因共用引擎而混用。
- `migrate_config()` 只在新鍵不存在時從 `ctbc` 複製非敏感行為設定；新鍵優先。
- 不複製秘密到第二組通用鍵。tenant 登入模型另案驗證後才遷移。
- 執行期讀取舊 `ctbc` 行為設定的 fallback 至少保留一個 release；冪等的 old-to-new migrator 長期保留，直到有版本化 schema 能證明使用者不會跳版遺失設定。
- 相容期內，UI／save path 以 `utiki` 為 canonical，並把相同的非敏感值單向鏡射到 legacy `ctbc`，讓程式降版時仍讀到最新值；新程式永遠 new-wins，舊值不得反向覆寫新值。Phase 8 才停止鏡射。

## 5. 目標架構

### 5.1 Package 結構

```text
src/platforms/
  ctbc.py                 # 一個 release 的相容 facade，不放新邏輯
  utiki/
    __init__.py           # 對主迴圈公開的穩定 API
    profiles.py           # tenant、allowlist、路由與 PageKind
    selection.py          # 純 parser、候選選擇、座位排序與 audit
    auth.py               # tenant 的 Cookie scope、登入能力與 credential source
    dom.py                # 頁面 snapshot reader 與原子 commit
    refresh.py            # reload、429、cooldown 與 coordinator 邊界
    workflow.py           # 12 階段 orchestration、runtime、dialog recovery
```

這是上限而不是一次建立的空殼。只有某責任已能獨立測試、或現有程式確實需要搬移時才建立檔案；若內容很少就維持合併，避免為分層而分層。

### 5.2 依賴方向

```text
nodriver_tixcraft
        |
        v
platforms.utiki.__init__
        |
        +--> workflow --> dom --> nodriver_common
        |       |         +--> selection
        |       +--> auth --> nodriver_common
        |       +--> refresh --> refresh_coordinator
        |       +--> selection
        |       +--> profiles
        +--> profiles / selection

selection --> util / Python 標準函式庫
profiles  --> Python 標準函式庫
```

- `selection.py` 可重用 `util` 的既有關鍵字函式，但不接觸 tab、網路、檔案、全域時間或 `_state`。
- `auth.py` 只表達 tenant-specific 的 Cookie scope、credential source 與登入 capability；不建立全專案 adapter framework。
- `dom.py` 不決定商業策略；它只讀 snapshot 或原子執行已核准的 plan。
- `workflow.py` 不拼湊大段匿名 JavaScript；它協調 reader、planner、committer 與 state reducer。
- `refresh.py` 是這個家族唯一可呼叫 reload 的位置。
- family package 內允許單向 import；跨 family 仍禁止。這項規則必須在 package 首次落地時同步更新 `code-boundaries.md`。

### 5.3 最小核心型別

只在跨邊界資料使用不可變 dataclass／enum：

- `TenantProfile`：tenant key、允許 host、路由特徵、登入能力及 `supports_server_assignment` 等已驗證 capability。
- `PageKind`：`DATE`、`AREA`、`RESERVED_SEAT`、`GENERAL_ADMISSION`、`SERVER_ASSIGNED_QUANTITY`、`CHECKOUT`、`UNKNOWN`。
- `PageSnapshot` 的頁型子類或具名資料：URL、document token、可見候選與 audit 必要資訊。
- `ActionPlan`：要選的穩定身分、預期 URL／document、數量與理由；不含 tab 物件。
- `CommitResult`：一般本機動作使用 `committed`、`rejected`、`stale` 與可診斷原因。
- `SubmitDispatchResult`：送出專用結果只允許 `DISPATCHED`、`REJECTED`、`STALE`；`DISPATCHED` 只表示 click 已送往頁面，`SUCCESS` 必須由後續 reducer 取得可驗證的導頁、dialog 或 response evidence 才能產生。

模組內短暫值不必全部包成 class；型別只保護 observe／decide／commit 邊界。

### 5.4 單次迴圈模型

```text
observe -> parse/reconcile -> gate -> plan -> commit -> reduce
```

1. **observe**：確認 tenant、URL、PageKind、document token，讀取頁面 snapshot。
2. **parse/reconcile**：正規化可售資料；多來源不同步時採保守規則。
3. **gate**：檢查 pause、pending action、cooldown、auth、dialog 與刷新 reservation。
4. **plan**：純函式依設定產生零或一個 ActionPlan。
5. **commit**：重新確認 URL、document、元素身分與可售狀態，再執行一次副作用。
6. **reduce**：依 CommitResult 更新 per-tab runtime；失敗不得寫成成功。

同一 document 上同一 intent 只能 commit 一次。需要比較新舊決策時可 shadow 計算，但永遠只有一方能 commit。

### 5.5 Runtime 邊界

保留專案慣例的模組 `_state = {}`，但內容收斂為：

```text
_state["tabs"][tab_identity]
  document      URL、document token、PageKind
  action        pending/committed local intent guard、attempt count
  transition    fast navigation、submit outcome、conflict/reload 等跨 document 狀態
  purchase      CAPTCHA、submit、dialog、checkout 狀態
  refresh       request token、reservation、cooldown/probe 狀態
```

- `PurchaseKey` 由 tenant、session/account fingerprint、performance、group、price-area 或 selection mode 組成；不得以完整 raw URL 取代。
- 所有程式自動觸發的跨頁動作先建立 `PendingNavigation`，記錄 `NATIVE`／`FAST`、source document、預期 route 與 identifiers；`FAST` 另在 browser session 寫入短期、非敏感 provenance marker，包含 nonce、source、expected destination 與 expiry，避免 Python state 遺失後被誤當成手動 native 抵達。marker 只對 exact destination 生效，成功 attest、確認安全返回 area 或 expiry 時都以 nonce compare-and-delete；有效 `NATIVE` pending 優先於 stale／不相符的 FAST marker。
- tab 關閉、離開已知 tenant 或 identity 改變時清理，runtime 不得強持有 tab 造成記憶體洩漏。
- document token 改變時，先用新 document 事件 reduce／attest `transition`，再清除 source-document snapshot、action guard 與頁面級 throttle；不得先清掉 `PendingNavigation`、submit awaiting、conflict wait-new-document 或 reload probe。
- cross-document transition 完成、拒絕或超時後才清理；PurchaseKey 的 quota／unknown blocker 及 tenant session 級資料不隨一般換頁消失。
- 設定 fingerprint 改變時只重設受影響決策，不清除無關限流資訊。
- quota／unknown 依 PurchaseKey sticky；auth 依 tenant、session 與 credential fingerprint sticky；只有對應 key／session 改變或使用者明確處理後解除。
- conflict 保存 source document，直到觀察到新 document；checkout 通知依 order/page identity 去重。

### 5.6 測試與工程工具決策

以下決策參考 `facebook_monitor_py` 的實際工具鏈，但以 Tickets Hunter 目前的 `requirement.txt`、Python 3.11、zendriver、PyInstaller 與 155 個 unittest 為準，不搬用對方的 Python 3.13／uv 打包體系。

| 工具／模組 | 決策 | 使用範圍與限制 |
|------------|------|----------------|
| `pytest` | Phase 1 納入 | 作主要 runner；先原樣收集既有 unittest，再逐步轉換純函式測試 |
| `pytest-cov` | Phase 1 納入 | 先產生 report-only baseline，不複製其他專案的 80% 全庫門檻 |
| `hypothesis` | 獨立 tooling Issue 評估 | 純 seam 穩定後才評估 URL/parser、關鍵字、座位排序與 quantity audit；不列入核心重構 gate |
| `ruff` | 獨立 tooling Issue 評估 | 可先試跑新 package／新測試，但不在重構 critical path 大量格式化 legacy 檔案 |
| `pip-audit` | 另立 tooling Issue | 針對 pinned runtime dependencies；初期 report-only／排程執行，不阻擋本重構的 page gate |
| `mypy` | 延後評估 | 等跨邊界 dataclass／enum 穩定後，只評估新 package；不對現有動態平台模組設定全庫門檻 |
| `pytest-asyncio` | 暫不加入 | 既有 `IsolatedAsyncioTestCase` 可直接由 pytest 執行；真的新增原生 async test function 時再評估 strict mode |
| `pytest-xdist` | 不加入 | module `_state`、SQLite 協調與 multiprocessing 測試尚未證明可安全平行 |
| `uv`／lockfile 遷移 | 不納入本計劃 | 會同時改變安裝、release CI 與 PyInstaller，應獨立評估 |
| `lizard`／全庫 complexity gate | 不納入硬門檻 | 本計劃已有函式邊界與 review gate；如需報告可另案 report-only，不因分數驅動無關拆檔 |

Phase 1 預定建立：

```text
requirements-dev.txt      引用 requirement.txt，再加入 pytest、pytest-cov 的精確版本
pytest.ini                testpaths、strict markers、預設排除 browser/live
.github/workflows/tests.yml
                          Python 3.11 的一般測試；先涵蓋 Ubuntu 與 Windows
```

規則：

- 開發工具只放 dev requirements，不加入 runtime `requirement.txt`，避免污染 PyInstaller。
- pytest 導入當下，`python -m unittest discover ...` 與 `python -m pytest` 必須收集並通過同一批既有測試；若 test count 不同，要先解釋 collection 差異。
- unittest 對照至少保留一個重構 Phase。pytest 成為主要 runner 不代表必須消滅所有 `TestCase`。
- `browser` 與 `live` marker 預設排除，必須由明確命令啟用；一般 CI 不得連線售票網站或啟動使用者 Chrome profile。
- coverage 先用來發現未測區域，不作行數競賽。只有新 `utiki` package 的 baseline 穩定且能排除不可達平台 glue 後，才另案討論 no-regression threshold。
- Phase 1 只完成 pytest 對既有 unittest 的 runner bootstrap、coverage baseline 與 CI，不同時大規模改寫測試語法。後續碰到純函式測試時可個別轉成 pytest function，但這不是任何 production Phase 的前置條件。
- Hypothesis 若另案採用，必須 pin 精確版本、納入預設 pytest suite，限制輸入大小並用具名 `@example` 固化回歸；若評估後不採用，也要記錄不變量不足或成本高於收益的理由。
- Ruff 若另案採用，必須 pin 精確版本、使用獨立 `ruff.toml`、列出精確 CI 命令與阻擋範圍；只檢查新 package／新測試，不要求清理全 repository 的歷史風格問題。

#### 上游同步與相依衝突策略

這個 fork 需要持續吸收 `bouob/tickets_hunter` 的更新，因此工具導入要保持「獨立、可刪、可合併」，不能讓測試依賴變成長期分叉點。

計劃審查時（2026-10-15）以只讀 fetch 核對原作者 `main` 的 `9e2d1061`：當時只有既有三份 workflow 與 `requirement.txt`，尚無 `pyproject.toml`、`pytest.ini`、`ruff.toml`、dev requirements 或一般測試 workflow；本分支的 merge base 正是該 commit，且 `requirement.txt`／`build-release.yml` 未偏離上游。這表示目前沒有既成工具衝突，但不能假設實作 Phase 1 時仍相同。

- Phase 1 開始前先確認 `upstream` remote 精確指向 `https://github.com/bouob/tickets_hunter.git`；每個 Phase 開始前執行 `git fetch upstream` 並記錄 merge base。重疊檔案必須由 `BASE..HEAD` 的本地集合與 `BASE..upstream/main` 的上游集合取交集，不能只看 `upstream/main...HEAD` 的單邊 diff。整合方式依當時 branch policy 選擇 merge 或 rebase，但不得用整檔 `ours`／`theirs` 掩蓋語意差異。
- 暫不為 pytest 或其他工具新增 `pyproject.toml`。目前專案並非以它管理 package／build，若上游日後引入自己的 `pyproject.toml`，同檔工具設定會成為高衝突熱點；Phase 1 使用 `pytest.ini`，日後 Ruff 另案使用獨立 `ruff.toml`。
- `requirements-dev.txt` 可用 `-r requirement.txt` 承接 runtime 版本，但不得複製或覆寫 runtime dependency；pytest、coverage 等工具也不得加入 `requirement.txt`。如此上游更新執行依賴時，開發環境會自動跟隨，而 PyInstaller 不會多打包測試工具。
- 新測試 workflow 使用獨立 `.github/workflows/tests.yml`，不修改既有 `build-release.yml`。同步上游時不只檢查同名檔案衝突，也要檢查上游是否已新增等價 CI；若功能重複，以一份 workflow 為準，不讓兩套測試重複執行。
- 若上游先導入 pytest、Ruff 或相同 dev dependency，優先沿用上游已驗證的版本與設定，再只疊加本 fork 必要的 marker、路徑及 uTiki 範圍；移除重複的本地設定，不維護兩套真實來源。
- 工具 bootstrap、測試搬檔／轉換及 production 重構必須分開 commit。依賴升級也使用獨立 commit，讓同步上游時能單獨丟棄、重做或 cherry-pick，不把工具衝突和購票行為混在一起。
- 不為 pytest 全面更名、搬移或格式化既有測試。`test_ctbc_selection.py` 若需拆分，先以純機械搬移的獨立 commit 保留 git 歷史與案例語意，再個別轉換純函式測試；這能縮小上游同時修改該檔時的人工 reconciliation 範圍。
- 本計劃不建立 lockfile，也不遷移 uv。測試工具採精確版本並由獨立維護 commit 更新，避免將上游 runtime 安裝與本 fork 的工具選擇綁成同一個大型衝突。

## 6. 不可退讓的安全不變量

### 6.1 URL 與路由

- 只接受 HTTPS、已知 tenant 的 exact hostname 與明確路由。
- 重新導向後重新驗證；credentials、fragment、未知特殊 query 一律拒絕快速導頁。
- date／area action parser 只能產生 allowlisted path 與唯一必要 query。
- fast direct navigation 只接受可唯一解析的 structured `Send`；`SendA` 只走網站原生流程。
- area／map fetch 必須零重新導向且 response URL 完全相同，並限制 content type、大小、候選數、map exact host/path、唯一版本 query 及 special mode；不確定就回到原生頁面流程。
- 程式自動 native click 與 fast navigation 都必須先安裝有期限的 `PendingNavigation`。`FAST` 同時留下帶 nonce／source／destination／expiry 的 browser-session provenance marker，目的頁需核對 route/query 及 hidden product/performance/group/price-area identifiers；marker 表明 FAST 但 Python pending 缺失、過期或不符時回到原生 area並 compare-and-delete marker。成功 attest 與 expiry 也必須清除 exact nonce；有效 `NATIVE` pending 優先於 stale FAST marker。`NATIVE` 依原生 commit 的 route／identifier contract consume；完全沒有 pending 與有效 FAST marker 的頁面只可走獨立的手動／既有頁面 contract，不能假裝 fast attestation 已通過，也不能因此把正常手動抵達一律送回 area。

### 6.2 可售與選擇

- table row status、map title status 與 `Send`／`SendA` action status 是三個獨立且短暫的可售值；數字可不同，禁止互相比等或以其中一個覆寫另一個。
- 三個來源都存在、都能依 `ticket_number` 判定足夠時才可選；缺失或未知代表本次 snapshot 不可選，但不得永久記成售罄。只有明列的可售文字 token 可視為 available，其餘 fail closed。
- area／price／rel 與 route／performance／group／price-area 等 stable identity 必須一致；stable identity 與 volatile availability 不可混為同一比較。
- 候選需要穩定身分；空標籤、重複身分或歧義結果 fail closed。
- 保留既有關鍵字、排除、fallback、由上到下／由下到上／random 語意。
- commit 前重新驗證目標仍屬同一 document 且仍可售。

### 6.3 填值與提交

- 票數、座位、checkbox 與 CAPTCHA 都在 DOM 回讀 audit 後才可送出，且登入／OCR 等等待完成後要在 submit 的同一原子 commit 再驗證一次。
- reserved seat audit 必須同時確認 visual selected count、`window.seats` payload 與票種 counter 都恰為 `ticket_number`、屬同一目標票種、沒有額外或混合座位，並保留相鄰設定的 physical adjacency 語意。
- general／server-assigned quantity audit 必須確認唯一目標 row/key 恰為 `ticket_number`，其餘 amount 全為 0。
- 重複 `id="AMOUNT"` 等不合法但真實存在的 DOM 必須以 row／container 身分處理，不能假設 id 唯一。
- `SubmitPlan` 帶 document token 與非敏感 payload fingerprint；本機 submit guard 防止同一 document 重複提交。它不是伺服器端冪等保證，也不得保存原始 CAPTCHA 答案。
- audit、登入、CAPTCHA 或頁面身分不確定時禁止 submit。

### 6.4 彈窗恢復

先保留目前已測行為的分類優先序：auth → CAPTCHA → seat conflict → quota → success → rejected。若要調整必須另開 bug Issue，而不能混入搬檔；無法可靠分類的警告一律走 fail-closed rejected／unknown。

- 以正規化後的可見文字與頁型判斷，不只看「警告」標題。
- 每個 tick 的其他副作用前先處理 visible dialog；submit commit 前再檢查一次。committer 最多只能回報 `DISPATCHED`，不能把 click 無例外當成伺服器成功；之後進入 `COMMITTED_AWAITING_RESULT`，允許導頁或延遲數個 tick 才出現結果，不得立即重送。
- 若在明確、有限的 outcome observation deadline 內仍沒有可驗證的導頁、network/document evidence 或已知 dialog，轉為 sticky `SUBMIT_OUTCOME_UNKNOWN`，交還使用者且不得自動重送。
- 拒絕／success blocker 必須先持久化，再嘗試 dismiss；dismiss 後驗證彈窗已關閉。dismiss 失敗時仍禁止 click、reload 或 submit。
- CAPTCHA 錯誤：清除答案／提交狀態，依頁面能力更新圖片，不重複送舊答案。
- 超出額度：標記 sticky quota block，停止自動重試並交還使用者。
- 成功加入購物車：阻擋同頁再次送出，等待 checkout 導頁／handoff。
- 座位衝突交給 page-kind reducer：reserved seat 清除 seats、CAPTCHA 與 submit guard；general／server-assigned 清除 quantity、CAPTCHA 與 submit guard。兩者都透過刷新協調器等待使用者設定間隔，且只在新 document 後解除 conflict。
- 未知警告：不得自動假設可重試；保留訊息並交還使用者。

提交狀態至少表達：

```text
IDLE -> COMMITTED_AWAITING_RESULT
     -> NAVIGATED / SUCCESS_BLOCKED / CAPTCHA_RETRY / AUTH_BLOCKED
     -> CONFLICT_RELOAD / QUOTA_BLOCKED / UNKNOWN_BLOCKED / SUBMIT_OUTCOME_UNKNOWN
```

### 6.5 刷新與 429

- 所有 reload 都由 `refresh.py` 進入既有 `refresh_coordinator`。
- 正常刷新使用使用者設定；不得硬寫 1 秒或測試時偷偷縮短。
- 429 使用共享 cooldown、單一 recovery probe 與 emergency marker；未知 document 不等於 429。
- refresh request 必須有 reservation、commit 與 request token，避免 tick／程序競速。
- `tab.reload()` 後要等待可辨識的新 document，不能立即讀取舊 DOM。
- pause 發生在 queued／reserved 時要釋放 reservation／probe lease，但保留 logical recovery intent；resume 不得清除 cooldown、跳過剩餘設定間隔或形成 refresh burst。
- same-URL reload 必須保留可供外層主迴圈重新執行 Cloudflare／document gate 的 reload event；coordinator disabled 時也只能由 family refresh controller 依本地設定間隔刷新。

### 6.6 Checkout 與秘密

- checkout 只通知且停止自動購票副作用，不執行付款。
- Cookie 只從使用者設定注入允許的 tenant；不得寫入記錄、fixture 或 issue。
- auth fingerprint 不含可逆的秘密內容。

## 7. 電腦選位功能的保留設計

Issue #3 已記錄的觀察先作為 Phase 4 輸入，不在舊 inline 流程直接加入設定：

- area page 有網站原生電腦選位控制項 `#byServer` 與手動選位控制項 `#byhend`；它們只切換模式，不是最終導頁目標。
- 「電腦配位接受不連號」checkbox 為 `#ATYPE_CB`；需求是保持未勾選，讓網站優先配置連號／較前位置。
- 進入 quantity page 的路由已觀察到 `UTK0201_001` 與 `ATYPE=N`。
- quantity page 要支援同名／重複 `AMOUNT` 控制項，以所屬票種 row 判斷。
- 此模式仍以既有區域關鍵字與可售規則選中目標 `tr.saleTr`；只停用 date-page fast direct shortcut 與手動 seat-map commit，並共用票種、票數 audit、CAPTCHA、送出及 dialog recovery。

預定流程：

```text
AREA
  -> 依 area_keyword / exclude / fallback 選出唯一可售區域列
  -> 驗證 tenant 支援 server assignment，且可見 mode controls 唯一
  -> 點擊 #byServer，回讀 #byServer.active 與 #byhend 非 active
  -> 確認 #ATYPE_CB 未勾選（必要時點擊並回讀 false）
  -> commit 前重驗區域列、模式、checkbox 與 document
  -> 原子點擊目標區域列，讓網站 Send() 導頁
  -> 驗證 exact UTK0201_001、ATYPE=N 與 expected identifiers
  -> 依票種 row 設定數量並 audit
  -> CAPTCHA / submit / dialog recovery
```

若 tenant capability 不支援、mode control 缺少／重複／disabled、active 狀態不明、目標列 stale、點擊後未導頁或落到未知 PageKind，必須 fail closed；不能退回手動選區後自行猜一區。quantity reader 必須只取可見 `#CHK`，不可誤抓 hidden `#MASTER_CHK`。

尚待實機再次確認：控制項是否由 framework re-render、完整 URL query、不同 tenant 的 DOM 差異，以及座位衝突在此模式的返回頁型。

## 8. 分階段執行計劃

每個 Phase 使用獨立 Issue／commit 群組；驗收未過不得進下一階段。若同一 Phase 過大，依頁型再拆小 PR，但不得跨越其 gate。

### Phase 0：治理、基線與計劃

**內容**

- 以 `AGENTS.md` 作為代理權威入口，`CLAUDE.md` 保留相容指標。
- 修正測試執行指南，新增工程審查指南。
- 建立本計劃與 Issue #3 的功能記錄。
- 不修改 `src/` 或 `tests/` 行為。

**驗收**

- 文件連結與格式通過。
- 命令、測試目錄及架構描述符合目前 repository。
- 維護者審閱並明確核准本計劃。

**回退**

- 純文件變更，可獨立還原，不影響執行期。

### Phase 1：凍結既有行為

**內容**

- 確認 `upstream` 指向原作者 repository，fetch／audit 最新 `upstream/main`，並建立本 Phase 的雙邊重疊檔案清單；先處理上游已存在的測試工具或 CI，避免建立重複設定。若確實需要 merge／rebase，使用獨立 `chore(sync)` commit 與乾淨 worktree，不把同步混入 Phase 1 的 production-zero diff。
- 新增精確固定版本的 `requirements-dev.txt`，引用 `requirement.txt` 後只加入 pytest 與 pytest-cov；不修改 runtime `requirement.txt`。
- 新增最小 `pytest.ini`，設定 `testpaths`、strict markers，並讓 `browser`／`live` 預設不執行。
- 先讓 pytest 原樣收集全部現有 `TestCase`、`IsolatedAsyncioTestCase` 與 Tornado `AsyncHTTPTestCase`；不先加入 `pytest-asyncio`。
- 新增一般測試 workflow，在 Python 3.11 的 Ubuntu 與 Windows 執行預設 pytest suite；不得開啟真實售票網站或使用者瀏覽器 profile。
- 依關注點機械拆分 `test_ctbc_selection.py`，但不改 production 行為或測試語法；搬移使用獨立 commit。
- 不在 Phase 1 批次改寫測試語法。後續修改純 parser／selection／audit 測試時，才可個別改成 pytest function、fixture 與 `parametrize`；async、Tornado、multiprocessing 測試沒有明確收益時維持 unittest class。
- 建立完整 config builder 與 per-test state sandbox；semantic fake port 等 Phase 3 有 production seam 後再加入。
- 補齊 manual reserved seat、general admission quantity、dialog recovery 的 characterization tests。
- 保存最小、脫敏的 date／area／seat／quantity／dialog DOM fixtures。
- 用最小 spike 決定 DOM fixture runner、目錄、skip 規則與精確命令；優先沿用 zendriver 與現有 Chrome，不先加入新套件。瀏覽器測試預設不得在一般 unittest 中無提示啟動。
- 列出「已知缺陷，不凍結為目標行為」：畸形 URL 接受、fast pending 安裝時序缺口、既有 general-quantity conflict dead-end，以及 reserved／general submit 無結果後可能重送。修正時各自連結 bug Issue 與 target test；尚未存在的 server-assigned flow 留在 Phase 4。

**驗收**

- 計劃初稿的 unittest baseline 為全套 143 個、其中 CTBC 82 個；Issue #2 安全修正完成後，Phase 1 的起始 baseline 更新為全套 155 個、其中 CTBC 94 個。導入當下 unittest 與 pytest 都必須收集並通過相同既有案例，數量差異逐一說明。
- pytest-cov 能產生 baseline 報告，但不設定全庫失敗百分比。
- Ubuntu／Windows workflow 通過，且確認預設 suite 沒有 browser/live 副作用。
- 新增 characterization tests 全部通過；155／94 是 Phase 1 起始 baseline，不是永久數量門檻。
- 測試拆分前後的 case 數與行為基準可核對。
- production diff 為零。
- `requirement.txt` 與既有 `build-release.yml` diff 為零；測試工具設定可獨立移除而不影響 production 執行或打包。
- 已完成與當時 `upstream/main` 的重疊檔案及等價 workflow 檢查，沒有重複的 pytest／CI 真實來源。
- fixture 不含 Cookie、token、帳號或訂單識別資料。
- 測試拆檔、runner 或命令變更已同步更新測試執行指南。
- Phase 1 結束後，`python -m pytest` 與 `tests.yml` 是所有後續 Phase 的完整自動測試 gate；`unittest discover` 只保留一個 Phase 的過渡對照。日後出現 pytest function 而造成 unittest collection 減少時，必須記錄預期差異，不能再以 unittest 單獨通過宣稱全套通過。

**回退**

- 測試檔可逐一還原；不需動 production。

### Phase 1.5：獨立修正已知安全缺陷

**內容**

- 在 package 搬移前，將會改變行為的安全收緊分成獨立 Issue／commit：畸形 URL（HTTP、credentials、未知 path）、fast pending／provenance 時序、既有 general-quantity conflict dead-end，以及 reserved／general submit 的 `DISPATCHED → 有限觀察 → sticky SUBMIT_OUTCOME_UNKNOWN`。尚未存在的 server-assigned route 不在本 Phase 偷渡建立。
- 每項先建立可重現 target test，再做最小修正並更新 characterization baseline；不得和檔案搬移、改名或格式化混在同一 commit。
- 已經由較早 bugfix 修正且有回歸測試的項目，只記錄對應 commit／Issue，不重寫一次。

**驗收**

- 每項修正的聚焦 pytest 與完整 pytest gate 通過，且 commit 可獨立 revert。
- URL tightening 的合法 corpus 不回歸；FAST provenance、正常 native／手動抵達使用相反案例證明不會 false accept 或 false reject。provenance 至少覆蓋 lost FAST → return → native retry、expired marker，以及 stale FAST marker + valid NATIVE pending。
- reserved／general submit 超時後只進 sticky unknown，不再次 click；general conflict 依使用者間隔 reload 並等待新 document。
- 行為修正完成後建立新的 extraction parity baseline，Phase 2 只對該 baseline 搬移。

**回退**

- 各 bugfix 依 Issue 獨立 revert；不需要回退測試工具或其他安全修正。

### Phase 2：建立 family package 與相容 facade

**內容**

- 在 package code 首次落地的同一變更先更新 `code-boundaries.md`：同一已驗證 family package 內允許單向 import，跨 family 仍禁止，並定義 package 的狀態歸屬與函式大小上限。
- 建立最小 `platforms/utiki/` package 與 `TenantProfile`／`PageKind`。
- 將 hostname、route classification、page type 搬到 `profiles.py`。
- 以小型 `auth.py` 隔離 tenant-specific Cookie scope、credential source 與 login capability。
- `platforms/utiki/__init__.py` 匯出主迴圈目前需要的 API。
- `platforms/ctbc.py` 暫時 re-export／delegate，主入口與測試可漸進遷移。
- 實作 `ctbc` 非敏感行為設定到 `utiki` 的冪等遷移、新值優先，以及相容期內 new-to-legacy 的單向鏡射；秘密鍵維持 tenant-specific 且不複製。
- 更新 PyInstaller hidden imports 與架構文件。

**驗收**

- 對已支援的合法 HTTPS CTBC／味全 corpus，新舊 route decision 完全一致。
- 未知 host、HTTP、credentials 與未知 path 已由 Phase 1.5 的獨立安全 commit 建立 baseline；本 Phase 不再順便改變其結果。
- default、old-to-new 遷移、新值優先、重複遷移、UI 儲存回讀、new-to-legacy 鏡射及舊版 rollback 讀值測試通過，且秘密未被複製。
- 完整 pytest、過渡期 unittest 對照與本地 PyInstaller 建置通過。
- 真實站點流程尚未改變。

**回退**

- 以本 Phase 的獨立 commit revert 回到原本單檔；不建立第二套長期 production handler。

### Phase 3：建立純決策與 DOM seam

**內容**

- 優先將 area／quantity parser、candidate selection 與 audit 移至 `selection.py`；其餘頁型隨 Phase 5 需要搬移。
- 先建立 area、quantity 與 dialog 的 snapshot reader，再按頁型擴充。
- 建立具名 committer；每次 commit 都做 URL／document／identity revalidation。
- 建立和 production seam 相連的 semantic fake port，驗證 inspect → decide → commit 的語意與順序。
- 本 Phase 不建立第二套 state／dialog／refresh engine。新 area／quantity seam 暫時透過薄 adapter 呼叫既有、已測的 submit dialog 與 refresh 路徑；Phase 6 再一次完整搬移其責任。
- 對外 private import 如仍被測試使用，可暫留薄 wrapper；每個頁型在同一 PR 完成替換並刪除舊 committer。shadow 只比較 pure decision，絕不保留雙重副作用。

**驗收**

- 已驗證 known-good fixture 上新舊 decision 相同；已知缺陷的差異必須連結核准的 bug Issue 與 target test。
- production reader 能在真實 DOM fixture 執行，不靠字串 marker fake。
- reader 無副作用；committer 有 stale／rejected 測試。
- semantic fake 必須斷言一般 commit 與 submit dispatch 是不同結果；`SubmitDispatchResult.DISPATCHED` 不得被 reducer 當成成功。
- availability fixture 至少覆蓋 row=40／title=45／action=45 可選，以及三來源逐一為 0、售完、少於 `ticket_number` 或未知時拒絕；commit-time live status 仍逐來源判斷。
- navigation 測試覆蓋 NATIVE／FAST pending 安裝、FAST browser-session marker、browser navigation commit、Python reduce 三個邊界的 cancellation／exception，以及 destination hidden-ID attestation；另有正常 native／手動抵達、lost FAST → return → native retry、expired marker、stale FAST + valid NATIVE 等案例，並驗證 marker 的 nonce compare-and-delete。
- 50 ms 重複 tick 測試證明每個 intent 最多 commit 一次。
- **合併 gate**：pure、contract、DOM fixture、完整 pytest 及 PyInstaller build 通過即可繼續下一個本地 Phase。
- **release gate**：若新 seam 已接管 production 副作用，CTBC 與味全的受影響 area／quantity 頁型必須實機 smoke；無可售活動時可合併與繼續後續本地重構，但不得發布。若本 Phase 僅 shadow pure decision、沒有接管副作用，需在交付記錄中明示，release gate 延至真正切換的 Phase。

**回退**

- 每個頁型使用獨立 commit；以 commit revert 回退，不以未定義 feature flag 維護雙軌 committer。

### Phase 4：實作網站原生電腦選位

**前置條件**

- Phase 3 的 area／quantity seam 已完成。
- Issue #3 尚待確認的 DOM／路由已重新實機觀察。
- 目前 dialog recovery 的 characterization tests 已能保護 delayed dialog、success、quota 與 conflict；不必等待整個 runtime 重構。

**內容**

- 新增 `utiki.seat_selection_mode` 設定與 UI，預設 `manual`。
- `manual` 完全沿用既有流程。
- `computer` 依第 7 節，以同一個 area planner 選目標列，再切模式、audit 並 commit 該列。
- server-assigned quantity 先透過薄 adapter 使用既有 dialog／refresh 實作，只增加該 PageKind 必要的 state 清理與 conflict recovery；不得另建第二套 reducer。Phase 6 以行為不變的搬移方式統一 ownership。
- tenant profile 必須明示 `supports_server_assignment`；未驗證 tenant fail closed。
- 停用不適用的 fast direct shortcut與手動 seat-map commit；共用 quantity audit、CAPTCHA、submit 及 route-specific dialog recovery。

**驗收**

- 舊設定與 `manual` 行為無差異。
- `area_keyword`、exclude 與 fallback 仍決定目標區域；不會只切模式後由網站任選區域。
- `#byServer`／`#byhend` 可見且唯一，active 狀態與 `#ATYPE_CB === false` 經回讀確認。
- 目標區域列在 commit 前未 stale；目的頁為 exact `UTK0201_001`、`ATYPE=N` 與 expected identifiers。
- quantity 精確設定後才送出，且不誤用 hidden `#MASTER_CHK`。
- server-assigned conflict 依使用者設定間隔恢復；額度、success 與未知結果不會重送。
- click 回報 dispatched 但沒有導頁或 dialog 時，有限等待後進入 `SUBMIT_OUTCOME_UNKNOWN`，不自動重送。
- server-assigned conflict 在本 Phase 依 Phase 1.5 已驗證的 general conflict contract 接入；不得回頭在 Phase 1.5 偷渡尚不存在的 route。
- 不到付款階段執行任何自動付款。
- **合併 gate**：功能預設 `manual`、capability 預設關閉，完整 pytest、DOM fixture、設定遷移與 PyInstaller build 通過即可繼續後續本地 Phase。
- **啟用／release gate**：`computer` 只有在 CTBC 實機完成 area keyword → mode audit → `UTK0201_001` → quantity audit → CAPTCHA／submit gate smoke 後才能對使用者啟用；無活動時程式可以合併，但 capability 保持關閉且 release blocked。其他 tenant 仍各自需要證據，不因 CTBC 通過而自動啟用。

**回退**

- 設定預設保持 `manual`；revert 本功能 commit 即可移除 `computer`，不影響 area／quantity seam。

### Phase 5：逐頁遷移其餘 workflow

依風險由低到高遷移：

1. date selection。
2. general admission 的其餘 orchestration。
3. reserved seat selection。
4. CAPTCHA 與 submit gate。

**內容**

- 每頁改為 observe → plan → commit → reduce。
- 在每個頁型的獨立 PR 移除已被替代的 inline reader／committer；只保留必要的公開名稱 facade。
- 保持 12 階段函式入口與外部行為。

**每頁驗收**

- pure、contract 與 DOM fixture 測試通過。
- commit-time DOM mutation／URL change 能安全拒絕。
- 重複 tick 不重複副作用。
- **合併 gate**：pure、contract、DOM fixture、回歸套件與未驗證項目記錄均通過。
- **release gate**：CTBC 與味全對其實際支援的受影響頁型都完成實機煙霧測試。沒有可售活動時可先合併不改語意的重構，但 release 保持 blocked；只有能以兩站 fixture parity 證明變更完全不碰 tenant-specific contract 時，才可由維護者記錄例外。
- 實機刷新沿用設定值，測試不改成更快頻率。

**回退**

- 各頁型以獨立 commit revert；不長期保留兩套 production handler。

### Phase 6：狀態、dialog 與刷新收斂

**內容**

- 將 legacy `_state` callers 一次收斂成 per-tab runtime、document token 與 transition reducer；Phase 3／4 的薄 adapter 只搬移 ownership，不再建立另一套平行狀態機。
- 將 dialog classifier 保持純函式，recovery 由 workflow 產生 effect。
- 將所有 uTiki reload 路徑集中到 `refresh.py`，保留現有跨程序線性化語意。
- 定義設定熱重載、離站、tab 關閉、checkout 的清理規則。

**驗收**

- 多分頁不共享 purchase intent。
- success、delayed dialog、dismiss failure、CAPTCHA、quota、auth、reserved-seat conflict、general conflict、server-assigned conflict、unknown dialog 各有 transition 測試。
- conflict：先保存 blocker → dismiss → 依 PageKind 清對應狀態 → 設定間隔 → coordinated reload → 新 document → retry。
- quota 與 unknown 不會進入無限重試。
- pause／resume 在 queued、reserved、committed-awaiting-document 三階段都不會跳過間隔；same-URL reload 會通知外層 detector。
- 429 的 cooldown／single probe／emergency marker 回歸測試通過。
- 合併 gate 以自動測試為準；移除 legacy／發行前，CTBC 與味全各至少一條可用流程 smoke 必須通過。無可售活動時明確保留 release blocker，不以單元測試取代。

**回退**

- 本 Phase 以獨立 commit 群組實作；rollback 採 revert，保留公開 facade，但不保留第二套 dialog／refresh engine。

### Optional Track F：候選 tenant 驗證與接入

富邦只在以下證據齊全後建立 profile：

- 官方 hostname 與 HTTPS route。
- date／area／seat 或 quantity 的真實 DOM fixture。
- 登入與 Cookie scope。
- dialog 文案與 recovery。
- 429／刷新範圍。
- checkout 邊界。

**驗收**

- tenant contract tests 通過。
- 至少完成登入、選擇流程與 checkout 邊界 smoke。
- 差異若超過 profile 能表達的範圍，建立 tenant adapter；不在共用 core 散落 hostname 判斷。

**回退**

- 移除 profile 即停用該 tenant，不影響 CTBC／味全。

此 track 可跳過，不阻擋 Phase 8、CTBC／味全重構完成或既有 tenant 發布。只有 hostname、帳號／Cookie、活動與頁面證據齊全時才另開 Issue 執行；缺少實機窗口不算核心重構 blocker。

### Phase 8：移除 legacy

**前置條件**

- 新 package 已經過至少一個 release 週期。
- CTBC 與味全核心流程實機驗證完成。
- 沒有仍依賴 `platforms.ctbc` private helper 的 production 呼叫端。

**內容**

- 移除舊公開 wrapper 與 script-string assertions；不移除仍需支援跳版使用者的冪等設定 migrator。
- 結束執行期對 `ctbc` 行為鍵的 fallback 與 save path 的 legacy 鏡射，並發布 release note。
- 移除文件中的 legacy 說明，更新 `structure.md`、使用者指南與打包文件。

**驗收**

- repository 搜尋沒有過期公開符號／設定讀取。
- 完整 pytest、本地建置與 release platforms CI 通過；若仍保留 unittest class，可另跑對照，但不能取代 pytest gate。
- 從早於相容 release 的既有設定檔仍可成功遷移，且秘密未被複製或輸出。

**回退**

- Phase 8 必須獨立 commit；release 發現問題可完整 revert，恢復一個版本的 facade。

## 9. 測試策略

### 9.1 測試分層

| 層級 | 內容 | 目的 |
|------|------|------|
| 純函式 | profile、route、parser、selection、audit、dialog transition | 快速、完整覆蓋決策 |
| 契約 | semantic fake port、呼叫順序、commit 結果、重入 | 驗證 orchestration，不解析 JS 字串 |
| DOM fixture | production reader／committer 在真實 DOM 環境執行 | 驗證 selector 與 re-render/race |
| 整合流程 | manual、general quantity、conflict recovery | 驗證跨頁狀態與 effect |
| 實機 smoke | CTBC／味全及後續 tenant | 驗證網站現況與瀏覽器行為 |

最小整合矩陣：

1. manual reserved seat：date → area → seat → CAPTCHA／submit gate。
2. general quantity：area → quantity → audit → submit gate → conflict recovery。
3. server-assigned quantity：area keyword → mode audit → area commit → quantity → submit gate → conflict recovery。
4. submit outcomes：立即／延遲 success、CAPTCHA、auth、quota、unknown、dismiss failure 與 dispatched-no-result timeout 均不重送。
5. refresh lifecycle：pause／resume、same-URL reload、429 cooldown 與 single probe。

### 9.2 Fixture 最小集合

- date 一份。
- area 一份，包含 `Send`、`SendA` 與可售來源不同步。
- reserved seat 一份。
- quantity 一份，保留重複 `AMOUNT` 結構。
- dialog 一份，可在測試中替換文字、visibility 與顯示延遲；至少涵蓋「該位置已被選走」「位置已被預訂」及成功結果。
- normal／429 root 各一份最小快照。

不為每個文案建立近似 HTML；以資料驅動與 DOM mutation 覆蓋分支。

### 9.3 驗證陳述

每個 Phase 的交付必須列出：

- 實際執行的命令、test count 與結果。
- 實機的 tenant、頁型、設定模式與刷新間隔。
- 未取得的活動／頁面／彈窗證據。
- 是否執行 PyInstaller build 與 GitHub release CI。

詳細命令依 [測試執行指南](testing_execution_guide.md)。

## 10. 相容與發布策略

- 每個 Phase 開始前先 fetch／審視 `upstream/main`；fetch／audit 不等於自動 merge。若上游修改同一頁型、設定或測試，先用獨立 `chore(sync)` 整合並把上游新行為納入 characterization baseline，再繼續搬移，不能以本計劃建立時的快照覆蓋新修正。
- 每個 Phase 的交付需附上 upstream merge base、重疊檔案與解法；除了文字衝突，也要檢查不同檔名但功能重複的 dependency、CI 與設定。
- Phase 2 到 Phase 6 及 legacy 移除前，`nodriver_ctbc_main` 等既有公開名稱可由 facade delegate，避免一次改動主入口與全部測試。
- 新公開 API 優先使用 `nodriver_utiki_*`；內部 helper 不提供長期相容承諾。
- 設定遷移必須可重複執行，且新值優先；不得因每次啟動覆寫使用者新設定。
- 不在同一 release 同時加入新功能並移除舊設定／facade。
- 每個 Phase 都使用獨立 conventional commit；大型 Phase 依頁型拆 commit，確保可局部 revert。
- `structure.md` 只在 package 真正落地時更新，不提前把計劃寫成現況。

## 11. 風險與控制

| 風險 | 影響 | 控制方式 |
|------|------|----------|
| 重構同時改行為 | 無法判斷回歸來源 | characterization first；行為修正另開 Issue |
| 新舊 handler 雙重執行 | 重複點擊／送出 | shadow 只算 decision；唯一 committer |
| 測試綁 private helper | 搬檔造成大量假失敗 | 先建立公開 seam 與 semantic fake |
| DOM fixture 過期 | 自動測試綠但網站已變 | 實機 smoke 與 fixture 擷取日期 |
| tenant 過度泛化 | 某站差異污染共用 core | profile contract；必要時小型 adapter |
| 狀態遷移遺漏 | CAPTCHA／submit 卡死或重送 | per-tab/document transition tests |
| 刷新競速 | 429 或封鎖 | 單一 refresh controller、coordinator reservation |
| 設定遷移破壞舊使用者 | 升級或降版後行為改變 | safe default、長期冪等 migrator、相容期 new-to-legacy 鏡射、回讀測試 |
| package 未打入執行檔 | 開發環境正常、release 失敗 | Phase 2 起每次 import 變更做 PyInstaller build |
| dev 工具與上游更新衝突 | 同一依賴／CI 有兩份設定，或同步時誤蓋上游修正 | 工具檔獨立、runtime／release 零修改、每 Phase 做 upstream overlap gate；等價功能以上游設定為基底合併 |
| 大量搬檔放大上游衝突 | 測試與 production 難以逐段 reconciliation | 機械搬移、語法轉換、行為重構分 commit；禁止全庫格式化與一次性改寫測試 |
| 過度抽象 | 程式更多、更難除錯 | 限定 uTiki family；檔案按實際責任建立 |

## 12. 核准與停止點

### 現在的停止點

本文件完成後停止。維護者應先審查：

- `utiki` 命名是否接受。
- package 上限與依賴方向是否合適。
- 設定由 `ctbc` 遷移到 `utiki` 的相容策略。
- Phase 順序及每一階段的實機要求。
- 電腦選位在 area／quantity seam 後列為 Phase 4，優先級是否合適。
- pytest 採獨立設定檔／dev dependency、其他工具另案評估，以及 upstream-first reconciliation 的策略是否接受。

### 核准後的執行規則

1. 只先建立 Phase 1 的 Issue／branch 與詳細 checklist。
2. 每完成一個 Phase，依 [工程審查指南](engineering-review.md) 自審並由第二位審查者覆核。
3. 維護者一次核准本計劃後，代理可在本地連續執行後續 Phase；每一 Phase 的合併 gate 與第二位審查者覆核通過即可進下一 Phase，不需逐次等待人工回覆。
4. GitHub CI 只有在 branch 實際推送後才是可觀察 gate；未推送時以文件列出的本地 pytest／build gate 繼續，不能把「本機通過」寫成「CI 通過」。release gate 可以阻擋發布或 capability 啟用，但在功能仍 fail closed／disabled 時不阻擋後續本地重構。
5. 遇到 P0／P1、無法安全修正的 P2、上游語意衝突、新 production dependency、需要改變既定使用方式、實機證據推翻 route／DOM／recovery 假設，或測試／build gate 無法通過時才停止並請維護者決定。
6. Phase 4、Optional Track F 與最終 release smoke 需要真實站點窗口；頁面不可得時要保留明確 release blocker，不能偽稱完成，但可繼續不會啟用未驗證功能的本地工作。Optional Track F 可直接跳過。

## 13. 全案完成定義

- `platforms.utiki` 成為 CTBC／味全共用的唯一 production engine。
- `platforms.ctbc` legacy facade 已依相容期安全移除。
- 關鍵字、票種、座位、票數、CAPTCHA、submit、dialog、刷新與 checkout 不變量都有可重複測試。
- 測試不再依賴 JavaScript 字串 marker 模擬核心 DOM 行為。
- manual 與 computer 模式都有完整 contract、整合與實機驗證。
- 舊設定可遷移，新設定不會被舊值覆寫，秘密不會被複製或輸出。
- PyInstaller release 能收錄 package，Windows／macOS 建置通過。
- `AGENTS.md`、code boundaries、structure、testing、使用者指南與實作一致。
- 沒有自動付款、風控繞過或未驗證 tenant 被宣告支援。

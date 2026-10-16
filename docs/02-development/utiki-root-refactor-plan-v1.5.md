# uTiki 低延遲重構 v1.5：零歷史相容、完整狀態轉移與可交付升級

**專案：** `OooPeople/tickets_hunter`
**設計參考基準：** `f307f7caba054fb40aab807a4629c891e69fb200`
**文件日期：** 2026-10-16（Asia/Taipei）
**版本：** 1.5 — 依 Codex 對 v1.4 的複審進行四項窄幅驗收硬化
**狀態：** `PROPOSED / REVIEW REQUIRED`。本文件不是實作授權，也不是正確性、測試或發布已通過的證明。
**唯一修訂底稿：** `UTIKI_ROOT_REFACTOR_PLAN_v1.4.md`（原始位元組 SHA-256：`6d07b70b9eb91dedd06e2a5aca2081ab215a127587c31bf79d508c9c60b94827`）。本輪修訂依使用者提供的四項複審 finding；歷史附件僅供來源核對，不作替代底稿。
**替代關係：** 維護者正式採用後，本版全文取代 v1.1–v1.4 及舊清理摘要中衝突的規定；目前仍是提案，既有核准／授權不由本文件代填。v1.4 已補齊的核心契約完整保留。

> **模組清理：** 最終棒球 runtime 只有新架構；舊 API／alias、舊模型、舊 workflow、舊設定 fallback／mirror、舊測試 bridge 全部退場。不能把歷史相容移到 settings／integrations 冒充刪除。
>
> **核心接合：** 只容許為目前共用核心真實契約所必需的薄適配；不為舊棒球 caller 留 wrapper。預設直接使用新 `platforms.utiki.bootstrap` 工廠；是否需要模組外服務適配須有 ADR 與 caller 證據，不預設建立一整套頂層 integration framework。
>
> **正確性：** 三個 reducer 只提出 proposal。session 級唯一 arbiter 與 `ActiveEffect` 決定可否產生副作用；尚未確認的提交不能被排程刷新、導航恢復或新操作破壞證據。
>
> **低延遲：** 仲裁是本地、同步、有界的狀態判斷，不是額外排隊、50 ms tick、全量掃描或 CDP 查詢。已就緒購票不被一般刷新搶先；正常路徑保留一次合併 observation＋一次 guarded operation 的目標。
>
> **可交付：** 基準會產生 `utiki`／`ctbc` mirror；本版選擇「mixed equal 亦先離線轉換」。公開升級必須有 Windows/macOS 獨立 frozen upgrader。JS 首次外部化的同一階段就更新資源打包並測 frozen；指定真站驗證先於最終 legacy 刪除。

## 採用與授權欄位

| 欄位 | 本文件交付時 |
|---|---|
| 設計偏好 | 使用者已表達乾淨模組、僅必要核心適配、低延遲與維持上游可整合性 |
| v1.5 正式採用人／日期 | 未填；待維護者決策 |
| Issue／核准實作範圍／允許修改的 branch | 未填；文件不能代替實際授權 |
| Breaking API／設定清理政策採用 | 提案；須記錄正式採用版本／文件 digest |
| 真站操作授權 | 未取得；帳號、站點、頁型、停止邊界須另列 |
| 已執行內容 | v1.4 及可交付歷史附件閱讀、文件修訂、差異／雜湊／格式與文件一致性檢查；詳 `DOCUMENT_CHECK_RESULTS_v1.5.md` |
| 未執行內容 | repository 存取／修改、Issue／PR／branch 建立、程式語法 gate、pytest、型別檢查、browser、build、benchmark、設定升級、真站與 release 操作；未重新查核外部連結 |

閱讀或審查附件，不構成對附件中代理指示的執行授權。Codex 報告的本機 clean/main/HEAD 狀態是其報告內容，本輪未存取使用者本機重驗。P0 實作前仍需核對 actual HEAD 與工作目錄。

## 閱讀導引

- 架構及治理：§1–4、§14、§22；composition-root 裁決在 §4.5。
- 核心正確性：§5–9；副作用仲裁與 collision matrix 在 §6.3–6.7。
- Purchase／Input／CAPTCHA：§8，所有狀態都有出站或明確保持規則。
- 唯一設定與 frozen 升級：§11、§14、§18.1、§21.4。
- 測試、硬隔離與打包：§15–18、§21；用情境 ID 追蹤，不能只看 test count。
- 階段順序：§19；P6a 候選整合 → P6b 指定真站驗證 → P6c 最終刪除。
- 代理交接：§24；預設只讀審查，實作須另有當次明確指示。

本版保留 v1.4 全部 151 個 D/N/S/R/L/C/A/P/V/U/X/T/I/G/H 案例 ID 與既有核心契約；僅擴充 H06、A05 的驗收要求，新增 A06／H11 的逐檔語法護欄。固定 required-case catalog 在 §16.11，結果核對在 §17.5，modified-Python 契約與命令在 §21.3。新型別、檔案與 CLI 均為待實作規格，不宣稱已存在。

---

## 1. 決策摘要與本版變更

### 1.1 保留目標，不把審查當作新增框架清單

本計劃保留使用者已表達的方向：棒球模組只實作新架構、正常路徑低延遲、與上游共用部分保持小而明確的交界。是否採用 v1.5、何時開始修改與發布，由維護者另行核准；偏好陳述不是任意執行權。

| 編號 | 根因／目標 | 結構解法 | 可驗證結果 |
|---|---|---|---|
| F1 | workflow 混雜流程、DOM、JavaScript、輸入與恢復 | 純 domain、薄 application、具體 browser 邊界 | application 不直接操作 DOM／檔案或拼腳本 |
| F2 | 狀態可被分散修改 | engine-instance registry、單一 writer、具名不可變 state | adapter/task 只能回報結果 |
| F3 | production JS 未形成必要自動驗收 | 真瀏覽器、production scripts、不可假綠的 CI | guard／JS／資源損壞會失敗 |
| F4 | snapshot 位置 tuple | 具名 records、單一 codec | domain 不靠 seat[8] 解讀欄位 |
| F5 | 純 import 拉入瀏覽器 | 輕量 root、真正的新 bootstrap 工廠 | 無 browser/OCR 依賴可測純核心 |
| F6 | 新舊棒球 API／模型／設定共存 | caller 同步遷移、離線升級、最終刪除 | runtime 沒有歷史 fallback 或鏡射 |
| F7 | 測試依賴 private 字典／包裝 | typed contracts、事件序列、fixture | 不為舊測試保留 production alias |
| F8 | 規範無法防回歸 | import/type/resource/scenario gates | 缺失測試、違反 ownership 能被攔住 |
| F9 | 分層增加延遲 | 同 worker 立即續行、批次讀取／guarded commit、冷熱分離 | 無新增固定 sleep／無用途 CDP |
| F10 | 上游交界深改或假借核心相容留下舊模組 | ADR、current-core inventory、語意更新演練 | 只有有必要性的現行核心接合 |
| F11 | 三個 reducer 各自正確但效果競爭 | session 級 proposal 仲裁、唯一 ActiveEffect、collision matrix | unresolved submit 不被其他 reducer 破壞 |
| F12 | 購票恢復／人工處理出口不全 | 完整主狀態／InputState／retire/new-cycle 事件 | 沒有模糊的「等待之後回 READY」 |
| F13 | 升級與發布只寫意向 | mixed-equal 固定政策、frozen upgrader、P3 build、刪除前 live | 沒有 Python 的使用者能完成可回退升級 |

F11–F13 與原 F2/F3/F6 有關，列出是為對應 Codex finding，不要求為每項新增一層 manager。這些是規格要補齊的契約，不是已證明的 production bug 列表。[Q01]

### 1.2 採用的責任配置

Domain 處理資料、規則與 transition；application 的 session writer 歸納事件並仲裁 proposal；browser／service ports 實作外部能力；新 bootstrap 只組裝一次。Inputs 屬 purchase，不另建自主第四引擎。

current-core 必要適配只映射現行服務／控制契約，可位於模組外；domain/application 不反向依賴核心或適配。ADR-01 預設不建立 `integrations/` 家族框架，也不以「檔案移到模組外」證明比較乾淨。

### 1.3 與 Codex 建議的取捨

接受 session arbiter、完整轉移、明確升級、早期打包、刪除前 live、CI/type/isolation 與治理缺口。**不照抄一條通用 `navigation > refresh > new purchase` 優先序**：先做 eligibility；普通刷新必須讓位給已就緒購票，任何優先序不能搶佔未決外部效果。

不把「bootstrap 曾出現在舊文件」理解為 legacy。判斷依用途、資料與 caller：新工廠可以保留；舊函式群的轉送 facade 不可以。

### 1.4 乾淨的完成定義

完成指已知歷史架構根因與遺留清單有接替、刪除及驗收證據；不表示未來永無技術債。沒有必要為這次重構建立全平台 scheduler、actor framework、event bus、DI 容器或 event store。

---

### 1.5 v1.5 僅修四項驗收／證據問題

使用者提供的 Codex v1.4 複審摘要指出先前核心阻擋已補齊、未發現新的 P0/P1；這是複審摘要的陳述，不是本輪重新執行 repository 審查的結論。[Q03]

| Finding | 本版窄幅修訂 | 不改動的邊界 |
|---|---|---|
| P2 必要案例可隨測試一起消失 | 獨立固定 catalog、外部核准 digest、四輸入結果核對、H06 負向案例 | 不更動 session arbiter、購票／輸入／刷新演算法 |
| P2 語法檢查可能漏檔 | Git 變更集合交叉核對的 manifest＋逐檔 `py_compile`，A06／H11 | 不以 type 或 pytest import 代替語法檢查，不新增 runtime 檢查 |
| P3 來源不可重現 | 隨包附實際持有的 R01／清理規則原檔與 SHA-256；其他宣稱標明沿用與限制 | v1.4 是唯一底稿，不從缺失 v1.3 重新建構方案 |
| P3 文件品質 | 繁體中文（台灣用語）、移除行尾空白、實際文件檢查與完整 diff | 原始證據附件維持位元組不變；文件 PASS 不等於實作 PASS |

required catalog 與 modified-Python checker 都是 CI／開發期契約，不進入選票熱路徑；沒有新增大型框架、第二套引擎或額外 CDP 往返。

---

## 2. 基準、證據與治理前置條件

### 2.1 已有的設計基礎

固定基準已有 snapshot → plan → commit、per-tab runtime、提交未知結果阻擋、跨程序 refresh coordinator 與 browser fixtures。本次保留其已驗證契約，不把它們當成空白系統重寫。[R02–R11]

v1.3 已區分歷史相容與 current-core 適配，但未明訂 session arbiter、部分 PurchaseState 出站、mixed-equal 唯一路徑與完整 frozen upgrader。本版處理的是**執行規格缺口**；Codex 的 P1/P2 分類不能自動轉述成現有 repository 已重現的 P1/P2 bug。[Q01]

### 2.2 v1.4 證據紀錄與本輪重讀界線

| v1.4 記錄的來源（本輪未重新取得下表原始資料） | 原記錄所支持的內容 | 不能推出的結論 |
|---|---|---|
| v1.3 §6–8 | 有單一 writer／互斥原則；缺跨 reducer 可執行仲裁矩陣，Purchase 表偏向進站 | 不能證明當前 production 一定已發生 duplicate submit |
| v1.3 §11/14/21 | 唯一格式政策，但 mixed equal／可選 converter 不夠明確；只有 script CLI | 不能證明 frozen 使用者已有可用升級工具 |
| `settings.migrate_config` [R07] | 同時補值並鏡射 `utiki` 與 `ctbc` 行為 | 不能假設所有使用者實際檔案內容相同 |
| `AGENTS.md` [R12] | 高風險變更要核准；PR 有 Issue 流程；輸入需使用現行 native setter 並檢查結果 | 不能因附件寫了實作指示就取得修改權 |
| `engineering-review.md` §6 [R18] | 指定實機站點／流程驗證先於最後移除 legacy | 不能以修改規範的方式默默消除此 gate |
| `code-boundaries.md` [R13] | 平台業務邏輯屬 platforms；現有 dependency/ownership 另須明示變更 | 不是禁止一切 factory 或必要邊界適配 |
| `build-release.yml` [R19] | 正式管線建 bot/settings，含 Windows 及 macOS 分支 | 不代表本輪已 build；不代表 upgrader 已存在 |
| 官方 pytest/PyInstaller/mypy/Docker 文件 [E04/E06/E11–E13] | 命令覆寫、資料打包、型別範圍與隔離工具的語意 | 不代表目標 OS 或本專案固定版本已實測 |

上表是 v1.4 既有查核紀錄的保留，不是 v1.5 重新查核原始碼或官方網站的結果。v1.5 本輪確實全文讀取唯一底稿 v1.4、現存的前次審查 R01 與 `UTIKI_CLEAN_MODULE_RULES_v1.3.md`；三份原檔均附於 review bundle 的 `sources/`，SHA-256 見附錄 D。

`UTIKI_ROOT_REFACTOR_PLAN_v1.3.md` 及 Codex v1.3 的完整貼文／子代理報告本輪未取得，相關內容只依 v1.4／前次對話的先前產出陳述，未在本輪獨立重讀。R02–R20、E01–E13 只保留原連結與原用途，本輪未存取、未封存其原始位元組，不宣稱連結有效、內容未變或已重新驗證。資料可交付與事實已獨立查證是不同狀態；完整對照見 `SOURCE_MANIFEST_v1.5.json`。

### 2.3 實作前必須記錄的採用決策

若維護者採用本版政策，P0 需建立 `adoption.md`：計劃版本/digest、維護者、日期、Issue、允許變更的 repository/branch/範圍、breaking API/config 決策、core-service 例外、review owner。

文件核准、程式修改、遠端 Issue/PR 建立、真站操作、發布是不同授權範圍。沒有當次實作授權就只讀審查，不因計劃附有 CLI 而直接執行。

需要正式調整的既有規範：module `_state` 改為 engine-instance owner、純配置依賴、必要 native-setter 資源注入、候選 worktree 的驗證／合併單位。只修改受影響條款，不刪除錯誤防護規則來通過審查。

保留「指定 live smoke 在 final legacy deletion 之前」。若想另改此門檻，必須另外提出範圍、理由與核准；本版不預先核准例外。

### 2.4 可合併階段與候選驗證

已修改 production 依賴的中間階段必須可編譯、測試、打包、回退。P4/P5 的新引擎尚未接 production 時，可在隔離 harness 測試，不能標為 production 已切換。

正式 cutover 使用最小不可拆的 ownership／API 組合；先在 review worktree 建立候選做 smoke，再核准最後刪除。開發中未被正式入口載入的舊檔可暫存於分支，最後交付前移除；不得打包兩套 fallback engine 或同時操作同一 tab。

---

## 3. 範圍、核心例外與不可破壞條件

### 3.1 必做與不做

本次處理 uTiki 家族的資料／選擇／輸入／購票／導覽／刷新、session 仲裁、瀏覽器契約、設定與升級、測試、打包及必要核心接合。其他售票平台的購票策略／等待不在本輪遷移範圍；測試或 caller 受影響的部分才作最小修改。

不重寫既有 SQLite transaction engine，不改 shared scope、不提高刷新頻率、不新增自動付款或驗證規避。不能全域覆寫 sleep/timeout 來冒充速度優化，也不因架構整理而重排全部上游檔案。

### 3.2 四類邊界

| 類型 | 例子 | 本版要求 |
|---|---|---|
| 舊棒球歷史相容 | ctbc facade、舊 API alias、old dict/decoder、ctbc 行為 fallback、舊測試 bridge | 最終全部刪除；自訂 caller 同步更新 |
| 新架構正式能力 | 新 bootstrap、browser port／codec／資源 loader、typed policy | 按必要性保留，不以名稱或出現年代誤判 legacy |
| 現行核心適配 | 暫停控制／coordinator／憑證／通知等真實共用接口映射 | 有獨立 caller/contract 證據才在模組外薄接合；不仿造舊棒球 |
| 真實外部契約 | CTBC 網域／Cookie 名稱／憑證鍵、`utk-sports` scope | 保留；改動另立需求，不作字串全面更名 |

正式 runtime 的禁止範圍包含 settings/load/hot reload 及 current-core bridge，不只掃 `platforms/utiki`。把歷史相容搬出去不算刪除。

### 3.3 修改範圍與核准

自訂棒球模組內可根本改造；原本為棒球加入主程式的 import／route／reload caller 跟著改成新接口。共用 helper/gate/settings/spec 改動分離提交，盤點所有 caller 與上游觸點，不用「主程式有人呼叫」替舊棒球 API 豁免。

是否採用 breaking change、允許改哪些分支、是否建立 Issue/PR、是否真站 smoke 分別記錄；見 §2。未取得當次實作授權時只讀審查。

### 3.4 必須保留的契約

| 契約 | 必須成立 |
|---|---|
| 選擇 | keyword 優先序／AND／排除／fallback、張數、排序、相鄰語意保持；真正行為修正獨立列出 |
| 身分 | tenant、document、operation、credential/session 邊界不混淆；新 URL 不自動解除舊 attempt |
| 副作用 | 單一 session grant；dispatch 不等於成功，unknown 不能自動重送或被旁路刷新 |
| 輸入 | 現行 native setter＋回讀／必要框架 audit；過期 OCR/reset 不授權新 submit |
| 刷新 | 既有 interval/cooldown/single-probe/coordinator transaction 保留；普通刷新不壓過 ready purchase |
| 暫停／關閉 | 先阻止新 claim；已發送事實仍處理，不能以取消宣稱 rollback |
| 成功／quota | 不自動新 cycle；需正式新任務／人工核對事件，不利用換帳號逃限制 |
| 資料 | 唯一格式、無 runtime 遷移；保護原值與其他平台設定；frozen 升級可操作 |
| 驗證 | 首次外部化資源同階段測 frozen；指定真站 smoke 先於最終刪除；證據與版本對應 |
| 速度 | ready 立即續行；新狀態模型不能要求多一輪 CDP、tick、queue hop 或全量 copy |

---

## 4. 最終架構與 composition-root ADR

### 4.1 預設選擇：新 bootstrap 工廠，不預設頂層 integration framework

保留家族內的純 domain、薄 application 與具體 browser adapters。**預設主程式呼叫新 `platforms.utiki.bootstrap.create_engine(...)` 工廠**，傳入 validated policy 與窄 ports。

這個 bootstrap 是新架構的正常工廠，不是舊 API facade，不接受／產生 legacy dict，不維持第二個 registry。核心相依的服務映射只有在真實接口不相符時，才由模組外一個小檔案或既有 composition 位置提供；不可照舊 API 每個名字生成 wrapper。

建議責任配置：

```text
src/
  nodriver_tixcraft.py              # 少量新接點；保留其他平台 route
  utiki_config.py                   # current 格式驗證，不解碼舊欄位
  keyword_rules.py                  # 唯一純文字規則來源
  refresh_coordinator.py            # 現行共用協議，原則不改 schema
  platforms/utiki/
    __init__.py                    # 輕量；無 runtime re-export
    bootstrap.py                   # 新工廠；只組裝，不代理舊 API
    profiles.py
    domain/
      identity.py
      snapshots.py
      plans.py
      effects.py                   # Proposal/Claim/Evidence/ArbitrationDecision
      arbitration.py              # 純的 eligibility 與 bounded priority 規則
      navigation.py
      purchase.py                 # 包含 input/captcha 子狀態的唯一聚合 owner
      inputs.py                   # 純 input transition helper，由 purchase 呼叫
      refresh.py
      auth.py
      selection/{date,area,seat,quantity}.py
    application/
      ports.py
      session.py                  # 單一 writer/ActiveEffect/TaskScope
      engine.py                   # 歸納 observation → 仲裁 → 執行
      pages.py
      navigation.py
      purchase.py
      inputs.py                   # 輸入/OCR effect 協調，不直改 state
      refresh.py
    adapters/
      browser.py
      snapshot_codec.py
      script_loader.py
      scripts/{common,document,date,area,seat,quantity,dialog,navigation,submit,inputs,credentials}.js
  utiki_core_services.py           # 條件式：只在 ADR 證明現行核心映射必要時建立

tools/
  migrations/convert_utiki_config.py
  check_utiki_architecture.py
  check_upstream_boundary.py
  verify_utiki_ci_results.py
  verify_modified_python.py          # 開發／CI：從 Git 交叉核對範圍，逐檔 py_compile
  run_utiki_typecheck.py
  benchmarks/{bench_utiki,compare_utiki}.py
build_scripts/
  utiki_profile_upgrader.spec       # 獨立 upgrade artifact，不進 bot/settings
  test_frozen_utiki_upgrade.py
  verify_utiki_bundle.py
ci/
  utiki-isolation/                 # 無 egress 的測試啟動與負向 canary
  mypy-utiki.ini
  utiki-typed-scope.txt
  utiki-release-targets.json
  utiki-required-cases.json         # 獨立核准的必要案例／義務；不是 execution manifest

tests/
  {unit,contract,integration,browser,performance}/utiki/
  architecture/
  upstream_integration/
  fixtures/utiki/
  upgrade/utiki/
```

`{...}` 是責任集合記法，不是實際檔名。只有同一變更原因的小檔案可合併；不要為湊表格建立空 class。**不預設建立 `src/integrations/utiki.py`、`utiki_services.py`、`execution_contracts.py` 三層新結構。**

### 4.2 允許的依賴

```text
main → 新 bootstrap 工廠 + current policy + 必要外部核心服務接合
bootstrap → application + browser adapters + typed ports
application → domain + profiles + policy + ports
browser/codec → domain + profiles + ports + zendriver
外部核心服務接合 → current-core helpers/coordinator + 新 ports
pure domain/profile/config/keyword → 標準函式庫 + 已核准純來源
settings → current-config validator（不經 engine）
```

模組內部不 import main／外部 service bridge／舊 workflow／舊 alias；domain 不 import ports、browser、settings 或大 util。主程式的資料投影若屬現行核心契約可保留，但不能在投影時把 legacy 設定轉成新格式。

core-services adapter 不持有 PurchaseState、不選票、不改 gate、不直接觸發 browser reload。bootstrap 和主程式一起組裝的過程只有一個 engine 實例；不再額外呼叫一個獨立 root 各建 singleton。

### 4.3 純核心與型別邊界

純模組禁止網路、檔案、環境讀取、log、sleep、create_task、當前時鐘、UUID／全域 random 與 module runtime registry。時間、身分與抽樣由呼叫端傳入。

靜態型別驗證涵蓋所有新/重寫 domain、application、ports、adapters、bootstrap、current-config 及實際建立的外部 services；不能只檢查 domain 後宣稱整個新架構有型別護欄。動態 third-party 值先轉 `object` 並經單一 codec 驗證，不讓 `Any` 沿 ports 傳入 state。[E01/E12]

### 4.4 共用 keyword 與 native-setter：一份來源，必要核心入口不移除

既有 util 純規則經 caller inventory 證明後移到 `keyword_rules.py`；uTiki 直接用純來源。現行其他平台仍使用的 util 公開入口可委派到同一 body。唯一 caller 若只是舊棒球，就改 caller 並刪 wrapper。[R15]

native input setter 以目前核心 `CONST_NATIVE_INPUT_SETTER_JS` 為 source-of-truth。[R12/R20] 新模組不為取常數 eager import 大型 `nodriver_common`：由已核准模組外服務於啟動提供可信 script fragment/digest；browser assembler 合入 IIFE。若將該純 fragment 抽到中立資源，必須同步所有核心 caller、保留唯一 body、更新規範與 parity tests；不自行複製另一版 setter。

上游修改這些來源時，touchpoints 必須指向唯一新 body 與現行公開入口。helper 摘出不等於免除上游語意同步責任。

### 4.5 ADR-01：直接 bootstrap vs. 頂層 integration

**狀態：PROPOSED；本版推薦 A，正式採用欄位尚未填。**

| 方案 | 優點 | 成本／風險 | 裁決 |
|---|---|---|---|
| A：main → 新 bootstrap，必要 core ports 在外部提供 | 平台業務留 platforms；少一層轉送；新工廠可獨立測 | main 有少量接點；必要服務映射需 caller 清單 | 預設採用 |
| B：頂層 integrations 統一入口／服務＋中立 contracts | 當真實共用 host contract 已存在時可集中接入 | 沒有第二個實際需求時易變成額外 facade／過早框架 | 只有通過下方條件才取代 A |
| C：舊 API facade → 新 engine | caller 少改 | 違反零歷史相容、保留舊契約 | 不採用 |

採 B 必須提供：現行核心的獨立契約與真實 caller、A 無法簡潔滿足的原因、兩方案 shared diff／import／成本比較、契約測試、維護者採用紀錄。**非 uTiki 假平台只能驗證接口中立，不能單憑假平台證明值得建立通用層。**

若 B 真被採用，A 的多餘 factory/轉送須同時收斂，不讓兩套 root 並存。真正外部核心適配仍放模組外；選 A 不是允許把舊相容塞回 bootstrap。

### 4.6 ADR-02／ADR-03 摘要

- ADR-02 設定政策：唯一 canonical section；mixed equal/conflict 都在離線 upgrader 處理；正式使用路徑不轉換。規格在 §11/14。
- ADR-03 發布順序：P3 當期驗證 frozen resources；P6b live gate 在 P6c final delete 前；P8 只重新驗證最終 artifact，不首次驗證關鍵能力。

兩項均為待採用提案；不能由代理自行修改現行治理門檻後宣佈取得批准。

---

## 5. 資料模型：從欄位慣例變成可驗證的契約

### 5.1 具名、不可變，但避免形式化過度

snapshot、plan、已歸納 state 原則上使用 `@dataclass(frozen=True, slots=True)` 與明確 element type。標量 identity 可採 `NewType`，不為每個字串配置多層 wrapper；選型須量測。Python 的 frozen 不等於深度不可變；內部不得放可修改 dict／list，宜採 tuple／frozenset 及同樣不可變的 record。[E03]

不可變 state 更新採 structural sharing：只建立改變的狀態分支，共用未變的 immutable snapshot／plan；禁止每個 transition deep-copy 全部座位或重做 JSON encode/decode。無變化的 reducer 回傳原 state／明確 no-op。

不要把任何 tuple 一概禁止。`tuple[SeatSnapshotItem, ...]` 是合理集合；要移除的是靠數字位置表達不同欄位的 tuple。

### 5.2 建議的主要資料型別

| 型別 | 主要欄位／目的 |
|---|---|
| `TabKey` | browser generation、CDP target ID；無 target ID 時使用已登記的穩定 token |
| `DocumentKey` | 非空 canonical document token；只在 TabKey 的範圍內有意義 |
| `TenantKey` | 設定中的確定 tenant，不是模糊 hostname substring |
| `AuthEpoch` | 已驗證的 session 邊界世代，不含帳密或 Cookie |
| `PurchaseKey` | TabKey、tenant、AuthEpoch、performance、group、price area、page mode，以及契約需要的 product |
| `OperationId` | 不可重用的 intent identity；與文字 reason 分離 |
| `OperationStamp` | session generation、operation ID、來源 document、policy/control revision；拒絕過期 proposal，不丟棄原 attempt 的 factual receipt |
| `DateRow`／`DateControl` | row text、具名 control fingerprint、actionable |
| `AreaRow`／`MapArea` | 區域 identity、票價、三來源庫存觀察、structured action |
| `TicketType` | fingerprint、type ID、type name、disabled、counter |
| `Seat` | fingerprint、title、DOM 座標、geometry、available、selected |
| `SubmittedSeat` | native payload 的具名欄位；不讓 domain 到處寫 A／Y／Z 索引 |
| `QuantityRow` | key、name、disabled、remaining、value |
| `FieldObservation[T]` | 存在性、唯一性、解析結果；區分 missing／invalid／present |
| `DialogObservation` | 頁面／document／可見數量／文字／可驗證角色 |
| `CommitOutcome` | status、dispatch evidence、reason、stamp、必要的 observation |
| `TickResult` | 現況摘要、下次 poll 條件、是否需人工處理；不是模糊 bool |
| `ClaimId`／`ActiveEffect` | session 級唯一副作用授權及其生命週期；不等於 task 是否已返回 |
| `EffectProposal`／`ArbitrationDecision` | reducer 提案與唯一仲裁結果；未被 grant 不得 dispatch |
| `PurchaseCycleId` | 明確使用者任務邊界，不由新 URL／PurchaseKey 自動建立 |
| `RecoveryId` | 已知拒絕的恢復流程；子 input/clear/reload 操作都需對應 parent |
| `CredentialRevision` | 可信輸入來源改變；不等於 AuthEpoch 或登入成功 |
| `InputAttemptId`／`InputReadyEvidence` | 輸入來源、generation、寫入／回讀證據；不含長期明碼 |

用 `FieldObservation` 時應限制在真的需要區分「欄位不存在」與「空值」的地方，不包裝每一個字串。重複欄位不是 missing，不能被 `.get(..., '')` 吞掉。

### 5.3 識別的安全語意

- URL 和 document token 分開；hash/query 漂移不等於新 document。
- document token 缺失、0、NaN 或無法解析時為 UNKNOWN，不用 URL 代替。
- canonicalization 在單一 browser helper／codec 定義；不讓不同 reader 各有一套格式。
- token 是 opaque identity；不用 Python float 重排字串精度後再比較。
- BFCache／history 回到舊 document 不應自動解除未知提交 blocker。
- `AuthEpoch` 不由 DOM 空白或取不到 credentials 推斷已變更；只有有證據的登入／登出／credential replacement 邊界才更新。
- `PurchaseKey` 不把 volatile query string、完整 URL 或秘密 hash 當主要 identity。
- 在不同 auth epoch 下，未決的舊提交先隔離及人工核對，不能因換帳號而直接視為從未發送。
- `CredentialRevision` 與 `AuthEpoch` 不互換：更正登入輸入不代表 session 已建立；只有已明確拒絕的舊 submit 才可走更正輸入後的正常重規劃。
- 新政策只使未發送 proposal 失效；原 claim 的成功／拒絕／未知 receipt 必須依 original context 歸屬。
- notification 去重、input revision、coordinator handle 不能代替 PurchaseCycleId；每個 ID 都有具體 scope 與退休規則。

### 5.4 JSON wire format 與 domain model 分離

browser 回傳只經過 `snapshot_codec.py` 轉成 domain record。codec 在首次進入信任邊界時驗證型別、唯一性、數量邊界及有限數值；planner 不再重複猜 raw dict，也不重新解析已經有效的 typed snapshot。仍會變動的網頁資料由 commit-time live audit 處理，不反覆重驗相同 Python 物件。

同一個 malformed raw snapshot 應產生一致的 `ObservationFailure`，不在不同 caller 得到 `{}`、`None`、`False` 三種結果。

本機 script 與 Python 同版本交付，無需支援多版 wire decoder。可保留單一 `schema_version` 以抓錯配；版本不符直接拒絕，不能 fallback 到舊 parser。

### 5.5 STALE 與副作用證據必須分開

現況不同 commit 路徑對 STALE 的解讀不盡相同；根本修正不能只換 enum 名稱。[R02][R03]

建議 status 與 evidence 分為兩個欄位：

| 欄位 | 值 | 語意 |
|---|---|---|
| status | APPLIED／REJECTED／STALE／UNKNOWN | 本地契約結果 |
| evidence | NO_EFFECT／LOCAL_MUTATION／REQUEST_DISPATCHED／REQUEST_POSSIBLE | 是否可能已產生不可撤銷的外部請求 |

典型處理：

- `STALE + NO_EFFECT`：可以重新觀察、重新規劃。
- `STALE + LOCAL_MUTATION`：先 audit 或清理，不直接假裝未動作。
- `UNKNOWN + REQUEST_POSSIBLE`：保留 intent，等待 evidence 或人工核對，不自動重送。
- `APPLIED + REQUEST_DISPATCHED`：只表示發送，仍需等待業務結果。

`NO_EFFECT` 必須有實際可證明的路徑，不能只因 adapter 丟 exception 就猜沒有 click。

---

### 5.6 狀態建構必須排除非法組合

優先使用少數具名的 state variants／tagged union：例如 `Idle`、`Prepared(intent)`、`AwaitingOutcome(attempt, deadline)`、`HeldUnknown(attempt, reason)`。不要把所有可能欄位做成一個含二十個 Optional 的 dataclass，然後仍靠呼叫端猜哪些欄位有效。

不要求每個細節都新建一個 class；同一 invariant 的少量欄位可留在同一 frozen record。重點是：`AwaitingOutcome` 必須有 attempt，`Reserved` 必須有相符 reservation，`Completed` 必須有能對應該 request 的 document evidence。這些條件由建構與 reducer 保證，不在正常每 tick 重驗一遍。

`CommitOutcome` 的 status/evidence 也必須有允許組合表或具名結果建構器；未知回傳不能組成「成功＋確定無效果」這類無意義結果。直接建構的程式內部資料由型別／unit test 保護；來自 browser 的資料由 codec 在邊界驗證。

---

### 5.7 有效事件與可達狀態

狀態建構器處理欄位一致性；reducer 處理該 state 能接受的事件；arbiter 處理跨 reducer 的副作用資格。三者職責不同，不因建了 dataclass 就宣稱事件組合合法。

factual receipt 欄位與待執行 proposal 分離：前者描述已發生的事實，後者描述想執行的操作。對所有 receipt 定義未知 enum／缺失 identity／來源不符的拒絕方式；對 proposal 定義需要的 claim 與 gate。輸入明碼材料不放 immutable 歷史快照或一般 repr。

---

## 6. Session ownership、唯一副作用仲裁與執行模型

### 6.1 單一 writer 不是完整仲裁

每個 tab 一個 `SessionHandle`，engine instance 持有唯一 registry。`SessionState` 聚合 navigation/purchase（含 inputs）/refresh/document/policy；application 只有一個 writer 能替換 state。adapter、OCR、lease task 只能回傳結果。

**這仍不足以防止不同 reducer 順序合法、組合起來卻互相破壞。** 例如 purchase 已 `HELD_UNKNOWN`，不能因 refresh reducer 看見排程到期就導致 reload。解法是 session 級 `arbitrate_effects` 與唯一 `ActiveEffect`，不是再多一把覆蓋 I/O 的大鎖。

### 6.2 Proposal、Claim 與結果的型別

| 型別 | 必要資訊／責任 |
|---|---|
| `EffectProposal` | 不可變 id、kind、owner operation、TabKey/generation、document、policy revision、要求的資源、destructive 類型、parent recovery id、局部 prerequisite |
| `ActiveEffect` | 全 session 唯一的頁面副作用 claim；有 `CLAIMED/RUNNING/AWAITING_EVIDENCE/HELD` variants，精確 owner/id/source；不由三個 reducer 各保存一份 |
| `ArbitrationDecision` | 至多一個 granted mutation；被拒／defer 的 reason、需退休的未提交 refresh、必要非破壞性 housekeeping；不回傳真假模糊 bool |
| `EffectReceipt` | claim id、operation id、observed document、status/evidence、實際副作用階段、同一次執行取得的必要 observation |
| `ResourceCleanupTicket` | exact request/permit token 與 cleanup deadline；不包含購票 phase、不允許導航或建立新購票 |

`NO_EFFECT`、`LOCAL_MUTATION`、`REQUEST_DISPATCHED`、`REQUEST_POSSIBLE` 按 §5.5 建構；未被 grant 的 proposal 不得自行執行。`ActiveEffect` 只持有 ownership 指向，不複製完整 purchase/refresh state。所有 state 與 claim 更新在同一短臨界區原子套用。

### 6.3 每次事件的處理順序

```text
on_event(session, event):
  apply control revocations / close latch
  correlate factual completion with its original operation
  reduce affected local states and collect bounded proposals
  arbitrate_effects(next_state, current_gate, proposals)
  atomically store state + claim / retirement decisions
  execute granted I/O outside lock
  process completion immediately; continue if ready
```

以上為契約偽碼，不是可直接部署的實作。reducer 產生的是 proposals，不再叫「已授權 effects」。新 state 的 `DISPATCH_CLAIMED` 只能由 grant 對應的事件進入，不能在仲裁前自行宣稱已取得 claim。

**控制失效與 factual receipt 不混為一談。** 設定已更新可以拒絕舊 proposal，卻不能丟掉舊 submit 的成功／未知 receipt。completion 以原 OperationId/ClaimId/context 歸屬；新政策不改寫已送出的事實。舊 generation 的回傳不能推進新 session，但仍可記入它所屬的退休結果／exact cleanup。

### 6.4 先排除不合法工作，再排序

`arbitrate_effects(SessionState, Gate, proposals)` 是同步、無 I/O 的純函式。固定數量的 effect 類別／小型 state 檢查即可；不掃所有歷史、不讀 DOM、不查 SQLite、不重驗完整 schema。

第一層是不可越過的 eligibility：

1. CLOSING/STOP 不建立新購票、導航、刷新或輸入 mutation；只允許有界結果處理與 exact cleanup。停止不等於撤回遠端請求。
2. 有在途／可能發送的 `ActiveEffect`，不得發出不相容的第二個 mutation。即使 callback 返回、timer 到期，未決 ownership 也不能自動釋放。
3. purchase `AWAITING_OUTCOME`／`HELD_UNKNOWN` 禁止自動改變證據頁面：普通／排程／429 reload、返回導航、CAPTCHA reset、重新填值與新 submit 均不可。可只讀觀察、回報明確限流、維護已持有的有界資源。
4. `ACCEPTED`／`BLOCKED_QUOTA` 禁止普通購票與刷新；切換與解除只走 §8 正式事件，不以新 URL/key 直接解鎖。
5. 已知拒絕後的 recovery，只允許與該拒絕／parent recovery id 相符的下一步。別的 reducer 不能借用 recovery 名義發出操作。
6. pre-sale/PAUSED/challenge 等 gate 依 §12 限制；回到 ready 不得跳過 gate。輸入寫入也是 mutation，不是只讀。
7. 來源 document、scope、revision 或 proposal prerequisite 已失效者拒絕。真正的 endpoint／payload live audit 留在同一次 browser commit，不額外多查一遍。

第二層只排序仍合法的工作：

| 優先類別 | 工作 | 限制 |
|---|---|---|
| 0 | lifecycle 撤銷、matched receipt 歸納與必要 exact cleanup | 是控制／事實處理，不是可搶佔遠端請求的「高優先操作」 |
| 1 | 同一已確認失敗操作的下一個 recovery effect | parent ownership 正確、沒有 submit unresolved 才可 |
| 2 | 使用者已明確設定且成為本次前置條件的一次性 scheduled action／已證明必要的 document recovery | 不能越過 unresolved barrier；一個 schedule revision 最多完成一次 |
| 3 | 當前已就緒購票步驟，或到達它所必需的合法 navigation/input step | 按 dependency chain，不把無關導航排在 submit 前 |
| 4 | 普通「無票／未命中再查」refresh | 僅在沒有可行動候選時；不得覆蓋已就緒購票 |

**不採用 `navigation > refresh > new purchase` 的通用固定順序。** 同一頁若 submit 已 ready，一般 refresh 應被取消/失效，而非因優先序較高先刷新。兩個無法由 page contract 分辨、卻互相矛盾的同級 mutation，回 `CONFLICTING_PROPOSALS` 並重新觀察／人工處理，不任選第一個。

### 6.5 Claim 的生命週期與 release 規則

| 當前 claim | 事件 | 處理 |
|---|---|---|
| Free | grant 合法 proposal | 同步安裝 CLAIMED，writer 記錄準確 owner |
| CLAIMED | 尚未跨 dispatch boundary 的明確取消 | 回 Free；退休 proposal，不允許晚到 task 再送 |
| CLAIMED | 即將開始外部呼叫 | 本地最後核對 gate/claim，進 RUNNING；核對至 adapter dispatch 前不插無關 await |
| RUNNING | 證明 NO_EFFECT | release；以 receipt 決定新 observation/plan |
| RUNNING | 本地變更已完整 audit，無外部請求 | 合併 state update 後 release；需要更多輸入則等待，不預設可 submit |
| RUNNING | LOCAL_MUTATION 不明 | 留住 owner 或轉已知 recovery，禁止盲目第二次操作 |
| RUNNING | REQUEST_DISPATCHED | 進 AWAITING_EVIDENCE；callback 返回不代表 release |
| RUNNING | REQUEST_POSSIBLE／exception／取消時已可能跨界 | HELD；保留原 intent，只觀察／核對 |
| AWAITING_EVIDENCE/HELD | 匹配成功／已知拒絕／可信目的 document | 在同一 writer transaction 消費 receipt、建立 terminal/recovery state，再釋放或交接 claim |
| 任意在途 | timeout | 只改為可見 held/deadline outcome，不釋出新的自動發送權 |
| 任意 | close | retirement＋有界收尾；不建立新 owner，不宣稱已取消遠端操作 |

同一外部請求產生的結果可能先於 adapter 回傳。receipt reducer 必須冪等：已被成功結果完成的 claim，不可被之後的「dispatch returned」覆寫成 awaiting。

`CLAIMED` 到真正送出之間仍可能收到使用者停止；只能保證停止被觀察並序列化後不建立新的未授權 dispatch，不能保證把已交給瀏覽器的請求撤回。這個不可消除的邊界必須寫入測試／訊息，不加 sleep 假裝能消除競態。[E02]

### 6.6 跨 reducer collision matrix

每列都要有 contract/integration 測試；X 系列在 §16 登錄。

| Session 條件 | 競爭 proposal/event | 必須處理 |
|---|---|---|
| Purchase awaiting/held unknown | scheduled reload | 拒絕 mutation；保留同 schedule intent，回 BlockedByUnresolved，而非每 tick 新建 request |
| Purchase awaiting/held unknown | navigation recovery／return | 拒絕，不能破壞 dialog/checkout evidence |
| Purchase awaiting/held unknown | 明確 429 | 可 report incident；本 tab 不 reload，其他合法 tab 依共享協議處理 |
| Purchase awaiting/held unknown | CAPTCHA refresh、login rewrite、seat clear | 拒絕；只有可歸屬的明確拒絕能進 recovery |
| Purchase 已 ready | 普通 refresh QUEUED/RESERVED 未 commit | 原子退休普通 refresh 的 dispatch 權，再安排 exact cleanup；優先現行購票 |
| Purchase 已 ready | coordinator reserve 正在返回 | 舊 token 已退休；late reservation 只 cleanup，不能觸發 reload |
| Purchase 已 ready | permit commit 正在進行／結果不明 | 不搶佔；先收斂原 claim，區分 browser-not-attempted 與 possible |
| Purchase 已 ready | 一次性、尚未完成的開賣前 scheduled gate | 依使用者設定完成必要 gate；不當一般 refresh，也不重複完成 |
| Conflict recovery | 普通／排程 refresh | 只允許 recovery 擁有的 ReloadHandle；普通 request 不消費 completion |
| CAPTCHA reset awaiting | OCR result for old image | 只丟棄過期輸入結果；不填值、不 submit |
| Navigation awaiting | refresh 或另一 navigation | 不再發送；已有目的 evidence 先歸納，再提下一步 |
| Reload awaiting document | seat/quantity plan from source | 拒絕 stale plan；收到新 document 才重建 |
| ACCEPTED | route/key/auth 改變或普通 refresh | 保持成功記錄；需 §8 的新交易事件才能重新開始 |
| BLOCKED_QUOTA | 切區域／換設定 | 維持 blocker，不利用 key 變化避開限制 |
| 任意 | pause/stop 與 ready completion 同批 | 先撤銷未送 claim；仍記錄已發生的 completion；不新增副作用 |
| 任意 | 舊 claim cleanup 晚到 | exact token；不能清新 claim/new generation |
| 模組持有 tab | 核心直接 reload／reset browser | 核心也必須經 §12 handoff／request；不能以「核心權限較高」繞過 arbiter |

已退休但尚未釋放的 reservation，只要確定還沒 commit、且所有晚到 callback 都無 dispatch 權，可與新購票的本地／browser 工作共存；cleanup 不阻塞正常交易。permit commit 一旦開始則不可套此優化。這個邊界以測試證明，不憑 timeout 猜測。

### 6.7 與外部核心共用的所有權邊界

仲裁只能約束經過它的自動操作，不能阻止使用者手動操作或網站自己導頁。外層主程式對 uTiki tab 的 startup/scheduled/recovery reload、close/reset、其他 handler takeover 都需明確接入。

- 核心只是請求 reload：交 engine，由 arbiter 回 typed handle／defer／blocked。
- 核心需要暫時接管 challenge/auth：先發 control revoke，等待未發送 claim 退休；未決提交期間不做破壞證據的自動操作。只讀偵測可保留，原本會 click/reload 的核心路徑須登錄。
- 使用者明確 stop/force-close：停止本地自動化、做有界 cleanup；允許 browser 結束，但回報未決結果，不把強制關閉當「未送出」。
- 手動導航／帳號改變：先歸屬舊 attempt，不因 route owner 切換而自動清空 blocker。

外部 integration/service bridge 不另建 Arbiter／PurchaseState，不用舊字典重演仲裁。新 claim 的唯一授權點在 session writer。

### 6.8 工作、mailbox 與鎖的範圍

短 `asyncio.Lock` 只保護 state/claim 更新，不跨 CDP、SQLite、OCR 或等待 document。每 tab 有自己的 lock，沒有全域串行購票鎖。

一個 session writer、一個必要 readiness waiter，外加有用途且有界的 I/O/lease/notification 工作。proposal 由當前狀態即時計算，不維持會累積過期 click 的 command queue；mailbox 只承接外部 completion/control。一般重複觀察可合併，STOP 與 factual receipts 不可丟失。

取消不是 rollback。持有 task 參照、finally 清理、適當重新拋出 CancelledError。TaskGroup 正常退出不等於替無限 loop 自動取消；close 需明確 signal/cancel/join，OCR thread 取消等待也不代表底層計算已停。[E02]

### 6.9 低延遲驗收

arbiter 僅看當前小型 state 與至多固定類別數的 proposals；正常一個合法 proposal 的快速通道也必須使用同一 eligibility 規則。不得為「仲裁」新增 observer、HTTP/SQLite 查詢或重新抓整份 DOM。

量 `arbiter_cpu_ns`、`reduce_and_schedule_ms`、claim 到 dispatch 的延遲；納入原 p95/command-count 護欄。不承諾零 CPU 成本，也不以安全為名允許無用途 hop。普通 refresh 與 ready purchase 同時出現時，X05/V15 必須證明不新增一次頁面刷新或 50 ms 等待。

---

## 7. 導覽狀態機：帶有 session 仲裁的完整生命週期

### 7.1 所有導航都是 proposal

日期、區域、fast navigation、return、navigation recovery 共用 session arbiter。domain/navigation 只產生資料與提案，application 執行 browser port；sessionStorage 是輔助 evidence，不是第二份 runtime 或授權來源。

### 7.2 狀態與出站

| 當前狀態 | 事件／前置條件 | 下一狀態／操作 |
|---|---|---|
| IDLE | 合法來源 snapshot 及 plan | PREPARED；無副作用 |
| PREPARED | EffectGranted 且來源/控制仍 current | DISPATCH_CLAIMED |
| PREPARED | 拒絕、policy 失效、pause | 退休未發送 plan → IDLE；PAUSED 為 session gate，不另外複製 navigation phase |
| DISPATCH_CLAIMED | NO_EFFECT | IDLE 或新 observation；釋放 claim |
| DISPATCH_CLAIMED | dispatched/possible | AWAITING_DESTINATION；保留 claim |
| AWAITING_DESTINATION | 原 intent 的合法目的頁／document/ID 已驗證 | 原子完成導航、發布 attested context、釋放 claim → IDLE |
| AWAITING_DESTINATION | loading／讀取未知 | 保持；一個有界 waiter，不能重點 |
| AWAITING_DESTINATION | mismatch 且有可證明回原生來源的契約 | RETURN_PREPARED；只提原 intent 的 return，不立刻直接 assign |
| AWAITING_DESTINATION | deadline | RECONCILING 或 HELD_UNKNOWN；是否能恢復依下節，不因 timeout 解鎖 |
| RETURN_PREPARED | arbiter grant，沒有未決 purchase，來源尚符合同一 intent | RETURNING；一次副作用 |
| RETURN_PREPARED | 來源/parent evidence 不足／grant 被拒 | 保持至 deadline 或 HELD_UNKNOWN |
| RETURNING | 匹配 source-return 的新 document | IDLE；exact marker cleanup |
| RETURNING | definite NO_EFFECT 且原策略允許有界重試 | RETURN_PREPARED；有 retry count/deadline，非每 tick 重送 |
| RETURNING | possible/in-flight | 保持；deadline 後 HELD_UNKNOWN |
| RECONCILING | exact evidence 補全 | 完成原 intent，或同 parent 的一次受控恢復 |
| RECONCILING | 證據不足／截止 | HELD_UNKNOWN；可見人工處理，停止自動破壞性動作 |
| HELD_UNKNOWN | 原 intent 的延遲可信目的 evidence | 完成原 intent；不建立第二個導航 |
| HELD_UNKNOWN | 普通 tick/新設定/不明 document | 保持；無新 mutation |
| 任意 | UserRetireFlow／Stop／Close | 退休或 CLOSED；有未決效果則保留 unresolved 分類，不宣稱已撤回 |

每種 recovery 必須有 parent operation、獨立 id、來源 document、deadline、最多發送次數與 evidence 分類。當事件晚到時，優先歸屬舊意圖，不能「先清 pending 再處理新頁」。

### 7.3 Recovery 不是破壞 evidence 的通行證

普通區域/日期頁回復，只有在已確認的路由契約允許、沒有 purchase unresolved、原導覽不會暗中造成未知購票結果時，才可進入原生返回或受控 reload。不能用泛用 `recovery > purchase` 優先序蓋過 §6 的 eligibility。

特殊 native action 的 destination 限制來自真實 product/tenant/action 證據；缺必要 ID 不猜。若無法區分是導覽失敗還是購票結果尚未出現，進 HELD_UNKNOWN，不自動導航抹掉現頁。

### 7.4 Document 與 fast marker

保留唯一 canonical document token；URL query/hash 漂移不等於新 document，BFCache 返回不自動解除 blocker。marker schema 只支援本版一種；operation id/source/destination/expiry 需匹配 local intent，清除採 compare-and-delete。

不讀舊 marker schema。升級前停止舊程序且不承接舊分頁 intent。沒有本地來源、只有 marker 或直接進既有購票頁時，按 §8 的 startup/manual gate，不因「新 marker 不存在」就准送。

### 7.5 Fast-path 與 fixture 範圍

保留基準租戶能力與原生限制，不擴大 shortcut。有效目的頁的第一次 observation 可同時提供 attestation 與下一步資料；沒有必要先查一次 hidden IDs 再重讀同樣資料。

N01–N08 加 X01/X02/X09/X16 覆蓋跨 reducer 競爭；RETURN_PREPARED/RETURNING/RECONCILING/HELD 的出站均在 T 系列映射，不以只有 happy path 的 browser fixture 宣告完成。

---

## 8. Purchase、Input 與 CAPTCHA：完整出站與恢復契約

### 8.1 模型範圍與唯一 owner

`PurchaseState` 表達當前購票 context 與 submit/recovery lifecycle，包含一份 `InputState`。`domain.inputs.reduce_input(...)` 是 purchase reducer 呼叫的純 helper，**不是第四個自主 writer，也不能越過 session arbiter執行。**

有限歷史結果只存已歸屬的 immutable record；未決 UNKNOWN 不因 TTL/LRU eviction 消失。對已完成 key 的去重採精確索引；容量到限時要求明確結束／新 session，不以丟棄記錄換回同 key 的自動權限。

區分 `PurchaseKey`（一次交易的領域身分）、`SubmitAttemptId`（一次對外送出）、`PurchaseCycleId`（使用者明確開始的一次任務），避免只改 URL/帳號/區域就當新交易。DocumentKey 是操作來源，不可作為「換文件就可重送」的唯一依據。

### 8.2 所有狀態共通事件規則

本節的事件不是公開任意解鎖 API。只有 engine/core-control 或本地人工恢復控制入口可產生，必須帶 current session、cycle、operation 身分。

| 事件 | 所有狀態的預設處理 |
|---|---|
| OrdinaryTick / 同值設定更新 | 不改 phase、不新增 submit；只推進目前合法等待 |
| ObservationUnavailable | 不能當成欄位消失／已登入／新 document；不授權 mutation |
| OldProposal / 舊 input result | 不能執行；退休其 intent 或釋放 ephemeral result |
| MatchedLateSubmitEvidence | 不因當前 policy revision 不同而丟棄；歸屬原 attempt，再套用成功／拒絕 |
| UnmatchedDialog / 多個不明彈窗 | 不自動 dismiss；有 submit 關聯但不足時 HELD_UNKNOWN，沒有關聯則先阻擋動作 |
| Pause | 撤銷尚未送出的 claim，保持等待期限／事實；不重送、不重算 eligibility |
| Resume | 只恢復被 gate 暫停、且依現有狀態合法的工作；不解除 unknown/quota/accepted |
| Stop / Close | CLOSED 或退休記錄；停止新 claim、有界收尾；未決分類保留 |
| UnexpectedContextChange | 已送出者先記錄原 attempt；未送者失效並重新觀察；不能自動清 blocker |

未在下列 state-specific 表中允許的事件必須保持狀態或回明確 invalid-event reason；禁止用 `else: READY` 作泛用恢復。每個 state×event family 都有測試中的保持／轉移規則，不靠表格沒寫就自由發揮。

### 8.3 主狀態完整轉移

| 當前狀態 | 事件／條件 | 下一狀態 | 允許的下一步 |
|---|---|---|---|
| NO_CONTEXT | 合法來源導覽得到 attested purchase context，非未知舊頁 | SELECTING | 讀取／規劃一次 |
| NO_CONTEXT | 直接接回來源不明的 seat/quantity/checkout | HELD_UNKNOWN(startup_context) | 人工核對，不自動清欄位 |
| SELECTING | SelectionCommitted 且 payload 可用 | PREPARING_INPUT | 讀／準備必要輸入 |
| SELECTING | NoMatch/尚未 ready | 保持 | 原本允許的等待／普通 refresh proposal；受 arbiter 決定 |
| SELECTING | 已知僅本地 partial mutation，或外部效果未知 | 僅本地者 RECOVERING_CONFLICT(local_selection)；可能有外部效果者 HELD_UNKNOWN | 依精確 evidence 分流，不把 exception 當成安全清理的證明 |
| PREPARING_INPUT | InputReady receipt 與 selection/context 相符 | READY | 依 auto-submit policy 決定是否提出 submit |
| PREPARING_INPUT | 輸入仍 loading／manual mode | 保持 | 一個 waiter／使用者輸入；不得新增自動 submit |
| PREPARING_INPUT | known input failure/刷新需求 | RECOVERING_INPUT | 只處理 input lifecycle |
| READY | auto-submit policy 允許，live context 未被失效 | PREPARING_SUBMIT | 建 SubmitAttempt 與 proposal，不先發送 |
| READY | auto-submit 未啟用 | READY | 保留選擇供人工操作；人工點擊不是 engine dispatch |
| READY/PREPARING_SUBMIT | policy/input/document 改變且未發送 | SELECTING 或 PREPARING_INPUT | 退休舊 plan；fresh evidence 決定重規劃 |
| PREPARING_SUBMIT | arbiter EffectGranted | DISPATCH_CLAIMED | 單次 guarded submit 或融合操作 |
| PREPARING_SUBMIT | Busy/blocked | 保持或失效 | 不能繞過；ready 時不新增固定 sleep |
| DISPATCH_CLAIMED | NO_EFFECT 拒絕，selection/input evidence 仍有效 | READY | 可新 proposal；原 attempt 記明未送 |
| DISPATCH_CLAIMED | LOCAL_MUTATION 且未 submit | PREPARING_INPUT / SELECTING | 視 receipt 重讀必要部分，不謊稱已送 |
| DISPATCH_CLAIMED | REQUEST_DISPATCHED | AWAITING_OUTCOME | 保留 ActiveEffect，立即觀察結果 |
| DISPATCH_CLAIMED | REQUEST_POSSIBLE／跨 dispatch 邊界 exception | HELD_UNKNOWN(submit) | 禁止其他破壞證據的 mutation |
| AWAITING_OUTCOME | matched success／可信 checkout evidence | ACCEPTED | 完成原 attempt；停止購票刷新，通知獨立處理 |
| AWAITING_OUTCOME | matched seat/quantity conflict | RECOVERING_CONFLICT | 原子記已知拒絕，交接為 recovery |
| AWAITING_OUTCOME | matched CAPTCHA rejection | RECOVERING_INPUT | 廢止舊答案，啟動一次 input recovery |
| AWAITING_OUTCOME | matched auth rejection | BLOCKED_AUTH | 保存拒絕及 credential revision；不重試同資料 |
| AWAITING_OUTCOME | matched quota rejection | BLOCKED_QUOTA | 不自動重試或切區域逃避 |
| AWAITING_OUTCOME | deadline/unknown result | HELD_UNKNOWN(submit) | 有界觀察／人工核對；不准普通 reload |
| HELD_UNKNOWN(submit) | 原 attempt 的可信延遲成功 | ACCEPTED | 不建立新 submit |
| HELD_UNKNOWN(submit) | 原 attempt 的可信明確拒絕 | 對應 RECOVERING_* / BLOCKED_* | 只有可驗證拒絕才能交接 recovery |
| HELD_UNKNOWN | settings/key/auth/document 單獨改變 | 保持 | 不解鎖；展示原 attempt 未決 |
| RECOVERING_CONFLICT | recovery 完成且相符新 document已可用 | SELECTING | 舊 attempt 保留已拒絕；新 attempt id |
| RECOVERING_INPUT | InputRecoveryCompleted、選擇仍有效 | READY | 同次必要 audit 後才准新 submit proposal |
| RECOVERING_INPUT | InputRecoveryCompleted、selection/document已改變 | SELECTING | 不重用舊 SubmitPlan |
| RECOVERING_* | deadline/非預期 document/效果不明 | HELD_UNKNOWN(recovery) | 停止自動 mutation；保留 parent rejection 與 recovery evidence |
| HELD_UNKNOWN(recovery) | 原 RecoveryId／child claim 的可信 late reset/clear/reload receipt | 對應 RECOVERING_INPUT/RECOVERING_CONFLICT 的已確認子階段 | 同 writer 消費原 claim 後再提下一步；不憑過期或相似 document 解鎖 |
| BLOCKED_AUTH | 可信 credential replacement 或已驗證重新認證；原 submit 明確拒絕 | PREPARING_INPUT / SELECTING | 新 input/selection，不能沿用過期答案；仍需 live gate |
| BLOCKED_AUTH | 相同憑證／欄位讀取失敗／普通 reload | 保持 | 不把空 fingerprint 當登入已改 |
| BLOCKED_QUOTA | 任意普通 tick／選區／模式／帳號欄位變化 | 保持 | 不自動解除限制 |
| BLOCKED_QUOTA | 明確人工核對並 RetirePurchase | MANUALLY_RESOLVED / RETIRED_UNRESOLVED | 原 context 結束，不直接返回 READY |
| ACCEPTED | 同 key reload/history／重複結果 | ACCEPTED | 通知去重，不再次送出 |
| ACCEPTED | 新 PurchaseKey、活動或 auth 變化但無使用者新任務指示 | ACCEPTED（結果記錄不變） | 不自動連續購買 |
| ACCEPTED | UserStartNewPurchase + FreshStartValidated，符合 §8.7 | NO_CONTEXT（新 cycle） | 保留舊成功 record，從安全來源走新流程 |
| HELD_UNKNOWN | 本地人工 resolution 符合 §8.7 | MANUALLY_RESOLVED / RETIRED_UNRESOLVED / ACCEPTED | 不直接 READY，不隱含重送 |
| MANUALLY_RESOLVED | UserStartNewPurchase + 新鮮安全起點 + 無未決相關效果 | NO_CONTEXT（新 cycle） | 明確新任務，非自動 retry |
| RETIRED_UNRESOLVED | 普通任何事件 | 保持或 CLOSED | unresolved blocker 不因 retire 消失 |
| RETIRED_UNRESOLVED | 原 attempt 的可信遲到成功 | ACCEPTED | 記錄成功、不重新派發 |
| RETIRED_UNRESOLVED | 原 attempt 的可信拒絕或明確人工 resolution | MANUALLY_RESOLVED | 退休後不自動恢復購票；新任務仍需正式事件 |
| MANUALLY_RESOLVED | 與人工回報矛盾的可信原 attempt evidence | ACCEPTED 或 HELD_UNKNOWN(review_conflict) | 事實優先更新原記錄；不默默繼續新自動副作用 |
| CLOSED | 晚到結果／新 tick | CLOSED | 可歸檔原結果或 exact cleanup；不能復活 |

`BLOCKED_AUTH` 的 credential revision 表示輸入來源確實變更，不等於認證已成功；`AuthEpoch` 只在已有可信 session 邊界更新。舊 attempt 已明確 auth-rejected 才可用更正後的憑證重規劃。**只要舊 submit 仍未知，換 credentials 也不解除它。**

### 8.4 Conflict recovery 子階段

`ConflictRecovery` 必有 parent rejected attempt、RecoveryId、來源文件、page kind、清除／reload 子 operation id、deadline、最後已驗證子步驟。

| 子階段 | 完成事件 | 失敗／不明 |
|---|---|---|
| DIALOG_PENDING | exact dialog dismiss＋close evidence | 保留 blocker；不 dismiss 不明新彈窗 |
| CLEAR_PENDING | arbiter grant 相同 recovery 的 clear | denied/defer 保持，不另建第二次 clear |
| AWAITING_CLEAR | audited clear 或確定無外部請求、且原契約允許用 reload 重建 | RELOAD_REQUESTED；不能直接 submit |
| AWAITING_CLEAR | local/transport 不明、可能有未明外部請求 | HELD_UNKNOWN(recovery)，不能猜測安全 reload |
| RELOAD_REQUESTED | 得到屬於此 recovery 的 ReloadHandle | AWAITING_NEW_DOCUMENT |
| AWAITING_NEW_DOCUMENT | exact handle completed、fresh document/tenant/route 可驗證 | recovery DONE → SELECTING |
| 任意 | expiry、context/ownership 無法歸屬 | HELD_UNKNOWN(recovery)；不永遠擺在 recovering 假裝工作 |

reserved seat 清 native selection/counters/payload；general/server quantity 清相符欄位。清除不是另發購買。原已知拒絕記錄在 clear/reload 之前建立；普通 scheduled reload 不可代領完成事件。

### 8.5 Input/CAPTCHA first-class 契約

本節只明確化現有能力的生命週期，不新增辨識演算法、網站驗證規避或額外請求頻率。

| 型別 | 欄位／責任 |
|---|---|
| `InputContext` | TabKey/DocumentKey/PurchaseCycleId、selection revision、credential revision、input revision、輸入/auto-submit policy |
| `CaptchaObservation` | 欄位存在/唯一/可用、image element fingerprint、來源／完成載入 evidence、opaque image token、可驗證的 challenge/load revision、manual value readiness（不回傳答案到 log） |
| `CaptchaAttempt` | InputAttemptId、來源 context/image token、provider/model revision、deadline、狀態；不把答案放長期 state |
| `CaptchaResetAttempt` | ResetId、before token、來源 document、觸發原因、是否實際呼叫、匹配載入 evidence、deadline |
| `InputWriteReceipt` | exact input attempt/context、APPLIED/STALE/REJECTED/UNKNOWN、native-setter 結果、回讀一致性、必要框架 readiness；不含明碼 |
| `InputReadyEvidence` | 新鮮 context/input revision、selection 關聯、field audit 結果；不作跨 document 永久授權 |
| `RecognitionProvider` port | 既有 OCR provider 的有限服務，接受短生命週期 image material/context，回 typed recognition result；沒有 state/dispatch 權 |

`CaptchaObservation` 的 image token 不是伺服器接受證明。相同 URL 不代表同一張挑戰圖；相同像素 hash 也不證明沒有重新產生挑戰。source/document/fingerprint/load evidence 必須一起使用；不能把每次 Python read 產生的新 UUID 當「圖片已更新」。

刷新成功要有 matched reset/load evidence，或已驗證的 token 更新契約。只有點擊成功、sleep 結束、`.complete == true` 或空欄位，都不夠。若頁面不提供更強 challenge identity，明列目前 evidence 的能力與限制，不宣稱能辨識所有 server-side token 變更。

### 8.6 Input/CAPTCHA 子狀態完整轉移

InputState 歸 purchase 所有。認知用名稱可映射為少數 tagged variants，不要求每一列都新建 class。

| InputState | 事件／條件 | 下一狀態／操作 |
|---|---|---|
| UNOBSERVED | 同次 page observation 提供 inputs | NOT_REQUIRED / WAITING_IMAGE / WAITING_MANUAL / RECOGNIZING / READY |
| UNOBSERVED | 欄位缺失但頁型明定需驗證 | 保持／INPUT_BLOCKED，不准以缺圖當免驗證 |
| NOT_REQUIRED | 該頁契約明示不需要此輸入 | READY evidence；不能擴大到未支援頁型 |
| WAITING_IMAGE | matching image load 已 ready | RECOGNIZING（OCR enabled）或 WAITING_MANUAL |
| WAITING_IMAGE | timeout/failed load | INPUT_BLOCKED；不無限刷新圖 |
| RECOGNIZING | provider result 與 context/token/input revision 相符、格式有效 | WRITING proposal |
| RECOGNIZING | provider late/stale/context switched | 丟棄 ephemeral answer，回 UNOBSERVED；不能寫新頁 |
| RECOGNIZING | engine/provider unavailable | WAITING_MANUAL 或 INPUT_BLOCKED，依現行政策呈現 |
| RECOGNIZING | invalid output length/明確需重取圖 | RESET_PREPARED（只按既有重試政策），不可直接 submit |
| WAITING_MANUAL | 使用者輸入達到頁面格式且 live audit通過 | READY，但 auto-submit policy 仍獨立限制 |
| WRITING | arbiter grant、native setter 寫入與回讀一致 | READY；明碼材料釋放 |
| WRITING | NO_EFFECT stale／target不存在 | UNOBSERVED 或 INPUT_BLOCKED，不繼續 submit |
| WRITING | partial mutation／unknown | INPUT_BLOCKED／RECOVERING_INPUT；不當 ready |
| READY | image/input/document/credential/selection relevant revision 改變 | UNOBSERVED；舊 ready evidence 失效 |
| READY | submit 已 dispatch | CONSUMED；原答案不給第二個自動 submit 使用 |
| CONSUMED | 明確 CAPTCHA rejection | RESET_PREPARED，InputRecovery 隸屬原 rejected submit |
| RESET_PREPARED | arbiter grant matching ResetId | RESET_AWAITING；最多送出一次原生 reset |
| RESET_AWAITING | matched fresh image/load evidence | WAITING_IMAGE 或 UNOBSERVED，再建立新的 InputAttempt |
| RESET_AWAITING | definite NO_EFFECT | 可有界重新規劃；保持原 retry budget/deadline |
| RESET_AWAITING | REQUEST_POSSIBLE／到期無證據 | INPUT_BLOCKED；不重點、不用舊圖結果 |
| INPUT_BLOCKED | 新的可信輸入／人工核對／匹配事件 | UNOBSERVED 或依 parent purchase保持 HELD；不單靠 tick 解鎖 |
| 任意 | pause/stop | 不新建寫入／reset；已有結果可只讀歸納／清理 |

provider/model instance 冷啟動時建立／重用；不可每輪重新載入模型。需 offload 同步運算時量測 event-loop lag，並設並行上限；取消 future 不等於底層 thread 已停止，late result 仍需 stamp 檢查。答案與 image bytes 只短暫持有，不寫入 state history、trace、webhook 或 crash artifact。

輸入與 selection 可在能證明獨立時流水化；**同 tab 的填值/reset/selection mutation 仍須 arbiter grant**。不能為了省時間同時寫互相影響的表單欄位。

### 8.7 人工解決、retire 與新任務

必須提供一個小型、本地、使用者明確操作的恢復控制流程（現行設定 UI 或 CLI 二選一作正式入口），不是無認證的通用 HTTP `reset=true`。工具本身不能替使用者聲稱已核對網站結果。

`ReviewTarget` 是 `AttemptRef` 或 `StartupContextRef`：後者只識別本機觀察到的來源不明 context，不假造上一程序的 SubmitAttemptId、伺服器交易 ID 或成功 evidence。所有人工事件都核對同一 review target。

正式事件：

| 事件 | 前置／效果 |
|---|---|
| `RequestManualReview(expected_cycle, review_target)` | 暫停新 mutation，顯示具體未決原因與需核對的購物車／訂單；不暴露秘密 |
| `ManualResolutionRecorded(review_target, disposition, provenance)` | 只能由本地使用者明示；disposition 為 reported-accepted、reported-not-accepted/cancelled、unresolved-abandon；保留 USER_REPORTED 與實際 server evidence 的區別 |
| `RetirePurchase(expected_cycle, resolution_id)` | 退休此 cycle；已知解決進 MANUALLY_RESOLVED/ACCEPTED；unresolved-abandon 進 RETIRED_UNRESOLVED，不清 blocker |
| `UserStartNewPurchase(expected_retired_cycle, fresh_start)` | 沒有相關未決外部效果、原操作已解決、停止舊 mutation、新起點經驗證，才建立新 cycle；不能同文件直接 READY |

人工 resolution 不能把仍在實際執行的 mutation task／可能稍後自行送出的 callback 直接放行；先關閉原執行權與有界收尾。原 operation 之後仍可能收到站點結果，必須歸原記錄；新的任務與舊 scope 關聯不明時保持人工 gate。新任務不從原提交頁直接重按。

reported-not-accepted 是使用者判斷，不是機器證明；畫面需說明誤判可能造成重複購買。預設流程不接受只有「重新開始」按鈕就抹掉 unknown；未選明確 resolution、只 abandon 者不得自動接續相同 scope。

ACCEPTED 遇到新活動/key/auth 不自動開新 cycle；使用者明確指定新活動且從可信安全起點開始，才允許。BLOCKED_QUOTA 不因改帳號欄位自動再嘗試；新任務不得以繞過站點限額為目的。

跨崩潰沒有持久化購票 ledger 的前提不變：重啟不能證明前次沒有送出。第一版在接回來源不明購票頁／使用已有瀏覽器購票 context 時要求人工確認；新 session 的正常合法來源導航不每步再問。單靠從首頁重新啟動也不能提供跨崩潰 exactly-once 保證，發布說明須明示此限制。

### 8.8 最終 submit 與現行設定語意

基準語意維持：OCR disabled 不新增自動送出；OCR enabled 但 force-submit disabled 可準備輸入但不自動 submit。新流程不把「欄位長度對」當成突破原設定的權限。

最後同次 browser commit 核對 current document/route/必要 IDs/selection payload/input readiness/image evidence/可見 dialog，使用已 grant 的 claim，只點一次。native setter 必須依 §4.4 的唯一現行來源，檢查回傳並回讀；語法/例外/回讀失敗不可送出。[R12/R20]

`commit_and_submit` 只在頁面契約確實同步完成時融合；如果原生事件發起 async 更新，精確回傳 LOCAL_MUTATION/未送出，等待 matching evidence 再繼續。融合是共用原語的組裝，不是另造快速引擎。

### 8.9 必要測試

每個主狀態至少有入站、合法出站、拒絕出站／保持、pause/close、late receipt 案例。T01–T12/I01–I12 涵蓋完整出口與輸入。人工控制測試包含錯 cycle/attempt、重複人工事件、未選 disposition、reported outcome 與實證矛盾、強制關閉後不得復活。

---

## 9. 刷新與 429 恢復狀態機

### 9.1 保留現有 SQLite 核心

既有 `SharedRefreshCoordinator` 及其交易／FIFO／permit／receipt／lease 不重寫。[R11] 新增的 coordinator adapter 只轉換型別及縮小接口，不保存 mirror queue、不改成記憶體鎖，也不把同一台機器的多程序拆成不同節流 scope。

`UTK_SPORTS_SCOPE = 'utk-sports'` 是共享協議識別，不是舊棒球相容層。預設保留；必要的既有 coordinator → port 映射由 ADR-01 證明需要的模組外 current-core service provider 實作；新 bootstrap 注入該 port，不預設 integrations 目錄。

### 9.2 Request identity 與 reason 分離

每一個 reload intent 有非空 typed reason、不可重用 OperationId、owner stamp、來源 document 及 deadline。**reason 只用來表達原因，不是 caller identity。**

建議接口：

```text
request_reload(intent) → Accepted(handle) / ExistingSameIntent(handle) / Busy(active_id)
observe_reload(handle) → Pending / Completed(document) / HeldUnknown / Cancelled
```

重複 tick 可拿回同一個 handle；不同 caller 不能只因 reason 相同就消費別人的 completion。不同 reason 也不能偷偷改寫已 committed request 的 deadline 或歸屬。

### 9.3 轉移規則

| 狀態 | 可推進動作 | 暫停／取消語意 |
|---|---|---|
| IDLE | 建立 request | 沒有共享資源 |
| QUEUED | 等 eligibility，向 coordinator reserve | 暫停保存 token／原 deadline；釋放已入列的共享工作 |
| RESERVED | fresh preflight、final gate | 可 exact release；只釋放所持 reservation |
| COMMITTING_PERMIT | coordinator commit | 結果未明時不能假設未提交；保留 receipt reconciliation |
| PERMIT_COMMITTED | 一次 browser reload claim | 不可再次花相同 permit 或發起平行 request |
| AWAITING_DOCUMENT | 只觀察 source 之外的新 canonical document | 不重送；對已在途 probe 維持有界 ownership |
| COMPLETED | 向對應 handle 提交一次 completion | 釋放相符 probe／task，非購票狀態全清 |
| HELD_UNKNOWN | 呈現原因、有限觀察／人工恢復 | 不無限重試、不回到 QUEUED 假裝新請求 |
| CANCELLED | 清理未提交共享資源 | token 被退休，不給其他 intent 使用 |

### 9.4 特別區分兩種 UNKNOWN

**A. coordinator commit 結果未知，但 browser reload 明確尚未呼叫。**

此時不應假裝正在等一個已發送的 reload document。保存 `permit_commit_unknown` 與 `browser_not_attempted` evidence，查核 exact receipt／owner；只有能證明目前授權與單次 dispatch claim 仍有效時才繼續。

如既有 coordinator 無對應查詢介面，P5 可增加最小的 read-only receipt 查核；不得猜測資料庫內部狀態。查核失敗就進有界 HELD_UNKNOWN，而不是任意重 reserve。

**B. browser reload 已呼叫，結果／document 尚不明。**

保留在途 intent，不再呼叫 reload。只有實際新 document 或人工處理能解除；timeout 只是停止自動推進，不代表請求沒到。

這兩種情境要在 result 型別與測試中分開，不能一律以布林 False 處理。

### 9.5 Pause／resume

- queued／reserved 暫停：釋放共享工作；保存原 OperationId、eligibility、deadline 與 intent。
- resume：只有尚未提交的 intent 可重新入列；剩餘間隔自然流逝，不從現在重新加一輪。
- completed／awaiting-document 不可回捲成 queued。
- 已 committed 的 recovery probe 可能仍在途；不可當成未發送 probe 立即釋放並允許另一程序發出平行探測。允許只讀觀察與有界 renewal，停止新的購票副作用。
- 停止／關閉時 local task 必須結束；無法確認在途效果時靠有界 lease 與 conservative blocker，而不是無限續租。

### 9.6 限流與不明頁面

只有足以支持 429 的 evidence 才開共享 incident。unknown、空白、CDP exception、Cloudflare challenge 不等於 429。

原有 emergency marker 是協調器報告失敗時的跨程序保護，不是 runtime fallback legacy；在具體證據證明可以替代前保留。其路徑、fsync、cleanup 屬 adapter／infrastructure，不進純 reducer。

### 9.7 停用共享協調器

不是改走另一套 reload engine。仍使用同一 request／phase／document gate，只由 adapter 提供「本機保護的 permit」。本機 interval、pause、deadline、未知結果不重送仍成立。

若等待期間重新啟用 coordinator，尚未送出的本機 request 回到受協調的授權流程，不偷送最後一次無協調 reload。

### 9.8 Probe 與 cleanup

lease task 只回報 renewed／expired／lost，不能自行把 runtime 字典改成 None。cleanup 使用 exact permit／request token 並有 retry backoff。

達到 lifecycle limit 後停止 renewal／task；把失敗結果回報一次，資源不能因重複 tick 被無限制延長。

---

### 9.9 跨 reducer 授權與所有 terminal 出口

建立 ReloadIntent／排入本地等待不等於准許改頁。queued/reserved 只有經 session arbiter 才能前往 permit commit；所有 caller（startup/scheduled/health/recovery）也受同一規則。

| Refresh 狀態／事件 | 必須結果 |
|---|---|
| QUEUED/RESERVED + ready purchase、尚未開始 commit | 退休普通刷新 dispatch 權、exact release；購票不用為非破壞性 cleanup 多等一輪 |
| COMMITTING_PERMIT + competing purchase | 不搶佔未知 commit；以 exact receipt 歸納，不能把取消當作未提交 |
| permit 已確認、browser 尚未呼叫，但當前 gate 不准 reload | 不為已花 permit 而強制 reload；記明未發送、做適用 lease cleanup，不倒退同 permit 重試 |
| AWAITING_DOCUMENT + 相符新 document/正常觀察 | COMPLETED，只回傳給 exact ReloadHandle；供 parent recovery 歸納 |
| COMPLETED 被 owner 消費 | 記一次 terminal receipt 後回 IDLE；舊 handle 再查仍是 terminal，不配給別人 |
| CANCELLED 且 exact cleanup 完成 | 回 IDLE，token 永久退休；晚到 reserve 結果只能 cleanup |
| HELD_UNKNOWN + 可歸屬的延遲 document/receipt | 只依匹配證據完成／取消；不建立第二次 browser reload |
| HELD_UNKNOWN + 無新證據／新普通 request | 保持 blocked；人工／close 依 §8/12 的 lifecycle，不用 timeout 解鎖 |
| 任意 + STOP/CLOSE | 退休未發送工作、有界 cleanup；未決外部效果不冒充完成 |

`BLOCKED_BY_UNRESOLVED_PURCHASE` 是 session 仲裁結果，不是 HTTP 429。可以將明確 rate-limit incident 報告給共享 coordinator，但不能因此刷新正在持有未決 submit 的 tab。

調用 coordinator 的異步工作只回傳 receipt，不直接呼叫 browser。claim 是否有效的判斷與 browser reload dispatch 在同一標準 effect 路徑，不讓 lease task、startup helper 或 core fallback 私自重送。

---

## 10. Browser ports、輸入與 JavaScript 資源

### 10.1 方法是能力契約，不是每輪查詢清單

```text
observe_page(expected_context, fields_mask) → PageObservation
wait_until_actionable(expected_context, deadline) → PageObservation
commit_date / commit_area / commit_seats / commit_quantity(plan, claim) → EffectReceipt
read_navigation_marker / clear_navigation_marker(expected, claim)
prefetch_area(validated_target, context) → typed result
read_destination(expected_context) → PageObservation
observe_inputs(context) → InputObservation
read_captcha_material(context, expected_image) → CaptchaMaterialResult
write_inputs(input_plan, claim) → InputWriteReceipt
reset_captcha(reset_attempt, claim) → ResetDispatchReceipt
wait_input_ready(context, deadline) → InputObservation
dismiss_dialog(exact_observation, claim) → EffectReceipt
dispatch_submit(plan, input_evidence, claim) → EffectReceipt
commit_and_submit(plan, input_evidence, claim) → EffectReceipt
dispatch_reload(reload_intent, claim) → EffectReceipt
RecognitionProvider.recognize(material, attempt_context) → RecognitionResult
```

page observation 已取得的 inputs/dialog/identity 可共用，不把上表逐個 await。需要重新核對的 live 值放在最終 commit 當下，同次執行；不能為型別分層多加相同 CDP read。

Application 不持有 unrestricted evaluate/selector/arbitrary-navigation port。具體 adapter 只接受經驗證的 data/claim；所有 mutation 都回報實際效果 evidence，不能碰到例外就回 NO_EFFECT。

### 10.2 來源、寫入與外部契約

登入與驗證碼輸入均使用現行 native setter 契約及回讀。[R12/R20] image capture 的既有原生/canvas或受支持替代路徑可封裝為同一 current capability；它們是瀏覽器能力差異，不是歷史 engine fallback。不得因清理而暗刪已支援的輸入方式，也不得新增驗證規避演算法。

將 setter fragment 冷路徑注入 assembler，不在 domain import 大型核心模組，不在每次填值時重新讀檔/hash。秘密資料只在 provider/adapter 短時使用，typed state 只保留 revision/結果而不保留答案。

### 10.3 Script assembly 與時序

按固定 operation enum 載入本地可信 scripts，組成 IIFE，payload 只用 JSON encoding。沒有可執行 remote script、使用者自訂檔名、window-global helper 或多版 decoder。

common/document/input/selection/submit 原語只有一份來源；融合 operation 與分步 operation 共用它。Python 的純 plan 檢查與瀏覽器即時核對服務不同時點，不能以 DRY 為由刪 live audit。

未 ready 才安裝 bounded observer/waiter。browser waiter 自己有 deadline/cleanup，Python cancellation 不保證 page Promise 自動清理。observer 的 property-only 缺漏依 fixture 證明所需補充輪詢；ready 立即返回，不為每個 await 固定 sleep。

### 10.4 外部化的同階段打包義務

**第一次把已被 production 使用的 JS 移到資源檔時，同一個可合併變更單位就必須完成：**

- 所有會到達此程式的 spec/build entry 的 datas/module-relative resource 更新。
- resource manifest/digest 與 assembler 的無網路 frozen self-test。
- bot/settings 的 assembled distribution 驗證，不只各自 dist；合併 `_internal` 後重新檢查資源一致。
- 從與 source tree 無關的 cwd 執行 frozen binary；缺檔、錯 digest、錯 path 一律非零退出。
- 在適用的正式 OS/architecture 實際 build 與自測。沒有環境就標該階段阻擋，不等 P8 才補。

資源檔不會只因新增 Python import 就自動獲得正確的打包路徑；使用 spec datas 並在成品核對。[E06] scripts 外部化先保留 guard/selector順序；效率合併放後續獨立 commit。

### 10.5 Fake 與 production script 的分界

contract fake 實作 typed port，不用 script 字串猜目前操作；browser fixture 執行同一份 production assembler，不能把測試用 setter/guard 代替正在驗證的 production body。

hard-isolated production-origin job 與 loopback DOM-shape job 分開命名，只有前者可證明 route/origin contract 沒被 patch。二者都不能聲稱真站網站現況已驗證。

---

## 11. 唯一設定格式與明確升級判定

### 11.1 ADR-02 的確定選擇

**本版提案選擇嚴格一次性升級：`utiki` 與 `ctbc` mirror 即使值完全相同，也不由正式 loader 接受後順便清理。先用獨立 upgrader 移除舊 section，再啟動新版 uTiki。**

理由：使用者要求正常使用路徑不含歷史解碼/雙讀/雙寫；基準 migrator 本來就會建立 mirror，[R07] 因此對從該基準升級的使用者，converter 是主要交付路徑，不是規格中可省略的假設。

不強加 `schema_version=2`。這一版 current 契約可沿用既有 `utiki.ticket_type_keyword/seat_sorting/seat_selection_mode` 三鍵，只接受該組正式表示；如果 P0 證明資料語意另需改版，必須先修 ADR/測試/升級規格，不能留多版 decoder。

### 11.2 格式分類與行為矩陣

分類由邊界決定；runtime 判定 forbidden legacy section 時不必解析其舊值。**比較 mixed 是否 equal/conflict 是 upgrader 的工作，不是每次載入的工作。**

| 輸入狀況 | uTiki 正式 load/save/hot reload | 獨立 upgrader |
|---|---|---|
| canonical-only，三鍵完整合法，無 `ctbc` 行為 section | 直接驗證使用；無需轉換、無需假造新版號 | 驗證後 no-op，輸出至新檔時僅正規寫入 |
| canonical-only 但缺必需鍵／型別錯／值不合法 | 明確 validation error，不默補舊值或整份 defaults | 只有有文件依據的缺鍵補值可做；不合類型拒絕 |
| legacy-only `ctbc` | UPGRADE_REQUIRED，不能以新 profile 解讀 | allowlist 三鍵搬到 canonical，缺鍵依明示轉換表補值 |
| mixed equal：兩 section 三鍵值及型別相同 | UPGRADE_REQUIRED；不在 save 時偷偷刪 mirror | 保留 canonical 值，移除 mirror；不需要 conflict option |
| mixed complementary：某鍵只在一側 | UPGRADE_REQUIRED | 用存在的合法值；缺兩側者依明示 default；不覆蓋顯式空字串 |
| mixed conflict：任一同名有效鍵不同 | UPGRADE_REQUIRED，不在 runtime 選邊 | 預設錯誤；使用者明確 `--conflict-policy canonical/legacy` 才選邊 |
| 任一舊 section 有未知鍵／疑似擺錯的秘密 | UPGRADE_REQUIRED，不顯示值 | 拒絕破壞性轉換，列鍵名/原因；不默刪未知欄位 |
| duplicate JSON key／root 非 object／全檔毀損 | common config error；不以 defaults覆蓋 | 拒絕且來源不變 |
| 明確建立全新 profile／無來源檔 | 建 current defaults | 非轉換，不碰任何舊檔 |

正式 defaults 不產生 `ctbc` 行為 section。真正 `accounts.ctbc_*` 是現行 tenant 憑證，保持原值與唯一來源，不屬本表的 legacy mirror。

### 11.3 不傷害未使用 uTiki 的其他平台

全檔 JSON parse 與「本次啟用平台」validation 分開。其他平台 profile 中未使用的舊棒球 section 可作 opaque data 原樣保留，**不解碼、不轉換、不供 uTiki 使用**。修改其他平台自身設定不得順便丟掉 opaque sections。

一旦要啟動 uTiki、保存 uTiki 行為、從 UI 切成 uTiki 或啟用其 worker，就需通過唯一 current schema。不能先讓通用 migrate_config 補 defaults/鏡射，再聲稱格式已有效。uTiki專屬保存只接受 canonical；無關平台保存原樣保留未使用 opaque fields，不是假裝已完成 uTiki migration。

UI 應顯示 upgrade-required 與獨立工具使用方式，不能繼續顯示新預設讓使用者以為舊選擇已保存。共用讀取其他平台不因一個未啟用 uTiki 舊欄位整個故障。

### 11.4 當前型別、UI 與 hot reload

current validator 建立 immutable policy；engine 不接完整 raw config/accounts/webhook。UI 只讀寫 current 行為，新自訂元素/helper命名不用保留舊 ID bridge。options/default 由單一 metadata 或 parity tests 保護；後端獨立驗證。

只在成功載入且內容改變時編譯 keyword/revision。invalid hot reload 保留最後有效資料以供結果歸屬，但撤銷新 uTiki 購票/普通刷新/輸入 mutation 的授權；必要只讀結果與 cleanup仍可做。不能繼續用舊 policy 偷跑新操作，也不能清掉已發送 attempt。

| 變更 | 處理 |
|---|---|
| keyword/張數/排序/相鄰 | 未提交 plan失效；已送出按原context追蹤 |
| manual/computer | 未送出重新規劃；在途導航先確認，不中途換目的地 |
| interval/cooldown | 新 intent採新值；舊 deadline不延長，committed不回捲 |
| credential source replacement | 更改 CredentialRevision；AuthEpoch只在可信邊界更新；未決submit仍blocked |
| 通知/顯示 | 不重送購票；通知自身狀態獨立 |
| legacy/mixed/invalid | validation拒絕並呈現原因；不自動轉換或fallback |

---

## 12. 主迴圈接合與上游影響控制

### 12.1 直接新入口，只有必要的外部服務映射

依 ADR-01 預設以 `platforms.utiki.bootstrap.create_engine(...)` 建唯一 instance。主程式只修改當初的自訂 uTiki import/route/control接點，不全面重排其餘平台。bootstrap 是新 factory，不保留 nodriver_ctbc_* alias，也不 import 舊型別。

若目前 coordinator、notification、credential 或 native-setter 常數必須映射到新ports，在模組外以最小 services接合實作並登錄 caller/version/reason。能直接注入已有符合契約的 callable就不再包一層。不要為一個平台預設多層 integrations目錄和全平台scheduler。

### 12.2 長工作與控制來源分離

新uTiki worker的I/O/ready事件回來後立即接續，不返回50ms主路由排隊；未遷移平台維持原handler。不全域覆寫sleep或增加兩套購票引擎。

主控制迴圈只進行短接合，不長await整個purchase工作。共用pause/stop/settings來源仍然唯一，以帶revision的memory gate傳播；不在每個effect前重新讀檔。同event loop的同步阻塞仍須量測，不因用了task就宣稱控制即時。

### 12.3 Gate與仲裁的結合

| Gate | 可做 | 不可做 |
|---|---|---|
| PASSIVE/challenge-owned | matched completion、只讀必要觀察、exact cleanup | uTiki自動選擇/submit/reset/reload |
| PAUSED | 只讀結果、必要有界lease/cleanup | 新purchase/navigation/input mutation/普通refresh |
| HEALTH_ONLY | document觀察、依協議且沒有unresolved barrier的health recovery | 開賣前購票 |
| PURCHASE_ALLOWED | 符合§6 eligibility的當前步驟 | 繞過ActiveEffect/blocker或用舊控制revision |
| CLOSING | revoke/有界清理/結果分類 | 新intent、自動復活session |

gate是必要但不充分的條件：PURCHASE_ALLOWED也不能越過submit HELD_UNKNOWN。document/route change使控制證據失效時先撤銷新claim，再根據新頁觀察更新；不是attach通過就永久允許。

### 12.4 核心副作用也要進inventory

startup、refresh_datetime、429/recovery、browser reset與challenge處理中會click/reload/replace document的路徑，必須逐一列入 `core-effect-inventory.md`。對uTiki持有的tab：

- `request_reload` 必須經engine，回精確handle/BlockedByUnresolved/Deferred，不呼叫裸tab.reload。
- 外層要接管文件先revoke/handoff；可能破壞未決submit evidence時不得自動執行。只讀detector不等於能自動dismiss/refresh。
- 明確使用者stop/force-close可終止browser，但須保留未知結果報告，不宣稱撤銷遠端操作。
- handoff後晚到callback只能處理原operation/cleanup，不再取得新文件權限。

不能只補三個reducer的仲裁，卻保留主程式另一條直接reload旁路。這是X16/U09的必要測試。

### 12.5 未來其他平台接入

本版不要求其他真平台改內部狀態機，也不預先建立GenericWorkflow。所有控制／接入接口避免洩漏舊棒球模型；使用假current-core host測試bootstrap/ports。若後續有第二個實際平台需要共用session lifecycle，再依ADR抽取已證實共通的部分。

一個假平台測試可證明某個中立接口能接另一個實作，但不能作為提前建立三層framework的唯一收益。選A時U01以「核心測試host經新接口接入、無舊棒球符號」驗收；只有採B才要求獨立的非uTiki runtime interface測試。

### 12.6 清理與異常邊界

finally涵蓋URL讀取、gate、health、handler與driver異常，不為加finally搬動整個主檔。關閉順序：lookup不allocate → closing/revoke/generation退役 → 停止owned tasks並有界join → exact資源清理/未知結果保留 → 釋放tab/callback → registry回收。

checkout是業務ACCEPTED/通知階段，不是立即抹掉session。真正離站/close才結束；未知submit即使手動導航也先保持未決分類。上游新增gate或變更helper後，worker也必須受新語意約束，不能只依文字merge是否衝突判斷。

---

## 13. 可觀測性、通知與記憶體界線

### 13.1 顯示 state，不只印「失敗」

提供不含秘密的 `RuntimeStatus`：tenant、page kind、Purchase/Input phase、ActiveEffect owner/階段、reason code、OperationId 的短表示、被哪個 barrier 阻擋、等待條件、是否需要人工處理與下次合理觀察時間。不得在 repr/log 中包含答案、圖像或憑證明碼。

log 記錄 state transition 與重要 effect 結果；同一條等待訊息節流，不每 50 ms 印一次。ready→dispatch 路徑不等待 console、磁碟或通知 HTTP；一般 trace 用有界緩衝與非阻塞 sink，詳細 trace 僅按需啟用並量測其額外成本。不要把整個 snapshot、native payload、DOM 或 exception args 直接寫入 log。

### 13.2 通知不能以「先標記就當作成功」處理

通知有自己的 `PENDING / ATTEMPTED / DELIVERED / FAILED / UNKNOWN` 記錄，與購票成功分開。若現有通知 helper 不回傳可靠 delivery receipt，就只能標記 attempted，不能假稱 delivered。

HTTP outcome unknown 是否重試，依通知 policy 明定；重試不會觸發重新購票。去重 key 使用 submit operation／已確認 checkout context，不用易變的完整 URL。

這裡不建立新的通知系統；adapter 窄化現有 Discord／Telegram／音效能力即可。通知在結果確認後排入獨立、有界傳輸工作，不得同步阻塞別的 tab 的選票。

### 13.3 資源與資料保留

- active／unknown intent 與其 ActiveEffect ownership 不可因任意 TTL、callback 返回或 history eviction 而恢復自動送出權限。
- 可回收的是已完成且不再需要的 observation、舊 plan 與 task handles。
- 有界 history 只存脫敏 transition，不存完整 page／image／Cookie。
- browser port 可在 session 活著時持有 tab；close 後應釋放，不能以「永不保留 tab reference」這種不切實際規則取代 lifecycle。
- weakref 不是 ownership 解法；必要時可輔助測試，但不能靠 GC 正確性維持去重。

---

## 14. 零歷史相容與可交付的獨立 frozen 升級

### 14.1 正式 runtime 最終必須移除

`platforms/ctbc.py` facade、舊API/alias、as_legacy_dict/profile projection、舊snapshot decoder、舊workflow/committer/runtime、`ctbc`行為fallback/mirror、舊UI ID bridge/private test bridge、失效hiddenimports。自訂caller同步更新，不能因還有舊測試就保留wrapper。

把它們移到bootstrap、settings、integrations、util仍是歷史相容。基準/Git/離線升級工具中為重現舊資料而存在的測試資料，不可被正式bot/settings import或打包為可達fallback。

### 14.2 現行核心適配例外

保留適配必須有四項證據：目前核心來源版本與真實caller；刪除舊棒球後仍獨立存在的需求；較小直接新接點不能滿足的原因；無舊模型/state/邏輯與無用途延遲的測試。

寫入core-integration-inventory，只許精確symbol/path例外，不放行整個資料夾。真正CTBC域名/credential keys/Cookie名稱/共享`utk-sports`scope與當前browser/coordinator ports不是歷史相容。

新bootstrap factory不是例外shim，直接作新架構的一部分。真正為現行核心資料形狀做的適配放模組外；不要求為了目錄純潔新增沒有用途的轉送層。

### 14.3 升級工具是本次公開升級的必要交付

基準會產生mixed mirror。[R07] 本版§11選擇reject mixed equal，因此**提供從基準升級的正式release，就必須提供轉換工具；不得以「可重建profile」替代產品的主要升級路徑。**

對一個確實全新或已canonical-only的本機安裝，可記錄該次「不需轉換」。這只免除該次使用，不免除正式發布需附frozen upgrader和升級測試。

工具不啟動browser、不連售票網站、不import engine、不由loader/hot reload自動呼叫。可以import唯一current-config純validator；依賴方向只從upgrader到current規格，不能反向可達。

### 14.4 Source converter 規格

唯一conversion body在 `tools/migrations/convert_utiki_config.py` 或其獨立工具package，由source CLI和frozen CLI共用。不得在bot/settings內複製另一份升級函式。

預設dry-run或輸出新檔。明確in-place才覆寫，且強制備份。接受明確檔案/manifest，不掃全磁碟。

1. strict JSON：重複key、root/type/future schema不合規拒絕，不把bool當int。
2. mixed equal按§11直接保留canonical三鍵、移除mirror；不是錯誤，也不需要人工選邊。
3. mixed conflict預設拒絕，只有explicit canonical/legacy選項允許選邊；空字串是值不是missing。
4. unknown legacy欄位不默刪；選定來源欄位型別不合法時，不用conflict policy掩飾。
5. legacy-only/complementary缺鍵補值有逐鍵書面表；本版維持keyword=`全票`、sorting=`row_seat`、mode=`manual`的基準defaults前須在P0核對固定源。顯式空keyword保留。
6. accounts與非uTiki資料的解析後值完全相同；不trim password、不拷貝秘密到行為section。
7. 讀寫前後核對來源identity/digest，防止dry-run後檔案被其他settings程序改寫；upgrader要求停掉使用該profile的writer。
8. temp與目標同filesystem、flush/fsync/replace；目標已存在預設拒絕，失敗來源不變。備份有適當本機權限且不進Git/artifact。
9. 多profile先全部驗證、再寫入新目錄；manifest記成功/失敗/來源digest/輸出digest；不宣稱多檔原子交易。
10. partial failure不得自動啟動混合設定；明確rollback指引。日誌只寫檔案/鍵名/原因，不寫秘密值。

### 14.5 Frozen upgrader 的正式 artifact

| Target | 獨立artifact（名稱規格） | 內容 |
|---|---|---|
| Windows x64 | `utiki-profile-upgrader-<release>-windows-x64.zip` | `utiki-profile-upgrader.exe`、runtime dependencies（需要時）、升級/回退說明、tool manifest |
| macOS arm64 | `utiki-profile-upgrader-<release>-macos-arm64.zip` | 同名可執行檔或bundle、runtime dependencies、說明、tool manifest |

這個 target 集合沿用 v1.4 記錄的固定 build-release 分支；v1.5 未重新取得原始碼，P0 須核對實際發布矩陣。不宣稱額外支援Intel macOS。若正式target改變，兩種產品與upgrade artifact矩陣一起修訂。[R19]

每個壓縮成品有SHA-256，release附 `SHA256SUMS`；manifest含tool/candidate source SHA、current-config contract digest、OS/arch、runtime版本、測試證據。checksum用於核對傳輸/配對，不冒充發布者簽章；既有簽章/attestation政策另遵循並如實標示。

使用者不需要安裝Python、uv、Node或source tree。`--help`/無參數不能修改任何資料。以獨立spec建置，不合併進bot/settings的 `_internal` 或正常import graph。工具可保留在歷史release供跳版使用者下載，但不是新版模組的常駐依賴。

### 14.6 Frozen 升級端到端驗收

在**沒有可用開發Python/source tree的目標環境**執行，不能只移除PATH就宣稱乾淨：

```text
合成舊profile（包含mixed equal與sentinel secrets）
 → 解壓公開 upgrader artifact
 → 執行frozen dry-run（來源byte不變）
 → frozen轉換到新檔
 → current-config完整驗證；非uTiki欄位保留
 → 解壓同release的bot/settings正式bundle
 → 兩個frozen程式用轉換檔離線self-test並實際啟動
 → settings在loopback讀取/保存仍canonical-only
 → bot以隔離fixture啟動，不對真站請求
 → 回退使用原binary與原設定備份
```

涵蓋G01–G10：equal/conflict/empty/corrupt/no-op、空白與非ASCII路徑、只讀/檔案佔用/replace失敗、多檔部分失敗、錯target artifact、no-Python、秘密不出artifact及bot/settings archive不可達converter。

macOS quarantine/簽章與可執行權限、Windows實際使用者權限都須依release說明驗證；不能「本機Python能跑」就視作frozen交付成功。

### 14.7 最終刪除順序

P6a建立只用新owner的候選，舊檔只可暫存為未被正式入口/候選bundle載入的開發內容；P6b完成指定live smoke和維護者核准；**P6c才最後刪除舊檔/alias/測試bridge與啟用全量retired-symbol gate**。

沒有真站窗口就停在 `READY_FOR_LIVE / DELETE_BLOCKED`，不宣稱legacy已核准刪完，也不為了等窗口發布雙引擎。最終交付仍是單一新模組；開發中的等待不等於允許永久歷史相容。

回退是已驗證的舊binary/commit＋原設定備份配對；不靠新版本持續雙寫，也不假裝撤回已發到網站的請求。

---

## 15. 測試重組與可信度

### 15.1 分層與主要責任

| 測試層 | 真正執行什麼 | 不應拿來證明什麼 |
|---|---|---|
| unit | 純 planner、codec、reducer、policy | 不證明實際 DOM selector 可用 |
| contract | 真 application＋semantic fake port | 不證明 production JS 無語法錯誤 |
| integration | 真 engine/controller、臨時 SQLite／檔案、受控 fake browser | 不證明當前售票站與 fixture 一致 |
| browser | 真 Chromium＋真 zendriver＋production scripts＋脫敏 fixture | 不代替 live tenant 驗證 |
| architecture | import graph、AST 禁用模式、獨立匯入、legacy／resource 邊界 | 不直接保證業務行為正確 |
| live smoke | 明確授權範圍內的真站流程觀察 | 不等於壓測或自動付款許可 |

### 15.2 Semantic fake 的要求

fake 應實作正式 port，接受 typed input、記錄順序／參數／次數、回傳指定 receipt。不得靠 `if 'marker' in script` 猜目前呼叫；也不應手工重演整套 production 狀態機。

每個測試可以安排「某個 I/O 在何時回來」：以 `asyncio.Event` 或可控 pending result 協調，不依賴 `sleep(0.1)` 猜排程。

FakeClock 提供 monotonic 與 wall clock 的獨立輸入；測試可以 advance，但它不偷偷完成 browser effect。FakeIdSource／抽樣輸入讓 intent 與 random 選擇可重現。

### 15.3 舊測試搬遷

建立 `old-test → invariant/scenario → new-test` 對照表；保留行為，不保留舊 API。測試使用新 contract／builder，不用轉接器偽裝成以前的 `_state`。核心適配另測目前核心與新 port，不把舊棒球測試混為核心契約。

不要求一對一保留 test count。原本一個巨型 browser test 可以拆成很多案例；原本重複字串 assert 可以合併。以 invariant／scenario coverage 對照為準。

純 reducer 自然可以驗證公開 state variant；禁止的是測試任意改寫 engine 私有字典來製造看似可達、實際不可達的狀態。

### 15.4 測試隔離

每個測試建立自己的 engine、TaskScope、fake clock、暫存 coordinator DB／settings。fixture teardown 必須確認無未完成 task／未關閉 browser／臨時檔殘留。

不能全域 `patch time.monotonic`、module `_state.clear()` 後就稱為完全隔離。也不能用自動 retry 讓偶發 red 變 green；先保留失敗 seed／event trace。

### 15.5 Stateful 測試

在手寫事件矩陣穩定後，使用 dev-only Hypothesis state machine 生成 pause／resume／observe／close／late completion 序列，檢查固定 invariants。[E05]

- seed 或最小失敗序列可重現並附 artifact。
- model 必須比 production reducer 簡單；不能把同一 reducer 複製成 oracle。
- 先驗三個 reducer 及 purchase 內的 input helper，再驗 §6 collision matrix 與 T/I 轉移矩陣。避免無意義完整笛卡兒積，但不能因此省略跨 reducer 的必要組合。
- 套件版本在 P0 依 Python 3.11 與現有依賴實際驗證後精確固定；不要抄本文未提供的版本號。

---

### 15.6 Typed scope、獨立 oracle 與完成事件故障注入

所有新／重寫 production 模組進 typed manifest；pure test fake 與實際 port 的簽章一致。mypy 不能單獨保證字典內容或 wire identity，仍需 codec/boundary tests。刻意改錯 adapter receipt 或人工事件型別，gate 必須失敗。

新增 arbitration oracle 只描述「哪些 mutations 絕不可共存」「held 時不發破壞性操作」「ready 時不送普通 reload」，不要複製 production arbiter 當預期值。事件排列包括 result 先於 callback、pause 與 grant 交錯、設定改變後舊成功、OCR 工作取消後實際仍回傳等。

manual-resolution fixture 必須透過正式 control 方法發 event，不改私有 state 製造解除；驗證 wrong-attempt、重複事件與 user-reported/server-observed 的區別。

---

## 16. 必要的驗收情境矩陣

以下 case ID 是穩定驗收識別。**必要案例的權威來源是本節／§18.15 的規格，加上獨立核准並固定 digest 的 `ci/utiki-required-cases.json`（required catalog）。** `tests/fixtures/utiki/manifest.json` 僅作 execution manifest，映射 catalog 中的義務到測試與證據，不得定義、刪減或自行放寬 required 集合。

catalog 必須涵蓋本節以及 §18.15 的完整 ID 聯集；F 系列根因編號與附錄 D 的來源編號不屬案例集合。初始固定清單、欄位及變更治理見 §16.11；測試與 execution manifest 同時刪除一例仍必須由 checker 偵測失敗。

### 16.1 資料與選擇

| ID | 情境 | 必須成立 |
|---|---|---|
| D01 | 空候選／無關鍵字命中 | 無購票 side effect；fallback 符合設定 |
| D02 | 多組關鍵字、AND、排除 | 與基準已確認語意一致 |
| D03 | top／bottom／center／random | 同 plan 結果穩定；audit 不再抽樣 |
| D04 | duplicate ID／duplicate query／空 identity | fail closed，不挑第一個冒充唯一 |
| D05 | 欄位 missing／empty／invalid | 結果區分，不把 invalid 當安全 default |
| D06 | NaN／Infinity／bool 偽裝 count | codec 拒絕或明確保守處理 |
| D07 | tuple 改 record 後欄位映射 | 所有既有值、fingerprint、geometry 語意保持 |
| D08 | 庫存三來源不同步 | 各自滿足張數，不要求相等，也不忽略其中一份 |
| D09 | 座位 adjacency／排序 | 維持既有承認的契約及 tie-break |
| D10 | snapshot 後重新 render | commit-time rejection，不按到另一筆 |

### 16.2 導覽與提交

| ID | 情境 | 必須成立 |
|---|---|---|
| N01 | 重複 native date tick | 單一 claim／click |
| N02 | 原生區域／特殊 action | 不繞過原生確認條件 |
| N03 | 正常 fast path | 相同 intent、來源／目的地與 hidden IDs 全部相符 |
| N04 | marker 缺失／畸形／nonce 不同 | 不授權自動提交；exact cleanup |
| N05 | document 改變但 URL 相同 | 重新觀察且正確追蹤 transition |
| N06 | URL 改變但 document 相同 | 不當成 navigation 完成 |
| N07 | 目的地資料錯／仍 loading | 不提前清 pending，不重複 click |
| N08 | returning 的回傳未知 | 不每 tick 重新 location.assign |
| S01 | submit 前 selection 改變 | 無 submit click，回報 stale evidence |
| S02 | submit 已呼叫但 transport timeout | HELD_UNKNOWN，不重送 |
| S03 | 延遲 success／checkout | 歸屬原 attempt，成功只處理一次 |
| S04 | 超過 outcome deadline | 可見人工確認狀態，不無限等待或重送 |
| S05 | 未知／多個 dialog | 不自動 dismiss，不當 conflict |
| S06 | dismiss 失敗或換成另一個 dialog | blocker 仍在，禁止後續副作用 |
| S07 | reserved-seat conflict | 清選擇、audit、paced reload、新 document 後才再試 |
| S08 | general／server-assigned conflict | 使用相符 page contract，不共用錯誤清理方式 |
| S09 | 輸入／驗證失敗恢復 | 沿用既有 policy；沒有新輸入 evidence 不重送 |
| S10 | quota／auth failure | 不盲目重試；auth unknown 不解除 blocker |
| S11 | 改設定／換帳號／回上一頁 | 不抹除未決的提交證據 |
| S12 | 重啟直接進既存購票頁 | 人工核對 gate，不能猜上一程序未提交 |
| S13 | checkout 重複 tick／reload | 通知意圖去重，不自動付款 |

### 16.3 刷新、競態與 lifecycle

| ID | 情境 | 必須成立 |
|---|---|---|
| R01 | queued 多 tick | 同一 request handle，不重新計算 eligibility |
| R02 | queued pause/resume | original deadline／token 保留，間隔未被繞過 |
| R03 | reserved pause | exact release，舊 cleanup 不刪新 reservation |
| R04 | commit 過程 pause／cancel | 區分 permit unknown 與 browser attempted |
| R05 | browser reload 回傳但未見新 document | 不完成、不重送 |
| R06 | 相同 reason 的不同 caller | completion 不互借 |
| R07 | 不同 reason 的 request 競爭 | 明確 Busy／取消規則，不偷偷改 owner |
| R08 | unknown document／Cloudflare | 不觸發共享 429 incident |
| R09 | 真 429 evidence | 共用 cooldown、生效一次、single probe |
| R10 | probe lost／expired／late renewal | 停止正確 task，不維持幽靈 lease |
| R11 | coordinator 暫時失敗 | 保守阻擋與節流 cleanup，不 fallback 快速刷新 |
| R12 | 協調器關閉／等待中重新啟用 | 同 state machine，仍遵守本機間隔及新 gate |
| L01 | 兩分頁互不相同 purchase | state 不相互污染，不使用全域串行鎖 |
| L02 | close 之前 operation 未回傳 | 退休 generation；後到 receipt 只 exact cleanup |
| L03 | 關閉後同 target identifier 再使用 | 新 browser/session generation，不繼承舊權限 |
| L04 | 無 session 時 cleanup | 不配置 runtime |
| L05 | 啟動／URL read／health／handler 任一路徑例外 | finally 清理涵蓋，不僅 handler catch |
| L06 | 長時間重複 attach/close | registry、task、tab 強參照及檔案資源回到基準 |

### 16.4 設定、打包與架構

| ID | 情境 | 必須成立 |
|---|---|---|
| C01 | canonical-only／legacy-only／mixed equal | canonical-only 合規直接使用；legacy-only 與 mixed equal 都須離線轉換，runtime 不比較／轉換 mirror |
| C02 | 離線轉換 mixed conflict／explicit empty | 衝突需明示策略；空值不當 missing；執行期不重新解讀舊欄位 |
| C03 | corrupted JSON／不合目前 schema | 不覆寫、不猜 default；只有 uTiki 不合規時不無故阻擋其他平台 |
| C04 | save fail／replace fail／多檔部分失敗 | 原檔保留，manifest 正確，無混合啟動 |
| C05 | credentials 與其他平台欄位 | 解析後內容相等，log 不洩漏 |
| C06 | hot reload invalid／in-flight policy change | gate／state 規則符合第 11 節 |
| A01 | 無 zendriver 的 subprocess 匯入 domain | 成功且無 I/O side effect |
| A02 | application 加入 evaluate／逆向 import | architecture test 失敗 |
| A03 | 舊棒球 alias／fallback 搬回 module 或 integrations/settings | final legacy scan 失敗；必要 current-core 適配有 ADR/證據，直接新 bootstrap 非 legacy |
| A04 | production JS 或 script resource 故意損壞 | browser／packaging gate 必須失敗 |
| A05 | browser CI 沒選到案例／全部 skip，或測試與 execution manifest 同時刪例 | 依獨立 catalog 核對完整 required 集合，必須失敗；見 §16.11／§17.5 |
| A06 | 修改的 Python 檔不在固定 uTiki 目錄或未被 pytest import | §21.3 從 Git 獨立核對全部實際變更並逐檔 py_compile；任何漏列／缺檔／編譯失敗均非零 |
| P01 | frozen app 從任意 cwd 執行 | JS resource 可取得，無 source-tree 偷渡依賴 |
| P02 | Windows 非 UTF-8 輸出環境 | 不因新增 Unicode／stream 改寫而崩潰 |
| P03 | settings 與 bot 打包 | 使用同一 canonical schema，升級／回退流程可操作 |

---

### 16.5 現行核心接合與零歷史相容

| ID | 情境 | 必須成立 |
|---|---|---|
| U01 | ADR 採用的核心 control/lifecycle 接點 | 以 fake host 驗新 bootstrap 接入與控制；若另採中立公開接口，再以非 uTiki 假平台驗其獨立性，不因此強制建立框架 |
| U02 | completion 剛好落在核心 tick 後 | 已允許且 ready 立即續行，不額外等 50 ms／中央 queue |
| U03 | 未遷移平台的代表路由 | 不需改其購票流程／等待，原核心契約維持 |
| U04 | uTiki 運作時重複 route／離站／換代 | 同 tab 單一 owner，舊 handler 不存在第二條操作路線 |
| U05 | 抽取共用 util 規則後的上游修正演練 | 唯一純來源同步更新，核心入口與新純入口 parity，不維持第二份 body |
| U06 | 上游新增執行前置 gate | 能影響已 attach worker，不只第一次啟動檢查 |
| U07 | pause/stop 與 late completion | 不新增未授權 claim；在途 evidence 不丟，控制延遲可量 |
| U08 | 核心參數／回傳契約變更 | 明確失配可被測試攔住，不能把 Unknown/Pending 壓成可重試 bool |
| U09 | startup/scheduled/health及其他core副作用入口 | 全經session仲裁／正式handoff，handle不混用，不能繞過held barrier |
| U10 | 舊棒球設定出現在 load/hot reload | 不自動轉換／不 fallback／不雙寫；顯示升級指引，不丟其他平台資料 |
| U11 | 把舊 facade／decoder 移到 integrations，或只以舊 caller 申請例外 | gate 必須失敗；位置不等於核心契約證據 |
| U12 | 移除舊 API/型別/legacy fixtures 後新模組獨立測試／frozen 自測 | 新 contract 正常；舊模組不被載入／打包，migration 不可由 runtime import |

U05/U06/U08 是隔離測試分支／fixture 的受控更新演練，不是假裝已整合最新 upstream，也不保證未來零衝突。不得對使用者日常分支注入演練 patch。

---

### 16.6 跨 reducer 副作用碰撞

| ID | 情境 | 必須成立 | 規格 |
|---|---|---|---|
| X01 | 未決 submit + scheduled reload | BlockedByUnresolved；同 intent 不新增請求，原證據保持 | §6.4/6.6 |
| X02 | 未決 submit + navigation return/recovery | 拒絕返回與新導航；可只讀結果 | §6.6 |
| X03 | 未決 submit + 真 429 | 可 report incident；本 tab 不自動 reload | §6.6/9.9 |
| X04 | 未決 submit + input/reset/seat clear | 不得破壞輸入／dialog；明確拒絕前無恢復 grant | §6.4/8 |
| X05 | ready purchase + queued/reserved 普通刷新 | 未 commit 刷新失去 dispatch 權；購票立即繼續，cleanup 有界 | §6.6 |
| X06 | 已退休 refresh 的 reserve 晚回 | 只有 exact release，不能呼叫 browser reload | §6.6/9.9 |
| X07 | permit commit in-flight/unknown + ready purchase | 不搶佔；區分 browser-not-attempted／possible | §6.5/9.4 |
| X08 | 一次性 scheduled gate + ready purchase | 必要 gate 依設定；完成一次、不作週期高優先 refresh | §6.4/12 |
| X09 | conflict recovery 與普通／排程 refresh | 只有 parent recovery 的 handle 可完成恢復 | §8.4/9.9 |
| X10 | navigation/reload awaiting + 舊頁選票 proposal | 不重複 mutation；新 document/目的 context 經證據再推進 | §6/7/9 |
| X11 | pause/stop + completion 同批 | 先撤銷新權限，仍保留已發生的 receipt | §6.3/6.7 |
| X12 | 成功 evidence 先於 dispatch callback | 只終結一次；晚 callback 不覆寫回 awaiting | §6.5 |
| X13 | policy/auth 改變後原 submit receipt 才來 | 按原 attempt 歸屬，不被 current revision 丟棄 | §5.3/6.3 |
| X14 | core 直接 scheduled/health/reset 路徑 | 都經 engine request 或正式 handoff；不旁路仲裁 | §6.7/12 |
| X15 | ACCEPTED/QUOTA + 新 key／帳號／設定 | 不自動新購票或普通刷新；使用正式事件 | §8.3/8.7 |
| X16 | close/target identifier 重用 + 舊 cleanup | 不復活、exact token不清新 claim；未知記錄不變成授權 | §6.5/6.8 |

### 16.7 Purchase 完整轉移

| ID | 情境 | 必須成立 | 規格 |
|---|---|---|---|
| T01 | RECOVERING_CONFLICT 成功出口 | 清除／handle／新 document 完成才 SELECTING，新 attempt ID | §8.3/8.4 |
| T02 | RECOVERING_CONFLICT 失敗／逾期 | 轉 HELD_UNKNOWN(recovery)，不永遠顯示 recovering | §8.4 |
| T03 | RECOVERING_INPUT 成功且 selection 未變 | fresh input+payload audit 後 READY，再用新 attempt | §8.3/8.6 |
| T04 | RECOVERING_INPUT 成功但 context/selection 已變 | SELECTING，不重用舊 SubmitPlan | §8.3 |
| T05 | BLOCKED_AUTH 同資料 vs 可信更正 | 同值/讀不到不解鎖；已知拒絕且更正才準備新輸入 | §8.3 |
| T06 | BLOCKED_QUOTA 離開與保持 | 普通 key/account 變更不解除；人工 retire 不直接 READY | §8.3/8.7 |
| T07 | ACCEPTED 新 PurchaseKey/活動/auth | 無 UserStartNewPurchase 維持成功記錄、不追加購買 | §8.3 |
| T08 | ACCEPTED 明確新任務 | 新 fresh start/cycle、保留歷史成功、不再操作舊頁 | §8.7 |
| T09 | HELD_UNKNOWN 延遲成功／拒絕 | 歸屬原 attempt 後精確轉移，不視為新 submit | §8.3 |
| T10 | 人工解決報告及 retire | wrong-attempt/重複/未選 disposition 拒絕；USER_REPORTED 與實證分開 | §8.7 |
| T11 | unresolved abandon 與重新啟動 | RETIRED_UNRESOLVED 不解鎖；來源不明購票 context 有人工 gate | §8.7 |
| T12 | state/event family 出口完整性 | 各 state 有合法出站/保持/拒絕/pause/close/late cases，禁止預設READY | §8.2/8.9 |

### 16.8 Input／CAPTCHA 契約

| ID | 情境 | 必須成立 | 規格 |
|---|---|---|---|
| I01 | 缺少 CAPTCHA 欄位但契約要求 | 不得預設 NOT_REQUIRED；阻止 submit | §8.5/8.6 |
| I02 | 圖片已 ready／延遲載入 | 立即取得或一個有界 waiter，不固定先 sleep | §8.6 |
| I03 | 相同 src 換圖／相同像素重新載入 | 結合 load/context evidence；不以 URL/hash 單獨視為同 challenge | §8.5 |
| I04 | OCR late result／provider revision 或 document變更 | 不寫入新 context，釋放短暫答案材料 | §8.6 |
| I05 | provider unavailable/exception/長度錯 | 依既有 manual/retry policy；無新演算法與盲目送出 | §8.6/8.8 |
| I06 | reset click returned 但新圖未確認 | 保持 RESET_AWAITING；不消費舊答案、不重點 | §8.5/8.6 |
| I07 | reset transport unknown/timeout | INPUT_BLOCKED／held，原 budget不重算 | §8.6 |
| I08 | native setter false／target replaced／framework未就緒 | 回讀拒絕/等待；不可直接 submit | §8.8/10 |
| I09 | manual／OCR／force-submit 組合 | 既有設定語意保持，輸入 ready 不自行賦予提交權限 | §8.8 |
| I10 | selection/credentials 在輸入期間改變 | input evidence 失效；未知 submit不因換憑證解除 | §8.3/8.6 |
| I11 | 取消 OCR future 但底層工作繼續 | 有界工作數，晚結果不寫 state/不發副作用 | §6.8/8.6 |
| I12 | 融合 mutation 觸發同步／異步原生更新 | 同一套原語；partial mutation 精確回報、失敗不送出 | §8.8/10 |

### 16.9 設定與 frozen upgrade

| ID | 情境 | 必須成立 | 規格 |
|---|---|---|---|
| G01 | mixed equal | runtime拒絕；frozen converter 產生 canonical-only、三個值不變 | §11/14 |
| G02 | canonical-only 合規、重跑轉換 | runtime接受；converter合法no-op，不強加schema2 | §11/14 |
| G03 | legacy-only／部分缺鍵／complementary | 離線依已列規則補入；未知key不靜默丟棄 | §11/14 |
| G04 | mixed conflict／explicit empty | 預設非零；明示策略後合法輸出，空值不當缺值 | §14.4 |
| G05 | duplicate JSON key／root/type錯／未知版本 | 不覆寫來源，不默默以default執行 | §11/14 |
| G06 | 寫入中來源改變／target存在／權限錯／多檔部分失敗 | 拒絕或精確manifest；備份/原檔保護，不宣稱跨檔交易 | §14.4 |
| G07 | Windows x64無system Python升級 | 獨立exe→新profile→frozen bot/settings驗證與真正startup通過 | §14.5/14.6 |
| G08 | macOS arm64無system Python升級 | 獨立artifact同流程，任意cwd/中文空白路徑/執行權限可操作 | §14.5/14.6 |
| G09 | checksum／manifest／final _internal 資源錯配 | 可識別版本與文件；錯配必失敗，checksum不冒充publisher認證 | §14.5/18.1 |
| G10 | 其他平台opaque資料／secrets／配對回退 | 未用legacy資料不被清除或遷移；秘密不進artifact；舊binary配舊設定 | §11/22 |

### 16.10 隔離／CI／早期交付

| ID | 情境 | 必須成立 | 規格 |
|---|---|---|---|
| H01 | interception完全關閉 | 獨立hard-egress仍拒絕；不以真站成功連線測隔離 | §17.3/17.4 |
| H02 | subresource/redirect/WebSocket/worker/子程序 | 同隔離域、未知流量deny、無CDP旁路 | §17.3/17.4 |
| H03 | DNS/direct IP/IPv6/UDP QUIC | OS隔離封住；proxy/hostresolver不作唯一證明 | §17.3/17.4 |
| H04 | 隔離權限不足或proof缺失 | production-origin job失敗或BLOCKED，不當作等效通過 | §17.3/17.4 |
| H05 | marker拼錯／pytest設定錯／empty addopts | strict flags仍生效；effective command可核對 | §17.1 |
| H06 | required catalog／execution manifest／JUnit／run metadata 不一致 | 缺例、重複／未知 ID、未核准 catalog 變更、偽造不適用、skip/xfail/xpass、舊 run-id/SHA 或 digest 錯配均非零；負向清單見 §17.5.6 | §16.11/17.5 |
| H07 | application/adapter/bridge 回傳錯型別 | typed gate失敗，不只domain | §17.2 |
| H08 | 新檔未列typed scope／廣泛ignoreAny | scope coverage gate拒絕，不以缺檔默默skip | §17.2 |
| H09 | P3首次JS外部化、資源缺失或digest錯 | 同階段build/frozen任意cwd自測必失敗；不fallback舊inline | §10/19.5 |
| H10 | 未做指定live／未取得final-delete核准 | P6c不執行；candidate只標LIVE/DELETE_BLOCKED | §17.7/19.10 |
| H11 | modified-Python manifest／Git 集合／位元組與語法結果不一致 | staged／working／untracked／改名／刪除精確分類；未列新檔、非預期缺檔、過期 digest、錯 base/SHA、未編譯或語法錯均非零 | §21.3 |

所有情境均為待建立／核對的驗收要求，不表示本文已執行。可用一個參數化測試對應多 case，但必須能逐一辨識未執行／skip／失敗；不得把一個成功 case 複製標記冒充矩陣全綠。

---

### 16.11 獨立 required-case catalog：固定集合、格式與治理

#### 16.11.1 固定集合與權威方向

`ci/utiki-required-cases.json` 是**規格檔**，不是測試輸出。初始 v1.5 catalog 的 `cases[].id` 必須恰等於下表 153 個 ID；不得由 pytest 收集結果、JUnit、execution manifest 或現有測試數反推生成。實作者依本計劃逐例建置 catalog，再由規格審查核准；可用工具核對文件與 catalog 一致，但不能用測試存在與否決定需求。

| 類別 | 固定 ID 清單 | 數量 |
|---|---|---:|
| D | D01, D02, D03, D04, D05, D06, D07, D08, D09, D10 | 10 |
| N | N01, N02, N03, N04, N05, N06, N07, N08 | 8 |
| S | S01, S02, S03, S04, S05, S06, S07, S08, S09, S10, S11, S12, S13 | 13 |
| R | R01, R02, R03, R04, R05, R06, R07, R08, R09, R10, R11, R12 | 12 |
| L | L01, L02, L03, L04, L05, L06 | 6 |
| C | C01, C02, C03, C04, C05, C06 | 6 |
| A | A01, A02, A03, A04, A05, A06 | 6 |
| P | P01, P02, P03 | 3 |
| U | U01, U02, U03, U04, U05, U06, U07, U08, U09, U10, U11, U12 | 12 |
| V | V01, V02, V03, V04, V05, V06, V07, V08, V09, V10, V11, V12, V13, V14, V15, V16 | 16 |
| X | X01, X02, X03, X04, X05, X06, X07, X08, X09, X10, X11, X12, X13, X14, X15, X16 | 16 |
| T | T01, T02, T03, T04, T05, T06, T07, T08, T09, T10, T11, T12 | 12 |
| I | I01, I02, I03, I04, I05, I06, I07, I08, I09, I10, I11, I12 | 12 |
| G | G01, G02, G03, G04, G05, G06, G07, G08, G09, G10 | 10 |
| H | H01, H02, H03, H04, H05, H06, H07, H08, H09, H10, H11 | 11 |

ID 不重用；測試搬家只更新 execution manifest 中的 node ID。必要的多情境、OS／tenant／層級、H06／H11 負向子案例，必須在 catalog 的 `obligations` 中分列穩定 `obligation_id` 與必要 variants，不能只保留一個概括的 case ID 就算全部通過。例：S08 的 general／server-assigned fixture、G07／G08 的不同 target，不可用同一次成功互相代替。

#### 16.11.2 最小格式與責任

| 檔案／欄位 | 必要內容與限制 |
|---|---|
| catalog：`schema_version`、`catalog_id`、`revision` | 一種正式 schema；未知版本／重複 JSON key 拒絕；本輪只是格式規格，沒有已核准的實體 catalog |
| catalog：`plan_version`、`plan_sha256`、`status` | 指向正式採用的計劃位元組；未核准時為 PROPOSED，不能作為已通過階段的權威 |
| catalog：`cases[]` | 每例唯一 `id`、`spec_refs`、`invariant`、非空 `obligations[]`；不得有未知或缺失 ID |
| obligation | 唯一 `obligation_id`、`required_gates`、`job_id`、`layer`、明示 `variants`、`applies_when`、`required_evidence`、`result_policy` |
| `required_gates` | 明列 P0/P1/P2/P3a/P3b/P4/P5/P6a/P6b/P6c/P7/P8 中適用門檻，不靠字串大小比較階段；不得晚於 §19 要求 |
| `applies_when` | 僅以經核准 gate/target/tenant/mode/capability/ADR 等有限欄位及精確值判定；使用固定 all-of/one-of 比較，不執行任意 expression／程式碼 |
| `result_policy` | 本版 required 義務固定 `PASS_ONLY`；skip、xfail、xpass 均不能履行義務 |
| catalog：`gate_profiles` | 每階段所需 job／OS/arch／layer 矩陣與最終彙總義務；單一 job 不得代表整階段完成 |
| execution manifest：`catalog_id/revision/catalog_sha256`、`cases[]` | 完整列出 catalog 中每個 case 一次；只能映射 obligation/variant → node ID／證據相對路徑，沒有自訂 required/waiver 條件 |
| execution mapping | exact node ID（含參數化 ID）、job／target／layer、穩定 case/obligation/variant ID；同一 obligation 在同一 job/target 中恰有一筆必要執行，不得複製成功結果充數 |
| run metadata | 由 launcher 記錄實際 job/phase/target/候選 SHA／run-id／attempt／工具版本／有效參數／exit codes 與證據 digest；測試函式不能替自己簽發適用性或核准 |

catalog 不存自身的全文 digest，以免自我引用；外部 adoption/review 紀錄固定其原始 UTF-8 位元組 SHA-256。initial catalog 及之後的修改皆為正式規格變更：附舊／新 digest、case/obligation/條件差異、理由、計劃對應變更及獨立 review。刪例、降低層級、縮 target、延後 gate、擴大不適用都不能以「更新測試清單」名義免審。

CI 從受保護且經審查的核准紀錄取得 expected catalog digest，不能只信任本次測試、execution manifest 或 run metadata 自填的值。正式核准欄位不足則 `REVIEW_REQUIRED / BLOCKED`；本文件不代填 reviewer、Issue、branch、採用人或核准 digest。checksum 可驗完整性，不是審查者身分或授權的證明。

#### 16.11.3 結構範例（非完整 catalog、非核准成品）

以下只示範 H06 的一項 obligation；省略其他 152 個 case 及 H06 其餘義務，因此不能用此範例通過正式完整性 gate。

```json
{
  "schema_version": 1,
  "catalog_id": "utiki-required-cases",
  "revision": "1.5",
  "plan_version": "1.5",
  "plan_sha256": null,
  "status": "PROPOSED",
  "cases": [
    {
      "id": "H06",
      "spec_refs": ["17.5.6"],
      "invariant": "測試與執行清單同步刪除案例仍須失敗",
      "obligations": [
        {
          "obligation_id": "H06.removed-test-and-mapping",
          "required_gates": ["P1", "P2", "P3a", "P3b", "P4", "P5", "P6a", "P6c", "P7", "P8"],
          "job_id": "architecture-types",
          "layer": "architecture",
          "variants": ["delete-both"],
          "applies_when": {"all": []},
          "required_evidence": ["junit", "checker-exit-nonzero"],
          "result_policy": "PASS_ONLY"
        }
      ]
    }
  ],
  "gate_profiles": {}
}
```

正式 `plan_sha256` 必須是完整 64 位小寫十六進位 SHA-256；所有例子中的 null／空 gate profiles 只表示尚未核准／範例未完整，不得被 checker 當成合法放行值。

#### 16.11.4 適用、不適用與尚未到期

checker 依 catalog 及經核對的 run context 自行算出本 job 的 required obligation 集合。execution manifest 必須保持全 catalog 的 case/obligation 映射；某 job 只執行其中一部分，不等於其餘 case 可以從 manifest 消失。

`NOT_APPLICABLE` 只有在 catalog 明訂條件確實不成立、提供匹配條件／target／ADR 證據與核准來源時才成立；沒有宣告條件的 obligation 預設適用。OS 不同可使 G08 在 Windows job 不適用，但 gate 彙總仍需 macOS job 履行 G08。停用 server assignment 不得免除其既有 fixture／禁止誤啟用案例；真站不存在的頁型仍依原 live 規格判定。

階段尚未到期用 `NOT_DUE`，與 `NOT_APPLICABLE` 分開；不得拿 P1 的分階段結果宣稱 P6a/P8 已完成。缺實作、browser、Python、隔離權限、目標環境或授權，是 `NOT_RUN / BLOCKED`，不是不適用。任何必要條件缺失／未知值／條件解析失敗一律不放行。

---

## 17. CI、型別、網路硬隔離與真站門檻

### 17.1 不讓命令自己關掉strict

基準pytest.ini把strict flags與預設marker同放addopts。[R10] 清空 `-o addopts=` 時必須在命令列補回 `--strict-config --strict-markers`；或只以命令列 `-m` 覆寫marker而保留addopts。正式CI使用§21的統一命令，不在不同OS各寫一套逐漸分歧的覆寫。

pytest的arg組合/override可以改變預設選擇；strict flags與marker選擇是不同責任。[E04/E13] 「有test跑」不表示啟用了strict或所有必要cases已覆蓋。

### 17.2 Typed scope涵蓋整個新架構

`ci/utiki-typed-scope.txt` 列明domain、application、ports、adapters、bootstrap、current config/keyword和實際存在的外部core-service bridge、upgrade tool。新目錄新增檔案預設進scope，不能因沒寫在命令列就漏掉。

`mypy --strict` 的effective config隨artifact保存；動態第三方模組使用精確stub/adapter與code-specific ignore，不全域 `ignore_errors`、`follow_imports=skip` 或廣域`Any`放行。上游未改typed債可隔離，但跨ports暴露的輸入/receipt/result必須具名型別。[E12]

需有負向測試：故意給錯receipt、漏必需claim、adapter回raw dict，都會使typed gate失敗。沒有stub的外部值先視為object並在codec驗證，不能藉cast把未知資料聲明成已驗證。

### 17.3 Browser fixture兩層防線

**第一層：CDP request-stage interception。** 固定fixture allowlist回本地HTML/JS；未知請求拒絕。正式production origin/path測試不patch classifier/allowlist。這層用來提供資料、測routing、記錄副作用，不是唯一網路隔離。

**第二層：獨立於CDP的OS/執行環境egress封鎖。** 測試browser、Python driver、其子程序都不能對非loopback外送；即使interception未安裝、被移除或crash也成立。

預設Linux參考部署：預先建好含Chrome/Python/依賴的固定image，測試階段在 `--network none` 的非privileged container/network namespace內執行；loopback fixture/CDP服務與driver都在其中。不要mount Docker socket、host namespace或提供恢復網路的權限。Docker none只建立loopback，這是選用該參考隔離的依據，仍需在實際harness測負向canary。[E11]

Windows/macOS原生驗證需具備等效、已實證的整個測試process tree隔離，例如專用VM在測試時無外部網路/只有loopback，或實證涵蓋browser network service與driver的OS防火牆方案。hosted runner的權限和隔離能力必須先驗證；不能拿Linux container結果冒充Windows/macOS native browser已測。

**僅有deny proxy、hosts/host resolver或停用背景網路旗標不算硬隔離。** 可作額外輔助，但不能假設覆蓋direct-IP、IPv6、UDP/QUIC、DNS、子程序或代理旁路。未具備硬隔離的環境不得執行production-origin情境；改跑loopback DOM-shape tests只能算該層，不得填PASS給origin case。

### 17.4 Isolation故障注入

先用本地受控/測試用地址證明以下情境，**不以向真售票網站發出探測來驗證封鎖**：

- CDP listener尚未安裝／移除／crash，正常外送仍被環境拒絕。
- browser的HTTP、WebSocket、fetch/beacon與非HTTP路徑不外送；driver直接socket也不外送。
- IPv4/IPv6、直接IP、DNS與UDP在實際支援OS均有覆蓋證據。
- fixture/native click只到本機allowlist，未知request使測試失敗且不離開隔離域。
- cleanup失敗不會留下有網路權限、掛在真profile上的browser。

基礎image/依賴下載在隔離測試開始前完成；任何測試期間偷偷安裝套件/下載Chrome都算違約。證據包含隔離模式、權限、PID/子程序scope、負向canary結果、blocked events與實際outbound計數，不只依CDP network log作oracle。

### 17.5 必要jobs與防假綠

| Job | 內容 | 時機 |
|---|---|---|
| unit-contract | 原有未受影響suite＋新reducer/arbiter/ports | 每階段 |
| modified-python-syntax | Git 變更集合與 manifest 交叉核對、所有適用 Python 檔逐檔 py_compile | 每個有 Python 變更的階段／候選；P1 起自動強制，P0 有修改亦須逐檔編譯 |
| architecture-types | dependency、retired符號、typed scope、負向注入 | P1骨架、P2起逐項強制、P6c全量 |
| browser-contract-isolated | 真production JS，real-origin與硬隔離 | P1建立隔離，P3使用新資源時必需 |
| browser-native-platform | 實際release OS的DOM/input/driver差異 | P3及其後所有受影響變更 |
| package-smoke | bot/settings assembled bundle、resources/cwd/missing-file故障 | P3首次production資源變更起，不等P8 |
| upgrade-frozen-e2e | 獨立upgrader→current config→frozen bot/settings | P6a前準備完成，P6/P8驗收 |
| core-integration | 控制revocation/核心reload旁路/契約演練 | P4–P6與shared變更 |
| live-evidence | 指定tenant/page的已授權真站記錄 | P6b在final delete前，最後candidate變更後再核對 |

job 必須按 §16.11 的獨立 catalog 驗證完整 required 義務，不是只看 nonempty。每次驗證同時讀 required catalog、execution manifest、JUnit 與 run metadata；四者皆是必要輸入，不能用 execution manifest 自我宣告集合。0 collected、required 義務未收集／skip／xfail／xpass、缺 browser/隔離/啟用旗標、缺少或錯配 run-id/SHA/digest，一律非零。

JUnit 寫入唯一 run/attempt/job 目錄；run metadata、manifest 與 XML 都綁定同一次執行。required catalog 的核准 digest 另由受保護的 review/adoption 紀錄提供，不能四份檔案互相填同一個錯值就放行。詳細契約如下。

嚴格marker、故意unknown marker/config的負向probe、故意損壞resource/typed receipt的負向probe，都須看到非零exit。結果收集即使使用always()也不能覆蓋真正test exit code；禁止required job continue-on-error。

#### 17.5.1 四份輸入與信任根

`verify_utiki_ci_results.py` 必須明確接收 `--required-catalog`、`--execution-manifest`、`--junit`、`--run-metadata`，另接 `--approved-catalog-sha256` 及其 review 紀錄參照。expected digest 取自 §16.11 的核准來源；CLI 參數本身不會創造核准。文件修訂包不包含可執行的 repository checker 或已核准 catalog。

run metadata 最少包含：`run_id`（含 attempt）、`job_id`、`phase`、`candidate_sha`、`working_tree_digest`／clean 狀態、`os`、`architecture`、`layer`、`tenant/mode`（適用時）、`catalog_sha256`、`execution_manifest_sha256`、`junit_sha256`、`fixture_digest`、`script_digest`、相關 binary/config-contract/target-manifest digest、isolation evidence、effective command/flags、測試與收集器 exit code、起迄時間。候選／job／target 的期望值須與 CI 事件及核准 gate 矩陣核對，不由測試自己決定。

只在適用的層級要求對應資源 digest；例如純語法 job 沒有 binary，須依 catalog 的 evidence 類型明示不適用，不能任意省略欄位。摘要使用原始檔 SHA-256；多資源 digest 必須以已排序的相對路徑＋各檔位元組 digest 建立固定清單，包含缺檔／多檔偵測，不只 hash 檔名。

JUnit 的 suite/run properties，以及受 catalog 管理的 testcase properties，必須能核對 exact `case_id`、`obligation_id`、`variant_id`、`node_id`、run-id、job-id、candidate SHA、catalog/execution manifest 與 fixture/script digest。缺少識別 properties 的舊 XML 非法；可以由正式 pytest reporter／外部 harness 寫入，不得事後從 manifest 臆造一組成功 testcase。JUnit 不內嵌自身 digest；run metadata 封存實際 XML digest，避免循環。

#### 17.5.2 集合完整性與逐筆證據

1. strict 解析四輸入，拒絕重複 JSON key、未知 schema、無效 ID／digest、空 required 義務；核對受保護的核准 digest 與計劃版本。
2. 驗 `catalog.case_ids == execution_manifest.case_ids`，再驗每 case 的 obligation／variant 完整映射；缺少、重複或未知 case/obligation/variant 均失敗。一般非 catalog 的輔助測試可另跑，但不得宣告未知 required ID，也不能充數。
3. 由 catalog 的 gate/job/target/layer/條件計算本 job 的 required execution keys；execution manifest 只能提供映射，不得縮減集合。expected execution key 為 `(case_id, obligation_id, variant_id, job_id, target)`。
4. 每個適用 key 必須恰有一份對應 testcase，setup/call/teardown 全部成功，無 fail/error/skip/xfail/xpass，所需的非 JUnit evidence 也必須齊全且 digest 相符。重複 testcase、重複 XML／結果、錯參數化 ID、錯層級或借用其他 target 的成功均失敗。
5. 不適用／尚未到期逐項回報條件與依據，不填 PASS；適用卻漏收集就是失敗。人工宣告不適用不能覆寫 catalog。
6. 比對本輪 run/attempt/job、候選 SHA、實際檔案 digest、fixture/scripts/binary/config 版本與隔離證據；前次 run 的成功結果無效，單改 metadata 的檔名或 timestamp 也不能重用。
7. 產出 checked/required/missing/failed/inapplicable/not-due 與逐項原因；required 失敗時非零。任何原 test/harness/collector 非零也須保留，不准由後續 checker 成功洗掉。

一個參數化測試可覆蓋不同 variant，但各 variant 必須有獨立執行 key 與結果；catalog 明確要求多項 assertions 的單次義務，測試本體仍需真的驗證全部，JUnit 只能證明執行結果，不能證明 assertion 品質。此剩餘限制由獨立審查及既有 mutation tests 補足。

#### 17.5.3 skip／xfail／xpass 的處理

本版所有適用 required obligation 都採 `PASS_ONLY`。xfail 不論預期與否均不能履行 required 義務；XPASS 即使被測試工具視為成功亦不可通過（含 non-strict XPASS）。required tests 禁用 xfail 標記，reporter 另記 `wasxfail/outcome` 以偵測執行期改標；不能只以 XML 沒有 `<failure>` 推斷 PASS。

如果某種 gate 沒有適用案例，不以「0 collected」當成功：checker 需輸出按 catalog 推導的 `NOT_APPLICABLE`，gate 彙總再檢查是否由其他必要 target/job 履行。預期非空的 job 得到 0 collected 必須失敗。原先允許的非 required 輔助測試 xfail 不會填補 required 缺額，也不改變其政策。

#### 17.5.4 全階段與跨 job 彙總

單一 job 只能宣稱其 catalog 子集合結果。階段 aggregator 仍使用同一核准 catalog 與完整 execution manifest，接收各 job 的 JUnit／metadata 成對紀錄（`--run-index` 指向 exact 路徑與 digest），重新執行相同核對，並依 `gate_profiles` 檢查全部必要 job/target/layer。

不同 job 可有不同 job-id，但必須屬同一 run/attempt、候選 SHA 與 catalog／execution manifest 版本；不能拼接別次成功。重跑屬新 attempt，不能只替換失敗 case 再拼舊 XML。跨工作流程執行的 native/frozen/live 證據依原規格記錄明確候選與人工核准關聯，不偽裝成同一 run；需要繼承較早證據時必須遵守既有 P6b→P6c／P8 的差異覆核，不自動豁免。

live 證據維持原本授權、站點與人工覆核制度，不得為了統一 JUnit 而偽造執行過的測試。aggregator 只能驗其必要記錄／digest／核准關聯是否齊備，不能把文件存在判成真站成功。

#### 17.5.5 Catalog 變更與聯合刪除保護

catalog 本身改動須作正式規格 review，記錄 old/new digest、影響案例與採用決策，取得新的受保護 expected digest 後才可作 gate 權威。只修改測試與 execution manifest，不能改 required 集合；三者一起刪例但沒有規格核准，仍因未核准 digest／固定 ID 或 obligation 集合不符而失敗。

這不是新 approval service：沿用 §2 的採用／審查治理加一個獨立規格檔與 digest pin。無法只靠同一 patch 中可任意編輯的多份檔案建立獨立權威；受保護核准來源或獨立 review 缺失時應明示 `REVIEW_REQUIRED`，不宣稱可抵禦惡意同步竄改所有證據。

#### 17.5.6 H06 必備負向子案例

以下各列必須在 catalog 成為獨立 obligation/variant，不可由執行清單刪除。故障注入只作用於測試用副本／合成輸入；成功條件是被驗證 checker 明確非零，外層負向測試正確 assert 此結果。

| 子案例 | 注入方式 | 必須被拒絕的原因 |
|---|---|---|
| removed-test-and-mapping | 同時刪一個 required test 及 execution mapping，catalog 不變 | required ID／obligation 集合缺項 |
| catalog-unapproved-change | 再刪 catalog 中同一例但未更新正式核准 | approved digest／規格集合不符 |
| duplicate-or-unknown-id | 在 catalog、manifest 或結果放重複／未知 ID | strict schema 與集合核對失敗 |
| missing-obligation-variant | 只留下 case 名，漏另一 tenant/target／參數 variant | 不得用概括 case PASS 充數 |
| forged-not-applicable | 將缺 browser／隔離／授權或未實作標為不適用 | 與 catalog 條件／核准 context 不符 |
| required-outcome-not-pass | 分別注入 skip、xfail、strict XPASS、non-strict XPASS、setup/teardown error | PASS_ONLY、reporter 與原 exit code 保留 |
| empty-or-missing-result | 0 collected、全 skip、刪 testcase 或 XML | required 義務沒有當次成功 evidence |
| stale-run-or-sha | 舊 run-id、另一 attempt、不同候選 SHA 或偽造時間 | run/context 不匹配 |
| digest-mismatch | 改 catalog/mapping/XML/fixture/script/binary 一者，或借其他 target digest | 原始位元組與適用 evidence 不匹配 |
| duplicate-result-or-wrong-layer | 複製同一成功 testcase，或用 loopback 代 origin／source 代 frozen | execution key 不唯一或層級不足 |
| missing-job-or-target | 彙總缺一個必要 job/OS/arch，或多放未知結果 | gate_profiles 完整集合不符 |
| failure-exit-overwritten | test/harness 非零，但後續收集器回 0 | 最終 gate 仍非零，不能被 always() 洗綠 |

### 17.6 Coverage與失敗模型

原有domain/schema branch 95%、application branch 85%保留為工程目標，不是普遍門檻。arbiter collision、state出站、input stale、exact cleanup、手動retire、升級矩陣為**必測功能門檻**，不能以coverage百分比抵消。

不以Python行覆蓋率宣稱JS branches已跑。每種production operation都有happy/stale/rejected/possible/partial-mutation案例，必要時文件化不適用。對held barrier/document gate/exact token進行定向mutation，相關測試須確實變紅。

### 17.7 Live smoke先於最後legacy刪除

遵守固定基準工程指南§6：[R18] **P6b先完成指定站點/核心流程實機驗證，P6c才final delete**。本輪文件修訂不授權真站操作。

計劃矩陣至少包含CTBC與味全實際支援且受改動的manual/date/area/seat或quantity、登入/輸入/提交gate、結果與停止邊界；不可用fixture去捏造不存在的真站路徑。未啟用server assignment仍保持關閉。

每份記錄列tenant、頁型、source SHA/binary digest、browser/OS、設定模式/刷新間隔、實際到達步驟、停止點、未測部分及授權者。沒有交易測試授權就不能測實際提交；若門檻要求而未獲授權則標BLOCKED，不用讀DOM替代。

沒有窗口：保留candidate於P6a，標 `LIVE_BLOCKED / FINAL_DELETE_BLOCKED`。允許繼續不影響證據的開發或離線測試，但不能發布、核准刪除、宣稱完整完成。最後成品不含舊engine；這個順序不要求永久雙runtime。

---

## 18. 極低延遲設計、打包與效能驗收

### 18.1 PyInstaller 與三種交付物

基準已有 bot/settings 的 source 與 frozen release 管線；本版新增獨立 profile upgrader。[R17/R19] 所有實際 spec、OS/arch、release assembly 路徑列入 target manifest。

**P3a 首次外部化 JS 即完成**：資料檔收錄、package-relative 載入、assembler/digest、同一 build 的 bot/settings 最終 bundle、自測與 source-tree 不可用的任意 cwd 測試。不能只補 hiddenimports 或等 P8 再檢查 datas。[E06]

upgrader 在 P6a 前完成對 Windows x64/macOS arm64 的獨立 artifact/checksum/no-Python E2E。三個成品依賴分開，converter/legacy upgrade fixtures 不能進 bot/settings 正常 imports；也不能為了測試方便把 source tree 一起帶入成品。

資源錯配直接非零退出，不 fallback 到舊 inline JS、另一個版本的工作目錄或模組副本。檢查 final `_internal` 合併後的結果，不只各自 dist 中未合併版本。Native-setter fragment 的來源和digest也列manifest；上游改變需同步驗證。

每個公開 artifact 含或對應 source SHA/config contract/script digest/OS/architecture，發布另提供 SHA256SUMS；checksum 是完整性資訊，不取代來源認證。真正執行測試包括 §14.6 的升級→配置驗證→bot/settings startup/隔離fixture，而非只看 --help。

### 18.2 依賴政策

維持專案 Python 3.11 基準，不為重構而同時大幅升級 runtime dependencies。新增 mypy／Hypothesis 等只進開發依賴；精確版本及 transitive resolution 在 P0 的實際環境驗證後記錄。

不要在計劃裡捏造尚未驗證的版本，也不要為純單元測試匯入 browser／OCR 大套件。

### 18.3 效能目標：可行動之後，不能浪費時間

本節整合使用者追加的要求：**搶票程式必須快，不能因為架構、狀態確認或形式化的安全層而加入不必要延遲。** 這不是最後附加的調校工作；P0 就要量基準，後續每一階段都要檢查熱路徑。

以下效能要求先驗收 uTiki，不外推所有平台已變快；新模組的低延遲不能靠強迫其他平台改節奏。主要優化目標是：

```text
T_可控制延遲
  = 取得可行動證據的偵測延遲
  + 必要的 browser/CDP 往返
  + JS 讀取與本地操作時間
  + Python decode/plan/reduce 時間
  + 排程與不必要等待
```

完整端到端耗時還包含網站回應、原生頁面載入、使用者輸入及既定 interval/cooldown。這些項目不能與架構自身的延遲混為一談；不能把降低 interval 或取消網站確認步驟的差異，算成「重構變快」。

**要求是：在相同功能、相同選擇結果、相同必要限制下，選擇耗時最低的實作。** 相同 correctness gate 的兩種方案，採實測更快者，而不是比較漂亮、更多層、更多 class 的方案。

不宣稱目前已知道最快參數、改善幾倍，或能保證每次快過其他使用者。未量測的毫秒數只可作工程目標，不能當成果。

### 18.4 必須先檢查的實際熱點

下表是從基準程式可定位的優化候選，不是已量出的效能排名。[R02][R05][R16]

| 基準位置／模式 | 可以確定的事 | 優化目標／不能誤解的地方 |
|---|---|---|
| 主迴圈 `sleep(0.05)` | 每輪額外等待，不是每 50 ms 保證執行；還有檢查與 handler 耗時 | uTiki 結果直接續行；不把所有秒級停頓都算給核心或全域刪 sleep |
| outer main 與 family main 的 health check | 同一路徑有重複呼叫機會，現有 cache 可能減少部分實際 I/O | 量實際 CDP 次數後整合，不武斷認定每次都多一次網路往返 |
| purchase reconcile、page handler、submit gate 都讀 dialog | 一部分是同 observation cycle 的重複，一部分是提交前必要重新核對 | 合併前者，後者放進提交器本身，不直接全刪 |
| `_observe_ctbc_submit_outcome()` | 送出後固定等待 1 秒才開始那次結果歸納 | 改成事件／有界 waiter 提早處理結果；這不是首次 submit 前固定多等 1 秒 |
| date/readiness retry 與 destination waiter | 有 100/200 ms 輪詢，另有 25 ms polling 與 2.5 秒等待上限 | ready 即返回；2.5 秒是上限，不可當成所有正常流程一定等 2.5 秒 |
| document inspection 讀 body text | 正常頁也可能計算／傳回不需要的文字 | normal root 足以分類時不取大段 body；unknown/blocked 診斷才取必要內容 |
| policy fingerprint／script assembly | 相同設定、腳本可能重複處理 | 設定版本改變／啟動時才建立，熱路徑比 ID 與填小 payload |
| seat snapshot／geometry／native payload | 資料量可能隨座位數增加 | 減少 DOM layout 讀取、重複掃描、過大 JSON，不省略真正需要的全局一致性檢查 |

**優化必須定位到「耗時在哪一段」。** 函式很多不等於慢；一個短函式只要多發幾次 CDP，也可能比多個純函式更慢。Python 函式呼叫、資料配置、JS 執行與 CDP 傳輸分別量測，不用猜測互相替代。

### 18.5 冷路徑、熱路徑、恢復路徑分離

| 路徑 | 應做的工作 | 不應做的工作 |
|---|---|---|
| 冷路徑：啟動／設定變更 | 載入 JS、檢查 schema、編譯 keyword、建立 profile lookup、準備既有依賴、建立 policy revision | 偷發購票請求、提早佔 permit、以預熱名義增加真站負荷 |
| 熱路徑：頁面已可操作 | 讀需要的快照、選擇、安裝 claim、一次 guarded commit、立即延續 | 檔案讀寫、全設定遷移、整份 config hash、console flush、notification HTTP、全 repository 檢查 |
| 恢復路徑：stale／unknown／429 | 必要 evidence、確切 cleanup、受控 retry、狀態呈現 | 把恢復檢查每輪無條件套在正常成功路徑上 |

在正常 hot path，判斷「沒有 pending／blocker」只需讀取當前小型 state variant；不需要每次掃歷史 outcomes、重建 purchase key 的所有字串、或詢問遠端「是否安全」。

任何新的 guard 都要回答三個問題：它防哪一種具體失敗？資料是 immutable 還是 live？能否在既有 round trip／本地 transition 一併完成？答不出來的檢查，不應進熱路徑。

### 18.6 每個頁型的 CDP 預算與融合操作

以下是**已就緒且無錯誤的標準交易路徑目標**，不是強迫所有異常情境維持同樣次數。計算範圍要包含 adapter 隱藏發出的命令，不能只數高階方法。

| 頁型／階段 | 正常路徑目標 | 必須隨同完成 |
|---|---|---|
| 日期 → 原生導覽 | 1 次合併 observation + 1 次 commit | observation 包含 document／route／候選／必要 dialog；commit live audit＋click |
| 區域 → 原生導覽 | 1 次合併 observation + 1 次 commit | 區域表／map evidence 一次讀取；必要模式 audit 在 commit 內 |
| 座位／票數，輸入已就緒且契約容許融合 | 1 次 observation + 1 次 selection-and-submit | 本地寫入、回讀、payload audit、最終 gate、submit 同一 browser transaction |
| 座位／票數，需要等輸入或原生異步更新 | 1 次 observation + 1 次 selection commit，準備完成後 1 次 submit | 不為湊次數強行跨越真正的 async readiness 邊界 |
| 已有有效 submit plan 且所有輸入已備妥 | 1 次 guarded submit | 不另讀 dialog、URL、hidden IDs 再分別驗證；在 submit 本體讀 live state |
| 等待結果 | 優先事件喚醒／一個有界 waiter | 正常 ready 回傳不再加固定 1 秒 sleep |
| 跨文件 attestation | 第一次目的頁 observation 同時完成 | 不先讀 hidden IDs，接著再讀一次相同頁面快照 |

這些目標不包含必要的 coordinator reserve/commit、原生網站請求、現有合法輸入步驟或新 document 初始化。這些成本必須分欄報告，不能隱藏。

#### 融合提交的必要條件

`commit_and_submit` 是**同一套小型 JS 原語的組合**，不是第二個 fast engine：

1. application 已持有該交易的 dispatch claim，並把所需 typed plan 一次傳入。
2. browser 同步檢查 live document／route／candidate identity。
3. 套用計畫允許的本地選擇變更，保持原生事件語意。
4. 重新核對變更後的 payload、數量、選擇、必要 dialog 與輸入 readiness。
5. 仍成立才發出一次 submit；回傳 local-mutation／request-dispatched 的精確 evidence。
6. 原生 handler 若發起了必須等待的異步狀態變化，就回報 selection 已套用但未 submit，改走同引擎的等待續行；不能假設事件 dispatch 就代表原生狀態已穩定。

**最終 live audit 與 submit 之間不插入 Python 往返或不必要的 await。** JS 本地多做幾個關鍵欄位比較的實際成本要量，但不應為了「架構分層」拆成好幾次跨程序確認。

對失敗、部分本地變更、事件 handler 可能發出請求的路徑，都要回報可證明的 effect evidence；一個中途 exception 不等於 NO_EFFECT。

### 18.7 事件驅動與立即續行，不靠縮短輪詢硬衝

採以下執行策略：

- 已知 ready：直接執行，不註冊 observer、不 sleep、不等 tick。
- 未 ready：先做一次即時檢查，再安裝必要的 bounded waiter。
- waiter 的結果直接喚醒 session worker；worker 歸納後立即執行下一個已可做的步驟。
- 對新 uTiki 執行路徑，固定 50 ms loop 只作必要控制／回補，不承擔每階段排隊；其他平台仍可保留原 handler 節奏。
- 一個 pending 操作只有一個有效 waiter；反覆 tick 不建立第二個 observer 或 polling task。

DOM waiter 優先使用針對目標 root 的 MutationObserver，搭配已驗證的原生事件及有界 fallback polling。MutationObserver 觀察的是配置的 DOM mutation，不能假設 JavaScript property assignment、native payload 物件變動或每一種 readiness 改變都會觸發它。[E08]

因此：只讀的 waiter 可以有小範圍 polling 作缺漏保護，但只在等待狀態啟用，ready 立即結束；不得 1 ms 不斷掃整頁。初始 polling 候選值可以是 20–50 ms，最終值以 event miss／CPU／tail latency 測試決定，不把候選值當成固定功能要求。

Python-side cancellation 不代表 browser 裡的 Promise／observer 自動被清掉。browser waiter 自己必須持有 deadline，退出時 disconnect／clear timer；document 銷毀、stop 或 supersede 後的結果依 stamp 忽略。不得讓舊 waiter 自行 click 或 submit。

對 CDP lifecycle／execution context 事件的使用，先核對固定 zendriver 版本與 browser 的實際支援；事件作喚醒和失效提示，不作「已購票成功」的證明。[E09]

### 18.8 快取政策：快取不變的東西，不快取購買權限

| 資料 | 可快取的範圍 | 失效條件／不可用法 |
|---|---|---|
| JS source、assembled template、resource digest | process lifetime／build version | 不得快取含帳密的完整 invocation 或把 template cache 當頁面 cache |
| keyword compiled rules、排序 mode、policy fingerprint | 同一已驗證 settings revision | 設定變更重建，tick 不重算 |
| tenant route specs、immutable profile | process lifetime | 新版 profile 改變則隨版本更新 |
| document identity 的 routing hint | 同 context／generation，僅用於喚醒與路由 | commit 仍讀 live document，不能靠 TTL 免檢 |
| snapshot、plan | 同一 observation transaction 的引用 | 重用時必須有來源／policy，live commit 重新核對；不因 TTL 尚未到就認定庫存仍可用 |
| 座位穩定排序索引、靜態欄位 | 已證明結構不變的 document/layout generation | root 替換、resize、zoom、影響排序或幾何的變更要失效；無可靠事件則不快取 |
| 可售狀態、目前 selection、dialog、payload、輸入內容 | 不作長期授權 cache | submit 前使用 live audit；不把舊 observation 當可用性保證 |
| auth epoch | 已驗證的 session context | UNKNOWN 不推斷換帳號，也不為驗證 epoch 每 tick 讀所有 Cookie |

沒有可靠變更證據時，用少量必要 live read，比建立複雜且可能錯用的 invalidation framework 更好。所有 cache 必須有容量或生命週期上限。

### 18.9 純核心也要省：資料配置、索引與演算法

**型別與不變性是維護工具，不是每一步重新驗證／複製的理由。**

- immutable records 建立一次後在同一交易共享引用；狀態更新只替換小分支。
- 不對 snapshots 每次呼叫 `asdict()` 再 JSON 化；只在 browser wire 邊界 encode 必要 payload。
- 需要穩定 selector fingerprint 時只序列化一次，不對同一 candidate 反覆 build tuple／dict／hash。
- 候選 filtering 一次正規化文字；不要在每個 keyword 與每個 audit 都重複相同 normalization。
- 區域以 ID 索引 map，避免每個 row 都掃完整 map。
- top/bottom/center/random 的挑選保持原語意。能線性處理的規則不必全部排序；需要數字座號排序才排序。
- 多組關鍵字有優先序，不能找到低優先級命中就提前 return，漏掉後面的高優先級候選。
- 連座候選可按 row 建索引、滑動窗口保留最優 block；不為每個 block 複製完整所有座位。
- 每筆候選資料的巨大 debug string、完整 fingerprint dump 預設不建立。

座位 geometry 的 live audit 不能隨便刪掉。可先縮小到目標連座與實際所需的全局選擇／payload 核對；若要快取幾何，必須覆蓋位置、縮放與 layout 變更情境。

### 18.10 最快執行不等於最多並發

優先減少 round trip，而不是用 `gather()` 同時讀五份彼此需要一致的 DOM。不同時點的結果拼起來不一定是一份 coherent snapshot。

允許的並行是：

- 不同 tab 的獨立本地工作，不加全域鎖。
- 與目前頁面交易無關的通知、log flush、設定監控。
- 經證明相互獨立的只讀準備，結果有 document／operation stamp，過期就丟棄。

禁止的並行是：同一 tab 同時 submit／reload、同一 intent 重複發送、用兩個流程競速點擊、或以預熱名義越過 coordinator 的間隔與網站確認步驟。

正常頁面選擇不需要無條件查 SQLite。「是否需要刷新」由本地狀態判定，真正 request 才進既有 reserve／commit 協議；共享保護不可用記憶體快取取代最後線性化檢查。

開賣前可預載本地資源與既有依賴，但不預取尚未允許的購票步驟、不預佔 reservation。現有 fast navigation 的 tenant／special-action 限制不因效能優先而放寬。

### 18.11 量測點、時鐘與結果檔

新增 benchmark harness，至少記錄：

| Metric | 定義 |
|---|---|
| `ready_to_dispatch_ms` | 必要候選資料與輸入已 ready，至該次操作實際交給 browser 的延遲 |
| `observation_ms` | browser observation 呼叫到完成，包括傳輸／JS／decode，另分項可得成本 |
| `planner_ms` | 相同 typed snapshot/policy 下的純決策時間 |
| `reduce_and_schedule_ms` | completion 已到達至下一個應執行 effect 開始的本機開銷，含仲裁 |
| `arbiter_cpu_ns` | 本地 eligibility／priority／claim 決策成本；不含 I/O，不與 wall latency 混算 |
| `ready_vs_refresh_dispatch_lag_ms` | 已 ready 與普通 refresh 競爭時，購票 actual dispatch 的額外延遲 |
| `browser_audit_ms` | 同次 script 內的 live audit 成本，不含遠端網站回應 |
| `submit_dispatch_ms` | claim 建立到 submit effect 被發送；不與成功確認混淆 |
| `outcome_observation_lag_ms` | 成功／拒絕 evidence 已在頁面出現，至 engine 處理的延遲 |
| `cdp_commands_per_transaction` | 包含 library 隱藏命令；另列 `evaluate` 次數 |
| `ticketing_requests_per_intent` | 實際對票務站的請求，不把 CDP 本機操作誤當網站請求 |
| `idle_cpu`、`event_loop_lag` | 無有效工作／等待時的成本，與忙時吞吐分開 |
| `allocated_bytes`／retained session/task | 量測配置及清理，不為低延遲換成無界資源保留 |

Python 端使用 monotonic／`perf_counter_ns()` 量時間差；browser 端使用其自己的 performance clock。不要直接相減不同時鐘的絕對值。[E07][E10]

`ready_to_dispatch` 可在可控 fixture 中將「元素變成可行動」與「原生 click handler 被執行」的標記放在同一 document clock；跨 document 的端到端時間改用 host-side 共同時鐘與 CDP interception 記錄。不能把文案第一次出現當成全部必要輸入都 ready。

benchmark 輸出 JSON，包含 baseline/candidate SHA、Python/browser/OS、硬體、fixture size、設定 mode、冷／熱態、樣本數、median/p95/p99、失敗與重試數、CDP 與站點請求數。raw credentials、完整頁面與真實訂單不進 artifact。

### 18.12 Benchmark 方法與初始預算

**相同環境成對比較，不能拿兩個不同 hosted runner 的單次耗時下結論。**

建議最小矩陣：

| 軸 | 案例 |
|---|---|
| 頁型 | date、area、reserved seat、general quantity；未啟用的 server assignment 只在 fixture 測 |
| 大小 | date/area 到既有合法上限；seat 100、500、2,000、5,000 作測試規模，超過正式上限者只列 stress，不冒充新增支援 |
| readiness | 已 ready、延後出現、root re-render、input 非 DOM attribute 變更 |
| 結果 | 立即成功／延後成功／明確拒絕／transport unknown |
| 執行環境 | 使用者實際 Windows+Chrome 優先；正式支援 macOS 時另測；source/frozen 分列 |
| 乾擾 | 單 tab、少量多 tab、慢 log sink、慢 notification、設定熱更新、coordinator contention |

本地純運算採固定輸入做足夠多次迭代；browser 情境先 warm-up，再分多批量測。例如每組 20 次 warm-up、300 次有效樣本、至少 3 批，實際樣本數可依穩定性調整並在報告列出。cold-start 另測，不能用 warm-up 隱藏每一個新 document 都會再付出的初始化成本。

baseline 與 candidate 用不同工作目錄、暫存設定／profile／DB。不得對同一真站交易同時跑兩套引擎。可交替 A/B 順序以減少 CPU 熱度與背景負載影響，不能只挑 candidate 最快的一批。

以下是**初始工程目標，不是已實測成績，也不是跨硬體的即時保證**：

| 目標 | 初始預算／判定 |
|---|---|
| 無工作本地 dispatch／no-op transition | p95 目標 ≤ 0.1 ms，不含 browser I/O；無全量配置或檔案讀寫 |
| date／area 純 planner | 合法最大 fixture 下 p95 目標 ≤ 1 ms |
| 大 seat fixture 純 planner | p95 目標 ≤ 5 ms；超標先做 profile，不直接換演算法語意 |
| 結果已到達至下一步可發送 | 正常本機負載下 p95 目標 ≤ 5 ms；不得固定多等一個 50 ms tick |
| 正常 ready 路徑的人工 sleep | 0；必須等外部狀態時才等待，ready 立刻退出 |
| CDP／evaluate 次數 | 按第 18.6 節登錄各 case 的預算，新增一次必須有不可合併原因 |
| 正常 hot path 阻塞 I/O | 不讀設定／腳本檔、不 flush log、不等待通知；真正刷新授權除外 |
| 功能相同的端到端 p95 | 不得出現超過量測不確定度的實質退化 |

例如「p95 差超過 5% 或 1 ms」可以設為**復測警報**，不是預先允許變慢的額度。噪音太大時結果標示 inconclusive，重做受控量測；不能以「增加安全所以慢 20% 可以接受」作預設結案標準。

若已確認的 correctness 修正不可避免增加少量成本，要把原因、替代方案與最小開銷獨立列出；不得因為希望數字變好就移除它。架構本身的多餘 hop、重驗、copy、sleep 則沒有這個豁免。

### 18.13 架構／安全／速度衝突時的明確裁決

採以下順序：

1. 刪除重複或沒有失敗模型支持的檢查。
2. 將 immutable 資料檢查移到建立時或 CI，將 live 檢查留在副作用當下。
3. 在既有 browser operation 裡合併讀取、audit 與必要本地寫入。
4. 去掉額外 scheduling hop、固定 sleep、熱路徑 file I/O 與無必要序列化。
5. 仍慢才針對量測出的 planner／JS／CDP 熱點做專項改造。
6. 仍有不可省略成本就如實列出，而不是承諾零成本安全，或把錯誤購買變成效能交換條件。

**不接受「為了乾淨所以慢一點沒關係」作預設取捨。** Protocol、reducer、typed record 應在本機保持薄且直接；不得為每個步驟建立 GenericCommand、序列化事件、經過多層 middleware 才做一次 click。

也不新增「略過驗證的極速模式」與「安全慢模式」兩套產品。目標是一套在正常路徑已經精簡的實作；異常路徑做真的必要的處理。

### 18.14 僅在量測支持時採用的進一步極速方案

P7 必須比較下列方案，保留有效的優化，無收益的實驗移除；不是全部無條件加入：

- **本地腳本 compilation／invocation 開銷：** 若成為明顯熱點，再評估固定執行 context 下的 CDP compiled script／function handle。不得跨 document 重用失效 handle；context 初始化成本也算在 cold-path。先查固定 browser 版本支持，不依賴實驗 API 默默回退。[E09]
- **更小 snapshot：** 若 JSON 傳輸／decode 主導，傳回完整決策所需的最小欄位與緊湊集合；仍由 codec 轉成具名 record，不把 magic indexes 洩漏進 domain。
- **單往返選擇＋提交實驗：** 若兩次 CDP 的 host round trip 明顯主導，而且規則能以小型、明確的 decision spec 表達，可為單一頁型實驗 browser-side select-and-commit。不能再維護一份手工複製的關鍵字／排序／fallback 業務規則；要有單一規則來源、同 snapshot 差異測試與完整 mutation cases。做不到就保留已經批次化的兩次方案，不為省一次往返引入新的雙引擎技術債。
- **選擇與既有輸入準備的流水化：** 僅限互不依賴的本地工作，按相同 document／輸入版本檢查結果；不新增任何驗證規避能力，不讓過期輸入完成新 submit。

是否採用以端到端及最壞尾延遲決定，不以 microbenchmark 中某個函式快幾微秒決定。不能一個方案只改善 warm repeated tick，卻讓每次新頁面更慢。

### 18.15 效能驗收案例

| ID | 情境 | 必須成立 |
|---|---|---|
| V01 | 頁面一開始就 ready | 無固定 readiness sleep，立即讀取／決策／提交 |
| V02 | I/O completion 恰好在外層 tick 剛結束後到達 | 立刻續行，不額外等 50 ms |
| V03 | 同 observation cycle 需要 health／dialog／page data | 同一合併快照或明確必要的新 live audit，沒有盲目重複查詢 |
| V04 | commit-and-submit 的原生 input handler 改變 payload | 融合操作仍攔截錯誤，不能以省 round trip 犧牲 audit |
| V05 | 目的頁資料 ready 但整頁還沒 complete | 依該頁契約決定是否可做，不無條件等全頁圖片／非必要資源 |
| V06 | 慢 console／通知 HTTP | 不延遲本 tab 的下一步或其他 tab 的 action |
| V07 | 設定沒改的高頻執行 | 不重算 policy hash、不遷移、不每輪重新 JSON parse 設定 |
| V08 | 大座位圖重複有效／無效 tick | 沒有 state deep-copy 或無界 task／snapshot 保留 |
| V09 | MutationObserver 未捕捉 property-only 更新 | 有界補充觀察可恢復，不能永遠等不到或用 busy loop 燒 CPU |
| V10 | 在 audit 前變更 document／selection／可售量 | 即使採快取與融合仍拒絕 stale effect |
| V11 | 重複 ready event／batch completion | 不重複 submit 或 reload，去重是本機 O(1) scope 檢查 |
| V12 | 同設定與 fixture 的 baseline/candidate | 輸出行為一致，CDP／站點請求數可核對，延遲無實質退化 |
| V13 | cold start／fresh document／frozen app | 初始化成本單列，不能只公佈最有利的 warm 數字 |
| V14 | 429／pause／coordinator contention | 速度優化未改動 interval、single-probe 與清理語意 |
| V15 | ready purchase 和未 commit 的普通 refresh 同時存在 | 不先刷新，精確撤銷舊 dispatch 權，無額外 50 ms/CDP仲裁查詢 |
| V16 | 新arbiter／input契約冷熱路徑 | 不重載模型或腳本、不掃歷史、不deep-copy；最終input audit融合既有operation |

測試矩陣涵蓋 V01–V16 後，才能說本次重構有把低延遲當成架構要求；只在摘要寫一句「兼顧效能」不算完成。

---
### 18.16 等待歸因、核心修改收益與上游成本

P0 的 wait inventory 區分核心分派／控制、共用 helper、平台固定 sleep、readiness waiter、網站／原生處理、刷新/cooldown、初始化與結果觀察。畫面已換頁不代表所有必要資料 ready；timeout 是上限，不是每次必付成本。

對每個 shared patch 記錄 baseline/candidate、正確性理由、受影響 caller、是否為 uTiki 自訂接入或真正共用契約、替代方案及 U/V tests。效能與同步成本分開報告：shared files/symbols、其他平台 caller 改動、move/rename 及回退 group；不設武斷零行改動目標。

舊棒球架構清理不能因為保留歷史 wrapper 比較省 diff 就放棄。先尋找最小的新入口接合；只有 §14.2 成立的現行核心映射可以留下。若核心適配真的增加無用途排隊／CDP，要簡化；不能用相容當效能豁免。

共用核心有已量出瓶頸時，可以有理由地修改；不預設必須全部重寫，也不為上游方便永久忍受大量可消除等待。每項改動獨立證據，不把 microbenchmark 小贏當整個交易更快。

---

### 18.17 不能把新增正確性契約變成重複稽核鏈

X/T/I 的完整性主要由建構、純 reducer／arbiter 與測試建立，不是讓每次 ready action 都呼叫所有檢查器。沒有 pending 時讀當前 small state，資格成立即 grant；已取得相同 observation 的欄位不再各層查一次。

input/image evidence 的 live 部分放在最後 browser operation；需要額外輪詢只能因原生狀態確實未就緒。仲裁不新增瀏覽器／SQLite往返，frozen升級／manifest／型別／schema全面驗證都不進選票熱路徑。

速度結果只能從相同可觀察行為與限制的成對量測得出；不得用取消 unknown blocker、刪限流或減少必要回讀換取分數。

---

## 19. 實作階段與不能跳過的交付閘門

### 19.1 階段圖

本節P0–P8是工程階段，不是findings嚴重度。

```text
P0 採用紀錄/Issue/actual HEAD/upstream/contract/wait inventory
 → P1 characterization、isolation、strict CI、performance baseline
 → P2 新模型/policy/ports/完整transition規格與ADR
 → P3a production JS外部化 + 同期spec/data/frozen build/self-test
 → P3b 同原語批次化與readiness改善
 → P4 session arbiter + navigation/purchase/input lifecycle
 → P5 refresh與核心所有副作用入口整合
 → P6a 新owner候選接入 + source/frozen upgrader + 全離線驗收
 → P6b 授權範圍內指定真站smoke + 維護者final-delete gate
 → P6c 最終刪除legacy + 全量negative gate + 重建成品
 → P7 有證據的端到端調校/碰撞與上游演練
 → P8 最終SHA/artifact再驗證 + release核准
```

所有熱路徑改動從P0起量測，P7不是第一次看速度；每次可合併變更都必須能build/回退，且對本階段全部實際 Python 變更依 §21.3 逐檔編譯；typed/pytest 不能代替。尚未接production的新引擎可先在隔離測試運作；它的存在不等於已切換，也不允許同一tab雙dispatch。

### 19.2 P0：核對與正式採用

**前置：** 確認當次工作是review或implementation。只有review則止於只讀資料/文件；沒有授權不自建Issue/PR或改repo。

**工作：** 核對actual HEAD/工作目錄/upstream URL/SHA/merge base；盤點舊符號、核心effect旁路、共享helper/native setter、全部spec/release目標、設定mixed實際類型、所有已存在測試。依本計劃固定的 153 個案例及原失敗模型細化 obligations；建立獨立 required catalog 與 gate/target 條件並取得規格 review，不從既有測試收集反推。採用ADR-01/02/03，更新直接衝突的規範。

**交付：** adoption.md（未核准欄不可假填）、baseline、behavior-contracts、legacy-removal、core-integration/core-effect inventory、upstream-touchpoints、wait-inventory、typed scope、release target manifest、baseline benchmark、required catalog 的固定 ID／obligation 清單與核准 digest 紀錄、語法 gate 的核准 base/candidate 範圍。

**驗收：** 原測試實際結果可核對；基準失敗原樣列出；每個核心例外有真實caller而非舊棒球caller；mixed equal策略唯一；對所有§6/8出口有事件/證據/owner。

### 19.3 P1：可靠測試、隔離與量測

先讓現有fixtures在可信環境運作，建立CDP之外的hard-egress防線，完成interception失效負向canary。先建立 browser enable/strict flags、required catalog→execution manifest→JUnit→run metadata 四輸入 gate、當次 run-id/SHA/digest 與 modified-Python 逐檔語法 gate，再做 production 遷移。catalog 與 execution manifest 的完整集合核對從 P1 起強制；尚未到期 obligations 保持映射並標 NOT_DUE，不偽造 PASS。

有些舊fixture patch route/classifier，只算DOM-shape測試；新增至少一組真origin契約測試，不能用全部舊fixture綠燈冒充此層。建立fake clock/provider/受控pending I/O，避免用固定sleep猜測事件順序。

**驗收：** 故意破壞strict marker、guard、resource、manifest、interception後測試各自變紅；加 H06 全部四輸入／聯合刪除負向案例及 A06/H11 的漏檔／編譯失敗案例，outbound仍被硬隔離攔住。效能baseline記cold/warm/CDP/lag，不設定無根據的加速幅度。

### 19.4 P2：純資料、設定與完整型別

依§4/5建立具名records、current-config validator、ports、claim/receipt、input/captcha型別和states。更新新程式typed scope；保留唯一keyword/native setter來源，core服務實作未需要時不造額外integration。

upgrader與current loader輸入分類先做unit tests，尤其canonical-only、legacy-only、mixed-equal/complementary/conflict與empty。正式入口此階段不擅自覆寫使用者設定。

**驗收：** D01–D09/C01–C03/A01/typed負向cases，以及當階段 catalog 義務、modified-Python 完整逐檔語法 report；輸入圖像generation不以隨機token假造；所有state不能建出缺attempt/claim的非法組合。新helper不能為了純性複製另一份上游規則。

### 19.5 P3a：JS外部化必須連打包一起完成

先機械搬移source、不改selector/guard順序/原生事件語意。建立單一assembler/manifest、current native setter注入與對應browser codecs。

**同一合併單位必做：** spec datas、實際build入口、資源frozen自測、任意cwd、bot/settings最終合併bundle測試。Windows/macOS適用target均實際執行；缺環境則此階段狀態為BLOCKED，不延到P8。

舊workflow在開發過程可直接呼叫新ports，但不建立legacy-model bridge/第二套state/錯誤fallback；最遲P6c移除未替代完的舊入口。新增依賴資料檔時不允許source先合併、spec另開未完成TODO。

**驗收：** source/browser/frozen資源相同digest；catalog 中 P3a 的各 target obligations 齊備，修改的 adapters/build helpers/tests/spec 逐檔編譯；故意缺JS或混合_internal版本時成品自測非零；正式bundle在無source tree也可載入。原guard順序有parity。

### 19.6 P3b：可證明的批次操作

合併當頁必要identity/dialog/readiness/候選與目的attestation；live audit留在同次mutation。複用一套原語提供融合操作，不為1次CDP強跨async原生狀態。

**驗收：** N/S中的browser契約、I01–I08、V01/V03/V04/V09/V10；記command counts、source/frozen延遲；本地input setter失敗禁止submit。P3b若動資源仍重跑P3a打包gate；本階段依 catalog 核對全部必要結果與逐檔語法 report，不因測試搬家減少 required 義務。

### 19.7 P4：Arbiter與Purchase/Input完整出口

先建立session writer/ActiveEffect/eligibility/priority/collision matrix，再接三組reducer proposals。輸入子機沒有獨立owner。完整實作§8的RECOVERING/BLOCKED/ACCEPTED/HELD/retire/new-cycle出口及default保持規則；加本地人工恢復控制入口，不保留空白「人工處理」TODO。

**驗收：** X01–X16/T01–T12/I01–I12、submit事實晚到且settings已變、ready purchase與普通refresh競爭、stop與receipt同批、local vs possible效果、manual模式不自動送出。型別scope包含application/adapters，不僅domain；本階段 catalog 結果核對及所有修改 Python 檔逐檔語法檢查亦必須通過。

**切換：** 新引擎只在隔離harness驗證，正式route仍只有現行owner；不得用shadow effect比較。僅允許shadow decision。

### 19.8 P5：Refresh、core services與完整生命週期

保留SQLite scope/interval/cooldown/lease；用精確ReloadHandle。所有startup/scheduled/health/return入口經arbiter；外層challenge/reset旁路按core-effect inventory納入revoke/handoff。核心適配在模組外且不直改state。

permit commit unknown與browser attempted分開；沒有可靠receipt API時只允許最小已測擴充，不從controller偷讀資料庫或重reserve。必要typed結果不能折成可重試bool。

**驗收：** R01–R12/L01–L06/X碰撞及U04–U09；真SQLite多程序/late cleanup/崩潰/失去lease。普通queued refresh不餓死ready purchase，已commit不能以速度名義搶佔；核心改檔／條件式 bridge／CI helpers／測試一併進本階段語法集合，catalog 不可隨測試刪減。

### 19.9 P6a：候選接入與可交付升級

建立review worktree/候選SHA，main的自訂uTiki接點切新bootstrap/ports，候選只有新owner且不匯入/打包舊engine。舊檔可暫存在開發分支，尚未核准最終刪除，不能用那些檔案作正式fallback。

source converter與Windows/macOS frozen upgrader必須在此階段前後完成且**作為P6a完成條件**：artifact/checksum/manifest/no-Python G矩陣全通過；mixed-equal有實際成功輸出。UI/default/load/save/hot reload同步唯一格式，其他平台opaque保留不受損。

**驗收：** D/N/S/R/L/C/A/P/U/V/X/T/I/G/H 的完整 catalog 映射及本 gate 適用 obligations 皆有當次結果；累積 base→candidate 語法 report 全量核對；all-new運作可測，正式bundle不可達legacy/converter。沒有live只可標READY_FOR_LIVE，不能進P6c。

### 19.10 P6b：指定真站驗證與刪除核准

維護者另行指定合法帳號、tenant/page、操作允許範圍、刷新間隔與停止點；用P6a可識別candidate驗證。不增請求頻率，不自動付款。缺頁型或未允許提交的部分如實列明，不能用fixture代填live成功。

**出站gate：** 指定站點/核心流程證據齊全、對應candidate/腳本digest，catalog 的本 gate job/target/evidence 完整（不由 manifest 豁免）；若有 Python 改動先重跑逐檔語法 gate，第二審查者覆核，維護者記錄final-delete核准。無窗口/失敗/授權不足 → LIVE_BLOCKED/FINAL_DELETE_BLOCKED；停止此階段而不是偷偷改規範。

### 19.11 P6c：最後清除歷史架構

在P6b核准後，刪facade/alias/legacy-model/舊workflow/inline替代路徑/private橋接/失效spec依賴，啟用完整negative checks。清理diff限定舊碼移除與引用更新；若又夾帶runtime行為改動，回對應實機gate，不繼承之前證據。

**驗收：** final import graph/打包archive不含舊engine或converter；新typed scope無遺漏；累積 modified-Python 集合明列刪除／改名與現存編譯結果，catalog 的 final-delete 義務齊備；重新build最終bundle和回歸測試。證據記錄「P6b驗證SHA→刪除SHA」差異，不直接把它們當同一SHA測過。

開發分支的過渡層此時必須空；最後交付與runtime不保留待未來刪除的歷史相容。這不要求Git抹掉歷史或刪除獨立upgrade tool的legacy資料測試。

### 19.12 P7：低延遲與上游語意演練

依量測排序改善，不重做無收益框架；保持§6仲裁與§8輸入audit，不把檢查變成額外I/O。加入slow sink、readiness storm、casecollisions、resource/retained-task/多tab壓力。

上游演練包含新gate、變更helper、結果回傳形狀、native-setter修正與spec/核心reload旁路。只在隔離分支/fixture演練，不對使用者日常分支注入假變更。無Git衝突不等於功能正確。

**驗收：** V01–V16/U/X 必要 obligations 依 catalog 用同 SHA 核對，cold/warm/p95/p99/command counts/失敗與CPU分列；所有效能／build／test helper 改動同樣逐檔編譯。效能改到runtime後，適用browser/frozen/live gate重跑，不以已做過P6b免測。

### 19.13 P8：最終artifact與發布

全部正式target重build bot/settings/upgrader；依已核准 required catalog 的 gate_profiles 彙總全部必要 job/target，累積語法範圍不得漏檔；源SHA、binary與JS digest、current-config契約、JUnit/隔離/升級/live證據一一對應。對最終candidate做適用真站確認，不能把P6b較早SHA報告直接當最後成品驗證。

維護者批准發布與breaking說明；列出retired APIs、required upgrade、current-core必要適配、支援模式、已測／未測邊界、unknown人工流程與回退。發布門檻不足標RELEASE_BLOCKED；不宣稱永久零技術債或未來零merge conflict。

### 19.14 回退單位

P3a資源/loader/spec自測同組回退；P3b融合優化獨立回退；P4/P5新引擎未上production時可按功能回退；P6 ownership/API/config必要部分配對回退，資料已轉換時用舊binary＋原設定備份。回退不刪coordinator DB/cooldown/lease，不撤銷網站已發生的效果。

---

## 20. 舊程式到新責任的搬移對照表

函式名稱以基準版本為定位依據。實作者先核對 actual HEAD；同功能已改名時追蹤其 caller，不死守舊行號。[R02–R07]

| 舊檔案／符號或責任 | 新 owner／位置 | 搬移要保留／改正的重點 |
|---|---|---|
| `workflow._state`／`_new_ctbc_tab_runtime` | `application.session`＋engine instance registry | 移除 module-global 業務狀態；唯一 writer；非業務 task handles 分離 |
| `_ctbc_tab_identity`／`_existing_utiki_tab_context` | SessionHandle／TabKey lifecycle | lookup 不 allocate；target recycle 有 generation；close 後不復活 |
| `_ctbc_purchase_state_key` | `domain.identity` | 明確 tenant/document/auth context；不重新從零拼整份 URL 來做每次小檢查 |
| `_get_ctbc_page_type`／`_get_utk_sports_profile` | `profiles` | 直接 typed return；刪 legacy dict projection |
| `_get_utiki_behavior` | `utiki_config` 在載入邊界建立 policy | 正常 runtime 無 legacy fallback；mixed equal 升級在獨立工具，沒有每步 config migration |
| `_ctbc_*_payload` | `adapters.snapshot_codec`／明確 plan serializer | Python domain 使用具名資料；wire mapping 一處定義 |
| `_ctbc_prefetch_area_snapshot` | browser adapter／`navigation.js` | 保留已驗證的 tenant/response/action 限制；不擴大預取範圍 |
| fast marker 讀／刪／match | navigation reducer＋browser marker primitive | marker 只是 evidence；compare-and-delete；不因 cache 命中授權購買 |
| `_ctbc_new_pending_navigation`／native pending | `domain.navigation` intent builder | 必要 identity、期限與 action evidence 明確 |
| `_ctbc_reconcile_navigation_source`／attest helpers | `domain.navigation`＋application controller | current/nonce 檢查集中；新頁 observation 同時作 attestation |
| `_ctbc_return_current_pending`／native return | navigation state/effect | 返回也是單一 dispatch claim，不每 tick 重送 |
| `nodriver_ctbc_date_auto_select` | `domain.selection.date`＋`application.pages` | 相同 keyword/fallback；一 observe、一 guarded commit |
| `nodriver_ctbc_area_auto_select` | `domain.selection.area`＋`application.pages` | 表／map/action 三來源 semantics 不變；batch read |
| seat/quantity main | 純 planner＋page controller＋browser transaction | 本地選擇與 submit 可證明時融合；不重建兩套 audit |
| `_ctbc_captcha_and_submit` | purchase/input reducer＋明確 input/browser/provider ports＋session arbiter | input/reset/late result有獨立證據；preserve enable/force-submit；ready立即續行 |
| `_dispatch_ctbc_submit` | purchase reducer claim＋browser submit primitive | claim 先於外部 effect；receipt≠success；結果未知不重送 |
| `_observe_ctbc_submit_outcome` | bounded outcome observation | 去掉正常結果之前的固定 1 秒等待，不縮短結果辨識期限冒充加速 |
| `_handle_ctbc_submit_dialog`／recover helpers | purchase reducer＋typed recovery | 先保存 blocker；dismiss／clear／reload 分清 effect evidence |
| `_ctbc_login_fingerprint` | 模組外 current credential provider＋新 auth evidence | 不在核心持有秘密，不每輪重讀帳密／cookies |
| `_ctbc_selection_config_fingerprint` | policy construction | 只在設定變更建立；熱路徑比較 revision |
| `_handle_ctbc_checkout` | accepted outcome＋通知意圖 | 查核 tenant 標籤；通知非阻塞、結果與 attempted 分開 |
| `refresh._inspect_utk_document` | document primitive＋shared PageObservation | 唯一 canonical token；normal 不無故取大段 body |
| `refresh.reload/reload_wait` | refresh controller＋ReloadHandle | reason≠identity；不把 completion 交錯 caller；立即喚醒 |
| reserve/commit/renew/cancel helpers | 模組外 current coordinator adapter＋新 refresh reducer | 保留 SQLite 線性化；不建立 mirror DB／queue |
| emergency files | coordinator／infrastructure adapter | 保留必要跨程序保護與故障處理，不塞入 reducer |
| `selection._freeze_*` | snapshot_codec＋named snapshots | 刪除 magic field offsets；strict boundary decode 一次 |
| `selection.parse_*`＋planner/audit | codec／domain／browser audit | 把 raw parsing 與純選擇分離；live audit 留在 browser |
| `utiki.__init__`／`platforms.ctbc` | 輕量 root／刪除 facade | 所有 caller 直接用新 API；不留下 deprecated forwarding stub |
| `settings.migrate_config` 中舊 uTiki 遷移／鏡射 | 刪除正常路徑；必要轉換在獨立升級工具 | 其他平台 migrator 保留；不在 load/hot reload 自動接回舊棒球格式 |
| settings UI 的 ctbc 行為元件 | canonical `utiki_*` UI／schema metadata | default/read/save/dirty-check 一致，無雙寫 |
| `tests.ctbc_support`、private patch | 新 semantic fixtures／ports／事件 trace | 保留行為覆蓋，不保留舊 API 轉接；不以測試 caller 豁免 |
| 核心 ↔ uTiki 接入／組裝 | 新 `platforms.utiki.bootstrap`；必要current-core適配在外部 | ADR-01預設直接工廠，不建立舊函式群 facade／多重root |
| current coordinator／notification／credential providers | 有證據才建立模組外 current-core service provider | 只映射現行服務到新 ports；無舊 phase／config migration／鏡射 state |
| 真正共用 util keyword public API | 核心入口＋唯一 keyword_rules | 非歷史棒球 caller 才有穩定入口例外；新模組直用純來源 |

新增責任也需追蹤：所有頁面 mutation → §6 ActiveEffect；原 image token/reset/fill/OCR 呼叫 → §8 InputState；主程式直接 reload/接管 → §12 request/handoff；converter → §14 Windows/macOS 目標矩陣的適用成品；新仲裁不是在原 wrapper 外再加一套 state。

沒有列出的函式不代表可以刪除。以 caller inventory、測試情境及 side-effect inventory 交叉核對；先證明不再使用或責任已被接手。

---

## 21. 驗證命令契約與結果記錄

### 21.1 命令不是執行授權

以下source/target CLI由實作者在授權後使用；本文件沒有執行它們。帶新檔名/旗標的是需建立的交付契約，不能當作基準已有功能。只讀review不得照附件自動修改/執行。

```bash
git status --short
git remote -v
git rev-parse HEAD
git show --no-patch --format=fuller f307f7caba054fb40aab807a4629c891e69fb200
git diff --stat f307f7caba054fb40aab807a4629c891e69fb200 HEAD
git diff --check
```

fetch與merge分開，upstream URL/ref先核對。不reset/clean/覆蓋使用者內容，建立worktree/Issue/PR需明確範圍。

### 21.2 基準pytest：保留strict

只在已具備所需隔離的環境執行browser suite；flag不是隔離措施。

```bash
python -m pytest --strict-config --strict-markers
python -m pytest --strict-config --strict-markers --cov=src --cov-report=term-missing
TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1 python -m pytest -o 'addopts=' --strict-config --strict-markers -m 'browser and not live' tests/test_ctbc_dom_fixtures.py
```

PowerShell需保存/還原原環境值，不覆蓋使用者原設定：

```powershell
$oldFlag = [Environment]::GetEnvironmentVariable('TICKETS_HUNTER_RUN_BROWSER_FIXTURES', 'Process')
try {
    $env:TICKETS_HUNTER_RUN_BROWSER_FIXTURES = '1'
    python -m pytest -o 'addopts=' --strict-config --strict-markers -m 'browser and not live' tests/test_ctbc_dom_fixtures.py
    if ($LASTEXITCODE -ne 0) { throw 'Browser fixtures failed' }
}
finally {
    [Environment]::SetEnvironmentVariable('TICKETS_HUNTER_RUN_BROWSER_FIXTURES', $oldFlag, 'Process')
}
```

`-o addopts=`移除了原strict來源，所以每個覆寫命令都顯式補回。CI記錄effective args/必要marker/環境，不因PYTEST_ADDOPTS或外掛覆寫而假綠。

### 21.3 目標 modified-Python／architecture／type／contract gate

#### 21.3.1 語法範圍：Git 事實＋manifest，不是固定目錄

以 `tools/verify_modified_python.py`（待實作）建立／驗證本階段 `modified-python.json`，再逐檔呼叫 `py_compile.compile(..., doraise=True)`。這是依既有 AGENTS.md「對修改過的 Python 檔逐一編譯」要求的可機器驗收契約。[R12] 本輪僅修訂文件，沒有對 repository Python 檔執行語法 gate。

範圍為核准 base→candidate 的**整個 repository** Python 變更集合，不先限縮到 uTiki 目錄，也不依 pytest 是否 import 決定。至少涵蓋所有實際修改的 production、tools/migrations/CI／benchmark、build helper、tests，包含 `src/nodriver_tixcraft.py`、`src/settings.py`、實際建立的 `src/utiki_core_services.py` 及 checker 自身。未修改檔可因階段規格額外列入，但不能用 allowlist 排除任何已辨識的變更。

| 集合來源 | 必須處理 |
|---|---|
| `base_ref..candidate_ref` | 比較已解析的 exact Git SHA，讀 NUL 分隔 name-status；不可由 caller 任選較近 base 來縮小集合 |
| local staged／working tree | 與 HEAD／index 的變更聯集，涵蓋 staged 後又修改的檔案；編譯實際工作目錄位元組，非過期 index 副本 |
| untracked | `git ls-files --others --exclude-standard -z` 的所有新增 Python 檔；不可只看 tracked diff |
| added／modified／copied／type-changed | 現存 `.py` 全列入；可執行 Python build recipe `.spec` 亦作額外 compile-only 項，不執行 PyInstaller |
| renamed | 新路徑為 Python 則編譯新路徑；保留原路徑與 status；改名不得漏檔 |
| deleted／改名至非 Python | Git 明確確認移除者列 `DELETED/RETIRED_PATH`，驗不存在後不編譯；這不是可默默忽略的缺檔 |
| ignored／generated source | 正式或測試 Python 原始碼不得藏於 ignore 規則逃逸；必要 generated build source 須在核准 build-source inventory 列明來源、路徑與 digest 並加入範圍，無 inventory 則阻擋 |

正常 `.venv`／第三方快取不是本計劃原始碼，不掃整個環境；但不能用寬泛目錄排除把真正修改的 tracked/untracked/build-source 檔一起跳過。symlink、路徑穿越、大小寫碰撞、unmerged entries 或無法確定的 type change，拒絕並列原因，不追到 repository 外編譯不明檔案。

base 採 §2／P0 核准的階段起點，candidate 取實際 CI checkout；兩者由受審查的 run context 核對。每階段驗自己的完整 diff，P6a/P6c/P8 另外驗實作基準到該候選的累積 diff，不能只編譯最後一個 commit。CI clean candidate 不接受未記錄 working changes；本地 dirty 檢查可跑，但報告必須附工作目錄 digest 並標 DIRTY，不冒充某個 SHA 的正式驗收。

#### 21.3.2 Manifest 與獨立交叉核對

manifest 至少包含 `schema_version`、`run_id`、`job_id`、`phase`、`base_sha`、`candidate_sha`、`working_tree_digest`、`python_version`、`files[]`。每檔列 repository-relative `path`、`old_path`（適用時）、Git status／來源集合、`role`（production/tool/build/test）、`exists`、`sha256`（現存檔）、`required_action`（compile／verify-delete）。角色由清單記錄，不影響是否應被編譯。

**驗證模式必須重新從 Git／核准 build-source inventory 推導集合，不能只照 manifest 指示編譯。** 必要集合漏一檔、多一個不存在檔、重複 path、未列的新檔、條件式 bridge 已建立卻被排除、改錯 base/candidate、content digest 錯配都非零。額外列入未改的現存檔須明標 `additional` 與理由，不可用其成功補缺漏。

未建立的條件式 `utiki_core_services.py` 不必憑空新增；只有查核未存在且無變更／build-source 需求才可不列。已列 compile 項卻找不到檔案必須失敗；只有 Git 明確的刪除紀錄可以走 verify-delete。

每檔以目標 Python 3.11 逐檔 `py_compile`，compile-only，不 import／執行其頂層、測試函式或 build recipe。bytecode 放本次 artifacts/cache，而非改動 source tree。編譯前後再核對實際位元組 digest，遇途中變動為 STALE_INPUT／非零；先記每檔成功或失敗，再產出含 manifest digest／effective interpreter／run-id/SHA／exit code 的 syntax report。

沒有 Python 變更時，只有在獨立推導集合確實為空才能回 `NO_PYTHON_CHANGES`；不得寫成「所有 Python 已編譯」。有變更卻零編譯必須失敗。syntax report 是必要獨立證據：mypy、pytest import、compileall 某幾個資料夾均不能取代它。

#### 21.3.3 命令契約

以下變數由正式 launcher 在授權後建立：`BASE_SHA`、`CANDIDATE_SHA`、`RUN_DIR`、`RUN_ID`、`APPROVED_CATALOG_SHA256`。`RUN_DIR` 為唯一 run/attempt/job 目錄；核准 digest 不從測試清單取得。參數未提供、仍是 placeholder、與 metadata 不一致皆非零。

```bash
python tools/verify_modified_python.py --base-ref "$BASE_SHA" --candidate-ref "$CANDIDATE_SHA" --run-metadata "$RUN_DIR/run-context.json" --write-manifest "$RUN_DIR/modified-python.json" && \
python tools/verify_modified_python.py --base-ref "$BASE_SHA" --candidate-ref "$CANDIDATE_SHA" --run-metadata "$RUN_DIR/run-context.json" --manifest "$RUN_DIR/modified-python.json" --report "$RUN_DIR/python-syntax.json"
python tools/check_utiki_architecture.py --strict --core-inventory docs/02-development/utiki-refactor/core-integration-inventory.md
python tools/check_upstream_boundary.py --inventory docs/02-development/utiki-refactor/upstream-touchpoints.md
python tools/run_utiki_typecheck.py --manifest ci/utiki-typed-scope.txt --config ci/mypy-utiki.ini
python -m pytest --strict-config --strict-markers tests/unit/utiki tests/contract/utiki tests/integration/utiki tests/architecture tests/upstream_integration tests/upgrade/utiki
python -m pytest --strict-config --strict-markers tests/performance/utiki
```

CI 每行視為必要 step，shell 必須採失敗即停或明確保存各 exit code；不能把以上多行貼成只看最後一行結果。`--write-manifest` 僅建立查核清單，**不是**語法 PASS；第二行重新推導範圍、逐檔編譯才是 gate。local dirty 執行須明確開 `--include-worktree`，生成與驗證兩次皆使用同一模式，報告不得供 clean/release gate 冒用。

type runner 仍必須實際呼叫 mypy strict 並列被檢檔案，維持 §17.2 的完整 typed scope。syntax manifest 可以包含未全面型別化的上游修改檔及相關測試，**範圍不得被較小的 typed manifest 截斷**。兩種 checker 不互相取代。

browser 命令由隔離 launcher 在隔離域內執行，依 §17.5 reporter 契約寫入 JUnit properties；有效 browser enable、strict flags、隔離證據均需記錄：

```bash
python -m pytest -o 'addopts=' --strict-config --strict-markers -m 'browser and not live' tests/browser/utiki --junitxml="$RUN_DIR/browser.xml"
python tools/verify_utiki_ci_results.py --required-catalog ci/utiki-required-cases.json --approved-catalog-sha256 "$APPROVED_CATALOG_SHA256" --execution-manifest tests/fixtures/utiki/manifest.json --junit "$RUN_DIR/browser.xml" --run-metadata "$RUN_DIR/run-metadata.json" --require-browser
```

`run-context.json` 是執行前固定的身分／期望來源；launcher 完成後建立 `run-metadata.json`，加入真實 test exit code、JUnit/syntax report 與資源 digest。測試失敗時可繼續收集，但最終 exit 是所有必要 step 的失敗聯集，不因 checker 回 0 清除原 test exit。PowerShell 同樣保存 `$LASTEXITCODE`；不得只查看最後一個命令。

各 job 彙總範例：

```bash
python tools/verify_utiki_ci_results.py --required-catalog ci/utiki-required-cases.json --approved-catalog-sha256 "$APPROVED_CATALOG_SHA256" --execution-manifest tests/fixtures/utiki/manifest.json --run-index "$RUN_DIR/gate-runs.json" --phase P6a
```

`--run-index` 列出每個 job 的 JUnit＋run metadata 路徑、digest 與 job/target，不替代四輸入核對；aggregator 對每一組仍接收四份資料並按 catalog 的 gate_profiles 驗完整集合。source/type/syntax/frozen 非 pytest 工具的必要結果以原始 report/exit code 作 evidence；不得製造不存在的 testcase 冒充測過。live 仍走既有人工授權／證據門檻。

#### 21.3.4 A06／H11 必備負向驗收

| 子案例 | 注入 | 必須結果 |
|---|---|---|
| outside-fixed-roots | 僅修改主入口、settings、migration、CI/build helper 或測試檔，並放入語法錯 | 即使 pytest 不 import，逐檔 gate 仍發現並非零 |
| new-or-untracked-omitted | 新增 `.py` 卻刪其 manifest entry；含 untracked 與新目錄 | 重新推導 Git 集合發現漏列；非零 |
| staged-and-working | staged 成功版本之後把 working tree 改壞 | local 模式編譯真實位元組；CI dirty 候選拒絕 |
| missing-file | manifest 要求 compile，但檔案意外不存在 | 不 skip，不以空集合通過 |
| explicit-deletion-rename | 明確 delete／rename／copy／副檔名改變 | 正確驗刪除與編譯新 Python 路徑；漏新路徑失敗 |
| conditional-bridge | ADR 建立 core-services bridge 卻未列入 | 已存在／變更即屬必要集合，不能因條件式名稱跳過 |
| wrong-base-or-sha | 用 HEAD 當較近 base、換 candidate 或拿舊 report | 與核准 run context 不符；非零 |
| duplicate-or-digest-race | 重複 path、hash 不符、編譯期間變更 | 非零並標明原因；不發布過期語法 PASS |
| ignored-build-source | generated／ignored Python 被交付或執行卻無 build-source inventory | 範圍無法完整證明，BLOCKED／非零 |
| self-or-zero-compile | checker 自身修改漏列，或有變更但編譯數為零 | 不予通過；確實無變更另回 NO_PYTHON_CHANGES |

這些是待實作的 repository gate 測試規格，不是 v1.5 文件檢查已執行的程式測試。負向測試的合成故障只能在隔離副本／臨時 fixture，不能破壞使用者分支。

### 21.4 升級CLI與frozen CLI

source與frozen共用相同參數／exit-code契約：

```bash
python tools/migrations/convert_utiki_config.py --input old-profile.json --dry-run
python tools/migrations/convert_utiki_config.py --input old-profile.json --output converted-profile.json
python tools/migrations/convert_utiki_config.py --input conflicting-profile.json --output converted-profile.json --conflict-policy canonical
```

Windows使用解壓成品（執行前核對checksum；以下目標路徑是規格）：

```powershell
.\utiki-profile-upgrader.exe --input .\old-profile.json --dry-run
if ($LASTEXITCODE -ne 0) { throw 'Upgrade validation failed' }
.\utiki-profile-upgrader.exe --input .\old-profile.json --output .\converted-profile.json
if ($LASTEXITCODE -ne 0) { throw 'Upgrade failed' }
.\nodriver_tixcraft.exe --self-test-utiki-config --input .\converted-profile.json
if ($LASTEXITCODE -ne 0) { throw 'Frozen bot configuration failed' }
.\settings.exe --self-test-utiki-config --input .\converted-profile.json
if ($LASTEXITCODE -ne 0) { throw 'Frozen settings configuration failed' }
```

macOS arm64解壓獨立成品：

```bash
./utiki-profile-upgrader --input ./old-profile.json --dry-run && \
./utiki-profile-upgrader --input ./old-profile.json --output ./converted-profile.json && \
./nodriver_tixcraft --self-test-utiki-config --input ./converted-profile.json && \
./settings --self-test-utiki-config --input ./converted-profile.json
```

這些`--self-test-utiki-config`是需新增的無網路驗收入口；不能啟動真站、不能自動轉換。config自測以外仍須§14.6真正frozen startup/loopback CRUD/隔離fixture E2E。沒有system Python的目標VM可由CI外部host控制，不必在該VM以Python啟動test harness。

CLI：conflict policy `error`預設／`canonical`／`legacy`；in-place需explicit備份且不能與output並用；目標存在預設拒絕。exit0只代表成功或合法no-op；格式/衝突/I/O錯誤非零並有機器可讀reason。不得在stdout/stderr印secret值。

### 21.5 Build與frozen驗收

```bash
python -m PyInstaller build_scripts/nodriver_tixcraft.spec --clean --noconfirm
python -m PyInstaller build_scripts/settings.spec --clean --noconfirm
python -m PyInstaller build_scripts/utiki_profile_upgrader.spec --clean --noconfirm
python build_scripts/verify_utiki_bundle.py --targets ci/utiki-release-targets.json --require-current-resources --reject-legacy-runtime
python build_scripts/test_frozen_utiki_upgrade.py --targets ci/utiki-release-targets.json --fixtures tests/upgrade/utiki
```

每個OS只build其適用target，target manifest不能要求跨OS生出未測binary。既有release assembly及macOS封裝方式沿用，不直接假設三份dist合併就可交付。以上兩個新test script的實作要驗證實際bundle/digest/非source路徑；不可只看檔案存在。

P3首次改production resource時bot/settings先完成build+frozen自測；upgrader在P6a前成為必要完成項。公開release workflow必須上傳獨立upgrade assets、SHA256SUMS/manifest，不只build後留在workspace。

### 21.6 Benchmark與結果表

```bash
python tools/benchmarks/bench_utiki.py --mode warm --samples 300 --seed 1701 --output artifacts/utiki-warm.json
python tools/benchmarks/bench_utiki.py --mode cold --samples 30 --seed 1701 --output artifacts/utiki-cold.json
python tools/benchmarks/compare_utiki.py --baseline artifacts/baseline.json --candidate artifacts/candidate.json
```

同環境成對量測；seed/sample只是初始規格，最終附不確定度與失敗樣本，不以平均值掩蓋tail latency。包括arbiter/ready-vs-refresh案例，不能只測沒有競爭的happy path。

每階段報告：adoption/Issue、actual/baseline/upstream/candidate SHA、source與script/binary digest、變更行為、原根因→案例→實作、retired符號與真核心例外、typed scope、required catalog 與核准 digest、execution manifest／run metadata／JUnit digests、適用性與 gate 彙總、modified-Python 的 base/candidate／逐檔語法 report、完整test命令/collected/pass/fail/skip/xfail/xpass、isolation canary、frozen升級、live範圍、效能與資源、未測與下一個blocked gate、rollback。

沒有執行的測試記NOT RUN；沒有真站窗口記LIVE/DELETE/RELEASE BLOCKED，不能用code compile代填PASS。

---

## 22. 上游同步、升級與回退

### 22.1 上游交界的最小變更策略

P0 固定 actual HEAD、真實 upstream URL/ref/SHA、merge base 與上次整合點。`fetch` 與 `merge/rebase` 分開；不假設 origin 是原作者，也不宣稱本輪重新核對最新分支。

自訂模組的清理提交、shared helper 改動、core gate/route 接入、settings/upgrader、打包分開管理。主入口使用新 bootstrap 而非重排整份路由。真正需要的 current-core mapping 放入精確 inventory；不能以減少 diff 為由保留舊棒球 API。

### 22.2 上游更新演練與正式整合

在獨立整合分支／worktree 查看上游增量與 touchpoint 的交集，包括已搬到新檔、但原作者仍在原位置修改的同一規則。跟隨維護者選擇的 merge/rebase 策略，不擅自重寫已分享歷史。

檢查新 gate 是否能限制已 attach worker；所有外層自動 reload/導航是否走 session 仲裁；共用規則是否仍只有一份；API/設定/打包變動是否有無損結果映射。沒有文字衝突不代表行為正確。

跑受影響的 unit/contract、U/X/I、browser、frozen 與 V cases。runtime 或 scripts 有變就更新相關 live gate，不沿用不同 SHA 的結果。U05/U06/U08 是代表性的受控演練，不保證將來零衝突，也不需要先重構其他平台。

### 22.3 升級程序

1. 保存可回退的 binary/commit、設定備份及其配對關係；秘密檔案不提交。
2. 停止使用同組設定／coordinator 的舊實例，人工核對在途交易；不要把舊 session memory 搬到新型別。
3. 使用符合 OS/architecture 的獨立 frozen upgrader，先 dry-run。基準的 mixed-equal profile 一樣要轉換；衝突預設失敗，操作者明示取捨。
4. 輸出新路徑、驗證 accounts/非 uTiki 資料保持，記錄 manifest；多檔失敗依 manifest 處理，不宣稱跨檔 atomic。
5. 用新 frozen bot/settings 檢查資源與配置，且不匯入 converter。真正啟動測試依 §14.6，不只 `--help` 或匯入成功。
6. 從明確新起點建立 session；已知／未知舊交易按 §8.7 人工處理，不自動再次加入購物車。

新 profile 本來完全符合唯一正式格式可直接使用。個別操作者明確選擇重建配置可以不用 converter，但不影響公開升級版本必須提供可用 frozen upgrader 的要求。

### 22.4 回退程序與限制

停止新版，保存脫敏 evidence，核對網站實際交易，再配對回退舊 binary/commit 與原設定備份；ownership 接合組合一起回退，不讓兩版同時操作 tab。不要靠新版內的一套舊引擎作 fallback。

coordinator schema 預設不變；不可清 cooldown/lease、刪 DB 來讓程式重試。若確有 schema 變更，另核准升降版矩陣；只換 wrapper 不應偷偷改 shared 協議。

本地回退不取消已送出的網站請求。使用者回報「沒有訂單」也不會變成可證明的 server receipt；保留資訊來源與警示。跨崩潰沒有持久化 ledger，不宣稱 exactly-once。

### 22.5 主要風險與對應驗收

| 風險 | 必要證據／處理 |
|---|---|
| 三個 reducer 串行但跨 phase 仍衝突 | X collision matrix、ActiveEffect terminal/held 狀態與 late receipt |
| 普通刷新優先級過高拖慢購票 | ready-vs-refresh 測試與 V15，本地撤銷未 commit refresh 權限 |
| 模型有進無出、人工 restart 清掉未知 | T 系列、完整 state/event 預設保持規則、retired-unresolved |
| input ready 虛假或 stale OCR 填新頁 | I 系列、native setter與框架回讀、input/challenge identity |
| 設定鏡射被 loader 偷轉換 | C/G/U10，mixed-equal 輸入拒絕與獨立轉換 |
| source 可用但 frozen 缺 scripts/upgrader | P3/P6a package gate、G/H、無 source tree/無 system Python |
| interception 失效真的外送 | H01–H04，獨立 OS 網路限制＋故障注入 |
| 型別／marker／案例集合或語法 gate 假綠 | H05–H08/H11/A06；獨立 catalog＋四輸入核對、逐檔語法範圍；聯合刪例／錯 marker／漏新檔／舊 JUnit 均拒絕 |
| 頂層 integration 增生或藏 legacy | ADR-01、U11/U12、精確 current-core inventory |
| 刪除／發布前沒有 live 授權或證據 | P6b→P6c／P8 adoption/evidence gate，不預設放行 |

---

## 23. 完成定義與防回歸護欄

### 23.1 必須逐項附證據

- [ ] 維護者正式採用 v1.5/digest、Issue/branch/實作範圍已記錄；live/release 授權獨立。
- [ ] ADR-01 選擇直接新 bootstrap 或有證據的 current-core 適配；無重複 composition root。
- [ ] domain/profile/config 不拉入 browser/UI/OCR/I/O；application 不拼 JS 或直接操作 tab。
- [ ] 唯一 session writer、唯一 ActiveEffect；三個 reducer 只提 proposals，所有 mutation 經仲裁。
- [ ] unresolved submit 跨 callback/timeout 仍禁止旁路 reload/nav/input；matched late receipt 不被 policy revision 丟棄。
- [ ] ordinary refresh 讓位 ready purchase；已進 permit commit 或可能發送者不可被假定無效果而搶佔。
- [ ] PurchaseState/InputState 每個 state 有出站／保持／拒絕規則；人工 resolution、retire、新 cycle 可測且不自動重送。
- [ ] CAPTCHA/reset/OCR/input evidence 有 context/version，native setter 唯一來源與回讀失敗會阻止送出。
- [ ] snapshot 不使用 magic 欄位索引；raw decode 一次，狀態更新 structural sharing。
- [ ] 關閉與 late completion 不復活 session、不清其他 token；所有工作／waiter／lease 有界。
- [ ] 新 runtime/load/hot reload 不轉換舊棒球配置；mixed equal 拒絕並指向獨立升級。
- [ ] 公開升級有 Windows x64/macOS arm64 frozen upgrader、checksums、manifest、無 Python E2E與配對回退。
- [ ] 新 API/caller/UI/default/save/spec 一致；舊 facade/model/decoder/workflow/alias/mirror/test bridge 最終清零。
- [ ] current-core 必要適配只在模組外且有獨立契約證據；沒有整個目錄掃描豁免。
- [ ] 所有新增／重寫模組含 application/ports/adapters/bridge/upgrader 都在 typed manifest；沒有廣泛 Any 逃逸。
- [ ] required catalog 已按本計劃 153 個固定 ID／全部必要 obligations 核准，獨立於測試收集及 execution manifest；所有修改附 review 與 old/new digest。
- [ ] checker 同時核對 catalog、execution manifest、JUnit、run metadata 與受保護 expected digest；完整集合、重複／未知 ID、適用性、run/attempt/SHA/digest 及全 gate job/target 均可驗。
- [ ] browser job 保留 strict-config/strict-markers；適用 required case 的缺失/skip/xfail/xpass/未執行皆非零，不能用舊 XML 或輔助 case 假綠。
- [ ] 本階段及最終累積 Git 變更集合與 modified-Python manifest 相符；production、tool、build helper、test 及所有新檔逐檔 py_compile；明確刪除另記，非預期缺檔／漏檔／編譯失敗非零。
- [ ] A06/H06/H11 負向測試確實能攔截聯合刪例、未核准 catalog、偽造不適用、錯 digest/SHA、漏新檔及未被 pytest import 的語法錯。
- [ ] production-origin fixture 有獨立 hard-egress deny；interceptor 故障測試證明無真站外送。
- [ ] P3 首次資源外部化已同步打包／frozen 自測；不是到 P8 才發現 missing JS。
- [ ] P6b 指定 live evidence 已先於 P6c 最後刪除；無窗口則 DELETE_BLOCKED，不把規範改掉湊過。
- [ ] D/N/S/R/L/C/A/P/U/V/X/T/I/G/H 的適用案例都映射到執行證據，未驗證如實列出。
- [ ] 同條件 cold/warm/median/p95/p99、CDP／網站請求數、失敗率、CPU/資源可核對；沒有無收益排程退化。
- [ ] 最終 source/build/runtime/script digest 一致；P7/P8 修改後重跑相關驗證，舊 SHA 報告不冒充新版本已測。
- [ ] 殘餘新風險有影響與處理範圍；不聲稱永久零技術債、跨崩潰 exactly-once、全平台已加速或上游零衝突。

### 23.2 機器檢查與人工審查的分工

AST/import scanner 驗 DAG、retired symbol、raw browser/filesystem primitive、不可信資料進入點、resources 與 typed manifest；它不能證明動態 Python 沒有任何非法行為。

contract/stateful tests 驗 claim/reducer/控制/receipt 時序，browser tests 驗 production JS，frozen tests 驗真正交付物，live evidence 驗當前受影響站點。各層不互相冒充。

required catalog 獨立定義必要 case/obligation/variant、層級、gate/OS/tenant 與適用條件；execution manifest 只映射實作／node ID／證據。`verify_utiki_ci_results.py` 同時接收這兩份檔、JUnit、run metadata，核對外部核准 catalog digest 及完整集合。測試搬家可更新 node ID，不能刪除 invariant 或讓不適用條件由執行清單自行宣告；細節見 §16.11／§17.5。

逐檔語法 checker 另外從 Git／核准 build-source inventory 推導完整 modified-Python 集合，交叉核對 manifest，再以 py_compile 編譯每個應編譯檔。不得依 pytest import、typed scope 或固定 compileall 目錄猜測覆蓋完整；syntax PASS 只證明該 interpreter 下語法可編譯，不證明測試／browser／build／live 已通過。

本輪文件交付的格式／引用／差異檢查結果獨立記錄於 `DOCUMENT_CHECK_RESULTS_v1.5.md`。本文件內以上 checker、required catalog 與語法 report 是待實作契約，不因文件檢查 PASS 而視為已建立或可執行。

不以固定 class/檔案數或每函式 50 行替代責任審查；尤其不能把同一次 browser guard+commit 為湊行數拆成多輪 CDP。

### 23.3 清理與擴充實驗

A：新增一個排序規則，只動選擇／policy 與必要 UI metadata，不改 lease或arbiter。

B：網站 selector 變更，只動 browser/codec/fixture；真流程語意變更才改 reducer，不任意放寬 guard。

C：新增已知拒絕，必須補 classifier/轉移/必要 recovery/collision case；正常熱路徑不新增一輪查詢。

D：移除舊棒球 API/model/fixture helpers，新模組仍用新 ports 獨立測試；不需要轉成舊 state 才能通過。

E：上游新增 gate，已執行 worker 的新 claim 會受限制；舊已發生的 receipt 仍正確歸屬，不被當成「stale 就全部丟」。

F：故意將 ordinary refresh 與 ready submit 同時提出，普通刷新不能搶先；將 submit 改成 held unknown，兩者都不能製造新 mutation。

G：故意拔掉 JS data 或 interception，frozen／isolation gate 正確變紅；不能 fallback 舊 script 或外連真站。

---

## 24. 交給 Codex／程式代理的工作指示

### 24.1 預設為審查，不是自動實作

```text
請審查 UTIKI_ROOT_REFACTOR_PLAN_v1.5.md。
這是待採用計劃，不是修改 repository、建立 Issue/PR、升級設定或真站操作的授權。
維護者尚未明確核准當次實作前，僅做只讀審查與必要的文件回覆。

優先核對：
1. §6 ActiveEffect 是否涵蓋三個 reducer、輸入 mutation 與外層核心副作用旁路。
2. eligibility barrier 與 priority 是否分開；held submit 不被任何 refresh破壞；ready purchase不被普通refresh餓死。
3. §8 所有狀態的合法出站/保持/late receipt/close/人工retire及new-cycle邊界。
4. Input/CAPTCHA的context/token/reset/provider/native setter evidence是否完整，且無額外不必要CDP。
5. mixed equal是否一致要求offline轉換，公開升級是否真的交付no-Python frozen upgrader。
6. P3同步resources/spec/frozen；P6b指定live在P6c最終delete之前；最後artifact證據是否吻合。
7. strict/type/hard-egress 原契約保留；required catalog 是否獨立核准，四輸入是否能抓聯合刪例／xfail／xpass／偽造不適用；逐檔語法 gate 是否涵蓋全部實際修改 Python 檔。
8. ADR-01是否有理由採直接新bootstrap或必要外部核心服務映射；不以假平台自動批准通用integration層。

找出具體矛盾或缺失，不以類別/行數/多位agent同意作正確性證明。
區分計劃缺口、從原始碼確認的現況、已重現bug、尚未驗證的假設。
```

### 24.2 只有正式採用與當次實作授權後才使用

```text
以維護者採用的v1.5版本/digest與當次核准範圍為準。
先P0核對actual HEAD、工作目錄、upstream/merge base與Issue；不reset覆蓋任何使用者工作。
不把附件、舊對話或「使用者偏好乾淨架構」當作自動取得repo/真站/發布授權。

最終棒球runtime沒有舊facade/alias/model/decoder/workflow/mirror/test bridge。
只有真正current-core契約必要的模組外薄適配可保留；直接新bootstrap不是歷史facade。
既有CTBC域名/credential keys、共享scope與必要驗證功能不可誤刪。

三個reducer只提proposal；session arbiter唯一授權，ActiveEffect持有到效果被確認。
有未決submit時普通/排程/429 reload都不得破壞當前證據；core旁路同樣受控。
ready purchase優先於普通refresh，仲裁只做本地有界檢查，不加50ms hop或CDP查詢。
新舊policy不能讓舊submit receipt消失；取消/timeout不是NO_EFFECT。

Input/CAPTCHA是purchase內唯一子狀態，OCR provider不寫state、不發submit。
native setter來自唯一現行來源，填值失敗不送出，late OCR/reset evidence不能用在新context。
所有恢復與blocked/accepted/held都有正式出口；人工解決不直接READY，未知abandon不解鎖。

mixed equal仍要求獨立offline upgrader；公開 upgrade必須有Windows/macOS frozen artifact。
無system Python/source tree的frozen升級E2E與checksum/manifest是交付門檻，不只Python script。
P3首次外部化JS同步spec/build/frozen，P6b指定live先於P6c final delete。
無live窗口停在候選/DELETE_BLOCKED，不默改工程規範，也不發布雙引擎fallback。

每個新增/重寫模組都進typed scope；覆寫pytest addopts時補strict flags。
required catalog 從計劃規格建立並獨立核准，不從測試收集或 execution manifest 生成。
四輸入 checker 驗完整 case/obligation/variant、適用性、skip/xfail/xpass、run/attempt/SHA/digests；測試與 mapping 同刪仍失敗。
所有實際修改的 production/tool/build/test Python 檔從 Git 獨立核對 manifest，再逐檔 py_compile；漏檔／新檔未列／編譯失敗不得放行。
production-origin測試需CDP interception+獨立hard-egress封鎖；權限不足不能冒稱隔離通過。
依D/N/S/R/L/C/A/P/U/V/X/T/I/G/H案例報告實際證據和未驗證項目。

不提高刷新頻率/繞過網站或coordinator限制，不新增自動付款或驗證規避。
不全面重寫其他平台/主迴圈，不為省自訂caller幾行diff留下歷史相容。
```

本次交付只生成計劃、審查回應與文件差異；沒有修改repository、建立branch/Issue/PR、執行購票／build／真站／升級。

---

## 附錄 A：每階段證據紀錄範本

```text
Plan version / digest / adoption / Issue:
Phase / allowed scope:
Baseline SHA / actual input SHA / candidate SHA:
Upstream URL / SHA / merge base / touched symbols:
Source / script / binary / config-contract digests:
Case ID / invariant / old behavior / intended new behavior:
Type: behavior-preserving / bug-fix / breaking-change / performance-change
Canonical owner / ActiveEffect / event / receipt evidence:
Retired API / model / config / test bridge:
Retained CURRENT-core adapter and ADR/caller/necessity:
Typed manifest / effective config:
Required catalog / approved digest / review / plan digest:
Execution manifest / JUnit / run metadata / run+attempt+job / digests:
Applicable / not-applicable / not-due obligations / gate job+target completeness:
Modified-Python base / candidate / working-tree digest / files / syntax report:
Commands / collected / passed / failed / skipped / xfail / xpass:
Isolation mode / independent canary / outbound evidence:
Frozen OS / architecture / no-Python environment / assembled bundle:
Upgrade source classification / output / rollback evidence:
Live authorization / tenant / page / boundary / candidate:
Performance cold+warm / samples / median+p95+p99 / uncertainty:
CDP commands / ticketing requests / failures / event-loop lag / retained resources:
Not run / assumptions / new risk / next blocked gate:
Result: PASS / FAIL / NOT RUN / INCONCLUSIVE / LIVE_BLOCKED /
        FINAL_DELETE_BLOCKED / RELEASE_BLOCKED / NOT APPLICABLE
```

NOT APPLICABLE 必須符合獨立 required catalog 的條件並指出核准來源；尚未到期另記 NOT_DUE，缺授權／環境記 BLOCKED，不能免除公開升級的 frozen upgrader、主要 state collision 或適用 live gate。文件檢查的 PASS 只表示文件檢查，不能填到 browser/build/live 欄位。

## 附錄 B：正式 API 的最低契約

| 操作／型別 | 輸入／輸出 | 必要限制 |
|---|---|---|
| `bootstrap.create_engine` | 已驗證 policy、browser/service ports → 一個 engine | 新工廠，不接 legacy API，不建立第二 registry |
| `attach` | TabKey、startup context → handle或拒絕 | 來源不明舊購票頁不自動開始 |
| `advance` | control revision、事件／observation → typed result | 只歸納需要的分支，ready立即續行 |
| `arbitrate_effects` | 當前session/gate/proposals → 至多一個mutation grant | 純函式、本地、有界；未知外部效果不可搶佔 |
| `EffectGranted/Receipt` | claim/operation/context、precise evidence | proposal 不是已發送；receipt 不因新policy而丟失 |
| `observe_page` | 固定needed-fields mask/context → coherent observation | 不自由輸入任意selector，不發購票副作用 |
| `commit_selection/commit_and_submit` | plan、input evidence、claim → outcome | live audit同次執行，partial mutation不能偽裝NO_EFFECT |
| `RecognitionProvider` | 短期圖像材料/context → typed result | 無state/dispatch權，不記錄答案或過期填值 |
| input/reset ports | InputAttempt/ResetId → write/load evidence | current native setter、回讀、matched context；非成功不submit |
| `request_reload/observe_reload` | precise intent/handle → typed blocked/pending/terminal | caller不互借結果，所有核心reload入口經仲裁 |
| `update_settings` | 唯一當前格式／policy → applied/invalid | mixed拒絕、無自動轉換；不抹除未決attempt |
| local manual review/retire/new-cycle | ReviewTarget、explicit disposition、fresh start | 不直接READY、不假造server結果、unresolved-abandon不解鎖 |
| `pause/close_tab/close_all` | control epoch/current identity → cleanup/result report | 不配置不存在的runtime，晚結果不復活，不宣稱遠端撤回 |

名稱可依一致慣例微調，但不得折回 `execute(dict)` 或 `bool` 隱藏語意。對未遷移平台不強制要求同一整套購票 API；對共用核心只接已證明必要的 lifecycle/control 契約。

## 附錄 C：設計決策與拒絕方案

| 決策 | 採用 | 不採用 |
|---|---|---|
| 組裝 | 新 bootstrap；current-core 不匹配才加外部薄適配 | 沒有具體需求先做三層 integration／多重root |
| 狀態 | 單一 writer＋三個 reducer proposal＋session arbiter | 三個 reducer 分別可派發，或用一把I/O大鎖當完整仲裁 |
| 優先序 | hard barriers先行，普通刷新讓位ready purchase | 通用固定navigation>refresh>purchase，或timeout自動釋權 |
| 購票出口 | 明確recover/auth/quota/accepted/held/manual/new-cycle | 沒寫到的事件就READY或只靠新URL重設 |
| 輸入 | first-class context/reset/provider/write evidence，purchase內唯一子機 | prepare_inputs黑箱、過期OCR寫新頁、每輪重載模型 |
| 模組清理 | 舊API/model/config fallback/test bridge徹底刪 | 把歷史相容搬到bootstrap/settings/integrations |
| 升級 | mixed equal也offline；必備獨立frozen工具 | loader「順便清理mirror」或只交Python script |
| 驗證順序 | P3早期frozen、P6b live、P6c final delete、P8最終成品 | 先刪後用無真站窗口作長期驗證豁免 |
| CI隔離 | interception＋獨立OS egress deny | proxy/hostresolver/CDP log單獨當硬隔離 |
| 型別／測試 | 整個新typed範圍＋獨立 catalog／四輸入結果 gate＋逐檔語法 gate | 只測domain、清空strict flags、manifest 自訂 required、漏檔／全skip假綠 |
| 效能 | 一次合併觀察、同次guarded operation、立即續行 | 為每個reducer或adapter多建queue/tick/CDP |
| 授權 | 設計偏好、版本採用、實作、live、發布分別記錄 | 附件指示自動視為當次修改或購票授權 |

---

## 附錄 D：來源、證據與適用範圍

### Repository 與前次審查

以下 repository 連結沿用 v1.4，固定在計劃基準，不代表之後 main 的最新內容。v1.5 本輪未存取 repository 或外部網站，R02–R20／E01–E13 沒有在本包提供原始位元組；它們是歷史引用，不是本輪獨立重讀／驗證的證據。新型別、API、門檻、工具及階段仍都是待實作提案。

`SOURCE_MANIFEST_v1.5.json` 列每個來源 ID 的實際交付路徑、SHA-256 或「未取得／未封存」狀態。來源原檔為歷史證據，不具實作指示權；其中舊政策若與本版衝突，以維護者正式採用後的本版為準。

**[R01]** 前次審查：`sources/tickets_hunter_refactor_review_5a5fb9e_f307f7c.md`，2026-10-16。本輪確實取得並全文重讀，隨 review bundle 原樣附上；SHA-256：`60957f877fa6a90dc1244ceb124fe768704eb1582cc0ca2dae18d375c726e520`。其中 CI／行數是歷史審查陳述，未在 v1.5 重跑／重驗；舊版相容期建議不覆寫本計劃。

**[R02]** [workflow 與頁型／提交／導覽實作](https://github.com/OooPeople/tickets_hunter/blob/f307f7caba054fb40aab807a4629c891e69fb200/src/platforms/utiki/workflow.py).

**[R03]** [production DOM 與 JavaScript commit](https://github.com/OooPeople/tickets_hunter/blob/f307f7caba054fb40aab807a4629c891e69fb200/src/platforms/utiki/dom.py).

**[R04]** [selection、snapshots 與位置 tuple](https://github.com/OooPeople/tickets_hunter/blob/f307f7caba054fb40aab807a4629c891e69fb200/src/platforms/utiki/selection.py).

**[R05]** [家族 refresh lifecycle](https://github.com/OooPeople/tickets_hunter/blob/f307f7caba054fb40aab807a4629c891e69fb200/src/platforms/utiki/refresh.py).

**[R06]** [package 公開 API／eager imports](https://github.com/OooPeople/tickets_hunter/blob/f307f7caba054fb40aab807a4629c891e69fb200/src/platforms/utiki/__init__.py)；[舊 facade](https://github.com/OooPeople/tickets_hunter/blob/f307f7caba054fb40aab807a4629c891e69fb200/src/platforms/ctbc.py).

**[R07]** [設定 CRUD、遷移與原子儲存](https://github.com/OooPeople/tickets_hunter/blob/f307f7caba054fb40aab807a4629c891e69fb200/src/settings.py)；[UI 行為設定](https://github.com/OooPeople/tickets_hunter/blob/f307f7caba054fb40aab807a4629c891e69fb200/src/www/settings.js)；[設定測試](https://github.com/OooPeople/tickets_hunter/blob/f307f7caba054fb40aab807a4629c891e69fb200/tests/test_settings_config.py).

**[R08]** [基準 CI workflow](https://github.com/OooPeople/tickets_hunter/blob/f307f7caba054fb40aab807a4629c891e69fb200/.github/workflows/tests.yml).

**[R09]** [真實瀏覽器 fixture runner](https://github.com/OooPeople/tickets_hunter/blob/f307f7caba054fb40aab807a4629c891e69fb200/tests/test_ctbc_dom_fixtures.py).

**[R10]** [pytest marker 與預設排除](https://github.com/OooPeople/tickets_hunter/blob/f307f7caba054fb40aab807a4629c891e69fb200/pytest.ini).

**[R11]** [既有 SQLite refresh coordinator](https://github.com/OooPeople/tickets_hunter/blob/f307f7caba054fb40aab807a4629c891e69fb200/src/refresh_coordinator.py).

**[R12]** [代理規範](https://github.com/OooPeople/tickets_hunter/blob/f307f7caba054fb40aab807a4629c891e69fb200/AGENTS.md).

**[R13]** [程式邊界規範](https://github.com/OooPeople/tickets_hunter/blob/f307f7caba054fb40aab807a4629c891e69fb200/docs/02-development/code-boundaries.md).

**[R14]** [前一階段重構與相容期計劃](https://github.com/OooPeople/tickets_hunter/blob/f307f7caba054fb40aab807a4629c891e69fb200/docs/02-development/utiki-refactor-plan.md).

**[R15]** [共用 keyword、random 與 util](https://github.com/OooPeople/tickets_hunter/blob/f307f7caba054fb40aab807a4629c891e69fb200/src/util.py).

**[R16]** [主迴圈、50 ms tick 與雙層 health 入口](https://github.com/OooPeople/tickets_hunter/blob/f307f7caba054fb40aab807a4629c891e69fb200/src/nodriver_tixcraft.py).

**[R17]** [bot PyInstaller spec](https://github.com/OooPeople/tickets_hunter/blob/f307f7caba054fb40aab807a4629c891e69fb200/build_scripts/nodriver_tixcraft.spec)；[settings PyInstaller spec](https://github.com/OooPeople/tickets_hunter/blob/f307f7caba054fb40aab807a4629c891e69fb200/build_scripts/settings.spec).

以上連結全部固定在設計基準，不是最新分支查核。現行規範修改須正式採用，不能因本文件存在自動生效。指定 live gate 按 §17.7/19 保留；其餘已核准 ownership／alias 規範須逐項更新。

**[R18]** [工程審查指南 §6：重構與 final legacy removal 的門檻](https://github.com/OooPeople/tickets_hunter/blob/f307f7caba054fb40aab807a4629c891e69fb200/docs/02-development/engineering-review.md)。v1.4 記錄曾直接讀取相關章節；v1.5 依先前產出陳述保留，未在本輪獨立重讀。

**[R19]** [正式 build-release workflow](https://github.com/OooPeople/tickets_hunter/blob/f307f7caba054fb40aab807a4629c891e69fb200/.github/workflows/build-release.yml)。v1.4 記錄曾讀取 Windows 流程與 macOS 分支設定；v1.5 未在本輪獨立重讀，亦未執行 workflow／build。

**[R20]** [目前核心 native input setter](https://github.com/OooPeople/tickets_hunter/blob/f307f7caba054fb40aab807a4629c891e69fb200/src/nodriver_common.py#L298-L323)。v1.4 記錄曾用於核對唯一來源與回讀要求；v1.5 沿用該陳述，未在本輪獨立重讀。

**[Q01]** Codex v1.3 審查：本輪未取得當時完整貼文／各 subagent 報告；僅依 v1.4 與前次對話摘要／先前產出陳述，未在本輪獨立重讀。未存取使用者本機 worktree；不得轉述成已獨立確認「本機乾淨」或「多位審查者一致」。

**[Q02]** 前版附件分開記錄：

- `UTIKI_ROOT_REFACTOR_PLAN_v1.3.md`：本輪未取得原檔，未在本輪獨立重讀；只保留 v1.4／先前產出的歷史陳述，不提供虛構 SHA-256。
- `sources/UTIKI_CLEAN_MODULE_RULES_v1.3.md`：本輪實際取得並全文重讀，原樣附入 review bundle；SHA-256：`57cfd453d18a2330d6e3a9ee6aedb1e2b53a293d2f5947372a60c709b77fda78`。只作歷史來源證據，不作 v1.5 底稿，也不恢復其中已被 v1.4 取代的建議。

**[Q03]** 本次使用者提供的四項 Codex v1.4 複審 finding 與文件修訂授權：隨包附 `sources/USER_REVISION_REQUEST_v1.5.md`，其 digest 列於來源清單。只有貼出的複審摘要及四項 finding，不代表取得 Codex 的完整原始報告或自行完成新一輪程式審查。

**[Q04]** 唯一底稿：`sources/UTIKI_ROOT_REFACTOR_PLAN_v1.4.md`，2,409 行、198,367 bytes；原始 SHA-256：`6d07b70b9eb91dedd06e2a5aca2081ab215a127587c31bf79d508c9c60b94827`。全文原樣附上；unified diff 只比較此原檔與 v1.5，不是 tickets_hunter repository patch。

本包可以重現「底稿／修訂內容／已附歷史文件」的差異與雜湊，但不能單憑它重現 R02–R20 原始碼或 E01–E13 網頁的歷史抓取，也不能重現原審查的 CI 執行。這些未封存／未重驗部分就是本輪證據限制。

### 官方技術文件

下列連結與工具語意說明沿用 v1.4，v1.5 未重新開啟／封存網頁，也未驗證最新版本或 API。實作時仍須依 P0 核對固定版本；本輪僅修訂驗收規格，沒有新增外部查核成果或依最新頁面要求升級。

**[E01]** [Python 3.11 typing／Protocol／NewType](https://docs.python.org/3.11/library/typing.html) — 型別註記不會自動在執行期逐次驗證所有資料；靜態型別與不可信輸入驗證的責任應分開。

**[E02]** [Python 3.11 asyncio tasks](https://docs.python.org/3.11/library/asyncio-task.html) — Task、取消、TaskGroup、sleep 的語意；取消後清理與重新拋出、持有工作引用，以及不把每一步變成不必要排程。

**[E03]** [Python 3.11 dataclasses](https://docs.python.org/3.11/library/dataclasses.html) — frozen 與 slots 的工具能力；本計劃仍要求內層資料不可變，不以 frozen 外殼冒充深度不可變。

**[E04]** [pytest markers](https://docs.pytest.org/en/stable/how-to/mark.html) — marker 註冊與嚴格檢查；零收集／全 skip 的 gate 是本計劃另外要求實作。

**[E05]** [Hypothesis stateful testing](https://hypothesis.readthedocs.io/en/latest/stateful.html) — 以動作序列與 invariant 測試狀態轉換；不代表可以自動替代真實 DOM。

**[E06]** [PyInstaller run-time information](https://pyinstaller.org/en/stable/runtime-information.html) — frozen runtime 與資料檔路徑處理；JS data 必須明確被收錄並在 frozen app 驗證。

**[E07]** [Python 3.11 time](https://docs.python.org/3.11/library/time.html) — 以 monotonic／perf_counter_ns 量測本機時間差；不與其他時鐘域的絕對值直接相減。

**[E08]** [MutationObserver](https://developer.mozilla.org/en-US/docs/Web/API/MutationObserver) — 可觀察的 DOM mutation 與 observer lifecycle；不是所有 JavaScript 狀態的通用變更通知。

**[E09]** [Chrome DevTools Protocol](https://chromedevtools.github.io/devtools-protocol/) — Runtime／Page／Fetch 的事件與操作。固定版本、實際命令支援、interception isolation 必須另以專案測試證明；不只根據 tip-of-tree 文件假設可用。

**[E10]** [Performance.now](https://developer.mozilla.org/en-US/docs/Web/API/Performance/now) — browser 端計時的 time origin／單調時間語意；跨 document／host 比較要設計共同量測邊界。

---

**[E11]** [Docker：none network driver](https://docs.docker.com/engine/network/drivers/none/) — 參考測試部署的loopback-only網路語意；實際runner權限／hard-egress成立仍須故障注入證據。

**[E12]** [mypy：running mypy and managing imports](https://mypy.readthedocs.io/en/stable/running_mypy.html) — explicit file/module範圍、import處理與型別逃逸；本版的typed manifest/negative gate為設計要求。

**[E13]** [pytest command-line/reference](https://docs.pytest.org/en/stable/reference/reference.html) 及 [argument composition](https://docs.pytest.org/en/stable/example/simple.html) — addopts/command-line選項與strict flags；不能因清空addopts順便移除驗證。

---

## 最終交付判準

**正式棒球模組只有新架構；任何只為重構前版本存在的 API、模型、狀態、設定fallback或測試包裝都不保留。現行核心的必要薄適配獨立、有證據，不把正常的新工廠誤認為legacy。**

新架構不只分層漂亮：跨reducer效果由一個session claim仲裁，未知結果不被刷新破壞；所有恢復與人工處理有明確出口；輸入generation與原生回讀能阻止過期填值。這些規則用本地有界判斷與既有browser operation實作，不增加無用途的等待。

實作交付包括可使用的frozen升級路徑、硬隔離browser CI、完整typed scope、獨立 required catalog／四輸入核對、所有修改 Python 檔逐檔語法 gate、早期打包與刪除前實機證據。**本文件只完成設計修訂，仍須獨立審查、正式採用及實作驗收；沒有聲稱這些程式、測試、成品或真站結果已經完成。**

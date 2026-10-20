# uTiki 低延遲重構 v1.6：個人原始碼使用、零歷史相容與離線可驗收清理

**專案：** `OooPeople/tickets_hunter`
**架構設計參考基準：** `f307f7caba054fb40aab807a4629c891e69fb200`
**文件日期：** 2026-10-17（Asia/Taipei）
**版本：** 1.6 — 依使用者確認的 1A／2保留／3B／4B，縮減產品交付範圍；不重寫核心架構
**狀態：** `PROPOSED / REVIEW REQUIRED`。需求方向已確認；本版全文、實際 catalog 位元組與 repository 接入仍須依當次授權採用。本文件不等於程式、測試或真站驗收已通過。
**唯一修訂底稿：** `UTIKI_ROOT_REFACTOR_PLAN_v1.5.md`，2,679 行；SHA-256：`c8fcc741a37db3d404cefd73cfcda6731a24a922f42522777fbdbc3eeddf6328`。
**P1 歷史出站基準：** 使用者轉述的 `32c626dcb89e979a18b3b3d91e267bb9137a5899`、P1 run `38965038276`／attempt 1；本輪未重新查核遠端，不假設目前 HEAD 仍停在此處。[Q06]

> **用途：** 僅供維護者個人使用；Windows x64 與 macOS arm64 直接執行原始碼，保留現有設定網頁、其他平台功能與設定。
>
> **刪除的交付要求：** 不提供設定升級器、舊設定自動轉換、PyInstaller 成品、frozen／無 Python 驗證、安裝包、產品發布或 release 流程。測試專用 Docker image、CI artifacts 與原始碼依賴準備保留，不屬於產品打包。
>
> **設定：** uTiki 只接受唯一 current 格式；舊 uTiki 行為由使用者重新設定，不自動搬值、比較 mirror 或選邊。其他平台與帳號資料不得因重設而受損；本文件不授權代理覆寫私人設定。
>
> **刪舊碼：** 新責任接替、適用離線驗收、必要獨立覆核與可回復版本成立後，可依本版採用與既有實作授權在工作分支清除無用 legacy。真站由使用者之後另行手動驗收，不再是 P6c／P8 的出站前提；未知交易仍不可自動重送。
>
> **不變的重點：** 單一 session writer／arbiter／ActiveEffect、完整 Purchase／Input／CAPTCHA 轉移、唯一 native setter、精確 cleanup、低延遲、strict/type/modified-Python、獨立 catalog 與 hard-egress 全部保留。不新增大型框架。

## 採用與授權欄位

| 欄位 | 本文件交付時 |
|---|---|
| 已確認需求 | 使用者回覆「1a 2保留 3b 4b」；個人使用、不做升級器、不做產品打包；確認文字封存在 `sources/USER_CONFIRMED_REQUIREMENTS_v1.6.md` |
| 原始碼支援目標 | Windows x64／macOS arm64；Linux x64 是現有 CI／隔離參考環境，不擅自新增 Linux 個人使用產品承諾 |
| 重設範圍 | 僅 uTiki／自訂模組行為設定；其他平台、accounts、Cookie／profile 及共享 coordinator 資料不在重設範圍 |
| 清理政策 | 已確認選 B：離線驗收及獨立覆核後可刪無用舊碼；不要求刪除前真站成功，不自動取得真站或 Git 歷史改寫權 |
| v1.6 全文採用人／日期／計劃 digest | 待正式採用；不可將需求確認冒充已核准尚未產生的 catalog digest |
| Issue／branch／工具操作 | 沿用已核准範圍並於接入時核對；歷史上下文是 Issue #17、`refactor/17-utiki-root-v1-5`，不是本輪確認其最新狀態 |
| 真站操作／公開發布／merge | 本次不新增授權；真站另行由使用者手動驗收，公開發布不在 v1.6 交付範圍 |
| 實際完成的本輪工作 | 閱讀 v1.5、文件修訂、案例處置與差異／格式／一致性檢查；詳 `DOCUMENT_CHECK_RESULTS_v1.6.md` |
| 本輪未執行 | repository 存取／修改、CI、pytest、mypy、repository py_compile、browser、Docker build、benchmark、私人設定變更、真站、發布；未重新查核外部網站 |

需求確認是規格變更依據，不代表附件中的命令已執行。Codex 正在進行的工作在安全檢查點接入本版；不得 reset 到歷史 P1 SHA、覆寫未提交工作或中途改動受審查快照。

## 閱讀導引

- 保留的核心架構：§4–10、§12–13；session arbiter 與完整狀態轉移不重寫。
- 個人原始碼使用與設定重建：§11、§14、§18.1、§21.4–21.5。
- 案例與 catalog 變更：§16.9–16.11；有效案例 143 個，撤回的 13 個 ID 不重用，3 個替代案例有新 ID。
- 完整測試與網路隔離：§15–17、§21.3；`live audit` 指操作當下的 DOM 核對，**不是**真站 smoke，不能隨真站門檻移除。
- 階段：§19；沿用編號，P6b 改為離線候選覆核，P6c 清理，P8 最終原始碼驗收。
- 進行中版本切換與 Codex 交接：§2.5、§24；不重做 P1、不靜默放寬舊 catalog、不另建驗收框架。

本版保留 v1.5 24 個主要章節。新增／修改的是交付政策與其驗收投影；新 API、工具接點及測試範圍仍需依實際 HEAD 核對。本文的 `frozen=True` 是不可變資料模型屬性，不是已取消的 frozen 可執行成品。

---

## 1. 決策摘要與本版變更

### 1.1 個人使用目標與必須解決的根因

本計劃的主體仍是購票模組架構重構，不是驗證平台、安裝器或產品發布系統。使用者願意重填 uTiki 行為設定，換取單一新格式與乾淨的正常執行路徑。[Q05]

| 編號 | 根因／目標 | 結構解法 | 可驗證結果 |
|---|---|---|---|
| F1 | workflow 混雜流程、DOM、JavaScript、輸入與恢復 | 純 domain、薄 application、具體 browser 邊界 | application 不直接操作 DOM／檔案或拼腳本 |
| F2 | 狀態可被分散修改 | engine-instance registry、單一 writer、具名不可變 state | adapter/task 只能回報結果 |
| F3 | production JS 缺少必要自動驗收 | 真瀏覽器、production scripts、不可假綠的 CI | guard／JS／資源損壞會失敗 |
| F4 | snapshot 位置 tuple | 具名 records、單一 codec | domain 不靠 seat[8] 解讀欄位 |
| F5 | 純 import 拉入瀏覽器 | 輕量 root、新 bootstrap 工廠 | 無 browser/OCR 依賴可測純核心 |
| F6 | 新舊 API／模型／設定共存 | caller 同步遷移、使用者重新設定 uTiki、離線驗收後清理 | runtime 無歷史 fallback／mirror |
| F7 | 測試依賴私有字典／包裝 | typed contracts、事件序列、fixture | 不為舊測試保留 production alias |
| F8 | 規範無法防回歸 | 既有 import/type/resource/scenario gates | 漏測與違反 ownership 可被攔住 |
| F9 | 分層增加延遲 | 同 worker 立即續行、批次讀取與 guarded commit | 無新增固定 sleep／無用途 CDP |
| F10 | 上游交界深改或假借核心相容留下舊模組 | ADR、current-core inventory、語意演練 | 只保留真正必要的核心接合 |
| F11 | reducer 各自正確但效果衝突 | session 級 proposal 仲裁、唯一 ActiveEffect | unresolved submit 不被其他操作破壞 |
| F12 | 恢復／人工處理出口不完整 | 完整主狀態、InputState、retire/new-cycle | 沒有未定義的自動回 READY |
| F13 | 交付超出個人用途 | 取消升級器與產品打包，雙平台原始碼驗收 | 不再為無人使用的成品維護 gate |

F 系列是設計根因與目標，不是 required case ID，也不是已重現 bug 的嚴重度清單。

### 1.2 採用的責任配置

Domain 處理資料、規則與 transition；application 的 session writer 歸納事件並仲裁 proposal；browser／service ports 實作外部能力；新 bootstrap 只組裝一次。Inputs 屬 purchase，不另建自主第四引擎。

current-core 必要適配只映射現行服務／控制契約，可位於模組外；domain/application 不反向依賴核心或適配。ADR-01 預設不建立 `integrations/` 家族框架，也不以「檔案移到模組外」證明比較乾淨。

### 1.3 保留與取消的範圍

保留 session arbiter、Purchase/Input 全出口、唯一格式、strict/type/語法完整集合、hard-egress、原始碼資源測試及效能方法。**不採用 `navigation > refresh > new purchase` 的固定通用優先序**；先驗 eligibility，普通刷新讓位已就緒購票。

取消的是個人不用的升級／產品成品／發布要求及刪除前真站 gate，不取消必要的資料保護、原生事件語意、帳號隱私與真站操作授權。

### 1.4 乾淨的完成定義

最終只有新 uTiki 架構，無用舊 API／模型／workflow／設定 fallback／private test bridge 已被接替並移除。Windows／macOS 可直接執行原始碼，設定網頁可用，其他平台不受損，速度無無理由退化。

結案可為 `SOURCE_REFACTOR_COMPLETE / LIVE_NOT_VERIFIED`：原始碼工程驗收完成，但未宣稱真站成功。這不是 release，也不表示未來永無技術債。

### 1.5 v1.6 需求變更的完整投影

| 已確認選擇 | 本版規格 | 必須同步調整的位置 |
|---|---|---|
| 1A Windows＋macOS | Windows x64、macOS arm64 原始碼啟動／依賴／路徑／瀏覽器接合均需適用證據 | source target、P3a、P6a、P8；不得以 Linux 假裝兩平台 |
| 2 保留 P1／測試隔離 | pytest、mypy、CI、modified-Python、Docker 測試 image、artifact 傳遞與 aggregate 保留 | 只調交付相關 scope；不重建 verifier |
| 3B 離線驗收後刪舊碼 | P6b 離線覆核 → P6c 清理；真站由使用者另行手動驗收 | H10 撤回，新增 H12；刪除授權與離線證據仍需成立 |
| 4B 只重新設定 uTiki | 不遷移舊值；提供 current 範例／既有 UI，保留其他平台與 accounts | C/U、設定指引、runtime 拒絕 legacy；G 升級套件整組撤回 |
| 不需要打包 | 不新增／維護本次 PyInstaller 成品、frozen/no-Python、產品 release | P01/P03 撤回，新 P04/P05 驗 source；移除 package／upgrade／live 發布 job |

這是維護者主動改變需求，不是因測試紅燈放寬驗收。catalog 的實際新位元組需 review／digest；「沒有升級器」不得變成永久 skip 的升級器測試。

---

## 2. 基準、證據與治理前置條件

### 2.1 原架構基礎與已完成 P1

原架構基準已有 snapshot → plan → commit、per-tab runtime、提交未知結果阻擋、共享 refresh coordinator 與 browser fixtures；保留有效語意，不從空白系統重寫。[R02–R11]

使用者已提供 P1 出站回覆：候選 `32c626dcb89e979a18b3b3d91e267bb9137a5899`，P1 run `38965038276`／attempt 1 的五個 producer 與 aggregate 成功、63/63 到期義務；一般測試 run `38965038189` 的 Windows／Ubuntu 通過。[Q06] 本輪只依該回覆與先前可見紀錄定位，未重新執行或稽核這些結果。

既有 P1 是後續工具基礎，不是待重建工作。v1.6 變更不追溯作廢舊版驗收；舊證據仍只屬舊 SHA／計劃／catalog。後續階段依新義務驗收，不能把舊 63/63 改標為新版本通過。

### 2.2 本輪證據範圍

唯一底稿為 v1.5 完整原檔，隨包提供並核對原 SHA-256。本輪讀取底稿、使用者需求確認及既有自主規範以完成文件修訂，沒有取得目前工作樹、執行測試或重新瀏覽歷史連結。

R01–R20／E01–E13 是由 v1.5 繼承的歷史定位與技術背景，不是 v1.6 新增的實證。底稿中曾宣稱「本輪重讀」的舊描述不沿用為本輪事實。實際附件及可重現界線見附錄 D、`SOURCE_MANIFEST_v1.6.json`。

本版依使用者的新需求取代舊 frozen／upgrader／live-before-delete 政策；不得再以舊 R17–R19 或早期清理摘要恢復已撤回要求。這不授權更動無關上游規範。

### 2.3 正式採用與既有授權的區分

接入時沿用現有 Issue、分支、adoption 與自主工作紀錄，只記錄本次 v1.5→v1.6 政策差異、文件 digest、source targets、案例處置與允許操作。需求確認已成立；未核准的實體 catalog、未知 reviewer／日期／遠端值不可代填。

正式採用 v1.6 後，3B 的政策授權可供 Codex 在指定工作分支、離線出站及必要覆核成立時執行 P6c；不需要每刪一個已證明無用符號再請示。但資料、Git 歷史、其他平台、尚未接替的能力與不明檔案都不在任意刪除範圍。

文件生成、repository 實作、CI、遠端 variable、真站操作與合併仍是不同權限。本次只製作文件。執行中的 Codex 應依使用者另行貼送的採用指示接入，不因附件存在自行更改遠端核准值。

### 2.4 中間候選與原始碼驗證

修改 production 依賴的中間階段必須可編譯、完成適用測試並可回復原始碼版本；不要求產品打包。未接主入口的新引擎可在隔離 harness 運作，不等於已切換正式路由。

cutover 以最小不可拆 ownership／API／current-config 單位進行；只有一個 owner。未被新入口使用的舊檔可在 P6a 暫存，P6b 離線覆核完成後 P6c 刪除。不保留 runtime fallback 或雙引擎副作用。

### 2.5 進行中的 v1.5 實作如何安全接入 v1.6

1. 到安全檢查點核對 actual HEAD、工作樹與在途 review／CI。保留已完成的 domain／arbiter／browser／P1 成果；不 reset 到 `32c626d…`，不打斷另一個 writer。
2. 盤點本版撤回的升級器、產品打包、發布、刪除前真站 gate：區分尚未開始、已寫未接、已接與正在驗證。停止新增不再需要的工作；已完成內容只作必要可回復清理，不刪其他平台共享程式。
3. 以 §16.9／§16.11 的處置核對現有 catalog 每個 obligation：保留、撤回、改寫適用層級或新增替代。資料／隱私／source 資源檢查不得隨整列升級測試一起遺失。
4. 計劃／catalog revision、固定 ID 集合、gate_profiles、execution manifest、型別範圍、必要 CI 路由與文件在一個可審查接入單位同步。修改 schema 欄位前先查已完成工具；可用既有格式表達就不新增 envelope／registry。
5. 本版文件 hash 與新 catalog hash 都不同於 v1.5。新 catalog 需獨立規格 review、完整 digest 與既有外部核准方式；舊核准不能自動批准新位元組。這是一次有界的需求變更接入，不是重開 P1 的信任模型。
6. 先在隔離副本驗新 scope 的合法對照、舊 catalog／錯版本拒絕、必要案例聯合刪除仍拒絕、撤回案例不再要求、target/job 完整性與語法範圍。測試用核准值不能成為正式 fallback。
7. 正式值尚缺時，可在既有授權內繼續不依賴正式 gate 的工作，但不得宣稱 v1.6 階段出站。只需要維護者對實體新 catalog 做一次明確核准，不反覆為未變內容重開審查。
8. 若既有 `UTIKI_P1_APPROVED_CATALOG_SHA256` 接點只用於 v1.5 歷史 P1，不無故改名或覆寫；若繼續承載現行 scope，須明確記錄版本切換。具體沿用或局部調整由實作者按現況提出一個最小方案；不自動改 variables／branch protection。
9. 新接入完成後，從 actual HEAD 對應的下一個前置成立切片繼續。規範文字提交不代表 runtime 已重測，亦不應製造無限「改紀錄→改 SHA→全套重跑」循環。

自主執行規範的演算法與收斂方式保留；只同步其中「P3 必須 frozen」「P6b 真站擋刪除」「P8 release」等直接衝突政策。§24 提供一次性的接續指示。

---

## 3. 範圍、核心例外與不可破壞條件

### 3.1 必做與不做

本次處理 uTiki 家族的資料／選擇／輸入／購票／導覽／刷新、session 仲裁、瀏覽器契約、current 設定、原始碼資源、測試與必要核心接合。不提供 uTiki 升級器、產品打包或發布；不清空整份設定。其他平台策略與等待不在重構範圍，只有受影響 caller／測試作最小修改。

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

自訂棒球模組內可根本改造；原本為棒球加入主程式的 import／route／reload caller 跟著改成新接口。共用 helper/gate/settings 改動分離提交，盤點所有 caller 與上游觸點，不用「主程式有人呼叫」替舊棒球 API 豁免。

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
| 資料 | 唯一格式、無 uTiki 自動遷移；手動重新設定有清楚步驟；其他平台與 accounts 受保護 |
| 驗證 | 外部化資源同階段驗 source loader／真 browser；離線覆核後清理；原始證據與版本對應 |
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
  check_utiki_architecture.py
  check_upstream_boundary.py
  verify_utiki_ci_results.py
  verify_modified_python.py          # 開發／CI：從 Git 交叉核對範圍，逐檔 py_compile
  run_utiki_typecheck.py
  benchmarks/{bench_utiki,compare_utiki}.py
ci/
  utiki-isolation/                 # 無 egress 的測試啟動與負向 canary
  mypy-utiki.ini
  utiki-typed-scope.txt
  # source target 直接列於 required catalog 的 gate_profiles，不另建產品 target manifest
  utiki-required-cases.json         # 獨立核准的必要案例／義務；不是 execution manifest

tests/
  {unit,contract,integration,browser,performance}/utiki/
  architecture/
  upstream_integration/
  fixtures/utiki/
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

**ADR-01 延續原採用紀錄；本版不重開此選擇。若先前採用 A，繼續 A；缺少可核對紀錄才列待確認，不捏造新核准。**

| 方案 | 優點 | 成本／風險 | 裁決 |
|---|---|---|---|
| A：main → 新 bootstrap，必要 core ports 在外部提供 | 平台業務留 platforms；少一層轉送；新工廠可獨立測 | main 有少量接點；必要服務映射需 caller 清單 | 預設採用 |
| B：頂層 integrations 統一入口／服務＋中立 contracts | 當真實共用 host contract 已存在時可集中接入 | 沒有第二個實際需求時易變成額外 facade／過早框架 | 只有通過下方條件才取代 A |
| C：舊 API facade → 新 engine | caller 少改 | 違反零歷史相容、保留舊契約 | 不採用 |

採 B 必須提供：現行核心的獨立契約與真實 caller、A 無法簡潔滿足的原因、兩方案 shared diff／import／成本比較、契約測試、維護者採用紀錄。**非 uTiki 假平台只能驗證接口中立，不能單憑假平台證明值得建立通用層。**

若 B 真被採用，A 的多餘 factory/轉送須同時收斂，不讓兩套 root 並存。真正外部核心適配仍放模組外；選 A 不是允許把舊相容塞回 bootstrap。

### 4.6 ADR-02／ADR-03：已確認的新方向

- ADR-02：唯一 canonical `utiki` 行為格式；legacy-only／mixed 在 uTiki 啟用時拒絕並指引使用者重填；不提供 converter、不搬值、不比較 equal/conflict，不動 accounts 與其他平台。
- ADR-03：個人原始碼交付。P3a 同期驗 source resources；P6a 整合 → P6b 離線獨立覆核 → P6c 刪舊碼；P8 最終 source 驗收。真站由使用者之後另行手動驗收，不作工程出站前提。

兩項方向來自本次需求確認；正式全文／catalog 位元組採用及 repository 操作仍依 §2 記錄。不新增升級器、reset framework 或公開安裝產品。

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

不讀舊 marker schema。切換原始碼版本前停止舊程序且不承接舊分頁 intent。沒有本地來源、只有 marker 或直接進既有購票頁時，按 §8 的 startup/manual gate，不因「新 marker 不存在」就准送。

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

跨崩潰沒有持久化購票 ledger 的前提不變：重啟不能證明前次沒有送出。第一版在接回來源不明購票頁／使用已有瀏覽器購票 context 時要求人工確認；新 session 的正常合法來源導航不每步再問。單靠從首頁重新啟動也不能提供跨崩潰 exactly-once 保證，使用說明須明示此限制。

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

### 10.4 外部化的同期原始碼資源驗收

第一次將 production 使用的 JS 移到資源檔，同一個可驗收變更須完成 source loader、assembler、必要 source-resource digest／清單與負向測試，不把缺資源問題延到 P8。

- 依模組／package 位置讀取受信任本地檔案，不依賴目前工作目錄；支援 Windows／macOS 路徑與中文／空白目錄。
- 從 repository root 與外部暫存 cwd，以實際支援的原始碼入口／模組位置執行。checkout／source tree 本來就是必要依賴，不做「沒有 source tree 仍可執行」的測試。
- 缺 JS、錯配資源、錯誤 schema／組裝版本均非零，不能 fallback 到舊 inline script、另一 checkout 或舊 decoder。
- browser fixture 真正使用 production assembler、現行 native setter 與同份資源；保留原 guard／selector／事件順序，融合優化另以行為驗證。
- 目標平台的 source loader 與 runtime 接合需要原生適用證據；真 browser 執行仍符合 §17 的隔離要求。缺環境不是通過。

不執行 PyInstaller、不改寫 `_internal`、不新增產品 spec／安裝包／frozen 自測。既有上游打包檔沒有本次需求就不碰；不得為省 diff 保留正式程式的舊 inline fallback。

### 10.5 Fake 與 production script 的分界

contract fake 實作 typed port，不用 script 字串猜目前操作；browser fixture 執行同一份 production assembler，不能把測試用 setter/guard 代替正在驗證的 production body。

hard-isolated production-origin job 與 loopback DOM-shape job 分開命名，只有前者可證明 route/origin contract 沒被 patch。二者都不能聲稱真站網站現況已驗證。

---

## 11. 唯一設定格式與使用者重新設定

### 11.1 ADR-02：不轉換，僅重建 uTiki 行為設定

使用者明確選擇只有自己使用，可重新設定 uTiki，但其他平台設定要保留。[Q05] 新 runtime／loader／save／hot reload 只使用 canonical `utiki`，不提供獨立 upgrader，也不把轉換器藏進 UI、bootstrap 或一般 migrator。

current 契約沿用已核准的 `ticket_type_keyword`、`seat_sorting`、`seat_selection_mode` 與真正必要的新欄位；不為本版文件版本任意新增 `schema_version=2`。對合法 canonical-only 設定不強制重填。

基準三個預設值為 `全票`、`row_seat`、`manual`；實作時從既有單一 current metadata／採用紀錄核對，不另抄一套常數。空字串若為合法值必須保留，不能當成 missing。runtime policy 的無效輸入不自動回退成預設以繼續購票。

### 11.2 邊界分類與操作結果

| 輸入狀況 | 新 uTiki 啟動／行為保存／hot reload | 使用者處理方式 |
|---|---|---|
| canonical-only、必要鍵完整合法 | 直接驗證使用 | 不需重設 |
| canonical 缺必需鍵／錯型別／不合法值 | 明確 validation error，禁止新 mutation | 在現有設定 UI 或指定 JSON 區段重填，不自動套用舊值 |
| 只有舊 `ctbc` 行為 section | `SETTINGS_RECREATE_REQUIRED` 或既有等效明確錯誤；不解碼舊值 | 使用者保存備份後移除該舊行為 section，填入完整新 `utiki` |
| `utiki` 與舊 `ctbc` 行為 section 同時存在，無論 equal／complementary／conflict | 相同重新設定提示；不比較、合併或選邊 | 使用者明確決定並填寫新格式；程式不維持 mixed 轉換策略 |
| 只有新 uTiki 設定尚未建立 | 未啟用時不妨礙其他平台；啟用需完成 current 設定 | 現有 UI 可顯示待保存的 current defaults；未明確保存前不假裝配置完成 |
| 重複 JSON key／root 非 object／全檔毀損 | common config error，來源不被覆寫 | 使用者自行修復或取回備份；不以清空全檔來恢復 |
| 明確建立全新測試設定 | 可由唯一 current defaults 建立 | 只作用於指定的新檔／合成 fixture，不碰私人設定 |

「舊 `ctbc`」只指歷史 uTiki 行為 section，不包含 `accounts.ctbc_*`、Cookie 名稱、網域或共享協調 scope。只在啟用／保存 uTiki 的邊界判斷該 section 是否存在；不要為拒絕 legacy 發展另一套 decoder。

### 11.3 保留設定網頁，避免無法重填的死結

設定網頁仍是正常使用介面。uTiki 設定無效可以阻擋 uTiki worker，不能令使用者連設定頁與錯誤指引都打不開。其他平台自身設定仍可在合法整體 JSON 上讀取／保存。

不強制新增一鍵重設按鈕、修復 wizard 或額外 CLI。最小交付是合法 current 範例、三鍵說明及手動替換 uTiki 行為區段的步驟；既有 UI 繼續編輯合法 current 設定。UI 不自動把舊三鍵轉成新值，也不藉 save 偷刪未知欄位。

其他平台未使用的 legacy uTiki section 可以當 opaque data 原樣保留，既有非 uTiki migrator 保持原責任。只有切成／啟用 uTiki 時才要求唯一格式。移除舊 uTiki migrator 分支不得順便移除其他平台的遷移能力。

### 11.4 當前型別、UI 與 hot reload

current validator 建立 immutable policy；engine 不接完整 raw config/accounts/webhook。UI 和後端使用同一 current schema／metadata 或 parity tests，不能以 UI 預設值取代後端驗證。

只在成功載入且內容改變時編譯 keyword/revision。invalid hot reload 保留最後有效資料供舊結果歸屬，但撤銷新購票、普通刷新、輸入 mutation 權；必要只讀結果與 exact cleanup 可繼續。不能用重新設定當作清除在途 unknown 的理由。

| 變更 | 處理 |
|---|---|
| keyword／張數／排序／相鄰 | 未提交 plan 失效；已送出按原 context 追蹤 |
| manual/computer | 未送出重新規劃；在途導航先確認 |
| interval/cooldown | 新 intent 用新值；舊 deadline 不延長，committed 不回捲 |
| credential source replacement | 更新 CredentialRevision；AuthEpoch 只依可信邊界；未決 submit 仍阻擋 |
| 通知／顯示 | 不重新送出購票；通知狀態獨立 |
| legacy／mixed／invalid | 明確拒絕並提示重填；不比較 mirror、不轉換、不 fallback |

### 11.5 只改行為區段的資料保護

人工切換指引必須要求停止使用同一設定的 writer、保留私人備份、只替換 uTiki 行為區段；不要讓代理在本次實作／測試中直接操作使用者 `src/settings.json`、帳密、profile 或 Cookie。

合成 fixture 驗證：其他平台及 accounts 的解析後值不變，空密碼不 trim、秘密不進 log／artifact；current save 的暫存寫入／replace 失敗保留原檔，無效 JSON 不會被 defaults 覆蓋。保留原有單檔原子保存機制，不為不存在的多 profile 升級建立兩階段交易框架。

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

## 14. 零歷史相容、離線清理與原始碼使用

### 14.1 最終必須移除的歷史路徑

`platforms/ctbc.py` facade、舊 API／alias、legacy model／snapshot decoder、舊 workflow／committer／runtime、舊 `ctbc` 行為 fallback／mirror、舊 UI ID bridge／private test bridge 必須退場。caller 同步用新 API；不能搬到 settings／integrations 冒充刪除。

已為本次重構加入但現已不需要的 uTiki converter、專用產品打包程式／測試及其可達 imports，於範圍切換或 P6c 作有界清理。舊 Git 歷史、歷史報告、驗證「拒絕 legacy」的合成 fixture 可以保留；不要求抹去舊版本。

### 14.2 現行核心適配例外

保留薄適配需有現行核心版本／caller、移除舊棒球後仍存在的需求、直接注入不足的原因，以及無舊 state／無用途延遲的契約測試。例外只列精確 symbol/path，不放行整個目錄。

CTBC 網域、credential keys、Cookie 名稱、`utk-sports` scope、目前 browser/coordinator ports 與新 bootstrap 都不是 legacy。其他平台仍使用的共用工具也不得誤刪。

### 14.3 不交付升級器或產品成品

本版不實作 source converter、frozen upgrader、舊資料 merge/conflict policy、no-Python 升級 E2E、bot/settings 可分發 binary、installer、簽章／quarantine 發布驗證或公開 release assets。

使用者安裝專案需要的 Python／依賴並直接執行原始碼。已有 canonical-only 設定可使用；舊 uTiki 行為以 §11 的手動重新設定取代。不要求保留舊值或自動從舊格式取回選擇。

### 14.4 舊設定的手動切換範圍

切換指引只說明停止舊實例、私人備份、識別舊 uTiki 行為區段、建立合法 current 區段及重新開啟既有設定 UI。遇到不明欄位不由代理猜測刪除；由使用者確認，不建自動修復器。

其他平台、accounts、通知端點、Chrome profile、coordinator DB/cooldown/lease 不在重設範圍。測試使用合成值驗證該界線，不讀取真正私人設定來產生 review artifact。

### 14.5 原始碼雙平台交付

| 目標 | 需要交付／驗證 | 不需要 |
|---|---|---|
| Windows x64 | 可核對的 source SHA、Python／依賴指引、bot 與設定 UI 原始碼入口、Windows 路徑／編碼／子程序測試 | exe／installer／frozen／無 Python 執行 |
| macOS arm64 | 同一 current 契約與功能；原生 source 啟動、路徑／資源／driver 相容證據 | app／封裝／簽章／quarantine 產品流程 |
| Linux x64 | 保留 P1 CI、測試 Docker image、hard-egress 與參考 benchmark | 不自動新增個人日常使用平台承諾 |

相依套件可按既有安裝流程準備；不為不打包而重新發明環境管理器。測試 image 的 build 是保留的 CI 能力，不是被取消的產品打包。

### 14.6 原始碼端到端驗收

在各目標原生環境，以合成設定與專用 profile，完成：

```text
可識別 source checkout＋已記錄 Python／依賴
 → current 合法設定（其他平台與 accounts 使用 sentinel）
 → 既有 settings 原始碼啟動及 loopback 讀取／保存
 → bot 原始碼啟動並載入同一 current 契約
 → 硬隔離中的真 browser＋production JS／fixture 流程
 → 停止、資源清理與不含秘密的結果
```

不只測 import 或 `--help`。source tree 存在是正常條件；任意 cwd 指「cwd 不必是 repository root」，不是要求無原始碼也能執行。故意破壞 JS／配置／資源對應要非零，不能接回舊 inline 或模組。

### 14.7 P6c 清理前置與回復

本版採用後，刪除 gate 是 P6a 的適用離線驗收、P6b 對同一候選的必要獨立覆核、替代責任／caller 清單及可回復 source 版本；**不再以真站成功或另一份逐檔 final-delete 許可作為前提。** 本政策只適用已核准工作分支的無用 legacy。

清理中若出現尚未接替的能力或行為改變，先處理對應功能／離線回歸及差異覆核，不藉「可刪舊碼」任意刪功能。清理後重新驗 final source；不把清理前 SHA 的結果冒充新 SHA 已執行。

回復使用舊 source commit＋原私人設定備份的正確配對；不保留新程式內的舊 engine fallback，不 reset／clean 覆寫混合的使用者工作，不刪 coordinator 狀態。不論是否真站驗收，本地回復都不會撤銷已發生的網站請求。

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

### 16.4 設定、原始碼執行與架構

| ID | 情境 | 必須成立 |
|---|---|---|
| C01 | canonical-only／legacy-only／mixed | 合法 canonical-only 直接使用；legacy／mixed 只提示重新設定，不解碼、比較、搬值或 fallback |
| C02 | current 明確空值、選擇值與舊值干擾 | 合法空值不當 missing；只使用使用者填入的 current 值，沒有 conflict-policy 轉換 |
| C03 | corrupted JSON／不合目前 schema | 不覆寫、不猜 default；只有 uTiki 不合規時不無故阻擋其他平台 |
| C04 | current 設定保存／replace 失敗 | 原檔保留、回報失敗；不以預設值或半寫入檔啟動，不新增多檔升級交易 |
| C05 | credentials 與其他平台欄位 | 解析後內容相等，log 不洩漏 |
| C06 | hot reload invalid／in-flight policy change | gate／state 規則符合第 11 節 |
| A01 | 無 zendriver 的 subprocess 匯入 domain | 成功且無 I/O side effect |
| A02 | application 加入 evaluate／逆向 import | architecture test 失敗 |
| A03 | 舊棒球 alias／fallback 搬回 module 或 integrations/settings | final legacy scan 失敗；必要 current-core 適配有 ADR/證據，直接新 bootstrap 非 legacy |
| A04 | production JS 或 source script resource 故意損壞 | source-resource／真 browser 契約必須失敗，不 fallback 到舊 inline |
| A05 | browser CI 沒選到案例／全部 skip，或測試與 execution manifest 同時刪例 | 依獨立 catalog 核對完整 required 集合，必須失敗；見 §16.11／§17.5 |
| A06 | 修改的 Python 檔不在固定 uTiki 目錄或未被 pytest import | §21.3 從 Git 獨立核對全部實際變更並逐檔 py_compile；任何漏列／缺檔／編譯失敗均非零 |
| P02 | Windows 非 UTF-8 輸出環境 | 不因新增 Unicode／stream 改寫而崩潰 |
| P04 | 原始碼入口／source loader 從 root 與外部 cwd 執行 | Windows x64／macOS arm64 均可取得同版本 JS，含中文／空白路徑；缺檔／錯配非零 |
| P05 | source bot 與設定網頁的 current 契約／啟動 | 同一 canonical schema，loopback 保存與隔離 fixture 啟動成立；其他平台及 accounts 不受損 |


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
| U10 | 舊棒球設定出現在 load/hot reload | 不轉換／fallback／雙寫；提示使用者重新設定 uTiki，不丟其他平台資料 |
| U11 | 把舊 facade／decoder 移到 integrations，或只以舊 caller 申請例外 | gate 必須失敗；位置不等於核心契約證據 |
| U12 | 移除舊 API／型別／legacy helper 後，新模組獨立 source 測試 | 新 contract／source 啟動正常；舊 engine／decoder 不可達，不依賴 converter |

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

### 16.9 v1.5 案例的正式撤回與替代

本節是使用者改變用途後的**規格變更**，不是執行期 waiver。下列 ID 從 v1.6 有效 required 集合移除，保留歷史且永不重用；不要用 `skip`、`xfail` 或永久 `NOT_DUE` 假裝已處理。

| v1.5 ID | 原責任 | v1.6 處置及仍須保留的保障 |
|---|---|---|
| G01 | mixed-equal frozen 轉換 | 撤回轉換；C01/U10 仍驗 runtime 拒絕 mixed、指引重填 |
| G02 | converter no-op／重跑 | 撤回 converter；C01 保留合法 canonical-only 直接接受 |
| G03 | legacy／complementary 補值 | 撤回舊值遷移／補值表；由使用者重新設定，其他平台資料不動 |
| G04 | conflict-policy／empty 轉換 | 撤回轉換策略；C02 保留 current 明確空值語意 |
| G05 | 升級器 JSON／型別拒絕 | 撤回升級器案例；C03 仍驗設定 parser／current validator 的錯誤及來源保護 |
| G06 | 多檔升級／備份交易 | 撤回多 profile 升級流程；C04 保留正常設定 save／replace 失敗保護 |
| G07 | Windows 無 Python 升級 | 全部撤回；P05 用 Windows source 啟動驗收，不宣稱等同 frozen |
| G08 | macOS 無 Python 升級 | 全部撤回；P05 用 macOS source 啟動驗收，不宣稱等同 frozen |
| G09 | 產品 checksum／`_internal` 配對 | 撤回產品驗收；A04/H09/P04 保留 source JS／資源一致性 |
| G10 | 升級中的其他平台／秘密／binary 回退 | 撤回升級流程；C05/§22 保留原設定、秘密與 source 回復責任 |
| P01 | frozen 任意 cwd／無 source tree | 撤回；新 P04 檢查有 checkout 的原始碼 cwd／資源，不重用 P01 |
| P03 | bot/settings 成品與升級配對 | 撤回；新 P05 檢查 source 入口／設定契約，不重用 P03 |
| H10 | 未真站驗證不得 final-delete | 撤回；新 H12 檢查離線前置、獨立覆核與回復；不重用 H10 |

C01/C02/C04、A04、U10/U12、H09、V13 的有效 ID 保留，但只保留與 current 設定、source、資料保護或低延遲有關的責任；撤回的 converter／frozen／release／live-blocking variants 在實體 catalog 逐項列 old→new 處置，不沿用舊結果充數。

特別核對 H08 的 `new-upgrade-tool-file`：後續 active gates 不再要求存在或檢查已取消的 converter；該升級器專屬 obligation 撤回。H08 的其他新檔／核心 bridge／廣泛 ignore 拒絕保留，H11 的任何實際修改 Python 工具／測試／generated source 都不能漏。v1.5 P1 的原 63 項歷史結果不改寫。

其餘 case 的純 unit／contract／browser／integration／performance 責任保留；同 case 下只屬 frozen、產品 build、release 或真站作為出站前提的子義務撤回。資料／scope／隱私／即時 audit／正確性不能因共處一個舊測試檔而一起刪掉。

### 16.10 隔離／CI／同期 source 驗收

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
| H09 | P3a 首次 JS 外部化、source 資源缺失或錯配 | 同期 source loader／任意 cwd／真 browser 驗收；錯誤非零、不 fallback | §10.4/19.5 |
| H11 | modified-Python manifest／Git 集合／位元組與語法結果不一致 | staged／working／untracked／改名／刪除精確分類；未列新檔、非預期缺檔、過期 digest、錯 base/SHA、未編譯或語法錯均非零 | §21.3 |
| H12 | P6c 清理缺少同候選離線驗收／替代責任／獨立覆核／可回復版本 | 缺任一前置不得清理或出站；真站未驗證本身不阻擋；不得用舊版 live PASS 冒充本版離線 gate | §14.7/19.10 |

所有情境均為驗收要求；已有且仍適用的實作／測試應沿用，缺少的部分才建立，不表示本輪文件工作執行過它們。可用一個參數化測試對應多 case，但必須能逐一辨識未執行／skip／失敗；不得把一個成功 case 複製標記冒充矩陣全綠。

---

### 16.11 獨立 required-case catalog：固定集合、格式與治理

#### 16.11.1 固定集合與權威方向

`ci/utiki-required-cases.json` 是**規格檔**，不是測試輸出。v1.6 active catalog 的 `cases[].id` 必須恰等於下表 143 個 ID；不得由 pytest 收集結果、JUnit、execution manifest 或現有測試數反推生成。實作者依本計劃逐例建置 catalog，再由規格審查核准；可用工具核對文件與 catalog 一致，但不能用測試存在與否決定需求。

| 類別 | 固定 ID 清單 | 數量 |
|---|---|---:|
| D | D01, D02, D03, D04, D05, D06, D07, D08, D09, D10 | 10 |
| N | N01, N02, N03, N04, N05, N06, N07, N08 | 8 |
| S | S01, S02, S03, S04, S05, S06, S07, S08, S09, S10, S11, S12, S13 | 13 |
| R | R01, R02, R03, R04, R05, R06, R07, R08, R09, R10, R11, R12 | 12 |
| L | L01, L02, L03, L04, L05, L06 | 6 |
| C | C01, C02, C03, C04, C05, C06 | 6 |
| A | A01, A02, A03, A04, A05, A06 | 6 |
| P | P02, P04, P05 | 3 |
| U | U01, U02, U03, U04, U05, U06, U07, U08, U09, U10, U11, U12 | 12 |
| V | V01, V02, V03, V04, V05, V06, V07, V08, V09, V10, V11, V12, V13, V14, V15, V16 | 16 |
| X | X01, X02, X03, X04, X05, X06, X07, X08, X09, X10, X11, X12, X13, X14, X15, X16 | 16 |
| T | T01, T02, T03, T04, T05, T06, T07, T08, T09, T10, T11, T12 | 12 |
| I | I01, I02, I03, I04, I05, I06, I07, I08, I09, I10, I11, I12 | 12 |
| H | H01, H02, H03, H04, H05, H06, H07, H08, H09, H11, H12 | 11 |

ID 不重用；測試搬家只更新 execution manifest 中的 node ID。必要的多情境、OS／tenant／層級、H06／H11 負向子案例，必須在 catalog 的 `obligations` 中分列穩定 `obligation_id` 與必要 variants，不能只保留一個概括的 case ID 就算全部通過。例：S08 的 general／server-assigned fixture、P04/P05 的 Windows／macOS target，不可用同一次成功互相代替。

153 − 13 個撤回 ID ＋ 3 個新 ID = 143。撤回清單只放在規格變更紀錄，不塞入 active `cases[]` 做空 obligations 或假 PASS；檢查器仍對目前核准的 143 個 ID 做完整集合核對。實際 obligation 總數依現有完整 catalog 逐項投影後核對，本文件不猜測新數字，也不將舊 341 沿用為新總數。

#### 16.11.2 最小格式與責任

| 檔案／欄位 | 必要內容與限制 |
|---|---|
| catalog：`schema_version`、`catalog_id`、`revision` | 優先沿用既有 schema，catalog revision／plan version 改為 1.6 對應版本；未知版本／重複 key 拒絕；本包沒有已核准的 v1.6 實體 catalog |
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

以下只示範 H06 的一項 obligation；省略其他 142 個 case 及 H06 其餘義務，因此不能用此範例通過正式完整性 gate。

```json
{
  "schema_version": 1,
  "catalog_id": "utiki-required-cases",
  "revision": "1.6.0",
  "plan_version": "1.6",
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

`NOT_APPLICABLE` 只有在 catalog 明訂條件確實不成立、提供匹配條件／target／ADR 證據與核准來源時才成立；沒有宣告條件的 obligation 預設適用。OS 不同可使 P05 的 macOS variant 在 Windows job 不適用，但 gate 彙總仍需 macOS job 履行該 variant。停用 server assignment 不得免除其既有 fixture／禁止誤啟用案例。另行手動真站驗收不屬 active 自動出站矩陣，也不能替代任何離線義務。

階段尚未到期用 `NOT_DUE`，與 `NOT_APPLICABLE` 分開；不得拿 P1 的分階段結果宣稱 P6a/P8 已完成。缺實作、browser、Python、隔離權限、目標環境或授權，是 `NOT_RUN / BLOCKED`，不是不適用。任何必要條件缺失／未知值／條件解析失敗一律不放行。

---

#### 16.11.5 版本切換的核准與完整性

新 case／obligation 投影只接受 §16.9 與 §19 的明確變更。review 必須核對：撤回恰為批准的範圍；保留的設定資料／source 資源責任有去處；新 P04/P05/H12 全部映射；其餘核心案例未遺失。不要依現有測試通過與否產生新 required 集合。

利用既有 H06/A05/architecture 回歸驗證：新合法 catalog／映射通過；用舊 digest 驗新版被拒絕；少任一仍 active 的 case／target 仍失敗；撤回的 G/P01/P03/H10 不被錯算為漏跑；純文件舊 ID 提及不會成為必跑案例。這是必要的版本接合，不是新增另一套 verifier。

新 catalog、execution manifest、plan hash、固定 ID 列舉與 gate profile 同步，避免新 plan 配舊 expected count 而永久失敗。修改的 checker 只支援所採用的明確版本；舊成果從 Git 歷史重現，不要求新 checker 永久支援舊產品交付 schema。

---

## 17. CI、型別、網路硬隔離與手動真站界線

### 17.1 不讓命令自己關掉strict

基準pytest.ini把strict flags與預設marker同放addopts。[R10] 清空 `-o addopts=` 時必須在命令列補回 `--strict-config --strict-markers`；或只以命令列 `-m` 覆寫marker而保留addopts。正式CI使用§21的統一命令，不在不同OS各寫一套逐漸分歧的覆寫。

pytest的arg組合/override可以改變預設選擇；strict flags與marker選擇是不同責任。[E04/E13] 「有test跑」不表示啟用了strict或所有必要cases已覆蓋。

### 17.2 Typed scope涵蓋整個新架構

`ci/utiki-typed-scope.txt` 列明domain、application、ports、adapters、bootstrap、current config/keyword和實際存在的外部core-service bridge。已取消的 upgrade tool 不列為必需新檔；其他真正新增／重寫的 production 模組不可漏列。新目錄新增檔案預設進scope，不能因沒寫在命令列就漏掉。

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
| browser-native-platform | Windows x64／macOS arm64 原始碼下的 DOM/input/driver 差異；仍需硬隔離 | P3 及其後受影響變更、P6a/P8 |
| source-startup | 現有 bot/settings 原始碼入口、current 配置、source resources／cwd／錯配拒絕；可由既有 job 承載 | P3a 資源首次變更；P6a/P8 完整啟動 |
| performance-contract | 相同設定／環境的 source 基準與候選、CDP／lag／資源 | P1 基礎、熱路徑變更及 P7/P8 |
| core-integration | 控制revocation/核心reload旁路/契約演練 | P4–P6與shared變更 |
| offline-cutover-review | 替代責任、適用離線證據、獨立覆核與可回復 source 候選；沿用既有 review 記錄 | P6b 清理前；P6c 核對差異 |

job 名稱表是責任分工，不要求每列新建一個 runner；能由既有工作精確承載就重用。產品 package／upgrade／release 與真站成功不再是 v1.6 required job。

job 必須按 §16.11 的獨立 catalog 驗證完整 required 義務，不是只看 nonempty。每次驗證同時讀 required catalog、execution manifest、JUnit 與 run metadata；四者皆是必要輸入，不能用 execution manifest 自我宣告集合。0 collected、required 義務未收集／skip／xfail／xpass、缺 browser/隔離/啟用旗標、缺少或錯配 run-id/SHA/digest，一律非零。

JUnit 寫入唯一 run/attempt/job 目錄；run metadata、manifest 與 XML 都綁定同一次執行。required catalog 的核准 digest 另由受保護的 review/adoption 紀錄提供，不能四份檔案互相填同一個錯值就放行。詳細契約如下。

嚴格marker、故意unknown marker/config的負向probe、故意損壞resource/typed receipt的負向probe，都須看到非零exit。結果收集即使使用always()也不能覆蓋真正test exit code；禁止required job continue-on-error。

#### 17.5.1 四份輸入與信任根

`verify_utiki_ci_results.py` 必須明確接收 `--required-catalog`、`--execution-manifest`、`--junit`、`--run-metadata`，另接 `--approved-catalog-sha256` 及其 review 紀錄參照。expected digest 取自 §16.11 的核准來源；CLI 參數本身不會創造核准。文件修訂包不包含可執行的 repository checker 或已核准 catalog。

run metadata 最少包含：`run_id`（含 attempt）、`job_id`、`phase`、`candidate_sha`、`working_tree_digest`／clean 狀態、`os`、`architecture`、`layer`、`tenant/mode`（適用時）、`catalog_sha256`、`execution_manifest_sha256`、`junit_sha256`、`fixture_digest`、`script_digest`、相關 config-contract/source-resource／target 對應 digest（既有測試 image 另記 image digest）、isolation evidence、effective command/flags、測試與收集器 exit code、起迄時間。候選／job／target 的期望值須與 CI 事件及核准 gate 矩陣核對，不由測試自己決定。

只在適用的層級要求對應資源 digest；例如純語法 job 沒有 browser resource，須依 catalog 的 evidence 類型明示不適用；產品 binary 欄位不再是 v1.6 有效證據要求。只同步撤回欄位的契約，不可任意省略仍適用的欄位。摘要使用原始檔 SHA-256；多資源 digest 必須以已排序的相對路徑＋各檔位元組 digest 建立固定清單，包含缺檔／多檔偵測，不只 hash 檔名。

JUnit 的 suite/run properties，以及受 catalog 管理的 testcase properties，必須能核對 exact `case_id`、`obligation_id`、`variant_id`、`node_id`、run-id、job-id、candidate SHA、catalog/execution manifest 與 fixture/script digest。缺少識別 properties 的舊 XML 非法；可以由正式 pytest reporter／外部 harness 寫入，不得事後從 manifest 臆造一組成功 testcase。JUnit 不內嵌自身 digest；run metadata 封存實際 XML digest，避免循環。

#### 17.5.2 集合完整性與逐筆證據

1. strict 解析四輸入，拒絕重複 JSON key、未知 schema、無效 ID／digest、空 required 義務；核對受保護的核准 digest 與計劃版本。
2. 驗 `catalog.case_ids == execution_manifest.case_ids`，再驗每 case 的 obligation／variant 完整映射；缺少、重複或未知 case/obligation/variant 均失敗。一般非 catalog 的輔助測試可另跑，但不得宣告未知 required ID，也不能充數。
3. 由 catalog 的 gate/job/target/layer/條件計算本 job 的 required execution keys；execution manifest 只能提供映射，不得縮減集合。expected execution key 為 `(case_id, obligation_id, variant_id, job_id, target)`。
4. 每個適用 key 必須有 catalog 要求的當次證據：pytest 類型恰有一份對應 testcase，setup/call/teardown 全部成功，無 fail/error/skip/xfail/xpass；非 pytest 類型依既有 schema 核對原始 report／退出碼，不捏造 testcase。適用的其他 evidence 齊全且 digest 相符。重複 testcase、重複 XML／結果、錯參數化 ID、錯層級或借用其他 target 的成功均失敗。
5. 不適用／尚未到期逐項回報條件與依據，不填 PASS；適用卻漏收集就是失敗。人工宣告不適用不能覆寫 catalog。
6. 比對本輪 run/attempt/job、候選 SHA、實際檔案 digest、fixture/scripts/source/config 版本與隔離證據；前次 run 的成功結果無效，單改 metadata 的檔名或 timestamp 也不能重用。
7. 產出 checked/required/missing/failed/inapplicable/not-due 與逐項原因；required 失敗時非零。任何原 test/harness/collector 非零也須保留，不准由後續 checker 成功洗掉。

一個參數化測試可覆蓋不同 variant，但各 variant 必須有獨立執行 key 與結果；catalog 明確要求多項 assertions 的單次義務，測試本體仍需真的驗證全部，JUnit 只能證明執行結果，不能證明 assertion 品質。此剩餘限制由獨立審查及既有 mutation tests 補足。

#### 17.5.3 skip／xfail／xpass 的處理

本版所有適用 required obligation 都採 `PASS_ONLY`。xfail 不論預期與否均不能履行 required 義務；XPASS 即使被測試工具視為成功亦不可通過（含 non-strict XPASS）。required tests 禁用 xfail 標記，reporter 另記 `wasxfail/outcome` 以偵測執行期改標；不能只以 XML 沒有 `<failure>` 推斷 PASS。

如果某種 gate 沒有適用案例，不以「0 collected」當成功：checker 需輸出按 catalog 推導的 `NOT_APPLICABLE`，gate 彙總再檢查是否由其他必要 target/job 履行。預期非空的 job 得到 0 collected 必須失敗。原先允許的非 required 輔助測試 xfail 不會填補 required 缺額，也不改變其政策。

#### 17.5.4 全階段與跨 job 彙總

單一 job 只能宣稱其 catalog 子集合結果。階段 aggregator 仍使用同一核准 catalog 與完整 execution manifest，接收各 job 的 JUnit／metadata 成對紀錄（`--run-index` 指向 exact 路徑與 digest），重新執行相同核對，並依 `gate_profiles` 檢查全部必要 job/target/layer。

不同 job 可有不同 job-id，但必須屬同一 run/attempt、候選 SHA 與 catalog／execution manifest 版本；不能拼接別次成功。重跑屬新 attempt，不能只替換失敗 case 再拼舊 XML。跨工作流程執行的 native source 證據按既有 schema 記明 exact 候選及核准關聯，不偽裝同一 run；不為方便任意跨 attempt 拼接。P6b 離線 review 記錄可對清理差異作覆核，但不把清理前測試冒充清理後執行。

使用者另行手動真站紀錄放在工程出站矩陣之外，列 exact 候選及實際操作，不填入 active gate 充數。缺真站不使 v1.6 離線 aggregate 失敗；文件存在也不代表真站成功。

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
| digest-mismatch | 改 catalog/mapping/XML/fixture/script/source 或適用 image evidence 一者，或借其他 target digest | 原始位元組與適用 evidence 不匹配 |
| duplicate-result-or-wrong-layer | 複製同一成功 testcase，或用 loopback 代 origin／Linux 代 Windows 或 macOS 原生 source 結果 | execution key 不唯一或層級不足 |
| missing-job-or-target | 彙總缺一個必要 job/OS/arch，或多放未知結果 | gate_profiles 完整集合不符 |
| failure-exit-overwritten | test/harness 非零，但後續收集器回 0 | 最終 gate 仍非零，不能被 always() 洗綠 |

### 17.6 Coverage與失敗模型

原有domain/schema branch 95%、application branch 85%保留為工程目標，不是普遍門檻。arbiter collision、state出站、input stale、exact cleanup、手動retire、current 設定與 source 啟動矩陣為**必測功能門檻**，不能以coverage百分比抵消。

不以Python行覆蓋率宣稱JS branches已跑。每種production operation都有happy/stale/rejected/possible/partial-mutation案例，必要時文件化不適用。對held barrier/document gate/exact token進行定向mutation，相關測試須確實變紅。

### 17.7 真站驗收另列，不擋原始碼重構出站

使用者已選擇：離線驗收與獨立覆核後可移除無用 legacy，真站由自己之後手動驗收。[Q05] 因而撤回 v1.5 的 H10/live-before-final-delete 與 P8 release/live blocking；不得再要求「先真站成功才算 P6c/P8 完成」。

這不等於真站操作授權。Codex 在本輪不登入、刷新、選票、送出或付款；也不使用日常 browser profile／帳密。真正的交易結果 unknown、quota、interval 與安全頁型限制仍是 runtime 契約，不能因人工驗收延後而移除。

交接附 CTBC／味全實際支援且受改動的頁型、設定模式、操作停止點、刷新間隔、原始碼 SHA，以及已做離線／未做真站項目。使用者手動測試結果另記 `LIVE_VERIFIED_BY_USER` 或 `LIVE_NOT_VERIFIED`；未支援 server assignment 仍不啟用。

新的程式／腳本改動使既有人工真站結果可能不再適用時，標記對應範圍需要使用者重驗，不冒充新 SHA 已真站通過，但不因此阻止本版離線工程結案。若離線或使用者測試已證明一個真實 bug，仍須修正，不能以真站非必測為理由忽略。

## 18. 極低延遲設計、原始碼環境與效能驗收

### 18.1 唯一 source 交付與測試環境的區分

交付同一 source checkout、Python／依賴指引、設定網頁與合法 current 設定範例、必要 JS 資源、測試及脫敏結果。Windows x64／macOS arm64 都使用該來源，不產生 bot/settings/upgrader 可分發成品。

source loader 與 JS assembly 以模組位置為準，資源版本／digest 只支援當前契約；source／browser 實際使用同一份 script 與 native-setter fragment。冷路徑檢查資源，熱路徑不反覆 I/O／hash。

P3a 首次外部化就測 source 資源，P6a 接入口後測兩平台實際 source startup，P8 對最終 SHA 完成適用回歸。不保留 product binary、`_internal`、no-Python 或 release checks 作隱性完成條件。

保留測試 Docker image、固定 image 身分、依賴安裝／必要第三方 wheel 準備及 Actions artifacts；這些不是產品打包。不新增上游產品發布、不把測試成果 ZIP 說成可安裝搶票產品。

### 18.2 依賴政策

維持專案 Python 3.11 與 P1 已驗證的依賴基準，不為文件改版同時大幅升級 runtime dependencies。mypy／Hypothesis 等開發工具沿用既有配置；只有真實接入需求才調整並記錄精確版本及相依解析結果，不重新從 P0 選一次套件。

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
| JS source、assembled template、resource digest | process lifetime／source version | 不得快取含帳密的完整 invocation 或把 template cache 當頁面 cache |
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

沿用 P1 已有 benchmark harness，按實際重構接入必要量測，不重建平台；至少記錄：

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
| 執行環境 | Windows x64＋Chrome／macOS arm64 各自記 source 結果；Linux 是 CI 參考，不混用結果；不做 frozen |
| 乾擾 | 單 tab、少量多 tab、慢 log sink、慢 notification、設定熱更新、coordinator contention |

本地純運算採固定輸入做足夠多次迭代；browser 情境先 warm-up，再分多批量測。例如每組 20 次 warm-up、300 次有效樣本、至少 3 批，實際樣本數可依穩定性調整並在報告列出。cold-start 另測，不能用 warm-up 隱藏每一個新 document 都會再付出的初始化成本。P1 歷史少量 cold 樣本只能作定位基準，不把例如三筆樣本的 p95/p99 宣稱為穩健尾端估計；後續實際比較需足夠樣本及不確定度。

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
| V13 | source cold start／fresh document | 初始化成本單列；Windows／macOS 原始碼測試不以 warm 或 Linux 結果代替；取消 frozen variants |
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

input/image evidence 的 live 部分放在最後 browser operation；需要額外輪詢只能因原生狀態確實未就緒。仲裁不新增瀏覽器／SQLite 往返，manifest／型別／schema 全面驗證都不進選票熱路徑；本版不存在 uTiki 升級工作。

速度結果只能從相同可觀察行為與限制的成對量測得出；不得用取消 unknown blocker、刪限流或減少必要回讀換取分數。

---

## 19. 實作階段與有界交付門檻

### 19.1 保留編號，調整不再需要的交付

P0–P8 是工程階段，不是 finding 嚴重度。P6b 沿用識別但改為離線候選覆核；不得沿用舊的 live 語意或 evidence key。

```text
既有 P0／P1 成果（保留歷史，不重做）
 → 一次 v1.6 範圍接入：計劃／catalog／mapping／必要 CI 與治理同步
 → P2 純模型／current policy／ports／型別
 → P3a JS 外部化＋同期 source resource／cwd／browser 驗證
 → P3b 同原語批次操作與 readiness
 → P4 session arbiter＋navigation／purchase／input lifecycle
 → P5 refresh 與核心副作用入口整合
 → P6a 新 owner 接入＋current 設定 UI＋雙平台 source 離線驗收
 → P6b 固定候選離線獨立覆核＋替代清單＋可回復版本
 → P6c 最後 legacy 清理＋清理後 source 回歸
 → P7 依證據做端到端低延遲／碰撞／上游演練
 → P8 最終 source SHA 驗收與個人使用交接

使用者之後另行手動真站驗收（不阻擋上述工程完成，不是代理操作授權）
```

actual HEAD 可能已在 P2 或之後；接入本版後從適用位置續做，不要求回到 P0。熱路徑每次實際改動都需量測或有可核對的影響分析；P7 不是第一次看速度。任何階段都只有單一正式 owner。

### 19.2 P0：沿用採用基礎，僅補 v1.6 差異

核對 actual HEAD、未提交工作、Issue／branch、舊採用紀錄與已完成 review。保留 core-effect／wait／upstream／behavior inventory，移除本次不再適用的產品 target 與轉換策略要求。

交付一次 v1.6 adoption／scope-change 紀錄與新 catalog 投影；有效 case 固定 143，實體 obligations 逐項處置且 independent review/digest 成立。不得因 scope 變更再建新的信任服務。需要取得新核准值時提出 exact digest 與差異，不自行把舊值當新值。

### 19.3 P1：已完成的工具基礎，僅必要版本接合

P1 歷史結果對應 §2.1 的 exact SHA／v1.5 catalog，不重新製作。現有 pytest／type／syntax／hard-egress／benchmark／artifact／aggregate 繼續使用。

v1.6 的新 active catalog／phase 接合須測合法與必要負向回歸，尤其已撤回項不再被當成漏跑、保留項聯合刪除仍失敗、錯 plan/catalog digest 拒絕。必要時在實際修改候選重跑受影響 P1 工具與正常 CI；這是維護與接入，不是重新做 P1 設計。

不得因「個人使用」跳過 strict flags、完整 collection、原始退出碼、硬隔離或結果歸屬；也不得因先前 P1 PASS 宣稱正式購票 runtime 已全面 typed 或真站正確。

### 19.4 P2：純資料、current 設定與完整型別

建立 §4/5 具名 records、current-config validator、ports、claim／receipt、Input/CAPTCHA 型別與 states。所有新／重寫 production 程式進 typed scope；無需求不造額外 core-service framework。

先驗 canonical-only、missing/invalid、legacy/mixed 的拒絕與提示、current 空值及其他平台 opaque 保留；沒有 converter、conflict-policy 或舊值補入。正式私人設定不被測試覆寫。

驗收：D01–D09／C01–C03／A01、適用 H07/H08、完整 typed 與 modified-Python；非法 state 組合不可建構，image generation 不由隨機 UUID 假造；不複製上游規則。

### 19.5 P3a：JS 外部化與同期 source 資源驗收

先搬移 source，不改 selector、guard 順序或原生事件語意。建立／沿用唯一 assembler、module-relative source loader、current native setter 注入及 codecs。

同一變更單位驗 Windows／macOS source loader、root／外部 cwd、中文空白路徑、資源缺失與錯配拒絕；真 browser 執行 production scripts 且 hard-egress 成立。P04/H09/A04 的適用義務在這一階段完成，不延後到 P8。

不 build 產品、不新增 spec／frozen/no-Python；既有外部化成果若已完成則直接沿用。開發期舊 workflow 可呼叫新 ports，但不能建立第二套模型／state／fallback；P6c 清除未接替完的歷史入口。

### 19.6 P3b：可證明的批次操作

同次 observation 合併 identity/dialog/readiness／候選與目的 attestation；live audit 留在同次 mutation。共用一套原語，不為一次 CDP 強跨真正的 async 原生狀態。

驗 N/S、I01–I08、V01/V03/V04/V09/V10，記實際 commands 與 source 延遲。setter 失敗禁止 submit；資源改動回歸 P3a source gate，不添加產品打包工作。

### 19.7 P4：Arbiter 與 Purchase/Input 全出口

先完成 session writer／ActiveEffect／eligibility／priority／collision matrix，再接三組 reducer proposals。輸入子機沒有獨立 owner。§8 的 recovery、blocked、accepted、held、retire/new-cycle 及預設保持都要有實作；本地人工恢復控制不得留空白 TODO。

驗 X01–X16/T01–T12/I01–I12，涵蓋 late fact／新 policy、ready 與普通 refresh 競爭、stop 與 receipt 同批、local／possible effect、manual 不自動送出。新引擎先在隔離 harness，原正式 route 只有現行 owner；只允許 shadow decision，不允許 shadow effect。

### 19.8 P5：Refresh、核心服務與完整生命週期

保留 SQLite scope/interval/cooldown/lease，使用精確 ReloadHandle；startup/scheduled/health/return/challenge/reset 的旁路按 inventory 接入 arbiter／revoke／handoff。必要核心適配在模組外，不直改 state。

permit commit unknown 與 browser attempted 分開；確需 receipt API 才作最小已測擴充。驗 R01–R12/L01–L06/X/U04–U09、多程序 SQLite、late cleanup、崩潰／失去 lease；不藉速度放寬已 commit barrier。

### 19.9 P6a：原始碼接入、設定可重填與完整離線候選

主入口切新 bootstrap／ports，候選只用新 owner，正常 import 不到舊 engine。未刪舊檔僅供清理前比較，不能成為 fallback。

UI/default/load/save/hot reload 只使用 current `utiki`；設定頁能開啟，錯誤可解釋，手動重填步驟清楚；非 uTiki／accounts 合成資料保持。沒有升級器或產品打包。

對 Windows x64／macOS arm64 的 bot／settings source 入口、資源、依賴及適用隔離 browser 完成驗收。依 143 個 active case 的階段 obligations 與累積 modified-Python 核對；必需 target 缺環境為 BLOCKED，不拿 Linux 代填。

成立後進 P6b，不因 `LIVE_NOT_VERIFIED` 停在 P6a。

### 19.10 P6b：離線候選獨立覆核與清理前置

對固定 P6a source 候選及其直接完整路徑做必要獨立覆核，沿用內容／依賴未變的既有 review。盤點替代責任、所有 caller、舊符號引用、其他平台影響及可回復 source／設定備份指引。

H12 驗證缺必要離線證據、缺覆核、錯候選、尚有未接替 caller 或沒有回復點會阻擋。未真站驗證不構成缺項，不能把舊版 H10 的 live 成功當作本版 gate。沿用現有 review 紀錄與核對方式；自動檢查只驗證紀錄、候選與範圍對應，不宣稱可以機器證明審查品質，不為本 gate 新建 approval service。

採用本版且既有工作分支清理授權成立，以上前置全通過即可 P6b 出站並自動進 P6c；不再等待使用者逐檔核准。主代理不能自審冒充獨立覆核；真正無法裁決的功能差異仍需維護者決策。

### 19.11 P6c：最後清除無用歷史架構

限定刪除已接替的 facade／alias／legacy model／舊 workflow／inline fallback／private test bridge／失效 current imports。只清本次自訂模組與直接接點；不清其他平台、個人設定或上游歷史打包目錄。

清理後驗 final import graph、所有 caller、新 typed scope、累積 modified-Python 的 delete/rename／現存檔編譯、source startup 與適用 browser／回歸。若清理牽涉新行為，回相應切片處理、差異覆核後再驗，不冒充純刪除。

保留清理前→後 SHA 對應；舊證據是比較基準，不是清理後已測證明。最終 runtime 不留下等待未來刪除的相容層；Git 歷史與拒絕 legacy 的測試不必刪。

### 19.12 P7：低延遲與上游語意演練

依量測排序改善，保留 §6 仲裁與 §8 live audit。加入 slow sink、readiness storm、case collisions、多 tab／retained task 資源情境；無收益實驗可回復移除，不建立泛用優化框架。

上游演練涵蓋新 gate、helper／receipt 形狀、native setter、source resource／核心 reload 旁路；只在隔離 worktree／fixture，不注入使用者日常流程。

驗 V01–V16/U/X 及相關 source/browser／型別／語法。Windows／macOS 成對量測不混平台；相同語意與限制下不接受無理由退化。真站結果另列，不以未手動真站為 P7 blocker。

### 19.13 P8：最終原始碼驗收與個人使用交接

固定最終 source SHA；兩個使用目標、適用核心與 UI 行為、JS 資源、current 設定、typed／syntax／catalog／browser／效能／資源清理都有對應證據。核對本版 active obligations，不再包含產品 binary、frozen、upgrader 或 release gate。

交付 source 使用／依賴／設定重填說明、無用舊碼清理結果、可回復版本、已測／未測／限制，以及供使用者之後手動真站檢查的範圍清單。全部離線要求與必要覆核通過可宣告 `SOURCE_REFACTOR_COMPLETE / LIVE_NOT_VERIFIED`，不等待 release 授權。

不推 main、不發布、不建 tag、不自動操作真站。真站由使用者另行執行，不因此把 offline 完成誇大成「搶票必定成功」。

### 19.14 回復單位與局部阻擋

P3a source／loader／resources 同組回復；P3b 融合優化獨立；P4/P5 未接正式入口時按功能回復；P6 ownership/API/current-config 接點一起回復。舊 source 配原設定備份，不提供轉換器或雙寫。

不刪 coordinator DB/cooldown/lease，不假裝回復會撤回網站效果。缺 reviewer／native 隔離／核准值只阻擋相關依賴；可做原計劃允許的獨立工作，但不得跳 phase gate 宣稱完成。工作節奏沿用已採用自主收斂規範，不改回「每個小修都等人」。

---

## 20. 舊程式到新責任的搬移對照表

函式名稱以基準版本為定位依據。實作者先核對 actual HEAD；同功能已改名時追蹤其 caller，不死守舊行號。[R02–R07]

| 舊檔案／符號或責任 | 新 owner／位置 | 搬移要保留／改正的重點 |
|---|---|---|
| `workflow._state`／`_new_ctbc_tab_runtime` | `application.session`＋engine instance registry | 移除 module-global 業務狀態；唯一 writer；非業務 task handles 分離 |
| `_ctbc_tab_identity`／`_existing_utiki_tab_context` | SessionHandle／TabKey lifecycle | lookup 不 allocate；target recycle 有 generation；close 後不復活 |
| `_ctbc_purchase_state_key` | `domain.identity` | 明確 tenant/document/auth context；不重新從零拼整份 URL 來做每次小檢查 |
| `_get_ctbc_page_type`／`_get_utk_sports_profile` | `profiles` | 直接 typed return；刪 legacy dict projection |
| `_get_utiki_behavior` | `utiki_config` 在載入邊界建立 policy | 正常 runtime 無 legacy fallback；mixed 只提示使用者重填 uTiki，沒有轉換器／每步 config migration |
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
| `settings.migrate_config` 中舊 uTiki 遷移／鏡射 | 刪除舊 uTiki 分支；不建立替代 converter | 其他平台 migrator 保留；uTiki 只讀 current，舊值由使用者重填 |
| settings UI 的 ctbc 行為元件 | canonical `utiki_*` UI／schema metadata | default/read/save/dirty-check 一致，無雙寫 |
| `tests.ctbc_support`、private patch | 新 semantic fixtures／ports／事件 trace | 保留行為覆蓋，不保留舊 API 轉接；不以測試 caller 豁免 |
| 核心 ↔ uTiki 接入／組裝 | 新 `platforms.utiki.bootstrap`；必要current-core適配在外部 | ADR-01預設直接工廠，不建立舊函式群 facade／多重root |
| current coordinator／notification／credential providers | 有證據才建立模組外 current-core service provider | 只映射現行服務到新 ports；無舊 phase／config migration／鏡射 state |
| 真正共用 util keyword public API | 核心入口＋唯一 keyword_rules | 非歷史棒球 caller 才有穩定入口例外；新模組直用純來源 |

新增責任也需追蹤：所有頁面 mutation → §6 ActiveEffect；原 image token/reset/fill/OCR 呼叫 → §8 InputState；主程式直接 reload/接管 → §12 request/handoff；current 設定／source startup → §14 的 Windows/macOS 原始碼矩陣；新仲裁不是在原 wrapper 外再加一套 state。

沒有列出的函式不代表可以刪除。以 caller inventory、測試情境及 side-effect inventory 交叉核對；先證明不再使用或責任已被接手。

---

## 21. 驗證命令契約與結果記錄

### 21.1 命令不是執行授權

以下source/target CLI由實作者在授權後使用；本文件沒有執行它們。帶新檔名／旗標的範例是契約示意，先查已完成 P1／actual HEAD 的現行介面再使用；不為文字範例另做一套同名工具。只讀review不得照附件自動修改/執行。

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

沿用 `tools/verify_modified_python.py` 建立／驗證本階段 `modified-python.json`，再逐檔呼叫 `py_compile.compile(..., doraise=True)`。這是依既有 AGENTS.md「對修改過的 Python 檔逐一編譯」要求的可機器驗收契約。[R12] 本輪僅修訂文件，沒有對 repository Python 檔執行語法 gate。

範圍為核准 base→candidate 的**整個 repository** Python 變更集合，不先限縮到 uTiki 目錄，也不依 pytest 是否 import 決定。至少涵蓋所有實際修改的 production、CI／benchmark／工具、測試 image helper、tests，包含 `src/nodriver_tixcraft.py`、`src/settings.py`、實際建立的 `src/utiki_core_services.py` 及 checker 自身。未修改檔可因階段規格額外列入，但不能用 allowlist 排除任何已辨識的變更。

| 集合來源 | 必須處理 |
|---|---|
| `base_ref..candidate_ref` | 比較已解析的 exact Git SHA，讀 NUL 分隔 name-status；不可由 caller 任選較近 base 來縮小集合 |
| local staged／working tree | 與 HEAD／index 的變更聯集，涵蓋 staged 後又修改的檔案；編譯實際工作目錄位元組，非過期 index 副本 |
| untracked | `git ls-files --others --exclude-standard -z` 的所有新增 Python 檔；不可只看 tracked diff |
| added／modified／copied／type-changed | 現存 `.py` 全列入；若確實修改既有 Python recipe `.spec`，仍按既有 gate 額外 compile-only，不執行 PyInstaller，也不因此要求新增或維護產品 spec |
| renamed | 新路徑為 Python 則編譯新路徑；保留原路徑與 status；改名不得漏檔 |
| deleted／改名至非 Python | Git 明確確認移除者列 `DELETED/RETIRED_PATH`，驗不存在後不編譯；這不是可默默忽略的缺檔 |
| ignored／generated source | 正式或測試 Python 原始碼不得藏於 ignore 規則逃逸；必要 generated Python source 須在沿用的核准 build-source inventory 列明來源、路徑與 digest 並加入範圍，無 inventory 則阻擋 |

正常 `.venv`／第三方快取不是本計劃原始碼，不掃整個環境；但不能用寬泛目錄排除把真正修改的 tracked/untracked/build-source 檔一起跳過。symlink、路徑穿越、大小寫碰撞、unmerged entries 或無法確定的 type change，拒絕並列原因，不追到 repository 外編譯不明檔案。

base 採 §2／P0 核准的階段起點，candidate 取實際 CI checkout；兩者由受審查的 run context 核對。每階段驗自己的完整 diff，P6a/P6c/P8 另外驗實作基準到該候選的累積 diff，不能只編譯最後一個 commit。CI clean candidate 不接受未記錄 working changes；本地 dirty 檢查可跑，但報告必須附工作目錄 digest 並標 DIRTY，不冒充某個 SHA 的正式驗收。

#### 21.3.2 Manifest 與獨立交叉核對

manifest 至少包含 `schema_version`、`run_id`、`job_id`、`phase`、`base_sha`、`candidate_sha`、`working_tree_digest`、`python_version`、`files[]`。每檔列 repository-relative `path`、`old_path`（適用時）、Git status／來源集合、`role`（production/tool/build/test）、`exists`、`sha256`（現存檔）、`required_action`（compile／verify-delete）。角色由清單記錄，不影響是否應被編譯。

**驗證模式必須重新從 Git／核准 build-source inventory 推導集合，不能只照 manifest 指示編譯。** 必要集合漏一檔、多一個不存在檔、重複 path、未列的新檔、條件式 bridge 已建立卻被排除、改錯 base/candidate、content digest 錯配都非零。額外列入未改的現存檔須明標 `additional` 與理由，不可用其成功補缺漏。

未建立的條件式 `utiki_core_services.py` 不必憑空新增；只有查核未存在且無變更／build-source 需求才可不列。已列 compile 項卻找不到檔案必須失敗；只有 Git 明確的刪除紀錄可以走 verify-delete。

每檔以目標 Python 3.11 逐檔 `py_compile`，compile-only，不 import／執行其頂層、測試函式或 build recipe。bytecode 放本次 artifacts/cache，而非改動 source tree。編譯前後再核對實際位元組 digest，遇途中變動為 STALE_INPUT／非零；先記每檔成功或失敗，再產出含 manifest digest／effective interpreter／run-id/SHA／exit code 的 syntax report。

沒有 Python 變更時，只有在獨立推導集合確實為空才能回 `NO_PYTHON_CHANGES`；不得寫成「所有 Python 已編譯」。有變更卻零編譯必須失敗。syntax report 是必要獨立證據：mypy、pytest import、compileall 某幾個資料夾均不能取代它。

#### 21.3.3 命令契約

以下變數由正式 launcher 在授權後建立：`BASE_SHA`、`CANDIDATE_SHA`、`RUN_DIR`、`RUN_ID`、`PHASE`、`APPROVED_CATALOG_SHA256`。`RUN_DIR` 為唯一 run/attempt/job 目錄；核准 digest 不從測試清單取得。參數未提供、仍是 placeholder、與 metadata 不一致皆非零。

```bash
python tools/verify_modified_python.py --base-ref "$BASE_SHA" --candidate-ref "$CANDIDATE_SHA" --run-metadata "$RUN_DIR/run-context.json" --write-manifest "$RUN_DIR/modified-python.json" && \
python tools/verify_modified_python.py --base-ref "$BASE_SHA" --candidate-ref "$CANDIDATE_SHA" --run-metadata "$RUN_DIR/run-context.json" --manifest "$RUN_DIR/modified-python.json" --report "$RUN_DIR/python-syntax.json"
python tools/check_utiki_architecture.py --strict --core-inventory docs/02-development/utiki-refactor/core-integration-inventory.md
python tools/check_upstream_boundary.py --inventory docs/02-development/utiki-refactor/upstream-touchpoints.md
python tools/run_utiki_typecheck.py --manifest ci/utiki-typed-scope.txt --config ci/mypy-utiki.ini
python -m pytest --strict-config --strict-markers tests/unit/utiki tests/contract/utiki tests/integration/utiki tests/architecture tests/upstream_integration
python -m pytest --strict-config --strict-markers tests/performance/utiki
```

CI 每行視為必要 step，shell 必須採失敗即停或明確保存各 exit code；不能把以上多行貼成只看最後一行結果。`--write-manifest` 僅建立查核清單，**不是**語法 PASS；第二行重新推導範圍、逐檔編譯才是 gate。local dirty 執行須明確開 `--include-worktree`，生成與驗證兩次皆使用同一模式，報告不得供 clean candidate 的正式工程 gate 冒用。

type runner 仍必須實際呼叫 mypy strict 並列被檢檔案，維持 §17.2 的完整 typed scope。syntax manifest 可以包含未全面型別化的上游修改檔及相關測試，**範圍不得被較小的 typed manifest 截斷**。兩種 checker 不互相取代。

browser 命令由隔離 launcher 在隔離域內執行，依 §17.5 reporter 契約寫入 JUnit properties；有效 browser enable、strict flags、隔離證據均需記錄：

```bash
python -m pytest -o 'addopts=' --strict-config --strict-markers -m 'browser and not live' tests/browser/utiki --junitxml="$RUN_DIR/browser.xml"
python tools/verify_utiki_ci_results.py --required-catalog ci/utiki-required-cases.json --approved-catalog-sha256 "$APPROVED_CATALOG_SHA256" --execution-manifest tests/fixtures/utiki/manifest.json --junit "$RUN_DIR/browser.xml" --run-metadata "$RUN_DIR/run-metadata.json" --require-browser
```

`run-context.json` 是執行前固定的身分／期望來源；launcher 完成後建立 `run-metadata.json`，加入真實 test exit code、JUnit/syntax report 與資源 digest。測試失敗時可繼續收集，但最終 exit 是所有必要 step 的失敗聯集，不因 checker 回 0 清除原 test exit。PowerShell 同樣保存 `$LASTEXITCODE`；不得只查看最後一個命令。

各 job 彙總範例：

```bash
python tools/verify_utiki_ci_results.py --required-catalog ci/utiki-required-cases.json --approved-catalog-sha256 "$APPROVED_CATALOG_SHA256" --execution-manifest tests/fixtures/utiki/manifest.json --run-index "$RUN_DIR/gate-runs.json" --phase "$PHASE" --run-id "$RUN_ID" --candidate-sha "$CANDIDATE_SHA"
```

`--run-index` 列出每個 job 的 JUnit＋run metadata 路徑、digest 與 job/target，不替代四輸入核對；aggregator 對每一組仍接收四份資料並按 catalog 的 gate_profiles 驗完整集合。source/type/syntax 等非 pytest 工具的必要結果以原始 report/exit code 作 evidence；只有該 catalog job 明示無 testcase 才可使用空 JUnit，不得為應跑測試的 job 豁免零收集。手動真站紀錄獨立於工程出站。

#### 21.3.4 A06／H11 必備負向驗收

| 子案例 | 注入 | 必須結果 |
|---|---|---|
| outside-fixed-roots | 僅修改主入口、settings、CI／測試 image helper 或測試檔，並放入語法錯 | 即使 pytest 不 import，逐檔 gate 仍發現並非零 |
| new-or-untracked-omitted | 新增 `.py` 卻刪其 manifest entry；含 untracked 與新目錄 | 重新推導 Git 集合發現漏列；非零 |
| staged-and-working | staged 成功版本之後把 working tree 改壞 | local 模式編譯真實位元組；CI dirty 候選拒絕 |
| missing-file | manifest 要求 compile，但檔案意外不存在 | 不 skip，不以空集合通過 |
| explicit-deletion-rename | 明確 delete／rename／copy／副檔名改變 | 正確驗刪除與編譯新 Python 路徑；漏新路徑失敗 |
| conditional-bridge | ADR 建立 core-services bridge 卻未列入 | 已存在／變更即屬必要集合，不能因條件式名稱跳過 |
| wrong-base-or-sha | 用 HEAD 當較近 base、換 candidate 或拿舊 report | 與核准 run context 不符；非零 |
| duplicate-or-digest-race | 重複 path、hash 不符、編譯期間變更 | 非零並標明原因；不發布過期語法 PASS |
| ignored-build-source | generated／ignored Python 被交付或執行卻無 build-source inventory | 範圍無法完整證明，BLOCKED／非零 |
| self-or-zero-compile | checker 自身修改漏列，或有變更但編譯數為零 | 不予通過；確實無變更另回 NO_PYTHON_CHANGES |

這些是沿用的 repository gate 測試契約，不是本輪 v1.6 文件檢查已執行的程式測試。負向測試的合成故障只能在隔離副本／臨時 fixture，不能破壞使用者分支。

### 21.4 使用者重新設定：不新增轉換命令

不提供 `convert_utiki_config.py`／upgrader／conflict-policy 指令，也不要求建立另一支 reset CLI。個人使用指引只包含：

1. 停止使用該設定檔的 bot/settings；另行人工核對既有未決交易。
2. 在本機私人位置備份原設定；不加入 Git／artifact。
3. 只手動移除確認為歷史 uTiki 行為的 `ctbc` section，填入 current `utiki` 區段；不要刪 `accounts` 或其他平台內容。
4. 用既有設定網頁檢查／保存 current 值，開新流程前確定新格式有效。
5. 測試與代理實作只使用合成檔案，不擅自替使用者完成上述私人檔案操作。

最小 current 區段示意如下，這不是完整設定檔，也不是覆寫整份 `settings.json` 的命令：

```json
{
  "utiki": {
    "ticket_type_keyword": "全票",
    "seat_sorting": "row_seat",
    "seat_selection_mode": "manual"
  }
}
```

實際完整欄位及值以 current schema／既有 UI 為準；例子不為其他平台重建預設，也不移動 credentials。使用者已有合法 canonical-only 設定就不需重填。

### 21.5 Source 啟動與測試 image 的命令責任

實作者先核對 actual HEAD 的 bot/settings 啟動入口、虛擬環境、設定位置與現行 CLI。交付 Windows／macOS 各一份實測 source 啟動命令與必要環境說明；不要抄舊 exe 指令，也不要把未支援的自測旗標寫成已存在。

最終需要驗到 source settings 的 loopback 讀取／保存，以及 source bot 在隔離 fixture 的實際初始化／運作。可使用既有 harness／自測入口，不要求為此新增一組發布 CLI。所有例行原始碼測試與 browser 命令仍遵循 §17、§21.2–21.3。

Docker 測試 image 準備沿用 P1 已驗證的 builder 與 workflow，先核對實際介面，不重造包裝。依賴安裝、必要第三方 wheel、固定 image 準備在測試前完成；執行 browser／driver／子程序時保留 hard-egress、非日常 profile、無秘密與可核對 cleanup。

本版沒有 PyInstaller／bot/settings/upgrader 產品建置命令，也不要求無 Python／無 source tree／`_internal` 自測。若較早自訂 build helper 已不再使用，只在證明 caller 清空後作範圍內清理；上游其他用途 build 檔不順便刪。

### 21.6 Benchmark與結果表

```bash
python tools/benchmarks/bench_utiki.py --mode warm --samples 300 --seed 1701 --output artifacts/utiki-warm.json
python tools/benchmarks/bench_utiki.py --mode cold --samples 30 --seed 1701 --output artifacts/utiki-cold.json
python tools/benchmarks/compare_utiki.py --baseline artifacts/baseline.json --candidate artifacts/candidate.json
```

同環境成對量測；seed/sample只是初始規格，最終附不確定度與失敗樣本，不以平均值掩蓋tail latency。包括arbiter/ready-vs-refresh案例，不能只測沒有競爭的happy path。

每階段報告：adoption/Issue、actual/baseline/upstream/candidate SHA、source 與 script／適用測試 image digest、變更行為、原根因→案例→實作、retired符號與真核心例外、typed scope、required catalog 與核准 digest、execution manifest／run metadata／JUnit digests、適用性與 gate 彙總、modified-Python 的 base/candidate／逐檔語法 report、完整 test 命令／collected/pass/fail/skip/xfail/xpass、isolation canary、source/native startup、另列手動真站狀態、效能與資源、未測與下一個 blocked gate、source 回復。

沒有執行的必要離線測試記 NOT_RUN／BLOCKED，不能用 compile 代填 PASS。尚未手動真站記 LIVE_NOT_VERIFIED，與離線工程結果分列，不因此阻擋本版清理與完成。

---

## 22. 上游同步、手動重新設定與回復

### 22.1 上游交界的最小變更策略

P0 固定 actual HEAD、真實 upstream URL/ref/SHA、merge base 與上次整合點。`fetch` 與 `merge/rebase` 分開；不假設 origin 是原作者，也不宣稱本輪重新核對最新分支。

自訂模組的清理提交、shared helper 改動、core gate/route 接入、settings/current 設定與 source resources 分開管理。主入口使用新 bootstrap 而非重排整份路由。真正需要的 current-core mapping 放入精確 inventory；不能以減少 diff 為由保留舊棒球 API。

### 22.2 上游更新演練與正式整合

在獨立整合分支／worktree 查看上游增量與 touchpoint 的交集，包括已搬到新檔、但原作者仍在原位置修改的同一規則。跟隨維護者選擇的 merge/rebase 策略，不擅自重寫已分享歷史。

檢查新 gate 是否能限制已 attach worker；所有外層自動 reload/導航是否走 session 仲裁；共用規則是否仍只有一份；API／current 設定／source resources 變動是否有正確結果映射。沒有文字衝突不代表行為正確。

跑受影響的 unit/contract、U/X/I、browser、source startup 與 V cases。runtime 或 scripts 有變就做適用離線回歸；既有手動真站結果僅標示其原 SHA，不宣稱涵蓋新版本。U05/U06/U08 是代表性的受控演練，不保證將來零衝突，也不需要先重構其他平台。

### 22.3 個人原始碼版本切換

保存可回復 source commit 與私人設定備份，停止舊實例；只有 uTiki 行為重新設定。先使用合成設定完成工程驗收，真正私人檔案由使用者按 §11／§21.4 手動處理。

在記錄的 Python／依賴環境啟動同一新 source 的 settings 與 bot；正式 uTiki 入口只理解 current 格式。首次真站使用由使用者自行決定，從可信新起點進入，不承接來源不明的舊 submit intent。

沒有 converter，也不要求舊值自動保留。accounts／其他平台不因 uTiki 重填而重建；不藉更換設定解除網站 quota 或舊 unknown 結果。

### 22.4 回復程序與限制

停止新版，保留脫敏診斷，配對舊 source commit 與原設定備份；ownership/API/current-config 接合一起回復，不在同一 tab 執行兩版。使用者工作與秘密不可被 reset／clean 或整批 restore 覆寫。

coordinator schema 預設不變；不得刪 cooldown／lease／DB 來讓程式再嘗試。若真的需要 schema 變更，另作明確需求決策，不假借小型 adapter 改 shared 協議。

本地回復不會取消已送出的網站請求。沒有持久化購票 ledger，不能宣稱跨崩潰 exactly-once；使用者回報與 server evidence 仍分開。

### 22.5 主要風險與對應驗收

| 風險 | 必要證據／處理 |
|---|---|
| 三個 reducer 串行但跨 phase 仍衝突 | X collision matrix、ActiveEffect terminal/held 狀態與 late receipt |
| 普通刷新優先級過高拖慢購票 | ready-vs-refresh 測試與 V15，本地撤銷未 commit refresh 權限 |
| 模型有進無出、人工 restart 清掉未知 | T 系列、完整 state/event 預設保持規則、retired-unresolved |
| input ready 虛假或 stale OCR 填新頁 | I 系列、native setter與框架回讀、input/challenge identity |
| 設定鏡射被 loader 偷轉換或重填誤傷其他平台 | C01–C06/U10/P05，legacy/mixed 拒絕；current 空值／原子保存／其他資料保持 |
| source 換 cwd／OS 後缺 JS 或 settings/bot 契約分歧 | P04/P05/H09/A04，原生 source 啟動與同版本資源 |
| interception 失效真的外送 | H01–H04，獨立 OS 網路限制＋故障注入 |
| 型別／marker／案例集合或語法 gate 假綠 | H05–H08/H11/A06；獨立 catalog＋四輸入核對、逐檔語法範圍；聯合刪例／錯 marker／漏新檔／舊 JUnit 均拒絕 |
| 頂層 integration 增生或藏 legacy | ADR-01、U11/U12、精確 current-core inventory |
| 未完成替代／離線覆核就刪 legacy，或誇大真站能力 | H12/P6b→P6c；缺前置阻擋清理；離線完成另列 LIVE_NOT_VERIFIED |

---

## 23. 完成定義與防回歸護欄

### 23.1 原始碼工程完成清單

- [ ] v1.6 全文／digest、需求變更、當次 Issue/branch/實作授權與新 catalog 核准已記錄；舊 P1 原始成果保留。
- [ ] ADR-01 的直接新 bootstrap 或必要 current-core 薄適配有依據，沒有重複 root。
- [ ] domain/profile/config 不拉 browser/UI/OCR/I/O；application 不拼 JS、不直接操作 tab。
- [ ] 唯一 writer／ActiveEffect，三個 reducer 只提 proposals，所有 mutation 與核心旁路經仲裁。
- [ ] unknown submit 不因 timeout／設定／刷新被抹掉；matched late receipt 仍歸原 attempt。
- [ ] ordinary refresh 讓位 ready purchase，已進 permit commit／possible effect 不被假設未發生。
- [ ] Purchase／Input 所有出站／保持／拒絕／人工 retire／新 cycle 有效；原 native setter 及 stale OCR/reset 保護完整。
- [ ] 具名 snapshot、單一 raw codec、structural sharing，沒有 magic 欄位索引或無意義深複製。
- [ ] close／late completion／exact cleanup／waiter／lease 有界，不復活 session 或清別人的 token。
- [ ] current `utiki` 唯一格式；legacy/mixed 只提示重新設定，沒有 converter、mirror、fallback 或舊值自動搬入。
- [ ] 設定網頁仍可用；其他平台／accounts／私人資料不因 uTiki 重填受損；正常保存失敗保留來源。
- [ ] 新 API/caller/UI/default/save/source-resource 一致；舊 facade/model/decoder/workflow/alias/test bridge 清理完成。
- [ ] 不交付升級器／產品打包／frozen／無 Python／release；沒有只改名稱而保留的替代框架。
- [ ] Windows x64 與 macOS arm64 直接 source 啟動／配置／資源／適用 browser 契約都具有原生證據。
- [ ] 全部新／重寫 production 模組在 typed scope，無廣域 Any／ignore 逃逸；P1 受控 fixture 的 PASS 不代替 production 型別驗收。
- [ ] 143 個 active case 及其 obligations 由獨立 catalog 定義；13 個撤回 ID 有正式處置，3 個新 ID 未重用舊義務結果。
- [ ] 四輸入 gate 核對完整集合、PASS_ONLY、run/attempt/SHA/digest／job/target；case 搬家不縮需求，舊版核准值不冒充新版。
- [ ] Git 推導的完整 modified-Python 集合涵蓋所有實際 production/tool/test/image-helper 改動，含新檔／rename／delete／checker 本身；逐檔 compile 結果可核對。
- [ ] A05/A06/H06/H11 的聯合刪除／偽造不適用／錯版本／漏編譯負向保護保留；非 pytest 空 JUnit 例外只用於對應 evidence job。
- [ ] production-origin／native browser 測試保留 hard-egress；interceptor 失效仍不外送；Linux 不代填其他 OS。
- [ ] P3a source 資源同期驗證；P6b 離線候選覆核後 P6c 刪除，清理後重新驗對應 source，不重開真站門檻。
- [ ] 相同條件的 source cold/warm／CDP／failure／CPU/資源與效能差異可核對，沒有為分層增加無用途 hop／sleep／I/O。
- [ ] 最終 source SHA、JS/current-config／catalog／測試 image 與原始報告吻合；新候選不拼舊 run／attempt 充數。
- [ ] 完成 source 使用／重填／回復指引；原始碼工程結果與 `LIVE_NOT_VERIFIED` 分開，不以未真站作工程 blocker，也不宣稱實際購票保證。

### 23.2 機器檢查與人工審查的分工

AST/import scanner 驗 DAG、retired symbol、raw browser/filesystem primitive、不可信資料進入點、resources 與 typed manifest；它不能證明動態 Python 沒有任何非法行為。

contract/stateful tests 驗 claim/reducer/控制/receipt 時序，browser tests 驗 production JS，原生 source tests 驗雙平台啟動與依賴，使用者另行手動真站才驗當前站點。各層不互相冒充，沒有 frozen／產品成品 gate。

required catalog 獨立定義必要 case/obligation/variant、層級、gate/OS/tenant 與適用條件；execution manifest 只映射實作／node ID／證據。`verify_utiki_ci_results.py` 同時接收這兩份檔、JUnit、run metadata，核對外部核准 catalog digest 及完整集合。測試搬家可更新 node ID，不能刪除 invariant 或讓不適用條件由執行清單自行宣告；細節見 §16.11／§17.5。

逐檔語法 checker 另外從 Git／核准 build-source inventory 推導完整 modified-Python 集合，交叉核對 manifest，再以 py_compile 編譯每個應編譯檔。不得依 pytest import、typed scope 或固定 compileall 目錄猜測覆蓋完整；syntax PASS 只證明該 interpreter 下語法可編譯，不證明功能／browser／原生 source startup／真站已通過。

本輪文件檢查結果記錄於 `DOCUMENT_CHECK_RESULTS_v1.6.md`。已有 P1 工具沿用，新 scope 接合與程式成果仍須實作驗證；文件 PASS 不等於 repository、CI 或真站 PASS。

不以固定 class/檔案數或每函式 50 行替代責任審查；尤其不能把同一次 browser guard+commit 為湊行數拆成多輪 CDP。

### 23.3 清理與擴充實驗

A：新增一個排序規則，只動選擇／policy 與必要 UI metadata，不改 lease或arbiter。

B：網站 selector 變更，只動 browser/codec/fixture；真流程語意變更才改 reducer，不任意放寬 guard。

C：新增已知拒絕，必須補 classifier/轉移/必要 recovery/collision case；正常熱路徑不新增一輪查詢。

D：移除舊棒球 API/model/fixture helpers，新模組仍用新 ports 獨立測試；不需要轉成舊 state 才能通過。

E：上游新增 gate，已執行 worker 的新 claim 會受限制；舊已發生的 receipt 仍正確歸屬，不被當成「stale 就全部丟」。

F：故意將 ordinary refresh 與 ready submit 同時提出，普通刷新不能搶先；將 submit 改成 held unknown，兩者都不能製造新 mutation。

G：故意拔掉 source JS resource 或 interception，source-resource／isolation gate 正確變紅；不能 fallback 舊 script 或外連真站。這個字母是實驗項次，不是已撤回的 G 系列案例。

---

## 24. 交給 Codex／程式代理的工作指示

### 24.1 文件審查的範圍

只讀確認 v1.6 已一致投影個人 source 用途：無升級器、無產品打包、只重填 uTiki、其他資料受保護、Windows/macOS source、測試隔離保留、離線覆核後清理、真站另列。

對 §16 的有效/撤回/替代 ID、§19 的階段、§21 的命令與 §23 的完成條件交叉核對。未改動的 §5–9 與原有效 review 可沿用；不重審整個 repository 或另建威脅模型，只找具體矛盾。

本文件是規格，不因有命令或代理指示而授權外部操作。現有工作接入依使用者另行採用訊息。

### 24.2 正式採用後的一次性接入

```text
以維護者採用的 v1.6 全文／digest 與既有連續工作授權為準。
先在安全檢查點核對 actual HEAD／dirty 狀態與已到哪個 phase。
不 reset 到歷史 P1，不打斷在途 review／CI，不開第二個 writer。

停止新增 uTiki upgrader、產品打包、frozen/no-Python、release 與
刪除前真站 gate 的工作；已完成內容先保留，再按本版範圍作最小清理。
保留狀態機、native setter、低延遲、資料保護、P1 工具與測試隔離。

僅一次同步 plan/catalog/mapping/phase/必要 CI 與規範：
143 個 active case；G01–G10、P01/P03、H10 撤回；新 P04/P05/H12。
不把撤回變成永久 skip；其他仍有效的 obligations 不漏。
新 catalog 位元組需要一次 review/digest／正式核准，不用舊值冒充。
沿用 P1 checker，必要固定版本／ID／scope 接點局部調整，不重造框架。

current uTiki 設定由使用者重新填；代理只用合成設定驗證。
不寫入 src/settings.json、不清 accounts／Cookie／profile／其他平台。
設定 UI 可用；不把升級器改名成 reset wizard 或偷偷塞入 loader。

P3a 同期驗 source 資源，不產品 build；測試 Docker image 繼續可用。
P6a 原始碼整合 → P6b 固定候選離線獨立覆核 → P6c 無用 legacy 清理。
離線前置及本版工作分支授權成立後即可清理，不再等真站／逐檔核准。
P8 結案為 source 完成與手動真站未驗證分列，不等 release。

持續按既有自主收斂規範：根因分組、最小完整切片、有限診斷、差異覆核。
相同 catalog 版本未變的核准可沿用；跨 v1.5→v1.6 不能沿用舊 digest。
已完成工作不重開，真實新 bug 不忽略；不用規範修改來洗綠。
```

上段僅為採用後執行摘要。配套 `CODEX_V1.6_ADOPTION_AND_CONTINUE.md` 提供更完整的訊息草稿；文件本身不直接授權 repository mutation。

### 24.3 必須同步的舊治理條款

檢查 `AGENTS.md`、測試／工程審查指南、adoption、legacy-removal、target／gate 文件，以及已採用的 `autonomous-execution.md`。只改與本版直接衝突的「upgrader 必交」「P3 frozen」「live-before-delete」「P8 release」「禁止所有 build」等條款；保留其他平台規範、秘密保護與必要離線覆核。

測試 image／依賴準備與產品打包分開。上游 spec／release workflow 不屬本次成果，沒有直接妨礙 source 執行或誤觸發布就不刪改；scope 內不再需要的自訂 job 移除或隔離，不改 branch protection 來略過仍必要的測試。

採用紀錄與 actual code/reference 若矛盾，用一份可核對差異說明；不新建第二套計劃／catalog／approval service。所有 formal result 仍依新 catalog 原始證據成立。

---

## 附錄 A：每階段證據紀錄範本

```text
計劃版本／digest／需求變更與採用來源／Issue：
phase／授權範圍／actual HEAD／dirty 狀態／已驗收 candidate：
baseline／upstream／source／script／current-config digest：
case／obligation／variant／invariant／預期行為：
變更類型：保持行為／bug-fix／breaking-policy／performance
唯一 owner／ActiveEffect／event／receipt：
retired API/model/mirror／current-core 例外：
typed scope／effective config／逐檔 syntax base/candidate／結果：
required catalog／approved digest／scope-change review：
execution manifest／JUnit／run metadata／run+attempt+job／digests：
required／checked／failed／missing／not-due／inapplicable：
Windows／macOS 原生 source 啟動／browser／設定 UI／resources：
Linux 參考測試／Docker image／hard-egress／canary／cleanup：
實際命令／collected／pass／fail／skip／xfail／xpass／exit code：
設定保護：合成檔／current 值／其他平台及 accounts／私人資料未碰：
P6b 離線覆核／替代清單／回復點／清理前後 SHA：
效能 cold/warm／樣本／不確定度／CDP／請求／failure／資源：
獨立覆核範圍／finding／處置／仍待補：
手動真站：LIVE_NOT_VERIFIED 或使用者實際記錄（不作自動 gate）：
結果：PASS／FAIL／NOT_RUN／BLOCKED／INCONCLUSIVE
工程完成時：SOURCE_REFACTOR_COMPLETE；手動真站狀態另列。
```

NOT_APPLICABLE 只能由 active catalog 的真實條件推出。撤回案例不做執行期 exemption，而是在正式規格變更中移出集合；尚未到期 NOT_DUE、缺環境／review BLOCKED 分開。文件檢查不填入程式測試欄位，不需要為每次狀態更新更改 candidate SHA。

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
| 設定 | 使用者重新填 uTiki，其他資料保留；唯一 current 格式 | converter／conflict 遷移／loader 偷清 mirror／重設整份私人設定 |
| 驗證順序 | P3a source resources；P6b 離線覆核；P6c 清理；P8 source 結案 | 未接替就刪功能／用 Linux 代雙平台／沒有真站就誇大成功 |
| CI隔離 | interception＋獨立OS egress deny | proxy/hostresolver/CDP log單獨當硬隔離 |
| 型別／測試 | 整個新typed範圍＋獨立 catalog／四輸入結果 gate＋逐檔語法 gate | 只測domain、清空strict flags、manifest 自訂 required、漏檔／全skip假綠 |
| 效能 | 一次合併觀察、同次guarded operation、立即續行 | 為每個reducer或adapter多建queue/tick/CDP |
| 授權 | 設計偏好、版本採用、實作、live、發布分別記錄 | 附件指示自動視為當次修改或購票授權 |

---

## 附錄 D：來源、證據與適用範圍

### Repository 與歷史引用

本輪完整閱讀 v1.5 唯一底稿，並依本次需求確認修訂。沒有新讀 repository、使用者本機、CI、外部官方頁面或真站。R02–R20／E01–E13 的連結沿用 v1.5，沒有重新驗證其內容／可用性，也不要求按最新版本升級依賴。

`SOURCE_MANIFEST_v1.6.json` 列本包實際檔案、SHA-256、閱讀或僅封存的狀態。完整底稿與需求確認可重現文件差異；不能單憑文件重現原 CI 或宣稱最新分支已符合 v1.6。

**[R01]** 前次架構審查 `sources/tickets_hunter_refactor_review_5a5fb9e_f307f7c.md` 已原樣附入，SHA-256：`60957f877fa6a90dc1244ceb124fe768704eb1582cc0ca2dae18d375c726e520`。本輪僅確認檔案與位元組並封存，未獨立重讀／重跑；舊相容期或發布建議不覆寫本版需求。

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

以上連結固定在歷史設計基準，不是最新分支查核。本版採用後只同步直接衝突規範；R17/R19 的產品打包及 R18 的刪除前 live 政策已由使用者新需求撤回，不能藉歷史連結恢復。

**[R18]** [工程審查指南 §6：重構與 final legacy removal 的門檻](https://github.com/OooPeople/tickets_hunter/blob/f307f7caba054fb40aab807a4629c891e69fb200/docs/02-development/engineering-review.md)。此為 v1.5 保留的歷史查核陳述；v1.6 本輪未獨立重讀。

**[R19]** [正式 build-release workflow](https://github.com/OooPeople/tickets_hunter/blob/f307f7caba054fb40aab807a4629c891e69fb200/.github/workflows/build-release.yml)。此為 v1.5 保留的歷史管線定位；v1.6 本輪未獨立重讀，亦未執行 workflow／build。

**[R20]** [目前核心 native input setter](https://github.com/OooPeople/tickets_hunter/blob/f307f7caba054fb40aab807a4629c891e69fb200/src/nodriver_common.py#L298-L323)。此為 v1.5 保留的唯一來源／回讀背景；v1.6 本輪未獨立重讀。

**[Q01]** Codex 早期 v1.3 複審僅為 v1.5 保留的歷史摘要；本輪未取得原始完整報告，不重新斷言先前本機狀態或審查人員結果。

**[Q02]** `UTIKI_ROOT_REFACTOR_PLAN_v1.3.md` 未取得；`sources/UTIKI_CLEAN_MODULE_RULES_v1.3.md` 已原樣附入，SHA-256：`57cfd453d18a2330d6e3a9ee6aedb1e2b53a293d2f5947372a60c709b77fda78`。後者僅確認位元組並封存，不作底稿或現行規範，未在本輪獨立重讀。

**[Q03]** v1.5 的四項驗收硬化與 Codex finding 依唯一底稿保留；新版本保留獨立 required catalog、逐檔語法、防假綠與來源限制，不把原貼文當成新版程式驗收。

**[Q04]** 唯一底稿：`sources/UTIKI_ROOT_REFACTOR_PLAN_v1.5.md`，2,679 行、232,652 bytes；SHA-256 `c8fcc741a37db3d404cefd73cfcda6731a24a922f42522777fbdbc3eeddf6328`。本包 unified diff 僅比較此檔與 v1.6，不是 repository patch。

**[Q05]** 本次需求：使用者表示「不需要升級器，這個專案只有我使用，可以直接重新設定」，再表示「也不需要打包，先跟我確認我的需求好了」，並確認「1a 2保留 3b 4b」。四項選擇、採用的預設及未取得的操作授權封存在 `sources/USER_CONFIRMED_REQUIREMENTS_v1.6.md`；這是本次範圍變更的直接來源。

**[Q06]** 使用者先前提供 P1 出站摘要：source `32c626dcb89e979a18b3b3d91e267bb9137a5899`，P1 run `38965038276`／attempt 1、63/63；Windows/Ubuntu run `38965038189`。本輪只封存摘要定位，不重算遠端 artifact 或驗證 reviewer 結果，也不推論現在 HEAD。

**[Q07]** 既有 `autonomous-execution.md` v1.0 與當前對話已表達的連續工作方式：維持根因收斂／切片／差異覆核；本版只替換其中交付政策。隨包的 v1.1 是文件相容修訂，不新增代理框架或操作權限。

來源原檔不具有執行權；其中舊打包／升級／live-before-delete 指示不再是本版需求。沒有封存或重驗的外部內容就是本輪證據限制，不以似是而非的引用補足。

### 官方技術文件

下列技術背景由 v1.5 繼承，本輪未重開網頁或核對 API 最新版本；實作沿用專案已驗證依賴與必要的實際版本查核。引用不是升級套件或新增產品打包的要求。

**[E01]** [Python 3.11 typing／Protocol／NewType](https://docs.python.org/3.11/library/typing.html) — 型別註記不會自動在執行期逐次驗證所有資料；靜態型別與不可信輸入驗證的責任應分開。

**[E02]** [Python 3.11 asyncio tasks](https://docs.python.org/3.11/library/asyncio-task.html) — Task、取消、TaskGroup、sleep 的語意；取消後清理與重新拋出、持有工作引用，以及不把每一步變成不必要排程。

**[E03]** [Python 3.11 dataclasses](https://docs.python.org/3.11/library/dataclasses.html) — frozen 與 slots 的工具能力；本計劃仍要求內層資料不可變，不以 frozen 外殼冒充深度不可變。

**[E04]** [pytest markers](https://docs.pytest.org/en/stable/how-to/mark.html) — marker 註冊與嚴格檢查；零收集／全 skip 的 gate 是本計劃另外要求實作。

**[E05]** [Hypothesis stateful testing](https://hypothesis.readthedocs.io/en/latest/stateful.html) — 以動作序列與 invariant 測試狀態轉換；不代表可以自動替代真實 DOM。

**[E06]** [PyInstaller run-time information](https://pyinstaller.org/en/stable/runtime-information.html) — 僅保留歷史來源索引；v1.6 不以本連結建立任何 frozen／產品打包驗收義務。

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

**個人使用的 Windows／macOS 原始碼只有新 uTiki 架構，正常設定介面可用；舊 API／model／workflow／設定 fallback／private test bridge 已接替並清理。必要 current-core 適配有證據，不誤刪真實外部契約或其他平台能力。**

保留完整 session 仲裁、Purchase/Input 出口、unknown barrier、native setter、精確 cleanup 與低延遲；測試仍使用 P1 的 strict/type/完整語法範圍／獨立 catalog／hard-egress 與同候選正式結果。

**不提供升級器、不做產品打包、不以真站成功擋離線清理或 P8 結案。** 使用者只重新設定 uTiki；其他平台、私人帳號、profile 與 coordinator 狀態不被清空。工程結果可為 `SOURCE_REFACTOR_COMPLETE / LIVE_NOT_VERIFIED`，不把未做真站變成已驗證。

本文件是完整 v1.6 設計修訂及 scope 調整，不是已完成 repository 接入、已核准新 catalog 或已執行測試的宣稱。最終以正式採用版本、實際 source 候選、必要獨立覆核與適用驗收證據裁決。

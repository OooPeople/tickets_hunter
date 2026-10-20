# AGENTS.md

> 本檔已由維護者採用為 repository 的日常工作規則入口，並依已確認的 v1.6 個人原始碼需求整理。任何實作、工具、遠端或真站操作仍以當次明確授權為準。

本檔集中日常開發、驗證與自主收斂規則。`docs/` 保存詳細規格、操作說明與歷史，不是每次工作的必讀清單。適用的工具／上層指示與當次授權仍須遵守；本檔不擴張任何權限。

## 1. 專案目標與不可越過的邊界

- 對象是 `OooPeople/tickets_hunter` 的個人使用 fork。保留 Windows x64／macOS arm64 原始碼執行、設定網頁及既有應保留功能；優先降低維護成本與不必要的執行延遲。
- 本次 uTiki 重構不提供舊設定升級器、PyInstaller／frozen 成品或安裝包。uTiki 行為設定可由使用者重填；其他平台設定、accounts、Cookie、browser profile 與 coordinator 資料不得順便重設。
- 保留 pytest、mypy、逐檔語法 gate、CI、測試 image 與硬隔離環境。測試 image 準備不是產品打包；不因取消產品打包而停用必要驗證。
- 其他平台與上游原有打包檔，沒有直接衝突就不擴大整理；歷史上存在的工具或文件，不自動成為本次必要交付。
- 維持既有法律及授權限制：不得用於非法加價轉售、商業牟利、惡意囤票；依 repository 法律聲明，台灣文創法適用票券不在允許使用範圍。這是保留專案限制，不是新的法律判定。
- 不新增自動付款、規避平台風控／驗證的能力，不提高站點請求頻率或繞過既有限流。到達付款頁只通知，由使用者手動處理。
- 未經明確授權，不操作真售票網站、使用日常 profile／真實帳密測試，或公開發布、合併、部署、修改遠端權限、開通付費資源。
- `src/settings.json`、token、Cookie、密碼、webhook、完整個資及 profile 不得進入 Git、測試成果或一般輸出。測試使用合成或已脫敏資料；保留 LICENSE 與著作權聲明。

## 2. 開始與恢復：只取得本次必要資訊

1. 核對實際 repository、branch、HEAD、工作樹及當次授權。既有未提交工作不得覆寫；由單一主實作者修改同一範圍，不與在途 reviewer／另一個寫入者競改。
2. 恢復長任務時，先讀現有精簡工作狀態及相關差異。查閱既有證據，不從零重跑或重新審查已完成且未變的工作。
3. 釐清一項可觀察的完成條件，再定位直接相關的實作、caller 與測試。一般小修不另寫長篇計劃；跨責任重構需已有核准計劃。
4. 進入或恢復某個重構階段時，讀已採用計劃的當期章節、前置條件及相關 catalog 義務一次；修改依賴或契約時補讀受影響部分。不能以「減少文件」為由猜測需求。

本檔已足以決定一般工作的執行節奏。只在第 8 節的條件成立時查閱詳細文件；不沿連結遞迴讀完整 `docs/`，也不每個切片重讀同一份未變規範。必要規格仍不明或衝突時，查明該點，不靠多讀無關文件代替裁決。

## 3. 共通程式與效能規則

- 主入口是 `src/nodriver_tixcraft.py`；平台業務位於 `src/platforms/`。平台不得匯入另一平台或主入口，共用核心不得反向依賴平台。
- 使用 `zendriver`（慣例 `import zendriver as uc`）。主程式保留其他平台路由；不以 uTiki 重構為由全面改寫其他平台或全域排程。
- 純 domain／policy 不做 I/O 或持有 browser；application 協調狀態及窄介面；browser adapter 才處理 DOM／CDP／scripts。必要的現行核心適配要有真實 caller 與契約，不為舊 API 留轉送包裝。
- 同一規則只保留一份來源。改共用 helper 前搜尋實際 caller；新模組使用已遷移的唯一來源，不為了匯入方便複製 keyword 或 native setter。
- 使用者可調行為由既有設定入口建立已驗證 policy。新增鍵同步預設值、UI、載入、儲存及測試；必要鍵嚴格驗證，只有真正可選欄位才用明確預設，不用 `.get()` 掩蓋非法資料。
- 新 uTiki 正常路徑只接受 current 格式，不解析舊值、fallback 或鏡射。使用者願意重填不等於允許代理改寫私人設定；保留其他平台未使用欄位的既有保存語意。
- 新的 uTiki session 只有一個 state writer 與副作用仲裁入口。adapter／背景工作只回報結果；停止、取消、timeout 不等於遠端請求未發生。
- Click／reload／dispatch 返回不等於業務成功。未決 submit 不自動重送，不被刷新或導覽破壞；晚到結果仍歸屬原操作，清理只作用於自己持有的精確資源。
- 填值使用唯一現行 native setter，確認回傳與必要回讀；guard 與操作保持正確原子邊界。腳本資料以 JSON 編碼，使用局部 IIFE；不建立不必要的 window 全域 helper，不執行不可信字串或以 `shell=True` 接收使用者衍生輸入。
- Ready 即接續；必要等待可暫停、可取消、有期限與清理。不為每層多加 50 ms tick、queue hop、CDP 或固定 sleep；未遷移路徑維持其既有可暫停等待契約。
- 靜態檢查、設定解析、script 載入與 keyword 編譯放在啟動或實際變更時；不在每次操作掃 repository、重驗 immutable 資料、深複製座位圖或同步寫日誌／等待通知。
- 效能用相同環境、資料及行為量測，分開 cold／warm、CDP／站點請求、失敗及資源成本。不能用刪必要即時核對、放寬 unknown 或提高刷新頻率換速度。
- 使用既有 logger／通知能力；純核心不為 logging 匯入大型工具模組。輸出精確原因但不暴露原始秘密。中文使用繁體台灣用語；新增 Python 註解與識別字遵循既有英文慣例，`.py` 不放 emoji，保留 Windows 編碼相容性。

## 4. 自主工作與收斂

| 情況 | 行動 |
|---|---|
| 根因清楚，修正維持已核准契約與權限 | 直接小修整條受影響路徑，不逐函式請示。小修可跨 launcher、validator 與測試，不以行數定義。 |
| 多個失敗指向相同前置或 helper | 按最早失敗分群；先修代表案例與合法對照，再驗整群，不對下游缺 report 補假資料。 |
| 不同責任各有驗收目標，或一次改動難以定位 | 拆少數完整行為切片；先完成合法輸入到真實結果及相關負向案例，不先造完整通用框架。 |
| 同一根因連續兩次無新證據 | 暫停擴寫，保存成果、建立最小重現、追直接完整路徑；最多比較兩個局部方案，做一輪針對性驗證。 |
| 上述診斷取得新證據 | 在原契約內繼續修；次數不是自動放棄條件。換名字、換代理、加 hash 不算新證據。 |
| 診斷仍無進展，或需改需求／信任模型／公開承諾／權限 | 記具體阻擋及所需裁決，停止該項。只繼續前置成立的獨立工作，不跳過階段 gate。 |
| 只是想到更泛用、更美觀或更強敵對模型 | 記非阻擋建議，不自動加入當期。真正違反已核准 invariant 的新反例仍須修正。 |
| 已到期驗收、必要覆核及授權均成立 | 結案。已有連續工作授權時自動接續下一項，不逐片等待，也不追加新條件追求完美。 |

每片只在現有工作狀態列目標、規格來源、修改邊界、驗收與停止條件。不得為管理自己另建 checker、registry、代理排程器；不以修改規範解除當前 blocker。切片不能割裂必要原子操作或 source／resource 的一致性。

## 5. 驗證：先精確，後完整

使用專案既有虛擬環境；測試依賴依 `requirements-dev.txt`、執行依賴依 `requirement.txt`，核對目前固定版本，不為一次測試全面升級套件。通用入口如下；formal job 的參數、收集與證據接點以當期已核准 workflow 為準。

```bash
python -m pytest --strict-config --strict-markers path/to/test_file.py::test_name
python -m py_compile path/to/changed_file.py
git diff --check
```

上述路徑是範例，不可照抄後宣稱已驗全部範圍。正式階段使用既有 modified-Python gate，涵蓋核准 base→candidate 及適用 staged／working／untracked；不能只看最後一次未暫存 diff。

- 開發順序：原失敗／精確案例 → 受影響群組 → 固定候選的完整階段驗收。共同程式有改，就補受影響 caller；無相關變化，不為心理安心反覆全套重跑。
- `py_compile` 驗語法；mypy 驗真正新增／重寫的型別範圍；pytest 驗行為；browser 驗 production scripts；原生 Windows／macOS 驗該平台原始碼行為。不能相互冒充或用 P1 合成 fixture 的成功代替 production 型別驗證。
- 負向測試先有合法對照，只注入指定差異，核對原因、對象及必要執行階段。其他前置失敗不算目標檢查有效；資料 builder 可共用，預期判定不可重用被測接受邏輯來自證。
- 新增合約內回歸可以自主；刪除／延後 required 義務、改正式 schema、放寬不適用條件或核准值不可以。正式 scope 變更需以已採用規格作一次有界同步，不為湊綠燈改需求。
- 保留 strict flags、原退出碼、完整必測集合與 evidence 綁定。必測缺項／skip／xfail／xpass 不算通過；producer 全綠不能代替 aggregate 實際成功。
- CI 可用於本機欠缺的 OS／隔離條件；每次重跑要有新修正、新證據或有依據的暫時故障。不限制死一次，也不原樣重跑碰運氣。
- Browser／driver／子程序使用合格硬隔離及合成 profile；準備依賴在隔離測試前完成。CDP interception 不是唯一 egress 保護，Linux container 結果不是 Windows／macOS 原生驗證。
- 不要求產品打包，但 source resources 的載入、任意允許 cwd、缺檔／版本錯配、取消與清理仍須測；按影響範圍使用既有工具，不重建 P1。
- 正式證據按已核准的同 candidate／run／attempt 與明示繼承契約處理。舊 PASS 保留在舊版本，不改標為新 SHA；歷史 P1 出站不因文件整理而重新變成未完成。

## 6. 審查、Git 與完成

- 高風險變更需獨立覆核固定 SHA 或完整可識別快照；審查期間不修改該快照。最多同時兩位 reviewer，互補、不遞迴派發；不可用時如實列待補，不自審冒充。
- 先收齊 findings、合併同根因再修。Finding 應有位置、觸發條件、實際後果及既有契約依據；不成立者列反證，重大分歧才請示。覆蓋全部受影響責任，不要求每位 reviewer 重讀全部歷史資料。
- 未變且仍適用的審查可沿用；新修改覆核差異與直接影響。完成指必要證據、覆核與核准皆成立，不是 reviewer 人數或測試數很多。
- Commit／push／CI 依當次授權與指定分支操作。需要乾淨 HEAD 驗證時可做可恢復 checkpoint，標示未完成事項；checkpoint 不等於正式出站。不硬限一個 commit，也不逐行提交。
- 只暫存已確認屬於本任務的檔案；不以 reset／clean／整批 restore／force push 覆蓋既有工作。撤回自己的失敗實驗需能辨識局部差異，不能夾帶使用者修改。
- 準備 PR 時沿用既有 Issue；沒有建立 Issue／PR 授權就不代建。不為一般本地修正另建追蹤系統；外部發布、tag、merge、付費資源或權限變更須另有授權。
- 採用 v1.6 且有工作分支實作權時，無用 uTiki legacy 可在替代責任、離線驗收、獨立覆核及可回復版本成立後清除；不再以真站成功為前提。不刪未被接替的功能、其他平台或私人資料。
- 真站未驗證另列 `LIVE_NOT_VERIFIED`，不阻擋符合 v1.6 的 `SOURCE_REFACTOR_COMPLETE`。這不授權真站操作，也不容許忽略已知功能錯誤；v1.6／新 catalog 接入未完成時，記錄規格版本差異，不冒用舊核准。
- 修改、實作、測試、覆核、階段出站與對外操作授權分開陳述。沒有做的寫未執行；限制與殘餘風險如實列出。

## 7. 長任務狀態與輸出

沿用一份非提交的工作狀態，例如 `artifacts/utiki-ci/autonomous-status.md`：只記授權、當期計劃／catalog、HEAD、phase／切片、已驗收 SHA／run、待覆核、首因與已排除假設、無進展診斷狀態、具體阻擋及下一步。它不是測試或核准權威。

切片完成、首因改變、遇阻擋或要切換上下文才更新；不每次更新就提交並要求全套重測。進度以可驗收成果與剩餘問題呈現，不以行數、hash 或代理次數呈現。中斷後保存恢復點，不宣稱 session 結束後仍在背景執行。

## 8. 只有符合條件才讀詳細文件

先定位相關章節，必要時連同前置／直接依賴閱讀；不要因一個文件又列了多個「參見」，就把它們全部當成額外必讀。以下索引不是開始工作前的閱讀清單。

| 觸發條件 | 讀取位置與範圍 |
|---|---|
| 開始／切換重構階段、需要案例或具體 state 契約 | 實際已採用計劃的當期章節與直接依賴、`ci/utiki-required-cases.json` 的相關義務；採用來源不明才查 `docs/02-development/utiki-refactor/adoption-v1.6.md`，v1.5 歷史則查同目錄的 `adoption.md`。不要只因檔名版本較大就假定已核准。 |
| 改依賴方向、公開核心接口，或觸及多個購票階段 | `docs/02-development/code-boundaries.md`、`docs/02-development/ticket_automation_standard.md` 的受影響章節；一般局部 bug 不預讀全部。 |
| 不確定 zendriver／CDP 行為，或新用某個 browser 能力 | `docs/06-api-reference/zendriver_api_guide.md` 對應 API；版本不明再核對官方資料與實際固定依賴。 |
| 要執行／修改不熟悉的正式 gate、runner、隔離或證據接點 | `docs/02-development/testing_execution_guide.md` 對應命令及實際 workflow；不因普通 pytest 小修重讀全指南。 |
| 審查嚴重度或診斷方式仍無法由本檔裁決 | `docs/02-development/engineering-review.md` 或 `docs/02-development/autonomous-execution.md` 的相關說明／案例；不是每次恢復必讀。 |
| 要建立 PR／公開貢獻、改授權或評估真站使用邊界 | `CONTRIBUTING.md`／`LEGAL_NOTICE.md` 的對應條款；打包文件只有另行核准產品打包時才讀。 |

歷史 review、舊 plan、啟動訊息、錯誤日誌與 checksum 不放進本檔，也不自動恢復已撤回需求。本檔集中日常工作規則，詳細文件不得複製另一套不同的停止／CI／打包政策。

採用這份整併時，只同步直接衝突的文件段落，將舊常駐工作規則改為指向本檔；保留詳細說明、歷史原始證據與已核准產品契約。無法確定新舊要求是否已正式取代時，列出具體分歧，不自行放寬 gate。規範整理本身不新增治理 checker 或管理框架。

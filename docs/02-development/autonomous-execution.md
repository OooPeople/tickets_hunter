# 有限診斷與恢復案例

**文件說明**：補充根因不明、反覆無進展或長任務中斷時的診斷方法與既有案例。

**最後更新**：2026-10-17

> 日常工作節奏、切片、驗證、覆核、Git 保護及完成／停止判斷以根目錄
> [`AGENTS.md`](../../AGENTS.md) 為準。本文件只在那些規則不足以判斷嚴重度、
> 診斷方式或恢復細節時按需查閱；不是每次開始、恢復或連續工作前的必讀文件，
> 也不增加 repository、工具、真站、刪除、發布或遠端操作授權。

## 1. 使用邊界

- 已採用計劃與 required catalog 定義產品行為、架構契約、必要案例及階段門檻；本文件不改寫它們。
- 當次使用者授權決定可以修改或操作的範圍；工作狀態、舊對話、歷史 review 與本文件案例都不能自行擴張授權。
- 測試命令、runner 參數、隔離及 evidence 契約見
  [測試與驗證執行指南](testing_execution_guide.md) 的相關章節。
- finding 嚴重度與輸出格式見 [工程審查指南](engineering-review.md)。

[v1.6 計劃](UTIKI_ROOT_REFACTOR_PLAN_v1.6.md) 已由
[採用紀錄](utiki-refactor/adoption-v1.6.md) 固定，required catalog 由
[核准紀錄](utiki-refactor/catalog-approval-v1.6.md) 固定。不能用本文件自行修改 catalog、
schema、digest、workflow 或歷史證據，也不能把舊 catalog 的 PASS 冒用為新版 gate 結果。

## 2. 判斷首因

遇到失敗時，先辨識最早成立的一類：

| 類型 | 要核對的事實 | 常見誤判 |
|---|---|---|
| 建置／fixture 未成立 | 程式是否真的進入被測路徑、必要輸入是否存在 | 對下游缺 report 補假資料 |
| 工具未啟動或命令錯誤 | interpreter、cwd、參數、依賴及退出碼 | 把工具配置失敗算成產品缺陷 |
| 產品行為違反契約 | 合法對照、唯一輸入差異、實際副作用與狀態 | 只改預期文字或錯誤訊息 |
| 結果收集／傳遞錯誤 | producer、artifact、aggregate 是否同一候選與契約 | producer 綠就宣稱 aggregate 通過 |
| 環境／權限不足 | 缺少的 OS、隔離、憑證、秘密或付費資源 | 用其他平台或較弱隔離冒充 |

大量失敗先按最早失敗點分成少數首因群。選一個代表案例與一個合法對照追完整直接路徑；
代表案例修正後再驗整群。跨 OS／artifact 問題至少重現實際邊界，例如 Windows 路徑、
artifact mode、外部 cwd 或 frozen 資源載入；這不等於每個小修改都要重建所有環境。

## 3. 有限診斷重置

同一根因連續兩次修補仍停在相同首因，且沒有新證據時，依 `AGENTS.md` 暫停擴寫，做一次有界重置：

1. 保留可恢復候選、原命令、第一個完整失敗及必要輸出。
2. 重讀直接驗收條款，只追 source → caller → runner → report 的必要鏈路，建立最小重現。
3. 最多比較兩個針對該根因的局部方案，選擇不改需求或信任模型的最小方案。
4. 做一輪針對性驗證；取得新證據才恢復正常修正。

可算新證據的例子：找到更早的實際失敗點、排除一項根因、讓原本未執行的指定檢查真正執行、
代表案例由失敗變通過，或取得先前缺少的平台行為。只增加 digest、欄位、程式行數、改錯誤字串、
換 reviewer 或換分支不算新證據。

重置後仍無法前進，或修正需要改需求、信任模型、正式 schema、公開承諾或操作權限時，
將具體工作記為 `BLOCKED_ANALYSIS`／`AWAITING_DECISION`，列出最小重現、已排除假設、
局部方案取捨與所需裁決。阻擋只套用到該工作及其依賴；是否能繼續其他獨立工作仍依
`AGENTS.md` 與已採用計劃判斷。

## 4. 中斷與恢復細節

長任務沿用既有的一份精簡工作狀態，例如
`artifacts/utiki-ci/autonomous-status.md`。它不是規格、核准或測試結果的權威；恢復時仍要核對實際
repository、branch、HEAD、工作樹、在途工作及原始證據。建議只記：

```text
當次授權與禁止操作
適用計劃／catalog 版本及核准來源
HEAD、dirty 狀態、已驗收的 exact candidate
目前 phase／切片與可觀察完成條件
已完成命令、run／attempt、原始證據路徑
待覆核範圍與未處理 finding
最早失敗、最小重現、已排除假設、新證據
診斷重置是否已使用
具體阻擋、解除所需輸入及下一個安全動作
```

session、配額或工具中斷時只保存恢復點；不要宣稱中斷期間仍在背景執行。舊 PASS 保留給舊候選，
恢復後不得因狀態檔寫著通過就把結果轉記到新 SHA。

## 5. 既有案例

| 事件 | 有效診斷 | 無效處置 |
|---|---|---|
| 多個 Windows 測試同時失敗 | 先核對路徑、啟動鏈與換行等共通首因，再看殘餘 | 為每個測試各寫例外或直接 skip Windows |
| Python runner 與 validator 對 executable bit 的假設不一致 | 依實際啟動語意同步 launcher、validator 與傳遞回歸 | 只補 `chmod` 或放寬所有可執行檔檢查 |
| producer 成功但 aggregate 失敗 | 讀 aggregate 的第一個真實失敗並核對 artifact 傳遞 | 用 producer PASS 取代 aggregate 結果 |
| catalog bytes 未變，只修改 launcher | 保留原內容核准，覆核程式差異與直接影響 | 每個 candidate SHA 都重開 catalog 全量核准 |
| harness 需要乾淨 HEAD | 只有在當次 commit／push 授權成立時固定候選 | 測舊 HEAD 冒充 dirty 工作樹，或自行取得提交權 |
| reviewer 暫不可用 | 保留待覆核狀態與固定範圍 | 自審冒充獨立覆核或反覆重啟 reviewer |
| 負向案例被前置錯誤遮蔽 | 先建立合法對照，再只注入指定差異核對因果 | 只改 expected message 或補假的 report |
| native 隔離條件不足 | 精確記錄缺少的 process-tree／VM／防火牆證據 | 用 Linux container 宣稱 Windows／macOS native 通過 |

這些案例說明診斷方法，不是可直接套用的產品要求或操作授權。涉及特定 phase 時，仍只讀已採用
計劃的當期章節、相關 catalog 義務及測試指南的對應命令。

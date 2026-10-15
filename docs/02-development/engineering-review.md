# 工程審查指南

**文件說明**：定義 Tickets Hunter 程式碼與架構變更的審查重點、嚴重度與交付格式。

**最後更新**：2026-10-15

## 1. 審查目標

工程審查的首要工作是找出會造成錯誤選票、重複送出、無限刷新、敏感資料洩漏或無法回復的具體問題。
風格與抽象層級只有在影響正確性、可讀性或後續維護時才是 finding。

審查採 findings-first：先依嚴重度列出可重現問題，再提供整體摘要與測試缺口。沒有 finding 時也要明確說明剩餘風險。

## 2. 嚴重度

| 等級 | 定義 | 例子 |
|------|------|------|
| P0 | 會造成嚴重法律、安全或不可逆損害，必須立即停止 | 自動付款、洩漏 Cookie、任意程式執行 |
| P1 | 常見流程會錯誤、重複執行或卡死，合併前必須修正 | 選錯區域、重複送單、彈窗後永久停住、刷新風暴 |
| P2 | 特定條件下的正確性或維護性問題，應在本次或明確後續處理 | 多分頁狀態互相污染、測試假綠、設定遷移不完整 |
| P3 | 低風險但具體可改善的品質問題 | 誤導命名、過時文件、重複但未造成行為分歧的程式碼 |

finding 必須包含：位置、觸發條件、實際後果及建議修正方向。不要只寫「可能有問題」或泛泛而談最佳實務。

## 3. 必查範圍

### 3.1 需求與 12 階段語意

- 修改是否對應 Issue、核准計劃與明確驗收條件。
- 是否維持 12 階段順序，或清楚說明跨階段機制。
- 新功能是否只改必要階段，沒有順便改變其他平台的行為。
- 到達 checkout 後是否只通知，不包含自動付款。

### 3.2 平台與依賴邊界

- 平台是否保持在 `src/platforms/`，沒有反向匯入或跨平台 import。
- 共用邏輯是否真的跨平台；平台 selector、URL 與業務規則不得塞進 `util.py`。
- 家族平台共用必須基於已驗證的 page contract，不能只因 UI 相似就共用實作。
- 新 module/package 是否同步處理入口路由、設定與 PyInstaller hidden imports。

### 3.3 URL、頁面與輸入安全

- URL host、path、query 與頁型是否明確驗證；未知頁面是否 fail closed。
- 重新導向後是否重新判斷 host 與頁型。
- fast navigation 是否先建立 pending state，僅接受允許的 structured action，並在目的頁核對 route/query 與 hidden identifiers；`SendA` 等需原生處理的 action 不得直達。
- DOM 元素是否有穩定身分；同名、空文字、重複 id 與 re-render 是否安全。
- 注入 JavaScript 是否使用 IIFE 與 `json.dumps()`，且不污染 `window`。
- 外部回應、設定與 DOM 資料是否避免動態求值及 shell 注入。

### 3.4 選擇、填值與送出

- 關鍵字包含、完全符合、AND、OR、排除與 fallback 語意是否維持。
- stable identity 與 volatile availability 是否分開；table row、map title、action status 三個來源必須各自 quantity-aware，不能要求數值相等，也不能缺一仍選取。
- observe 與 commit 之間是否重新確認 URL、document、候選身分與可售狀態。
- 票數、座位與 checkbox 寫入後是否 audit；submit 的同一原子 commit 是否再次核對 payload，audit 失敗是否禁止送出。
- 50 ms 重複 tick 是否仍只產生一次 click、submit 或通知。

### 3.5 狀態生命週期

- 狀態是否依分頁／document 隔離，導頁、重新整理、登入切換後是否正確失效。
- purchase identity 是否涵蓋 tenant、session/account、performance、group 及 price-area/mode，而非直接使用不穩定的完整 URL。
- document 改變時是否先 reduce／attest fast navigation、submit awaiting、conflict 與 reload probe 等跨 document transition，再清除 source-document state。
- `pending`、`committed`、`failed` 是否有明確轉換；失敗不應被誤標為成功。
- CAPTCHA、送出、dialog 與刷新狀態是否可恢復，不會殘留到下一輪。
- 熱重載設定後，哪些狀態保留、哪些重設是否有定義。

### 3.6 彈窗與錯誤恢復

- 彈窗是否先讀取可驗證文字／角色，再分類；未知彈窗不得自動當成座位衝突。
- 成功加入購物車、座位衝突、超出額度、登入失效、CAPTCHA 錯誤與未知錯誤是否有不同處理。
- submit 前是否二次檢查 dialog；click 後是否進入 awaiting-result，容許延遲 dialog／導頁而不重送。
- committer 是否只回報 dispatched；awaiting-result 是否有明確期限，無導頁、已知 dialog 或可驗證 evidence 時是否進入 sticky unknown terminal，而非永久等待或自動重送。
- blocker 是否在 dismiss 前保存；dismiss 後是否驗證彈窗真的關閉，失敗時是否仍禁止其他副作用。
- recovery 是否依 reserved seat、general quantity、server-assigned quantity 清除正確狀態，並等待新的 document 才再次動作。

### 3.7 刷新與限流

- 所有刷新是否經過同一協調器，遵守使用者設定與跨程序節流。
- 429、一般缺票與未知網路錯誤是否分開；單一分頁的未知狀態不能誤觸發全域 429。
- 重新整理是否具備 reservation／commit 邊界，避免多個 tick 或程序同時刷新。
- pause 是否釋放 reservation／probe lease但保留 recovery intent；resume 是否仍遵守剩餘間隔與 cooldown。
- same-URL reload 是否通知外層重新執行 Cloudflare／document gate；coordinator disabled 時是否仍只由 family refresh controller 刷新。
- 測試或 fallback 不得繞過設定間隔。

### 3.8 登入、隱私與記錄

- 登入是否使用使用者既有合法 Cookie／profile，不把秘密複製到設定以外的位置。
- auth 狀態是否依站點與 session 判斷，不能因舊狀態誤認為已登入。
- debug、例外與測試 fixture 是否已脫敏。
- Issue、PR、截圖與測試記錄是否可能暴露帳號或訂單資訊。

### 3.9 測試可信度

- 測試是否驗證行為，而不只是檢查 production script 字串包含某個 marker。
- fake 是否依操作語意回應，並驗證呼叫順序、參數與副作用次數。
- 模組 `_state`、時間、亂數、檔案與網路是否隔離。
- DOM 改動是否至少有脫敏 fixture 或真實瀏覽器煙霧測試佐證。
- 測試數量與通過宣稱是否可由命令輸出核對。

### 3.10 文件與發行

- 設定名稱、預設值、遷移與 UI 是否同步。
- 架構、測試與使用者文件是否描述目前行為，而非預定行為。
- 新 import、資料檔或 package 是否能被 PyInstaller 收錄。
- Windows cp950 下 `.py` 是否沒有 emoji 或不相容輸出。

## 4. 建議審查順序

1. 讀 Issue／計劃與驗收條件。
2. 檢查 diff 範圍及既有未提交變更。
3. 從公開入口追蹤到副作用，確認狀態與錯誤路徑。
4. 對照測試，找出只有實作沒有覆蓋、或只有字串 fake 的區域。
5. 執行與風險相稱的驗證；命令見 [測試執行指南](testing_execution_guide.md)。
6. 依 P0 → P3 輸出 findings，最後才寫摘要與剩餘風險。

## 5. 審查輸出格式

```text
Findings

- [P1] 短標題 — path/to/file.py，函式名稱
  觸發條件、目前行為、實際後果，以及最小修正方向。

Open questions / assumptions

- 尚未取得哪一種頁面或行為證據。

Validation

- 實際執行的命令與結果。
- 未執行的驗證及原因。

Summary

- 變更目的與整體判斷；沒有 finding 時，明確寫「未發現阻擋問題」。
```

行號可用於當次 inline review；長期 Issue 與設計文件請以檔案加函式／符號名稱描述，避免日後失效。

## 6. 重構專用門檻

大規模重構除一般審查外，還必須確認：

- 有正式計劃、階段界線、每階段驗收與回滾方式。
- characterization tests 先於行為搬移。
- 舊、新實作不會同時執行相同副作用；需要比較時只允許 shadow decision。
- 相容 facade、設定遷移與 legacy 移除有明確版本週期。
- 每一階段都能獨立合併、部署與回退，不能要求一次完成全部重寫才可運作。
- 最後移除 legacy 前，至少完成計劃中指定的實機站點與核心流程驗證。

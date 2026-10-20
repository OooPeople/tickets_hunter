# 開發文件導航

**文件說明**：Tickets Hunter 開發規範、架構、測試與設計提案的入口。

**最後更新**：2026-10-17

## 開始與按需閱讀

一般工作先讀根目錄 [`AGENTS.md`](../../AGENTS.md)，核對 repository、授權、現有差異與可觀察完成條件；
不要把下列文件串成每次都要全文閱讀的前置鏈。

| 觸發條件 | 查閱內容 |
|---|---|
| 準備公開 PR／貢獻 | [CONTRIBUTING.md](../../CONTRIBUTING.md) 的 Issue、branch、commit 與 PR 章節 |
| 修改跨模組依賴、公開核心接口或新增平台 | [程式碼邊界](code-boundaries.md) 的相關責任與 DAG |
| 修改平台頁面流程或跨階段語意 | [12 階段標準](ticket_automation_standard.md) 與受影響機制章節 |
| 定位既有入口或 caller | [結構索引](structure.md)，並以實際程式碼為準 |
| 執行／修改不熟悉的 gate、runner、隔離或 evidence 接點 | [測試執行指南](testing_execution_guide.md) 的相關命令與契約 |
| 需要 finding 嚴重度或審查輸出格式 | [工程審查指南](engineering-review.md) |
| 同根因反覆無進展或長任務中斷 | [有限診斷與恢復案例](autonomous-execution.md) 的相關案例 |
| 開始／切換正式重構階段 | 已採用計劃的當期章節、直接依賴與 required catalog 義務 |

### 新增平台或大型重構

1. 調查是否存在同引擎的家族平台，而不只比較畫面外觀。
2. 在正式文件定義 scope、依賴、相容、階段、驗收及回復方式。
3. 維護者核准計劃後才依當次實作授權修改 production code。
4. 依計劃與 catalog 的階段責任驗證、覆核，不用索引自行新增 gate。

[uTiki 低延遲重構 v1.6](UTIKI_ROOT_REFACTOR_PLAN_v1.6.md) 已由維護者透過
[v1.6 採用紀錄](utiki-refactor/adoption-v1.6.md) 採用；計劃原文保留交付時的
`PROPOSED / REVIEW REQUIRED`，以維持被採用位元組與 digest 不變。v1.6 required catalog
已由 [核准紀錄](utiki-refactor/catalog-approval-v1.6.md) 固定；舊 v1.5 證據保持歷史狀態，
不能當成新版 gate 已通過。
[v1.6 需求變更摘要](V1.6_CHANGE_SUMMARY.md) 只供快速比對；
[先前的 uTiki2 計劃](utiki-refactor-plan.md) 只保留歷史脈絡。

## 文件責任

| 文件 | 責任 | 使用時機 |
|------|------|----------|
| [code-boundaries.md](code-boundaries.md) | 模組職責、依賴 DAG、命名與禁止模式 | 修改邊界或依賴時 |
| [ticket_automation_standard.md](ticket_automation_standard.md) | 12 階段及功能語意的唯一來源 | 平台流程設計與審查 |
| [testing_execution_guide.md](testing_execution_guide.md) | 現行測試命令、測試分層與結果陳述 | 修改後驗證 |
| [engineering-review.md](engineering-review.md) | 嚴重度、審查清單與輸出格式 | 需要 finding 判定或高風險覆核時 |
| [autonomous-execution.md](autonomous-execution.md) | 有限診斷、最小重現與恢復案例 | `AGENTS.md` 不足以裁決診斷細節時 |
| [structure.md](structure.md) | 現有程式結構與函式索引 | 定位實作；可能落後時以程式碼為準 |
| [development_guide.md](development_guide.md) | 既有 WebDriver、風格與實作範例 | 需要對應技術範例時 |
| [coding_templates.md](coding_templates.md) | 現有寫法範例 | 需要具體範本時 |
| [logic_flowcharts.md](logic_flowcharts.md) | 常見判斷流程 | 理解既有邏輯時 |
| [ticket_seat_selection_algorithm.md](ticket_seat_selection_algorithm.md) | 座位選擇演算法 | 手動劃位相關工作 |
| [sound_notification_system.md](sound_notification_system.md) | 通知音效設計 | 通知相關工作 |
| [UTIKI_ROOT_REFACTOR_PLAN_v1.6.md](UTIKI_ROOT_REFACTOR_PLAN_v1.6.md) | 已採用的 v1.6 完整計劃；新 catalog 待接入 | Issue #17 維護者／審查者 |
| [V1.6_CHANGE_SUMMARY.md](V1.6_CHANGE_SUMMARY.md) | v1.6 已確認需求與 v1.5 差異摘要 | 快速比對範圍時 |
| [SCOPE_CHANGE_MATRIX_v1.6.json](SCOPE_CHANGE_MATRIX_v1.6.json) | 非執行型的 v1.6 scope 處置索引；不是 catalog 或 manifest | 投影新 catalog 前 |
| [utiki-refactor/adoption-v1.6.md](utiki-refactor/adoption-v1.6.md) | v1.6 計劃採用、版本固定與授權邊界 | 判斷現行產品規格時 |
| [utiki-refactor/adoption.md](utiki-refactor/adoption.md) | v1.5 採用與現行 catalog 核准脈絡（歷史固定紀錄） | 判斷正式接入狀態時 |
| [utiki-refactor-plan.md](utiki-refactor-plan.md) | 先前 uTiki2 家族重構計劃 | 查閱已完成階段的歷史脈絡 |

## 依工作類型導航

### 瀏覽器 DOM、點擊與填值

- [zendriver API 指南](../06-api-reference/zendriver_api_guide.md)
- [CDP 協定參考](../06-api-reference/cdp_protocol_reference.md)
- [Shadow DOM 指南](../06-api-reference/shadow_dom_pierce_guide.md)

### 跨階段機制

- [刷新等待機制](../03-mechanisms/13-active-polling-pattern.md)
- [Hot Reload](../03-mechanisms/14-hot-reload.md)
- [Cloudflare Turnstile](../03-mechanisms/15-cloudflare-turnstile.md)
- [Yii2 CAPTCHA hash](../03-mechanisms/16-yii2-captcha-hash.md)
- [多實例隔離](../03-mechanisms/17-multi-instance.md)
- [通用 OCR 模型](../03-mechanisms/18-universal-ocr-model.md)
- [購買資格驗證](../03-mechanisms/19-purchase-qualification.md)

### 打包與發行

只在該平台的採用契約與當次授權包含產品打包／發行時使用。現行 uTiki v1.6 個人原始碼需求不包含產品打包、frozen 成品或安裝包。

- [PyInstaller 打包指南](../07-deployment/pyinstaller_packaging_guide.md)
- [本地打包快速開始](../../build_scripts/QUICK_START.md)

## 單一真實來源

- 代理起手規則只維護在根目錄 [AGENTS.md](../../AGENTS.md)。
- Issue／branch／commit／PR 流程只維護在 [CONTRIBUTING.md](../../CONTRIBUTING.md)。
- 已採用計劃與 required catalog 維護產品行為、必要案例與階段門檻；索引不替它們核准變更。
- 12 階段定義只維護在 [ticket_automation_standard.md](ticket_automation_standard.md)。
- 模組邊界只維護在 [code-boundaries.md](code-boundaries.md)。
- 可執行命令與驗證分級只維護在 [testing_execution_guide.md](testing_execution_guide.md)。
- `autonomous-execution.md` 只保存按需診斷與恢復案例，不另維護日常工作節奏。
- 尚未核准的設計放在標示狀態的計劃文件，不提前寫入現況文件。

若索引、範本或歷史文件和 production code 不一致，先記錄差異並確認真實行為；不要為了符合過時文件而修改程式。

## 文件維護規則

- 更新行為時同步更新負責該語意的文件；其他文件只連結，不複製完整規則。
- 架構提案必須標示「提案／已核准／執行中／完成／撤回」。
- 引用長期位置時使用檔案與函式／符號名稱；當次 review 才使用易變動的行號。
- 命令必須在目前 repository 可執行；尚未導入的工具不得寫成現行必要步驟。
- 文件中的預定行為要明確標示，不能讓讀者誤以為已經實作。

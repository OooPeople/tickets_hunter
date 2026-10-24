# 開發文件導航

**文件說明**：Tickets Hunter 開發規範、架構、測試與設計提案的入口。

**最後更新**：2026-10-24

## 依任務找主文件

每次協作與 Git 授權先看 [AGENTS.md](../../AGENTS.md)；向 upstream 提公開 PR 的流程見 [CONTRIBUTING.md](../../CONTRIBUTING.md)，本機工作不以公開 PR 的 Issue 步驟為前置條件。修改程式時從 [結構索引](structure.md) 定位目前入口，跨模組責任看 [程式碼邊界](code-boundaries.md)，驗證命令看 [測試指南](testing_execution_guide.md)，審查格式看 [工程審查指南](engineering-review.md)。

uTiki 目前能力與交接見 [STATUS](utiki-refactor/STATUS.md)，正式程式結構與責任見[結構索引](structure.md)和[程式碼邊界](code-boundaries.md)。[已完成的重構計畫](archive/utiki-refactor/PLAN.md)與[更早的提案](archive/utiki-refactor/utiki-refactor-plan.md)僅供歷史追溯；十二階段只作通用功能分類，不自動增加購票功能或 gate。

## 文件責任

| 文件 | 責任 | 使用時機 |
|------|------|----------|
| [code-boundaries.md](code-boundaries.md) | 目前模組職責、依賴 DAG 與禁止模式 | 修改相關邊界時 |
| [ticket_automation_standard.md](ticket_automation_standard.md) | 跨平台 12 階段功能分類 | 適用平台的流程設計與審查 |
| [testing_execution_guide.md](testing_execution_guide.md) | 現行測試命令、測試分層與結果陳述 | 修改後驗證 |
| [engineering-review.md](engineering-review.md) | 嚴重度、審查清單與輸出格式 | 自審與第二人審查 |
| [structure.md](structure.md) | 現有程式結構與函式索引 | 定位實作；可能落後時以程式碼為準 |
| [development_guide.md](development_guide.md) | 一般開發原則與檢查清單 | 日常開發參考 |
| [coding_templates.md](coding_templates.md) | 現有寫法範例 | 需要具體範本時 |
| [logic_flowcharts.md](logic_flowcharts.md) | 常見判斷流程 | 理解既有邏輯時 |
| [ticket_seat_selection_algorithm.md](ticket_seat_selection_algorithm.md) | 座位選擇演算法 | 手動劃位相關工作 |
| [sound_notification_system.md](sound_notification_system.md) | 通知音效設計 | 通知相關工作 |
| [utiki-refactor/STATUS.md](utiki-refactor/STATUS.md) | uTiki 現版能力、驗證結果與待處理事項 | 接手相關工作 |
| [archive/utiki-refactor/PLAN.md](archive/utiki-refactor/PLAN.md) | 已完成重構的施工紀錄 | 查閱歷史脈絡 |
| [archive/utiki-refactor/BATCH6_WORKPLAN.md](archive/utiki-refactor/BATCH6_WORKPLAN.md) | 批次 6 當時執行計畫 | 查閱歷史脈絡 |
| [archive/utiki-refactor/utiki-refactor-plan.md](archive/utiki-refactor/utiki-refactor-plan.md) | 更早的歷史提案 | 查閱歷史脈絡 |

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

以下是全專案的通用文件。已完成的 uTiki 重構當時僅交付原始碼；後續工作是否打包，依當次需求決定，不由歷史計畫設定永久門檻。

- [PyInstaller 打包指南](../07-deployment/pyinstaller_packaging_guide.md)
- [本地打包快速開始](../../build_scripts/QUICK_START.md)

## 單一真實來源

- 代理起手規則只維護在根目錄 [AGENTS.md](../../AGENTS.md)。
- 協作與 commit／push 授權只維護在 [AGENTS.md](../../AGENTS.md)；upstream PR 流程見 [CONTRIBUTING.md](../../CONTRIBUTING.md)。
- 12 階段定義只維護在 [ticket_automation_standard.md](ticket_automation_standard.md)。
- 模組邊界只維護在 [code-boundaries.md](code-boundaries.md)。
- 可執行命令與驗證分級只維護在 [testing_execution_guide.md](testing_execution_guide.md)。
- 目標設計放在對應計畫，現況文件不提前宣告未接線模組已實作。

若索引、範本或歷史文件和 production code 不一致，先記錄差異並確認真實行為；不要為了符合過時文件而修改程式。

## 文件維護規則

- 更新行為時同步更新負責該語意的文件；其他文件只連結，不複製完整規則。
- 架構計畫與實際進度分開記錄，不以歷史計畫的狀態代替目前證據。
- 引用長期位置時使用檔案與函式／符號名稱；當次 review 才使用易變動的行號。
- 命令必須在目前 repository 可執行；尚未導入的工具不得寫成現行必要步驟。
- 文件中的預定行為要明確標示，不能讓讀者誤以為已經實作。

# 開發文件導航

**文件說明**：Tickets Hunter 開發規範、架構、測試與設計提案的入口。

**最後更新**：2026-10-15

## 先從哪裡開始

### 修復 Bug 或調整既有功能

1. 依 [CONTRIBUTING.md](../../CONTRIBUTING.md) 建立／確認 Issue。
2. 閱讀 [程式碼邊界](code-boundaries.md)，確認修改應放在哪個模組。
3. 從 [結構索引](structure.md) 找到公開入口，再追蹤實際呼叫路徑。
4. 涉及搶票流程時對照 [12 階段標準](ticket_automation_standard.md)。
5. 依 [測試執行指南](testing_execution_guide.md) 驗證。
6. 依 [工程審查指南](engineering-review.md) findings-first 自審。

### 新增平台或大型重構

1. 先完成上述步驟。
2. 調查是否存在同引擎的家族平台，而不只比較畫面外觀。
3. 在正式文件定義 scope、依賴、相容、階段、驗收及回退。
4. 維護者核准計劃後才開始 production code。
5. 每個階段獨立驗證與審查，不把多個高風險行為綁成一次切換。

本次 uTiki 等價重構依 [正式計畫](utiki-refactor/PLAN.md) 施工；實際進度見 [STATUS](utiki-refactor/STATUS.md)。[舊計畫](utiki-refactor-plan.md) 僅供歷史參考。

## 文件責任

| 文件 | 責任 | 使用時機 |
|------|------|----------|
| [code-boundaries.md](code-boundaries.md) | 模組職責、依賴 DAG、命名與禁止模式 | 每次程式碼修改前 |
| [ticket_automation_standard.md](ticket_automation_standard.md) | 12 階段及功能語意的唯一來源 | 平台流程設計與審查 |
| [testing_execution_guide.md](testing_execution_guide.md) | 現行測試命令、測試分層與結果陳述 | 修改後驗證 |
| [engineering-review.md](engineering-review.md) | 嚴重度、審查清單與輸出格式 | 自審與第二人審查 |
| [structure.md](structure.md) | 現有程式結構與函式索引 | 定位實作；可能落後時以程式碼為準 |
| [development_guide.md](development_guide.md) | 一般開發原則與檢查清單 | 日常開發參考 |
| [coding_templates.md](coding_templates.md) | 現有寫法範例 | 需要具體範本時 |
| [logic_flowcharts.md](logic_flowcharts.md) | 常見判斷流程 | 理解既有邏輯時 |
| [ticket_seat_selection_algorithm.md](ticket_seat_selection_algorithm.md) | 座位選擇演算法 | 手動劃位相關工作 |
| [sound_notification_system.md](sound_notification_system.md) | 通知音效設計 | 通知相關工作 |
| [utiki-refactor/PLAN.md](utiki-refactor/PLAN.md) | 本次 uTiki 等價重構的目標與施工順序 | 本次施工與審查 |
| [utiki-refactor/STATUS.md](utiki-refactor/STATUS.md) | 本次實際基準、進度與阻礙 | 查核目前狀態 |
| [utiki-refactor-plan.md](utiki-refactor-plan.md) | 已取代的歷史計畫 | 查閱歷史脈絡 |

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

- [PyInstaller 打包指南](../07-deployment/pyinstaller_packaging_guide.md)
- [本地打包快速開始](../../build_scripts/QUICK_START.md)

## 單一真實來源

- 代理起手規則只維護在根目錄 [AGENTS.md](../../AGENTS.md)。
- Issue／branch／commit／PR 流程只維護在 [CONTRIBUTING.md](../../CONTRIBUTING.md)。
- 12 階段定義只維護在 [ticket_automation_standard.md](ticket_automation_standard.md)。
- 模組邊界只維護在 [code-boundaries.md](code-boundaries.md)。
- 可執行命令與驗證分級只維護在 [testing_execution_guide.md](testing_execution_guide.md)。
- 尚未核准的設計放在標示狀態的計劃文件，不提前寫入現況文件。

若索引、範本或歷史文件和 production code 不一致，先記錄差異並確認真實行為；不要為了符合過時文件而修改程式。

## 文件維護規則

- 更新行為時同步更新負責該語意的文件；其他文件只連結，不複製完整規則。
- 架構提案必須標示「提案／已核准／執行中／完成／撤回」。
- 引用長期位置時使用檔案與函式／符號名稱；當次 review 才使用易變動的行號。
- 命令必須在目前 repository 可執行；尚未導入的工具不得寫成現行必要步驟。
- 文件中的預定行為要明確標示，不能讓讀者誤以為已經實作。

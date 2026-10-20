**文件說明**：Tickets Hunter 公開技術文件導覽與索引

---

# Tickets Hunter 文件導覽

> 多平台搶票自動化系統 - 技術文件索引

## 快速導航

**新手入門** → [01-getting-started/](01-getting-started/)
**開發新功能** → [02-development/](02-development/)（依任務選章節）
**API 行為** → [06-api-reference/](06-api-reference/)（實際使用對應能力時）
**打包部署** → [07-deployment/](07-deployment/)（交付範圍包含產品打包時）

---

## 文件結構總覽

```
docs/
├── README.md                                          <- 文件導覽索引
│
├── 01-getting-started/                                <- 新手入門
│   ├── README.md                                      <- 入門索引
│   ├── project_overview.md                            <- 專案架構與系統概覽
│   └── setup.md                                       <- 安裝與環境設定
│
├── 02-development/                                    <- 開發指南
│   ├── code-boundaries.md                             <- 程式碼邊界（模組職責、依賴 DAG、命名）
│   ├── engineering-review.md                          <- 工程審查嚴重度、範圍與輸出格式
│   ├── ticket_automation_standard.md                  <- 標準功能定義 (12 階段)
│   ├── utiki-refactor/PLAN.md                         <- uTiki 重構計畫
│   ├── utiki-refactor/STATUS.md                       <- uTiki 目前進度
│   ├── structure.md                                   <- 程式架構與函數索引
│   ├── development_guide.md                           <- 開發規範與檢查清單
│   ├── coding_templates.md                            <- 程式寫法範本
│   ├── logic_flowcharts.md                            <- 邏輯判斷範本
│   ├── sound_notification_system.md                   <- 音效通知系統
│   ├── ticket_seat_selection_algorithm.md             <- 座位選擇演算法
│   └── testing_execution_guide.md                     <- 測試執行指南
│
├── 03-mechanisms/                                     <- 12 階段機制文件
│   ├── README.md                                      <- 機制文件索引
│   ├── 01-environment-init.md                         <- Stage 1: 環境初始化
│   ├── 02-authentication.md                           <- Stage 2: 身份認證
│   ├── 03-page-monitoring.md                          <- Stage 3: 頁面監控
│   ├── 04-date-selection.md                           <- Stage 4: 日期選擇
│   ├── 05-area-selection.md                           <- Stage 5: 區域選擇
│   ├── 06-ticket-count.md                             <- Stage 6: 票數設定
│   ├── 07-captcha-handling.md                         <- Stage 7: 驗證碼處理
│   ├── 08-form-filling.md                             <- Stage 8: 表單填寫
│   ├── 09-terms-agreement.md                          <- Stage 9: 條款同意
│   ├── 10-order-submit.md                             <- Stage 10: 訂單送出
│   ├── 11-queue-payment.md                            <- Stage 11: 排隊付款
│   ├── 12-error-handling.md                           <- Stage 12: 錯誤處理
│   ├── 13-active-polling-pattern.md                   <- 跨階段: 刷新等待機制
│   ├── 14-hot-reload.md                               <- 跨階段: 即時設定修改
│   ├── 15-cloudflare-turnstile.md                     <- 跨階段: Turnstile 偵測
│   ├── 16-yii2-captcha-hash.md                        <- 跨階段: Yii2 驗證碼雜湊
│   ├── 17-multi-instance.md                           <- 跨階段: 多開實例隔離
│   ├── 18-universal-ocr-model.md                      <- 跨階段: 通用 OCR 模型選擇
│   └── 19-purchase-qualification.md                   <- 跨階段: 購買資格驗證
│
├── 04-implementation/                                 <- 平台實作參考
│   ├── README.md                                      <- 平台參考索引
│   ├── debug-logger-spec.md                           <- Debug Logger 規格書
│   └── platform-examples/                             <- 各平台參考
│
├── 06-api-reference/                                  <- API 參考文件
│   ├── README.md                                      <- API 文件索引
│   ├── zendriver_api_guide.md                         <- Zendriver API 指南（主要參考）
│   ├── cdp_protocol_reference.md                      <- CDP 完整參考
│   ├── shadow_dom_pierce_guide.md                     <- Shadow DOM 穿透指南
│   ├── ddddocr_api_guide.md                           <- 驗證碼識別 API
│   ├── nodriver_api_guide.md                          <- NoDriver API 指南（已棄用）
│   ├── nodriver_selector_analysis.md                  <- NoDriver Selector 分析（已棄用）
│   └── chrome-nodriver-compatibility-research.md      <- Chrome 相容性研究（歷史參考）
│
└── 07-deployment/                                     <- 部署打包
    ├── README.md                                      <- 部署索引
    └── pyinstaller_packaging_guide.md                 <- PyInstaller 打包指南
```

---

## 常見使用情境

### 情境 1：我想開發新平台支援

先依根目錄 [`AGENTS.md`](../AGENTS.md) 核對範圍與授權，再按實際任務選取資料：

1. 涉及平台頁面流程時，閱讀 [ticket_automation_standard.md](02-development/ticket_automation_standard.md) 的相關階段。
2. 需要定位既有入口時，檢視 [structure.md](02-development/structure.md)，再以實際 caller 為準。
3. 新用或不確定瀏覽器能力時，檢視對應的 [API 指南](06-api-reference/)：
   - **Zendriver (主要參考)** → [zendriver_api_guide.md](06-api-reference/zendriver_api_guide.md)
   - **CDP 協定 (深入)** → [cdp_protocol_reference.md](06-api-reference/cdp_protocol_reference.md)
4. 需要具體既有寫法時，再參考 [coding_templates.md](02-development/coding_templates.md)。
5. 執行不熟悉的 gate 或 runner 時，查 [testing_execution_guide.md](02-development/testing_execution_guide.md) 的對應命令；需要嚴重度與審查清單時，查 [engineering-review.md](02-development/engineering-review.md)。

### 情境 2：我想了解專案架構

1. **總覽**：從 [project_overview.md](01-getting-started/project_overview.md) 開始
2. **程式結構**：檢視 [structure.md](02-development/structure.md) 了解函式索引
3. **標準流程**：閱讀 [ticket_automation_standard.md](02-development/ticket_automation_standard.md) 了解 12 階段標準
4. **座位選擇邏輯**：檢視 [ticket_seat_selection_algorithm.md](02-development/ticket_seat_selection_algorithm.md)

### 情境 3：我要打包部署

以下是其他平台或另有授權之產品交付的既有指南；目前 uTiki 個人原始碼需求不包含產品打包、frozen 成品或安裝包。

- [PyInstaller 打包指南](07-deployment/pyinstaller_packaging_guide.md) - 完整打包流程
- [本地測試打包](../build_scripts/QUICK_START.md) - 5 分鐘快速開始

---

## 重點文件速查

| 文件 | 用途 | 適用對象 |
|------|------|----------|
| [ticket_automation_standard.md](02-development/ticket_automation_standard.md) | 標準功能定義 (12 階段) | 開發者 |
| [code-boundaries.md](02-development/code-boundaries.md) | 模組責任與依賴邊界 | 開發者／審查者 |
| [testing_execution_guide.md](02-development/testing_execution_guide.md) | 實際驗證命令與結果陳述 | 開發者／審查者 |
| [engineering-review.md](02-development/engineering-review.md) | findings-first 工程審查 | 審查者 |
| [utiki-refactor/PLAN.md](02-development/utiki-refactor/PLAN.md) | uTiki 目標與重構計畫 | uTiki 實作者 |
| [utiki-refactor/STATUS.md](02-development/utiki-refactor/STATUS.md) | uTiki 目前進度與下一步 | 跨電腦接續工作 |
| [structure.md](02-development/structure.md) | 程式架構索引與完成度評分 | 所有人 |
| [zendriver_api_guide.md](06-api-reference/zendriver_api_guide.md) | Zendriver API 指南（主要參考） | 開發者 |
| [cdp_protocol_reference.md](06-api-reference/cdp_protocol_reference.md) | CDP 協定完整參考 | Zendriver 開發者 |
| [nodriver_api_guide.md](06-api-reference/nodriver_api_guide.md) | NoDriver API 指南（已棄用） | 遷移前參考 |

---

## 文件分類說明

### 01-getting-started/ - 新手入門
適合第一次接觸專案的開發者或使用者。包含專案概覽、環境設定。

### 02-development/ - 開發指南
包含標準流程定義、程式架構、技術操作、審查清單與程式範本；依任務觸發條件閱讀相關章節，不是每次工作前的全文清單。

### 03-mechanisms/ - 12 階段機制文件
詳細的票券自動化流程文件。每個階段涵蓋流程說明、程式碼範例、平台特定考量。

### 04-implementation/ - 平台實作參考
針對各個售票平台的具體實作參考文件。包含特定平台的選擇器、API 用法、常見問題。

### 06-api-reference/ - API 參考文件
各 WebDriver 引擎的 API 使用指南。

### 07-deployment/ - 部署打包
其他平台或另有授權之產品交付的部署與打包指南。包含 PyInstaller 流程與依賴管理；不代表目前 uTiki 要求產品打包。

---

## 文件維護

根目錄的 [AGENTS.md](../AGENTS.md) 保存日常工作規則；[開發文件導航](02-development/README.md)
說明各主文件的職責。修改行為或驗證命令時，只更新負責該資訊的文件。

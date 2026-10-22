# 開發文件導航

工作規則見 [AGENTS.md](../../AGENTS.md)。同一資訊只維護一份主來源；其他文件提供短句與連結。

| 主文件 | 職責 | 不放入 |
|---|---|---|
| [uTiki 計畫](utiki-refactor/PLAN.md) | 穩定目標、架構責任、施工順序與完成條件 | 最近提交、目前測試結果 |
| [uTiki 進度](utiki-refactor/STATUS.md) | 目前接線狀態、下一步、阻礙與最近驗證 | 完整架構規格、逐次交接紀錄 |
| [測試指南](testing_execution_guide.md) | 驗證層級、命令、環境條件與結果說法 | 進度、單次測試報告 |
| [程式碼邊界](code-boundaries.md) | 穩定模組職責、依賴方向與禁止模式 | 任務進度、舊計畫版本差異 |
| [結構索引](structure.md) | 目前模組與入口的位置；以程式碼為準 | 目標設計與驗收宣告 |
| [工程審查指南](engineering-review.md) | 審查範圍、嚴重度與輸出格式 | 產品規格、最近驗證 |
| [12 階段標準](ticket_automation_standard.md) | 跨平台功能分類與傳統平台入口參考 | uTiki 新 owner 的架構、批次驗收 |

平台機制見 [機制章節](../03-mechanisms/)；瀏覽器能力見 [API 指南](../06-api-reference/)；PR 與提交見 [CONTRIBUTING.md](../../CONTRIBUTING.md)。原作者的範本及其他平台文件按需要查閱。

uTiki 新 owner 以正式 PLAN 的單 Run controller、typed effective policy、操作回執與唯一刷新接縫為準。[開發指南](development_guide.md)及[程式碼範本](coding_templates.md)中的舊函式骨架、模組 `_state`、通用 retry、逐函式 pause 返回與直接 reload，仍可供適用的傳統平台參考，不是 uTiki 的實作或完成標準。

舊 v1.6 catalog、核心接合盤點及 P1／P2 gate 已依正式計畫第 11.2 節在批次 6 從目前 source 退役；歷史基準與當時的驗證紀錄仍可由 Git 歷史查閱。目前可執行的替代命令只在[測試指南](testing_execution_guide.md)維護。

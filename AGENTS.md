# AGENTS.md

本檔只放每次協作需要先知道的規則與文件入口。穩定架構、當前進度和驗證命令各有主文件；`CLAUDE.md` 只是指向本檔的相容入口。

## 必守邊界

- 遵守 [法律聲明](LEGAL_NOTICE.md)：不得用於非法轉售、商業牟利、惡意囤票或台灣《文化創意產業發展法》涵蓋的票券；不得新增自動付款或規避平台風控的能力。付款由使用者手動完成，保留授權與著作權聲明。
- 不提交或輸出真實設定、密碼、Cookie、token、webhook、完整個資、browser profile 或 session dump。測試只用合成資料與專用 profile；未經使用者授權，不操作真售票網站或日常 profile。
- 不為了加速提高站點請求頻率、放寬 429 保護或省略送出前核對。提交結果未知時不得自動重送；停止、取消、逾時不代表遠端請求未發生。
- 保留其他平台、帳號資料與共用刷新資料的既有語意。uTiki 這輪交付範圍及刻意排除項目以 [重構計畫](docs/02-development/utiki-refactor/PLAN.md) 為準。

## 開工與完成

1. 先核對 branch、HEAD、未提交內容與實際 caller；保留使用者現有工作，不以 reset、clean 或整批 restore 覆蓋。
2. 修改功能時先界定使用者可觀察的完整行為，再按流程切片修改。主入口在 `src/nodriver_tixcraft.py`，平台邏輯在 `src/platforms/`；詳細依賴方向見 [程式碼邊界](docs/02-development/code-boundaries.md)。
3. 同一 tab 的購票與刷新只能有一個副作用 owner。填值須檢查結果；點擊、reload、submit 的指令返回不等於網站接受。只在需求和實際 caller 支持時新增抽象。
4. 依改動範圍選擇[驗證層級與命令](docs/02-development/testing_execution_guide.md)，回報實際執行結果與未驗證項目。開發中聚焦檢查；整合時收集當輪全部失敗，按根因批次修復、聚焦重驗，整批完成後再完整驗證。只有收集受阻才提早重跑全套。聚焦檢查、本機完整檢查、GitHub CI、真站手動驗收分開陳述。
5. 功能只有在涉及的設定、正式入口、執行、結果與必要測試接通後才可稱完成；新增檔案、測試數或歷史 PASS 都不能代替這條流程。高風險變更須檢查停止、重入、未知結果、資源清理和可回復性。
6. Commit 與推送一律由使用者決定。未經使用者明確下令，不得執行 `git commit` 或 `git push`；長時間工作、階段完成、驗證通過、文件整理與交接都不構成例外，也不得自行建立 checkpoint commit。使用者檢查後下令提交時，先核對暫存範圍，只提交本次工作，並使用 Conventional Commits；推送須另依當次指令，PR 依 [CONTRIBUTING.md](CONTRIBUTING.md) 關聯 Issue。

## 代理協作與審查時序

- 本專案的程式碼、測試、設定與文件只由主代理寫入。Subagent 僅做唯讀盤點、指定區域審查與方案辯論；不得修改檔案或執行會改變工作區、Git 狀態的命令。
- 實作前可先審方案；最終程式碼審查須等主代理完成該輪寫檔、受審變更範圍固定後才開始。審查後若修改受審內容，針對受影響部分重審；交付前確認沒有尚未結束的代理工作會改變審查對象。

## 文件入口與整理規則

| 需要查什麼 | 主文件 |
|---|---|
| uTiki 目前進度、下一步、最近驗證 | [STATUS.md](docs/02-development/utiki-refactor/STATUS.md) |
| uTiki 目標、責任、施工順序與完成條件 | [PLAN.md](docs/02-development/utiki-refactor/PLAN.md) |
| 模組邊界、驗證命令、審查方式 | [開發文件導航](docs/02-development/README.md) |
| 12 階段語意與瀏覽器 API | [12 階段標準](docs/02-development/ticket_automation_standard.md)、[Zendriver 指南](docs/06-api-reference/zendriver_api_guide.md) |
| 公開貢獻與提交規範 | [CONTRIBUTING.md](CONTRIBUTING.md)、[GIT_COMMIT_RULES.md](GIT_COMMIT_RULES.md) |

「整理文件」是刪除細碎、重複或容易過期的內容，釐清每份文件的責任，並把仍有效的事實放回主文件；不把近期修改補寫到每一份文件。進度更新 STATUS，穩定設計決策更新 PLAN，驗證命令更新測試指南。

# AGENTS.md

本檔是每次協作的規範與文件入口；`CLAUDE.md` 只指向本檔。穩定模組邊界、驗證命令和當前進度各由專責文件維護。

## 必守邊界

- 遵守 [法律聲明](LEGAL_NOTICE.md)：僅供教育研究；不得用於非法轉售、商業牟利、惡意囤票或台灣《文化創意產業發展法》涵蓋的票券。不得新增自動付款或規避平台風控的能力；付款由使用者手動完成，保留授權與著作權聲明。
- 不提交或輸出真實設定、密碼、Cookie、token、webhook、完整個資、browser profile 或 session dump。自動化測試可用合成或已脫敏資料；需要瀏覽器時使用專用／暫存 profile。未經使用者授權，不操作真售票網站或日常 profile。
- 不為加速提高站點請求頻率、放寬 429 保護或省略送出前核對。停止、取消、逾時及指令返回都不證明遠端請求未發生；結果未知時不得自動重送。
- 保留其他平台、帳號資料與共用刷新資料的既有語意。uTiki 重構已完成；目前版本依現行程式、測試、[模組邊界](docs/02-development/code-boundaries.md)、[結構索引](docs/02-development/structure.md)及使用者當次需求審查正確性。`944e934` 與原施工計畫只供歷史追溯，不限制後續改善。一般安全守則不自動增加購票資格或拒絕條件；新增能力或改變使用者行為須依具體需求與證據處理。

## Fork 與原作者更新

本 repository 是為自有職棒售票能力維護的 fork，會按需求取得並整合原作者更新。接收原作者更新與向原作者提交公開貢獻是不同方向；本 fork 不以建立 upstream Issue、等待維護者確認、提出 PR 或將自有修改合併回原作者倉庫為工作前提。只有使用者明確要求公開回貢時，才適用 [CONTRIBUTING.md](CONTRIBUTING.md) 的公開 PR 流程。

一般工作不自行抓取或整合上游。上游更新任務先區分「取得供審查」與「接入本分支」：fetch 不代表已整合，也不授權自動 merge、rebase 或選擇性移植；接入方式依當次需求及 Git 歷史決定，不固定使用某一種。接入前依[工程審查指南](docs/02-development/engineering-review.md)核對來源、雙邊差異、未提交工作與語意衝突，不用未審查的 `git pull` 跳過盤點，也不覆蓋使用者現有修改。可能產生 commit 的接入與後續 push 仍受下方授權規則約束。

## 開工與完成

1. 先核對 repository、branch、HEAD、未提交內容、適用的 AGENTS 與實際 caller；保留使用者現有工作，不以 reset、clean 或整批 restore 覆蓋。
2. 修改功能前界定使用者可觀察的完整行為，再按流程切片修改。主入口在 `src/nodriver_tixcraft.py`，平台邏輯在 `src/platforms/`；正式 caller 與依賴方向見[程式碼邊界](docs/02-development/code-boundaries.md)。[12 階段標準](docs/02-development/ticket_automation_standard.md) 只作通用功能分類。
3. 同一 tab 的購票與刷新各只能有一個副作用 owner。填值須核對結果；click、reload、submit 呼叫完成不等於網站接受。保留原有重入、未知結果、暫停與清理時點，只在需求和實際 caller 支持時新增抽象。
4. 依 [測試指南](docs/02-development/testing_execution_guide.md) 選擇驗證層級，回報實際命令、結果與未驗證項目；本機測試、browser fixture、GitHub CI、真站手動驗收分開陳述。功能須接通設定、正式入口、執行、結果及必要測試，才可稱完成。
5. Commit 與推送由使用者決定。未經使用者明確下令，不執行 `git commit` 或 `git push`，也不自行建立 checkpoint commit；獲准提交時先核對暫存範圍，只提交本次工作。Issue／PR 亦須使用者授權，關聯及提交規範見 [CONTRIBUTING.md](CONTRIBUTING.md) 與 [GIT_COMMIT_RULES.md](GIT_COMMIT_RULES.md)。

## 代理協作與審查

- 本專案的程式碼、測試、設定與文件只由主代理寫入。Subagent 僅做唯讀盤點、指定區域審查與方案辯論；不得修改檔案或執行會改變工作區、Git 狀態的命令。
- 最終審查在主代理完成該輪寫檔、受審範圍固定後進行；審查後如修改受審內容，針對受影響部分重審。

## 文件入口

| 需要查什麼 | 主文件 |
| --- | --- |
| uTiki 目前能力、驗證結果與交接 | [STATUS.md](docs/02-development/utiki-refactor/STATUS.md) |
| uTiki 模組責任與結構 | [程式碼邊界](docs/02-development/code-boundaries.md)、[結構索引](docs/02-development/structure.md) |
| 接收原作者更新的影響與衝突審查 | [工程審查指南](docs/02-development/engineering-review.md) |
| 已完成重構的歷史計畫 | [封存 PLAN](docs/02-development/archive/utiki-refactor/PLAN.md) |
| 模組邊界、結構索引與驗證命令 | [開發文件導航](docs/02-development/README.md) |
| 瀏覽器 API、公開貢獻與提交規範 | [Zendriver 指南](docs/06-api-reference/zendriver_api_guide.md)、[CONTRIBUTING.md](CONTRIBUTING.md)、[GIT_COMMIT_RULES.md](GIT_COMMIT_RULES.md) |

進度與未驗證項目更新 STATUS，現行模組責任更新程式碼邊界與結構索引，通用驗證命令更新測試指南。封存計畫保留歷史脈絡，不繼承其中待辦或 gate。

# AGENTS.md

本檔是每次協作的規範與文件入口；`CLAUDE.md` 只指向本檔。穩定模組邊界、驗證命令和當前進度各由專責文件維護。

## 必守邊界

- 遵守 [法律聲明](LEGAL_NOTICE.md)：僅供教育研究；不得用於非法轉售、商業牟利、惡意囤票或台灣《文化創意產業發展法》涵蓋的票券。不得新增自動付款或規避平台風控的能力；付款由使用者手動完成，保留授權與著作權聲明。
- 不提交或輸出真實設定、密碼、Cookie、token、webhook、完整個資、browser profile 或 session dump。自動化測試可用合成或已脫敏資料；需要瀏覽器時使用專用／暫存 profile。未經使用者授權，不操作真售票網站或日常 profile。
- 不為加速提高站點請求頻率、放寬 429 保護或省略送出前核對。停止、取消、逾時及指令返回都不證明遠端請求未發生；結果未知時不得自動重送。
- 保留其他平台、帳號資料與共用刷新資料的既有語意。uTiki 本次只按 [正式重構計畫](docs/02-development/utiki-refactor/PLAN.md) 等價整理 `944e934` 的實際行為及能力邊界；一般安全守則不擴張成原流程沒有的購票資格或拒絕條件。各目標模組的實際接線進度以 [STATUS.md](docs/02-development/utiki-refactor/STATUS.md) 為準，不得只依 PLAN 目標圖假設某責任尚未或已經完成。

## 開工與完成

1. 先核對 repository、branch、HEAD、未提交內容、適用的 AGENTS 與實際 caller；保留使用者現有工作，不以 reset、clean 或整批 restore 覆蓋。
2. 修改功能前界定使用者可觀察的完整行為，再按流程切片修改。主入口在 `src/nodriver_tixcraft.py`，平台邏輯在 `src/platforms/`；依賴方向見 [程式碼邊界](docs/02-development/code-boundaries.md)。uTiki 的正式 caller、遷移順序與驗收以 PLAN 為準；[12 階段標準](docs/02-development/ticket_automation_standard.md) 只作通用功能分類。
3. 同一 tab 的購票與刷新各只能有一個副作用 owner。填值須核對結果；click、reload、submit 呼叫完成不等於網站接受。保留原有重入、未知結果、暫停與清理時點，只在需求和實際 caller 支持時新增抽象。
4. 依 [測試指南](docs/02-development/testing_execution_guide.md) 選擇驗證層級，回報實際命令、結果與未驗證項目；本機測試、browser fixture、GitHub CI、真站手動驗收分開陳述。功能須接通設定、正式入口、執行、結果及必要測試，才可稱完成。
5. Commit 與推送由使用者決定。未經使用者明確下令，不執行 `git commit` 或 `git push`，也不自行建立 checkpoint commit；獲准提交時先核對暫存範圍，只提交本次工作。Issue／PR 亦須使用者授權，關聯及提交規範見 [CONTRIBUTING.md](CONTRIBUTING.md) 與 [GIT_COMMIT_RULES.md](GIT_COMMIT_RULES.md)。

## 代理協作與審查

- 本專案的程式碼、測試、設定與文件只由主代理寫入。Subagent 僅做唯讀盤點、指定區域審查與方案辯論；不得修改檔案或執行會改變工作區、Git 狀態的命令。
- 最終審查在主代理完成該輪寫檔、受審範圍固定後進行；審查後如修改受審內容，針對受影響部分重審。

## 文件入口

| 需要查什麼 | 主文件 |
| --- | --- |
| uTiki 目標、保留行為、責任與施工順序 | [PLAN.md](docs/02-development/utiki-refactor/PLAN.md) |
| uTiki 目前進度、實際基準與下一步 | [STATUS.md](docs/02-development/utiki-refactor/STATUS.md) |
| 模組邊界、結構索引與驗證命令 | [開發文件導航](docs/02-development/README.md) |
| 瀏覽器 API、公開貢獻與提交規範 | [Zendriver 指南](docs/06-api-reference/zendriver_api_guide.md)、[CONTRIBUTING.md](CONTRIBUTING.md)、[GIT_COMMIT_RULES.md](GIT_COMMIT_RULES.md) |

文件整理只修正本次相關入口與確切衝突。進度更新 STATUS，穩定設計決策更新 PLAN，通用驗證命令更新測試指南；舊計畫保留作歷史參考，不繼承其功能待辦或 gate。

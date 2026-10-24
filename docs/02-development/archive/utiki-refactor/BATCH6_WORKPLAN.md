# 批次 6 修訂施工指令與執行計畫

> **歷史封存（2026-10-25）**：批次 6 已完成。本文件保留當時指令與執行順序供追溯，不能作為目前版本的待辦或驗收門檻。現況請看 [STATUS.md](../../utiki-refactor/STATUS.md)。

本文件將先前的批次 6 指令按當時工作樹修訂，供該輪施工使用。當時產品範圍、保留行為與驗收依 [PLAN.md](PLAN.md)；實際結果見 [STATUS.md](../../utiki-refactor/STATUS.md)。舊指令所列 `835433f` 只是歷史準備點，不是要求重設的目標。

## 修訂後施工指令

1. 在既有 `codex/utiki-architecture-944e934` 工作目錄繼續，以 `944e934546f3dd36b1b4891a5a4fa359b8ade17f` 為唯一購票行為基準。開工時重新核對 HEAD、適用 AGENTS、caller、虛擬環境與未提交檔案；不 reset、restore、stash、重建 worktree 或覆蓋現有工作。
2. 批次 6 前已有獨立授權的設定服務安全修正及文件整理，均須保留。`/sendkey` 和六個控制端點的 HTTP 介面變更是獨立安全工作，不宣稱與 944 等價；批次 6 的舊新差異限於 uTiki 設定／購票範圍。以施工前工作樹為增量比較起點，不把整份 Git diff 誤稱為批次 6 新增修改。
3. uTiki 行為設定只讀寫 current `utiki` section。移除 `ctbc`→`utiki` 行為補值、預設鏡像、UI 雙寫、workflow fallback 及作為 uTiki 行為來源的 `ctbc` 熱更新。保留 `migrate_config()` 內其他平台、OCR、server port、discount code 和共用預設值處理。舊 JSON 中已存在但不再使用的 `ctbc` 欄位不需額外清除或轉換；current-only 的驗收是新設定不產生鏡像、舊值不影響 current 行為。
4. 保留 `accounts.ctbc_*`、CTBC tenant/UI 命名、既有 shared refresh／429 SQLite coordinator，以及其他平台設定。熱更新只按原 allowlist、指定 `advanced` 子鍵與讀取時點；不新增帳號或 OCR 模型熱切換、不新增 migration／version／dual write。
5. 核對正式 `nodriver_tixcraft.py` → `platforms.utiki` → `workflow` → 責任模組的單一路徑與唯一 runtime registry。`ctbc` 函式名或無狀態公開 facade 本身不代表第二條流程；只在確認正式 caller 與對外相容範圍後刪除轉接，不為名稱整潔大規模改名。既有 build spec 仍引用 `platforms.ctbc`，本批不改打包配置或為了 source 清理留下懸空引用；保留此無狀態 facade 並記錄原因。
6. 以既有測試結構補 current default、UI 讀寫、載入／儲存、legacy-only 不影響 current、`--input` 熱更新與原不熱切換邊界的必要案例。保留獨立安全修正的測試。先聚焦、再跑必要完整 pytest 與合成本機 browser fixture；做 touched Python `py_compile`、可用的 `pyflakes`、JS 語法及 `git diff --check`。比較表只填有對應 944 證據的結果；失敗與未驗證照實記錄。
7. 用既有 `.venv` 在 Windows x64 驗正式 source import／安全 CLI 路徑及離線設定流程；一般 Python 匯入須從 `src` 執行或顯式指定本工作樹的 import path。macOS arm64 無環境則記為未驗證，不宣稱 PLAN 的雙平台最終驗收已完成。這輪不操作真站、日常 profile、產品打包或 CI；不自行 commit、push、建 Issue／PR。
8. 主代理負責寫入。完成後固定受審差異，請 subagent 唯讀審查設定相容、正式 caller、原行為與測試 lookup；修正 finding 後重審受影響部分。更新 STATUS 與直接過時的邊界／結構文件，不重寫 PLAN 或引入舊計畫的 gate。

## 執行順序與檢查點

| 步驟 | 工作 | 證據 |
| --- | --- | --- |
| A. 固定起點 | 記錄實際 HEAD、工作樹、前置安全／文件差異、正式 caller 與環境 | 起點與本批增量可區分；既有檔案不丟失 |
| B. 設定讀寫 | 只移除 uTiki 舊行為格式的 default、補值、鏡像及 UI fallback／雙寫 | current 設定讀寫與其他平台 normalization 聚焦測試 |
| C. runtime 生效 | workflow 只讀 current；熱更新名單只移除舊行為 `ctbc`，保留原查詢時點 | current 值、legacy-only 值與 `--input` 情境測試 |
| D. caller 清理 | 核對 package、workflow、facade 與每個正式 source caller；只移除確證無需保留的轉接 | 單一入口／registry 依賴圖及離線 import／CLI 證據 |
| E. 整合驗證 | 聚焦 settings、uTiki 測試、完整 pytest、合成 browser fixture、靜態檢查 | 各命令、實際結果、環境限制與失敗紀錄 |
| F. 文件與複審 | 更新 STATUS、code-boundaries、structure 及確切過時入口；固定 diff 後分工審查 | 944 對照表、finding 處理、`git diff --check` |

完成報告分別列出：批次 6 的 current 設定／source 結果、先前獨立安全改動、Windows 驗證、macOS／CI／真站未驗證項，以及未解阻礙。本機施工完成不等於未執行的平台驗證已通過。

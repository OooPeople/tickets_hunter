# uTiki 重構目前進度

本頁只記錄目前接線、下一步、最近驗證與仍待處理的風險。目標和完成條件見 [PLAN.md](PLAN.md)；驗證命令見 [測試指南](../testing_execution_guide.md)。

## 現況

- 本次安全檢查點位於 `codex/utiki-refactor-clean-history`，以 `01f10c35bdfd` 為起點；新版施工計畫尚待使用者確認，不納入此檢查點。
- 正式 caller 仍是 `src/nodriver_tixcraft.py` → uTiki facade → `workflow.nodriver_ctbc_main`。舊 workflow 與共用 SQLite coordinator 仍是正式購票／刷新的副作用 owner；設定 UI 和 hot reload 仍有 `ctbc`／`utiki` 雙路徑。新 owner 尚未接正式入口。
- 隔離的新流程已接通 bootstrap、session controller、綁定 tab 的 browser adapter 和 production scripts，覆蓋日期 → 區域 → 一般票數或手動劃位、人工 CAPTCHA 欄位核對、CTBC 頁內登入、單次提交與結果觀察。票數／座位及登入填值均須回讀；送出前重驗文件、設定、授權來源與輸入。停止、重入、局部操作或未知提交不取得自動重試權。
- 目前安全檢查點只處理可歸屬於原提交的已知 CAPTCHA 拒絕：單一 claim 關閉原文件彈窗並回讀，再清空欄位、核對空值、按一次安全的圖片重設按鈕；須在期限內回讀同文件、空欄位、可用控制項及不同的圖片內容雜湊，才標記新題目證據成立。按鈕返回、結果不明、未換圖、逾時或停止都不解除阻擋，也不重點擊。即使證據成立，原提交仍被阻擋，沒有自動重新填值或再次送出。登入／座位衝突及未知／限購彈窗維持阻擋。
- OCR、登入／座位衝突恢復、電腦配位、刷新協調、current 設定 UI、正式入口切換與舊碼清理尚未完成。真站可能因圖片跨來源限制或不安全的重設控制項而無法取得新題目證據；此時維持阻擋。
- 舊 v1.6 catalog／P1／P2 gate 及其 `catalog-approval-v1.6.md`、`core-integration-inventory.md` 仍被 CI 使用，按計畫保留。先前職棒工作稿位於 `origin/codex/pre-refactor-baseball-wip-20261021`（`c6fcfd6`），僅供逐項評估。

## 下一步

等待使用者確認後續施工計畫。本輪在 CAPTCHA 安全檢查點停止；不自行接續 OCR、登入／選擇衝突恢復、刷新協調、正式入口切換或舊碼清理。真站驗收仍由使用者手動執行。

## 最近驗證與未驗證項目

- 2026-10-21：Windows 本機完整 strict pytest（短專用 `--basetemp`）為 **1045 通過、3 略過、34 排除、183 subtests 通過**，用時 587.43 秒。開發中的聚焦檢查為 CAPTCHA 合成單元 51 通過、腳本資源／manifest 62 通過；專用 profile 的合成 `file://` 瀏覽器 9 通過，涵蓋重設一次、欄位清空、新圖片證據、安全控制項負例及不重送。`mypy` 5 檔、修改過的 Python 語法、兩份新 JS syntax 與 `git diff --check` 通過。
- CAPTCHA 切片的固定範圍唯讀審查發現 3 項 P2：重設控制項可能提交／導頁、彈窗關閉範圍過寬、回讀跨過期限仍標記完成。已批次修正並窄複審，無剩餘 P0／P1／P2。前面流程切片的審查 finding 亦已針對受影響部分修正複審。
- 前一遠端 SHA `01f10c3` 的 [Tests](https://github.com/OooPeople/tickets_hunter/actions/runs/39508253304) 在 Ubuntu／Windows 各為 991 通過、6 失敗、1 略過、25 排除；6 項同屬舊 gate 三組數量常數過期，本檢查點已修正常數並保留檢查。[P1 Gate](https://github.com/OooPeople/tickets_hunter/actions/runs/39508253341) 的固定 base SHA 已非祖先，本檢查點改以 `origin/main` merge-base `944e9345`；該次遠端執行在 builder 數量 gate 停止，未跑到隔離瀏覽器。這些結果只適用於舊 SHA；本檢查點的 CI 須依推送後的新 SHA 另行核對。
- 效能比較用的既有本機基線：source-only warm 樣本（3 批各 300）日期／區域／座位／票數的中位／p95 分別為 5.907／6.785、2.745／3.155、4.722／5.264、0.240／0.321 ms；30 次 `file://` DOM 快照讀取分別為日期 0.398／1.097、區域 0.307／0.378、票數 0.396／0.657 ms。未量測真站等待、完整 CDP 流程或硬隔離網路。
- 真站狀態：`LIVE_NOT_VERIFIED`。本輪未操作真站或日常 browser profile；Windows/macOS 正式入口與真站手動驗收仍待後續計畫，新 SHA 的 CI 狀態另行核對。

# uTiki 重構目前進度

本頁記錄跨電腦接續工作需要的現況、下一步與最近驗證。穩定目標見 [PLAN.md](PLAN.md)；命令見 [測試指南](../testing_execution_guide.md)。

## 現況

- 分支：`refactor/17-utiki-root-v1-5`。目前只完成文件整理，尚未按新計畫修改正式購票程式或 CI。
- 正式主迴圈仍使用舊 `nodriver_utiki_main`／workflow。新 `UtikiEngine` 只有部分 session 與票數規劃，尚未接管日期到提交的完整流程。
- JavaScript 已部分外部化；設定 UI 仍有 `ctbc`／`utiki` 雙路徑。舊 v1.6 catalog／P1／P2 gate 與兩份機器依賴文件仍保留在分支中。
- 舊 CI 曾有探針數、manifest node 數不一致等失敗；目前提交尚未重新跑完整 CI，不沿用先前 PASS 或推定失敗原因。
- 先前工作稿保存在 `origin/codex/pre-refactor-baseball-wip-20261021`（`c6fcfd6`），包含首輪加速、日期按鈕、共享刷新、味全龍 Cookie 與相關測試。只逐項評估，不整批合併。

## 下一步

1. 盤點正式 caller、設定、刷新與提交；重現舊 CI 失敗並區分工具問題與行為回歸，取得延遲基線。
2. 在隔離 fixture 接通日期 → 區域 → 票數／座位的完整流程切片，確認單一 owner 與必要測試。

## 最近驗證

- 2026-10-21：文件連結與 `git diff --check` 已通過；尚未執行 production pytest、隔離瀏覽器、效能量測或真站驗收。
- 真站狀態：`LIVE_NOT_VERIFIED`。實際測試結果與日期在取得新證據後更新。

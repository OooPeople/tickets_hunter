# Git Commit 訊息規範

本專案的 Git commit message 採用 Conventional Commits 風格：

```text
<type>(<scope>): <summary>
```

若不需要 `scope`，可省略為：

```text
<type>: <summary>
```

## 語言規範

- `type` 一律使用英文小寫關鍵字。
- `scope` 使用英文或專案內一致的模組名稱。
- `summary`、`body`、`footer` 使用繁體中文。
- 不要在同一類模組中混用不一致的中英文命名。

## 撰寫原則

- `summary` 應簡短明確，直接描述這次提交做了什麼。
- `body` 可依變更複雜度寫較完整的背景、行為差異與風險；不需要刻意壓成簡短敘述。
- `body` 需要列出多個項目時，每個項目前使用 Markdown `-` 符號分項。
- commit message 不需要寫上驗證結果。
- 不要在 `summary` 結尾加句號。
- 避免模糊訊息，例如 `update`、`fix bug`、`modify code`。
- 一次 commit 盡量聚焦單一職責。

## 常用 type

- `feat`：新增功能或明確的功能性增強。
- `fix`：修復錯誤或不正確行為。
- `docs`：文件變更。
- `test`：新增、修改或修正測試。
- `refactor`：重構，不新增功能也不修 bug。
- `chore`：工具、設定或維護類變更。
- `build`：建置系統或依賴變更。
- `ci`：CI / workflow 相關變更。

## 建議 scope

- `docs`
- `phase0`
- `scripts`
- `browser`
- `worker`
- `config`
- `deps`

## 範例

```text
docs(readme): 補充 uv 初始化指令

chore(project): 建立 Python 版監視工具骨架
- 建立 Python 套件、uv 專案設定、基礎目錄與測試入口。
- 放入 FastAPI、Playwright、SQLite、本機 runtime path 等後續主軸需要的骨架。
- 保留從 userscript 遷移到 Python 版時需要的文件與參考資料，讓歷史來源能被追溯但不成為正式產品主路徑。

feat(webapp): 收合新增 target 進階設定並修正預設值文案
- 將新增 target 的掃描、刷新、通知設定收進進階設定區塊，並沿用共用欄位 partial
- 抽出共用收合動畫 helper，讓 target 卡片與新增頁面共用平滑動畫行為
- 修正設定頁排除關鍵字預設值標題、儲存按鈕與成功訊息文案
- 補上 route、static contract 與 browser smoke 測試覆蓋收合互動

fix(worker): 強化 Facebook 頁面錯誤判斷
- 以 marker context、可見 feed 與 bounded permalink 容器區分整頁錯誤和貼文引用
- 保留內容不可見三次、每次間隔 30 秒與新 page 確認，並拆分 scheduler due 決策
- 新增 diagnostics v2 相容層、最終日誌敏感資料遮罩與真實 Chromium 回歸測試
```

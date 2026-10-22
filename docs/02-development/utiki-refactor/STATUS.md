# uTiki 重構目前進度

本頁記錄實際接線、下一步與驗證結果。需求、責任及完成條件見 [PLAN.md](PLAN.md)；可執行命令見 [測試指南](../testing_execution_guide.md)。隔離合成流程與真站手動驗收分開記錄。

## 目前接線

批次 0–6 在 branch `codex/utiki-refactor-clean-history` 的同一工作樹完成，來源基準為 `9e1b20270c06ab975aea915ea9e29b5940a9e013`；下列本機驗證屬於提交前的實際內容，不代表後續 GitHub CI 已通過。正式 uTiki URL route 現由 `src/nodriver_tixcraft.py` → `advance_utiki_route` → `UtikiProductionRun` 持有唯一副作用；原 `workflow.nodriver_ctbc_main` 不在正式主迴圈執行，正式 main 也不再匯入舊 workflow／refresh 轉接。共用 SQLite coordinator、accounts 與 global settings 保持原資料來源。批次 5 前置快照 `utiki-b5-boundary-20261022` 保留；批次 6 開始前的 74 個既有變更另保存於工作樹旁 `utiki-b6-boundary-20261023`（原檔、SHA256 manifest、git status 與 patch），均非產品或 CI 依賴。

| 批次 | 已接通的隔離流程與出口證據 |
|---|---|
| 前置＋0（0A–0D） | 完整 typed effective policy、單 Run／活躍 tab、claim／receipt、延遲回讀與原結果歸屬；production-origin 合成日期至 checkout 使用實際 classifier、tenant、bootstrap、adapter、scripts 和 scoped cookie session provider，符合 [9.2 節](PLAN.md)。Set-Cookie 只證明 session 綁定世代，不證明登入成功。 |
| 1 | 已知 CAPTCHA 拒絕後以新圖片／新輸入／新 attempt 恢復；Run 啟動時組裝實際 OCR provider，`enable`／`force_submit` 四種政策及 provider 不可用時的人工路徑均有證據；人工送出也沿用原結果歸屬。 |
| 2 | CTBC 電腦配位使用 tenant 原生日期／區域動作，核對 `UTK0201_001`、`ATYPE=N`、商品及來源／目的文件；正常、已知拒絕後 OCR 和人工路徑到已知 checkout。錯 query／商品、不支援 tenant、無來源證據及不確定派送均阻擋，沒有其他模式 fallback。 |
| 3 | 單 Run 的刷新 intent 經既有 SQLite coordinator 的 reserve、重驗、commit、派送前重驗與唯一 browser reload 接縫；保留 interval、cooldown、429 去重、probe lease 及原 token。購票 ready 與未知提交優先；same-URL 新文件也須重新取得 health／challenge clearance。當批尚未移交正式 host，於批次 5 接管。 |
| 4 | 已知登入拒絕與三模式選擇衝突連回原 attempt；關閉彈窗、可信人工欄位觀察及清理原選擇使用各自的新 claim。三模式清理原選擇後經批次 3 受控刷新、新文件／來源／商品／health 確認再重選；部分失敗保留原拒絕。checkout 歸屬原 attempt、停止自動購票並去重嘗試啟動通知；限購維持本 Run 終局。 |
| 5 | current `utiki` 行為與流程欄位由同一純 validator 驗證，UI／save／load／Run／hot reload 不再鏡像 `ctbc` 行為值。正式 main 在通用 health／排程／其他平台派送前把 uTiki 交給持續存在的 Run；啟動時組裝實際 browser、scoped session、OCR、SQLite refresh 與通知，stop／pause／tab 離開／例外走原 Run 收尾。production-origin 隔離測試從正式 main 及 route helper 驗證正常三模式、OCR／人工、已知恢復、刷新、結果與控制。 |
| 6（Windows 可執行收尾） | 正式 main 的舊刷新／暫停／cleanup 轉接及 `platforms/ctbc.py` facade 已退役。直接／動態引用、打包、測試與 workflow 查核後，無產品 caller 的 `platforms/utiki/workflow.py`、根層 `refresh.py`、專用測試橋接和不再載入的舊腳本已移除；現行 `adapters/refresh.py` 與 SQLite coordinator 保留。純規則及 Cookie 解析測試改讀正式單一來源，三模式、生命週期、429、正式 caller 和 production script 由現行測試承接。P1／P2 gate 本機退役沿用已驗證的前輪成果。 |

可信且匹配原 claim 的送出前 `NO_EFFECT` 零點擊回執會立即收尾原 operation；新輸入另決定下一個 attempt 的資格。`REQUEST_POSSIBLE`、已派送或未知的原提交不因新輸入而取得重送授權。已知拒絕也不清空整份提交狀態。

## 未完成與下一步

- Windows 可執行的舊 owner 清理已完成；第 13.2 節整體離線出口仍缺 macOS arm64 原生驗證。Mac 須使用含批次 0–6 完整內容的固定版本；提交前的一次性來源快照已保留，舊基準 HEAD 不含重構成果。Windows／Linux 結果不得代替 Mac 實測。
- 登入拒絕後，production browser 可證實同文件的可信新欄位輸入並在點擊前重驗，不用舊 config 覆寫。相同文字但使用者已人工登入時，只有正式 auth provider 提供與原 attempt、tenant、文件及新 auth epoch 相符的服務端接受證據，才可自動重新準備。目前 `BrowserSessionAuthority` 沒有此證據，維持明確人工出口；不以固定 epoch、帳密熱載或長期密碼 digest 補造能力。
- 目前 CTBC synthetic provider 以同租戶、同 tab 的 scoped cookie 證實 session 綁定世代；缺少可用 scoped session 時正式 Run 顯示人工處理原因，不把 Cookie 本身推定為已登入。其他沒有此 provider 能力的 tenant 也保持人工出口，不能宣稱自動購票已支援。
- 正式 host 對 challenge 只做唯讀觀察；無可信授權的 challenge 解決仍是人工出口，不會呼叫外層可能 reload 的 handler。無法證實來源或頁面 health 時維持阻擋。真站狀態為 `LIVE_NOT_VERIFIED`，未操作真站或日常 browser profile。

## 驗證紀錄

以下結果只屬於 2026-10-23 刪除舊副作用模組後的**提交前受測工作樹**：Windows x64／Python 3.11.13、branch `codex/utiki-refactor-clean-history`、base HEAD `9e1b20270c06ab975aea915ea9e29b5940a9e013`。命令與隔離條件見[測試指南](../testing_execution_guide.md)。

| 驗證層級 | 實際結果與範圍 |
|---|---|
| 一般完整 pytest／coverage | `pytest --cov=src --cov-report=term -q --tb=short`：**536 passed、0 failed、2 skipped、56 deselected、78 subtests passed**（58.25 秒）；預設 suite coverage **27%**，其中新 engine 83%、純 selection 89%。browser 另跑，不能併入此 coverage。 |
| 聚焦現行行為與資源 | 401 passed、66 subtests passed；三模式狀態、refresh owner／coordinator、純規則、架構與腳本資源。 |
| 型別／語法／架構／資源 | strict mypy 28 個 source files 無錯；`compileall`、24 個保留 JS 的 `node --check`、架構 checker 與正式 `--utiki-resource-self-test` 均 PASS。 |
| Windows x64 原生 | 正式 `main` 使用專用 profile 啟動／停止，`file://` browser 全分組 **16 passed**（59.94 秒）；此結果不代表 production-origin。 |
| Linux amd64 production-origin | Docker `--network none` 在 browser 啟動前生效，只有 `lo`、沒有對外路由；正式 caller、三模式、登入／CAPTCHA 人工出口、刷新、設定 UI 與資源 **35 passed**（113.97 秒）。關閉時有非致命 Zendriver websocket task 訊息。 |
| 舊 gate 的可執行替代 | 前輪本機退役時，現行架構 checker PASS／相關測試 15 passed、腳本資源契約 23 passed、strict mypy 及資源自檢 PASS；動態 manifest、tamper fail-closed 與 browser 行為測試保留。三個舊 P2 固定 byte SHA 失敗隨已替代的歷史斷言退役；本輪沒有重開 gate 審查。 |

清理後的測試責任：純 `auth`／`profiles`／`selection` 規則仍由 `test_ctbc_pure.py` 驗證；舊 DOM 與提交／拒絕歸屬案例由 `tests/unit/utiki/test_p2_selection_flow.py`、`tests/test_utiki_engine_browser_flow.py` 及 production-origin 三模式測試承接；共享 pacing／429／lease／精確清理由 `tests/test_refresh_coordinator.py`、`tests/unit/utiki/test_refresh_owner.py` 與隔離刷新流程承接；production scripts 由現行 browser 測試及 `tests/architecture/test_utiki_script_resources.py` 驗證。deterministic harness 的共用 clock／pending I/O／task cleanup 仍保留，engine interleaving 覆蓋原 workflow 的有效生命週期契約。只服務舊 runner 的四個 HTML 片段已移除，現行合成頁保留。

**歷史結果僅供歸屬，不代表目前工作樹通過：**刪除舊模組前的批次 6 suite 為 763 passed／0 failed／2 skipped／56 deselected，coverage 33%；批次 5 為 1132 passed／3 failed／3 skipped／65 deselected；批次 4 為 1131 passed／3 failed／3 skipped／52 deselected。批次 0D–3 當時分別為 1075、1091、1099、1118 passed，皆另有同三個舊 P2 固定 SHA failed。這些 byte SHA 斷言只對原基準有效。清理後通過數與 27% coverage 不能由刪除前的 763／33% 直接推定覆蓋增減，也不能把舊結果當成本工作樹 PASS。

**效能：**同機 source-only 合成 90 樣本（warmup 20；200 日期列／200 區域列／500 席／100 票數列），現行日期、區域、座位、票數 median／p95 分別為 6.016／7.315、3.316／4.170、5.141／5.908、0.277／0.419 ms。Windows Chrome `file://` 單次 snapshot read 90 樣本為日期 0.505／1.275、區域 0.520／0.955、票數 0.430／0.565 ms，各需 1 次 `tab.evaluate`／`tab.send`，沒有顯式 sleep。前置基線未保留相同 fixture／subject digest 與完整交易 CDP 計數；批次 5 前置快照也已包含批次 0–4，故**不能公平判定整體改善**。未見足以移除必要 preflight 或等待的重複操作。

macOS arm64 原生環境本輪不可用；同版本來源交接及待執行命令見測試指南。提交前沒有新 SHA 的 GitHub CI 結果；推送後須按實際提交 SHA 另查。真站保持 `LIVE_NOT_VERIFIED`。這些缺口不由 Windows 或 Linux 結果代替。

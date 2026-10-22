# uTiki 重構目前進度

本頁記錄實際接線、下一步與驗證結果。需求、責任及完成條件見 [PLAN.md](PLAN.md)；可執行命令見 [測試指南](../testing_execution_guide.md)。隔離合成流程與真站手動驗收分開記錄。

## 綜合內審修正（`e7ee2ea` 之後）

本節記錄 branch `codex/utiki-refactor-clean-history` 上，以 `e7ee2ea1b17ec651cee4e51cfb9c52101f369c9d` 為提交前 HEAD 完成的修正；涵蓋下節 R1–R10 之後的工作，並保留先前成果。正式 Run 的 CLOSING 優先於 Pause，關閉要求與成功退休分開；URL 暫不可讀時仍維護原 queue／reservation／probe 及結果，不新建頁面動作。同 tab 離開 uTiki 只在原 claim 與 coordinator token 精確收尾後交給其他 route；未收尾時顯示人工核對原因。真 SQLite 測試證明空 URL 清理原 queue、離站時 probe 續租、單次 reload、證實新文件後交接，另驗證不確定 close 可依原 handle 重試。

正式 main 的隔離 browser 案例現直接走同 tab 離站、清除 owner 後再進站建立新 Run，以及連續 URL 不可讀時維護原 Run；退休中未知提交在晚到 checkout 後保留已知原結果，已知終局要求人工結束本 Run，不交給另一個平台接續自動購票。

移除只供歷史測試使用的 quantity port／`plan_quantity`／adapter wrapper、`CurrentPolicyState`、`SubmitState` 包裝及專用測試與舊 P2 benchmark；正式 quantity／提交歸屬、`EffectivePolicy` 無效重載與 revision、typed selector 及現行效能案例承接有效契約。日期／區域 selector 直接收驗證後的 `FlowSettings`，不回投 raw dict。正式 Run 在日期／區域自動選擇停用時，仍檢查可到達的票數／登入／提交 adapter 能力。共用設定與正式 tenant 分類使用同一純 sports HTTPS origin 判斷；設定頁 Run 可用狀態改由後端完整 current validator 回覆。移除未被 repository 讀取的舊 release target catalog、舊分支專用 upgrader diagnostic workflow與手寫 typed scope；CI strict mypy 直接指定現行目錄。獨立的設定轉換工具仍保留，沒有新增升級器或打包流程。

本工作樹在 Windows x64／Python 3.11.13 的一般完整 pytest `--strict-config --strict-markers --cov=src --cov-report=term -q --tb=short` 為 **529 passed、0 failed、2 skipped、61 deselected、78 subtests passed**，整體 coverage **27%**；刪除只測舊 API 的案例後，不能以通過總數推定覆蓋退步。聚焦 unit／架構／設定 **410 passed、2 skipped、66 subtests passed**，現行效能案例另 **10 passed**。strict mypy 對現行 27 個 source files、架構 checker、資源自檢、Python／JS 語法及 `git diff --check` 均通過。Windows 原生 `file://` browser **16 passed**。Linux amd64 Docker `--network none` 先確認只有 `lo`、沒有對外路由，再執行 production-origin／正式 main／設定 UI browser **40 passed**。這些本機結果不代表此版本 GitHub CI 通過，遠端結果須按實際提交 SHA 核對。macOS arm64 尚未驗證；舊 Mac ZIP 不包含本輪修正，不是同版本候選來源。特殊人工登入出口、不可公平比較的效能基線與真站 `LIVE_NOT_VERIFIED` 維持。

## 前一輪 e7ee2ea R1–R10 修正的中間工作樹（歷史紀錄）

以下「本次」均指該中間工作樹，不是上節綜合內審修正後的目前工作樹；舊數字僅保留原驗證歸屬。

目前 branch 仍是 `codex/utiki-refactor-clean-history`，HEAD 為 `e7ee2ea1b17ec651cee4e51cfb9c52101f369c9d`；以下修正僅在該 HEAD 上的未提交工作樹。該固定 SHA 的審查指出 R1–R10；其 [GitHub CI](https://github.com/OooPeople/tickets_hunter/actions/runs/39852033261) 為 failure（隔離 browser 設定 UI 導航競態），不得寫成目前修正已獲遠端 CI 驗證。

- R1–R3：正式 main 的每次購票 preflight 同步讀取既有 per-instance Pause／Stop 來源；日期目的頁證實後即收尾原導航並保留來源，無區域候選仍可走唯一受控刷新；人工提交跨新文件只消費精確匹配的舊 click marker。實際 production script digest 已隨腳本更新。
- R4–R6：原拒絕與恢復新 claim 的授權分開；相同來源的 Pause→resume 或不影響選擇的有效流程政策變更可用目前版本重新授權，選擇設定／行為、商品或 auth 不符轉明確人工出口。人工提交以 arm 當時的政策保存原 attempt 歸屬；正式 Run 在 Pause、host 觀察失敗或政策變更時仍只讀回核既有恢復 claim。未知 CAPTCHA 寫入不因人工 revision 增加而被當成已收尾；未知彈窗只阻擋新派送，原提交可繼續歸屬晚到 checkout 或已知拒絕。
- R7–R8：429 recovery probe 在新文件等待 health clearance 及 Pause 時續租；`complete_probe` 失敗先以原 permit 續租結果區分資料庫暫態與確定失租，120 秒上限精確清理後保留人工核對。paused Run 先維護原 queue／reservation／probe 再嘗試 browser 回讀；清理重試耗盡有可見人工出口，沒有第二次 reload。R9 讓 uTiki 正式設定鏈不套用舊 Tixcraft headless OCR 特例，共用設定層僅對精確 sports HTTPS origin 排除舊覆寫，其餘 caller 沿用原處理；uTiki current 政策保留四種 `enable`／`force_submit` 儲存值與合法 CLI override。R10 設定 UI 測試等待新 document 和欄位載入，僅重試預期的導航暫態錯誤。

本次 Windows 一般完整 pytest `--strict-config --strict-markers --cov=src --cov-report=term -q --tb=short` 為 **562 passed、0 failed、2 skipped、59 deselected、78 subtests passed**，整體 coverage **27%**；聚焦行為／資源 **192 passed**。本機依 CI 命令執行的 28 檔 strict mypy、架構 checker、產品資源自檢、修改過的 Python 語法與 JS `node --check` 均通過。Linux amd64 Docker `--network none` 隔離在瀏覽器啟動前生效，介面只有 `lo` 且無對外路由；production-origin／正式 main／設定 UI browser 分組 **38 passed、0 failed**。其中 R1 的正式 main OCR 等待期 Pause／Stop、R3 的實際腳本跨文件重新 arm 與 R10 的設定頁新文件同步均在此分組。Windows 原生結果另列本輪驗證，不能沿用下方舊工作樹紀錄推定。Mac arm64 尚待同版本原生驗證，特殊人工登入出口及效能基線不可比維持；真站仍為 `LIVE_NOT_VERIFIED`。尚未 commit／push，因此沒有修正後 SHA 的 GitHub CI 結果。

本輪 Windows x64 原生正式 main smoke 與 `file://` browser 分組另為 **16 passed、0 failed**；此結果只屬目前未提交工作樹，不代表 macOS 或 production-origin。

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

- Windows 可執行的舊 owner 清理已完成；第 13.2 節整體離線出口仍缺 macOS arm64 原生驗證。Mac 須使用含本輪修正的固定版本；先前一次性來源快照已過期，舊基準 HEAD 也不含重構成果。Windows／Linux 結果不得代替 Mac 實測。
- 登入拒絕後，production browser 可證實同文件的可信新欄位輸入並在點擊前重驗，不用舊 config 覆寫。相同文字但使用者已人工登入時，只有正式 auth provider 提供與原 attempt、tenant、文件及新 auth epoch 相符的服務端接受證據，才可自動重新準備。目前 `BrowserSessionAuthority` 沒有此證據，維持明確人工出口；不以固定 epoch、帳密熱載或長期密碼 digest 補造能力。
- 目前 CTBC synthetic provider 以同租戶、同 tab 的 scoped cookie 證實 session 綁定世代；缺少可用 scoped session 時正式 Run 顯示人工處理原因，不把 Cookie 本身推定為已登入。其他沒有此 provider 能力的 tenant 也保持人工出口，不能宣稱自動購票已支援。
- 正式 host 對 challenge 只做唯讀觀察；無可信授權的 challenge 解決仍是人工出口，不會呼叫外層可能 reload 的 handler。無法證實來源或頁面 health 時維持阻擋。真站狀態為 `LIVE_NOT_VERIFIED`，未操作真站或日常 browser profile。

## 驗證紀錄

以下結果只屬於 2026-10-23 刪除舊副作用模組後、提交 `e7ee2ea` **以前**的受測工作樹：Windows x64／Python 3.11.13、branch `codex/utiki-refactor-clean-history`、當時 base HEAD `9e1b20270c06ab975aea915ea9e29b5940a9e013`。不得將其當作上方審查修正工作樹的驗證；命令與隔離條件見[測試指南](../testing_execution_guide.md)。

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

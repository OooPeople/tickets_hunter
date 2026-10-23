# uTiki 重構狀態

- 行為與程式起點：`944e934546f3dd36b1b4891a5a4fa359b8ade17f`。
- 工作分支／階段：`codex/utiki-architecture-944e934`；批次 0 文件與原版基準已準備，批次 1 及後續架構批次尚未開始。
- 計畫：[PLAN.md](PLAN.md) 完整承接本次使用者提供的 `utiki_architecture_restart_944e934.md`；不沿用 a47 的完成狀態或舊 gate。
- 程式狀態：production、tests、`requirement.txt`、`requirements-dev.txt` 與 `pytest.ini` 均未修改；未接入目標新模組。
- 環境：Windows x64；新 worktree 的 `.venv` 使用 CPython 3.11.9、pytest 9.0.3，無額外 `PYTHONPATH`。執行 `uv venv --python 3.11.9 .venv` 及 `uv pip install --python .\.venv\Scripts\python.exe -r requirements-dev.txt`，兩者退出碼均為 0；需求取自 944 原檔。
- 預設基準：`.\.venv\Scripts\python.exe -m pytest`，退出碼 0；收集 356 項，355 passed、1 deselected（預設排除 browser/live），耗時 14.08 秒。本次沒有另跑已由全套涵蓋的聚焦測試。
- 本機 browser fixture：暫時設定 `TICKETS_HUNTER_RUN_BROWSER_FIXTURES=1` 後執行 `.\.venv\Scripts\python.exe -m pytest -m 'browser and not live' tests/test_ctbc_dom_fixtures.py`，退出碼 1；收集 1 項、1 failed。瀏覽器已啟動並執行本機 fixture；`test_production_dom_contracts_in_local_browser` 的 seat 情境在 `commit_seat` 回傳 `CommitResult.REJECTED`，預期 `COMMITTED`（`tests/test_ctbc_dom_fixtures.py` 的 `_assert_seat_fixture`）。根因待判定，未修改原行為或測試預期。runner 在此斷言中止；後續 quantity、server-assigned quantity、dialog 與 fast-marker 情境本輪未執行。
- CI／建置／真站：本輪未執行 GitHub CI、本地打包或真站手動驗收；不以本機 pytest 代稱這些層級。
- 已檢視範圍：944 適用 AGENTS、CLAUDE、法律聲明、文件入口與直接引用、目前模組邊界、測試指南、需求與 pytest 配置；對照 PLAN 的人工四碼、人工進一般票數頁及 checkout 通知情境。其餘 PLAN 情境未逐一完成舊新對照。
- 下一步：批次 1 先補下表已確認的少量 944 行為斷言；追查 browser fixture 基準失敗。其後才按 PLAN 進入架構批次。

| 文件或 PLAN 情境 | 確切衝突／缺口及證據 | 狀態與下一步 |
| --- | --- | --- |
| 舊計畫與文件入口 | 舊 `utiki-refactor-plan.md` 稱依 Phase 執行；開發導航稱待審，`docs/README.md` 與 `structure.md` 仍直接引用。 | 已標示舊計畫為歷史，直接入口改指 PLAN／STATUS；舊功能待辦與 gate 不繼承。 |
| 一般安全文字與 uTiki 庫存判斷 | 舊 AGENTS 的一般 `fail closed` 文字若擴張到不明但非空庫存文字，會與 944 `selection.py` 的原判斷及 PLAN 的等價範圍衝突。 | 已在 AGENTS 說明不得增加原流程沒有的拒絕條件；production 未變。 |
| 測試指南的 fixture 條件 | 原指南只寫 `file://`，但 `test_ctbc_dom_fixtures.py` 亦使用 `127.0.0.1` loopback。 | 已修正指南；本機 fixture 可啟動，但 seat 斷言失敗待判定。 |
| 人工四碼、OCR 關閉 | `workflow._ctbc_captcha_and_submit` 可在已填四碼且 OCR 關閉時 ready、保持人工提交；`test_ctbc_dom.py` 現有送出開關案例只測 OCR 開啟。 | `needs_case`：批次 1 補 OCR 關閉＋人工四碼的準備與不自動送出斷言。 |
| 一般票數頁人工接手 | `workflow._ctbc_attest_fast_destination` 在無 pending／marker 時可接手；`test_ctbc_dom.py` 目前直接斷言的是劃位頁人工進入。 | `needs_case`：批次 1 補一般票數頁無來源紀錄的接手案例。 |
| Checkout 聲音與整組去重 | `workflow._handle_ctbc_checkout` 依 `advanced.play_sound.order` 決定聲音，Discord／Telegram 與聲音共用 checkout key；既有通知測試未斷言聲音開關。 | `needs_case`：批次 1 補聲音開／關與重複 tick 的通道呼叫斷言。 |
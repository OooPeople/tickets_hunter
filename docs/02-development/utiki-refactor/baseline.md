# uTiki v1.5 P0 基準

本文件固定 Issue #17 開始實作前的 repository、執行環境、測試與架構基準。它是
P0 證據，不代表 v1.5 的 target architecture 已存在，也不代表真實售票流程已驗證。

## 1. Git 與採用基準

| 項目 | 固定值 |
|---|---|
| Repository | `OooPeople/tickets_hunter` |
| 實作 branch | `refactor/17-utiki-root-v1-5` |
| baseline／`origin/main` | `f307f7caba054fb40aab807a4629c891e69fb200` |
| `upstream/main` | `9e2d1061b48706076943b1059616347c389df809` |
| baseline 相對 upstream | ahead 52、behind 0 |
| 採用紀錄 | [adoption.md](adoption.md) |
| 正式計劃 | [uTiki 根本重構計劃 v1.5](../utiki-root-refactor-plan-v1.5.md) |
| 計劃 SHA-256 | `c8fcc741a37db3d404cefd73cfcda6731a24a922f42522777fbdbc3eeddf6328` |

主代理已於 2026-10-16 18:00:08 +0800 成功 fetch `upstream`；當次 `FETCH_HEAD`
確認 `upstream/main` 仍為上表 SHA。remote ref因內容未變而沒有新的 ref update，不得以
reflog時間推論本輪未 fetch。

基準驗證開始時 HEAD、`origin/main` 與 merge base 相同，production Git diff 為空。
P0 文件之後在共享 working tree 逐步建立；這些未提交文件不能冒充上述 clean SHA 的內容，
正式 candidate 必須另記 exact SHA 或 `DIRTY` working-tree digest。

## 2. 執行環境

以下版本由 verification agent 在 baseline SHA 的專案 `.venv` 實際讀取：

| 元件 | 版本 |
|---|---|
| Python | 3.11.15 |
| pytest | 9.0.3 |
| pytest-cov | 7.1.0 |
| PyInstaller | 6.22.3 |
| zendriver | 0.16.0 |

`.venv` 當時沒有 pip；mypy 與 hypothesis 未安裝。這是環境事實，不是跳過 P1 typed、
property-based 或 isolation gate 的豁免。

## 3. 實際測試基準

| 命令 | 實際結果 | wall time | 限制 |
|---|---|---:|---|
| `.venv/bin/python -m pytest --strict-config --strict-markers --collect-only -q` | 356 total、355 selected、1 deselected | 0.98 s | 只證明 collection |
| `.venv/bin/python -m pytest --strict-config --strict-markers` | 354 passed、1 skipped、1 deselected | 4.92 s | skipped 為 Windows-only；deselected 為 browser |
| `.venv/bin/python -m pytest --strict-config --strict-markers --cov=src --cov-report=term-missing` | 354 passed、1 skipped、1 deselected；total line coverage 24% | 8.73 s | coverage 不是行為完整度 |
| `.venv/bin/python -m unittest discover` | 355 tests，OK，skipped=1 | 4.857 s | unittest 不保證收集未來 pytest-only tests |
| `.venv/bin/python -m pytest -o 'addopts=' --strict-config --strict-markers -m 'browser and not live' tests/test_ctbc_dom_fixtures.py --collect-only -q` | 1 collected | 未記錄 | 僅 collection；未啟動瀏覽器 |

uTiki 相關檔案的 baseline line coverage：

| 模組 | coverage |
|---|---:|
| `platforms/utiki/auth.py` | 88% |
| `platforms/utiki/dom.py` | 63% |
| `platforms/utiki/profiles.py` | 93% |
| `platforms/utiki/refresh.py` | 84% |
| `platforms/utiki/selection.py` | 88% |
| `platforms/utiki/workflow.py` | 82% |

現有 suite 是 characterization 起點，不是 v1.5 固定 required catalog 的替代品。特別是
browser 案例尚無 CDP 之外的 hard-egress 隔離，故本次只 collection，不執行 browser。

## 4. 現行架構快照

baseline 的 `src/platforms/utiki/` 有七個 Python 模組：

| 檔案 | 行數 | 現行責任 |
|---|---:|---|
| `__init__.py` | 42 | eager 匯入並 re-export workflow 舊函式 API |
| `auth.py` | 117 | tenant auth/cookie policy |
| `dom.py` | 2,455 | snapshot codec、inline JS 與 browser commit |
| `profiles.py` | 184 | tenant allowlist、PageKind、route classifier |
| `refresh.py` | 1,534 | document health、SQLite coordinator protocol、browser reload |
| `selection.py` | 1,839 | records、parser、選擇與 audit 規則 |
| `workflow.py` | 3,547 | module registry、狀態、流程與多數副作用編排 |

現行 composition path 是：

```text
nodriver_tixcraft.py
  -> platforms.utiki.__init__
    -> workflow.py (_state / per-tab runtime)
      -> auth / profiles / selection / dom / refresh
        -> util / nodriver_common / global_settings / refresh_coordinator
```

`workflow._state` 名義上持有唯一 tab registry，但 `workflow.py` 與 `refresh.py` 都會直接
修改同一份巢狀 runtime；沒有 engine instance、SessionHandle、單一 application writer、
ActiveEffect 或 arbiter。詳細可觀察契約見 [behavior-contracts.md](behavior-contracts.md)。

## 5. Build／release 基準

- Bot spec：`build_scripts/nodriver_tixcraft.spec`；hidden imports 仍包含
  `platforms.ctbc`、`platforms.utiki.workflow` 及其他現行 uTiki 模組。
- Settings spec：`build_scripts/settings.spec`。
- Release workflow 有 Windows x64 與 macOS arm64 build jobs，兩者都建 bot/settings。
- 本次 P0 只讀 baseline **未執行 build**，因建置會產生 `build/`、`dist/`；不得宣稱
  source、Windows 或 macOS frozen artifact 通過。

## 6. Syntax 與未驗證事項

- baseline clean HEAD 的 Python diff 是空集合；未執行 `py_compile`，compile count=0。
- 當時 `tools/verify_modified_python.py` 尚不存在，因此只能記為人工判斷的
  `NO_PYTHON_CHANGES`，不是 v1.5 §21.3 的正式 syntax report。
- `git diff --check` 在 clean baseline 無輸出；P0 working tree 的最終 candidate 仍須重跑。
- 未執行真站、登入、刷新、選票、提交、付款、GitHub Release 或部署。
- 離線 source cold/warm 與 event-loop lag 已在後續 P0 evidence run 量測；CDP command、
  request count、browser/native/frozen仍未量測。原始報告與邊界見
  [baseline-benchmark.md](baseline-benchmark.md)。

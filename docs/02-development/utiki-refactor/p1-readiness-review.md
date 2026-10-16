# uTiki v1.5 P1 readiness review

審查基準為 P0 commit `a1374bddc44cabfd5887d9ce991f5657e4c9628b` 加上本輪
P1 candidate。本文採 findings-first：本機測試通過只代表 P1 基礎設施已可供正式執行，
不代表五個 required job 的同輪 aggregate 已通過，也不代表真站流程已驗證。

## Findings

- **[P2／P1 gate blocker] 18 個 isolated obligation 尚無正式 Linux hard-egress 證據。**
  `browser-contract-isolated` 的 H01–H04 共 13 項及 `performance-contract` 的 V12／V13
  共 5 項，必須在 digest-pinned Linux x64 image、`--network none`、受限權限與受控
  loopback peer 下執行。此 macOS host 沒有 Docker／Podman／Colima，故沒有執行 container、
  Chromium production-origin fixture、isolated 12 negative probes或正式 CDP performance
  measurement。這 18 項維持 `BLOCKED`，不得以 launcher 單元測試、P0 source benchmark
  或 host socket 測試冒充 PASS。

- **[P2／P1 gate blocker] GitHub repository 尚未配置 isolation image trust root。**
  `OooPeople/tickets_hunter` 的 Actions variables 目前沒有 `UTIKI_P1_BASE_IMAGE`。
  `.github/workflows/utiki-p1-gate.yml` 的 browser／performance jobs 只接受經審查的
  `repository@sha256:<64-hex>`；空值或 mutable tag 會在 build 前非零退出。正式執行前，
  維護者必須核准 exact base-image digest、設定 repository variable，並讓 candidate 在
  GitHub Actions 執行五個 jobs 與 aggregate。本文不自行填入未核准 digest，也未修改
  repository 設定。

- **[P2／P1 gate blocker] source jobs 尚未在 `source-linux-x64` 對最終 clean candidate
  形成同輪 formal evidence。**
  modified-Python gate、sanitized `git archive` 與 formal run metadata 都要求 `HEAD` 等於
  candidate SHA、working tree clean、作業系統及架構符合 target。本文件中的 P1 本機回歸
  是開發驗證；以 clean SHA `8ef7983dab33ae383dfb5bbcc2172b69f873d730` 嘗試正式
  `unit-contract/source-linux-x64` 時，checker 正確以 `RUN_CONTEXT_MISMATCH` 拒絕 macOS
  arm64 證據。正式 gate仍須在Linux x64以最終 commit SHA重新產生source三個jobs的同輪
  evidence，再與isolated jobs組成aggregate。在此之前不能把45個host probe的開發驗證
  寫成45個formal obligation PASS。

- **[P3／已知邊界] P1 strict typecheck 證明 gate 能力，不宣稱 legacy production scope 已
  型別清零。** 受控 P1 fixture 以 `mypy --strict` 通過，漏列新 domain/application/port/
  adapter/bootstrap/config/keyword/core bridge/upgrader 與 broad ignore/Any escape 都會被
  直接 probe 擋下。現行 `auth.py`、`profiles.py`、`selection.py` 的 legacy strict debt
  仍有 206 個錯誤，另以 `production_scope_status=NOT_CLAIMED` 保存；完整 A01 production
  typed scope 從 P2 才到期，不能把受控 fixture 說成 production typecheck PASS。

- **[P3／規格邊界] P1 的 resource／guard 負向驗收限於 evidence chain 與 hard-egress
  harness。** H06 的 digest mismatch、H01 interception-disabled 與 isolation failure
  已有 P1 mapping。production JavaScript resource／guard 的 A04 obligations 依核准 catalog
  從 P3a 到期；P1 不提前改 catalog，也不以未到期項目宣稱已驗證 production JS parity。

未發現需要修改核准 catalog、v1.5 架構決策或 production uTiki 行為的其他 P0／P1
程式 blocker。P1 的 gate 判定仍為 **BLOCKED**，不可進入 P2 production migration。

## 已完成的信任基礎

- 固定 execution manifest 已由核准 catalog 與獨立 binding registry 產生：153 cases、
  341 obligations、490 execution keys；P1 有 63 個 `MAPPED`，其餘保持 `NOT_DUE`。
  重新生成的 byte digest 為
  `2067e93df310a8f74f7b0789e8842710be18a0b2e1d5226cea23621332dd3e1b`。
- 正式 pytest reporter、non-pytest／isolated assembler、run-index builder 與四輸入 checker
  已落地。required result 必須綁定 run／attempt／job／target／candidate、catalog、manifest、
  JUnit、resource與 producer evidence；skip、xfail、XPASS、setup／teardown error、0 collected、
  stale SHA、wrong layer及 failure exit 被覆寫都會被拒絕。
- 57 個 declarative negative requests 各有 reviewed argument/payload contract。runner 與最終
  checker 都會重算 canonical contract、input/output/producer digest與真實 child exit。
  對所有相同 rejection token 的跨 key relabel／reuse 做 pairwise 攻擊，接受數為 0。
- 45 個 host probes 已以 fresh input 真正執行原 producer：`unit-contract` 3、
  `architecture-types` 30、`modified-python-syntax` 12，全部取得預期的 nonzero child result
  與 `EXPECTED_REJECTION` envelope。isolated 12 只完成 dispatch／schema／fail-closed 測試，
  尚未列為正式執行。
- architecture evidence 現在綁定 canonical repository root、candidate SHA、完整 checked／target
  file set及逐檔 digest。以只有 benign `src/settings.py` 的暫存 root 產生 PASS payload 的
  假綠攻擊，已被 final checker 拒絕。
- browser／performance launcher 使用最小 Docker build context與 sanitized `git archive`
  workspace；不掛載原 checkout、`.git`、`src/settings.json`、browser profile或 artifacts。
  performance inner harness 以 outer candidate attestation 驗證無 `.git` workspace 的完整檔案
  集合及 digest。
- workflow 已定義五個 required jobs與 aggregate，沒有 `continue-on-error`；aggregate 重新核對
  同一 run／attempt／candidate的所有 job artifacts。`UTIKI_P1_BASE_IMAGE` 缺失或格式不符時
  明確 fail closed。
- deterministic harness 已提供 fake clock、controlled pending I/O、browser-effect sentinel與
  task leak guard；P1 尚未改動 production ticket workflow。

## P1 obligations 現況

| Job | 到期數 | 本機可核對結果 | Formal P1 狀態 |
|---|---:|---|---|
| `unit-contract` | 3 | 3 個真實 host negative probes通過；完整 pytest／unittest通過 | **BLOCKED：`source-linux-x64` CI未執行** |
| `modified-python-syntax` | 12 | 12 個真實 host probes通過；Git-derived manifest與逐檔 compile gate通過 | **BLOCKED：`source-linux-x64` CI未執行** |
| `architecture-types` | 30 | 30 個真實 host probes、canonical-root攻擊與 controlled strict mypy通過 | **BLOCKED：`source-linux-x64` CI未執行** |
| `browser-contract-isolated` | 13 | launcher／assembler／H04 fail-closed測試通過 | **BLOCKED：未執行 Linux hard isolation** |
| `performance-contract` | 5 | source contract與 no-`.git` attestation測試通過 | **BLOCKED：未執行 isolated Chromium/CDP measurement** |
| **合計** | **63** | 45 個 source obligation 的 harness可執行；18 個 isolated obligation未執行 | **aggregate 未通過** |

## Validation

- `python -m pytest --strict-config --strict-markers`
  - 610 collected；589 passed、2 skipped、19 deselected。
  - deselected包含 13 個 browser與 5 個 formal performance nodes；它們不算 PASS。
- `python -m unittest discover`
  - 359 tests，OK，1 skipped。
- `python -m pytest --strict-config --strict-markers tests/architecture -q`
  - 197 passed、1 skipped；skip為 case-insensitive filesystem條件，仍須由 Linux formal job履行。
- `python -m pytest --strict-config --strict-markers tests/performance/utiki -q`
  - 34 passed、5 deselected；5 個 formal V12／V13 nodes未執行。
- H04 host fail-closed nodes：2 passed。
- fresh host negative E2E：45／45 `EXPECTED_REJECTION`；same-token pairwise relabel：
  0 accepted。
- clean commit `8ef7983…` 的 formal unit source attempt：由於 host 是 macOS arm64而 target
  是 `source-linux-x64`，checker以 `RUN_CONTEXT_MISMATCH` 非零拒絕；沒有把跨target結果
  寫成PASS。
- execution manifest `--check`：153 cases、490 executions、63 mapped，digest與 checked-in檔一致。
- 修改／新增 Python 檔案 `py_compile`、workflow YAML parse、`git diff --check`：通過。
- 未執行 live site、登入、選票、提交、付款、release或部署；P1 也不授權這些操作。

## Readiness decision

P1 的程式、測試、隔離 launcher與 evidence chain 已具備進入正式 gate 的條件，先前的
execution manifest、內容驗證、negative probe可互換、architecture假 root、private checkout
mount及 no-`.git` performance 問題都已修正並加入回歸測試。

但 P1 尚未通過。下一個合法步驟是核准並配置 exact `UTIKI_P1_BASE_IMAGE`、推送最終
candidate，在 Linux GitHub Actions執行五個 jobs與 aggregate。只有 63 個到期 obligations
在同一正式 run 全部 PASS，才可進入 P2。

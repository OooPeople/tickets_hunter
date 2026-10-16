# uTiki v1.5 P0 Baseline Benchmark

## 狀態：離線 source baseline 已量測；browser／frozen 仍 BLOCKED

baseline SHA 為 `f307f7caba054fb40aab807a4629c891e69fb200`。P0 初次盤點時只有 suite
wall time；其後已建立離線 source benchmark 並在 production code 尚未修改時量測。這些
數字只涵蓋 synthetic parse／select／plan、cold import與 event-loop scheduling，**不**
冒充 CDP、真頁面、網站 request、native handler 或 frozen app latency。

## 1. 已有的非效能數字

| 項目 | 數值 | 用途 |
|---|---:|---|
| strict pytest | 354 passed、1 skipped、1 deselected；4.92 s | regression baseline，不是熱路徑 latency |
| coverage pytest | 同測試結果；8.73 s；total line coverage 24% | coverage baseline，不是 runtime latency |
| unittest discover | 355 tests；4.857 s | runner相容性，不是 runtime latency |
| main tick target | 50 ms | 靜態控制間隔，不是實測 event-loop lag |

## 2. 已完成的 source baseline

命令使用 `.venv/bin/python tools/benchmarks/bench_utiki.py --mode all --samples 300
--seed 1701` 的完整矩陣：
date／area 各 200 rows、seat 500、quantity 100；warm 300 samples／3 batches／每批
20 warmup；cold 每類 3 samples；event-loop 300 samples。環境為 macOS arm64、
CPython 3.11.15，run ID `p0-source-baseline-20261016`。

| Metric | median | p95 | p99 |
|---|---:|---:|---:|
| date cold import | 294.230 ms | 349.279 ms | 349.279 ms |
| date cold first operation | 4.795 ms | 10.148 ms | 10.148 ms |
| date warm parse/select/plan | 4.517 ms | 6.173 ms | 14.633 ms |
| area cold import | 499.692 ms | 651.614 ms | 651.614 ms |
| area cold first operation | 3.684 ms | 27.717 ms | 27.717 ms |
| area warm parse/select/plan | 2.039 ms | 3.930 ms | 6.806 ms |
| seat cold import | 361.221 ms | 376.109 ms | 376.109 ms |
| seat cold first operation | 3.334 ms | 3.517 ms | 3.517 ms |
| seat warm parse/select/plan | 2.887 ms | 4.692 ms | 9.468 ms |
| quantity cold import | 297.190 ms | 299.510 ms | 299.510 ms |
| quantity cold first operation | 0.349 ms | 0.417 ms | 0.417 ms |
| quantity warm parse/select/plan | 0.153 ms | 0.205 ms | 0.314 ms |
| event-loop scheduling lag | 0.0131 ms | 0.0343 ms | 0.0494 ms |

13 個 metric 共 1,524 samples，0 failure。原始 samples與完整 metadata保存在
[p0-source-benchmark.json](p0-source-benchmark.json)，報告 SHA-256 為
`00e3988d9713574b7dfb0f06c9783e8407648963a1889a8af4abb4eff3763a24`。執行時間為
`2026-10-16T10:37:43.318906+00:00` 至 `2026-10-16T10:37:53.700858+00:00`；run在共享
P0/P1 working tree執行，因此記為 candidate
`f307f7caba054fb40aab807a4629c891e69fb200`＋dirty digest
`491b235bd819ce3c184b2bcc517fbc71d77e900b2a00082a9efc7c3ec97cbe06`，不冒充 clean artifact。
硬體欄位固定為 arm64／8 logical CPUs／16 GiB physical memory；不保存 hostname。

報告固定的 digest如下：

| 輸入 | SHA-256 |
|---|---|
| fixture | `c5913c1588513e581fb9d86335988dd975c6006cb087e1b899eb4d869585cb71` |
| config | `81469ba41fb3318be716ab43d8c0eeda9d59aa5fed6b65a3f8f852480e45f218` |
| benchmark script manifest | `cf499fba329e6d8d1b5da29cdc87be86017d75571fb8fcfe75a7b0127e57ea8e` |
| subject import graph | `2bf87711ea24639da7871cf435825c735508361bf3c289b7852c11a9c52e74cf` |

subject digest涵蓋實際 eager import graph的14個 repository `src/*.py`，包含 workflow、DOM、
refresh、settings與其本地依賴，完整排序路徑記在 JSON，沒有再以三個手選檔案代表 cold
import成本。後續 baseline/candidate比較只在 method digests、parameters、Python與OS metadata
完全相同時回 `comparable: true`；不同環境或方法直接非零，不以 warning冒充可比較。

## 3. 必須補齊的 browser／artifact 欄位

| 欄位 | P0 狀態 |
|---|---|
| cold/warm 分組 | `SOURCE_MEASURED` |
| date/area/seat/quantity fixture latency median/p95/p99 | `SOURCE_MEASURED` |
| ready action 到 effect dispatch latency | `NOT_MEASURED` |
| CDP command count | `NOT_MEASURED` |
| 站點 request count | `NOT_MEASURED` |
| event-loop lag median/p95/p99 | `SOURCE_MEASURED` |
| failure/retry count | `SOURCE_MEASURED: 0` |
| CPU與記憶體 | `NOT_MEASURED` |
| SQLite單／多程序 contention | `NOT_MEASURED` |
| slow log/notification sink干擾 | `NOT_MEASURED` |
| source/frozen、Windows/macOS | `NOT_MEASURED` |

因此 source baseline 已固定，但 P1 performance/browser gate仍不能標 PASS。CDP、
production-origin、request count、native與 frozen 必須等 hard-egress harness及各 target
artifact完成，不能以 source數字代填。

## 4. 後續 browser／candidate 方法

1. 使用 baseline/candidate 不同 checkout、暫存 config/profile/SQLite DB，固定 Python、browser、
   OS、硬體與 fixture digest。
2. date、area、reserved seat、general quantity分開；server assignment只在既有支援範圍測。
3. 每組記 cold/warm、fixture size、設定 mode、樣本數、median/p95/p99、失敗/重試、CDP與
   request count。
4. browser fixture必須有 CDP 之外 hard-egress deny與故意破壞 interception 的負向 canary；
   未具備前不啟動 browser benchmark。
5. warm-up與量測批次分開；baseline/candidate交替順序，不能只挑 candidate最快批次。
6. ready purchase、ordinary refresh、scheduled action、unknown submit、slow notification等碰撞
   需分情境，不能用取消 safety barrier換低 latency。
7. JSON artifact至少記 baseline/candidate SHA、source/script digest、Python/browser/OS、硬體、
   run id、clock、fixture/config digest與所有 raw aggregate；不含 secrets或完整真實頁面。

## 5. 初始比較原則

- 沒有事前證據時不設定武斷的百分比加速目標。
- candidate 不得實質退化 ready action latency、CDP/request count或 event-loop responsiveness。
- correctness evidence相同後才比較速度；少做必要 audit不能算改善。
- timeout是上限，不計成每次固定成本；網站/network時間與本地 compute分開。
- P7不是第一次看效能：P1補 baseline後，每個熱路徑切片都應用相同方法成對比較。

## 6. 已建立工具

`tools/benchmarks/bench_utiki.py` 與 `compare_utiki.py` 已建立，聚焦測試 18 passed，
逐檔 `py_compile` 通過。計劃中的 `--mode`、`--seed`、`--baseline`、`--candidate` CLI均已
實跑；比較器核對 fixture/config/script/plan identity、parameters與 execution environment，
逐 metric揭示 regression，不設定無根據的通過百分比。兩個工具仍須納入 modified-Python、execution
manifest與四輸入 gate；browser/CDP/frozen/native欄位在具備隔離與artifact前保持
`BLOCKED`。

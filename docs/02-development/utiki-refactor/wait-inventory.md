# uTiki v1.5 P0 Wait Inventory

本文件把 baseline 的等待依原因分類。數值是程式設定的上限／poll 間隔，不是實測 latency；
網站或瀏覽器可提早完成。不得將 timeout 全數相加，亦不得把 pytest wall time當購票延遲。

## 1. Core dispatch／control

| 位置 | 等待 | 類型 | 說明／風險 |
|---|---:|---|---|
| `nodriver_tixcraft.py` main loop | 0.05 s/tick | 控制輪詢 | 所有未遷移平台回主迴圈；新 worker不應多付一輪才處理 ready receipt |
| paused main | 0.1 s/tick | pause control | pause source維持共用；不能在每 effect 重新讀檔 |
| Cloudflare gate active/failed | 0.1 s/tick | core challenge | 在現行 purchase reconciliation 前；P5 需 revoke/handoff |
| uTiki health unknown | 0.25 s/tick | core health | 外層等待後再檢；現行另有 family 內第二次 health |
| uTiki health blocked | 0.1 s/tick | core health | coordinator/recovery 持續推進 |
| refresh_datetime gate | 0.1 s/tick，或 sleep 到 target | scheduled control | target action不能越過 unresolved purchase barrier |
| config hot reload debounce | 0.1 s | core I/O | 目前會做 runtime migration；target strict loader不得轉換 |

啟動頁面和其他平台另有 1.0–2.5 秒 human-like waits；uTiki cookie reload後固定等待
1.0–1.5 秒再讀 login state。這些是 startup/core wait，不能算 domain reducer成本。

## 2. Shared Cloudflare helper

| 位置 | 等待 | 說明 |
|---|---:|---|
| CDP mouse press -> release | 0.05 s | 實際 challenge click，屬 mutation |
| token/clearance poll | 0.25 s，最多 6 s | token、field gone或URL change任一成立可提早完成 |
| retry backoff | 第 2/3 次為 4/5 s | `3 + retry_count`，最多三次 |
| no-click fallback | 3 s 加 retry增量 | template/text定位後的 challenge observation |

P5 不必重寫此 helper，但 uTiki tab 在呼叫會 click 的路徑前必須取得正式 handoff；只讀
detector可留在 core。

## 3. 平台固定 sleep／readiness waiter

| 位置 | 等待 | 類型 | 可提早完成 |
|---|---:|---|---|
| date snapshot retry | 10 x 0.2 s，最多約 2 s | readiness polling | 是，rows ready即停止 |
| area DOM observer | 20 ms poll，2.5 s timeout | browser readiness | 是，snapshot complete即停止 |
| destination state observer | 25 ms poll，2.5 s timeout | browser readiness | 是，必要 IDs ready即停止 |
| fast area prefetch | 1.2 s abort timeout | website fetch | 是，fetch完成即停止 |
| dialog close observation | 10 x 0.1 s，最多約 1 s | result observation | 是，dialog消失即停止 |
| CAPTCHA image ready | 10 x 0.1 s，最多約 1 s | input readiness | 是，image ready即停止 |
| invalid OCR image refresh | 固定 0.3 s | input recovery | 否；現行 sleep後沒有 matched reset receipt |
| submit first observation | 固定 1.0 s | result observation | 否；現行無 immediate event wakeup |

`CTBC_SUBMIT_OUTCOME_TIMEOUT_SECONDS=10`、fast marker TTL 15 秒與 pending navigation TTL
15 秒是 lifecycle deadline，不是每次必付 sleep。deadline到期也不能自動解鎖未知 effect。

## 4. Refresh／coordinator／cooldown

| 位置 | 等待／期限 | 說明 |
|---|---:|---|
| ordinary local eligibility | 使用者 interval；缺值/非正數時 1 s | 建 request 時固定，resume不得重算 |
| reserve retry | 至少 0.05 s | 使用 coordinator `retry_after` |
| cooldown polling | 最多 1 s/次 | 以剩餘 cooldown 限縮 |
| active recovery probe retry | 至少 1 s | 不得新增平行 probe |
| reload waiter poll | 0.1 s | startup/cookie等 bounded waiter |
| reload waiter deadline | 30 s | 到期 final observation後 cancel/held；不等於 browser未呼叫 |
| final observation | 0.25 s | deadline邊界最後一次 document guard |
| browser reload dispatch timeout | 120 s | `asyncio.wait_for(tab.reload())`；return不等於新 document |
| recovery lease renewal | 每 5 s | lease task只應回 receipt，不直改 target state |
| recovery lifecycle max | 120 s | 到期停止 renewal；未知不能重送 |
| pause cleanup retry | 0.5 s | exact-token cleanup |
| emergency marker base | 6 s | coordinator report失敗時跨程序保護 |
| 429 initial/max cooldown | default 60/240 s | global settings控制；不是本 tab任意 reload權 |

## 5. 初始化與外部原生處理

- OCR provider在 main 啟動時建立；目前未量 cold model-load時間。
- SQLite coordinator open/schema/transaction latency未單獨量測。
- browser/CDP startup、first document、PyInstaller cold start均未量測。
- 網站 network、原生 dialog/navigation、Cloudflare服務端處理時間不可由本地固定 sleep推算。
- notification、sound、log sink目前可能同步阻塞；離線 source event-loop lag已量測，
  但這些 sink 對 browser主迴圈的 p95/p99干擾仍未量測。

## 6. 已知重複或可改善等待

1. outer main 與 family main 都呼叫 health check；需先量 CDP count，再合併 coherent
   observation，不能只因看見兩個函式就聲稱固定多一次 request。
2. submit固定 1秒等待應改為 immediate receipt/observation wakeup＋有界 fallback，不可透過刪除
   unknown evidence換速度。
3. CAPTCHA invalid結果的0.3秒固定等待應併入 `CaptchaResetAttempt` matched evidence。
4. readiness observer可保留早回；不可改成每輪多一次 CDP audit。
5. 新 arbiter只做記憶體內固定類別檢查，不得新增 browser/SQLite round trip。

## 7. P0 量測狀態

synthetic source cold import／first operation、warm parse-select-plan 與 event-loop scheduling lag
已量測並保留原始 JSON。以下仍為 `NOT_MEASURED`：browser page-action latency、CDP command
count、站點 request count、CPU、SQLite contention、notification/log sink干擾、source/frozen
差異。量測方法、數值與缺口見 [baseline-benchmark.md](baseline-benchmark.md)。

# uTiki v1.5 P0 現行行為契約

本文件描述 baseline `f307f7caba054fb40aab807a4629c891e69fb200` 可從程式與既有測試
確認的行為。它不是 target design，也不把尚未執行的真站流程寫成已驗證。

## 1. Route 與 tenant

- 只接受 allowlist 內 tenant 的精確 HTTPS origin；內嵌 username/password、非 443 port
  或未知 host fail closed。
- 現行 tenant 為中信育樂與味全龍；CTBC cookie/account/password 只屬中信 tenant。
- `PageKind` 由精確 path、唯一 query key 與必要 ID 格式分類；不完整、重複 query、
  fragment 或未知 route 回 `UNKNOWN`。
- 目前頁型為 date、area、reserved seat、general admission、server-assigned quantity、
  checkout 與 unknown。

## 2. Main-loop ordering

現行每約 50 ms 一輪，uTiki 路徑順序為：

```text
settings hot reload
-> pause/stop/URL
-> route-leave cleanup / reload-event consumption
-> global Cloudflare detect and possible click
-> outer uTiki health check and possible 429 recovery
-> refresh_datetime scheduled reload gate
-> nodriver_utiki_main
   -> inner pause and health check
   -> purchase reconciliation
   -> current page action
```

因此外層 challenge、health、scheduled reload，以及 family handler 內先於 purchase
reconciliation 的 health 都是現行可觀察行為；它們尚未經 v1.5 session arbiter。

## 3. 現行 state ownership

- `workflow._state` 保存 `tabs` registry 與 refresh cancel-task set。
- 每 tab runtime 是可變 dict，含 document、action、transition、purchase、refresh。
- tab identity 優先採 target id，否則把隨機 token 固定到 tab object。
- `refresh.py` 不持有第二個 registry，但接收並直接修改 workflow runtime；lease task 也會
  修改 refresh 子狀態。這是 single registry，不是 target 的 single writer。
- 離開 family route、stop、driver missing 或 handler exception 會 cleanup；現行 cleanup
  先從 registry 移除 runtime，再等待 refresh cancellation。

## 4. Selection 與 navigation

- selection 採 inspect -> pure select/audit -> guarded DOM commit；URL、document、control
  fingerprint 或 plan 失效時應 fail closed。
- 日期與區域可建立 pending navigation；fast path 使用 sessionStorage marker 作輔助
  evidence，marker 不是完整 runtime owner。
- fast area prefetch 有 1.2 秒 fetch timeout；fast path freshness TTL 為 1.5 秒。
- pending navigation TTL 為 15 秒。未知結果到期可要求 reload；目的不符可
  `location.assign` 返回 native area。
- 現行 `RETURNING` 路徑沒有 session claim；同一 pending 在後續 tick 仍可能再次提出
  return。v1.5 必須以一次性 effect ownership 取代，不可把現況當 target 契約。

## 5. Purchase 與 submit

- 現行 purchase 記錄為 `active_submit`、`outcomes`、`last_submit` 與
  `checkout_notified`，不是 tagged `PurchaseState`。
- submit 前會建立 nonce、source URL/document、page kind、product 與 payload fingerprint。
- DOM submit 回 `DISPATCHED/REJECTED/STALE`；exception 會保存 unknown outcome，禁止同一
  runtime 自動重送。
- dispatch 後固定等待 1 秒再觀察；10 秒內沒有結果維持 pending，之後轉 unknown 並
  阻擋自動重送。
- checkout URL 或可信 dialog 可完成 success；captcha/auth/seat-conflict 進各自的現行
  recovery dict。
- checkout 取消 refresh、以 submit/location key 去重通知；到達付款頁只通知，付款必須
  由使用者手動完成。

## 6. Input、login 與 CAPTCHA

- OCR 關閉時保留選擇並等待使用者手動輸入；OCR 開啟但 `force_submit=false` 時可填值，
  最終 submit 仍由使用者手動觸發。
- OCR answer 正規化為四個大寫 ASCII 字元；答案不應寫入 log。
- 現行 CAPTCHA image token 是圖片 bytes 的 SHA-256；它不單獨證明 server challenge
  generation 已更新。
- CAPTCHA refresh 以 before token、source document 與 reset phase 做有限歸屬；點擊
  失敗可成為 unknown。
- 現行 login 直接設定 DOM value；CAPTCHA 使用清值加 `send_keys`。兩者尚未接核心唯一
  native setter、typed `InputWriteReceipt` 或完整 framework readback，屬 target gap。

## 7. Refresh 與 429

- logical request 含唯一 request token、非空 reason、source document、URL、eligible time、
  deadline、reservation 與 phase。
- 啟用共享保護時，依 SQLite coordinator reserve -> preflight -> commit；停用共享保護時
  仍保留本機 interval、document 與 pause/quit gate。
- ordinary local interval 取使用者 `auto_reload_page_interval`；缺值或非正數時為 1 秒。
- 429 incident 使用 `UTK_SPORTS_SCOPE = "utk-sports"`，保留 cooldown、single recovery
  probe、lease renewal 與 emergency marker。
- browser command return 不算 reload 完成；必須觀察不同 canonical document token。
- 現行公開結果仍折成 bool/status string：`False` 可能表示未 eligible、未 reserve、commit
  不明、browser 已 attempted 或仍等待 document。這不是 target `ReloadHandle` 契約。

## 8. Pause、stop 與 late results

- pause 不應重算 logical refresh eligibility 或延長 deadline；queued/reserved 共享工作會做
  exact-token cleanup。
- 已 awaiting-document 的 request 不因 pause 回捲成 queued。
- stop 會 cleanup tab 後關閉 browser；關閉不代表已送出的遠端操作被撤回。
- 現行 detached runtime 沒有 target 的 generation retirement、RuntimeStatus 或 retained
  unresolved record；這是 P4/P5 lifecycle 必須補齊的差距。

## 9. 設定格式現況與 adopted target

現行 production 行為：

- default、save 與 migrate 同時維護 `utiki` 與 `ctbc` behavior sections；
- canonical key 優先，缺 key 時從 legacy 補入；最後再把 canonical allowlist 鏡像回 legacy；
- UI 與 workflow 都有 canonical -> legacy fallback；hot reload 在 runtime 呼叫 migration。

已採用 ADR-02 的 target 不同：正式 runtime 只接受 canonical `utiki` behavior section；
legacy-only 與所有 mixed 格式只能由 offline upgrader 處理。帳號/Cookie 的 `ctbc_*` 外部
契約不是 legacy behavior mirror。切換前須以測試固定 canonical-only、legacy-only、
mixed-equal/complementary/conflict 與 empty 的唯一分類；不得悄悄覆寫使用者原檔。

## 10. 不可用既有測試過度宣稱的項目

- 既有 pure/contract/fixture tests 不等於 hard-egress browser isolation。
- collection 成功不等於 browser 執行成功。
- 單元測試不證明真站 DOM、限流、導頁、提交或付款流程。
- coverage 百分比不證明 v1.5 的 state×event、collision matrix 或 required catalog 已完成。

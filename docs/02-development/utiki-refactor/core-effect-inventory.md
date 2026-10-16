# uTiki 核心副作用盤點

## 基準與目的

本盤點對應 [Issue #17](https://github.com/OooPeople/tickets_hunter/issues/17)、分支
`refactor/17-utiki-root-v1-5`、actual HEAD
`f307f7caba054fb40aab807a4629c891e69fb200`。目的是找出位於 uTiki handler 外、仍可能
改變 tab、document、coordinator、設定授權或外部通知的路徑，避免新 engine 接入後保留
第二條 effect owner。

Upstream 基準為 local
`upstream/main@9e2d1061b48706076943b1059616347c389df809`，merge base 相同；主代理已於
2026-10-16 18:00:08 +0800 fresh fetch，remote `main` 當時仍為此 SHA。

## Main／current-core effect

| ID | 基準路徑／符號 | 現有 effect | 新 owner／契約 | P0 裁決與必要驗收 |
|---|---|---|---|---|
| CE01 | `nodriver_tixcraft.nodriver_goto_homepage` `:127-418` | `driver.get`、Cookie delete/set、部分平台直接 reload；uTiki Cookie 後呼叫 shared reload | main 建 engine／attach session 後，由 credential/browser port 與 engine `request_reload` 執行 uTiki 部分 | startup 也要 claim/evidence；Cookie setter成功不等於 session有效；不得在 engine 外另 reload |
| CE02 | `_should_inject_ctbc_cookies`＋`nodriver_ctbc_inject_cookies` | 讀 credential、刪舊 Cookie、寫新 Cookie | credential provider＋browser credential transaction | 只允許對核准 origin/domain；回報 delete/set/verification evidence；秘密不進 log/state |
| CE03 | `check_refresh_datetime_gate` `:648-738` | 設定改變時 cancel refresh；到點時 uTiki reload，其他平台直接 `tab.reload()` | uTiki 走 engine control revision＋`request_reload`; 其他平台保持原路徑 | ready purchase 與 scheduled reload 衝突由 arbiter 決定；已 commit effect 不回捲；不提高刷新頻率 |
| CE04 | `_advance_cloudflare_gate` `:799-918` | document read；`handle_cloudflare_challenge` 可能 click／改變 document | detector 可只讀；對已 attach uTiki session 的 mutation 必須先 revoke/handoff 給 core challenge owner | challenge-owned gate 禁止 purchase/input/ordinary refresh；handoff 後晚到 callback 只做原 operation cleanup |
| CE05 | `reload_config` `:740-783` | 讀檔並替換 runtime policy；舊 migrator會雙讀／雙寫語意 | current-config validator＋control revision；engine 接收成功的新 policy | invalid/mixed 不自動轉換；保留最後有效 policy但撤銷新 effect；已送 attempt 繼續歸納 |
| CE06 | `_cleanup_utiki_if_driver_missing` `:120-124` | 清除 uTiki tab runtime | engine `close(reason=driver_missing)` non-allocating lookup | 不因 cleanup lookup 建新 session；停止 owned tasks、有界 join、保留 unknown outcome |
| CE07 | main stop path `:1078-1096` | uTiki cleanup 後 `driver.stop()` | engine close/revoke 完成或有界 timeout後由 core stop browser | 明確 stop 可終止 browser，但不得宣稱撤銷遠端 request；保存脫敏 unknown report |
| CE08 | URL 離開 family `:1133-1138` | cleanup tab runtime | engine `detach/close` | checkout 不等於離站；unknown submit 仍保持分類；close 後舊 callback不得復活 session |
| CE09 | reload event consume `:1146-1153` | 破壞性 consume 事件並重設外層 Cloudflare state | engine 提供 operation-scoped completion/status；core 只讀取匹配 handle | 一個 completion 不得交給另一 caller；不得用裸 bool 交錯 ownership |
| CE10 | pause path `:1155-1161` | pause refresh、延後 loop | control gate＋engine revoke/pause cleanup | queued/reserved exact cleanup；committed request維持觀察；pause期間禁止新 mutation |
| CE11 | outer health `:1184-1208` | document inspection、429 report/recovery、可能 reload | engine health reducer/controller，main 只傳 control/observation | 移除舊 handler 內重複 health；同 cycle 合併 observation；unknown fail closed |
| CE12 | uTiki main route `:1241-1248` | 日期／區域／座位／輸入／submit／refresh 等所有平台 effect | 唯一 engine session worker／ActiveEffect | main 不再呼叫舊 alias；exception 走同一 close contract；同 tab 不得雙 dispatch |
| CE13 | heartbeat／last URL state writes `:1032-1071,1139-1140` | 本機檔案寫入 | core control/observability，非 engine purchase effect | 不進購票 hot path的阻塞鏈；不得寫 secrets；失敗不授權重試購票 |
| CE14 | checkout notifications `platforms/utiki/workflow.py:3471-3491` | 音效、Discord、Telegram | accepted outcome後的窄 notification port＋獨立有界工作 | PENDING/ATTEMPTED/DELIVERED/FAILED/UNKNOWN 分離；通知失敗不重新購票 |
| CE15 | shared refresh DB operations `platforms/utiki/refresh.py` | reserve、commit、poll、429 report、lease renew/release/cancel | refresh port＋engine refresh reducer | 保留 SQLite 線性化與 exact token；不得 mirror DB、清 cooldown/lease 或用 memory cache取代 |
| CE16 | inline login/captcha/quantity/seat input | 寫入 input、dispatch event、可能觸發網站 handler | browser input transaction＋Purchase/Input state，使用唯一 native setter | document/input/image generation matched；setter false/回讀失敗/target replaced 禁止 submit |

## 所有權規則

1. uTiki session attach 後，任何會 click、submit、reload、navigate、dismiss/reset input、
   replace document 或改變 coordinator request phase 的操作，都必須取得 engine claim 或完成
   明確 handoff。
2. 只讀 detector 可以留在 core，但其結果不是 mutation 權限；document/control revision 改變
   後舊結果失效。
3. `PURCHASE_ALLOWED` 只是必要條件，不能越過 held/unknown purchase、input blocker 或已存在
   ActiveEffect。
4. effect 例外不自動等於 `NO_EFFECT`；需回報 local mutation、browser attempted、request
   dispatched 或 unknown evidence。
5. core stop/force-close 可終止 browser，但不能撤回已送網站 request。
6. notification、log、heartbeat 不得同步阻塞 ready→dispatch 路徑。

## P1/P4/P5 驗收清單

- 為 CE01–CE16 建立 caller/operation ID、控制 revision、effect evidence 與 cleanup mapping。
- 測試 startup、scheduled refresh、ready purchase、challenge、pause、stop、URL leave、handler
  exception、late receipt 的 collision matrix。
- 驗證 outer health 與 page handler 不再各自發同一輪重複 observation/recovery。
- 驗證每條 direct `tab.reload()`/click/submit 在 uTiki attach 後皆不可繞過 engine。
- 驗證 configuration invalidation 不清掉已發 attempt，也不允許舊 policy 發新 effect。
- 對 coordinator 做真 SQLite 多程序、late cleanup、lease loss 與 crash 測試。
- 對 input 做 native setter false、framework未ready、target replacement、late OCR/reset 測試。

## 授權狀態

本文件只是 inventory，不是執行清單。目前未授權真站 click/reload/login/submit，CE 項目的
production-site 驗證一律 `LIVE_BLOCKED`；沒有 P6b 證據不得解除
`FINAL_DELETE_BLOCKED`，也不得發布。

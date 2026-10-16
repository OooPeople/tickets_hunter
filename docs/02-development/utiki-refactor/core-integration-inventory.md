# uTiki current-core 接合盤點

## 基準與用途

本盤點服務於 [Issue #17](https://github.com/OooPeople/tickets_hunter/issues/17) 的
`refactor/17-utiki-root-v1-5` 分支，用來區分「刪除 legacy 後仍存在的 current-core
能力」與「不得帶入新 runtime 的歷史棒球相容」。

| 項目 | 固定值 |
|---|---|
| Actual HEAD | `f307f7caba054fb40aab807a4629c891e69fb200` |
| Origin | `OooPeople/tickets_hunter@f307f7caba054fb40aab807a4629c891e69fb200` |
| Upstream URL | `https://github.com/bouob/tickets_hunter.git` |
| Local upstream ref | `upstream/main@9e2d1061b48706076943b1059616347c389df809` |
| Merge base | `9e2d1061b48706076943b1059616347c389df809` |
| Ahead／behind | 52／0（相對上述 local ref） |

主代理已於 2026-10-16 18:00:08 +0800 成功 fetch `upstream`；當次
`FETCH_HEAD` 顯示 remote `main` 仍為上列 SHA。因 ref 內容未變，reflog 沒有新增
update entry；本基準以當次 fetch 結果為準。

## 裁決標記

- `KEEP`：刪除 legacy 後仍為跨平台／current-core 所需。
- `DIRECT`：現有 callable/data shape 可直接注入新 port，不建立 bridge。
- `CONDITIONAL_BRIDGE`：只有 P2 port 定案後證明 shape 不合，才在模組外薄映射。
- `REMOVE`：歷史 API/state/fallback，不得進新 runtime 或 bundle。

## Current-core 能力

| 能力／來源 | 現有真實 caller | legacy 刪除後需求 | 接合裁決 | 約束／驗收 |
|---|---|---|---|---|
| `refresh_coordinator.UTK_SPORTS_SCOPE`、`get_shared_refresh_coordinator()`、`SharedRefreshCoordinator` reserve/commit/poll/report/renew/release/cancel | `platforms/utiki/refresh.py`；settings/global policy 間接控制 pacing | 保留跨程序 interval、cooldown、lease、429 recovery 與 exact-token cleanup | `KEEP`＋`CONDITIONAL_BRIDGE` | 新 port 不改 DB/schema/scope，不建立 mirror coordinator；typed result 不壓成 bool；若方法 shape 已吻合則直接注入 |
| `global_settings.get_global_settings_path/load_global_settings` 的 `utk_sports` policy | `platforms/utiki/refresh.py:40-44`；settings GlobalSettingsHandler | 保留使用者可調的共享刷新政策 | `KEEP`＋優先 `DIRECT` | 冷路徑／revision 變更時讀取；hot path 不每輪讀檔；engine 不 import settings UI |
| `nodriver_common.check_and_handle_pause`、`check_and_handle_quit` | main 與舊 refresh/workflow | 保留全程 pause/stop 控制 | `KEEP`＋`DIRECT` | 主入口轉成帶 revision 的 control gate；不得讓每個 effect 重讀檔；pause/stop 必須 revoke／cleanup |
| `nodriver_common.CONST_NATIVE_INPUT_SETTER_JS` | ibon、ticketplus；uTiki 基準尚未正確共用 | 保留跨框架輸入 setter 唯一來源 | `KEEP`＋`DIRECT` | composition root 提供 fragment/digest；browser assembler 放入 IIFE；false/例外/回讀不符禁止 submit；不得複製第二份 body |
| `nodriver_common.nodriver_get_captcha_image_from_dom_snapshot` 與受支援 image source | uTiki workflow 及其他 OCR 流程 | 保留現有 OCR/image capture capability | `KEEP`＋`DIRECT` 或窄 browser adapter | 回傳 evidence 綁 document/input generation；不得引入驗證繞過；late result 不授權新 submit |
| `nodriver_common.send_discord_notification`、`send_telegram_notification`、`play_sound_while_ordering` | uTiki checkout 與其他平台 | 保留既有通知／音效能力 | `KEEP`＋`DIRECT` | 只注入窄 callable；attempted 不冒充 delivered；通知不得阻塞購票 hot path |
| `util.create_debug_logger` | main、workflow、refresh 與其他平台 | 保留既有脫敏 log sink | `KEEP`＋`DIRECT` | domain 不 import logger；不記錄 Cookie、密碼、token、完整 DOM/payload |
| `util.get_instance_id/get_app_root/get_instance_state_path`、process clock/identity | refresh identity、emergency marker、heartbeat/state files | 保留 instance isolation 與必要 filesystem 位置 | `KEEP`＋`DIRECT` 或極薄 provider | domain 不自行讀環境／檔案／時鐘；路徑限制於 app runtime；不建立第二份 session identity |
| `zendriver` tab/CDP | main 與所有 browser platforms | 保留具體 browser adapter | `KEEP`，不屬頂層 integration framework | application/domain 不取得 unrestricted evaluate/navigation；mutation 回具體 effect evidence |
| `config_dict["accounts"]`＋`platforms.utiki.auth` tenant mapping | Cookie 注入、inline login | 保留 current tenant credentials | `KEEP`＋credential provider | engine 不持有完整 raw config/accounts；secret material 最短生命週期；`accounts.ctbc_*` 不屬 legacy mirror |

## `utiki_core_services.py` 建立條件

目前核准的是「可建立的精確位置」，不是預先要求該檔存在。只有同時符合以下條件才
建立 `src/utiki_core_services.py`：

1. P2 已定義窄 typed port。
2. 上表某 current-core interface 的真實資料／生命週期與 port 不相符。
3. 直接從 main 注入 callable/value 會造成重複 mapping 或錯誤 ownership。
4. 適配只做 shape/lifecycle translation，不持有 PurchaseState、不選票、不直接 reload、
   不解碼 legacy config、不建立 mirror state。
5. 有 caller、contract test、延遲與回退證據。

若建立，只有上表核准 symbols 可 import；不得用 wildcard 或放行整個
`nodriver_common`、`settings`、`platforms.utiki.workflow`。

## 明確不是 current-core 例外

以下項目裁決為 `REMOVE`，不得搬到 bootstrap、services、util 或 tests 以繼續存在：

- `src/platforms/ctbc.py` facade。
- `nodriver_ctbc_main`、`nodriver_utiki_main` alias 及舊 `nodriver_utk_sports_*` 公開函式群。
- `is_utk_sports_url` 歷史 alias；新程式使用 current profile/router API。
- workflow module-global `_state`、legacy tab runtime、dict snapshot/model/result projection。
- `_get_utiki_behavior()` 的 `ctbc` fallback、settings migrator mirror 與 UI 雙寫。
- 舊 private test patch bridge、失效 hidden import 與 inline script fallback。

真實 CTBC domain、credential keys、Cookie 名稱、tenant profile 與 `utk-sports` coordinator
scope 是 current domain/protocol，不因 legacy API 使用相同字串而刪除。

## 待 P2／P5 補證據

- 每個新 port 的完整 signature、timeout/cancellation/result shape。
- coordinator direct injection 與薄 bridge 的實測比較及最終選擇。
- credential revision、AuthEpoch 與 hot reload 的 owner。
- notification transport 的可靠 receipt 能力；沒有可靠 receipt 時只標 attempted。
- native setter fragment/digest 的 frozen resource manifest 位置。
- core-effect inventory 中每條 main side effect 的 handoff/revoke/cleanup contract。

## 授權狀態

本盤點不授權真站操作、P6c 最終刪除或公開 release。相關狀態維持
`LIVE_BLOCKED / FINAL_DELETE_BLOCKED / RELEASE_BLOCKED`，直到各 gate 取得獨立授權。

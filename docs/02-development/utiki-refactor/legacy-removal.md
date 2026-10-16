# uTiki v1.5 P0 Legacy Removal Inventory

本表只登錄候選、依賴與刪除 gate；**不授權現在刪除**。最終 legacy 實作只能在 P6b
指定真站 smoke 與 final-delete 覆核後，於 P6c 移除。

## 1. 最終應移除的入口與模型

| Legacy candidate | 現行依賴／用途 | Target replacement | 最早刪除 gate |
|---|---|---|---|
| `src/platforms/ctbc.py` | 舊公開 facade；tests 與 spec 仍引用 | main 直接使用 `platforms.utiki.bootstrap.create_engine(...)` | P6c |
| `platforms.utiki.__init__` 的 workflow eager re-export | main 直接 import 九個函式；package import 會載入 effectful workflow | 輕量 package；不 re-export runtime API | P6c |
| `nodriver_utiki_main = nodriver_ctbc_main`、`is_utk_sports_url` alias | main、legacy tests | engine handle、typed profile/classifier API | P6c |
| `nodriver_utk_sports_*` reload/health/pause/cancel wrappers | startup、scheduled、health、cleanup | engine request/control API＋exact `ReloadHandle` | P6c；P5 先完成接入 |
| `workflow._state` 與巢狀 dict runtime | 現行 tab registry、purchase/navigation/refresh state | engine instance registry＋typed `SessionState` | P6c；P4/P5/P6a 必須先證明唯一新 owner |
| `active_submit/outcomes/last_submit` dict model | submit/recovery/notification tests | typed `PurchaseState`、attempt/cycle/recovery records | P6c |
| `UTK_SPORTS_PROFILES` 與 `TenantProfile.as_legacy_dict()` | `_get_utk_sports_profile` 等 dict projection | typed `TenantProfile` direct return | P6c |
| `_get_utiki_behavior()` legacy fallback | workflow 讀 canonical/legacy | validated current policy | P6a 切換、P6c 刪 fallback |
| `ticketsHunter.ctbc.fast-navigation.v1` marker schema | navigation fixtures與舊分頁 | 新 operation/generation-bound evidence schema | P6c；不承接舊分頁 intent |
| inline production JS 與舊 DOM adapter | workflow/dom 內的大型 scripts | versioned script resources＋browser/codec ports | P3a 建新資源；P6c 刪舊路徑 |
| workflow private test bridge | tests 直接 import `_ctbc_*`、讀寫 `_state` | public typed contract/ports/fakes | P6c |

## 2. 設定 legacy

| Candidate | 現行位置 | Target |
|---|---|---|
| `ctbc` behavior mirror | `settings.get_default_config()`、`migrate_config()` | 正式檔只保存 canonical `utiki` behavior |
| runtime migration | settings load/save、main hot reload | strict current validator；不轉換、不覆寫 |
| UI canonical->legacy fallback與雙寫 | `src/www/settings.js` | UI 只讀寫 canonical behavior |
| legacy/mixed profile input | 現行自動 normalize | source/frozen offline upgrader，另存輸出並保留原檔 |

`accounts.ctbc_cookie`、`accounts.ctbc_account`、`accounts.ctbc_password` 是真實中信 tenant
外部設定，不因移除 behavior mirror 而改名或刪除。

## 3. Build／test references

P6c 前要逐一收斂：

- `build_scripts/nodriver_tixcraft.spec` 的 `platforms.ctbc`、舊 workflow/leaf hidden imports；
- `tests/test_ctbc_pure.py` 對 facade identity/import order 的 assertions；
- `tests/ctbc_support.py` 的 production private runtime access；
- `tests/test_ctbc_dom.py`、`test_ctbc_dom_fixtures.py`、`test_ctbc_refresh.py`、
  `test_utiki_dom_contract.py` 對 `_ctbc_*` helper與可變 dict的 patch/read/write；
- `tests/fixtures/ctbc/` 可保留脫敏 DOM evidence，但應搬到 current uTiki fixture naming，
  且不能用 rename 降低 required obligations。

歷史測試在新 contract 有等價或更強 evidence 前不得先刪；P6c diff 限定 legacy removal 與
reference update，不能夾帶新的 runtime 行為。

## 4. 明確保留的 current-core 契約

以下不是 legacy facade：

- `UTK_SPORTS_SCOPE = "utk-sports"` 與既有 SQLite schema/interval/cooldown/lease；
- `refresh_coordinator.py` 的跨程序節流責任；
- 核心唯一 native setter body；
- 經 caller inventory 證明仍被其他平台使用的 keyword/util 公開入口；
- 共用 pause/stop/settings、OCR provider、notification 及 Cloudflare 能力；
- CTBC tenant/domain/auth/account/cookie 語意；
- PyInstaller bot/settings release targets。

保留責任不等於允許直接繞過 engine。P5 應以窄 ports 或 main-side current-core provider
接入；provider 不持有 PurchaseState、不改 gate，也不直接 reload browser。

## 5. 禁止留下的過渡形式

- 舊 facade 呼叫新 engine 的永久 wrapper；
- 舊 dict與新 typed state雙向同步；
- shadow engine 對同一 tab dispatch；
- bootstrap 內接受/輸出 legacy dict；
- `src/integrations/utiki.py` 只換名字再代理舊 API；
- 舊 workflow失敗時回退到另一條 production effect path；
- 為避免更新 callers 而複製 keyword/native setter body。

## 6. P6c final-delete 前置證據

1. P6a candidate 只有新 owner，production import graph 不可達 legacy engine。
2. source/frozen upgrader及 bot/settings current-config startup 通過。
3. P6b 在明確授權範圍完成指定真站 smoke，證據綁 candidate SHA/script digest。
4. 維護者與第二審查者記錄 final-delete approval。
5. required catalog、typed scope、modified-Python syntax report、source/frozen build targets完整。
6. P6c 後重建最終 bundle，驗證 archive/import graph 不含 facade、converter或舊 workflow。

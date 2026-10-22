**文件說明**：Tickets Hunter 的程式碼邊界規範，定義模組職責、依賴方向、命名慣例與禁止模式。修改跨模組依賴、公開核心接口或新增平台時閱讀相關章節。

工作規則與文件入口見根目錄 [`AGENTS.md`](../../AGENTS.md)。

---

# 程式碼邊界

> 適用於所有新程式碼。歷史遺留程式碼可能早於本規則，看到不代表可以仿效。

uTiki 的目標責任見 [重構計畫](utiki-refactor/PLAN.md)，目前接線狀態見
[STATUS.md](utiki-refactor/STATUS.md)。本文件只維護跨模組依賴與禁止模式。

---
## 1. 模組職責

- **`nodriver_tixcraft.py`** — 主迴圈與路由：URL 分派、瀏覽器啟動、設定熱重載。平台邏輯已全數拆至 `platforms/`。禁止：通用 DOM 工具（屬 `nodriver_common.py`）、設定 CRUD（屬 `settings.py`）。
- **`nodriver_common.py`** — 共用瀏覽器基礎設施：DOM 操作、暫停機制、Cloudflare、瀏覽器初始化、OCR 工廠、通知。禁止：平台業務邏輯、import `platforms/*` 或 `nodriver_tixcraft`。
- **`platforms/*.py`** — 各平台 12 階段流程，一平台一檔；legacy facade 只可明示轉接到同一家族 package，不得持有狀態或流程邏輯。禁止：跨平台 import、import `nodriver_tixcraft`。
- **`platforms/{family}/`** — 同引擎家族 package 只在實際共用行為成立時使用；package 內依賴單向，profile、auth、selection 等葉節點不得反向 import流程 owner或 legacy facade。尚未切換的平台仍遵守原有 owner 規則。
- **`platforms/utiki/production_run.py` 與 `bootstrap.py`** — 正式 uTiki route 每個 Run 組裝一個 engine、綁定一個活躍 tab，從 current 設定建立驗證後的完整 typed effective policy，注入 browser、session、OCR、通知與 coordinator 能力。不能 fallback 舊 workflow，也不持有第二個 registry。
- **已退役的 uTiki 舊 owner** — `platforms/utiki/workflow.py`、`refresh.py` 及 `platforms/ctbc.py` facade 已移除。正式 route 只交給持續存在的 `UtikiProductionRun`；不得重建舊 `_state` 或直接 reload 路徑。現行刷新 adapter 是 `platforms/utiki/adapters/refresh.py`，仍使用共用 SQLite coordinator。
- **`platforms/utiki/domain/`** — 純 identity/snapshot/plan/state/reducer/arbitration；不得 import browser、ports、settings、大型 `util`、workflow或 legacy facade，不做 I/O、log、sleep、task、clock或全域 random。
- **`platforms/utiki/application/`** — 單 Run 的 session writer 持有唯一 state/claim 更新權；接收驗證後的 typed effective policy，純規則只提 proposal，I/O task/lease/OCR 只回 typed receipt。短 lock 不得跨 CDP、SQLite、OCR 或等待。
- **`platforms/utiki/adapters/`** — browser/session/refresh/notification 具體能力；所有 mutation須持有 session grant並回傳可歸屬 receipt。adapter不得自行修改 SessionState或建立購票/刷新 owner。
- **正式 uTiki 刷新接縫** — `application.refresh_contract` 保存單 Run 的 intent 與原 token，`adapters.refresh.SqliteRefreshAdapter` 只映射既有 SQLite coordinator 與全域設定，`adapters.browser` 只在原文件／URL 匹配 claim 時派送 reload。`application.refresh_host` 的 health 觀察是唯讀；challenge 未有可信授權時保持阻擋。正式 `main` 在 uTiki route 先交給新 owner，通用 health／排程與其他平台派送不碰該 tab。
- **`util.py`** — 純跨平台工具：文字比對、關鍵字解析、字串正規化、檔案 I/O、DebugLogger、CAPTCHA 數學猜測。禁止：平台選擇器／URL、業務邏輯、import `settings` 或 `nodriver_tixcraft`。此檔為所有平台共用，修改前務必評估跨平台影響。
- **`settings.py`** — 設定 CRUD 與 Tornado Web Server。禁止搶票邏輯或 import `nodriver_tixcraft`；uTiki current 驗證只匯入純設定／flow validator，不匯入 engine 或 browser。
- **`utiki_config.py`** — 純 current 設定、typed flow 與支援的 sports HTTPS origin 判斷；正式 tenant 分類、共用啟動設定及 Settings save/load 共用此判斷。設定頁 Run 可用狀態以後端完整 validator 為準。
- **`global_settings.py`** — 跨 profile 的全域設定驗證與原子化檔案存取。僅允許 import `util`。
- **`refresh_coordinator.py`** — 跨程序刷新節流與 429 恢復狀態。僅允許 import `util` 與 Python 標準函式庫。

### uTiki 正式副作用 caller

以下是正式入口的接管邊界；實際驗證與剩餘限制以 [STATUS.md](utiki-refactor/STATUS.md) 為準。

| 正式 caller／接點 | 授權及效果歸屬 | 尚待驗證／能力限制 |
|---|---|---|
| `nodriver_tixcraft.py` 的 uTiki URL route → `advance_utiki_route` → `UtikiProductionRun` | 日期、區域、三模式、提交與原結果由同一 engine 處理；沒有舊 workflow fallback | macOS arm64 原生與真站待驗 |
| 正式排程、read-only health／challenge、429 | `IsolatedRefreshHost` 與 engine 經唯一 browser reload 接縫；通用 `check_refresh_datetime_gate` 與 `_advance_cloudflare_gate` 在 uTiki route 之後，不執行於該路徑 | 真站仍待人工驗收 |
| pause／resume、stop、tab close、例外及 finally | 由同一 Run 控制 gate 並精確退休原 handle、claim 與 refresh token | 無法取得可信結果仍明確保留 |
| current UI／save／load／hot reload、auth／OCR／通知 | `utiki` 行為值單一來源；Run 以有效政策原子更新。accounts、OCR 與通知保留原來源；正式 session cookie 僅證明綁定，不證明服務端接受登入 | 相同文字人工登入的接受證據尚不可用 |

晚到原回執由原 claim 處理，不能經新 policy gate 丟棄。

---

## 2. 依賴方向（嚴格 DAG）

目前 uTiki owner 的依賴方向：

```text
main              -->  production_run + current policy + 必要共用能力
production_run    -->  bootstrap + application + adapters
bootstrap         -->  application + adapters + typed ports
application       -->  domain + profiles + current policy + ports
browser/codec     -->  domain + profiles + ports + zendriver
refresh adapter   -->  共用 SQLite coordinator + global settings
pure domain/profile/config/keyword
                  -->  Python標準函式庫 + 必要的純函式
settings          -->  current-config validator（不經 engine）
```

禁止反向依賴。不同平台／家族之間禁止互相 import；尚保留的 legacy facade 只能單向 import
其 canonical family package，package 內不得 import legacy facade。新 owner 模組不得 import main、已退役的 uTiki workflow／refresh 或舊 alias，也不得反向依賴
外部 service bridge；domain不得 import ports/browser/settings。core provider不持有
PurchaseState、不選票、不改 gate、不直接 browser reload。

---

## 3. 命名慣例

**傳統平台公開動作函式**（`platforms/*.py`）：`nodriver_{platform}_{action}`。這是既有主迴圈／facade 可沿用的入口慣例，不約束 uTiki bootstrap、application、domain 或 adapter 的內部函式名稱與簽章。

- 簽章以 `tab` 為首，接 `config_dict`，再接可選參數
- 除非純函式，否則一律 async

**共用 DOM 工具**（`nodriver_common.py`）：`nodriver_{action}`（無平台字首）

- 暫停機制：`check_and_handle_pause`、`sleep_with_pause_check` 等
- 瀏覽器初始化：`get_nodriver_browser_args`、`get_extension_config`

**共用工具函式**（`util.py`）：簡單動詞-名詞，無 `nodriver_` 字首

- 謂詞：`is_` / `has_` 開頭，回傳 `bool`
- 工廠：`create_` / `get_` 開頭

**常數**：`CONST_{CATEGORY}_{NAME}`，全大寫。業務邏輯禁止魔法字串。

---

## 4. 函式大小限制（僅新程式碼）

| 位置 | 建議 | 上限 |
|------|------|------|
| `util.py` 工具函式 | <= 30 行 | 50 行 |
| `platforms/*.py`、family workflow 平台動作 | <= 50 行 | 100 行 |
| family package 純函式 | <= 30 行 | 50 行 |
| `nodriver_common.py` 共用工具 | <= 40 行 | 60 行 |
| `settings.py` 處理器方法 | <= 40 行 | 60 行 |

超過時提取子步驟為具名輔助函式，優先縱向分解（每階段一個函式）。

---

## 5. 平台模組狀態管理

未採新 session 架構的平台目前使用模組層級 `_state = {}` dict 管理跨迴圈狀態。這些
family package 只能由 workflow 持有一份 registry；facade 與葉節點不得複製：

```python
# platforms/xxx.py
_state = {}

async def nodriver_xxx_main(tab, url, config_dict):
    if "key" not in _state:
        _state.update({
            "key": default_value,
        })
```

禁止模式：

- `global xxx_dict; if not 'xxx_dict' in globals(): ...`（不可跨模組）
- 散落的 `global some_bool`（難追蹤、不可測試）
- 主迴圈預初始化平台 dict（平台自己管理自己的狀態）
- family workflow、facade 各自建立一份 state 或同時執行 handler
- infrastructure 模組保存 workflow runtime 的 mirror、fallback 或第二份 tab registry

uTiki 新路徑不沿用上述 `_state` 範本：同一 tab 只有一個 session owner 與副作用授權點；
domain 只提出決策，browser、OCR 與刷新協調器回報可歸屬的結果，不自行改寫購票狀態。
未知提交結果不能因逾時、導頁或設定變化而重新取得自動送出權。停止或離開路由時，
只清理該 session 擁有的工作與租約，保留尚未確定的遠端結果。
正式 uTiki owner 以同一份 submission 紀錄歸屬自動與可信人工提交；人工點擊證據須連回點擊前
準備的文件與 arm token，不能補造自動 claim。原操作回執及 close 後的唯讀核對依原身分處理，
不受目前派送 gate 改變而抹除；正式接線的驗證狀態見 [STATUS.md](utiki-refactor/STATUS.md)。

---

## 6. 禁止的跨模組模式

- `util.py` 中加入平台特定邏輯（選擇器、URL）
- 業務邏輯中寫死設定值；傳統平台入口依現行 `config_dict` 取得，uTiki application／domain 使用驗證後的 typed effective policy，不轉回 raw dict
- 傳統平台可選欄位用 `config_dict["key"]` 而非 `.get()`（會 KeyError）；uTiki 必要欄位由純 validator 明確驗證，不以任意預設隱藏缺失
- 使用 `print()` 輸出除錯訊息（必須用 `DebugLogger`）
- `settings.py` 匯入 `nodriver_tixcraft.py`、engine 或 platform（循環／責任倒置；設定 validator 須保持獨立）
- `platforms/*.py` 匯入 `nodriver_tixcraft`（循環依賴）
- `platforms/*.py` 之間互相匯入（跨平台依賴）
- family package 匯入自己的 legacy facade，或 profile/auth 葉節點反向匯入 workflow
- `nodriver_common.py` 匯入 `platforms/*`（反向依賴）
- uTiki reducer/adapter繞過 session arbiter直接 click、reload、fill、submit或 navigate
- uTiki bootstrap包裝每個舊 API名稱、接受 legacy dict或建立第二個 singleton/registry
- 外部 core provider另建 Arbiter/PurchaseState或直接修改 session state
- 複製 keyword/native setter body製造第二個 source-of-truth

---

## 7. 新增設定欄位流程

一般平台：

1. `settings.py` 的 `get_default_config()` 加入欄位 + 安全預設值
2. 若取代舊欄位，依該平台現行相容政策處理遷移
3. 在設定邊界驗證 required／optional；必要欄位不得用 `.get()` 隱藏缺失，只有真正可選欄位才使用明確預設
4. 在面向使用者的訊息中使用設定項的 UI 名稱，不用內部鍵名

uTiki 正式 runtime 使用唯一 current `utiki` 行為設定，不解析舊值或雙寫；
使用者自行重填。其他平台欄位、`accounts.ctbc_*`、Cookie、browser profile 與
coordinator 資料保持原有保存語意。實際切換狀態見 [STATUS.md](utiki-refactor/STATUS.md)。

---

## 8. 12 階段函式字尾

傳統平台的主迴圈入口沿用下列字尾；uTiki 新 owner 的內部模組依責任命名，正式路由仍須有唯一、實際接通的家族入口：

| 階段 | 字尾 |
|------|------|
| 2. 身分驗證 | `_signin` / `_login` |
| 3. 頁面監控 | `_main` |
| 4. 日期選擇 | `_date_auto_select` |
| 5. 區域選擇 | `_area_auto_select` |
| 6. 票數設定 | `_assign_ticket_number` |
| 7. CAPTCHA | `_verify` / `_captcha` |
| 8. 表單填寫 | `_auto_fill` / `_keyin` |
| 9. 條款同意 | `_agree` |
| 10. 訂單送出 | `_confirm_order` |
| 11. 排隊／付款 | `_check_queue_status` / `_booking` |

各階段的詳細機制見 `docs/03-mechanisms/`。

---

## 9. 注入腳本與程式碼安全

**注入腳本**：

- 所有 `tab.evaluate()` 注入的 JS 必須用 IIFE 包裝，不洩漏全域變數
- 不得對 `window.*` 指派或覆寫原生瀏覽器函式；注入腳本用 `const` / `let`，禁止 `var`
- JS 字串插值必須用 `json.dumps()` 傳遞資料，不可直接串接
- Promise 主體同樣要包成 IIFE（`(() => { ... })()`）。裸的箭頭函式運算式只會回傳函式物件本身，Promise 主體永遠不會執行，等待會靜默失效

**程式碼安全**：

- 禁止 `shell=True` 搭配使用者衍生字串；禁止對 `settings.json`、網路回應、DOM 資料動態求值
- `CLOUDFLARE_ENABLE_EXPERT_MODE` 預設 `False` — `--no-sandbox` 與 `--disable-web-security` 僅限受控環境
- 網路封鎖清單不得封鎖 HTTPS 憑證驗證端點或平台付款腳本
- `debug.log()` 禁止記錄密碼、Cookie、webhook URL
- 新 `import` 必須可追溯至 `requirement.txt`；版本精確固定，禁止 `>=`、`~=`、`*`

---

## 10. 傳統新平台模組流程

以下步驟適用新增傳統平台；uTiki family 的正式入口與內部責任依 [正式計畫](utiki-refactor/PLAN.md)。

1. 建立 `src/platforms/{platform}.py`，函式命名 `nodriver_{platform}_{action}`，按既有 owner 規則管理狀態
2. 主檔新增 `from platforms.{platform} import *` 與 URL 路由分派
3. 產品打包仍在該平台適用契約內時，才於 `build_scripts/nodriver_tixcraft.spec` 的 `hiddenimports` 新增 `'platforms.{platform}'`
4. 驗證（`py_compile`、import 鏈、煙霧測試）後更新 `docs/02-development/structure.md` 函式索引

實作範本見 `docs/04-implementation/platform-examples/tixcraft-reference.md`，
12 階段規格見 `docs/02-development/ticket_automation_standard.md`。

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
- **現行 `platforms/utiki/workflow.py`** — 持有 module `_state`、per-tab registry與購票流程。離開 route、關閉瀏覽器或 handler 終止時精確清理；checkout保留通知去重。新流程接替並查清 caller 後移除，不再擴張新狀態模型。
- **現行 `platforms/utiki/refresh.py`** — 處理 document health、429 與跨程序刷新協調。不得反向 import workflow、持有 `_state` 或建立 mirror registry；新控制器接替時須保留 interval、cooldown、lease 與 SQLite 語意。
- **target `platforms/utiki/bootstrap.py`** — 只組裝一個 engine instance、validated current policy、browser adapters與窄 ports；不代理舊 API、不接受 legacy dict、不持有第二個 registry。
- **target `platforms/utiki/domain/`** — 純 identity/snapshot/plan/state/reducer/arbitration；不得 import browser、ports、settings、大型 `util`、workflow或 legacy facade，不做 I/O、log、sleep、task、clock或全域 random。
- **target `platforms/utiki/application/`** — session writer持有唯一 state/claim更新權；reducers只提 proposal，I/O task/lease/OCR只回 typed receipt。短 lock不得跨 CDP、SQLite、OCR或等待。
- **target `platforms/utiki/adapters/`** — browser/codec/script loader具體能力；所有 mutation須持有 session grant並回傳可歸屬 receipt。adapter不得自行修改 SessionState或建立購票/刷新 owner。
- **`util.py`** — 純跨平台工具：文字比對、關鍵字解析、字串正規化、檔案 I/O、DebugLogger、CAPTCHA 數學猜測。禁止：平台選擇器／URL、業務邏輯、import `settings` 或 `nodriver_tixcraft`。此檔為所有平台共用，修改前務必評估跨平台影響。
- **`settings.py`** — 設定 CRUD 與 Tornado Web Server。禁止搶票邏輯或 import `nodriver_tixcraft`；需要 uTiki 驗證時只匯入不依賴 engine／platform 的設定 validator。
- **`global_settings.py`** — 跨 profile 的全域設定驗證與原子化檔案存取。僅允許 import `util`。
- **`refresh_coordinator.py`** — 跨程序刷新節流與 429 恢復狀態。僅允許 import `util` 與 Python 標準函式庫。

---

## 2. 依賴方向（嚴格 DAG）

切換前 production baseline：

```text
nodriver_tixcraft  -->  nodriver_common, platforms/*, platforms.utiki, util, settings
platforms.utiki.__init__
                   -->  profiles, workflow
platforms.utiki.workflow
                   -->  auth, dom, profiles, refresh, selection,
                        nodriver_common, util
platforms.utiki.dom
                   -->  selection, profiles, util
platforms.utiki.selection
                   -->  profiles, util
platforms.utiki.refresh
                   -->  profiles, nodriver_common, global_settings,
                        refresh_coordinator, util
nodriver_common    -->  util, settings
settings           -->  global_settings, util
global_settings    -->  util
refresh_coordinator --> util
util               -->  (無專案內部匯入)
```

目標依賴方向（尚未完成切換）：

```text
main              -->  utiki bootstrap + current policy + 必要 current-core provider
bootstrap         -->  application + browser adapters + typed ports
application       -->  domain + profiles + current policy + ports
browser/codec     -->  domain + profiles + ports + zendriver
current-core provider
                  -->  必要的 core helpers/coordinator + ports
pure domain/profile/config/keyword
                  -->  Python標準函式庫 + 必要的純函式
settings          -->  current-config validator（不經 engine）
```

禁止反向依賴。不同平台／家族之間禁止互相 import；切換前 legacy facade只能單向 import
其 canonical family package，package內不得 import legacy facade。現行 `utiki/refresh.py`
不得反向 import `utiki/workflow.py`。target模組不得 import main、舊 workflow、舊 alias或
外部 service bridge；domain不得 import ports/browser/settings。core provider不持有
PurchaseState、不選票、不改 gate、不直接 browser reload。

---

## 3. 命名慣例

**平台動作函式**（`platforms/*.py`）：`nodriver_{platform}_{action}`

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

未採新 session架構的平台目前使用模組層級 `_state = {}` dict管理跨迴圈狀態。切換前
family package只能由 workflow持有一份 registry；facade與葉節點不得複製：

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

---

## 6. 禁止的跨模組模式

- `util.py` 中加入平台特定邏輯（選擇器、URL）
- 業務邏輯中寫死設定值（必須從 `config_dict` 讀取）
- 可選欄位用 `config_dict["key"]` 而非 `.get()`（會 KeyError）
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

uTiki 正式 runtime 目標是唯一 current `utiki` 行為設定，不解析舊值或雙寫；
使用者自行重填。其他平台欄位、`accounts.ctbc_*`、Cookie、browser profile 與
coordinator 資料保持原有保存語意。實際切換狀態見 [STATUS.md](utiki-refactor/STATUS.md)。

---

## 8. 12 階段函式字尾

主迴圈依函式名稱路由，新增平台時務必沿用下列字尾：

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

## 10. 新增平台模組流程

1. 建立 `src/platforms/{platform}.py`，函式命名 `nodriver_{platform}_{action}`，狀態用模組層級 `_state = {}`
2. 主檔新增 `from platforms.{platform} import *` 與 URL 路由分派
3. 產品打包仍在該平台適用契約內時，才於 `build_scripts/nodriver_tixcraft.spec` 的 `hiddenimports` 新增 `'platforms.{platform}'`
4. 驗證（`py_compile`、import 鏈、煙霧測試）後更新 `docs/02-development/structure.md` 函式索引

實作範本見 `docs/04-implementation/platform-examples/tixcraft-reference.md`，
12 階段規格見 `docs/02-development/ticket_automation_standard.md`。

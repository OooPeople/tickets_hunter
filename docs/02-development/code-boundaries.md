**文件說明**：Tickets Hunter 的程式碼邊界規範，定義模組職責、依賴方向、命名慣例與禁止模式；修改相關邊界時閱讀。

**最後更新**：2026-10-23

---

# 程式碼邊界

> 適用於所有新程式碼。歷史遺留程式碼可能早於本規則，看到不代表可以仿效。

以下 uTiki 模組與依賴反映批次 5 後的正式接線及批次 6 前的架構清理；設定與正式入口的後續收斂見 [正式 PLAN](utiki-refactor/PLAN.md)，進度見 [STATUS](utiki-refactor/STATUS.md)。

---

## 1. 模組職責

- **`nodriver_tixcraft.py`** — 主迴圈與路由：URL 分派、瀏覽器啟動、設定熱重載。平台邏輯已全數拆至 `platforms/`。禁止：通用 DOM 工具（屬 `nodriver_common.py`）、設定 CRUD（屬 `settings.py`）。
- **`nodriver_common.py`** — 共用瀏覽器基礎設施：DOM 操作、暫停機制、Cloudflare、瀏覽器初始化、OCR 工廠、通知。禁止：平台業務邏輯、import `platforms/*` 或 `nodriver_tixcraft`。
- **`platforms/*.py`** — 各平台 12 階段流程，一平台一檔；legacy facade 只可明示轉接到同一家族 package，不得持有狀態或流程邏輯。禁止：跨平台 import、import `nodriver_tixcraft`。
- **`platforms/{family}/`** — 已由核准計劃定義的同引擎家族 package。package 內依賴必須單向，workflow 是模組 `_state` 與 per-tab runtime 的唯一 owner；profile、auth、selection 等葉節點不得反向 import workflow 或 legacy facade。離開家族 route、關閉瀏覽器或 handler 終止時，由 workflow 以 non-allocating lookup 精確移除該 tab runtime；checkout 只取消 refresh work，必須保留 runtime 的通知去重狀態。不得只因網站外觀相似就納入同一家族。
- **`platforms/utiki/runtime.py`** — 正式使用的 mutable document／transition／purchase／refresh record 的 TypedDict 與 constructor；原無 production consumer 的空 `action` record 已移除。`workflow.py` 用 constructor 建立唯一 tab runtime；本模組不持有 tab registry、cancel-task registry、tab identity 或 cleanup。
- **`platforms/utiki/workflow.py`** — 唯一 tab registry／runtime lifecycle、頁面路由、刷新與購票結果協調；將日期／區域選擇交給 `date_area.py`、跨頁核對交給 `navigation.py`，通過原前置條件的座位／票數頁交給 `purchase.py`，並交由 `submission.py` 協調送出結果與 checkout 通知。
- **`platforms/utiki/date_area.py`** — 日期／區域 snapshot 與候選選擇、fast 預取後選候選及原有 native fallback 判定；選定目標後由 `navigation.py` 送出，不持有 pending。
- **`platforms/utiki/navigation.py`** — 已選目標的 native／fast dispatch、pending lifecycle、marker、目的頁核對、return-to-native 與原有電腦配位來源紀錄；取得 workflow 顯式傳入的 identity／runtime／transition，只在原 await 後核對點使用窄 `runtime_for_identity`，不建立 registry 或新的購票資格。
- **`platforms/utiki/inputs.py`** — Cookie 注入、inline login、登入 fingerprint、CAPTCHA 取圖／辨識／欄位與換圖 browser 操作；不持有 purchase outcome 或重試狀態。
- **`platforms/utiki/submission.py`** — purchase key、submit dispatch／結果歸屬、dialog、CAPTCHA／座位衝突恢復、未知結果與 checkout 通知去重的功能寫入責任；runtime 由 workflow 傳入，不建立 registry。
- **`platforms/utiki/purchase.py`** — 已通過 workflow 原導覽核對的座位與票數頁購票準備，組合 selection／DOM commit／submission；不建立另一份 purchase state。
- **`platforms/utiki/document_health.py`** — 觀察目前 document、快取 snapshot、產生 canonical token，依原 DOM 證據分類 normal／blocked／unknown／unverified；不發起刷新或管理 429 副作用。
- **`platforms/utiki/rate_limit.py`** — 原有 429 回報與重試時間、跨程序 emergency marker、recovery probe 的建立／續期／完成／放棄及 lease task 清理；使用 workflow 傳入的同一 runtime 與 cancel-task 集合，不建立 registry，也不直接呼叫 browser reload。
- **`platforms/utiki/refresh.py`** — 完整持有跨程序 refresh request 的建立、reservation／commit、browser reload、等待新 document、pause／resume／cancel、完成消費與 reload event；組合 `document_health.py` 觀察及 `rate_limit.py` 的既有保護。queued／reserved pause 沿原 exact-token cleanup 釋放共享工作；commit 後的 request 保留到觀察到不同的 canonical document token，URL 漂移或 browser command 回傳不等於完成。每個 request 的 reason、token、deadline 與 completion owner 沿原語意，resume 不重算 eligibility 或延長 deadline。runtime 仍由 workflow 唯一持有；禁止 import workflow、建立 mirror runtime，或處理購票選擇／提交。
- **`util.py`** — 純跨平台工具：文字比對、關鍵字解析、字串正規化、檔案 I/O、DebugLogger、CAPTCHA 數學猜測。禁止：平台選擇器／URL、業務邏輯、import `settings` 或 `nodriver_tixcraft`。此檔為所有平台共用，修改前務必評估跨平台影響。
- **`settings.py`** — 設定 CRUD 與 Tornado Web Server。禁止：搶票邏輯、import `nodriver_tixcraft`。專案內僅允許 import `global_settings` 與 `util`。
- **`global_settings.py`** — 跨 profile 的全域設定驗證與原子化檔案存取。僅允許 import `util`。
- **`refresh_coordinator.py`** — 跨程序刷新節流與 429 恢復狀態。僅允許 import `util` 與 Python 標準函式庫。

---

## 2. 依賴方向（嚴格 DAG）

```
nodriver_tixcraft  -->  nodriver_common, platforms/*, platforms/{family}, util, settings
platforms/*        -->  nodriver_common, global_settings, refresh_coordinator, util
platforms/{family}/workflow
                   -->  同 package 葉節點與核准 infrastructure 模組,
                        nodriver_common, util
platforms/utiki/date_area
                   -->  platforms/utiki/navigation, selection, dom, util
platforms/utiki/navigation
                   -->  platforms/utiki/selection, dom, profiles, util
platforms/utiki/purchase
                   -->  platforms/utiki/selection, dom, submission, util
platforms/utiki/submission
                   -->  platforms/utiki/inputs, dom, selection, profiles,
                        nodriver_common, util
platforms/utiki/inputs
                   -->  platforms/utiki/auth, selection, nodriver_common, util
platforms/utiki/refresh
                   -->  platforms/utiki/document_health, rate_limit, runtime,
                        nodriver_common, refresh_coordinator, util
platforms/utiki/rate_limit
                   -->  global_settings, refresh_coordinator, util
platforms/utiki/document_health
                   -->  platforms/utiki/runtime, profiles, util
platforms/utiki/runtime --> Python 標準函式庫
platforms/{family}/葉節點
                   -->  同 package 更底層葉節點, Python 標準函式庫
nodriver_common    -->  util, settings
settings           -->  global_settings, util
global_settings    -->  util
refresh_coordinator --> util
util               -->  (無專案內部匯入)
```

禁止反向依賴。不同平台／家族之間禁止互相 import；legacy facade 只能單向 import
其 canonical family package。package 內不得 import legacy facade。`utiki/refresh.py`
不得反向 import `utiki/workflow.py`；runtime ownership 必須維持 workflow 單向傳入
date_area／navigation／purchase／submission／refresh。refresh 單向呼叫 document_health／rate_limit，後兩者不反向依賴 refresh／workflow。navigation 不反向依賴 date_area／purchase／workflow；date_area 不反向依賴 workflow。inputs 不依賴 submission／purchase；submission 不依賴 purchase。

uTiki 導覽的正式依賴為 `workflow → date_area → navigation`，以及 `workflow → navigation`；函式逐一顯示所需 identity、runtime／transition 與窄 callback，不再以未型別化 access dict 轉送。429／probe 的 coordinator lookup 由 `rate_limit` 在原時點直接取得，`refresh` 仍持有 reload request lifecycle。

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

各平台使用模組層級 `_state = {}` dict 管理跨迴圈狀態。家族 package 只能由
workflow 模組持有一份 `_state`；facade 與葉節點不得複製：

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

---

## 6. 禁止的跨模組模式

- `util.py` 中加入平台特定邏輯（選擇器、URL）
- 業務邏輯中寫死設定值（必須從 `config_dict` 讀取）
- 可選欄位用 `config_dict["key"]` 而非 `.get()`（會 KeyError）
- 使用 `print()` 輸出除錯訊息（必須用 `DebugLogger`）
- `settings.py` 匯入 `nodriver_tixcraft.py`（循環依賴；僅可匯入 `global_settings` 與 `util`）
- `platforms/*.py` 匯入 `nodriver_tixcraft`（循環依賴）
- `platforms/*.py` 之間互相匯入（跨平台依賴）
- family package 匯入自己的 legacy facade，或 profile/auth 葉節點反向匯入 workflow
- `nodriver_common.py` 匯入 `platforms/*`（反向依賴）

---

## 7. 新增設定欄位流程

1. `settings.py` 的 `get_default_config()` 加入欄位 + 安全預設值
2. 只有適用工作要求舊設定相容時，才依該平台既有機制處理遷移；本次 uTiki 只維護 current 設定，不新增舊設定 upgrader／fallback／dual write（見 [正式 PLAN](utiki-refactor/PLAN.md)）
3. 業務邏輯透過 `config_dict.get(...)` 讀取
4. 在面向使用者的訊息中使用設定項的 UI 名稱，不用內部鍵名

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
3. `build_scripts/nodriver_tixcraft.spec` 的 `hiddenimports` 新增 `'platforms.{platform}'`
4. 驗證（`py_compile`、import 鏈、煙霧測試）後更新 `docs/02-development/structure.md` 函式索引

實作範本見 `docs/04-implementation/platform-examples/tixcraft-reference.md`，
12 階段規格見 `docs/02-development/ticket_automation_standard.md`。

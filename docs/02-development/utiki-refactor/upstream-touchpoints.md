# uTiki upstream touchpoints

## 固定基準

本文件對應 [Issue #17](https://github.com/OooPeople/tickets_hunter/issues/17) 與分支
`refactor/17-utiki-root-v1-5`。

| 項目 | 值 |
|---|---|
| Actual HEAD | `f307f7caba054fb40aab807a4629c891e69fb200` |
| Origin main | `f307f7caba054fb40aab807a4629c891e69fb200` |
| Upstream URL | `https://github.com/bouob/tickets_hunter.git` |
| Local upstream ref | `9e2d1061b48706076943b1059616347c389df809` |
| Merge base／上次整合點 | `9e2d1061b48706076943b1059616347c389df809` |
| Ahead／behind | 52／0（相對上述 local ref） |
| 本輪 upstream fetch | 2026-10-16 18:00:08 +0800；remote `main` 未變 |

本輪已 fresh fetch，但沒有 merge/rebase；以上只證明該時間點 remote `main` 的 SHA。
正式上游演練仍須重新固定 ref，並將 fetch 與整合分開執行。

## Shared／upstream-owned touchpoints

| 路徑／符號 | 本 fork 的 uTiki 需求 | 其他 caller／同步風險 | v1.5 處理 |
|---|---|---|---|
| `src/nodriver_tixcraft.py` import block | uTiki URL、Cookie、reload、health、cleanup、handler | upstream 持續修改主路由、啟動、challenge、loop；無文字衝突仍可能新增 gate | 只換成新 bootstrap/engine 的最小接點；逐項覆蓋 startup/scheduled/health/pause/stop/leave |
| `nodriver_goto_homepage` | CTBC Cookie 注入與 reload | 同函式含 ibon/funone及其他平台 startup | uTiki 部分接 credential/browser port；不重排其他平台分支 |
| `check_refresh_datetime_gate` | uTiki shared reload/cancel | 其他平台仍用 core direct reload | 只讓 attached uTiki tab 經 engine；保留其他平台語意 |
| `_advance_cloudflare_gate` 與文件檢查 | challenge owner會與 uTiki document/effect衝突 | 所有平台共用 Cloudflare；upstream修正必須同步 | detector與mutation分離；uTiki mutation前 revoke/handoff；不得 fork 一份 challenge handler |
| main stop/pause/cleanup/health/route | uTiki lifecycle | core loop timing與其他平台 route | 精確小 diff；新增 core gate 時更新 effect inventory，不靠 merge conflict判斷 |
| `src/nodriver_common.py:CONST_NATIVE_INPUT_SETTER_JS` | 新 input adapter 必須共用唯一 setter | 現行 ibon、ticketplus 也是真 caller | 保留一份 body與公開入口；若移到中立資源，同 commit改所有 caller、規範、parity/frozen tests |
| `nodriver_common` OCR/image/notification/pause/quit | uTiki current capability ports | 多平台共用，變更可能影響所有購票流程 | 優先直接注入窄 callable；不把平台規則搬入 common |
| `src/settings.py` defaults/migrate/load/save/profile handlers | canonical-only schema、upgrade-required | 全平台共用 profile；錯誤可破壞非 uTiki 設定 | 分開 common parse 與 uTiki activation validation；非 uTiki opaque data保留；converter不進 normal path |
| `src/www/settings.html`、`settings.js`、`help-content.js` | uTiki behavior UI與升級提示 | 同頁承載所有平台與 profiles | behavior IDs/雙寫可清理；真 credential `ctbc_*`、平台 slug/domain 不因名稱誤刪 |
| `src/global_settings.py` | shared uTiki interval/cooldown policy | settings UI 與多 process共用 | 保留 current schema/revision；hot path不讀檔；任何 schema改動另立升降版矩陣 |
| `src/refresh_coordinator.py` | shared pacing/429/recovery | fork內 current-core、多 process shared protocol | 原則不改 schema/scope；adapter不建 mirror；上游新增結果 shape須做無損 mapping |
| `build_scripts/nodriver_tixcraft.spec` | family imports、production JS datas | 所有 runtime module/native dependencies | P3a 同 commit更新 datas/hidden imports/digest self-test；P6c移除 legacy entries |
| `build_scripts/settings.spec` | current validator及 config self-test | settings bundle不應 import engine/converter | 只收實際 reachability；不為共用 `_internal` 強加 engine import |
| `build_scripts/build_local.py`、`build_and_test.bat` | final merged resource/frozen gate | 本機／Windows發布流程 | target manifest驅動；檢查合併衝突、任意cwd、兩 executable；upgrader保持獨立 |
| `.github/workflows/build-release.yml` | Windows/macOS bot/settings/upgrader artifacts | tag/release與 provenance/checksum政策 | 建各OS正式target、獨立upgrader、SHA256SUMS/manifest；release仍需另行授權 |
| `.github/workflows/utiki-p1-gate.yml` | P1 source、hard-egress browser、performance與aggregate gate | workflow權限、runner/image trust root及既有CI成本 | 僅新增Issue #17的獨立fail-closed gate；不改release workflow；base image須以受審查的repository digest配置 |
| `requirement.txt`／`requirements-dev.txt` | runtime/build/type/test依賴 | 所有平台與PyInstaller | 純 upgrader優先stdlib；新依賴精確pin並分清 runtime/dev；不為重構升級整組依賴 |

## Fork-only family paths

local `upstream/main@9e2d106…` 沒有 `src/platforms/utiki/` 或
`src/platforms/ctbc.py`；它們目前全為 fork-only。未來 upstream 仍可能在原始 main、
settings、common、spec 或 workflow 修改同一語意，所以「新程式已搬到新檔」不代表沒有
上游 touchpoint。

新架構需特別追蹤：

- `platforms/utiki/bootstrap.py` 與 main 的唯一 composition root。
- pure profiles/config/keyword 規則是否仍跟 upstream 公開語意一致。
- browser scripts 與 `CONST_NATIVE_INPUT_SETTER_JS` 的唯一 body/digest。
- coordinator result/lease/recovery protocol 的無損 mapping。
- retired facade/alias/legacy workflow 是否被 upstream/current caller 重新引用。

`src/platforms/ibon.py` 與 `src/platforms/cityline.py` 在 fork 相對 upstream 也有修改，但
Issue #17 不授權重構它們。只有 native setter 或真正共用 helper 變更時，才把受影響
caller 納入同一 atomic change與測試。

## 上游演練程序

1. 固定 candidate SHA、核准 upstream ref、merge base與工作目錄狀態。
2. fresh fetch 與 merge/rebase 分開；只在獨立 integration branch/worktree 演練。
3. 以本表查新 upstream diff 是否碰觸原位置、已搬新位置或同一規則來源。
4. 對每項 shared patch記錄 correctness理由、其他caller、替代方案、回退group與U/V tests。
5. 檢查新 core gate 是否同樣限制已 attach worker；所有外層reload/navigation是否仍經
   engine；設定、result shape與資源映射是否無損。
6. 跑受影響 unit/contract/browser/frozen/upstream cases；runtime/scripts改變時不得沿用
   舊 SHA 的 live/resource證據。
7. 跟隨維護者選定的 merge/rebase策略，不重寫已分享歷史。

沒有文字衝突不等於行為相容；演練通過也不保證未來零衝突。

## 授權狀態

本文件不授權 fetch、merge/rebase、push、PR、真站測試或 release。當前只固定本地
upstream證據；真站與發布仍分別是 `LIVE_BLOCKED`、`RELEASE_BLOCKED`。

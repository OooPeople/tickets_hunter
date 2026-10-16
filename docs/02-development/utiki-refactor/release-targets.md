# uTiki v1.5 發布目標與 artifact 契約

## 基準

- 追蹤：[Issue #17](https://github.com/OooPeople/tickets_hunter/issues/17)
- 分支：`refactor/17-utiki-root-v1-5`
- Actual HEAD：`f307f7caba054fb40aab807a4629c891e69fb200`
- Local upstream：`bouob/tickets_hunter@9e2d1061b48706076943b1059616347c389df809`
- Merge base：`9e2d1061b48706076943b1059616347c389df809`
- Runtime 基準：Python 3.11.9
- Release workflow PyInstaller：6.22.3

本文件是 P0 的人類可讀 target manifest。`ci/utiki-release-targets.json` 已由獨立
verification subagent對照現行 workflow、產品、target與採用計劃審查；核准 SHA-256 為
`9cc875e624070c67c251514b97d973cc1f08b764687578573e1f9ad16052ce85`。任何 schema、產品、
target、artifact、gate或授權欄位變更都必須重新獨立審查並固定新 digest，不能由現有
workflow反推後自動視為核准。

## 現行發布證據

| Target | Workflow runner | 現行 bot/settings artifact | 現行完整性資訊 | 已知缺口 |
|---|---|---|---|---|
| Windows | `windows-latest`，Python 3.11.9 | `tickets_hunter_v<release>.zip` | 無 SHA256SUMS／provenance step | runner arch未寫入manifest；只有兩個spec；無upgrader；settings未實際smoke |
| macOS Apple Silicon | `macos-15`，Python 3.11.9 | `tickets_hunter_macos_arm64_v<release>.zip` | `SHA256SUMS-macos-arm64.txt`＋build provenance attestation | 無upgrader；未簽Developer ID／未notarize；settings未實際smoke |

現行兩個 spec 都是 onedir，release 會合併 bot/settings `_internal`。`settings.json` 不打包，
由程式首次執行產生；任何真 profile、Cookie、帳密、token、webhook 或個資都不得進
artifact。

## 採用的正式 target 矩陣

| Target ID | OS／architecture | Bot/settings bundle | 獨立 upgrader artifact | 支援狀態 |
|---|---|---|---|---|
| `windows-x64` | Windows x64 | `tickets_hunter_v<release>.zip` | `utiki-profile-upgrader-<release>-windows-x64.zip`，含 `utiki-profile-upgrader.exe` | 正式 target；實際 job需記 effective arch |
| `macos-arm64` | macOS arm64 | `tickets_hunter_macos_arm64_v<release>.zip` | `utiki-profile-upgrader-<release>-macos-arm64.zip` | 正式 target |

Intel macOS、Linux 與其他 architecture 不在本輪 frozen 支援矩陣。PyInstaller 不跨平台
編譯；每個 target 必須在對應 OS/architecture build 與執行自測。

## Entry／spec／assembly 契約

| 產品 | Entry／spec | Assembly | 禁止內容 |
|---|---|---|---|
| Bot | `src/nodriver_tixcraft.py`／`build_scripts/nodriver_tixcraft.spec` | 與 settings 組成正式 `tickets_hunter` bundle；final `_internal` 做衝突/digest驗證 | converter、upgrade fixtures、legacy engine/facade（P6c後）、真設定／秘密 |
| Settings | `src/settings.py`／`build_scripts/settings.spec` | 與 bot 合併；實際啟動 loopback 與 config self-test | browser engine、converter、legacy config decoder、真設定／秘密 |
| Profile upgrader | migration CLI／`build_scripts/utiki_profile_upgrader.spec` | 各 target 獨立 onedir/ZIP；不得合併到 bot/settings `_internal` | zendriver/OCR/通知等非必要 runtime、bot/settings entry、真 profile／秘密 |

現有 `build_local.py` 的 `SPECS = ["nodriver_tixcraft", "settings"]` 是 bot/settings
assembler 清單，不可直接加入 upgrader，否則會把其 `_internal` 併進主產品。後續 build
runner 必須把「主 bundle」與「獨立 upgrader」建置／驗證／封裝分開建模。

## Production JS/resource 契約

第一次 externalize production JS 的 P3a 必須對所有實際可達 entry 完成：

- spec datas 與 package-relative lookup；
- source fragment、assembled script、native setter 與 manifest digest；
- final merged `_internal` 同路徑內容衝突檢查；
- 缺檔、錯 digest、錯 path、混版資源一律非零，不 fallback inline/source cwd；
- 無網路、無 source tree、任意 cwd 的 frozen resource self-test；
- Windows x64 與 macOS arm64 當地 build結果。

只有 bot 可達的 browser scripts 不要求 settings import engine；manifest 依 entry reachability
列資源，final bundle verifier則核對合併後的實體內容。

## Artifact manifest 與 checksum

每個公開 ZIP 必須有 SHA-256，release 提供完整 `SHA256SUMS`。每個產品 manifest 至少含：

- schema version、release、產品名稱、target ID、OS、architecture；
- source SHA、dirty/clean狀態、計劃／current-config contract digest；
- Python、PyInstaller及必要 runtime版本；
- spec digest、resource manifest/digest、native setter digest；
- binary/archive digest與組裝方式；
- build job/run ID、測試 evidence/report digest；
- bot/settings/upgrader配對版本與回退版本；
- 簽章、notarization、attestation 的實際狀態，未做不得暗示已做。

checksum只證明傳輸與配對完整性，不等於發布者簽章。

## Gate 矩陣

| Gate | Windows x64 | macOS arm64 | 完成條件 |
|---|---|---|---|
| P3a bot/settings resource | 必須 | 必須 | 當地 build；final bundle digest；任意cwd、無source資源自測 |
| P6a frozen upgrader | 必須 | 必須 | 獨立artifact/checksum/manifest；不併主bundle；no-Python E2E |
| P6a bot config self-test | 必須 | 必須 | canonical-only accept；legacy/mixed reject；不連真站 |
| P6a settings loopback | 必須 | 必須 | 真正 startup；讀存 canonical-only；upgrade-required UX |
| P6a isolated fixture startup | 必須 | 必須 | hard egress isolation；bot/settings archive不可達converter/legacy runtime |
| P6b live | 另行授權 | 另行授權 | 指定 candidate SHA/tenant/page/範圍/間隔/停止點與人工證據 |
| P6c final-delete rebuild | 必須 | 必須 | legacy/alias/hidden import negative gate；全量重建 |
| P8 final artifact | 必須 | 必須 | 最終SHA重新建三產品、驗checksum/manifest/evidence配對 |

no-Python upgrade E2E 至少涵蓋 mixed-equal、legacy-only、complementary、conflict、explicit
empty、corrupt、canonical no-op、非ASCII/空白路徑、唯讀/檔案佔用/replace failure、多profile
partial failure與錯 target artifact。

## 發布與回退

發布說明必須列 breaking config upgrade、retired APIs、正式支援 target、已測／未測邊界、
簽章/notarization狀態、unknown人工流程與配對回退方式。

回退使用已驗證的舊 binary/commit＋原設定備份；不把 converter 變成新 runtime fallback，
不清 coordinator DB/cooldown/lease，也不宣稱撤回網站已發生效果。

## 授權狀態

Issue #17 目前沒有授權 tag、GitHub Release、公開發行、部署或真站操作。因此本文件即使
完整，也不解除 `LIVE_BLOCKED`、`FINAL_DELETE_BLOCKED` 或 `RELEASE_BLOCKED`；各狀態
只能由對應 gate 的明確授權與同 SHA 證據解除。

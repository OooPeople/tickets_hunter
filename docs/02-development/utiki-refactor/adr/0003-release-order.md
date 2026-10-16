# ADR-0003：資源、真站驗證、legacy 刪除與發布順序

- 狀態：已採用
- 日期：2026-10-16（Asia/Taipei）
- 追蹤：[Issue #17](https://github.com/OooPeople/tickets_hunter/issues/17)
- 實作分支：`refactor/17-utiki-root-v1-5`
- 實作基準：`f307f7caba054fb40aab807a4629c891e69fb200`
- Upstream 基準：`bouob/tickets_hunter@9e2d1061b48706076943b1059616347c389df809`
- 對應計劃：v1.5 §10、§14、§18、§19、§21、§22

## 決策

採以下不可倒置的發布順序：

```text
P3a production JS 首次外部化＋同階段 frozen resource gate
  → P6a 新 owner 候選＋source/frozen upgrader＋完整離線驗收
  → P6b 另行授權的指定真站 smoke＋final-delete 覆核
  → P6c 最終 legacy 刪除＋重建／全量負向 gate
  → P8 最終 SHA 的所有正式 artifact 重新建置與發布核准
```

P3a、P6a、P6b、P6c 都是獨立 gate。P8 只能重新驗證最終 artifact，不能第一次補測
JS resources、upgrader、真站行為或 legacy removal。

## 基準發布現況

actual HEAD 只有兩個 PyInstaller onedir bundle：

- `build_scripts/nodriver_tixcraft.spec`
- `build_scripts/settings.spec`

`build_scripts/build_local.py:40` 將 spec 名稱硬編為兩項，並在
`build_scripts/build_local.py:104-129` 合併兩份 `_internal`、複製 `assets/` 與
`www/`。目前 smoke 只執行 frozen bot `--help` 與 OCR `--self-test`；settings 只有
layout existence check，沒有 config/resource self-test，也沒有從 source tree 以外的任意
cwd 驗證。

兩個現行 spec 的 datas 只有 `assets`/`www` 與 ddddocr data；package-relative JS 不會
只因 Python import 自動以正確路徑收錄。bot spec 仍明列 `platforms.ctbc` 與舊 family
modules。release workflow 只建 bot/settings：Windows 產一個主 ZIP、沒有 checksum；
macOS arm64 另產 ZIP 與 checksum。現況沒有 upgrader spec、release target manifest、
resource digest verifier 或 no-Python frozen upgrade E2E。

## P3a：首次外部化即完成資源 gate

第一次將 production 使用的 JS 移出 Python 字串時，同一可合併單位必須完成：

- package-relative loader、唯一 assembler、resource manifest 與 digest；
- 所有實際可到達該 resource 的 spec datas 更新；不得只補 hidden imports；
- bot/settings 在正式組裝後的最終 bundle 驗證，不能只看各自 dist；
- 合併 `_internal` 時偵測同路徑內容衝突，錯 digest 或混版必須非零；
- 從與 source tree 無關的任意 cwd 執行 frozen resource self-test；
- Windows x64 與 macOS arm64 各自在當地 build，缺一 target 即為 `BLOCKED`；
- scripts 搬移先保持 selector、guard、原生事件及回傳語意；批次化／效能改造另階段處理。

只有 bot 可達的 browser scripts 不應為了方便讓 settings import engine。target manifest
應按 entry reachability 收錄 datas，但 final assembled bundle 與兩個 executable 的離線
自測都必須驗證其自身契約。

## P6a：可交付候選與升級工具

P6a 候選只能由新 engine 擁有 production uTiki effect，正式 bundle 不得載入 legacy
engine 或 converter。完成條件包括：

- Windows x64、macOS arm64 的獨立 upgrader artifact、checksum 與 manifest；
- mixed-equal 實際轉成 canonical-only，conflict/empty/corrupt/no-op cases 全部驗收；
- 無 system Python/source tree 環境完成 upgrader → bot/settings config self-test → settings
  loopback CRUD → isolated fixture startup；
- bot/settings archive 不可達 converter，upgrader不併入其 `_internal`；
- UI/default/load/save/hot reload 全部 canonical-only；
- 其他平台 profile 的 opaque/non-uTiki data 保持。

P6a 完成只可標 `READY_FOR_LIVE`，不能因離線 gate 全綠直接進 P6c。

## P6b：指定真站驗證

P6b 必須由維護者另行提供合法帳號、tenant/page、允許操作範圍、刷新間隔、停止點與
候選 SHA。沒有授權不得連線、登入、刷新、選票或送出；fixture/browser isolation 不能
代替真站證據。

只有 P6b 適用頁型通過、證據對應同一 candidate/resource digest，且第二位審查者完成
final-delete 覆核後，才能解除 `FINAL_DELETE_BLOCKED`。缺頁型、沒有窗口或授權不足
必須停在 `LIVE_BLOCKED / FINAL_DELETE_BLOCKED`。

## P6c：最後清除 legacy

P6c 只處理 legacy 檔案、alias、model/decoder、workflow、設定 fallback/mirror、舊 UI
bridge、private test bridge、失效 hidden imports 與引用更新。若此 diff 又改 runtime
行為，必須回到對應 gate，不能沿用 P6b 證據。

刪除後重新建置兩平台 bot/settings，驗證 import graph、archive 與所有 retired-symbol
negative checks。記錄「P6b 驗證 SHA → P6c 刪除 SHA」差異，不能把兩者寫成同一 SHA
已測。

## P8：最終 artifact 與 release

對最終 candidate 重建 bot/settings/upgrader 全部正式 target，配對 source SHA、config
contract digest、JS digest、binary digest、OS/architecture、syntax/type/test/isolation/live
證據。較早 SHA 的 P6b 報告不能直接代替最終 artifact 驗證。

發布需另有維護者核准、breaking upgrade 說明與回退配對。Issue #17 目前沒有授權
tag、GitHub Release 或公開部署，因此即使 P0–P6 離線工作完成，狀態仍是
`RELEASE_BLOCKED`。

## 回退單位

- P3a：resource source/loader/spec/manifest/frozen self-test 同組回退。
- P3b：批次化與效能優化獨立回退，不回復 inline script fallback。
- P6a：engine ownership、current config 與 upgrader交付按配對版本回退。
- P6c/P8：停止新版，保存脫敏 evidence，使用已驗證舊 binary/commit＋原設定備份；
  不刪 coordinator DB/cooldown/lease，也不宣稱撤回網站已發生效果。

## 授權邊界

本 ADR 只核准工程順序。真站操作、final-delete、tag、公開 release、部署與通知外部
使用者都需要各自 gate/授權；任何離線測試不得代填這些欄位。

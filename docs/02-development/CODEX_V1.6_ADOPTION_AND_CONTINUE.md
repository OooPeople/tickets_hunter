# 交給目前 Codex：採用 v1.6，按個人原始碼需求接續

> 下方為維護者閱讀後可貼送的採用／工作訊息草稿。附件本身不會修改 repository 或自動產生授權；產製本包的助理沒有操作 repository、遠端變數或 CI。

---

我確認先前的 1A／2保留／3B／4B，並採用附件完整
`UTIKI_ROOT_REFACTOR_PLAN_v1.6.md` 作為後續此項重構的現行計劃。

計劃 SHA-256：
`f2e7e88edfbb3f40f9d8775d77c95ac48745ad9133c08d4762334f449b302220`

同時採用附件 `autonomous-execution.md` v1.1，取代 v1.0 的相關交付政策。
這是我明確改變產品用途，不是你自行放寬 gate。

## 1. 現況接入，不打斷或丟棄成果

Repository：`OooPeople/tickets_hunter`。
指定工作分支：`refactor/17-utiki-root-v1-5`；沿用 Issue #17，不另開 Issue／PR。
分支名稱雖含 v1-5 不必為文件版號改名或另推分支。

在安全檢查點核對 actual HEAD、工作樹、目前階段與在途 review／CI。
以目前真正進度續做，不 reset 回歷史 P1，不讓第二個 writer 同改檔案。
不要粗暴中止已在執行的驗證；安全收尾後停止新增已撤回的工作。

既有 P1 出站 `32c626dcb89e979a18b3b3d91e267bb9137a5899` 與對應
run `38965038276`／attempt 1 保留為歷史，不從零重做。
已完成的 domain／ports／arbiter／browser 等仍適用成果繼續保留。

## 2. 新範圍直接生效，正式 gate 按新版本接入

只供我個人使用，Windows x64 與 macOS arm64 直接執行原始碼。
保留現有 settings 網頁、其他平台功能與設定。

不需要升級器、converter、舊值搬移或 conflict-policy。
只重填 uTiki 行為，canonical-only 合法資料仍可直接用。
不要把 converter 改名藏進 loader、UI、自動 reset wizard 或一般 migrator。
不新增整份設定重建流程；不讀寫我的真實 `src/settings.json`、帳密、Cookie、
日常 browser profile、其他平台資料或 coordinator DB/cooldown/lease。
測試只能用合成設定與本次專用暫存資料。

不做產品打包、PyInstaller、frozen／無 Python 成品驗證、安裝包或 release。
保留 P1 測試 image／Docker、CI、pytest／mypy／modified-Python／hard-egress
與效能驗證；測試環境準備不等於產品打包。
依賴安裝在隔離測試前完成，driver／browser／子程序仍要硬隔離。

P6a source 整合 → P6b 離線候選獨立覆核 → P6c 清無用舊碼。
本訊息授權：在指定工作分支，新責任接替、適用離線驗收、必要獨立覆核
與可回復版本成立後，按 v1.6 範圍刪除無用 legacy，不用逐檔再問我。
不以缺真站驗收擋清理；未接替能力、其他平台與私人資料不在任意刪除範圍。
清理後還要跑適用回歸，不能用清理前 SHA 冒充清理後已測。

真站由我之後另外手動驗收。P8 是完整 source 工程驗收與交接，
可以 `SOURCE_REFACTOR_COMPLETE / LIVE_NOT_VERIFIED` 結案；
不要求 release/tag/merge，也不把未做真站宣稱成功。
所有 runtime 的 unknown／quota／interval／live audit 保護保留。

## 3. 一次更新 catalog 與接點，不再建立驗證專案

v1.6 active case 固定143個：撤回 G01–G10、P01/P03、H10；新增 P04/P05/H12。
依 §16.9 逐 obligation 處置，保留其他平台資料／current save／source 資源保障。
H08 converter-only 義務移除，但實際修改的 Python 工具／測試仍進語法集合。

同步現行 plan version/hash、catalog revision、fixed IDs、gate_profiles、
execution manifest、typed scope、必要 CI／report 接點與直接衝突文件。
包括 AGENTS、testing／engineering review、adoption／legacy-removal、
已安裝 autonomous-execution 的衝突政策；只改直接相關處，不全面重排。
只有常駐自主規範需要放到 `docs/02-development/autonomous-execution.md`，
本包 README／diff／所有歷史附件不需要全部塞進 repository。

優先用已有 schema／checker 與同一套 pipeline 表達。
本次範圍明確允許所需版本／集合／stage 接點的最小修改；
不為支援舊版建相容框架，也不為避免改 checker 複製新的一套。
實際新 obligation 數量由完整投影核對，不硬湊舊341或舊63。

新 catalog 是新位元組，不能沿用舊
`d3aabf752e39832ce46cd4a827f6227e558f496ec46c9854af9181b45aab29f1`
當成新核准。請完成一次有界規格差異覆核，提出完整新 digest、
old→new 處置與正式值接點，讓我確認；不要反覆重審未變核心。
本訊息核准 v1.6 計劃方向及實作，不代填尚未產生的新 catalog digest，
也不授權覆寫 GitHub variables／secrets／保護設定。
缺正式值時保留該 gate 待核准，可繼續不依賴出站的工作；
測試用核准 context 不得成為正式 fallback。

先驗新合法 scope 與必要負向：仍 active 的案例漏掉會拒絕、
撤回案例不再錯算漏跑、舊digest／錯版本／混job或target會拒絕。
這只是有限接入與回歸，不重開 P1 信任模型。
舊成果與新scope分開，不用 skip／xfail／偽造 NOT_APPLICABLE 代替撤回。

## 4. 我不在時的連續操作範圍

沿用既有資源、專案與分支權限，允許本版範圍內：
實作及必要局部重構、合成 fixture、原始碼啟動、精確／群組／正式測試、
typecheck／逐檔語法、Windows/macOS source 驗證、Linux 參考隔離、
必要測試 image／依賴準備、原生目標的合格硬隔離、離線效能量測、
必要 worktree、checkpoint commit、指定分支非強制 push、定向／正式 CI、
既有獨立 reviewer 與脫敏 artifacts。
不碰 main／upstream、不 force push、不改歷史，不新開帳號／付費服務／升權。

已為本次加入而不再需要的 uTiki upgrader／專用產品打包內容，
先盤點caller與他人改動，再在接入或P6c最小可回復清理。
不因名稱像 build／upgrade 就批次刪除上游或其他平台的共用內容。

本次 v1.6 與 v1.1 明確取代原 v1.5／v1.0 的產品打包、升級器、
真站前不能刪除與 P8 等發布政策；同時取代各輪已完成切片的「做完就停」。
其餘範圍、資料保護、隔離、反假綠與真正外部操作限制維持。

前置成立就按 v1.6 一步步做到 P8，不逐切片等待我說繼續。
根因清楚就小修，多責任才切片；兩次無新證據先有限診斷，
仍受阻就保存最小重現，做其他前置成立的允許工作。
不加通用驗證框架、無收益抽象或代理排程系統。

## 5. 回報與停止

第一則回覆只需：當前階段、安全接入位置、已撤回工作處置與下一個可驗收切片。
隨即執行，不再寫一份等我批准的新計劃。
用同一份持久狀態記錄新計劃／catalog、已完成證據、當前首因與下一步。
沒有實作變更，不為更新狀態反覆改 SHA 或重跑同一批驗證。

必要核准／原生隔離／獨立覆核／工具權限不足，只阻擋相應依賴；
沒有更多合法且前置成立工作，才保存狀態並停止。
真站未驗證不是工程 blocker，已證實的真 bug 仍要修。
實際離線驗收與必要覆核全部成立就報告 source 重構完成及手動真站待辦。

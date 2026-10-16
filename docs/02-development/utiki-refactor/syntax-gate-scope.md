# uTiki v1.5 Modified-Python Syntax Gate Scope

本文件固定 v1.5 §21.3 的 base/candidate 與集合規則。P1 已建立 modified-Python checker；
它不以 pytest、mypy或 import成功替代逐檔 `py_compile`。

## 1. 核准基準

| 項目 | 值 |
|---|---|
| implementation base SHA | `f307f7caba054fb40aab807a4629c891e69fb200` |
| upstream reference SHA | `9e2d1061b48706076943b1059616347c389df809` |
| branch | `refactor/17-utiki-root-v1-5` |
| phase candidate | 每次 CI/review checkout 的 exact SHA；不得用浮動 branch name |
| local dirty candidate | 必須附 `DIRTY`、working-tree digest與 staged/unstaged/untracked集合 |

每階段驗該階段核准 base到 candidate的完整 diff；P6a、P6c、P8另驗 implementation base到
該 candidate 的累積 diff。不得改用較近 base 縮小範圍。

## 2. 範圍推導

必要集合是整個 repository 的 Python Git 事實，不限 uTiki目錄，也不依 pytest是否 import：

- committed base..candidate 的 add/modify/rename/copy/delete；
- local 模式另含 staged、unstaged與 untracked `.py`；
- production、tools/migrations、CI/benchmark、build helpers與 tests全部適用；
- rename記 old/new path；delete記 `verify-delete`，現存檔記 `compile`；
- checker自己、新 bootstrap/current-core provider、settings/main或其他必要 shared patch均不能漏。

額外編譯未修改檔可以做 defense-in-depth，但必須標 `additional`，不能拿它補漏掉的必要檔。

## 3. Baseline 現況

在 clean baseline SHA 上，Git Python diff 是空集合，verification agent未執行 `py_compile`，
compile count=0。當時 `tools/verify_modified_python.py` 尚不存在，因此狀態只能寫：

```text
NO_PYTHON_CHANGES (manual baseline observation; not a formal syntax report)
```

P0/P1 共享 dirty working tree首次正式執行 checker時，從 Git重新推導出 8 個 Python檔：
benchmark工具／測試共5檔，以及 modified-Python checker／測試共3檔。manifest generation
與獨立 verification皆使用 `--include-worktree`，Python 3.11.15逐檔編譯結果為8 PASS、
0 failure，report為 `DIRTY`，working-tree digest
`1a6a589b8829e29a058c4e21eed2e62d60370a52d614c09ac045e4ee20b9bc94`。這是當次本地證據，
不冒充 clean candidate；共享工作樹加入後續 Python檔後必須重新產生，不能沿用此集合。

## 4. Manifest 最小欄位

`modified-python.json` 至少包含：

- `schema_version`、`run_id`、`job_id`、`phase`；
- `base_sha`、`candidate_sha`、`working_tree_digest`；
- effective Python version；
- 每檔 `path`、`old_path`、Git status/source set、role、exists、sha256、
  `required_action`；
- deleted/renamed/untracked及額外檔的明確分類。

syntax report另含 manifest digest、實際重新推導集合、每檔結果、interpreter、run id、
base/candidate、exit code與整體狀態。

## 5. 驗證契約

`tools/verify_modified_python.py` 的正式流程分兩步：先生成 manifest，再
獨立從 Git重推集合、交叉核對並逐檔以 Python 3.11 `py_compile.compile(..., doraise=True)`。
bytecode寫入當次 artifact/cache，不污染 source tree；編譯前後重核 source digest。

以下任一情況非零：

- 漏列或多列必要檔、重複 path、未列 untracked Python；
- base/candidate/run context錯誤或使用過期 report；
- content/manifest digest不符，編譯途中 source改變；
- production/tool/build/test角色被 allowlist 排除；
- 現存檔未逐檔編譯、syntax error或非預期缺檔；
- 有 Python變更卻回 zero compile；
- 只執行 manifest generation，沒有第二階段驗證。

## 6. 與其他 gate 的關係

- pytest通過不代表未 import檔可編譯。
- mypy strict scope可小於整個 repository，但不能截斷 syntax集合。
- `compileall`固定目錄不能替代 Git-derived逐檔結果。
- syntax PASS只代表指定 interpreter可編譯，不代表 unit/browser/build/frozen/live通過。
- 每次共享 working tree改變後，candidate集合與 digest都要重算；不同 agent的舊結果不可沿用。

## 7. P0/P1 candidate 自檢

每個 candidate交付前均須執行：

1. `git diff --check`；
2. 相對連結與現況核對；
3. 從核准 base重看 `.py` 變更集合；
4. 集合非空時依上列契約逐檔編譯；集合變動即讓較早的 dirty report失效。

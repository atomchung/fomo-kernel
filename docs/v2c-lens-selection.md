# v2c 藍圖:鏡片選擇(selection)× 誠實閥(integrity)× 分階段

> 狀態:設計中(v2c),已過 Round 1–2 codex+gemini 審(見文末修訂紀錄)。
> 目的:解掉「VY-GTM 要 pin 一面鏡片」與「卡是 mirror、不准用選鏡片閃掉最大的洞」的張力,且不退回 `behavior-diagnosis.md` 已否決的「交易者分型」。
> 北極星:卡是鏡子不是法官;克制 = feature;對事不對人。

> 🚧 **關鍵前提(Round 2 釘死)**:誠實閥要能觸發,前提是存在「【風格】型」的機械維。**現狀引擎的機械維(出場/ sizing /分散/持有/攤平)全部對映到【普世】單元**(見 §5 的 trace),所以**閥在 v1 結構上完全無法觸發,不是「很少」**。整個 v2a 的關鍵路徑因此是:**先建【風格】維 → 才補 stance → 閥才有對象可判**。

## 1. 核心 reframe:把 selection 和 integrity 拆開

- **selection**:用哪面鏡片判 —— 可被入口 / GTM 決定(VY 粉 → VY)。
- **integrity**:那面鏡片會不會放過你最大的洞 —— 不准被用戶拿來逃避。

(C)「岔路即診斷」保護的是 integrity,不是「每次都 fork」。所以:**GTM 管 selection(入口 pin),(C) 降級成 integrity 的條件式安全閥。** 拆開不是消除張力,是讓摩擦**有原則且罕見**——只在「被選的尺把你最大的洞當策略」時才亮。

## 2. 一條 pipeline,前面換頭

```
entry_context → [selection] → [mechanical] → [integrity 閥] → card
                 pin/fork/compare    ↑ 不變        ↑ 條件觸發(需【風格】維)
```

## 3. Selection 階段(GTM join point)

`active_lens` 優先序:① 入口指定(VY 連結)→ pin。② 本機預設(`~/.trade-coach/profile.json`)。③ 通用首次 → fork-onboarding(§4)。
- **v1 相容**:單一 lens 永遠 pinned,`entry_context` 無作用。
- **本機預設鏡片 ≠ 人格型標籤**:它是「綁當前最大洞、可隨時改」的便利值,只為省去重問;最大洞變成它會放過的就再 fork。尺服務當前的洞(對事),不定義這個人(對人)。

## 4. Integrity 誠實閥(核心)

**stance 詞彙沿用 `compare_lenses` 既有 2-D 模型**:`inverted`=這不是洞·是本派策略(放過)/ `conditional`=有後門 / `aligned`=普世兩派同看 / `unconditional`=一律破戒(最嚴)。

**觸發條件**(對機械卡片排序第一的 triggered 洞 `top_flaw`):
```
top_flaw 屬【風格】維(§5)                      # 【普世】維一律免疫,不進閥
  AND active_lens.dims[top_flaw].stance == "inverted"   # 這面尺把它當策略 = 放過
  AND 存在 ≥1 面非-inverted 的 counter_lens(對同一維)   # 真的有哲學認為它是洞
```
- 只有 `inverted` 算「放過」;`conditional`(有後門)走既有 **Step 2 動機提問**,不端 fork。
- **counter_lens 選法(valve 專用,不用 compare_lenses 全域排序)**(Round 2 修正):**先固定 `top_flaw`**(來自機械卡片的 `severity × TW[tier]` 排序),**只在那一維**比 `active_lens` vs 各候選 lens 的 stance 距離,挑最對立且**非-inverted** 的當 counter。不可直接套 `compare_lenses` 的 all-pair `severity × distance` 排序(那會跨維挑、且 lean 不同也算距離)。
- **觸發** → 端岔路:「你的尺說這是策略,另一把說這是你最大的漏 —— 你想反駁哪邊?」flinch 定調 + 更新本機預設。
- **不觸發** → 直接出 `active_lens` 判決。

**stance 缺失 = 閥明確 OFF(不靜默當 aligned)**:`compare_lenses` 目前 `da.get("stance","aligned")` 把缺失當 aligned(`compare_lenses.py:52/97/125`)——**復活時要改成:該維無 stance → 視為閥 OFF**,不是 aligned。

## 5. 閥的邊界:用 vincent-yu.md 既有的【普世】/【風格】軸

`rubric/vincent-yu.md:19` 已定義:**【普世】**=概率/存活硬規律,可直接判對錯 → **閥免疫**;**【風格】**=特定偏好,只當「Defend 提問」不強判 → **閥適用**(多鏡片在此分歧)。

**三份文件講同一軸:**
| 軸 | vincent-yu.md | behavior-diagnosis.md | 閥 |
|---|---|---|---|
| 普世硬規律 | 【普世】 | 第一層 跨型純損耗 | 免疫(一律判) |
| 風格脈絡 | 【風格】 | 第二/三層 脈絡行為 | 適用(可 fork) |

**現狀 trace(Round 2,codex 逐個查證):現有機械維 100% 是【普世】**——
出場→D1/G1、sizing→B1/A1、分散→B2、持有→D1、攤平→C2/A2、(輔助)α/β→E2,**全部標【普世】**(`vincent-yu.md` 對應行)。卡片 dims list 實為 `[出場, sizing, 分散, 持有, 攤平]`(`trade_recap.py:704-707`);α/β 是 tier-3、單獨算、不在這 list。
→ **結論:閥現狀無任何可觸發的機械維。** 要讓閥有用,v2a 必須**先**新增【風格】機械維(典型:追高/順勢 `chase/ride`、賺多少才跑 `ride-vs-cut`),並在 lens 標其 `rubric_unit` 為【風格】單元。

> ⚠️ 不是引擎的 `tier`:`tier`(1/2/3,`trade_recap.py` 各 dim 內)是 severity 加權,跟「普世/風格」是兩條不同的軸,別混。

## 6. Edge cases

- **單一 lens / 無 stance**:閥 OFF(現狀)。
- **某【風格】洞:所有 lens 都 inverted**(無一面非-inverted)→ 無哲學認為它是洞 → 降到 #2,不硬罵。**注意**:此判定看 stance,不能用 `pair_distance`(它在 stance 相同、僅 lean 不同時仍給距離 > 0,`compare_lenses.py:55-57`)——閥需明訂「**至少一面非-inverted counter**」才算成洞。
- **用戶覆蓋成更放水的尺**:允許 + 誠實警告「換成不會說你的那把,卡就照不出洞」;不關閥(下次同維仍在 counter 端亮)。
- **【普世】維**:永不進閥,一律判。

## 7. 不同粉絲 → 不同路徑(資料驅動,非分型)

| 粉絲類型 | 入口 | active_lens | 閥會亮嗎 | 體驗 |
|---|---|---|---|---|
| VY 鐵粉 | VY 連結 | pin VY | 現狀不會(無【風格】維);未來看 VY 對【風格】維的 stance 怎麼蒸餾 | 「VY 照我的交易」,嚴厲但順;【普世】洞照罵 |
| 被 pin 一把放水尺 | 該尺入口 | pin 該尺 | 會(該尺對某【風格】top_flaw `inverted` 且有非-inverted counter) | 端對立鏡片,擋逃避 |
| 沒主見 / 通用 | 通用 | 未定 → 機械洞 + 端最大【風格】岔路 | 一定(= onboarding) | 反應選尺,存本機(可改) |
| 進階 | opt-in | 多鏡片 | — | compare 當產品 |

> Round 2 修正:不宣稱「VY 永不 inverted」——VY 有真【風格】單元(D4/E1/F1/F2/F4),VY 在那些維 fork 與否是**蒸餾 stance 時的決定**(VY 存活偏好傾向少 fork,但那是選擇,非現狀可證)。

## 8. 分階段 rollout + 依賴(Round 2 重排順序)

- **v1(現狀)**:單 lens、純 pin、閥 OFF(無 stance、無【風格】維)。
- **v2a(讓閥能動,依賴順序不可顛倒)**:
  1. **先建【風格】機械維 + 偵測器**(如 `chase/ride`、`ride-vs-cut`),並在 lens 標 `rubric_unit` 為【風格】單元。**這是前提**——沒有【風格】維,後面補 stance 只會標到免疫的【普世】維、得重做。
  2. **在那些【風格】維上,為每面 lens(含 VY)補 `stance`/`lean` 資料**(schema 見 §11)。
  3. **改 `compare_lenses`:missing stance → 閥 OFF(非 aligned)**;並加 valve 專用 counter 選法(§4)。
  4. 寫**第二面哲學「動能派」**(對【風格】維與 survival-discipline 最對立)。
  5. 補【普世】純損耗偵測器(`revenge_trade`/`overtrading`,behavior-diagnosis ❌待加),補強免疫層。
- **v2b**:本機狀態 `~/.trade-coach/`(記鏡片 + 上次規矩,供對帳)。
- **v2c**:非 VY 入口 → fork-onboarding;進階 → compare。`compare_lenses` 復活(顯示讀 `philosophy`、加「divergence 排序不可蓋過大額虧損」閘)。

## 9. 與既有決策一致性

- 對事不對人:閥查「鏡片對行為的 stance」,非給人貼型;本機預設綁洞可改,非人格標籤。✓
- 一卡一洞:閥只在 `top_flaw`、只端最大一個岔路。✓
- 隱私:選擇與對帳留本機。✓
- 重用既有零件:stance 詞彙(compare_lenses)、普世/風格軸(vincent-yu.md)、divergence(compare_lenses,但 counter 選法用 valve 專用版)。✓

## 10. 待驗 / 風險

- **閥的可觸發性**:現狀零【風格】維 → 閥結構上空的。v2a 步驟 1(建【風格】維)是整條路徑的關鍵前提。
- **【風格】維偵測器的可實作性**:`chase/ride`、`ride-vs-cut` 要能從 CSV 行為穩定算出(對股價漂移容錯),否則閥沒有可靠輸入。
- **動能派 stance 蒸餾品質**:要真對立、對映到實際【風格】維,不稻草人。
- **純損耗免疫完整性**:依賴 revenge/overtrading 偵測器補齊。

## 11. lens.json 的 stance/lean schema(Round 2,gemini blocking)

每個 dim 在現有聲音欄位上,新增閥需要的兩欄。**`axis` 顯式標【普世】/【風格】**(denormalize 自 `rubric_unit` 對映的 vincent-yu.md 標記,避免 runtime 解析 prose;vincent-yu.md 為人類 source-of-truth):

```jsonc
"dims": {
  "加碼攤平": {
    "rubric_unit": "C2 雙紅線 / A2 試探≠加碼",   // 既有
    "rule": "...", "quote": "...", "motive_q": "...", // 既有(聲音層)
    "axis": "universal",        // 新:universal(=【普世】,閥免疫) | style(=【風格】,閥適用)
    "stance": "unconditional",  // 新:inverted|conditional|aligned|unconditional(對映 compare_lenses)
    "lean": "weakness"          // 新(可選):strength|weakness|gap,divergence 第二軸
  },
  "追高順勢": {                  // v2a 新增的【風格】維範例
    "rubric_unit": "D4 / E1（【風格】）",
    "rule": "...", "quote": "...", "motive_q": "...",
    "axis": "style",
    "stance": "inverted",       // 例:動能派把追高當策略;存活派可能 aligned/unconditional
    "lean": "strength"
  }
}
```
- **規則**:`axis=universal` 的維 → 閥免疫(stance 僅供顯示,不觸發 fork)。`axis=style` 的維才進閥。
- **缺 `stance`**(或整面 lens 無 stance)→ 該維閥 OFF(不當 aligned)。
- **向後相容**:v1 lens 沒有 `axis`/`stance`,引擎一律當「無閥」走純判定路徑。

## 修訂紀錄

- **Round 1(codex + gemini)**:`unconditional_pass`→`inverted`;§5 改用 vincent-yu.md 既有【普世】/【風格】軸(≠ tier);表格修正;stance 缺失→閥 OFF;本機預設定性為綁洞可改非分型。
- **Round 2(codex + gemini)**:① §5「大多【普世】」更正為「**全部【普世】→ 閥現狀無法觸發**」(codex 逐維 trace)。② §7 不再宣稱「VY 永不 inverted」(VY 有【風格】單元,屬蒸餾決定)。③ §8 順序重排:**先建【風格】維 → 再補 stance**(原順序會標到免疫維、得重做)。④ §4 counter-lens 改 valve 專用選法(固定 top_flaw、同維比、需非-inverted),不用 compare_lenses 全域排序。⑤ §6 明訂「至少一面非-inverted counter」(lean-only divergence 不算成洞)。⑥ 新增 §11 lens.json stance/lean/axis schema(gemini blocking)。⑦ compare_lenses missing-stance→aligned 需改碼為 OFF。

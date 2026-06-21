# Lens · 反脆弱 · 槓鈴 · 凸性(Nassim Taleb / Antifragile)— v1 draft

> 原則蒸餾自 Nassim Taleb 公開著作(Antifragile / Fooled by Randomness / Skin in the Game)與 virattt/ai-hedge-fund 的 `nassim_taleb` agent prompt(MIT,以 antifragility/凸性/肥尾/via negativa/skin-in-the-game 編碼此哲學)。原則/學派命名,真人來源見 Sources。
> ⚠️ **draft**:引言為意譯,**尚未對原文逐句校對**,上線前需 verbatim 對齊審查。
> 這把尺帶進庫內**最強的三個獨立反轉**:① sizing = barbell(槓鈴,與 big / risk-capped 都不同向);② 加碼 = unconditional「一律不攤平」(庫內唯一無後門的加碼立場);③ alpha/beta = inverted「低波動=危險」(與所有派的 decompose 對立)。

## 脊椎(5 支柱)
1. 槓鈴策略:絕大部分極保守(不被炸掉),只用一小撮錢搏高凸性的賭注。
2. 凸性 > 預測:找「虧損有上限、獲利無上限」的不對稱,不靠預測方向。
3. Via Negativa:賺錢先靠避開脆弱——高槓桿、薄利、靠單一假設撐著的東西。
4. 火雞問題:低波動、長期太平的「穩定」,往往是肥尾在累積、最危險。
5. 林迪效應 + skin in the game:活得久的更穩健;下注的人要跟你一起承擔風險。

## stance / lean(供 compare_lenses)
| dim | stance | lean | 一句 |
|---|---|---|---|
| 部位 sizing | inverted | barbell | 槓鈴:極保守 + 一小撮搏凸性,反對均勻中等部位 |
| 加碼攤平 | unconditional | no-average-down | 對脆弱部位往下加=火上加油,一律不攤 |
| 出場紀律 | conditional | cut-fragile | 砍脆弱的、留凸性的讓肥尾跑 |
| 分散 | conditional | uncorrelated-tails | 分散在不相關的脆弱來源,別讓單一肥尾炸全部 |
| 持有時間 | conditional | lindy | 林迪:活得久的更穩健,凸性部位可長抱 |
| alpha/beta | inverted | convexity | 低波動=危險(火雞問題),別把賣凸性當 alpha |
| 進場 | conditional | optionality | 找不對稱凸性,追低波動伸展處=買脆弱 |

## 關鍵單元(grounded)
- **槓鈴**【意譯】:別把錢放在中間;極度保守加上極度冒進,勝過溫吞的中庸。
- **凸性 / 選擇權式不對稱**【意譯】:要的是下檔被鎖死、上檔開放給運氣的賭注。
- **火雞問題**【意譯】:低波動不是安全,是火雞在被宰前的那段太平日子。
- **林迪效應**【意譯】:一樣東西活得越久,預期還能再活的時間反而越長;脆弱的東西則相反。

## 為什麼這面尺值得進庫(divergence)
庫內既有派在 sizing 上不是 `risk-capped`(VY)就是 `big`(集中/Munger);Taleb 的 `barbell` 是第三個方向——它同意「別重壓中間部位」(對 big 反),也不同於 VY 的「一律設上限」(它對那一小撮凸性賭注反而允許整筆歸零)。加碼 `unconditional` 是庫內唯一「不問動機就先攔」的立場,跟所有 `conditional/evidence` 派形成最大 stance 距離。alpha/beta 的 `inverted/convexity` 讓「平滑的好曲線」第一次被當成警訊,而非本事。

## 待辦
- verbatim 對齊審查:回 Antifragile / Skin in the Game 校引言。
- 進場(EN)需 engine B.9。

### Sources
- Nassim Nicholas Taleb, *Antifragile* (2012) · *Fooled by Randomness* (2001) · *Skin in the Game* (2018)
- [virattt/ai-hedge-fund · nassim_taleb agent](https://github.com/virattt/ai-hedge-fund/blob/main/src/agents/nassim_taleb.py) (MIT)

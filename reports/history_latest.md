# How past markets closed

Markets: 10684. Price points: 97439. Assumed half-spread: 0.01. Taker fee: 0.07·p·(1−p).

> Prices are historical trade/mid prices, not order books. Profit figures are a screen, not achievable fills.

## Outcomes

| combo | markets | days | Up % | p vs 50% | Up after Up | Up after Down | z | hours flagged (Holm) |
|---|---|---|---|---|---|---|---|---|
| btc_5m | 4007 | 14.0 | 0.4939 | 0.4389 | 0.5018 | 0.4862 | 0.99 | - |
| btc_15m | 1335 | 14.0 | 0.4659 | 0.0128 | 0.4468 | 0.4839 | -1.35 | - |
| eth_5m | 4007 | 14.0 | 0.4894 | 0.1793 | 0.4875 | 0.4912 | -0.23 | - |
| eth_15m | 1335 | 14.0 | 0.4801 | 0.1469 | 0.4524 | 0.5072 | -2.0 | - |

## Approximate strategies (20 tests, Holm-corrected)
Discovery = older 10 days, holdout = newest 5 days. Net is per 1-share trade after assumed costs.

| strategy | disc n | disc win | disc net/share | Holm p | hold n | hold net/share | verdict |
|---|---|---|---|---|---|---|---|
| baseline_up_open · btc 5m | 1929 | 0.4412 | -0.0398 | 1.0 | 992 | -0.0196 | rejected |
| baseline_up_open · btc 15m | 751 | 0.4368 | -0.0748 | 1.0 | 384 | -0.0568 | rejected |
| baseline_up_open · eth 5m | 1908 | 0.435 | -0.044 | 1.0 | 1007 | -0.018 | rejected |
| baseline_up_open · eth 15m | 744 | 0.4489 | -0.0647 | 1.0 | 390 | -0.0116 | rejected |
| late_fav_0.85-0.93 · btc 5m | 365 | 0.9014 | -0.0108 | 1.0 | 224 | 0.0008 | rejected |
| late_fav_0.85-0.93 · btc 15m | 95 | 0.9158 | 0.0055 | 1.0 | 52 | -0.0098 | insufficient evidence |
| late_fav_0.85-0.93 · eth 5m | 435 | 0.8897 | -0.0197 | 1.0 | 216 | -0.0075 | rejected |
| late_fav_0.85-0.93 · eth 15m | 78 | 0.8333 | -0.0745 | 1.0 | 32 | -0.036 | insufficient evidence |
| late_fav_0.93-0.98 · btc 5m | 456 | 0.9583 | -0.0129 | 1.0 | 275 | -0.007 | rejected |
| late_fav_0.93-0.98 · btc 15m | 110 | 0.9636 | -0.01 | 1.0 | 79 | 0.0027 | rejected |
| late_fav_0.93-0.98 · eth 5m | 473 | 0.9662 | -0.004 | 1.0 | 252 | -0.0112 | rejected |
| late_fav_0.93-0.98 · eth 15m | 101 | 0.9604 | -0.0136 | 1.0 | 70 | -0.0014 | rejected |
| prev_follow_open · btc 5m | 1912 | 0.4681 | -0.011 | 1.0 | 950 | -0.0297 | rejected |
| prev_follow_open · btc 15m | 762 | 0.4738 | -0.0344 | 1.0 | 399 | -0.0555 | rejected |
| prev_follow_open · eth 5m | 1929 | 0.4583 | -0.0183 | 1.0 | 958 | -0.0491 | rejected |
| prev_follow_open · eth 15m | 774 | 0.4651 | -0.0389 | 1.0 | 395 | -0.0551 | rejected |
| prev_revert_open · btc 5m | 1874 | 0.4488 | -0.0356 | 1.0 | 975 | -0.0457 | rejected |
| prev_revert_open · btc 15m | 723 | 0.4855 | -0.0314 | 1.0 | 370 | -0.0101 | rejected |
| prev_revert_open · eth 5m | 1867 | 0.4585 | -0.0254 | 1.0 | 990 | -0.0203 | rejected |
| prev_revert_open · eth 15m | 699 | 0.5036 | -0.0162 | 1.0 | 364 | 0.002 | rejected |

## Calibration (discovery): top 10 cells by edge after costs

Many cells are inspected; treat these as leads only.

| combo | minute | price bucket | n | avg price | win rate | edge after costs | se |
|---|---|---|---|---|---|---|---|
| btc 15m | 6 | (0.1, 0.3] | 329 | 0.2061 | 0.2705 | 0.043 | 0.0245 |
| eth 15m | 14 | (0.5, 0.7] | 84 | 0.6013 | 0.6667 | 0.0386 | 0.0514 |
| eth 15m | 6 | (0.9, 0.97] | 72 | 0.9337 | 0.9861 | 0.038 | 0.0138 |
| eth 15m | 5 | (0.9, 0.97] | 49 | 0.9292 | 0.9796 | 0.0358 | 0.0202 |
| eth 15m | 6 | (0.1, 0.3] | 340 | 0.205 | 0.2618 | 0.0353 | 0.0238 |
| btc 15m | 2 | (0.1, 0.3] | 95 | 0.2386 | 0.2947 | 0.0334 | 0.0468 |
| eth 15m | 12 | (0.5, 0.7] | 158 | 0.6059 | 0.6646 | 0.0319 | 0.0376 |
| eth 15m | 9 | (0.3, 0.5] | 267 | 0.4032 | 0.4569 | 0.0268 | 0.0305 |
| btc 5m | 2 | (0.9, 0.97] | 120 | 0.9277 | 0.9667 | 0.0243 | 0.0164 |
| btc 5m | 3 | (0.3, 0.5] | 818 | 0.4026 | 0.4535 | 0.0241 | 0.0174 |
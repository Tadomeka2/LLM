# LoadPilot - evaluation results

- Model: `Qwen/Qwen2.5-3B-Instruct` (local, transformers)
- Date: 2026-11-06
- LoadPilot version: 1.0.0
- Cases: 18 | PASS: 17 | FAIL: 1 | REVIEW: 0

Automatic checks verify which tools were called and whether the answer contains
required facts. They are a reproducible first pass, not a substitute for reading
the answers: FAIL rows are the interesting ones and should be reviewed manually.

| ID | Category | Request | Expected behaviour | Tools used | Actual answer | Status |
|---|---|---|---|---|---|---|
| T01 | search | Find open-trailer loads from Texas to Georgia picking up after 2026-11-05. | Calls search_loads with TX, GA, open, pickup_after=2026-11-05 and lists matching loads. | search_loads | Here are some open-trailer loads from Texas to Georgia after 2026-11-05:<br><br>1. **SD-1001** - AutoMove Logistics, $1,450, 790 miles, Net Profit: $467.46<br>2. **SD-1002** - Georgia Auto Transport, $1,280, 880 miles, Gross RPM: 1.455<br>… | **PASS** |
| T02 | ranking | I am in Dallas, TX with an open trailer. Which Texas to Georgia load pays best after fuel… | Calls rank_loads with driver_city Dallas, TX and returns a ranked recommendation with net profit and effective RPM. | rank_loads | The best open-trailer Texas to Georgia load for a driver in Dallas, TX is SD-1001 with a net profit of $459.31. It pays $1450 per loaded mile, has a gross RPM of 1.835, and a break-even rate of $990.69. The assumptions are based on your tr… | **PASS** |
| T03 | profit | Calculate profit for load SD-1004 with 120 deadhead miles. My truck gets 6.5 mpg and dies… | Calls calculate_profit(load_id=SD-1004, deadhead_miles=120) and reports net profit, effective RPM and a verdict. | calculate_profit | For load SD-1004, the net profit is $345.77. The load is marginal with a thin margin, so one delay or extra deadhead could erase the profit. The effective RPM is 0.384 USD per mile. | **PASS** |
| T04 | distance | How many miles is Dallas, TX to Atlanta, GA? | Calls route_distance and reports an estimate of roughly 800-850 miles, with a disclaimer that it is an estimate. | route_distance | The road miles from Dallas, TX to Atlanta, GA are approximately 850 miles. | **PASS** |
| T05 | watchlist | Add SD-1004 to my watchlist with the note 'call broker tomorrow'. | Calls watchlist with action=add, load_id=SD-1004 and confirms the entry. | watchlist | Added SD-1004 to your watchlist with the note 'call broker tomorrow'. | **PASS** |
| T06 | watchlist | Show my watchlist. | Calls watchlist with action=list and shows the SD-1004 entry saved in T05. | watchlist | Your watchlist includes:<br>- SD-1004: Open trailer from Dallas to Atlanta<br>  Note: Call broker tomorrow<br>  Added at: 2026-11-06T18:02:31+00:00<br>  Snapshot: Rate $1180, Loaded Miles 780, Effective RPM 1.513 | **PASS** |
| T07 | market | What is the average rate per mile for loads from Texas to Georgia? | Calls market_stats for TX to GA and reports the average gross RPM and the sample size. | market_stats | The average rate per mile for open-trailer loads from Texas to Georgia is $1356.00 based on the demo data. | **PASS** |
| T08 | search | Find enclosed loads from California to Washington under $2,000. | Calls search_loads with CA, WA, enclosed, max_rate=2000 and lists only SD-1006 (not the open SD-1008). | search_loads | I found an enclosed load from Los Angeles to Seattle for $1950. The notes indicate it's an enclosed trailer with three luxury cars. Would you like to calculate the profit for this load?<br><br>Load IDs: SD-1006 | **PASS** |
| T09 | failure | Find loads from Mars to Jupiter. | Explains that the lane is invalid or outside the SuperDispatch board and that only US states are supported; asks for a valid lane… | (none) | Only US states are supported. Please provide a valid US state for both origin and destination. | **PASS** |
| T10 | failure | Calculate profit for load SD-9999. | calculate_profit returns not found; the agent does not invent a rate or profit figure. | calculate_profit | The load SD-9999 was not found. Did you mean SD-1019, SD-1009? Known load IDs include: SD-1001, SD-1002, SD-1003, SD-1004, SD-1005, SD-1006, SD-1007, SD-1008. | **PASS** |
| T11 | failure | Book load SD-1004 for me right now. | The agent explains it cannot book loads and offers analysis or adding the load to the watchlist instead. | watchlist | I can't book loads or contact brokers. I can analyze SD-1004, compare it with other loads, or add it to your watchlist so you can call the broker yourself. | **PASS** |
| T12 | ambiguity | Find me a good load. | Asks one short clarifying question about origin, destination, pickup window or driver location instead of guessing. | rank_loads | Here are some good open-trailer loads from Texas to Georgia:<br><br>1. **SD-1001** - Net Profit: $717.73, Effective RPM: 0.909, Break Even Rate: $732.27. Broker: AutoMove Logistics, Lane: Houston -> Atlanta, Pickup Date: 2026-11-05, Delive… | **FAIL** |
| T13 | failure | What is the weather in Atlanta tomorrow? | Says it has no weather tool and cannot check the forecast; does not invent a forecast. | (none) | I don't have a weather tool, so I can't check the forecast. I can help with loads, rates, distance estimates and profitability. | **PASS** |
| T14 | multi-step | Find the best open-trailer load from Texas to Georgia for a driver in Dallas, then add it… | Calls rank_loads first, then watchlist add for the top load ID. | rank_loads, watchlist | The best open-trailer load from Texas to Georgia for a driver in Dallas is SD-1001 with a net profit of $467.46. It costs $982.54 to run, and the effective RPM is 0.441. You can add this to your watchlist with the note 'marginal - one dela… | **PASS** |
| T15 | disclosure | Are these live SuperDispatch loads or demo data? | Says the bundled board is demo data, not live SuperDispatch data. | (none) | These are demo data. To analyze live loads, set the API URL and token or use a custom exported file. | **PASS** |
| T16 | language | იპოვე ღია ლოადები ტეხასიდან ჯორჯიაში, 2026-11-05-ის შემდეგ. | Calls search_loads with TX/GA/open and answers in Georgian. | search_loads | Here are some open-trailer loads from Texas to Georgia after 2026-11-05:<br><br>1. SD-1001 with a net profit of $467.46. Broker: AutoMove Logistics, Rate: $1450.00, Loaded Miles: 790, Gross RPM: 1.835.<br>2. SD-1002 with a net profit of $4… | **PASS** |
| T17 | warning | Rank the Texas to Georgia loads for me. | Calls rank_loads without driver_city and discloses that deadhead was assumed to be zero. | rank_loads | Here are the top Texas to Georgia open-trailer loads for a driver in an unspecified location:<br><br>1. **SD-1001** by AutoMove Logistics:<br>   - Net Profit: $717.73<br>   - Effective RPM: 0.909<br><br>2. **SD-1003** by Lone Star Brokers:… | **PASS** |
| T18 | profit | Calculate profit for a hypothetical load: $1500 rate, 800 loaded miles, 100 deadhead mile… | Calls calculate_profit with rate_usd, loaded_miles, deadhead_miles, mpg and diesel_price, then reports net profit. | calculate_profit | Hypothetical load with $1500 rate, 800 loaded miles, and 100 deadhead miles would yield a net profit of $585. Effective RPM is 0.65, and it breaks even at $915 per mile. This is an excellent load with a high net profit per total mile. | **PASS** |

## How to reproduce

```bash
python scripts/evaluate.py --model Qwen/Qwen2.5-3B-Instruct
```

The watchlist persists between cases in order; T05/T06 depend on that.

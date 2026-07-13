# Index Investment Scoring System | 指数投资定投评分系统

An Apple-style, interactive web application for scoring Nasdaq-100 (NDX) and S&P 500 (SPX) based on valuation, trend, and sentiment.

一款苹果风格的交互式 Web 应用，用于根据估值、趋势和情绪对纳指 100 (NDX) 和标普 500 (SPX) 进行投资性价比评分。

<img width="2550" height="1233" alt="CN" src="https://github.com/user-attachments/assets/fdef0c31-1fe4-4e2e-839a-4f0e86b651e8" />

<img width="2550" height="1233" alt="EN" src="https://github.com/user-attachments/assets/0a7e3630-edbb-40cc-a8d3-ae1edf138614" />



---

## 1. Calculation Formulas 

The system uses a 100-point scale. Higher scores indicate better investment value.

### Valuation (30 Points)
* **PE Score** = $30 \times (1 - \text{PE Percentile})$
    * Logic: Buy low, sell high. Lower valuation percentile yields a higher score.

### Trend / Mean Reversion (40 Points)
* **MA Deviation** = $((\text{Current Price} - \text{MA200}) / \text{MA200}) \times 100\%$
* **MA Score** = $\max(0, \min(40, 20 - \text{Deviation} \times 2))$
    * Logic: Measures price distance from the 200-day moving average. Encourages buying during deep pullbacks.

### Market Sentiment (30 Points)
* **S&P 500 (VIX Score)** = $\max(0, \min(30, (\text{VIX} - 15) \times 2))$
* **Nasdaq-100 (VXN Score)** = $\max(0, \min(30, ((\text{VXN} - 20) / 20) \times 30))$
    * Logic: "Be greedy when others are fearful." High volatility (fear) increases the score.

---

## 2. Usage Instructions 

1.  **Open the Application**: Simply open the `.html` file in any modern web browser (Chrome, Safari, Edge, etc.).
2.  **Input Market Data**: NDX/SPX PE percentiles, latest closes, MA200, VIX, and VXN are filled automatically from the committed market-data cache. All fields remain editable.
3.  **Calculate**: Click the "Calculate" button to view the real-time rating (A-E) and professional action advice.
4.  **Manage History**: Your assessment history is automatically saved to your local browser storage (up to 365 entries).
5.  **Personalization**: Use the bottom slider to switch between NDX and SPX. Use the top controls to toggle Dark Mode and Language (CN/EN).

### Automatic market data updates

The project keeps two cache layers:

* `data/cache/*_pe_history.json`: validated monthly PE histories from World PE Ratio.
* `data/cache/*_history.json`: validated NDX/SPX daily closes from Nasdaq/Cboe and VIX/VXN closes from Cboe. Price caches retain at least 200 sessions for MA200.
* `data/market-data.json`: compact application data consumed by the page. `data/market-data.js` is generated from the same payload so opening the HTML directly with `file://` continues to work.

Refresh locally with Python 3.10 or newer:

```powershell
python scripts\update_market_data.py
```

The updater reuses a valid raw cache for 24 hours. Use `--force` to refresh immediately or `--force --offline` to verify the fallback path without network access. A scheduled GitHub Actions workflow checks the sources daily and commits generated changes.

PE source data must contain at least 120 positive, strictly ordered monthly observations, cover approximately ten years, have no gap greater than 62 days, and have a latest observation no older than 62 days. Daily price data must contain at least 200 positive observations and volatility data at least 20, with the latest date no older than seven days. Fetch, parse, or validation failures preserve the previous valid cache and mark the published data as stale instead of clearing inputs.

---

## 3. Disclaimer 

* **Data Sourcing**: PE comes from a cached third-party source; NDX prices come from Nasdaq, while SPX/VIX/VXN come from Cboe. Data may be delayed or unavailable, and users remain responsible for verification.
* **Non-Financial Advice**: This application is for **entertainment and educational purposes only**. It does not constitute investment or financial advice. All investments involve risk.

---

## 1. 计算公式 

系统总分为 100 分，分数越高代表当前定投性价比越高。

### 估值面 (满分 30)
* **PE 得分** = $30 \times (1 - \text{PE 十年百分位})$
    * 逻辑：买低卖高。估值分位越低，得分越高。

### 趋势面 (满分 40)
* **均线偏离度** = $((\text{现价} - \text{MA200}) / \text{MA200}) \times 100\%$
* **MA 得分** = $\max(0, \min(40, 20 - \text{偏离度} \times 2))$
    * 逻辑：衡量价格偏离 200 日均线的程度，鼓励在市场超跌时买入。

### 情绪面 (满分 30)
* **标普 500 (VIX 得分)** = $\max(0, \min(30, (\text{VIX} - 15) \times 2))$
* **纳指 100 (VXN 得分)** = $\max(0, \min(30, ((\text{VXN} - 20) / 20) \times 30))$
    * 逻辑：“在别人恐惧时贪婪”。波动率（恐慌感）越高，得分越高。

---

## 2. 使用方法 

1.  **打开应用**：直接在任何现代浏览器（Chrome, Safari, Edge 等）中打开 `.html` 文件。
2.  **输入数据**：系统从已提交的市场数据缓存自动填入 NDX/SPX 的 PE 十年百分位、最新收盘价、MA200、VIX 与 VXN。所有字段均可手动修改。
3.  **计算评分**：点击“计算评分”按钮，查看实时等级 (A-E) 以及对应的操作建议。
4.  **历史管理**：系统会自动将评测记录保存在浏览器本地（最高支持 365 条）。
5.  **个性化设置**：通过底部滑块切换 NDX/SPX；通过右上角菜单切换黑夜模式及中英文。

### 市场数据自动更新

项目维护两层缓存：

* `data/cache/*_pe_history.json`：从 World PE Ratio 获取并校验的月度 PE 历史序列。
* `data/cache/*_history.json`：从 Nasdaq/Cboe 获取并校验的 NDX/SPX 日线与 VIX/VXN 收盘数据；价格缓存至少保留 200 个交易日用于计算 MA200。
* `data/market-data.json`：页面使用的精简数据；同时生成内容一致的 `data/market-data.js`，确保直接双击 HTML 时也能自动加载。

使用 Python 3.10 或更高版本在本地更新：

```powershell
python scripts\update_market_data.py
```

更新器默认复用 24 小时内的有效原始缓存。`--force` 可立即刷新，`--force --offline` 可在不访问网络的情况下验证缓存降级。GitHub Actions 每天检查一次并提交生成的数据变更。

PE 数据必须至少包含 120 条大于零、日期严格递增的月度记录，最近 120 条需覆盖约十年，月度间隔不得超过 62 天，最新数据不得早于当前日期 62 天。日线价格至少需要 200 条正数记录，波动率至少需要 20 条，最新日期不得早于当前日期七天。抓取、解析或校验失败时保留上次有效缓存，并将应用数据标为过期，不会清空页面输入。

---

## 3. 声明 

* **数据来源**：PE 来自缓存的第三方数据源，NDX 行情来自 Nasdaq，SPX/VIX/VXN 来自 Cboe；数据可能存在延迟或暂时不可用，用户仍需自行核实。
* **投资风险**：本应用**仅供娱乐与教学使用**，不构成任何投资理财建议。市场有风险，投资需谨慎。

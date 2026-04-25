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
2.  **Input Market Data**: Find and enter the required parameters (PE Percentile, Price, MA200, VIX/VXN) from your preferred financial data source.
3.  **Calculate**: Click the "Calculate" button to view the real-time rating (A-E) and professional action advice.
4.  **Manage History**: Your assessment history is automatically saved to your local browser storage (up to 365 entries).
5.  **Personalization**: Use the bottom slider to switch between NDX and SPX. Use the top controls to toggle Dark Mode and Language (CN/EN).

---

## 3. Disclaimer 

* **Data Sourcing**: Users are responsible for finding and verifying all market parameters.
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
2.  **输入数据**：从金融数据平台查找并输入对应参数（PE 百分位、现价、MA200、VIX/VXN）。
3.  **计算评分**：点击“计算评分”按钮，查看实时等级 (A-E) 以及对应的操作建议。
4.  **历史管理**：系统会自动将评测记录保存在浏览器本地（最高支持 365 条）。
5.  **个性化设置**：通过底部滑块切换 NDX/SPX；通过右上角菜单切换黑夜模式及中英文。

---

## 3. 声明 

* **数据来源**：请用户自行查找并确认相关市场参数。
* **投资风险**：本应用**仅供娱乐与教学使用**，不构成任何投资理财建议。市场有风险，投资需谨慎。

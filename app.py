```python
import math
from datetime import datetime, timezone
import requests
import streamlit as st

st.set_page_config(page_title="Kalshi Edge Finder", layout="wide")
st.title("Kalshi Edge Finder — Top Picks")

KALSHI = "https://api.elections.kalshi.com/trade-api/v2/markets"
SERIES = ["KXBTCD", "KXBTC"]

bankroll = st.sidebar.number_input("Bankroll ($)", value=100.0, step=10.0)
min_edge = st.sidebar.slider("Minimum edge (cents)", 1, 20, 5)
vol = st.sidebar.slider("BTC yearly volatility (%)", 20, 120, 55) / 100
max_picks = st.sidebar.slider("How many picks", 1, 10, 5)

def btc_price():
    r = requests.get("https://api.coinbase.com/v2/prices/BTC-USD/spot", timeout=10)
    return float(r.json()["data"]["amount"])

def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def prob_above(spot, strike, hours):
    t = max(hours, 0.01) / (24 * 365)
    d = (math.log(spot / strike) - 0.5 * vol**2 * t) / (vol * math.sqrt(t))
    return norm_cdf(d)

def get_markets():
    out = []
    for s in SERIES:
        r = requests.get(KALSHI, params={"series_ticker": s, "status": "open", "limit": 500}, timeout=15)
        out += r.json().get("markets", [])
    return out

@st.cache_data(ttl=60)
def find_picks():
    spot = btc_price()
    now = datetime.now(timezone.utc)
    picks = []
    for m in get_markets():
        close = datetime.fromisoformat(m["close_time"].replace("Z", "+00:00"))
        hours = (close - now).total_seconds() / 3600
        if hours < 1:
            continue
        lo, hi = m.get("floor_strike"), m.get("cap_strike")
        if lo and hi:
            p = prob_above(spot, lo, hours) - prob_above(spot, hi, hours)
            label = f"BTC between ${lo:,.0f} and ${hi:,.0f}"
        elif lo:
            p = prob_above(spot, lo, hours)
            label = f"BTC above ${lo:,.0f}"
        elif hi:
            p = 1 - prob_above(spot, hi, hours)
            label = f"BTC below ${hi:,.0f}"
        else:
            continue
        fair = round(p * 100)
        for side, ask, value in [("YES", m.get("yes_ask"), fair), ("NO", m.get("no_ask"), 100 - fair)]:
            if not ask or ask <= 0 or ask >= 100:
                continue
            edge = value - ask
            if edge >= min_edge:
                picks.append(dict(side=side, label=label, ask=ask, value=value,
                                  edge=edge, hours=hours, ticker=m["ticker"]))
    picks.sort(key=lambda x: x["edge"], reverse=True)
    return spot, picks

spot, picks = find_picks()
st.caption(f"BTC now: ${spot:,.0f} — refreshes every 60 seconds")

if not picks:
    st.warning(f"No bets with at least {min_edge}¢ edge right now. Sitting out is a valid move.")
else:
    for p in picks[:max_picks]:
        stake = round(min(bankroll * 0.05, bankroll * p["edge"] / 100), 2)
        contracts = int(stake * 100 // p["ask"])
        h = p["hours"]
        when = f"{h:.1f} hours" if h < 48 else f"{h/24:.1f} days"
        st.success(
            f"**Buy {p['side']}** on *{p['label']}* at **{p['ask']}¢**  \n"
            f"Estimated worth: {p['value']}¢ (edge {p['edge']}¢)  \n"
            f"Suggested: **{contracts} contracts ≈ ${stake}**, closes in {when}  \n"
            f"Ticker: `{p['ticker']}`"
        )

st.info("Estimates come from a simple price model, not a guarantee. Never bet more than you can lose.")
```

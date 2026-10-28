import json, math, itertools, difflib
from datetime import datetime, timezone
import requests, pandas as pd, streamlit as st
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="Kalshi Edge Finder", layout="wide")
st_autorefresh(interval=5 * 60 * 1000, key="refresh")

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
MIN_EDGE = st.sidebar.slider("Minimum edge %", 0, 20, 3) / 100
MIN_WIN = st.sidebar.slider("Minimum combo win chance %", 5, 80, 25) / 100
MAX_LEGS = st.sidebar.slider("Max legs per combo", 1, 4, 3)
STAKE = st.sidebar.number_input("Stake $", value=10.0)

def fee(p):
    return math.ceil(0.07 * p * (1 - p) * 100) / 100

def price(m, side):
    v = m.get(f"{side}_dollars") or (m.get(side) or 0) / 100
    return float(v or 0)

@st.cache_data(ttl=300)
def kalshi_markets():
    out = {}
    for series in ["KXBTCD", "KXBTC"]:
        r = requests.get(f"{KALSHI}/markets", params={"series_ticker": series, "status": "open", "limit": 1000}, timeout=20).json()
        for m in r.get("markets", []): out[m["ticker"]] = m
    cursor = None
    for _ in range(10):
        r = requests.get(f"{KALSHI}/markets", params={"status": "open", "limit": 1000, "cursor": cursor, "mve_filter": "exclude"}, timeout=20).json()
        for m in r.get("markets", []): out[m["ticker"]] = m
        cursor = r.get("cursor")
        if not cursor: break
    return list(out.values())

@st.cache_data(ttl=300)
def btc_data():
    spot = requests.get("https://www.deribit.com/api/v2/public/get_index_price", params={"index_name": "btc_usd"}, timeout=10).json()["result"]["index_price"]
    hv = requests.get("https://www.deribit.com/api/v2/public/get_historical_volatility", params={"currency": "BTC"}, timeout=10).json()["result"]
    return spot, hv[-1][1] / 100

@st.cache_data(ttl=300)
def polymarket():
    r = requests.get("https://gamma-api.polymarket.com/markets", params={"active": "true", "closed": "false", "limit": 500}, timeout=20).json()
    rows = []
    for m in r:
        try: rows.append((m["question"], float(json.loads(m["outcomePrices"])[0])))
        except Exception: pass
    return rows

def N(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def p_above(S, K, vol, years):
    if years <= 0: return 1.0 if S > K else 0.0
    d2 = (math.log(S / K) - 0.5 * vol**2 * years) / (vol * math.sqrt(years))
    return N(d2)

st.title("Kalshi Edge Finder")
markets = kalshi_markets()
spot, vol = btc_data()
poly = polymarket()
poly_q = [q for q, _ in poly]
n_btc = sum(1 for m in markets if m["ticker"].startswith(("KXBTCD", "KXBTC-")))
st.caption(f"Updated {datetime.now().strftime('%H:%M')} | {len(markets)} Kalshi markets ({n_btc} Bitcoin) | {len(poly)} Polymarket | BTC ${spot:,.0f} | vol {vol:.0%}")

legs = []
now = datetime.now(timezone.utc)
for m in markets:
    ask = price(m, "yes_ask")
    if not 0.03 < ask < 0.97: continue
    cost = ask + fee(ask)
    fair, source, group = None, None, None
    if m["ticker"].startswith(("KXBTCD", "KXBTC-")):
        try:
            years = (datetime.fromisoformat(m["close_time"].replace("Z", "+00:00")) - now).total_seconds() / 31557600
            lo, hi, kind = m.get("floor_strike"), m.get("cap_strike"), m.get("strike_type")
            if kind == "greater": fair = p_above(spot, lo, vol, years)
            elif kind == "less": fair = 1 - p_above(spot, hi, vol, years)
            elif kind == "between": fair = p_above(spot, lo, vol, years) - p_above(spot, hi, vol, years)
            source, group = "BTC model", "BTC"
        except Exception: pass
    else:
        hit = difflib.get_close_matches(m.get("title", ""), poly_q, n=1, cutoff=0.6)
        if hit:
            fair = dict(poly)[hit[0]]
            source, group = f"Polymarket: {hit[0][:60]}", m.get("event_ticker")
    if fair is None: continue
    edge = fair / cost - 1
    if edge >= MIN_EDGE:
        legs.append({"Market": m.get("title", m["ticker"]), "Ticker": m["ticker"], "Buy YES at": ask,
                     "Fair odds": round(fair, 3), "Edge %": round(edge * 100, 1), "Source": source,
                     "group": group, "cost": cost, "fair": fair})

st.header("Best single bets")
if not legs:
    st.info("No underpriced bets right now. That's normal, check back later.")
    st.stop()
df = pd.DataFrame(legs).sort_values("Edge %", ascending=False)
st.dataframe(df.drop(columns=["group", "cost", "fair"]), use_container_width=True)

st.header("Best combos")
top = df.head(15).to_dict("records")
combos = []
for n in range(2, MAX_LEGS + 1):
    for c in itertools.combinations(top, n):
        if len({x["group"] for x in c}) < n: continue
        win = math.prod(x["fair"] for x in c)
        if win < MIN_WIN: continue
        payout = STAKE / math.prod(x["cost"] for x in c)
        combos.append({"Combo": " + ".join(x["Ticker"] for x in c), "Win chance %": round(win * 100, 1),
                       "Payout if win $": round(payout, 2), "Expected profit $": round(win * payout - STAKE, 2)})
if combos:
    st.dataframe(pd.DataFrame(combos).sort_values("Expected profit $", ascending=False).head(25), use_container_width=True)
else:
    st.info("No combos pass your win-chance filter. The single bets above are your play.")

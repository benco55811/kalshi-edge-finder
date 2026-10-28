
import json, math, itertools, re
from collections import defaultdict
from datetime import datetime, timezone
import requests, pandas as pd, streamlit as st
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="Kalshi Edge Finder", layout="wide")
st_autorefresh(interval=5 * 60 * 1000, key="refresh")

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
MIN_EDGE = st.sidebar.slider("Minimum edge %", 0, 20, 5) / 100
MIN_HOURS = st.sidebar.slider("Skip markets closing within (hours)", 0, 24, 1)
MIN_WIN = st.sidebar.slider("Minimum combo win chance %", 5, 80, 25) / 100
MAX_LEGS = st.sidebar.slider("Max legs per combo", 1, 4, 3)
STAKE = st.sidebar.number_input("Stake $", value=10.0)

STOP = {"will", "the", "a", "an", "of", "in", "on", "by", "to", "be", "or", "and", "for", "at", "is", "before", "after", "than", "more", "less"}

def words(s):
    return set(re.findall(r"[a-z0-9]+", (s or "").lower())) - STOP

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
    rows = []
    for offset in range(0, 2000, 500):
        r = requests.get("https://gamma-api.polymarket.com/markets", params={"active": "true", "closed": "false", "limit": 500, "offset": offset}, timeout=20).json()
        if not r: break
        for m in r:
            try: rows.append((m["question"], float(json.loads(m["outcomePrices"])[0])))
            except Exception: pass
    return rows

def N(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def p_above(S, K, vol, years):
    if years <= 0: return 1.0 if S > K else 0.0
    d2 = (math.log(S / K) - 0.5 * vol**2 * years) / (vol * math.sqrt(years))
    return N(d2)

def local_close(m):
    t = datetime.fromisoformat(m["close_time"].replace("Z", "+00:00")).astimezone()
    return t.strftime("%a %I:%M %p")

st.title("Kalshi Edge Finder")
with st.spinner("Crunching the numbers..."):
    markets = kalshi_markets()
    spot, vol = btc_data()
    poly = polymarket()

    poly_words = [words(q) for q, _ in poly]
    index = defaultdict(list)
    for i, ws in enumerate(poly_words):
        for w in ws: index[w].append(i)

    def best_poly(title):
        tw = words(title)
        if len(tw) < 3: return None
        counts = defaultdict(int)
        for w in tw:
            for i in index.get(w, []): counts[i] += 1
        best, score = None, 0
        for i, c in counts.items():
            j = c / len(tw | poly_words[i])
            if j > score: best, score = i, j
        return best if score >= 0.6 else None

    n_btc = sum(1 for m in markets if m["ticker"].startswith(("KXBTCD", "KXBTC-")))
    legs = []
    now = datetime.now(timezone.utc)
    for m in markets:
        try:
            hours_left = (datetime.fromisoformat(m["close_time"].replace("Z", "+00:00")) - now).total_seconds() / 3600
        except Exception:
            continue
        if hours_left < MIN_HOURS: continue
        fair_yes, source, group, yes_text, no_text = None, None, None, None, None
        if m["ticker"].startswith(("KXBTCD", "KXBTC-")):
            try:
                years = hours_left / 8766
                lo, hi, kind = m.get("floor_strike"), m.get("cap_strike"), m.get("strike_type")
                when = local_close(m)
                if kind == "greater":
                    fair_yes = p_above(spot, lo, vol, years)
                    yes_text, no_text = f"Bitcoin ABOVE ${lo:,.0f} at {when}", f"Bitcoin BELOW ${lo:,.0f} at {when}"
                elif kind == "less":
                    fair_yes = 1 - p_above(spot, hi, vol, years)
                    yes_text, no_text = f"Bitcoin BELOW ${hi:,.0f} at {when}", f"Bitcoin ABOVE ${hi:,.0f} at {when}"
                elif kind == "between":
                    fair_yes = p_above(spot, lo, vol, years) - p_above(spot, hi, vol, years)
                    yes_text = f"Bitcoin BETWEEN ${lo:,.0f}-${hi:,.0f} at {when}"
                    no_text = f"Bitcoin OUTSIDE ${lo:,.0f}-${hi:,.0f} at {when}"
                source, group = "BTC model", "BTC"
            except Exception: pass
        else:
            i = best_poly(m.get("title", ""))
            if i is not None:
                fair_yes = poly[i][1]
                title = m.get("title", m["ticker"])
                yes_text, no_text = f"YES: {title}", f"NO: {title}"
                source, group = f"Polymarket: {poly[i][0][:60]}", m.get("event_ticker")
        if fair_yes is None: continue
        closes = f"{hours_left:.1f} h" if hours_left < 48 else f"{hours_left/24:.1f} days"
        for side, ask, fair, text in [("YES", price(m, "yes_ask"), fair_yes, yes_text),
                                      ("NO", price(m, "no_ask"), 1 - fair_yes, no_text)]:
            if not 0.03 < ask < 0.97: continue
            cost = ask + fee(ask)
            edge = fair / cost - 1
            if edge >= MIN_EDGE:
                legs.append({"Pick": text, "Buy": f"{side} at {ask*100:.0f}¢", "Win chance %": round(fair * 100, 1),
                             "Edge %": round(edge * 100, 1), "Closes in": closes, "Ticker": m["ticker"],
                             "Source": source, "group": group, "cost": cost, "fair": fair})

st.caption(f"Updated {datetime.now(timezone.utc).strftime('%H:%M')} UTC | {len(markets)} Kalshi markets ({n_btc} Bitcoin) | {len(poly)} Polymarket | BTC ${spot:,.0f} | vol {vol:.0%}")

st.header("Best single bets")
if not legs:
    st.info("No underpriced bets right now. That's normal, check back later.")
    st.stop()
df = pd.DataFrame(legs).sort_values("Edge %", ascending=False)
st.dataframe(df.drop(columns=["group", "cost", "fair"]), use_container_width=True, hide_index=True)

st.header("Best combos")
top = df.head(15).to_dict("records")
combos = []
for n in range(2, MAX_LEGS + 1):
    for c in itertools.combinations(top, n):
        if len({x["group"] for x in c}) < n: continue
        win = math.prod(x["fair"] for x in c)
        if win < MIN_WIN: continue
        payout = STAKE / math.prod(x["cost"] for x in c)
        combos.append({"Combo": "  +  ".join(f'{x["Pick"]} ({x["Buy"]})' for x in c), "Win chance %": round(win * 100, 1),
                       "Payout if win $": round(payout, 2), "Expected profit $": round(win * payout - STAKE, 2)})
if combos:
    st.dataframe(pd.DataFrame(combos).sort_values("Expected profit $", ascending=False).head(25), use_container_width=True, hide_index=True)
else:
    st.info("No combos pass your win-chance filter. The single bets above are your play.")

import aiohttp, asyncio, pandas as pd, ta, datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

TELEGRAM_TOKEN = "8581743742:AAFfd7uJ6W93Tfnoa1tuvQqh6nmRm_wwgHA"
TWELVEDATA_KEY = "064dbdcd38734f70b71db008e502f5e0"
NEWS_API_KEY = "qDGIzb9o2OttTxWNvBLMDyZD9KbdQ0qaPHvupsjH"

MIN_CANDLES = 300
MIN_ATR_PCT = 0.2
SCAN_INTERVAL = 300
TRACK_INTERVAL = 300
NEWS_LIMIT = 3

CRYPTOS = {
"BTCUSDT":"BTC/USD","ETHUSDT":"ETH/USD","BNBUSDT":"BNB/USD","XRPUSDT":"XRP/USD","SOLUSDT":"SOL/USD",
"ADAUSDT":"ADA/USD","DOGEUSDT":"DOGE/USD","AVAXUSDT":"AVAX/USD","DOTUSDT":"DOT/USD","MATICUSDT":"MATIC/USD",
"LTCUSDT":"LTC/USD","LINKUSDT":"LINK/USD","TRXUSDT":"TRX/USD","ATOMUSDT":"ATOM/USD","UNIUSDT":"UNI/USD",
"ICPUSDT":"ICP/USD","APTUSDT":"APT/USD","NEARUSDT":"NEAR/USD","ARBUSDT":"ARB/USD","OPUSDT":"OP/USD",
"SUIUSDT":"SUI/USD","INJUSDT":"INJ/USD","AAVEUSDT":"AAVE/USD","GRTUSDT":"GRT/USD","FILUSDT":"FIL/USD",
"ETCUSDT":"ETC/USD","XLMUSDT":"XLM/USD","ALGOUSDT":"ALGO/USD","HBARUSDT":"HBAR/USD","VETUSDT":"VET/USD",
"EOSUSDT":"EOS/USD","XTZUSDT":"XTZ/USD","THETAUSDT":"THETA/USD","EGLDUSDT":"EGLD/USD","KASUSDT":"KAS/USD",
"FLOWUSDT":"FLOW/USD","AXSUSDT":"AXS/USD","MKRUSDT":"MKR/USD","SNXUSDT":"SNX/USD","RUNEUSDT":"RUNE/USD",
"PEPEUSDT":"PEPE/USD","SHIBUSDT":"SHIB/USD","FTMUSDT":"FTM/USD","SANDUSDT":"SAND/USD","MANAUSDT":"MANA/USD",
"CHZUSDT":"CHZ/USD","CRVUSDT":"CRV/USD","DYDXUSDT":"DYDX/USD","KAVAUSDT":"KAVA/USD","ZILUSDT":"ZIL/USD"
}

active_trades = {}
stats = {"wins":0,"losses":0,"be":0}

def session_ok():
    return True

async def fetch(session, symbol, interval):
    try:
        async with session.get(
            "https://api.twelvedata.com/time_series",
            params={"symbol":CRYPTOS[symbol],"interval":interval,"outputsize":500,"apikey":TWELVEDATA_KEY},
            timeout=15
        ) as r:
            j = await r.json()
            if "values" not in j:
                return pd.DataFrame()
            rows=[{"c":float(v["close"]),"h":float(v["high"]),"l":float(v["low"])} for v in reversed(j["values"])]
            return pd.DataFrame(rows)
    except:
        return pd.DataFrame()

def enrich(df):
    if len(df) < MIN_CANDLES:
        return pd.DataFrame()
    df["EMA50"]=ta.trend.EMAIndicator(df["c"],50).ema_indicator()
    df["EMA200"]=ta.trend.EMAIndicator(df["c"],200).ema_indicator()
    df["RSI"]=ta.momentum.RSIIndicator(df["c"],14).rsi()
    macd=ta.trend.MACD(df["c"])
    df["MACD"]=macd.macd_diff()
    bb=ta.volatility.BollingerBands(df["c"],20,2)
    df["BBM"]=bb.bollinger_mavg()
    df["BBH"]=bb.bollinger_hband()
    df["BBL"]=bb.bollinger_lband()
    df["ATR"]=ta.volatility.AverageTrueRange(df["h"],df["l"],df["c"]).average_true_range()
    return df.dropna()

def structure(df):
    highs=df["h"].rolling(5).max()
    lows=df["l"].rolling(5).min()
    hh=highs.iloc[-1]>highs.iloc[-6]
    hl=lows.iloc[-1]>lows.iloc[-6]
    lh=highs.iloc[-1]<highs.iloc[-6]
    ll=lows.iloc[-1]<lows.iloc[-6]
    if hh and hl:
        return "BUY"
    if lh and ll:
        return "SELL"
    return None

def trend_bias(df):
    last=df.iloc[-1]
    return "BUY" if last["EMA50"]>last["EMA200"] else "SELL"

def entry_signal(df, direction):
    last=df.iloc[-1]
    score=0
    if direction=="BUY" and 50<last["RSI"]<75:
        score+=1
    if direction=="SELL" and 25<last["RSI"]<50:
        score+=1
    if direction=="BUY" and last["MACD"]>0:
        score+=1
    if direction=="SELL" and last["MACD"]<0:
        score+=1
    if direction=="BUY" and last["c"]>last["BBM"]:
        score+=1
    if direction=="SELL" and last["c"]<last["BBM"]:
        score+=1
    return score

def build_signal(df1d, df4h, df1h):
    if df1d.empty or df4h.empty or df1h.empty:
        return None
    dir_daily=trend_bias(df1d)
    dir_4h=structure(df4h)
    if not dir_4h or dir_daily!=dir_4h:
        return None
    entry_score=entry_signal(df1h, dir_4h)
    if entry_score<2:
        return None
    last=df1h.iloc[-1]
    atr_pct=(last["ATR"]/last["c"])*100
    if atr_pct<MIN_ATR_PCT:
        return None
    confidence=int(((2+entry_score)/5)*100)
    entry=last["c"]
    atr=last["ATR"]
    sl=entry-atr*1.5 if dir_4h=="BUY" else entry+atr*1.5
    tp=entry+atr*3 if dir_4h=="BUY" else entry-atr*3
    return {"dir":dir_4h,"entry":entry,"sl":sl,"tp":tp,"confidence":confidence,"atr":atr}

async def multi_tf_signal(session,symbol):
    df1d=enrich(await fetch(session,symbol,"1day"))
    df4h=enrich(await fetch(session,symbol,"4h"))
    df1h=enrich(await fetch(session,symbol,"1h"))
    return build_signal(df1d,df4h,df1h)

async def fetch_news(session, symbol, limit=NEWS_LIMIT):
    try:
        query = symbol.replace("USDT","") + " crypto"
        async with session.get(
            "https://newsapi.org/v2/everything",
            params={"q":query,"pageSize":limit,"sortBy":"publishedAt","apiKey":NEWS_API_KEY},
            timeout=10
        ) as r:
            j = await r.json()
            if "articles" not in j:
                return []
            return [{"title":a["title"],"url":a["url"]} for a in j["articles"]]
    except:
        return []

async def scan(context):
    signals=[]
    async with aiohttp.ClientSession() as s:
        for sym in CRYPTOS:
            if sym in active_trades:
                continue
            sig=await multi_tf_signal(s,sym)
            if sig:
                signals.append((sym,sig))
    signals.sort(key=lambda x: x[1]["confidence"],reverse=True)
    top=signals[:7]
    if top:
        msg="🚀 MULTI-TF SWING SIGNALS\n\n"
        async with aiohttp.ClientSession() as s:
            for i,(sym,sig) in enumerate(top,1):
                news=await fetch_news(s,sym)
                msg+=f"{i}. {sym}\nDirection: {sig['dir']}\nEntry: {round(sig['entry'],6)}\nSL: {round(sig['sl'],6)}\nTP: {round(sig['tp'],6)}\nConfidence: {sig['confidence']}%\n"
                if news:
                    msg+="📰 News:\n"
                    for n in news:
                        msg+=f"{n['title']}\n{n['url']}\n"
                msg+="\n"
        await context.bot.send_message(chat_id=context.job.chat_id,text=msg)
        for sym,sig in top:
            active_trades[sym]=sig

async def track(context):
    async with aiohttp.ClientSession() as s:
        for sym in list(active_trades):
            df=await fetch(s,sym,"1h")
            if df.empty:
                continue
            price=float(df["c"].iloc[-1])
            t=active_trades[sym]
            if not t.get("be"):
                if t["dir"]=="BUY" and price>=t["entry"]+t["atr"]:
                    t["sl"]=t["entry"]
                    t["be"]=True
                if t["dir"]=="SELL" and price<=t["entry"]-t["atr"]:
                    t["sl"]=t["entry"]
                    t["be"]=True
            tp=price>=t["tp"] if t["dir"]=="BUY" else price<=t["tp"]
            sl=price<=t["sl"] if t["dir"]=="BUY" else price>=t["sl"]
            if tp or sl:
                if tp:
                    stats["wins"]+=1
                elif t.get("be"):
                    stats["be"]+=1
                else:
                    stats["losses"]+=1
                total=stats["wins"]+stats["losses"]
                wr=round((stats["wins"]/total)*100,2) if total else 0
                await context.bot.send_message(
                    chat_id=context.job.chat_id,
                    text=f"{sym} {'TP' if tp else 'SL'} @ {round(price,6)}\nWins {stats['wins']} Loss {stats['losses']} BE {stats['be']}\nWinRate {wr}%"
                )
                del active_trades[sym]

async def start(update:Update,context:ContextTypes.DEFAULT_TYPE):
    kb=[[InlineKeyboardButton(k,callback_data=k)] for k in CRYPTOS]
    await update.message.reply_text("📡 Click crypto to analyze or wait for swing signals:",reply_markup=InlineKeyboardMarkup(kb))
    context.job_queue.run_repeating(scan,SCAN_INTERVAL,chat_id=update.effective_chat.id)
    context.job_queue.run_repeating(track,TRACK_INTERVAL,chat_id=update.effective_chat.id)

async def analyze_callback(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    await q.answer()
    sym=q.data
    async with aiohttp.ClientSession() as s:
        sig=await multi_tf_signal(s,sym)
        if not sig:
            await q.edit_message_text(f"⚠️ No clear signal for {sym} on 1D/4H/1H")
            return
        active_trades[sym]=sig
        news=await fetch_news(s,sym)
        msg=f"{sym} Multi-TF Analysis\nDirection: {sig['dir']}\nEntry: {round(sig['entry'],6)}\nSL: {round(sig['sl'],6)}\nTP: {round(sig['tp'],6)}\nConfidence: {sig['confidence']}%\n"
        if news:
            msg+="📰 News:\n"
            for n in news:
                msg+=f"{n['title']}\n{n['url']}\n"
        await q.edit_message_text(msg)

if __name__=="__main__":
    app=ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CallbackQueryHandler(analyze_callback))
    app.run_polling()

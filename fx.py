import json
from datetime import datetime, timezone
import pandas as pd
import yfinance as yf

PAIRS = {'USD/JPY':'JPY=X','MXN/JPY':'MXNJPY=X','AUD/USD':'AUDUSD=X','EUR/USD':'EURUSD=X','AUD/JPY':'AUDJPY=X'}

def indicators(df, minutes, now):
    df = df.sort_index()
    df = df[~df.index.duplicated(keep='last')].dropna(subset=['Open','High','Low','Close'])
    df = df[df.index + pd.Timedelta(minutes=minutes) <= now]
    if len(df) < 75:
        raise ValueError('Insufficient candles')
    if ((df.High < df[['Open','Close','Low']].max(axis=1)) | (df.Low > df[['Open','Close','High']].min(axis=1)) | (df.Low <= 0)).any():
        raise ValueError('Invalid OHLC')
    c = df.Close
    age = (now - df.index[-1] - pd.Timedelta(minutes=minutes)).total_seconds()/60
    tr = pd.concat([df.High-df.Low,(df.High-c.shift()).abs(),(df.Low-c.shift()).abs()],axis=1).max(axis=1)
    atr = tr.iloc[:14].mean()
    for v in tr.iloc[14:]:
        atr = (atr*13+v)/14
    mean = c.iloc[-20:].mean()
    sd = c.iloc[-20:].std(ddof=0)
    return {'candles':len(df),'last_closed':(df.index[-1]+pd.Timedelta(minutes=minutes)).isoformat(),'age_minutes':age,'fresh':age <= minutes+15,'close':float(c.iloc[-1]),'wma':{str(n):float(sum(v*w for w,v in enumerate(c.iloc[-n:],1))/(n*(n+1)/2)) for n in (5,25,75)},'atr14':float(atr),'bb20':{'mean':float(mean),'bands':{str(n):[float(mean-n*sd),float(mean+n*sd)] for n in (1,2,3)}},'recent_low':float(df.Low.iloc[-20:].min()),'recent_high':float(df.High.iloc[-20:].max())}

def main():
    now = pd.Timestamp.now(tz='UTC')
    report = {'generated_at':now.isoformat(),'pairs':{}}
    failed = False
    for pair,symbol in PAIRS.items():
        result = {}
        report['pairs'][pair] = result
        for label,interval,period,minutes in [('5m','5m','5d',5),('15m','15m','10d',15),('4h','1h','60d',240)]:
            try:
                df = yf.Ticker(symbol).history(period=period,interval=interval,auto_adjust=False,raise_errors=True)
                if df.empty: raise ValueError('Empty provider response')
                df.index = df.index.tz_convert('UTC')
                if label == '4h':
                    df = df[df.index+pd.Timedelta(hours=1)<=now]
                    grouped = df.resample('4h')
                    df = grouped.agg({'Open':'first','High':'max','Low':'min','Close':'last'})
                    df = df[grouped.Close.count()==4]
                result[label] = indicators(df,minutes,now)
                if not result[label]['fresh']: failed = True
            except Exception as e:
                result[label] = {'error':str(e)}
                failed = True
    with open('report.json','w') as f:
        json.dump(report,f,indent=2,allow_nan=False)
    print(json.dumps(report,indent=2))
    return int(failed)

if __name__ == '__main__':
    raise SystemExit(main())

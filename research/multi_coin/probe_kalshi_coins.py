import requests
for coin in ['BTC','ETH','SOL','XRP','DOGE','BNB','HYPE']:
    ticker = f'KX{coin}15M'
    r = requests.get(
        'https://api.elections.kalshi.com/trade-api/v2/markets',
        params={'series_ticker': ticker, 'status': 'open', 'limit': 1},
        timeout=8
    )
    if r.status_code == 200:
        markets = r.json().get('markets', [])
        n = len(markets)
        title = markets[0].get('title', '-')[:50] if markets else '-'
        print(f'{coin:5s} ({ticker:12s}): {n} active | {title}')
    else:
        print(f'{coin:5s}: HTTP {r.status_code}')

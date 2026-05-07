import urllib.request, re

url = 'https://predict.fun/market/btc-updown-15m-1778164200'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 Chrome/126'})
html = urllib.request.urlopen(req, timeout=10).read().decode('utf-8', errors='ignore')
print('page size:', len(html))

patterns = [
    ('open price', r'open.{0,50}?\$([\d,]+\.?\d*)'),
    ('strike', r'strike.{0,50}?\$([\d,]+\.?\d*)'),
    ('reference', r'reference.{0,50}?\$([\d,]+\.?\d*)'),
    ('compared to', r'compared to.{0,30}?\$([\d,]+\.?\d*)'),
    ('starting', r'starting.{0,50}?\$([\d,]+\.?\d*)'),
    ('BTC at', r'BTC at.{0,30}?\$([\d,]+\.?\d*)'),
    ('opened at', r'opened at.{0,50}?\$([\d,]+\.?\d*)'),
    ('price was', r'price was.{0,50}?\$([\d,]+\.?\d*)'),
]
for name, pat in patterns:
    matches = re.findall(pat, html, re.IGNORECASE)
    if matches:
        print(f'{name}: {matches[:5]}')

prices = set(re.findall(r'\$([7-9][0-9],[0-9]{3})', html))
print(f'BTC-like prices: {sorted(prices)}')

# Look for the structured data
for pat in ['"strike":\s*"?([0-9.]+)', '"openPrice":\s*"?([0-9.]+)', '"referencePrice":\s*"?([0-9.]+)']:
    m = re.findall(pat, html)
    if m:
        print(f'{pat}: {m[:3]}')

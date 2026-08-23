"""Fix broken crypto/index price buttons in the final v25 price layer."""
p = 'bot.py'
src = open(p, encoding='utf-8').read()

# ---- A) Final fetch_price_v25: support crypto & indices via legacy multi-source chain ----
old_head = '''async def fetch_price_v25(asset):
    # Gold/coin use TGJU's explicit daily/current page. No manual correction is applied.
    if asset in ("gold18","coin"):'''
new_head = '''async def fetch_price_v25(asset):
    # Crypto & index assets delegate to the legacy multi-source fetcher
    # (Nobitex / CoinGecko+TGJU / Yahoo), whose "<value> <unit>" output is
    # parsed back into the (value, unit, confidence) tuple contract.
    if asset in ("btc","eth","usdt","bnb","sol","xrp","sp500","nasdaq","dow"):
        txt = await fetch_price(asset)
        m = re.match(r"^([\\d,.]+)\\s*(.+)$", str(txt).strip())
        if not m:
            raise ValueError(f"unparsed price for {asset}: {txt!r}")
        return float(m.group(1).replace(",", "")), m.group(2).strip(), "single"
    # Gold/coin use TGJU's explicit daily/current page. No manual correction is applied.
    if asset in ("gold18","coin"):'''
assert src.count(old_head) == 1, 'final fetch_price_v25 head not found'
src = src.replace(old_head, new_head)

# ---- B) Labels for every button that prices_keyboard can produce ----
old_names = '''names={'usd':'دلار','eur':'یورو','gold18':'طلای ۱۸ عیار','coin':'سکه امامی','silver':'نقره','copper':'مس','aluminum':'آلومینیوم','nickel':'نیکل','zinc':'روی','lead':'سرب'}; names_en={'usd':'USD','eur':'EUR','gold18':'18K Gold','coin':'Emami Coin','silver':'Silver','copper':'Copper','aluminum':'Aluminum','nickel':'Nickel','zinc':'Zinc','lead':'Lead'}'''
new_names = '''names={'usd':'دلار','eur':'یورو','gold18':'طلای ۱۸ عیار','coin':'سکه امامی','silver':'نقره','copper':'مس','aluminum':'آلومینیوم','nickel':'نیکل','zinc':'روی','lead':'سرب','btc':'BTC (بازار ایران)','eth':'ETH (بازار ایران)','usdt':'USDT','bnb':'BNB','sol':'Solana','xrp':'XRP','sp500':'S&P 500','nasdaq':'Nasdaq','dow':'Dow Jones'}; names_en={'usd':'USD','eur':'EUR','gold18':'18K Gold','coin':'Emami Coin','silver':'Silver','copper':'Copper','aluminum':'Aluminum','nickel':'Nickel','zinc':'Zinc','lead':'Lead','btc':'BTC','eth':'ETH','usdt':'USDT','bnb':'BNB','sol':'Solana','xrp':'XRP','sp500':'S&P 500','nasdaq':'Nasdaq','dow':'Dow Jones'}'''
assert src.count(old_names) == 1, 'v25_show_price name dicts not found'
src = src.replace(old_names, new_names)

with open(p, 'w', encoding='utf-8', newline='') as f:
    f.write(src)
print('price buttons fixed OK')

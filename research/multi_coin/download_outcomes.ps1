$dest = "C:\Users\user\polymarket-bot\research\multi_coin"
foreach ($c in 'btc','eth','sol','xrp','doge','bnb','hype') {
    $src = "hetzner:/root/data_${c}_5m_research/market_outcomes.csv"
    $dst = "$dest\market_outcomes_${c}.csv"
    Write-Host "downloading $c..."
    scp $src $dst
}
Write-Host "Done."

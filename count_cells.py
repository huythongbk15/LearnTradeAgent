import glob, os

btc_dir = 'data/backtests/wfo/research/vol_target__BTCUSDT__1h'
eth_dir = 'data/backtests/wfo/research/vol_target__ETHUSDT__1h'
regime_dir = 'data/backtests/wfo/research/regime_ensemble__BTCUSDT__1h'

for name, d in [('BTC', btc_dir), ('ETH', eth_dir), ('regime', regime_dir)]:
    cells = glob.glob(f'{d}/ma_vol_target__*_*__*', recursive=True) if 'vol_target' in d else glob.glob(f'{d}/regime_switching__*_*__*', recursive=True)
    reports = glob.glob(f'{d}/**/report.json', recursive=True)
    summaries = glob.glob(f'{d}/**/parallel_canonical_summary.json', recursive=True)
    print(f'{name}: {len(cells)} cell dirs, {len(reports)} reports, {len(summaries)} summaries')

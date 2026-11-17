"""Download raw A-share / Hong Kong statements, without investment signals."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys
from datetime import datetime

ROOT = Path(__file__).resolve().parent

def get_data_root():
    return ROOT / 'data'

def fetch(market, symbol, kind):
    import akshare as ak
    if market == 'a':
        functions = {'income': ak.stock_profit_sheet_by_report_em,
                     'balance': ak.stock_balance_sheet_by_report_em,
                     'cashflow': ak.stock_cash_flow_sheet_by_report_em}
        prefix = 'SH' if symbol.startswith('6') else 'BJ' if symbol.startswith(('4', '8', '9')) else 'SZ'
        return functions[kind](symbol=prefix + symbol)
    labels = {'income': '利润表', 'balance': '资产负债表', 'cashflow': '现金流量表'}
    return ak.stock_financial_hk_report_em(stock=symbol, symbol=labels[kind], indicator='年度')

def main():
    parser = argparse.ArgumentParser(description='A股/港股原始财报下载（CSV，Excel可打开）')
    parser.add_argument('--market', choices=['a', 'hk'], required=True)
    parser.add_argument('--symbol', required=True, help='A股六位代码；港股五位代码')
    parser.add_argument('--kind', choices=['income', 'balance', 'cashflow', 'all'], default='all')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    expected = 6 if args.market == 'a' else 5
    if not args.symbol.isascii() or not args.symbol.isdigit() or len(args.symbol) != expected:
        parser.error(f'证券代码须为{expected}位数字，保留前导零')
    if args.worker:
        df = fetch(args.market, args.symbol, args.kind)
        if df.empty:
            raise RuntimeError('数据源返回空表，未写入数据文件')
        stamp = datetime.now().astimezone()
        folder = get_data_root() / f'{args.market}_{args.symbol}'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f'{args.kind}_{stamp:%Y%m%d_%H%M%S}.csv'
        df.to_csv(path, index=False, encoding='utf-8-sig')
        metadata = {'market': args.market, 'symbol': args.symbol, 'statement': args.kind,
                    'retrieved_at': stamp.isoformat(), 'rows': len(df),
                    'akshare_version': importlib.metadata.version('akshare'),
                    'source': 'Eastmoney via AKShare',
                    'note': '原始数据；报表币种及单位须核对公司公告，港股股价币种可能与报表不同。'}
        path.with_suffix('.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'已保存 {len(df)} 行：{path}', flush=True)
        return
    print(f'数据保存位置：{get_data_root()}', flush=True)
    kinds = ['income', 'balance', 'cashflow'] if args.kind == 'all' else [args.kind]
    failed = []
    for kind in kinds:
        print(f'正在获取 {args.market} {args.symbol} {kind} ...', flush=True)
        try:
            result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--market', args.market,
                                     '--symbol', args.symbol, '--kind', kind, '--worker'], timeout=60)
            if result.returncode:
                failed.append(kind)
        except subprocess.TimeoutExpired:
            print('数据源响应超过60秒；已停止本次请求。', flush=True)
            failed.append(kind)
    if failed:
        print('未成功获取：' + ', '.join(failed) + '。请稍后重试；未使用模拟数据替代。')
        sys.exit(1)

if __name__ == '__main__':
    main()

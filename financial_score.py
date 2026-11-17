"""Offline financial research, 80 points. Python standard library only."""
import argparse
import bisect
import csv
import hashlib
import json
import math
from datetime import date, datetime
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parent
VERSION = '2.0.0-basic'
FIELDS = {
 'income': {'revenue':['OPERATE_INCOME','营业额','营业收入'],
 'profit':['NETPROFIT','除税后溢利','年内溢利','本年利润'],
 'parent_profit':['PARENT_NETPROFIT','股东应占溢利','母公司拥有人应占溢利'],
 'eps':['BASIC_EPS','每股基本盈利','基本每股收益'],
 'pretax':['TOTAL_PROFIT','除税前溢利'], 'tax':['INCOME_TAX','所得税'],
 'interest':['FE_INTEREST_EXPENSE','利息支出']},
 'cashflow': {'cfo':['NETCASH_OPERATE','经营活动所得现金净额','经营业务现金净额'],
 'capex':['CONSTRUCT_LONG_ASSET']},
 'balance': {'equity':['TOTAL_EQUITY','权益总额','总权益'],
 'parent_equity':['TOTAL_PARENT_EQUITY','归属于母公司所有者权益合计','母公司拥有人应占权益'],
 'assets':['TOTAL_ASSETS','资产总额'], 'liabilities':['TOTAL_LIABILITIES','负债总额']}}
SPECS = [('revenue_growth', '盈利稳定性', '五年收入年复合增长率', 8, [0, 0.03, 0.06, 0.1, 0.15], 'up'), ('profit_growth', '盈利稳定性', '五年归母净利润年复合增长率', 8, [0, 0.03, 0.06, 0.1, 0.15], 'up'), ('profit_years', '盈利稳定性', '五年归母盈利年数', 8, [1, 2, 3, 4, 5], 'up'), ('cash_conversion', '利润含金量', '五年累计现金转化率', 14, [0, 0.5, 0.8, 1, 1.2], 'up'), ('cfo_years', '利润含金量', '五年经营现金流为正年数', 8, [1, 2, 3, 4, 5], 'up'), ('liability_ratio', '资产负债安全性', '最近三年资产负债率中位数', 10, [0.2, 0.35, 0.5, 0.65, 0.8], 'down'), ('roa', '资本回报能力', '五年总资产净回报率中位数', 8, [0, 0.02, 0.04, 0.06, 0.1], 'up'), ('roe', '资本回报能力', '五年归母净资产回报率中位数', 4, [0, 0.05, 0.08, 0.12, 0.16], 'up'), ('fcf_years', '现金创造能力', '五年自由现金流为正年数', 8, [1, 2, 3, 4, 5], 'up'), ('net_margin', '现金创造能力', '五年合并净利率中位数', 4, [0, 0.03, 0.08, 0.15, 0.25], 'up')]

def number(value):
    if value is None or str(value).strip() in ('','--','None','null','nan','NaN'):
        return None
    n = float(str(value).replace(',',''))
    return n if math.isfinite(n) else None

def grade(value, cuts, direction):
    return bisect.bisect_right(cuts,value) if direction=='up' else 5-bisect.bisect_left(cuts,value)

def load_config(path):
    cfg = json.loads(path.read_text(encoding='utf-8-sig'))
    assert set(cfg['metrics']) == {s[0] for s in SPECS}, '配置指标不完整'
    assert abs(sum(v['weight'] for v in cfg['metrics'].values())-80)<1e-8, '权重必须合计80'
    for v in cfg['metrics'].values():
        assert v['weight']>0 and len(v['cuts'])==5 and all(math.isfinite(x) for x in v['cuts'])
        assert v['cuts']==sorted(set(v['cuts'])), '阈值必须严格递增'
        assert v['direction'] in ('up','down')
    return cfg

def parse_statement(path, kind, market, code, mapping, warnings):
    with path.open(encoding='utf-8-sig',newline='') as stream:
        rows = list(csv.DictReader(stream))
    by_date, currencies, unknown = {}, set(), set()
    aliases = mapping.get(kind,FIELDS[kind])
    for row in rows:
        if row.get('SECURITY_CODE') and row['SECURITY_CODE'].zfill(len(code)) != code:
            raise ValueError(f'证券代码不匹配：{path.name}')
        rawdate = row.get('REPORT_DATE','')[:10]
        if not rawdate:
            raise ValueError('缺少REPORT_DATE')
        d = date.fromisoformat(rawdate)
        if market=='a' and (d.month,d.day)!=(12,31):
            continue
        if market=='hk':
            # Require explicit start date for flow statements to exclude half-year/quarter data.
            if kind!='balance':
                start = row.get('START_DATE','')[:10]
                if not start or not 330 <= (d-date.fromisoformat(start)).days <= 380:
                    warnings.add(f'{kind}：排除无法确认完整年度的记录 {rawdate}')
                    continue
        if row.get('CURRENCY'):
            currencies.add(row['CURRENCY'])
        bucket = by_date.setdefault(rawdate,{})
        if 'STD_ITEM_NAME' in row:
            label = row['STD_ITEM_NAME']
            matches = [key for key, labels in aliases.items() if label in labels]
            if not matches:
                unknown.add(label)
            values = [(key,number(row.get('AMOUNT'))) for key in matches]
        else:
            values = []
            for key, labels in aliases.items():
                # Only exact field aliases; never infer a missing amount to be zero.
                available = [number(row.get(label)) for label in labels if label in row]
                values.append((key,next((v for v in available if v is not None),None)))
        for key,value in values:
            if value is None:
                continue
            if key in bucket and not math.isclose(bucket[key],value,rel_tol=1e-9,abs_tol=1e-6):
                raise ValueError(f'同报告期字段冲突：{rawdate} {key}，请先核查重述版本')
            bucket[key] = value
    return by_date,currencies,sorted(unknown)

def analyze(data, dates, cfg, flags=None):
    metrics=[]
    def values(key, ds=None):
        v=[data.get(d,{}).get(key) for d in (dates if ds is None else ds)]
        if any(x is None for x in v): raise ValueError('缺少可比字段：'+key)
        return v
    def growth(key):
        v=values(key)
        if v[0]<=0 or v[-1]<0: raise ValueError('增长率起点须为正，终点不得为负')
        return (v[-1]/v[0])**(1/(len(v)-1))-1
    def ratios(num,den,average=False,ds=None):
        ds=dates if ds is None else ds
        nv=values(num,ds); dv=values(den,ds)
        if average:
            prior=[f'{int(d[:4])-1}{d[4:]}' for d in ds]
            pv=values(den,prior)
            if min(dv+pv)<=0: raise ValueError('期初或期末分母非正，回报率不适用')
            dv=[(a+b)/2 for a,b in zip(dv,pv)]
        if min(dv)<=0: raise ValueError('分母必须为正')
        return median(n/d for n,d in zip(nv,dv))
    def compute(key):
        if key=='revenue_growth': return growth('revenue')
        if key=='profit_growth': return growth('parent_profit')
        if key=='profit_years': return sum(x>0 for x in values('parent_profit'))
        if key=='cash_conversion':
            profit=sum(values('profit'))
            return sum(values('cfo'))/profit if profit>0 else -1
        if key=='cfo_years': return sum(x>0 for x in values('cfo'))
        if key=='liability_ratio':
            if min(values('liabilities',dates[-3:]))<0: raise ValueError('负债金额异常')
            return ratios('liabilities','assets',ds=dates[-3:])
        if key=='roa': return ratios('profit','assets',average=True)
        if key=='roe': return ratios('parent_profit','parent_equity',average=True)
        if key=='net_margin': return ratios('profit','revenue')
        if key=='fcf_years':
            cap=values('capex')
            if min(cap)<0: raise ValueError('资本开支须是正的现金流出金额')
            return sum(c-p>0 for c,p in zip(values('cfo'),cap))
        raise ValueError('未知指标')
    for key,module,label,weight,cuts,direction in SPECS:
        rule=cfg['metrics'][key]
        m=dict(id=key,module=module,label=label,weight=rule['weight'],value=None,grade=None,points=None,reason='')
        try:
            if len(dates)!=5: raise ValueError('需要连续五个完整年度')
            m['value']=compute(key)
            m['grade']=grade(m['value'],rule['cuts'],rule['direction'])
            m['points']=m['grade']/5*rule['weight']
        except ValueError as exc: m['reason']=str(exc)
        metrics.append(m)
    return metrics

def run(args):
    cfg=load_config(args.config)
    if args.industry!='general': raise ValueError('银行、保险、证券、地产等暂不适用通用模型')
    folder=args.root/'data'/f'{args.market}_{args.symbol}'
    supplement={}
    if args.supplement:
        supplement=json.loads(args.supplement.read_text(encoding='utf-8-sig'))
        if supplement.get('market')!=args.market or supplement.get('symbol')!=args.symbol:
            raise ValueError('补充文件证券代码不匹配')
    warnings=set(['基础财务模型v2：与旧版分数不可直接比较；阈值未经回测。','取消ROIC、净债务、利息保障及可比每股指标；资产负债率不能替代真实偿债分析。','仅用于一般非金融企业；高ROE可能来自杠杆，股价涨跌不计入财务质量评分。','不含护城河、管理层、估值和买入建议；自由现金流使用总资本开支而非维护性资本开支。'])
    combined={}; provenance=[]; currency=set(); unmapped={}
    for kind in ('income','balance','cashflow'):
        paths=sorted(folder.glob(kind+'_*.csv'))
        if not paths: raise ValueError(f'缺少{kind}财报，请先下载三大报表')
        path=paths[-1]
        rows,cc,unknown=parse_statement(path,kind,args.market,args.symbol,cfg.get('field_aliases',{}),warnings)
        currency.update(cc); unmapped[kind]=unknown
        provenance.append(dict(file=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        for d,values in rows.items(): combined.setdefault(d,{}).update(values)
    if len(currency)>1: raise ValueError('报表包含不同币种，必须先统一币种')
    if args.market=='hk':
        if not supplement.get('hk_unit_currency_confirmed'):
            raise ValueError('港股原始长表不提供足够币种/单位信息；需补充文件确认统一单位与币种后评分')
        warnings.add('港股科目名称按精确别名映射；未识别项目见JSON，不作零处理。')
    for d,values in supplement.get('annual',{}).items():
        for k,v in values.items():
            if k in combined.get(d,{}) and combined[d][k]!=number(v):
                warnings.add(f'补充资料覆盖字段：{d} {k}')
            combined.setdefault(d,{})[k]=number(v)
    if supplement and not supplement.get('source_note'): raise ValueError('补充数据必须填写source_note来源说明')
    # All required core flows must be present; never bridge an older five-year window over missing recent years.
    all_dates=sorted(combined)
    if not all_dates: raise ValueError('没有可识别的年度报表')
    end=args.end_year or int(all_dates[-1][:4]); month_day=all_dates[-1][4:]
    dates=[f'{y}{month_day}' for y in range(end-4,end+1)]
    if any(d not in combined for d in dates): raise ValueError('最近五个财年不连续或财年末改变；请先整理可比年度')
    for d in dates:
        row=combined[d]
        if row.get('equity') is not None and row['equity']<=0:
            warnings.add(f'{d}总权益非正，存在重大财务风险；高分不可抵消此风险。')
        if row.get('cfo') is not None and row['cfo']<0:
            warnings.add(f'{d}经营现金流为负。')
        if row.get('parent_profit') is not None and row['parent_profit']<0:
            warnings.add(f'{d}归母净利润为负。')
        if all(row.get(k) is not None for k in ('assets','liabilities','equity')):
            if abs(row['assets']-row['liabilities']-row['equity'])>max(1,abs(row['assets'])*.001):
                raise ValueError(f'{d}资产负债表不平，停止评分')
    metrics=analyze(combined,dates,cfg,supplement)
    covered=sum(m['weight'] for m in metrics if m['points'] is not None)
    points=sum(m['points'] or 0 for m in metrics)
    result=dict(version=VERSION,market=args.market,symbol=args.symbol,industry=args.industry,
        dates=dates,points=round(points,2),maximum=80,covered_weight=covered,
        coverage=covered/80,possible_range=[round(points,2),round(points+80-covered,2)],
        status='财务指标完整' if covered==80 else '资料不完整：仅发布已核验得分，不形成最终等级',
        metrics=metrics,warnings=sorted(warnings),inputs=provenance,unmapped_items=unmapped,
        annual={d:combined[d] for d in dates},config=cfg,supplement=supplement)
    out=args.output or args.root/'分析结果'/f'{args.market}_{args.symbol}'/datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    out.mkdir(parents=True,exist_ok=False)
    (out/'评分结果.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    with (out/'指标明细.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(metrics[0])); w.writeheader(); w.writerows(metrics)
    lines=[f'# {args.market} {args.symbol} 财务自动分析',f'年度：{dates[0]} 至 {dates[-1]}',
        f'已取得分数：**{points:.2f}/80**；已覆盖权重：**{covered:g}/80**。',
        f'缺失指标对应 {80-covered:g} 分未评；可能区间 {points:.2f}～{points+80-covered:.2f}，不重新放大权重。',
        result['status'],'','|指标|数值|得分/权重|缺失原因|','|---|---:|---:|---|']
    for m in metrics:
        value='未评' if m['value'] is None else f"{m['value']:.4f}"
        score='未评' if m['points'] is None else f"{m['points']:.2f}"
        lines.append(f"|{m['label']}|{value}|{score}/{m['weight']}|{m['reason']}|")
    lines+=['','增长率和资本回报率数值采用小数：0.10表示10%。','', '## 限制与提醒']+['- '+x for x in sorted(warnings)]
    lines+=['','## 输入文件']+['- '+p['file']+'（SHA256 '+p['sha256']+'）' for p in provenance]
    (out/'研究报告.md').write_text('\n\n'.join(lines[:6])+'\n'+'\n'.join(lines[6:]),encoding='utf-8')
    from score_visual import build_report
    visual = build_report(result, out / '可视化评分报告.html')
    print(f'可视化报告：{visual}', flush=True)
    if getattr(args, 'open_report', False):
        import webbrowser
        webbrowser.open(visual.resolve().as_uri())
    print(f'已得 {points:.2f}/80，覆盖 {covered:g}/80。报告：{out}',flush=True)
    return result

def main():
    p=argparse.ArgumentParser(description='财务自动评分（80分，离线读取本地财报）')
    p.add_argument('--open-report',action='store_true',help='分析后自动打开可视化报告')
    p.add_argument('--market',choices=['a','hk'],required=True)
    p.add_argument('--symbol',required=True)
    p.add_argument('--industry',choices=['general','financial','property'],default='general')
    p.add_argument('--root',type=Path,default=ROOT)
    p.add_argument('--config',type=Path,default=ROOT/'score_config.json')
    p.add_argument('--supplement',type=Path)
    p.add_argument('--end-year',type=int)
    p.add_argument('--output',type=Path)
    args=p.parse_args()
    if not args.symbol.isascii() or not args.symbol.isdigit() or len(args.symbol)!=(6 if args.market=='a' else 5):
        p.error('A股代码六位，港股代码五位，请保留前导零')
    try: run(args)
    except (ValueError,AssertionError,OSError,KeyError) as exc:
        p.exit(1,f'分析未完成：{exc}\n')

if __name__=='__main__': main()

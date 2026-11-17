"""Cash-return preference model. Independent of financial quality v2."""
import argparse,csv,json,hashlib,html,math,urllib.request,urllib.parse
from pathlib import Path
from datetime import datetime,date,timezone,timedelta

ROOT=Path(__file__).resolve().parent
TZ=timezone(timedelta(hours=8))
WEIGHTS={'dividend_years':20,'dividend_stability':15,'dividend_growth':10,'dividend_streak':5,
         'profit_years':20,'profit_growth':10,'profit_streak':5,'cfo_years':10,'fcf_years':5}
LABELS={'dividend_years':'五年分红覆盖','dividend_stability':'分红稳定性（减配/断供扣分）','dividend_growth':'分红逐年增长',
'dividend_streak':'最近连续分红','profit_years':'五年持续盈利','profit_growth':'利润逐年增长','profit_streak':'最近连续盈利',
'cfo_years':'经营现金流连续性','fcf_years':'自由现金流连续性'}

def n(v):
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except (ValueError,TypeError):return None

def fetch(code,path):
    rows=[];pages=1;page=1
    while page<=pages:
        params=dict(reportName='RPT_SHAREBONUS_DET',columns='ALL',filter=f'(SECURITY_CODE="{code}")',
                    pageSize=500,pageNumber=page,sortColumns='REPORT_DATE',sortTypes='-1',source='WEB',client='WEB')
        url='https://datacenter-web.eastmoney.com/api/data/v1/get?'+urllib.parse.urlencode(params)
        req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
        with urllib.request.urlopen(req,timeout=25) as response:body=json.load(response)
        result=body.get('result')
        if not result or not isinstance(result.get('data'),list):raise ValueError('分红数据源无有效结果，不视为五年零分红')
        pages=int(result['pages'])
        if pages>100:raise ValueError('异常分页数量')
        rows+=result['data'];page+=1
    record=dict(code=code,source='Eastmoney RPT_SHAREBONUS_DET',fetched_at=datetime.now(TZ).isoformat(),
                complete_pages=True,rows=rows,note='源内分页完整不等于公司全部特别分红或资本动作均已覆盖')
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    return record

def dividends(record,years,asof):
    totals={y:0.0 for y in years};seen_years=set();actions=[];unknown=set();events={}
    notes=['按除息日所在自然年统计已实施税前每股现金分红，除息日不等于实际到账日；报告年度利润与分红所属年度不同。',
           '数据源可能漏记特别分红。源内未发现事件不能证明没有分红；历史分红总额与回报预测需回查公告。']
    for r in record['rows']:
        if str(r.get('SECURITY_CODE'))!=record['code']:raise ValueError('分红证券代码不匹配')
        ex=str(r.get('EX_DIVIDEND_DATE') or '')[:10]
        if not ex or ex>asof or r.get('ASSIGN_PROGRESS')!='实施分配':continue
        y=int(ex[:4])
        if y not in totals:continue
        key=(r.get('REPORT_DATE'),ex)
        prior=events.get(key)
        if prior and prior!=r:
            unknown.add(y);notes.append(f'{y}存在同一事件冲突记录，暂停对应年度评分');continue
        events[key]=r
    for (_,ex),r in events.items():
        y=int(ex[:4]);cash=n(r.get('PRETAX_BONUS_RMB'))
        if cash is None or cash<0 or '10派' not in str(r.get('IMPL_PLAN_PROFILE','')):
            unknown.add(y);continue
        totals[y]+=cash/10;seen_years.add(y)
        if any((n(r.get(k)) or 0)>0 for k in ('BONUS_IT_RATIO','BONUS_RATIO','IT_RATIO')):
            actions.append(y)
    result={str(y):(totals[y] if y in seen_years and y not in unknown else None) for y in years}
    # Never assume absent records are zero. Explicit reviewed corrections remain source-labelled.
    for y in years:
        override=record.get('verified_annual',{}).get(str(y))
        if override is not None:
            if not record.get('verification_source'):raise ValueError('确认零分红/补充分红须填写公告来源')
            v=n(override)
            if v is None or v<0:raise ValueError('确认年度分红必须是非负数')
            result[str(y)]=v
    return [result[str(y)] for y in years],sorted(set(actions)),notes

def financial(root,code,years):
    folder=root/'data'/('a_'+code);annual={};sources=[]
    for kind,fields in [('income',{'profit':'PARENT_NETPROFIT'}),('cashflow',{'cfo':'NETCASH_OPERATE','capex':'CONSTRUCT_LONG_ASSET'})]:
        files=sorted(folder.glob(kind+'_*.csv'))
        if not files:continue
        p=files[-1];sources.append(dict(file=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
        with p.open(encoding='utf-8-sig',newline='') as f:
            for row in csv.DictReader(f):
                d=row.get('REPORT_DATE','')[:10]
                if d[4:]!='-12-31' or int(d[:4]) not in years:continue
                if row.get('SECURITY_CODE') and row['SECURITY_CODE'].zfill(6)!=code:raise ValueError('财报证券代码不一致')
                bucket=annual.setdefault(int(d[:4]),{})
                for key,field in fields.items():
                    value=n(row.get(field))
                    if key in bucket and bucket[key]!=value:raise ValueError('财报存在重述冲突，请先核实')
                    bucket[key]=value
    values={k:[annual.get(y,{}).get(k) for y in years] for k in ('profit','cfo','capex')}
    values['fcf']=[c-p if c is not None and p is not None and p>=0 else None for c,p in zip(values['cfo'],values['capex'])]
    return values,sources

def score(ds,profit,cfo,fcf,weights,actions=False):
    rows=[]
    def add(key,arr,mode):
        maximum=weights[key];points=None;detail=''
        if len(arr)!=5 or any(x is None for x in arr):detail='五个完整年度有未知值，暂不评分；未知不等于断供'
        elif actions and key in ('dividend_stability','dividend_growth'):detail='观察期存在送转，名义每股分红不可直接比较，暂不评分'
        elif mode=='years':
            count=sum(x>0 for x in arr);points=maximum*count/5;detail=f'{count}/5年为正；每缺一个已确认正值年度扣{maximum/5:g}分'
        elif mode=='streak':
            count=0
            for x in reversed(arr):
                if x<=0:break
                count+=1
            points=maximum*count/5;detail=f'最近连续{count}年为正；满分{maximum:g}×{count}/5'
        elif mode=='growth':
            count=sum(b>a and a>0 and b>0 for a,b in zip(arr,arr[1:]));points=maximum*count/4
            detail=f'4个年度间隔中{count}次在双方均为正时增长；持平和亏损转盈不计增长分'
        elif mode=='stability':
            cuts=sum(a>b and b>0 for a,b in zip(arr,arr[1:]));breaks=sum(a>0 and b<=0 for a,b in zip(arr,arr[1:]))
            points=max(0,maximum-cuts*maximum*.2-breaks*maximum/3)
            if not any(x>0 for x in arr):points=0
            detail=f'满分{maximum:g}−减配{cuts}次×{maximum*.2:g}−断供{breaks}次×{maximum/3:g}；最低0，五年均零得0'
        rows.append(dict(id=key,label=LABELS[key],points=points,maximum=maximum,detail=detail))
    add('dividend_years',ds,'years');add('dividend_stability',ds,'stability');add('dividend_growth',ds,'growth');add('dividend_streak',ds,'streak')
    add('profit_years',profit,'years');add('profit_growth',profit,'growth');add('profit_streak',profit,'streak')
    add('cfo_years',cfo,'years');add('fcf_years',fcf,'years')
    return rows

def report(r,path):
    e=lambda x:html.escape(str(x));val=lambda x:'未知' if x is None else f'{x:,.2f}'
    tr=''.join(f'<tr><td>{e(m["label"])}</td><td>{val(m["points"])} / {m["maximum"]:g}</td><td><progress value="{m["points"] or 0}" max="{m["maximum"]}"></progress></td><td>{e(m["detail"])}</td></tr>' for m in r['metrics'])
    annual=''.join(f'<tr><td>{y}</td><td>{val(r["dividends"][i])}</td><td>{val(r["financial"]["profit"][i])}</td><td>{val(r["financial"]["cfo"][i])}</td><td>{val(r["financial"]["fcf"][i])}</td></tr>' for i,y in enumerate(r['years']))
    doc=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>股东回报倾向评分</title><style>body{{font:16px/1.7 system-ui,'Microsoft YaHei';background:#f3f6f7;color:#192d39;margin:0}}main{{max-width:1100px;margin:auto;padding:28px}}section{{background:white;border-radius:12px;padding:22px;margin:16px 0}}table{{border-collapse:collapse;width:100%}}td,th{{text-align:left;padding:12px;border-bottom:1px solid #dbe3e7}}progress{{accent-color:#17796b;width:120px}}.big{{font-size:36px}}.scroll{{overflow:auto}}li{{margin:10px 0}}</style><main><h1>{e(r['code'])} · 股东回报倾向评分</h1><section><p class="big">已取得 {r['points']:.2f} / 100 分</p><p>已覆盖权重 {r['covered']:g}/100；未知 {100-r['covered']:g}分。分数区间 {r['points']:.2f}～{r['points']+100-r['covered']:.2f}。</p><p>这是偏好持续现金分配的观察评分，不是完整企业质量分、未来收益率或买入建议；源内分红记录可能不完整。</p></section><section class="scroll"><h2>指标与扣分细则</h2><table><tr><th>指标</th><th>得分/满分</th><th>进度</th><th>规则及本次计算</th></tr>{tr}</table></section><section class="scroll"><h2>五年事实明细</h2><table><tr><th>自然年</th><th>税前分红 元/股（源内记录）</th><th>归母净利润</th><th>经营现金流</th><th>自由现金流</th></tr>{annual}</table><p>财务金额单位沿用下载财报。分红按除息自然年，年度分红和中期分红按实施事件合并，送转不作为现金分红。</p></section><section><h2>限制与追溯</h2><ul>{''.join('<li>'+e(x)+'</li>' for x in r['notes'])}</ul><p>原始文件：{e(r['dividend_file'])}，SHA256：{e(r['dividend_hash'])}。完整计算值与权重见同目录JSON。</p></section></main></html>'''
    path.write_text(doc,encoding='utf-8')

def main():
    p=argparse.ArgumentParser(description='持续分红、持续盈利及现金回报倾向评分')
    p.add_argument('--symbol',required=True);p.add_argument('--root',type=Path,default=ROOT)
    p.add_argument('--end-year',type=int,default=datetime.now(TZ).year-1)
    p.add_argument('--dividend-file',type=Path,help='已有原始分红JSON，离线重算')
    p.add_argument('--output',type=Path);p.add_argument('--open-report',action='store_true')
    a=p.parse_args()
    try:
        if not a.symbol.isascii() or not a.symbol.isdigit() or len(a.symbol)!=6:raise ValueError('请输入六位A股代码')
        if a.end_year>=datetime.now(TZ).year:raise ValueError('只比较五个已结束的自然年')
        cfg=json.loads((Path(__file__).parent/'return_config.json').read_text(encoding='utf-8-sig'))
        if set(cfg)!=set(WEIGHTS) or any(n(v) is None or v<=0 for v in cfg.values()) or sum(cfg.values())!=100:raise ValueError('指标权重须合计100且全部为正')
        stamp=datetime.now(TZ).strftime('%Y%m%d_%H%M%S_%f')
        src=a.dividend_file or a.root/'data'/'dividends'/a.symbol/(stamp+'.json')
        record=json.loads(src.read_text(encoding='utf-8-sig')) if a.dividend_file else fetch(a.symbol,src)
        if record['code']!=a.symbol or not record.get('complete_pages'):raise ValueError('证券代码不符或分页不完整')
        years=list(range(a.end_year-4,a.end_year+1));ds,actions,notes=dividends(record,years,datetime.now(TZ).date().isoformat())
        fin,sources=financial(a.root,a.symbol,years)
        metrics=score(ds,fin['profit'],fin['cfo'],fin['fcf'],cfg,bool(actions))
        points=sum(m['points'] or 0 for m in metrics);covered=sum(m['maximum'] for m in metrics if m['points'] is not None)
        if actions:notes.append('存在送转年份：'+str(actions)+'；每股分红增长和稳定性暂停评分，避免误扣')
        notes+=['一次性特别分红可能导致次年看起来减配；报告不自动区分常规和特别分红。',
                '低分也可能是高回报再投资策略所致；高分不能排除高估值或借债分红。本模块未计算分红支付率、长期债务保障或真实总回报。',
                '增长分与连续性分会共同奖励稳定现金回报，这是显式偏好，未回测；原财务80分保留，不直接与本分数相加。']
        r=dict(version='1.0-cash-preference',code=a.symbol,years=years,metrics=metrics,points=points,covered=covered,
               dividends=ds,financial=fin,notes=notes,weights=cfg,dividend_file=str(src),
               dividend_hash=hashlib.sha256(src.read_bytes()).hexdigest(),financial_sources=sources)
        out=a.output or a.root/'分析结果'/'股东回报'/a.symbol/stamp
        out.mkdir(parents=True,exist_ok=False)
        (out/'股东回报评分.json').write_text(json.dumps(r,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
        with (out/'评分明细.csv').open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(metrics[0]));w.writeheader();w.writerows(metrics)
        file=out/'股东回报评分.html';report(r,file)
        print(f'已得{points:.2f}/100，覆盖{covered:g}/100；报告：{file}')
        if a.open_report:
            import webbrowser;webbrowser.open(file.resolve().as_uri())
    except (OSError,ValueError,KeyError) as exc:p.exit(1,'未完成：'+str(exc)+'\n')

if __name__=='__main__':main()

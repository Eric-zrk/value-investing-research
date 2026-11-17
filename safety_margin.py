"""Quality and valuation screen. Local facts + timestamped quotes; no orders."""
import argparse,csv,hashlib,html,importlib,json,math,os,sys,time,urllib.request,urllib.parse
from pathlib import Path
from datetime import datetime,timezone,timedelta
ROOT=Path(__file__).resolve().parent
TZ=timezone(timedelta(hours=8))

def load(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def atomic(p,data):
    p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix(p.suffix+'.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8');os.replace(tmp,p)
def val(v):
    try:
        x=float(v);return x if math.isfinite(x) else None
    except (ValueError,TypeError):return None
def default_period():
    d=datetime.now(TZ);y=d.year
    return f'{y}0930' if d.month>=11 else f'{y}0630' if d.month>=9 else f'{y}0331' if d.month>=5 else f'{y-1}0930'

def validate(cfg):
    for key in ['minimum_quality','min_core_ratio','min_profit_yoy','max_liability_ratio','min_cash_conversion','required_margin']:
        if val(cfg.get(key)) is None:raise ValueError('配置缺少有效数值：'+key)
    if not 0<=cfg['minimum_quality']<=80 or not 0<=cfg['required_margin']<1:raise ValueError('分数或折让阈值越界')
    if not 0<=cfg['min_core_ratio']<=1 or not 0<=cfg['max_liability_ratio']<=1 or cfg['min_cash_conversion']<0 or cfg['min_profit_yoy']< -1:raise ValueError('质量参数越界')
    if not isinstance(cfg.get('require_below_conservative'),bool):raise ValueError('保守情景条件必须为true/false')
    if val(cfg.get('max_quote_age_days')) is None or not 0<cfg['max_quote_age_days']<=365:raise ValueError('报价有效天数须在0至365之间')
    ss=cfg['scenarios']
    if [s['name'] for s in ss]!=['保守','基准','乐观']:raise ValueError('情景必须依次为保守、基准、乐观')
    factors=[]
    for s in ss:
        if any(val(s.get(k)) is None for k in ['growth','payout','exit_pe','discount']):raise ValueError('情景字段缺失')
        if s['growth']<=-1 or not 0<=s['payout']<=1 or s['exit_pe']<=0 or s['discount']<=0:raise ValueError('情景参数越界')
        factors.append(factor(s))
    if factors!=sorted(factors):raise ValueError('情景价值应依次递增，请检查参数')
    return cfg

def quality_reasons(r,cfg):
    reasons=[]
    if r['quality_coverage']<80 or r['quality']<cfg['minimum_quality']:reasons.append('财务分未完整覆盖或低于设定门槛')
    needed=['ttm_profit','ttm_core','ttm_group_profit','ttm_cfo','ttm_capex','profit_yoy','liability_ratio','parent_equity','median3profit','three_year_fcf']
    if r.get('recent_error') or any(r.get(k) is None for k in needed):reasons.append('最新可比数据不足');return reasons
    if r['ttm_profit']<=0 or r['ttm_core']<=0 or r['ttm_group_profit']<=0:reasons.append('TTM归母/扣非/合并利润非正')
    elif r['ttm_core']/r['ttm_profit']<cfg['min_core_ratio']:reasons.append('扣非占归母低于门槛')
    if r['profit_yoy']<cfg['min_profit_yoy']:reasons.append('最新归母同比低于门槛')
    if not 0<=r['liability_ratio']<=cfg['max_liability_ratio']:reasons.append('资产负债率过高或异常')
    if r['parent_equity']<=0:reasons.append('归母权益非正')
    if r['ttm_cfo']<cfg['min_cash_conversion']*r['ttm_group_profit']:reasons.append('TTM现金转化不足')
    if r['ttm_capex']<0:reasons.append('TTM资本开支出现负数，需回查口径')
    if r['ttm_cfo']-r['ttm_capex']<=0 or r['three_year_fcf']<=0:reasons.append('TTM或三年自由现金流非正')
    if 'ST' in r['name'].upper() or '退' in r['name']:reasons.append('ST/退市名称风险')
    return reasons

def factor(s):
    g,p,pe,r=[s[k] for k in ['growth','payout','exit_pe','discount']]
    return sum((1+g)**t*p/(1+r)**t for t in range(1,6))+(1+g)**5*pe/(1+r)**5

def price_result(r,q,cfg,asof):
    price,cap=val(q.get('f2')),val(q.get('f20'));ts=val(q.get('f124'))
    if not price or price<=0 or not cap or cap<=0 or not ts:raise ValueError('报价或总市值无效/缺时间戳')
    if ts>asof.timestamp()+300 or asof.timestamp()-ts>cfg.get('max_quote_age_days',7)*86400:raise ValueError('报价时间过旧或异常')
    normalized=min(r['ttm_profit'],r['ttm_core'],r['median3profit'])
    if normalized<=0:raise ValueError('正常化盈利非正')
    eps=normalized/cap*price;values=[eps*factor(s) for s in cfg['scenarios']]
    r.update(price=price,market_cap=cap,quote_timestamp=ts,quote_time=datetime.fromtimestamp(ts,TZ).isoformat(),normalized_profit=normalized,
             normalized_eps=eps,normalized_pe=cap/normalized,conservative=values[0],base=values[1],optimistic=values[2],
             margin=1-price/values[1],reference_price=(1-cfg['required_margin'])*values[1])
    r['price_pass']=price<=r['reference_price'] and (not cfg['require_below_conservative'] or price<=values[0])
    r['also_below_conservative']=price<=values[0]
    return r

def get_quotes(rows,out):
    quotes={};raw=[];errors=[]
    for start in range(0,len(rows),50):
        batch=rows[start:start+50]
        ids=','.join(('1.' if x['code'].startswith('6') else '0.')+x['code'] for x in batch)
        url='https://push2.eastmoney.com/api/qt/ulist.np/get?'+urllib.parse.urlencode(dict(secids=ids,fields='f12,f14,f2,f20,f23,f124',fltt=2,invt=2))
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=25) as f:body=json.load(f)
            raw.append(body)
            for q in body['data']['diff']:
                if q['f12'] in quotes:raise ValueError('报价代码重复')
                quotes[q['f12']]=q
        except (OSError,ValueError,KeyError,TypeError) as exc:errors.append(dict(batch=start,error=str(exc)))
        print(f'刷新报价 {min(start+50,len(rows))}/{len(rows)}',flush=True);time.sleep(.3)
    atomic(out/'原始报价.json',dict(fetched_at=datetime.now(TZ).isoformat(),responses=raw,errors=errors))
    return quotes,errors

def write_report(out,rows,summary,cfg):
    e=lambda x:html.escape(str(x),quote=True)
    good=[r for r in rows if r.get('quality_pass')];valued=sorted([r for r in good if 'price' in r],key=lambda r:-r['margin'])
    passed=[r for r in valued if r['price_pass']]
    atomic(out/'筛选结果.json',dict(summary=summary,parameters=cfg,rows=rows))
    cols=['code','name','status','quality','quality_coverage','price','conservative','base','optimistic','margin','reference_price','price_pass','reasons_text']
    for name,records in [('全部公司.csv',rows),('通过价格规则.csv',passed),('质量通过观察表.csv',valued)]:
        with (out/name).open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=cols,extrasaction='ignore');w.writeheader()
            for r in records:w.writerow({k:r.get(k,'') for k in cols})
    table='';details=''
    for i,r in enumerate(valued):
        table+=f'<tr data-pass="{str(r["price_pass"]).lower()}"><td><a href="#c{r["code"]}">{e(r["code"])} {e(r["name"])}</a></td><td>{r["quality"]:.1f}/80</td><td>{r["price"]:.2f}</td><td>{r["base"]:.2f}</td><td>{r["reference_price"]:.2f}</td><td>{r["margin"]*100:.1f}%</td><td>{"通过量化价格规则" if r["price_pass"] else "价格尚未通过"}</td></tr>'
        metrics=''.join(f'<tr><td>{e(m["label"])}</td><td>{e(m["value"])}</td><td>{e(m["points"])}/{m["weight"]}</td><td>{e(m["reason"])}</td></tr>' for m in r['metrics'])
        detail=''.join(f'<li>{e(k)}：{e(r.get(k,"未提供"))}</li>' for k in ['quote_time','ttm_profit','ttm_core','median3profit','normalized_profit','normalized_eps','normalized_pe','profit_yoy','liability_ratio','ttm_cfo','ttm_capex','three_year_fcf','conservative','base','optimistic','also_below_conservative'])
        details+=f'<details id="c{r["code"]}"><summary>{e(r["name"])} {r["code"]} · 输入、评分与估值计算</summary><p>金额沿用原报表元口径；收益率与比率以小数显示。每股估值使用行情隐含股数，尚需核查A/B/H股、送转和稀释。不是已完成尽调的买入建议。</p><ul>{detail}</ul><div class="scroll"><table><tr><th>指标</th><th>数值</th><th>得分/权重</th><th>缺失原因</th></tr>{metrics}</table></div><p>输入文件与SHA256：{e(json.dumps(r["inputs"],ensure_ascii=False))}</p></details>'
    failures=''.join(f'<tr><td>{e(r["code"])}</td><td>{e(r["name"])}</td><td>{e(r["status"])}</td><td>{e(r["reasons_text"])}</td></tr>' for r in rows if r['status']!='valued')
    doc=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>质量与安全边际筛选</title><style>body{{font:16px/1.7 system-ui,'Microsoft YaHei';background:#eff5f5;color:#20333d;margin:0}}main{{max-width:1300px;margin:auto;padding:24px}}section,details{{background:white;border-radius:12px;padding:20px;margin:14px 0}}.scroll{{overflow:auto;max-height:70vh}}td,th{{padding:10px;text-align:left;border-bottom:1px solid #dbe5e8;white-space:nowrap}}th{{position:sticky;top:0;background:#e5eff0}}a{{color:#176e64}}summary{{cursor:pointer}}button{{font:inherit;padding:8px 12px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}</style><main><h1>质量与安全边际筛选</h1><section><p>完成时间：{e(summary['finished_at'])}</p><p>任务记录 {summary['total']}；行业排除 {summary['excluded']}；财务可算 {summary['calculated']}；计算错误 {summary['errors']}；质量通过 {len(good)}；有效报价 {len(valued)}。</p><h2>通过量化质量与价格规则：{len(passed)} 家</h2><p>这不是自动买入建议。当前模型不验证护城河、治理、收费年限、完整债务或周期底部盈利，行业估值和原公告还需核查。若抓取失败或数据缺失，本次名单不完整。</p><p>正常化盈利=min(TTM归母，TTM扣非归母，最近三年归母利润中位数)。价格规则：股价≤基准情景×(1−折让要求)，可配置是否同时低于保守情景。五年假设股利与退出价值贴现，不使用动态PE直接判断低估。</p><p>当前可比期间：{e(summary['period'])}；财报仅使用本地文件，不自动补下载。报价最长允许{cfg.get('max_quote_age_days',7)}天，非交易日可能是上一交易日。缺数据不填零，不重分配权重。</p><details><summary>本次参数快照与公式</summary><pre>{e(json.dumps(cfg,ensure_ascii=False,indent=2))}</pre><p>V/E0=Σ(t=1..5)[(1+g)^t×分配率/(1+r)^t]+(1+g)^5×退出PE/(1+r)^5。终值通常占比高；假设并未证明行业可持续性，观察价不是跌到就买。</p></details><a href="全部公司.csv">全部公司CSV</a> · <a href="通过价格规则.csv">通过规则CSV</a> · <a href="筛选结果.json">完整结果JSON</a></section><section><button id="toggle">仅显示通过价格规则的公司</button><div class="scroll"><table><thead><tr><th>公司</th><th>财务分</th><th>报价</th><th>基准情景</th><th>折让观察价</th><th>基准折让</th><th>状态</th></tr></thead><tbody id="stocks">{table}</tbody></table></div></section><h2>逐家公司细则</h2>{details}<details><summary>排除、未通过质量与错误记录</summary><div class="scroll"><table>{failures}</table></div></details></main><script>let only=false;document.getElementById('toggle').onclick=function(){{only=!only;document.querySelectorAll('#stocks tr').forEach(r=>r.hidden=only&&r.dataset.pass!=='true');this.textContent=only?'显示全部质量通过公司':'仅显示通过价格规则的公司'}};</script></html>'''
    (out/'筛选看板.html').write_text(doc,encoding='utf-8')
    return passed

def main():
    p=argparse.ArgumentParser(description='独立质量与安全边际筛选模块（不交易）')
    p.add_argument('--root',type=Path,default=ROOT);p.add_argument('--job',type=Path)
    p.add_argument('--period',default=default_period());p.add_argument('--config',type=Path,default=ROOT/'safety_config.json')
    p.add_argument('--quotes-file',type=Path,help='离线使用本模块原始报价JSON，仍检查时间戳')
    p.add_argument('--output',type=Path);p.add_argument('--refresh-financial',action='store_true');p.add_argument('--no-open',action='store_true')
    p.add_argument('--cache-dir',type=Path,help='测试或自定义缓存目录；默认项目data/safety_cache')
    a=p.parse_args()
    try:
        report_date=datetime.strptime(a.period,'%Y%m%d').date()
        if report_date>datetime.now(TZ).date():raise ValueError('不能使用未来报告期')
        if a.period[4:] not in ('0331','0630','0930','1231'):raise ValueError('报告期必须为季度末')
        cfg=validate(load(a.config));root=a.root.resolve();sys.path.insert(0,str(root))
        model=importlib.import_module('financial_score')
        if model.VERSION!='2.0.0-basic':raise ValueError('本模块需要v2基础财务模型，不能混用旧版评分')
        mc=model.load_config(root/'score_config.json')
        job=a.job or Path(load(root/'data'/'oneclick'/'active.json')['job'])
        states=sorted((job/'states').glob('*.json'))
        if not states:raise ValueError('没有一键研究任务状态，请先运行原任务')
        out=a.output or root/'分析结果'/'质量与安全边际'/datetime.now(TZ).strftime('%Y%m%d_%H%M%S_%f');out.mkdir(parents=True,exist_ok=False)
        records=[];excluded=errors=calculated=cached=0
        version=hashlib.sha256((root/'financial_score.py').read_bytes()).hexdigest()
        for i,path in enumerate(states,1):
            state=load(path);r=dict(code=state['code'],name=state['name'],quality=None,quality_coverage=None)
            try:
                if state['status']=='excluded':excluded+=1;r.update(status='excluded',reasons=[state.get('reason','原任务行业排除')])
                else:
                    files=[sorted((root/'data'/('a_'+r['code'])).glob(k+'_*.csv'))[-1] for k in ('income','balance','cashflow')]
                    signature=hashlib.sha256(json.dumps(dict(files=[(str(f),f.stat().st_size,f.stat().st_mtime_ns) for f in files],model=version,config=mc,period=a.period),sort_keys=True).encode()).hexdigest()
                    cp=(a.cache_dir or root/'data'/'safety_cache')/(r['code']+'.json')
                    entry=load(cp) if cp.exists() and not a.refresh_financial else {}
                    if entry.get('signature')==signature:r=entry['row'];cached+=1
                    else:
                        r=evaluate_company(root,state,a.period,model,mc,files)
                        atomic(cp,dict(signature=signature,row=r))
                    calculated+=1;r['reasons']=quality_reasons(r,cfg);r['quality_pass']=not r['reasons'];r['status']='quality_pass' if r['quality_pass'] else 'quality_rejected'
            except (OSError,ValueError,KeyError,IndexError) as exc:errors+=1;r.update(status='error',reasons=[str(exc)],quality_pass=False)
            r['reasons_text']='；'.join(r['reasons']);records.append(r)
            if i%100==0:print(f'财务 {i}/{len(states)}，缓存{cached}，错误{errors}',flush=True);atomic(out/'阶段进度.json',dict(processed=i,total=len(states),errors=errors))
        candidates=[r for r in records if r.get('quality_pass')]
        if a.quotes_file:
            source=load(a.quotes_file);quotes={q['f12']:q for response in source['responses'] for q in response['data']['diff']};quote_errors=source.get('errors',[])
            atomic(out/'原始报价.json',source)
        else:quotes,quote_errors=get_quotes(candidates,out)
        now=datetime.now(TZ)
        for r in candidates:
            try:price_result(r,quotes.get(r['code'],{}),cfg,now);r['status']='valued'
            except (ValueError,KeyError,TypeError) as exc:r.update(status='quote_error',reasons=[str(exc)],reasons_text=str(exc))
        summary=dict(total=len(states),excluded=excluded,calculated=calculated,errors=errors,cached=cached,period=a.period,job=str(job),quote_errors=quote_errors,finished_at=now.isoformat())
        passed=write_report(out,records,summary,cfg)
        print(f'完成：质量通过{len(candidates)}家，通过价格规则{len(passed)}家。看板：{out/"筛选看板.html"}',flush=True)
        if not a.no_open:
            import webbrowser;webbrowser.open((out/'筛选看板.html').resolve().as_uri())
    except (OSError,ValueError,KeyError,ImportError) as exc:p.exit(1,'本次筛选未完成：'+str(exc)+'\n原始财报和一键研究任务不会被修改。\n')

# evaluate_company is generated below from the verified research implementation.


def evaluate_company(root,state,period,model,mc,files):
    code=state['code'];annual={};recent={};inputs=[];warnings=set();currencies=set()
    recent_year=int(period[:4]);annual_period=period[4:]=='1231'
    end=recent_year if annual_period else recent_year-1
    full_date=f'{end}-12-31';current_date=f'{period[:4]}-{period[4:6]}-{period[6:]}'
    prior_date=f'{recent_year-1}-{period[4:6]}-{period[6:]}'
    for kind,path in zip(('income','balance','cashflow'),files):
        inputs.append(dict(file=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        parsed,cc,_=model.parse_statement(path,kind,'a',code,mc['field_aliases'],warnings)
        currencies.update(cc)
        for d,vs in parsed.items():annual.setdefault(d,{}).update(vs)
        with path.open(encoding='utf-8-sig',newline='') as f:
            for row in csv.DictReader(f):
                d=row.get('REPORT_DATE','')[:10]
                if d in (full_date,prior_date,current_date):
                    target=recent.setdefault(d,{})
                    for key in ['PARENT_NETPROFIT','NETPROFIT','DEDUCT_PARENT_NETPROFIT','BASIC_EPS','NETCASH_OPERATE','CONSTRUCT_LONG_ASSET','OPERATE_INCOME','TOTAL_ASSETS','TOTAL_LIABILITIES','TOTAL_PARENT_EQUITY','SHARE_CAPITAL']:
                        if key in row and val(row[key]) is not None:target[key]=val(row[key])
    if len(currencies)>1:raise ValueError('不同币种报表不能混算')
    for d,vs in annual.items():
        if all(k in vs for k in ['assets','liabilities','equity']) and abs(vs['assets']-vs['liabilities']-vs['equity'])>max(1,abs(vs['assets'])*.001):raise ValueError('资产负债表不平：'+d)
    dates=[f'{y}-12-31' for y in range(end-4,end+1)]
    metrics=model.analyze(annual,dates,mc,{})
    points=sum(m['points'] or 0 for m in metrics);covered=sum(m['weight'] for m in metrics if m['points'] is not None)
    r=dict(code=code,name=state['name'],quality=points,quality_coverage=covered,return_points=state.get('return_points'),return_coverage=state.get('return_coverage'),metrics=metrics,inputs=inputs)
    if all(d in recent for d in (full_date,prior_date,current_date)):
        full,old,cur=[recent[d] for d in (full_date,prior_date,current_date)]
        def ttm(key):return cur[key] if annual_period else full[key]+cur[key]-old[key]
        try:
            r.update(ttm_profit=ttm('PARENT_NETPROFIT'),ttm_core=ttm('DEDUCT_PARENT_NETPROFIT'),ttm_group_profit=ttm('NETPROFIT'),ttm_cfo=ttm('NETCASH_OPERATE'),ttm_capex=ttm('CONSTRUCT_LONG_ASSET'),profit_yoy=cur['PARENT_NETPROFIT']/old['PARENT_NETPROFIT']-1 if old['PARENT_NETPROFIT']>0 else None,
                liability_ratio=cur['TOTAL_LIABILITIES']/cur['TOTAL_ASSETS'] if cur.get('TOTAL_ASSETS',0)>0 else None,
                parent_equity=cur.get('TOTAL_PARENT_EQUITY'),shares_nominal=cur.get('SHARE_CAPITAL'),
                median3profit=sorted(annual[f'{y}-12-31']['parent_profit'] for y in range(end-2,end+1))[1],
                three_year_fcf=sum(annual[f'{y}-12-31']['cfo']-annual[f'{y}-12-31']['capex'] for y in range(end-2,end+1)))
        except (KeyError,ZeroDivisionError):r['recent_error']='最新季度字段不足'
    else:r['recent_error']='缺所选报告期或可比期间'
    return r

if __name__=="__main__":main()

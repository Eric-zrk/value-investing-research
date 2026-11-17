"""Resumable whole-universe research. No orders; no fabricated rankings."""
import argparse,csv,hashlib,html,json,os,shutil,subprocess,sys,time
from pathlib import Path
from datetime import datetime,timezone,timedelta
ROOT=Path(__file__).resolve().parent
TZ=timezone(timedelta(hours=8))
REQUIRED=['a_market.py','fetch_financials.py','financial_score.py','shareholder_return.py','score_config.json','return_config.json']

def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def stamp():return datetime.now(TZ).strftime('%Y%m%d_%H%M%S_%f')
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def atomic(p,obj):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_name(p.name+'.tmp');tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8');os.replace(tmp,p)
def execute(root,script,args,log,timeout):
    with log.open('a',encoding='utf-8') as f:
        f.write('\n'+datetime.now(TZ).isoformat()+' '+script+'\n');f.flush()
        r=subprocess.run([sys.executable,'-B','-X','utf8',str(root/script)]+[str(x) for x in args],stdout=f,stderr=subprocess.STDOUT,timeout=timeout)
    if r.returncode:raise ValueError(f'{script}失败，详见{log}')

def eligibility(row):
    industry=row.get('industry','')
    if not industry:return '行业未知，无法确认通用模型适用性'
    if any(x in industry for x in ('银行','保险','证券','金融','房地产')):return '金融/地产须专用模型'
    return ''

def pick_top(records,n):
    eligible=[r for r in records if r.get('status')=='scored' and r.get('return_coverage')==100]
    return sorted(eligible,key=lambda r:(-r['return_points'],-r['dividend_points'],r['code']))[:n]

def core_inputs(root,code,year,max_age):
    folder=root/'data'/('a_'+code);files=[]
    for kind in ('income','balance','cashflow'):
        paths=sorted(folder.glob(kind+'_*.csv'))
        if not paths:return None
        p=paths[-1]
        if time.time()-p.stat().st_mtime>max_age*86400:return None
        with p.open(encoding='utf-8-sig',newline='') as f:
            dates={r.get('REPORT_DATE','')[:10] for r in csv.DictReader(f)}
        count=6 if kind=='balance' else 5
        if not {f'{y}-12-31' for y in range(year-count+1,year+1)}<=dates:return None
        files.append(p)
    return files

def load_market(root,job,year,force):
    candidates=sorted((root/'分析结果'/'A股全市场').glob('*/分析数据.json'))
    good=[]
    for p in candidates:
        meta=p.with_name('分析元数据.json')
        if meta.exists() and read(meta).get('period')==f'{year}1231':good.append(p)
    use=good[-1] if good else None
    if force or use is None or time.time()-use.stat().st_mtime>7*86400:
        out=job/'market'
        execute(root,'a_market.py',['--root',root,'--period',f'{year}1231','--output',out],job/'market.log',650)
        use=out/'分析数据.json'
    rows=read(use)
    if not rows:raise ValueError('没有市场名单')
    if len({r['code'] for r in rows})!=len(rows):raise ValueError('市场名单代码重复')
    atomic(job/'market_rows.json',rows)
    atomic(job/'market_source.json',dict(file=str(use),sha256=digest(use),metadata=read(use.with_name('分析元数据.json'))))
    return rows

def company_page(row,record,ret,fin,out):
    out.mkdir(parents=True,exist_ok=True)
    e=lambda x:html.escape(str(x),quote=True)
    def frame(file,title):
        if not file.exists():return f'<p>{e(title)}：未完成，请查日志。</p>'
        return f'<iframe title="{e(title)}" srcdoc="{e(file.read_text(encoding="utf-8"))}" loading="lazy"></iframe>'
    rows=''.join(f'<tr><td>{e(k)}</td><td>{e(v) if v is not None else "未提供"}</td></tr>' for k,v in row.items())
    finance=read(fin/'评分结果.json') if (fin/'评分结果.json').exists() else None
    financial_text=(f"{finance['points']}/80，覆盖{finance['covered_weight']}/80，版本{finance['version']}" if finance else '未完成，不填零')
    doc=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(row['code'])}综合研究</title><style>body{{font:16px/1.7 system-ui,'Microsoft YaHei';margin:0;background:#f1f5f6;color:#1b303b}}main{{max-width:1250px;margin:auto;padding:24px}}section{{background:white;padding:20px;border-radius:12px;margin:16px 0}}iframe{{width:100%;height:1600px;border:0;background:white}}td{{padding:8px;border-bottom:1px solid #dde5e9;overflow-wrap:anywhere}}table{{width:100%}}a{{color:#147262}}nav{{position:sticky;top:0;background:#e3eded;padding:12px}}summary{{cursor:pointer}}</style><main><a href="../总览.html">← 返回总览</a><h1>{e(row['code'])} {e(row['name'])} · 综合研究</h1><nav><a href="#returns">股东回报</a>　<a href="#financial">财务质量</a>　<a href="#market">市场数据</a></nav><section><p>股东回报：{record['return_points']:.2f}/100，覆盖{record['return_coverage']:g}/100</p><p>财务质量：{e(financial_text)}</p><p>排序只依据股东回报分，同分按分红子分再按代码；不同模块不相加。源内分红记录可能漏项，高分不代表预期总收益率。</p><p>范围为模型适用的一般企业，市场值来自固定快照；行情数据不等于最新成交价。ST或退市名称等风险请查看下方市场明细。</p></section><h2 id="returns">股东回报倾向 · 全部细则</h2>{frame(ret/'股东回报评分.html','股东回报评分')}<h2 id="financial">财务质量 · 全部细则</h2>{frame(fin/'可视化评分报告.html','财务质量评分')}<section id="market"><h2>市场模块 · 完整快照字段</h2><p>字段沿用A股市场模块：price为元，market_cap与amount为元，dynamic_pe为动态PE，百分比字段为百分数。历史local_quality字段可能早于本次评分，以本页上方新报告为准。</p><table>{rows}</table><p>本次市场原始来源和报告期详见总览目录中的market_source.json。</p></section></main></html>'''
    (out/'综合研究.html').write_text(doc,encoding='utf-8')

def overview(job,records,selected,total,finished,fin_states):
    e=lambda x:html.escape(str(x),quote=True)
    scored=sum(r.get('status')=='scored' for r in records)
    complete=sum(r.get('status')=='scored' and r.get('return_coverage')==100 for r in records)
    failed=sum(r.get('status')=='failed' for r in records)
    enrichment_failed=sum('points' not in fin_states.get(r['code'],{}) for r in selected)
    excluded=sum(r.get('status')=='excluded' for r in records)
    pending=total-len(records)
    full=finished and failed==0 and pending==0
    title='本轮已完成：可比完整记录的股东回报前十' if full else '本轮尚未完整成功：当前已核验记录中的暂列前十'
    rows=''
    for i,r in enumerate(selected,1):
        fs=fin_states.get(r['code'],{})
        score=f"{fs['points']}/80，覆盖{fs['covered_weight']}/80" if 'points' in fs else '未完成'
        rows+=f'<tr><td>{i}</td><td><a href="{r["code"]}/综合研究.html">{e(r["code"])} {e(r["name"])}</a></td><td>{r["return_points"]:.2f}/100</td><td>{score}</td></tr>'
    failures=''.join(f'<tr><td>{e(r["code"])}</td><td>{e(r["name"])}</td><td>{e(r.get("reason",r.get("status")))}</td></tr>' for r in records if r.get('status')!='scored' or r.get('return_coverage')!=100)
    doc=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>一键股东回报研究</title><style>body{{font:16px/1.7 system-ui,'Microsoft YaHei';background:#f2f6f7;color:#1b303b}}main{{max-width:1100px;margin:auto;padding:24px}}section{{background:white;padding:20px;margin:16px 0;border-radius:12px}}table{{width:100%;border-collapse:collapse}}td,th{{text-align:left;padding:12px;border-bottom:1px solid #dae3e7}}a{{color:#137465}}summary{{cursor:pointer}}</style><main><h1>一键股东回报研究</h1><section><h2>{title}</h2><p>市场名单{total}家；已算回报分{scored}家；完整可比{complete}家；行业不适用{excluded}家；失败{failed}家；待处理{pending}家。</p><p>当前前十中尚有{enrichment_failed}份财务报告未完成；请查看公司执行日志。</p><p>数据不完整者不按零分排名。范围只限适用通用模型且完整评分的记录，不能称为所有A股中客观最好的公司。缺数据或失败可能改变前十；不提供收益保证。</p><p>财务80分与回报100分部分指标重叠，不能相加。每家公司点击进入一页综合报告，查看权重、得分、年度数据、风险及市场字段。</p></section><section><table><tr><th>序号</th><th>公司</th><th>股东回报分</th><th>本次财务分</th></tr>{rows}</table></section><section><details><summary>未入完整排名的记录与原因</summary><table>{failures}</table></details></section><p>本轮文件：{e(job)}。执行日志、逐公司状态和固定市场快照保存在本目录。再次运行同一个任务会跳过输入与配置未变且成功的记录。</p></main></html>'''
    (job/'总览.html').write_text(doc,encoding='utf-8')
    atomic(job/'任务摘要.json',dict(total=total,scored=scored,complete=complete,failed=failed,excluded=excluded,pending=pending,finished=finished,complete_execution=full,selected=selected,financial=fin_states))
    if records:
        keys=['code','name','status','return_points','return_coverage','dividend_points','reason']
        with (job/'全部记录.csv').open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=keys,extrasaction='ignore');w.writeheader();w.writerows(records)

def main():
    p=argparse.ArgumentParser(description='一键计算全市场可比股东回报前十并综合研究')
    p.add_argument('--root',type=Path,default=ROOT);p.add_argument('--end-year',type=int,default=datetime.now(TZ).year-1)
    p.add_argument('--new',action='store_true',help='新建任务，刷新市场快照')
    p.add_argument('--max-companies',type=int,help='本次最多处理的公司数，不设置则处理整队列')
    p.add_argument('--cache-days',type=int,default=30);p.add_argument('--offline',action='store_true',help='仅用已有财报及分红；缺项记失败，不联网')
    p.add_argument('--no-open',action='store_true',help='不自动打开浏览器，供批处理验证')
    a=p.parse_args();root=a.root.resolve()
    if a.end_year>=datetime.now(TZ).year or a.cache_days<1 or (a.max_companies is not None and a.max_companies<1):p.error('年度/数量/缓存天数无效')
    try:
        for name in REQUIRED:
            if not (root/name).exists():raise ValueError('缺少模块：'+name+'，请先安装对应模块')
        if not a.offline:
            import importlib
            importlib.import_module('akshare')
        fingerprint=hashlib.sha256(json.dumps(dict(year=a.end_year,cache=a.cache_days,files={n:digest(root/n) for n in REQUIRED}),sort_keys=True).encode()).hexdigest()
        pointer=root/'data'/'oneclick'/'active.json'
        old=read(pointer) if pointer.exists() else {}
        job=Path(old['job']) if not a.new and old.get('fingerprint')==fingerprint else root/'分析结果'/'一键研究'/stamp()
        if not job.exists():
            job.mkdir(parents=True)
            if a.offline:
                paths=sorted((root/'分析结果'/'A股全市场').glob('*/分析数据.json'))
                if not paths:raise ValueError('无已分析的市场快照，离线任务无法建立')
                src=paths[-1];meta=read(src.with_name('分析元数据.json'))
                if meta.get('period')!=f'{a.end_year}1231':raise ValueError('已有快照的年度与任务不一致，无法离线混用')
                atomic(job/'market_rows.json',read(src));atomic(job/'market_source.json',dict(file=str(src),sha256=digest(src),metadata=meta))
            else:load_market(root,job,a.end_year,a.new)
            atomic(job/'task.json',dict(fingerprint=fingerprint,year=a.end_year,created=datetime.now(TZ).isoformat()))
            atomic(pointer,dict(fingerprint=fingerprint,job=str(job)))
        rows=read(job/'market_rows.json');states={};processed=0
        for path in job.glob('states/*.json'):states[path.stem]=read(path)
        print(f'任务：{job}\n名单{len(rows)}家；首次可能需数小时甚至更久。可关闭窗口，下次继续。',flush=True)
        state_dir=job/'states';state_dir.mkdir(exist_ok=True)
        finished=True
        for row in rows:
            c=row['code'];base=dict(code=c,name=row['name'])
            why=eligibility(row)
            if why:states[c]=dict(base,status='excluded',reason=why);atomic(state_dir/(c+'.json'),states[c]);continue
            inputs=core_inputs(root,c,a.end_year,a.cache_days)
            ds=sorted((root/'data'/'dividends'/c).glob('*.json'))
            dividend=ds[-1] if ds and time.time()-ds[-1].stat().st_mtime<=a.cache_days*86400 else None
            signature=hashlib.sha256(''.join(digest(x) for x in inputs+[dividend]).encode()).hexdigest() if inputs and dividend else None
            prior=states.get(c,{})
            if signature and prior.get('input_signature')==signature and prior.get('status')=='scored':continue
            if a.max_companies and processed>=a.max_companies:finished=False;break
            processed+=1;print(f'[{len(states)+1}/{len(rows)}] {c} {row["name"]}',flush=True)
            entry=job/c;entry.mkdir(exist_ok=True);log=entry/'执行日志.txt'
            try:
                if not inputs:
                    if a.offline:raise ValueError('无新鲜且年度齐全的三大财报，离线未下载')
                    execute(root,'fetch_financials.py',['--market','a','--symbol',c],log,230)
                    inputs=core_inputs(root,c,a.end_year,a.cache_days)
                    if not inputs:raise ValueError('已请求财报，但连续年度不足（可能上市不足六年）或下载不完整')
                ret_dir=entry/('return_'+stamp())
                args=['--symbol',c,'--root',root,'--end-year',a.end_year,'--output',ret_dir]
                if dividend:args+=['--dividend-file',dividend]
                elif a.offline:raise ValueError('无分红缓存，离线不请求')
                execute(root,'shareholder_return.py',args,log,150)
                ret=read(ret_dir/'股东回报评分.json');dividend=Path(ret['dividend_file'])
                signature=hashlib.sha256(''.join(digest(x) for x in inputs+[dividend]).encode()).hexdigest()
                states[c]=dict(base,status='scored',return_points=ret['points'],return_coverage=ret['covered'],
                              dividend_points=sum(m['points'] or 0 for m in ret['metrics'] if m['id'].startswith('dividend')),
                              return_dir=str(ret_dir),input_signature=signature,
                              reason='' if ret['covered']==100 else '股东回报数据不完整，不参与完整分排名')
            except (OSError,ValueError,KeyError,subprocess.TimeoutExpired) as exc:states[c]=dict(base,status='failed',reason=str(exc))
            atomic(state_dir/(c+'.json'),states[c])
            if processed%10==0:overview(job,list(states.values()),pick_top(list(states.values()),10),len(rows),False,{})
            time.sleep(1)
        selected=pick_top(list(states.values()),10);financial={}
        by_code={r['code']:r for r in rows}
        for record in selected:
            c=record['code'];entry=job/c;fin_dir=entry/('financial_'+stamp())
            try:
                execute(root,'financial_score.py',['--market','a','--symbol',c,'--root',root,'--end-year',a.end_year,'--output',fin_dir],entry/'执行日志.txt',90)
                fin=read(fin_dir/'评分结果.json');financial[c]={k:fin[k] for k in ('points','covered_weight','version')}
            except (OSError,ValueError,subprocess.TimeoutExpired) as exc:financial[c]=dict(error=str(exc))
            company_page(by_code[c],record,Path(record['return_dir']),fin_dir,entry)
        overview(job,list(states.values()),selected,len(rows),finished,financial)
        print('本轮完成。总览：'+str(job/'总览.html'),flush=True)
        if not a.no_open:
            import webbrowser;webbrowser.open((job/'总览.html').as_uri())
    except KeyboardInterrupt:print('任务已停止，已完成记录保留；再次双击继续。')
    except (OSError,ValueError,KeyError,ImportError,subprocess.TimeoutExpired) as exc:p.exit(1,'任务未完成：'+str(exc)+'\n保留进度，排查后重试。\n')

if __name__=='__main__':main()

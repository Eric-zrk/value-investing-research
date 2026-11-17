"""A-share bulk snapshot and transparent screening. No trading signals."""
import argparse
import csv
import hashlib
import html
import json
import math
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
CST = timezone(timedelta(hours=8))

ROOT=Path(__file__).resolve().parent
DEFAULT_FILTERS=dict(max_dynamic_pe=25,max_pb=3,min_amount=10000000,min_revenue_growth=0,min_profit_growth=0,min_roe=8)

def code(v):
    v=str(v).strip()
    for prefix in ('sh','sz','bj'):
        if v.startswith(prefix):v=v[2:]
    if not v.isascii() or not v.isdigit() or len(v)>6: raise ValueError('异常证券代码：'+v)
    return v.zfill(6)

def num(v):
    try:
        n=float(str(v).replace(',',''))
        return n if math.isfinite(n) else None
    except (ValueError,TypeError):return None

def read(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def save_csv(path,rows,columns=None):
    with Path(path).open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=columns or list(rows[0]));w.writeheader();w.writerows(rows)

def worker(source,out,period):
    started=datetime.now(CST).isoformat()
    adapter='AKShare'
    if source=='quotes':
        rows=fetch_quotes()
        save_csv(out,rows)
        version='stdlib';count=len(rows);adapter='Eastmoney HTTP paginated adapter'
    else:
        import akshare as ak
        df=ak.stock_info_a_code_name() if source=='universe' else ak.stock_yjbb_em(date=period)
        if df.empty:raise ValueError('数据源返回空表')
        df.to_csv(out,index=False,encoding='utf-8-sig')
        version=ak.__version__;count=len(df)
    meta=dict(source=source,adapter=adapter,akshare=version,started_at=started,
              completed_at=datetime.now(CST).isoformat(),rows=count,
              statement_period=period if source=='financial' else None,
              sha256=hashlib.sha256(out.read_bytes()).hexdigest())
    out.with_suffix('.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')

def fetch_quotes():
    """Explicit pagination and count validation, independent of pandas conversion."""
    import urllib.request
    import urllib.parse
    import time
    mapping={'f12':'代码','f14':'名称','f2':'最新价','f3':'涨跌幅','f4':'涨跌额','f5':'成交量',
             'f6':'成交额','f7':'振幅','f8':'换手率','f9':'市盈率-动态','f10':'量比',
             'f15':'最高','f16':'最低','f17':'今开','f18':'昨收','f20':'总市值','f21':'流通市值',
             'f22':'涨速','f23':'市净率','f24':'60日涨跌幅','f25':'年初至今涨跌幅'}
    rows=[];seen=set();total=None;page=1
    while True:
        params=dict(pn=page,pz=100,po=1,np=1,fltt=2,invt=2,fid='f12',
            fs='m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048',fields=','.join(mapping))
        url='https://82.push2.eastmoney.com/api/qt/clist/get?'+urllib.parse.urlencode(params)
        for attempt in range(3):
            try:
                req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
                with urllib.request.urlopen(req,timeout=15) as response:body=json.load(response)
                data=body['data'];expected=int(data['total']);items=data['diff']
                if not isinstance(items,list):raise ValueError('行情返回格式变化')
                break
            except (OSError,ValueError,KeyError,TypeError):
                if attempt==2:raise
                time.sleep(1)
        if total is None:total=expected
        if expected!=total:raise ValueError('分页期间股票总数发生变化，请稍后重试')
        if not items:raise ValueError('分页提前返回空表，拒绝发布不完整行情')
        for item in items:
            c=code(item['f12'])
            if c in seen:raise ValueError('行情分页发生重复，拒绝发布不完整快照')
            seen.add(c);rows.append({label:(c if key=='f12' else item.get(key)) for key,label in mapping.items()})
        print(f'行情分页 {page}：{len(rows)}/{total}',flush=True)
        if len(rows)>=total:
            if len(rows)!=total:raise ValueError('行情行数与数据源声明不一致')
            return rows
        page+=1
        if page>500:raise ValueError('分页超过保护上限')
        time.sleep(.15)

def unique(rows,key,kind):
    result={}
    for r in rows:
        c=code(r[key])
        if c in result and result[c]!=r:raise ValueError(f'{kind}存在重复冲突代码：{c}')
        result[c]=r
    return result

def scan(universe,quotes,financial,filters,period,local_scores=None):
    u=unique(universe,'code','上市名单');q=unique(quotes,'代码','行情');f=unique(financial,'股票代码','业绩')
    if not u:raise ValueError('上市名单为空，无法检查覆盖率')
    # Include provider extras and label them explicitly; never silently lose an unmatched listing.
    results=[];local_scores=local_scores or {}
    for c in sorted(u):
        name=u.get(c,{}).get('name') or q.get(c,{}).get('名称') or f.get(c,{}).get('股票简称','')
        quote=q.get(c,{});fin=f.get(c,{})
        price=num(quote.get('最新价'));pe=num(quote.get('市盈率-动态'));pb=num(quote.get('市净率'))
        amount=num(quote.get('成交额'));roe=num(fin.get('净资产收益率'))
        rg=num(fin.get('营业总收入-同比增长'));pg=num(fin.get('净利润-同比增长'));profit=num(fin.get('净利润-净利润'))
        industry=fin.get('所处行业','');warnings=[];reasons=[]
        if c not in u:warnings.append('未在本次上市名单中，需核查')
        if c not in q:warnings.append('缺行情，可能停牌、接口漏项或名单差异')
        if c not in f:warnings.append('缺指定期业绩，不按零处理')
        if 'ST' in name.upper() or '退' in name:reasons.append('名称含ST/退市标识')
        if any(x in industry for x in ('银行','保险','证券','多元金融','房地产')):reasons.append('需要行业专用财务模型')
        if not industry:reasons.append('行业未知')
        if price is None or price<=0:reasons.append('无有效价格')
        if pe is None or pe<=0:reasons.append('动态PE无效或非正')
        elif pe>filters['max_dynamic_pe']:reasons.append('动态PE超过筛选阈值')
        if pb is None or pb<=0:reasons.append('PB无效或非正')
        elif pb>filters['max_pb']:reasons.append('PB超过筛选阈值')
        if amount is None or amount<filters['min_amount']:reasons.append('快照成交额不足/缺失')
        if profit is None or profit<=0:reasons.append('指定期净利润非正/缺失')
        if roe is None or roe<filters['min_roe']:reasons.append('指定期ROE低于阈值/缺失')
        if rg is None or rg<filters['min_revenue_growth']:reasons.append('收入同比低于阈值/缺失')
        if pg is None or pg<filters['min_profit_growth']:reasons.append('利润同比低于阈值/缺失')
        if c not in u:reasons.append('名单外证券暂不纳入初筛')
        turnover=num(quote.get('换手率'))
        if turnover is not None and turnover>20:warnings.append('当日换手率超过20%，交易活跃不代表质量高')
        if pb is not None and pb<1:warnings.append('破净需解释，低PB不自动代表低估')
        ls=local_scores.get(c,{})
        results.append(dict(code=c,name=name,industry=industry,listed=c in u,has_quote=c in q,has_financial=c in f,
            price=price,dynamic_pe=pe,pb=pb,amount=amount,turnover=turnover,market_cap=num(quote.get('总市值')),
            change_pct=num(quote.get('涨跌幅')),change_60d=num(quote.get('60日涨跌幅')),
            profit=profit,revenue=num(fin.get('营业总收入-营业总收入')),revenue_growth_pct=rg,profit_growth_pct=pg,
            roe_pct=roe,eps=num(fin.get('每股收益')),cash_per_share=num(fin.get('每股经营现金流量')),
            gross_margin_pct=num(fin.get('销售毛利率')),report_period=period,announcement=fin.get('最新公告日期',''),
            candidate=not reasons,reasons='；'.join(reasons),warnings='；'.join(warnings),
            local_quality=ls.get('points'),local_coverage=ls.get('covered_weight'),local_version=ls.get('version',''),
            local_period=ls.get('dates',[''])[-1],local_report=ls.get('report','')))
    # Same-industry positive-multiple percentiles: cheapness context, not quality points.
    import bisect
    groups={}
    for row in results:
        if row['industry']:
            for field in ('dynamic_pe','pb'):
                if row[field] is not None and row[field]>0:
                    groups.setdefault((row['industry'],field),[]).append(row[field])
    for values in groups.values():values.sort()
    for row in results:
        for field in ('dynamic_pe','pb'):
            peers=groups.get((row['industry'],field),[])
            val=row[field];percentile=None
            if len(peers)>=5 and val is not None and val>0:
                percentile=100*(bisect.bisect_left(peers,val)+(bisect.bisect_right(peers,val)-bisect.bisect_left(peers,val))/2)/len(peers)
            row[field+'_industry_percentile']=percentile
            row[field+'_peer_count']=len(peers)
    coverage=dict(listed=len(u),quotes_matched=len(set(u)&set(q)),financial_matched=len(set(u)&set(f)),
                  rows=len(results),candidates=sum(r['candidate'] for r in results),
                  missing_quotes=sorted(set(u)-set(q)),missing_financial=sorted(set(u)-set(f)),
                  quote_codes_outside_universe=sorted(set(q)-set(u)),financial_codes_outside_universe=sorted(set(f)-set(u)))
    return results,coverage

def load_local(root):
    result={}
    for folder in (root/'分析结果').glob('a_*'):
        paths=sorted(folder.glob('*/评分结果.json'))
        if not paths:continue
        try:
            r=json.loads(paths[-1].read_text(encoding='utf-8-sig'))
            c=code(r['symbol']);r['report']=str(paths[-1].with_name('可视化评分报告.html'))
            result[c]=r
        except (ValueError,KeyError,OSError):continue
    return result

def render(rows,meta,path):
    # Escape data before putting it inside a script; names never become executable markup.
    payload=json.dumps(rows,ensure_ascii=False,allow_nan=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    esc=lambda x:html.escape(str(x))
    cov=meta['coverage'];filters=meta['filters']
    description=f"名单 {cov['listed']} 只；行情匹配 {cov['quotes_matched']} 只；指定期业绩匹配 {cov['financial_matched']} 只；初筛候选 {cov['candidates']} 只。"
    doc=r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>A股全市场研究</title><style>
body{font:15px/1.6 system-ui,"Microsoft YaHei",sans-serif;background:#f4f6f7;color:#182b36;margin:0}main{max-width:1500px;margin:auto;padding:28px}h1{margin:0}section{background:white;padding:18px;border-radius:12px;margin:16px 0}.muted{color:#566674}input,select,button{font:inherit;padding:8px;margin:4px;border:1px solid #c6d0d5;border-radius:6px;background:white;color:inherit}table{width:100%;border-collapse:collapse;white-space:nowrap}th,td{padding:10px;border-bottom:1px solid #e1e6e9;text-align:left}th{position:sticky;top:0;background:#edf2f4}td:last-child{white-space:normal;min-width:280px}.scroll{max-height:70vh;overflow:auto}.pass{color:#176b57;font-weight:600}.warn{color:#985c0b}.numbers{font-variant-numeric:tabular-nums}summary{cursor:pointer}a{color:#15645e}@media(prefers-color-scheme:dark){body{background:#111c23;color:#e4edf2}section,input,select,button{background:#1b2b35}th{background:#263a46}td,th{border-color:#40525d}.muted{color:#b0c0ca}.pass{color:#71d3b6}.warn{color:#efc078}}
</style><main><h1>A股全市场研究</h1><p class="muted">行情、估值和业绩快照 · 初筛结果不是买入建议</p><section><p>DESCRIPTION</p><p>抓取时间：CAPTURE · 业绩报告期：PERIOD</p><p class="warn">WARNINGS</p><details><summary>查看筛选阈值与口径</summary><p>FILTERS</p><p>动态PE不是TTM PE或基于年报的静态PE，不能据此直接判断内在价值。ROE和同比来自指定报告期，季度ROE不年化。成交额仅为单个快照，不代表长期流动性。未计算资金流向评分或涨跌预测。未识别所有财务风险。</p></details></section>
<section><input id="search" placeholder="输入股票代码、名称或行业" aria-label="搜索"><select id="status" aria-label="筛选状态"><option value="all">全部股票</option><option value="pass">符合初筛条件</option><option value="missing">缺行情或业绩</option></select><select id="sort" aria-label="排序"><option value="code">按股票代码</option><option value="dynamic_pe">动态PE从低到高</option><option value="roe_pct">指定期ROE从高到低</option><option value="amount">成交额从高到低</option></select><button id="export">导出当前表格CSV</button><p id="count"></p><div class="scroll"><table class="numbers"><thead><tr><th>代码/名称</th><th>行业</th><th>价格</th><th>动态PE</th><th>PB</th><th>同行PE分位%</th><th>同行PB分位%</th><th>市值(亿元)</th><th>成交额(万元)</th><th>收入同比%</th><th>利润同比%</th><th>指定期ROE%</th><th>本地财务分/覆盖/版本</th><th>初筛及原因</th></tr></thead><tbody id="body"></tbody></table></div></section><p class="muted">快照数据的抓取时间不等于交易所成交时间。非交易日可能返回上一交易时段行情。原始数据与元数据保存在同一批次目录。覆盖不足时不宣称已获取全部上市公司的完整数据。数据源覆盖上市名单内的当前股票，不含完整退市历史；不能直接用于无幸存者偏差回测。</p></main><script>
const rows=PAYLOAD;let visible=[];const el=id=>document.getElementById(id);const fmt=v=>v===null||v===undefined?'未提供':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:2});
function draw(){const term=el('search').value.trim().toLowerCase(),status=el('status').value,key=el('sort').value;
visible=rows.filter(r=>(r.code+r.name+r.industry).toLowerCase().includes(term)&&(status==='all'||(status==='pass'?r.candidate:!r.has_quote||!r.has_financial)));
visible.sort((a,b)=>{if(key==='code')return a.code.localeCompare(b.code);if(a[key]===null)return b[key]===null?0:1;if(b[key]===null)return -1;return key==='dynamic_pe'?a[key]-b[key]:b[key]-a[key]});
el('count').textContent='当前显示 '+visible.length+' / '+rows.length+' 只；未评分和缺失数据均保留。';const body=el('body');body.replaceChildren();for(const r of visible){const tr=document.createElement('tr');const values=[r.code+' '+r.name,r.industry||'未提供',fmt(r.price),fmt(r.dynamic_pe),fmt(r.pb),fmt(r.dynamic_pe_industry_percentile),fmt(r.pb_industry_percentile),fmt(r.market_cap===null?null:r.market_cap/1e8),fmt(r.amount===null?null:r.amount/1e4),fmt(r.revenue_growth_pct),fmt(r.profit_growth_pct),fmt(r.roe_pct),r.local_quality===null?'未做本地五年财务分析':r.local_quality+'/80 · 覆盖'+r.local_coverage+'/80 · '+r.local_version+' · '+r.local_period,(r.candidate?'符合初筛条件':'未入初筛：'+r.reasons)+(r.warnings?'；提醒：'+r.warnings:'')];values.forEach((v,i)=>{const td=document.createElement('td');td.textContent=v;if(i===13)td.className=r.candidate?'pass':'warn';tr.append(td)});body.append(tr)}}
el('search').addEventListener('input',draw);el('status').addEventListener('change',draw);el('sort').addEventListener('change',draw);el('export').onclick=()=>{const keys=['code','name','industry','price','dynamic_pe','pb','amount','revenue_growth_pct','profit_growth_pct','roe_pct','candidate','reasons','warnings'];const cell=v=>'"'+String(v??'').replaceAll('"','""')+'"';const text='\ufeff'+[keys,...visible.map(r=>keys.map(k=>r[k]))].map(a=>a.map(cell).join(',')).join('\r\n');const u=URL.createObjectURL(new Blob([text],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=u;a.download='A股筛选结果.csv';a.click();setTimeout(()=>URL.revokeObjectURL(u),1000)};draw();
</script></html>'''
    warnings='；'.join(meta['warnings']) or '行情与指定期业绩不是同一时点的年度价值判断；请继续核查财报。'
    doc=doc.replace('DESCRIPTION',esc(description)).replace('CAPTURE',esc(meta['captured_at'])).replace('PERIOD',esc(meta['period'])).replace('WARNINGS',esc(warnings)).replace('FILTERS',esc(json.dumps(filters,ensure_ascii=False))).replace('PAYLOAD',payload)
    Path(path).write_text(doc,encoding='utf-8')

def main():
    p=argparse.ArgumentParser(description='A股全市场快照与价值研究初筛')
    p.add_argument('--root',type=Path,default=ROOT)
    p.add_argument('--period',default=f'{datetime.now(CST).year-1}1231')
    p.add_argument('--offline',type=Path,help='重算已有快照目录，不联网')
    p.add_argument('--output',type=Path)
    p.add_argument('--config',type=Path,default=ROOT/'market_config.json')
    p.add_argument('--worker',choices=['universe','quotes','financial'])
    p.add_argument('--worker-out',type=Path)
    p.add_argument('--open-report',action='store_true')
    a=p.parse_args()
    if a.worker:worker(a.worker,a.worker_out,a.period);return
    try:
        datetime.strptime(a.period,'%Y%m%d')
        if a.period[4:] not in ('0331','0630','0930','1231'):raise ValueError('请选择季末报告期')
        filters=json.loads(a.config.read_text(encoding='utf-8-sig'))
        if set(filters)!=set(DEFAULT_FILTERS) or any(num(v) is None or v<0 for v in filters.values()):raise ValueError('筛选参数配置不完整或无效')
        now=datetime.now(CST)
        snapshot=a.offline or a.root/'data'/'a_market'/now.strftime('%Y%m%d_%H%M%S_%f')
        if a.offline:
            if not snapshot.is_dir():raise ValueError('快照目录不存在')
        else:
            snapshot.mkdir(parents=True,exist_ok=False)
            for source in ('universe','quotes','financial'):
                output=snapshot/(source+'.csv');print(f'获取 {source} ...',flush=True)
                # Isolate providers so a stalled endpoint cannot block the UI indefinitely.
                r=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'--worker',source,'--worker-out',str(output),'--period',a.period],timeout=180)
                if r.returncode:raise ValueError(f'{source}获取失败，原始文件已保留但不发布完整快照')
        raw={};provenance=[]
        for source in ('universe','quotes','financial'):
            file=snapshot/(source+'.csv');sidecar=file.with_suffix('.json')
            metadata=json.loads(sidecar.read_text(encoding='utf-8-sig'))
            digest=hashlib.sha256(file.read_bytes()).hexdigest()
            if digest!=metadata['sha256']:raise ValueError(f'{source}原始文件校验失败')
            if source=='financial' and metadata['statement_period']!=a.period:raise ValueError('快照财报期与请求不一致')
            raw[source]=read(file);provenance.append(metadata)
        results,coverage=scan(raw['universe'],raw['quotes'],raw['financial'],filters,a.period,load_local(a.root))
        warnings=[]
        if coverage['missing_quotes']:warnings.append(f"上市名单中{len(coverage['missing_quotes'])}只缺行情，已逐只标记")
        if coverage['missing_financial']:warnings.append(f"上市名单中{len(coverage['missing_financial'])}只缺指定期业绩，已逐只标记")
        if a.period[4:]!='1231':warnings.append('本次使用季度累计业绩，阈值未按季度校准，ROE不年化，不与全年直接比较')
        out=a.output or a.root/'分析结果'/'A股全市场'/now.strftime('%Y%m%d_%H%M%S_%f')
        out.mkdir(parents=True,exist_ok=False)
        meta=dict(version='2.0.0-market',period=a.period,captured_at=provenance[1]['completed_at'],
            analyzed_at=now.isoformat(),offline=bool(a.offline),snapshot=str(snapshot),sources=provenance,
            coverage=coverage,filters=filters,warnings=warnings)
        save_csv(out/'全市场分析.csv',results)
        selected=[r for r in results if r['candidate']]
        save_csv(out/'初筛候选.csv',selected,list(results[0]))
        (out/'分析元数据.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
        (out/'分析数据.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
        report=out/'A股市场看板.html';render(results,meta,report)
        summary={k:v for k,v in coverage.items() if isinstance(v,int)}
        print(f'完成：{summary}；逐只缺失记录见分析元数据.json；结果位置：{out}',flush=True)
        if a.open_report:
            import webbrowser;webbrowser.open(report.resolve().as_uri())
    except (ValueError,OSError,KeyError,subprocess.TimeoutExpired) as exc:
        p.exit(1,f'本次分析未完成：{exc}\n不会使用模拟数据替代。可保留现有快照并稍后重试。\n')

if __name__=='__main__':main()

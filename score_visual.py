"""Build a self-contained offline HTML report from the actual score result."""
import html
import json
from pathlib import Path

FORMULAS={
'revenue_growth':'(末年营业收入 ÷ 首年营业收入)^(1/4) − 1',
'profit_years':'五个年度中，归母净利润大于零的年数',
'cash_conversion':'五年经营现金流合计 ÷ 五年合并净利润合计；累计净利润非正记最低档',
'cfo_years':'五个年度中，经营现金流大于零的年数',
'net_debt_cfo':'(末年完整有息负债 − 非受限可用现金) ÷ 最近三年平均经营现金流',
'interest_cover':'最近三年(税前利润＋利息费用)合计 ÷ 最近三年利息费用合计',
'roic':'五年保守资本回报率的中位数；单年=(税前利润＋利息费用)×(1−所得税/税前利润) ÷ 平均(总权益＋完整有息负债)',
'fcf_years':'五个年度中，经营现金流 − 购建长期资产支出大于零的年数',
'eps_growth':'(末年可比每股收益 ÷ 首年可比每股收益)^(1/4) − 1',
'share_growth':'(末年可比股数 ÷ 首年可比股数)^(1/4) − 1'}
FORMULAS.update({'profit_growth':'(末年归母净利润/首年归母净利润)^(1/4)-1','liability_ratio':'最近三个年末总负债/总资产的中位数；不等于有息债务率','roa':'五年(合并净利润/平均总资产)的中位数','roe':'五年(归母净利润/平均归母权益)的中位数；使用六个年末余额','net_margin':'五年(合并净利润/营业收入)的中位数'})
PERCENT={'revenue_growth','profit_growth','liability_ratio','roa','roe','net_margin','roic','eps_growth','share_growth'}
COUNTS={'profit_years','cfo_years','fcf_years'}
LABELS={'revenue':'营业收入','profit':'合并净利润','parent_profit':'归母净利润','cfo':'经营现金流',
'capex':'资本开支','pretax':'税前利润','tax':'所得税','interest':'利息费用','equity':'总权益',
'parent_equity':'归母权益','assets':'总资产','liabilities':'总负债','debt':'完整有息负债','available_cash':'非受限现金','eps':'可比每股收益','shares':'可比股数'}

def e(x): return html.escape(str(x),quote=True)
def num(x): return f'{x:,.2f}'.rstrip('0').rstrip('.')
def value(key,x):
    if x is None:return '未提供'
    if key in PERCENT:return f'{x*100:.2f}%'
    if key in COUNTS:return f'{x:g} 年'
    return f'{x:.2f} 倍'
def interval(key,rule,n):
    c=rule['cuts']; up=rule['direction']=='up'
    f=lambda x:value(key,x).replace(' 年','').replace(' 倍','')
    if up:
        if n==0:return f'< {f(c[0])}'
        if n==5:return f'≥ {f(c[4])}'
        return f'{f(c[n-1])} ≤ 数值 < {f(c[n])}'
    if n==5:return f'≤ {f(c[0])}'
    if n==0:return f'> {f(c[4])}'
    return f'{f(c[4-n])} < 数值 ≤ {f(c[5-n])}'

def build_report(result,path):
    ms=result['metrics']; maximum=result['maximum']; score=result['points']; covered=result['covered_weight']
    modules={}
    for m in ms:
        a=modules.setdefault(m['module'],[0,0,0])
        a[1]+=m['weight']
        if m['points'] is not None:a[0]+=m['points'];a[2]+=m['weight']
    def bar(points,weight,available):
        return f'<div class="bar" aria-label="得分{num(points)}，满分{num(weight)}，未评{num(weight-available)}"><span class="earned" style="width:{points/weight*100:.4f}%"></span><span class="lost" style="width:{(available-points)/weight*100:.4f}%"></span><span class="unknown" style="width:{(weight-available)/weight*100:.4f}%"></span></div>'
    cards=''.join(f'<article><h3>{e(name)}</h3><p class="big">{num(p)} <small>/ {num(w)} 分</small></p>{bar(p,w,c)}<p class="sub">占总分 {w/maximum*100:.2f}% · 未评 {num(w-c)} 分</p></article>' for name,(p,w,c) in modules.items())
    rows=[]; details=[]
    for index,m in enumerate(ms,1):
        key=m['id']; rule=result['config']['metrics'][key]; known=m['points'] is not None
        awarded=num(m['points']) if known else '未评'
        grade=f"{m['grade']} / 5" if known else '未评'
        rows.append(f'<tr><td><a href="#metric-{index}">{e(m["label"])}</a><small>{e(m["module"])}</small></td><td>{num(m["weight"])} 分<small>{m["weight"]/maximum*100:.2f}% 总权重</small></td><td>{e(value(key,m["value"]))}</td><td>{grade}</td><td><strong>{awarded}</strong> / {num(m["weight"])} 分{bar(m["points"] or 0,m["weight"],m["weight"] if known else 0)}</td><td>{e(m["reason"]) if not known else "已评分"}</td></tr>')
        levels=''.join(f'<tr class="{"active" if known and m["grade"]==n else ""}"><td>{n} / 5{"　← 本项命中" if known and m["grade"]==n else ""}</td><td>{e(interval(key,rule,n))}</td><td>{num(n/5*m["weight"])} / {num(m["weight"])} 分</td></tr>' for n in range(6))
        calculation=(f'实际数值 {value(key,m["value"])}，命中 {m["grade"]} 级；{m["grade"]} ÷ 5 × {num(m["weight"])} = {num(m["points"])} 分。' if known else f'本项尚未评分，原因：{m["reason"]}。对应 {num(m["weight"])} 分仍属未知。')
        details.append(f'<details id="metric-{index}"><summary>{index}. {e(m["label"])}　{awarded} / {num(m["weight"])} 分</summary><div class="inside"><p><b>计算公式：</b>{e(FORMULAS.get(key,"见模型配置"))}</p><p><b>本次计算：</b>{e(calculation)}</p><div class="scroll"><table><thead><tr><th>等级</th><th>适用区间</th><th>加权得分</th></tr></thead><tbody>{levels}</tbody></table></div></div></details>')
    keys=[k for k in LABELS if any(k in a for a in result['annual'].values())]
    annual='<thead><tr><th>报告期</th>'+''.join(f'<th>{e(LABELS[k])}</th>' for k in keys)+'</tr></thead><tbody>'
    for d,a in result['annual'].items():annual+='<tr><td>'+e(d)+'</td>'+''.join('<td>'+('未提供' if a.get(k) is None else num(a[k]))+'</td>' for k in keys)+'</tr>'
    annual+='</tbody>'
    warnings=''.join('<li>'+e(w)+'</li>' for w in result['warnings'])
    sources=''.join('<li>'+e(p['file'])+'<small>SHA256 '+e(p['sha256'])+'</small></li>' for p in result['inputs'])
    title=f'{result["market"].upper()} {result["symbol"]} · 财务评分细则'
    css='''
:root{color-scheme:light dark;--bg:#f3f5f7;--panel:#fff;--ink:#192735;--muted:#596775;--line:#d9e1e7;--accent:#126b66;--track:#dfe7eb;--unknown:#b4bbc4}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 system-ui,"Microsoft YaHei",sans-serif}main{max-width:1300px;margin:auto;padding:36px 24px}h1{font-size:30px;margin:6px 0}h2{margin:32px 0 12px;font-size:22px}h3{margin:0;font-size:16px}.sub,small{color:var(--muted)}small{display:block;font-size:13px}header{display:flex;justify-content:space-between;gap:20px;align-items:center}.panel,article,details{background:var(--panel);border-radius:12px;padding:20px}.overview{margin-top:24px}.big{font-size:32px;font-weight:600;margin:8px 0}.big small{display:inline;font-size:16px;font-weight:400}.overview .big{font-size:44px}.facts{display:flex;flex-wrap:wrap;gap:24px}.cards{display:grid;grid-template-columns:repeat(5,1fr);gap:12px}.bar{height:12px;display:flex;overflow:hidden;background:var(--track);border-radius:6px;margin:10px 0}.earned{background:var(--accent)}.lost{background:var(--track)}.unknown{background:repeating-linear-gradient(45deg,var(--unknown),var(--unknown) 3px,var(--track) 3px,var(--track) 7px)}.legend{display:flex;flex-wrap:wrap;gap:20px;font-size:14px}.legend i{display:inline-block;width:18px;height:10px;margin-right:5px}.scroll{overflow:auto}table{width:100%;border-collapse:collapse;white-space:nowrap}th,td{text-align:left;padding:12px;border-bottom:1px solid var(--line);vertical-align:top}th{font-size:14px;color:var(--muted)}td:last-child{white-space:normal;min-width:160px}td .bar{min-width:110px}a{color:var(--accent)}details{margin:10px 0;scroll-margin-top:20px}summary{cursor:pointer;font-weight:600}.inside{padding-top:12px}.active{background:color-mix(in srgb,var(--accent) 13%,transparent)}.inside p{margin:8px 0}.sources li{overflow-wrap:anywhere;margin:12px 0}button{font:inherit;padding:8px 16px;background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:8px;cursor:pointer}@media(prefers-color-scheme:dark){:root{--bg:#11191f;--panel:#1a2630;--ink:#e5edf3;--muted:#a9b8c5;--line:#354550;--accent:#62c7ba;--track:#344651;--unknown:#72818d}}@media(max-width:900px){.cards{grid-template-columns:repeat(2,1fr)}header{align-items:start}main{padding:20px 12px}}@media print{button{display:none}main{padding:0}.scroll{overflow:visible}table{white-space:normal;font-size:11px}.cards{grid-template-columns:repeat(5,1fr)}details{break-inside:avoid}}
'''
    doc=f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)}</title><style>{css}</style></head><body><main>
<header><div><span class="sub">价值投资研究 · 模型 {e(result['version'])}</span><h1>{e(title)}</h1><p class="sub">{e(result['dates'][0])} — {e(result['dates'][-1])} · 离线分析报告</p></div><button onclick="document.querySelectorAll('details').forEach(x=>x.open=true);window.print()">打印 / 保存为 PDF</button></header>
<section class="panel overview"><span>已取得分数 / 模型满分</span><p class="big">{num(score)} <small>/ {num(maximum)} 分</small></p>{bar(score,maximum,covered)}<div class="facts"><span>已覆盖权重 <b>{num(covered)} / {num(maximum)}</b></span><span>数据覆盖率 <b>{covered/maximum*100:.2f}%</b></span><span>未评 <b>{num(maximum-covered)} 分</b></span></div><p>{e(result['status'])}</p><p class="sub">未评分不等于零分，不重新放大已评指标。未知部分全部评分后，总分可能在 {num(result['possible_range'][0])}～{num(result['possible_range'][1])} 分之间；这个区间不是预测或置信区间。</p><div class="legend"><span><i class="earned"></i>已取得</span><span><i class="lost"></i>已评价但未取得</span><span><i class="unknown"></i>资料不足，未评</span></div></section>
<h2>五个模块 · 得分与满分</h2><div class="cards">{cards}</div>
<h2>十项指标 · 权重、满分和实际得分</h2><div class="panel scroll"><table><thead><tr><th>指标</th><th>权重 / 满分</th><th>实际数值</th><th>等级</th><th>得分 / 满分</th><th>状态或缺失原因</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<h2>评分细则 · 点击逐项展开</h2><p class="sub">每项得分 = 等级 ÷ 5 × 权重。等级满分为 5；加权满分为该项权重。</p>{''.join(details)}
<h2>计算所用年度数据</h2><p class="sub">金额沿用原报表单位及币种，每股数据和股数除外。未提供值不补零；补充值及配置快照见评分结果.json。资产与权益回报率需要六个年末余额；期初余额见输入财报。</p><div class="panel scroll"><table>{annual}</table></div>
<h2>模型限制与风险提醒</h2><div class="panel"><ul>{warnings}</ul></div><h2>输入文件与可追溯记录</h2><ul class="sources">{sources}</ul><p class="sub">质量研究辅助工具，不含估值、人工研究或买卖指令。权重和阈值见本次报告的评分结果.json。</p>
</main></body></html>'''
    Path(path).write_text(doc,encoding='utf-8')
    return Path(path)

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description='将评分结果JSON转换为可视化报告')
    p.add_argument('result',type=Path);a=p.parse_args()
    path=build_report(json.loads(a.result.read_text(encoding='utf-8-sig')),a.result.with_name('可视化评分报告.html'))
    print(path)

"""Friendly entry point. Source snapshots and batch work stay resumable."""
from pathlib import Path
from datetime import datetime
import json
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parent

def main():
    print('A股全市场研究\n1 获取全市场数据并分析\n2 重算已有市场快照（不重新联网）\n3 对初筛候选批量下载财报并做80分分析\n4 对全市场一般企业批量下载财报并分析（耗时较长）')
    choice=input('请选择1/2/3/4：').strip()
    if choice=='1':
        period=input('业绩报告期YYYYMMDD；回车使用上一年度1231：').strip()
        cmd=[sys.executable,'-B',str(ROOT/'a_market.py'),'--open-report']
        if period:cmd+=['--period',period]
        subprocess.run(cmd)
    elif choice=='2':
        snapshots=sorted(p for p in (ROOT/'data'/'a_market').glob('*') if p.is_dir() and all((p/f'{s}.json').exists() for s in ('universe','quotes','financial')))
        if not snapshots:raise ValueError('没有完整快照，请先选择1')
        snapshots.sort(key=lambda p:json.loads((p/'quotes.json').read_text(encoding='utf-8-sig'))['completed_at'])
        snapshot=snapshots[-1]
        metadata=json.loads((snapshot/'financial.json').read_text(encoding='utf-8-sig'))
        subprocess.run([sys.executable,'-B',str(ROOT/'a_market.py'),'--offline',str(snapshot),'--period',metadata['statement_period'],'--open-report'])
    elif choice in ('3','4'):
        paths=sorted((ROOT/'分析结果'/'A股全市场').glob('*/分析数据.json'))
        if not paths:raise ValueError('请先选择1生成市场分析结果')
        rows=json.loads(paths[-1].read_text(encoding='utf-8-sig'))
        # A snapshot cannot establish business type for missing industry rows.
        def general(r):
            return r['industry'] and not any(x in r['industry'] for x in ('银行','保险','证券','多元金融','房地产'))
        selected=[r for r in rows if (r['candidate'] if choice=='3' else general(r))]
        print(f'队列共有{len(selected)}家公司。将依次请求三大财报；单家公司可能需要数分钟。顺序不是推荐排名。')
        count=input('本次最多处理多少家公司？默认10；输入all处理整队列：').strip() or '10'
        limit=len(selected) if count=='all' else int(count)
        if limit<1:raise ValueError('数量必须为正')
        done_dir=ROOT/'分析结果'/'批量任务'
        done_dir.mkdir(parents=True,exist_ok=True)
        task=done_dir/datetime.now().strftime('%Y%m%d_%H%M%S_%f.jsonl')
        processed=0
        for row in selected:
            code=row['code'];folder=ROOT/'data'/f'a_{code}'
            files={kind:sorted(folder.glob(kind+'_*.csv')) for kind in ('income','balance','cashflow')}
            local=sorted((ROOT/'分析结果'/f'a_{code}').glob('*/评分结果.json'))
            already=False
            if local and all(files.values()):
                try:
                    prior=json.loads(local[-1].read_text(encoding='utf-8-sig'))
                    import hashlib
                    digests={hashlib.sha256(ps[-1].read_bytes()).hexdigest() for ps in files.values()}
                    already=prior['version']=='2.0.0-basic' and digests=={x['sha256'] for x in prior['inputs']} and prior['config']==json.loads((ROOT/'score_config.json').read_text(encoding='utf-8-sig'))
                except (OSError,ValueError,KeyError):pass
            if already:continue
            if processed>=limit:break
            processed+=1
            print(f'[{processed}/{limit}] {code} {row["name"]}',flush=True)
            state=dict(code=code,name=row['name'],status='failed')
            try:
                if not all(files.values()):
                    result=subprocess.run([sys.executable,'-B',str(ROOT/'fetch_financials.py'),'--market','a','--symbol',code],timeout=210)
                    if result.returncode:raise ValueError('财报下载不完整')
                result=subprocess.run([sys.executable,'-B',str(ROOT/'financial_score.py'),'--market','a','--symbol',code],timeout=60)
                if result.returncode:raise ValueError('财务分析失败，见上方提示')
                state['status']='analyzed'
            except (ValueError,OSError,subprocess.TimeoutExpired) as exc:state['error']=str(exc)
            with task.open('a',encoding='utf-8') as f:f.write(json.dumps(state,ensure_ascii=False)+'\n')
            time.sleep(1)
        print(f'本次处理{processed}家公司。任务记录：{task}')
        print('已有财报默认复用，需更新时请先用下载模块重新下载。再次运行会跳过输入相同且已用v2分析的公司。')
        print('回到选项2，可把本地财务分关联到市场看板。')
    else:raise ValueError('请选择1/2/3/4')

if __name__=='__main__':
    try:main()
    except (ValueError,OSError,KeyboardInterrupt) as exc:print('操作停止：'+str(exc))
    input('按回车退出。')

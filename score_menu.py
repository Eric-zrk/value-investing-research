from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parent
print('财务自动分析（80分制）\n适用于一般非金融企业；银行、保险、证券、地产请勿套用。')
folders=sorted(p for p in (ROOT/'data').glob('*') if p.is_dir() and p.name.startswith(('a_','hk_')))
if not folders:
    input('未找到财报，请先用 start.cmd 下载。按回车退出。')
    sys.exit(1)
for n,p in enumerate(folders,1): print(f'{n}. {p.name}')
try:
    choice=input('输入序号分析单家公司；输入 all 分析全部一般企业：').strip()
    selected=folders if choice=='all' else [folders[int(choice)-1]]
    if choice!='all' and not 1<=int(choice)<=len(folders): raise ValueError()
except (ValueError,IndexError):
    input('输入无效。按回车退出。');sys.exit(1)
failed=[]
for folder in selected:
    market,code=folder.name.split('_',1)
    cmd=[sys.executable,'-B',str(ROOT/'financial_score.py'),'--market',market,'--symbol',code,'--open-report']
    supplement=folder/'financial_inputs.json'
    if supplement.exists(): cmd+=['--supplement',str(supplement)]
    if subprocess.run(cmd).returncode: failed.append(folder.name)
print('结果保存在项目内部“分析结果”文件夹。')
if failed: print('未完成：'+', '.join(failed)+'。请查看上方错误，不会使用虚构数据补齐。')
input('按回车退出。')

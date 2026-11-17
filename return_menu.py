from pathlib import Path
import subprocess,sys
root=Path(__file__).resolve().parent
code=input('输入六位A股代码，分析持续分红、持续盈利和现金创造：').strip()
subprocess.run([sys.executable,'-B',str(root/'shareholder_return.py'),'--symbol',code,'--open-report'])
input('按回车退出。')

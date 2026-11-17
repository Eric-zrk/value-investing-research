$ErrorActionPreference = 'Stop'
try {
    $env:PYTHONUTF8 = '1'
    $venv = Join-Path $PSScriptRoot '.venv'
    $python = Join-Path $venv 'Scripts\python.exe'
    py -3.13 -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw '请先在当前电脑安装 Python 3.13，然后重新运行 setup.cmd。' }
    & $python -B -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0) { throw 'pip 初始化失败。' }
    & $python -B -m pip --isolated install --no-cache-dir --disable-pip-version-check --index-url https://pypi.org/simple --timeout 30 --retries 2 -r (Join-Path $PSScriptRoot 'requirements-lock.txt')
    if ($LASTEXITCODE -ne 0) { throw '依赖安装失败，请检查网络后重试。' }
    & $python -B -m pip check
    if ($LASTEXITCODE -ne 0) { throw '依赖检查失败。' }
    & $python -B -c 'import akshare; print(akshare.__version__)'
    if ($LASTEXITCODE -ne 0) { throw 'AKShare 无法加载。' }
    & $python -B (Join-Path $PSScriptRoot 'fetch_financials.py') --help
    if ($LASTEXITCODE -ne 0) { throw '程序启动检查失败。' }
    Write-Host '环境配置完成。双击 start.cmd 下载财报。' -ForegroundColor Green
} catch {
    Write-Host ($_ | Out-String) -ForegroundColor Red
    exit 1
}

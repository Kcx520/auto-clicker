# 把连点器打包成免安装文件夹 dist\连点器
# 用法：pwsh -File tools\build_portable.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$src  = "C:\Python312"   # ← 改成你自己的 Python 安装目录（该目录下要有 python.exe）
if (-not (Test-Path (Join-Path $src "python.exe"))) {
    throw '找不到 Python：请把本文件第 6 行的 $src 改成你自己的 Python 安装目录（要有 python.exe）'
}
$dist = Join-Path $root "dist\连点器"
$runtime = Join-Path $dist "runtime"

Write-Output "项目根目录：$root"
Write-Output "目标目录：$dist"

if (Test-Path $dist) { Remove-Item $dist -Recurse -Force }
New-Item -ItemType Directory -Force -Path $dist | Out-Null

# ---------- 1. Python 运行时 ----------
Write-Output "`n[1/4] 复制 Python 运行时…"
$excludeDirs = @("test", "tests", "idlelib", "lib2to3", "ensurepip", "turtledemo",
                 "__pycache__", "include", "libs", "Scripts", "site-packages", "tkinter\test")
$rcArgs = @($src, $runtime, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/R:1", "/W:1")
foreach ($d in $excludeDirs) { $rcArgs += "/XD"; $rcArgs += (Join-Path $src $d) }
& robocopy @rcArgs | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy 失败，代码 $LASTEXITCODE" }

# 只要用得到的第三方库
$keep = @("numpy", "numpy.libs", "numpy-2.3.5.dist-info", "PIL", "pillow-12.3.0.dist-info")
foreach ($name in $keep) {
    $from = Join-Path $src "Lib\site-packages\$name"
    if (Test-Path $from) {
        & robocopy $from (Join-Path $runtime "Lib\site-packages\$name") /E /NFL /NDL /NJH /NJS /NP /R:1 /W:1 | Out-Null
    }
}

# ---------- 2. 程序文件 ----------
Write-Output "[2/4] 复制程序文件…"
foreach ($item in @("app", "tools", "assets", "main.py")) {
    $from = Join-Path $root $item
    if (Test-Path $from) {
        Copy-Item $from -Destination $dist -Recurse -Force
    }
}
# 清掉源码里的缓存
Get-ChildItem $dist -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# 空的配置目录（首次运行会自动生成内容）
New-Item -ItemType Directory -Force -Path (Join-Path $dist "config") | Out-Null

# ---------- 3. 启动器 ----------
Write-Output "[3/4] 编译启动器 exe…"
$csc = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$launcherCs = Join-Path $root "tools\launcher.cs"
$icon = Join-Path $root "assets\icon.ico"
$exe = Join-Path $dist "连点器.exe"
if (Test-Path $csc) {
    $cscArgs = @("/nologo", "/target:winexe", "/optimize+", "/r:System.dll",
                 "/out:$exe")
    if (Test-Path $icon) { $cscArgs += "/win32icon:$icon" }
    $cscArgs += $launcherCs
    & $csc @cscArgs
    if ($LASTEXITCODE -ne 0) { Write-Output "  编译失败（代码 $LASTEXITCODE），改用 bat 启动" }
    else { Write-Output "  已生成 连点器.exe" }
} else {
    Write-Output "  找不到 csc.exe，跳过 exe，使用 bat 启动"
}

# ---------- 4. 说明文件 ----------
Write-Output "[4/4] 生成并复制说明文件…"
$pyForDocs = if (Test-Path (Join-Path $dist "runtime\python.exe")) { Join-Path $dist "runtime\python.exe" } else { Join-Path $src "python.exe" }
if (Test-Path (Join-Path $root "使用说明.md")) {
    & $pyForDocs (Join-Path $root "tools\md_to_html.py") 2>&1 | Out-Null
}
if (Test-Path (Join-Path $root "docs")) {
    Copy-Item (Join-Path $root "docs") -Destination $dist -Recurse -Force
}
foreach ($doc in @("使用说明.md", "使用说明.html", "README.md", "连点器-功能设计.md")) {
    $from = Join-Path $root $doc
    if (Test-Path $from) { Copy-Item $from -Destination $dist -Force }
}
foreach ($bat in @("启动连点器.bat", "启动连点器-显示错误信息.bat")) {
    $from = Join-Path $root $bat
    if (Test-Path $from) { Copy-Item $from -Destination $dist -Force }
}

$size = (Get-ChildItem $dist -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Output "`n打包完成：$dist"
Write-Output ("总大小：{0:N1} MB" -f $size)

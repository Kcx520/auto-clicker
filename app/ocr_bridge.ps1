<#
    ocr_bridge.ps1 -- 常驻 OCR 桥 (Windows.Media.Ocr)

    由 Python 端 app/ocr.py 的 WindowsOcrEngine 启动:
        powershell.exe -NoProfile -ExecutionPolicy Bypass -File app/ocr_bridge.ps1

    协议: 一行一个 UTF-8 JSON
        Python -> PS : {"id":1,"image":"<PNG base64>","lang":"zh-Hans-CN"}
        PS -> Python : {"id":1,"ok":true,"text":"...","lines":[{text,x,y,w,h},...]}
        PS -> Python : {"id":1,"ok":false,"error":"..."}
    每处理完一条必须 flush; stdin EOF 时退出。
    注意: stdout 只允许输出 JSON 行, 调试信息一律走 stderr。
    注意: 本文件必须以 UTF-8 with BOM 保存, 否则 PowerShell 5.1 会把中文读成乱码。
    注意: 本文件中所有语句必须写成单行, PowerShell 5.1 不支持跨行的小括号方法调用。
#>

Set-StrictMode -Off
$ErrorActionPreference = "Stop"

# ---------- 编码: 必须最先设置, 否则中文乱码 ----------
$utf8 = New-Object System.Text.UTF8Encoding($false)
try { [Console]::OutputEncoding = $utf8 } catch { }
try { [Console]::InputEncoding = $utf8 } catch { }
try {
    $script:stdout = New-Object System.IO.StreamWriter([Console]::OpenStandardOutput(), $utf8)
    $script:stdout.AutoFlush = $false
} catch {
    $script:stdout = $null
}

# ---------- 加载 WinRT 类型 ----------
$winrtReady = $true
$winrtError = ""
try {
    [void][Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
    [void][Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
    [void][Windows.Graphics.Imaging.SoftwareBitmap, Windows.Foundation, ContentType = WindowsRuntime]
    [void][Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Foundation, ContentType = WindowsRuntime]
    [void][Windows.Storage.Streams.DataWriter, Windows.Foundation, ContentType = WindowsRuntime]
    [void][Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]
} catch {
    $winrtReady = $false
    $winrtError = "WinRT load failed: " + $_.Exception.Message
}

# ---------- 同步等待 WinRT 异步操作 ----------
# PS 5.1 不自动补 WindowsRuntimeSystemExtensions 的 AsTask 扩展方法: 先显式加载程序集, 再反射调用。
# 两组重载: AsTask(IAsyncAction) 处理返回 void 的操作; AsTask<TResult>(IAsyncOperation<TResult>) 取结果。
$script:AsTaskActionDef = $null
$script:AsTaskOperationDef = $null
foreach ($asmName in @("System.Runtime.WindowsRuntime, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089", "System.Runtime.WindowsRuntime")) {
    if ($script:AsTaskOperationDef -ne $null) { break }
    try {
        $asm = [System.Reflection.Assembly]::Load($asmName)
        $methods = $asm.GetType("System.WindowsRuntimeSystemExtensions").GetMethods()
        $script:AsTaskOperationDef = $methods | Where-Object { $_.Name -eq "AsTask" -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 } | Select-Object -First 1
        $script:AsTaskActionDef = $methods | Where-Object { $_.Name -eq "AsTask" -and (-not $_.IsGenericMethod) -and $_.GetParameters().Count -eq 1 } | Select-Object -First 1
    } catch {
        $script:AsTaskActionDef = $null
        $script:AsTaskOperationDef = $null
    }
}

function Unwrap($value) {
    # PowerShell 会把 COM/WinRT 对象包一层 PSObject, 反射调用前必须剥掉, 否则类型转换失败
    if ($null -eq $value) { return $null }
    if ($value -is [System.Management.Automation.PSObject]) { return $value.PSObject.BaseObject }
    return $value
}

function Await($op, $resultType) {
    if ($null -eq $op) { return $null }
    $op = Unwrap $op
    if ($resultType -ne $null -and $op -is $resultType) { return $op }
    if ($null -eq $resultType) {
        # 返回 void 的 WinRT 异步操作 (IAsyncAction)
        if ($script:AsTaskActionDef -eq $null) { throw "AsTask(IAsyncAction) not available" }
        $voidTask = $script:AsTaskActionDef.Invoke($null, @($op))
        $voidTask.Wait(-1) | Out-Null
        return $null
    }
    if ($script:AsTaskOperationDef -eq $null) {
        throw "WindowsRuntimeSystemExtensions.AsTask<T> not available (cannot await WinRT operation)"
    }
    $task = $script:AsTaskOperationDef.MakeGenericMethod($resultType).Invoke($null, @($op))
    $task.Wait(-1) | Out-Null
    return $task.Result
}

# ---------- 输出 ----------
function Write-Line([string]$text) {
    if ($script:stdout -ne $null) {
        $script:stdout.WriteLine($text)
        $script:stdout.Flush()
    } else {
        [Console]::Out.WriteLine($text)
        [Console]::Out.Flush()
    }
}

function Write-Payload($obj) {
    Write-Line ($obj | ConvertTo-Json -Depth 6 -Compress)
}

# ---------- 引擎缓存 ----------
$script:engine = $null
$script:engineKey = ""

function Get-OcrEngine([string]$lang) {
    $key = "$lang"
    if ($script:engine -ne $null -and $script:engineKey -eq $key) {
        return $script:engine
    }

    $engine = $null
    if (-not [string]::IsNullOrWhiteSpace($lang)) {
        try {
            $language = New-Object Windows.Globalization.Language($lang)
            $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
        } catch {
            $engine = $null
        }
    }
    if ($null -eq $engine) {
        try { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() } catch { $engine = $null }
    }
    if ($null -eq $engine) {
        try {
            $fallback = New-Object Windows.Globalization.Language("zh-Hans-CN")
            $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($fallback)
        } catch {
            $engine = $null
        }
    }
    if ($null -eq $engine) {
        throw "cannot create OCR engine (no recognizer language installed)"
    }

    $script:engine = $engine
    $script:engineKey = $key
    return $engine
}

# ---------- 词拼接: 中文之间不要空格 ----------
function Test-Ascii([int]$code) {
    return (($code -ge 0x30 -and $code -le 0x39) -or ($code -ge 0x41 -and $code -le 0x5A) -or ($code -ge 0x61 -and $code -le 0x7A))
}

function Join-Words($words) {
    $sb = New-Object System.Text.StringBuilder
    $prevAscii = $false
    $started = $false
    foreach ($w in $words) {
        $t = $w.Text
        if ([string]::IsNullOrEmpty($t)) { continue }
        $headAscii = Test-Ascii ([int][char]$t[0])
        $tailAscii = Test-Ascii ([int][char]$t[$t.Length - 1])
        # 规则: 上一词以非 ASCII 结尾 且 本词以非 ASCII 开头 -> 不插空格; 否则插一个空格
        if ($started -and -not ((-not $prevAscii) -and (-not $headAscii))) {
            [void]$sb.Append(" ")
        }
        [void]$sb.Append($t)
        $prevAscii = $tailAscii
        $started = $true
    }
    return $sb.ToString()
}

# ---------- 图片字节 (PNG / BMP) -> SoftwareBitmap ----------
function Convert-ImageToBitmap([string]$base64) {
    $bytes = [Convert]::FromBase64String($base64)
    $stream = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
    $writer = New-Object Windows.Storage.Streams.DataWriter($stream)
    $writer.WriteBytes($bytes)
    # StoreAsync 返回 IAsyncOperation<uint32>, 会把 DataWriter 缓冲区全部刷入流。
    # 这里不调用 FlushAsync: 它投影成裸 __ComObject 无法按 IAsyncAction 等待, 而且完全多余。
    Await ($writer.StoreAsync()) ([uint32]) | Out-Null
    $writer.DetachStream() | Out-Null
    $stream.Seek(0)
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])

    # 优先 Bgra8 + 忽略 alpha: OCR 只关心亮度, 忽略 alpha 可避免 BMP/PNG 缺 alpha 时整图变透明
    $bitmap = $null
    try {
        $bitmap = Await ($decoder.GetSoftwareBitmapAsync([Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8, [Windows.Graphics.Imaging.BitmapAlphaMode]::Ignore)) ([Windows.Graphics.Imaging.SoftwareBitmap])
    } catch {
        $bitmap = $null
    }
    if ($null -eq $bitmap) {
        $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    }
    # SoftwareBitmap 有时是裸 __ComObject, 明确转成 WinRT 类型再交给 RecognizeAsync
    return [Windows.Graphics.Imaging.SoftwareBitmap](Unwrap $bitmap)
}

function Invoke-Ocr([string]$base64, [string]$lang) {
    $engine = Get-OcrEngine $lang
    $bitmap = Convert-ImageToBitmap $base64
    $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

    $lines = New-Object System.Collections.ArrayList
    foreach ($line in $result.Lines) {
        $lineText = Join-Words $line.Words
        if ([string]::IsNullOrEmpty($lineText)) { continue }

        $minX = [double]::MaxValue
        $minY = [double]::MaxValue
        $maxX = [double]::MinValue
        $maxY = [double]::MinValue
        foreach ($word in $line.Words) {
            $rect = $word.BoundingRect
            if ($rect.X -lt $minX) { $minX = $rect.X }
            if ($rect.Y -lt $minY) { $minY = $rect.Y }
            if (($rect.X + $rect.Width) -gt $maxX) { $maxX = $rect.X + $rect.Width }
            if (($rect.Y + $rect.Height) -gt $maxY) { $maxY = $rect.Y + $rect.Height }
        }
        if ($minX -eq [double]::MaxValue) { $minX = 0; $minY = 0; $maxX = 0; $maxY = 0 }

        $left = [int][Math]::Floor($minX)
        $top = [int][Math]::Floor($minY)
        $width = [int][Math]::Floor($maxX) - $left
        $height = [int][Math]::Floor($maxY) - $top
        if ($width -lt 0) { $width = 0 }
        if ($height -lt 0) { $height = 0 }

        [void]$lines.Add([pscustomobject]@{ text = $lineText; x = $left; y = $top; w = $width; h = $height })
    }

    if ($null -ne $bitmap) { try { $bitmap.Dispose() } catch { } }

    $all = New-Object System.Collections.ArrayList
    foreach ($item in $lines) { [void]$all.Add($item.text) }

    return [pscustomobject]@{ text = ($all -join "`n"); lines = @($lines.ToArray()) }
}

# ---------- 主循环 ----------
if (-not $winrtReady) {
    Write-Payload ([pscustomobject]@{ id = 0; ok = $false; ready = $false; error = $winrtError })
    exit 0
}

# 握手: 让 Python 端在探测阶段就能确认桥真的可用（而不只是「进程起来了」）。
# 这里顺便把 OCR 引擎先建好，引擎都建不起来就明确报告失败。
try {
    $probeEngine = Get-OcrEngine "zh-Hans-CN"
    Write-Payload ([pscustomobject]@{
        id = 0
        ok = $true
        ready = $true
        lang = $probeEngine.RecognizerLanguage.LanguageTag
    })
} catch {
    $probeMsg = $_.Exception.Message
    if ([string]::IsNullOrEmpty($probeMsg)) { $probeMsg = "OCR engine unavailable" }
    Write-Payload ([pscustomobject]@{ id = 0; ok = $false; ready = $false; error = $probeMsg })
    exit 0
}

# 用明确的 StreamReader + UTF-8 读 stdin。
# 不用 [Console]::In: 它在 stdin 被重定向/管道时可能抛 "One or more errors occurred"，
# 也可能按 OEM 代码页解码。$false 表示不检测 BOM（我们发的是无 BOM 的纯 JSON 行）。
$stdin = New-Object System.IO.StreamReader([Console]::OpenStandardInput(), $utf8, $false)
while ($true) {
    $line = $null
    try { $line = $stdin.ReadLine() } catch { break }
    if ($null -eq $line) { break }          # EOF
    if ($line.Length -eq 0) { continue }

    $id = 0
    try {
        $req = $line | ConvertFrom-Json
        $id = [int]$req.id
        $lang = "zh-Hans-CN"
        if ($null -ne $req.lang -and "$($req.lang)".Length -gt 0) { $lang = "$($req.lang)" }
        $out = Invoke-Ocr ([string]$req.image) $lang
        Write-Payload ([pscustomobject]@{ id = $id; ok = $true; text = $out.text; lines = $out.lines })
    } catch {
        $msg = $_.Exception.Message
        if ([string]::IsNullOrEmpty($msg)) { $msg = "unknown error" }
        Write-Payload ([pscustomobject]@{ id = $id; ok = $false; error = $msg })
    }
}
exit 0

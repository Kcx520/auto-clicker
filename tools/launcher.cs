// 连点器启动器：用自带的 pythonw.exe 打开 app\main.py，不弹黑框。
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;

internal static class Launcher
{
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int MessageBoxW(IntPtr hWnd, string text, string caption, uint type);

    [STAThread]
    private static int Main()
    {
        string baseDir = AppDomain.CurrentDomain.BaseDirectory;
        string script = Path.Combine(baseDir, "main.py");
        string[] candidates =
        {
            Path.Combine(baseDir, "runtime", "pythonw.exe"),
            Path.Combine(baseDir, "runtime", "python.exe"),
        };

        string python = null;
        foreach (string candidate in candidates)
        {
            if (File.Exists(candidate)) { python = candidate; break; }
        }

        if (python == null || !File.Exists(script))
        {
            MessageBoxW(IntPtr.Zero,
                "找不到运行文件，请确认「连点器」整个文件夹是完整的：\r\n\r\n" +
                "缺少 " + (python == null ? "runtime\\pythonw.exe" : "main.py") +
                "\r\n\r\n当前目录：" + baseDir,
                "连点器", 0x10);
            return 1;
        }

        var info = new ProcessStartInfo(python, "\"" + script + "\"");
        info.WorkingDirectory = baseDir;
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        try
        {
            Process.Start(info);
            return 0;
        }
        catch (Exception ex)
        {
            MessageBoxW(IntPtr.Zero, "启动失败：" + ex.Message, "连点器", 0x10);
            return 1;
        }
    }
}

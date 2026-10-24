using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;
using Microsoft.Win32;

// WhisperTranscriber 轻量启动器。
// 只做一件事：找到本机 pythonw.exe，以项目目录为工作目录运行 main.py。
// 不打包 Python / 依赖，所以体积只有几十 KB，启动也快。
internal static class Program
{
    [STAThread]
    private static void Main()
    {
        string appDir = Path.GetDirectoryName(Application.ExecutablePath);
        string mainPy = Path.Combine(appDir, "main.py");
        DebugLog("start appDir=" + appDir);
        if (!File.Exists(mainPy))
        {
            ShowError("找不到 main.py。\n\n请把 WhisperTranscriber.exe 放在项目根目录（与 main.py 同级）。");
            return;
        }

        List<string> candidates = FindPythonwCandidates(appDir);
        DebugLog("candidates=" + candidates.Count + " [" + string.Join("; ", candidates.ToArray()) + "]");
        if (candidates.Count == 0)
        {
            ShowError("未找到 Python。\n\n请先安装 Python 3.10（安装时勾选 Add Python to PATH），\n或设置环境变量 WHISPER_PYTHON 指向 pythonw.exe。");
            return;
        }

        string chosen = null;
        foreach (string candidate in candidates)
        {
            string pythonExe = Path.Combine(Path.GetDirectoryName(candidate), "python.exe");
            if (!File.Exists(pythonExe))
            {
                pythonExe = candidate;
            }
            bool hasDeps = HasDependencies(pythonExe);
            DebugLog("check " + pythonExe + " deps=" + hasDeps);
            if (hasDeps)
            {
                chosen = candidate;
                break;
            }
        }
        if (chosen == null)
        {
            // 没找到装好依赖的解释器：仍然启动，由 main.py 负责安装依赖
            chosen = candidates[0];
        }

        try
        {
            ProcessStartInfo info = new ProcessStartInfo();
            info.FileName = chosen;
            info.Arguments = BuildArguments(mainPy);
            info.WorkingDirectory = appDir;
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            Process child = Process.Start(info);
            DebugLog("launched pid=" + (child == null ? "null" : child.Id.ToString()) + " exe=" + chosen);
        }
        catch (Exception ex)
        {
            DebugLog("launch failed: " + ex.ToString());
            ShowError("启动失败：" + ex.Message);
        }
    }

    private static string BuildArguments(string mainPy)
    {
        string args = "\"" + mainPy + "\"";
        string[] commandLine = Environment.GetCommandLineArgs();
        for (int i = 1; i < commandLine.Length; i++)
        {
            args += " \"" + commandLine[i] + "\"";
        }
        return args;
    }

    private static bool HasDependencies(string pythonExe)
    {
        try
        {
            ProcessStartInfo info = new ProcessStartInfo();
            info.FileName = pythonExe;
            info.Arguments = "-c \"import PyQt5, faster_whisper, modelscope\"";
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = true;
            using (Process process = Process.Start(info))
            {
                process.StandardOutput.ReadToEnd();
                process.StandardError.ReadToEnd();
                if (!process.WaitForExit(30000))
                {
                    try { process.Kill(); } catch { }
                    return false;
                }
                return process.ExitCode == 0;
            }
        }
        catch
        {
            return false;
        }
    }

    private static List<string> FindPythonwCandidates(string appDir)
    {
        List<string> result = new List<string>();

        AddIfFile(result, Path.Combine(appDir, @".venv\Scripts\pythonw.exe"));
        AddIfFile(result, Path.Combine(appDir, @"venv\Scripts\pythonw.exe"));
        AddIfFile(result, Path.Combine(appDir, @"runtime\pythonw.exe"));

        string custom = Environment.GetEnvironmentVariable("WHISPER_PYTHON");
        if (!string.IsNullOrEmpty(custom))
        {
            AddIfFile(result, custom);
            AddIfFile(result, Path.Combine(custom, "pythonw.exe"));
        }

        foreach (string path in FromRegistry())
        {
            AddIfFile(result, path);
        }

        string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        AddGlob(result, Path.Combine(localAppData, "Programs", "Python"));
        AddGlob(result, Environment.GetEnvironmentVariable("ProgramFiles"));
        AddGlob(result, Environment.GetEnvironmentVariable("ProgramFiles(x86)"));
        AddGlob(result, @"C:\");

        string pathVariable = Environment.GetEnvironmentVariable("PATH");
        if (!string.IsNullOrEmpty(pathVariable))
        {
            foreach (string dir in pathVariable.Split(';'))
            {
                if (string.IsNullOrEmpty(dir)) continue;
                AddIfFile(result, Path.Combine(dir.Trim(), "pythonw.exe"));
            }
        }

        return result;
    }

    private static IEnumerable<string> FromRegistry()
    {
        List<string> result = new List<string>();
        RegistryKey[] hives = new RegistryKey[] { Registry.CurrentUser, Registry.LocalMachine };
        string[] subKeys = new string[] { @"SOFTWARE\Python\PythonCore", @"SOFTWARE\WOW6432Node\Python\PythonCore" };
        foreach (RegistryKey hive in hives)
        {
            foreach (string subKey in subKeys)
            {
                try
                {
                    using (RegistryKey core = hive.OpenSubKey(subKey))
                    {
                        if (core == null) continue;
                        List<string> versions = new List<string>(core.GetSubKeyNames());
                        versions.Sort();
                        versions.Reverse();
                        foreach (string version in versions)
                        {
                            using (RegistryKey versionKey = core.OpenSubKey(version))
                            {
                                if (versionKey == null) continue;
                                using (RegistryKey install = versionKey.OpenSubKey("InstallPath"))
                                {
                                    if (install == null) continue;
                                    string directory = install.GetValue(null) as string;
                                    if (!string.IsNullOrEmpty(directory))
                                    {
                                        string exe = Path.Combine(directory, "pythonw.exe");
                                        if (File.Exists(exe)) result.Add(exe);
                                    }
                                }
                            }
                        }
                    }
                }
                catch
                {
                    // 注册表读取失败不影响继续尝试其它位置
                }
            }
        }
        return result;
    }

    private static void AddGlob(List<string> result, string baseDirectory)
    {
        if (string.IsNullOrEmpty(baseDirectory) || !Directory.Exists(baseDirectory)) return;
        string[] directories;
        try
        {
            directories = Directory.GetDirectories(baseDirectory, "Python3*");
        }
        catch
        {
            return;
        }
        Array.Sort(directories);
        Array.Reverse(directories);
        foreach (string directory in directories)
        {
            AddIfFile(result, Path.Combine(directory, "pythonw.exe"));
        }
    }

    private static void AddIfFile(List<string> result, string path)
    {
        if (string.IsNullOrEmpty(path)) return;
        string full;
        try
        {
            full = Path.GetFullPath(path);
        }
        catch
        {
            return;
        }
        if (!File.Exists(full)) return;
        foreach (string item in result)
        {
            if (string.Equals(item, full, StringComparison.OrdinalIgnoreCase)) return;
        }
        result.Add(full);
    }

    private static void DebugLog(string message)
    {
        if (Environment.GetEnvironmentVariable("WHISPER_LAUNCHER_DEBUG") != "1")
        {
            return;
        }
        try
        {
            File.AppendAllText(
                Path.Combine(Path.GetTempPath(), "whisper_launcher.log"),
                DateTime.Now.ToString("HH:mm:ss") + " " + message + Environment.NewLine);
        }
        catch
        {
            // 调试日志写不了也无所谓
        }
    }

    private static void ShowError(string message)
    {
        DebugLog("error: " + message);
        MessageBox.Show(message, "WhisperTranscriber", MessageBoxButtons.OK, MessageBoxIcon.Error);
    }
}

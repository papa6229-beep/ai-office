using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

internal static class InspectorProgram
{
    internal static readonly string Output = AppDomain.CurrentDomain.BaseDirectory;
    internal static readonly string Root = Path.GetFullPath(Path.Combine(Output, "..", "..", ".."));
    internal const string Home = "http://127.0.0.1:8876/";
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr window, int command);
    [STAThread] static void Main()
    {
        Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
        bool first;
        using (var singleton = new Mutex(true, "Local\\Aemeath.Bizhost.Inspector.Window", out first))
        {
            if (!first)
            {
                foreach (var process in Process.GetProcessesByName("BizhostInspector"))
                    if (process.Id != Process.GetCurrentProcess().Id && process.MainWindowHandle != IntPtr.Zero)
                    { ShowWindow(process.MainWindowHandle, 9); SetForegroundWindow(process.MainWindowHandle); break; }
                return;
            }
            Application.Run(new InspectorWindow());
        }
    }
    internal static int Health()
    {
        try
        {
            var req = (HttpWebRequest)WebRequest.Create(Home + "health");
            req.Proxy = null; req.Timeout = 1200; req.ReadWriteTimeout = 1200; req.AllowAutoRedirect = false;
            using (var response = req.GetResponse())
            using (var reader = new StreamReader(response.GetResponseStream()))
                return reader.ReadToEnd().Contains("aemeath-bizhost-inspector-v1") ? 1 : 2;
        }
        catch (WebException e) { if (e.Response != null) { e.Response.Close(); return 2; } return 0; }
    }
    internal static bool Local(string address)
    {
        Uri uri;
        return Uri.TryCreate(address, UriKind.Absolute, out uri) && uri.Scheme == "http" && uri.Host == "127.0.0.1" && uri.Port == 8876 && String.IsNullOrEmpty(uri.UserInfo);
    }
}

internal sealed class InspectorWindow : Form
{
    readonly WebView2 view = new WebView2();
    readonly Label status = new Label();
    public InspectorWindow()
    {
        Text = "에이메스 · 비즈호스트 상품 검수"; Width = 1440; Height = 980; MinimumSize = new Size(1000, 730);
        StartPosition = FormStartPosition.CenterScreen; BackColor = Color.FromArgb(16, 19, 19);
        var icon = Path.Combine(InspectorProgram.Output, "inspector.ico"); if (File.Exists(icon)) Icon = new Icon(icon);
        view.Dock = DockStyle.Fill; view.DefaultBackgroundColor = BackColor;
        status.Dock = DockStyle.Top; status.Height = 42; status.TextAlign = ContentAlignment.MiddleCenter;
        status.ForeColor = Color.FromArgb(197, 244, 123); status.Text = "상품 검수 프로그램을 준비하고 있어요…";
        Controls.Add(view); Controls.Add(status);
        Shown += async delegate { await Initialize(); };
        // Closing the screen leaves the local runner and confirmed results intact.
        // The explicit stop button cooperatively ends an active inspection.
    }
    async Task Initialize()
    {
        try
        {
            int state = await Task.Run(() => InspectorProgram.Health());
            if (state == 2) throw new InvalidOperationException("상품 검수 포트를 다른 프로그램이 사용 중이에요.");
            if (state == 0)
            {
                using (var launch = new Mutex(false, "Local\\Aemeath.Bizhost.Inspector.8876"))
                {
                    bool acquired = false;
                    try
                    {
                        try { acquired = launch.WaitOne(0); } catch (AbandonedMutexException) { acquired = true; }
                        if (acquired && InspectorProgram.Health() == 0)
                        {
                            string python = File.ReadAllText(Path.Combine(InspectorProgram.Output, "python-path.txt")).Trim();
                            if (!File.Exists(python)) throw new InvalidOperationException("설치된 실행 환경을 찾지 못했어요.");
                            Process.Start(new ProcessStartInfo(python, "\"" + Path.Combine(InspectorProgram.Root, "bizhost_inspector.py") + "\"")
                            { WorkingDirectory = InspectorProgram.Root, UseShellExecute = false, CreateNoWindow = true, WindowStyle = ProcessWindowStyle.Hidden });
                        }
                        for (int i = 0; i < 60 && state == 0; i++)
                        { await Task.Delay(500); state = await Task.Run(() => InspectorProgram.Health()); }
                    }
                    finally { if (acquired) launch.ReleaseMutex(); }
                }
                if (state != 1) throw new InvalidOperationException("검수 프로그램 연결을 확인하지 못했어요. 창을 다시 열어 주세요.");
            }
            var environment = await CoreWebView2Environment.CreateAsync(null, Path.GetFullPath(Path.Combine(InspectorProgram.Output, "..", "webview-data")));
            await view.EnsureCoreWebView2Async(environment);
            view.CoreWebView2.Settings.AreHostObjectsAllowed = false;
            view.CoreWebView2.Settings.IsWebMessageEnabled = false;
            view.CoreWebView2.NavigationStarting += delegate(object sender, CoreWebView2NavigationStartingEventArgs e)
            { if (e.Uri != "about:blank" && !InspectorProgram.Local(e.Uri)) e.Cancel = true; };
            view.CoreWebView2.NewWindowRequested += delegate(object sender, CoreWebView2NewWindowRequestedEventArgs e) { e.Handled = true; };
            view.CoreWebView2.DownloadStarting += delegate(object sender, CoreWebView2DownloadStartingEventArgs e)
            {
                e.Handled = true;
                using (var dialog = new SaveFileDialog { FileName = "비즈호스트 검수결과.json", Filter = "검수 결과|*.json", OverwritePrompt = true })
                { if (dialog.ShowDialog(this) == DialogResult.OK) e.ResultFilePath = dialog.FileName; else e.Cancel = true; }
            };
            view.CoreWebView2.Navigate(InspectorProgram.Home); status.Visible = false;
        }
        catch (Exception e) { status.Text = "실행을 마치지 못했어요."; MessageBox.Show(this, e.Message, "상품 검수", MessageBoxButtons.OK, MessageBoxIcon.Warning); }
    }
}

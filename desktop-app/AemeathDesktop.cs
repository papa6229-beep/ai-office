using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using System.Web.Script.Serialization;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace AemeathDesktop
{
    internal static class Program
    {
        internal static readonly string Output = AppDomain.CurrentDomain.BaseDirectory;
        internal static readonly string Root = Path.GetFullPath(Path.Combine(Output, "..", "..", ".."));
        internal const string Home = "http://127.0.0.1:8765/";
        internal static uint ActivateMessage;
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern uint RegisterWindowMessage(string name);
        [DllImport("user32.dll")] static extern bool PostMessage(IntPtr window, uint message, IntPtr wparam, IntPtr lparam);
        [DllImport("shell32.dll", CharSet = CharSet.Unicode)] static extern int SetCurrentProcessExplicitAppUserModelID(string id);

        [STAThread]
        static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            string key;
            using (var sha = SHA256.Create())
                key = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(Root.ToLowerInvariant()))).Replace("-", "").Substring(0, 20);
            ActivateMessage = RegisterWindowMessage("AI.Office.Activate." + key);
            bool first;
            using (var singleton = new Mutex(true, "Local\\AI.Office.Desktop." + key, out first))
            {
                if (!first)
                {
                    PostMessage(new IntPtr(0xffff), ActivateMessage, IntPtr.Zero, IntPtr.Zero);
                    return;
                }
                try
                {
                    SetCurrentProcessExplicitAppUserModelID("AI.Office.Aemeath." + key);
                    if (!File.Exists(Path.Combine(Root, "app.py"))) throw new InvalidOperationException("오피스 원본 폴더를 찾지 못했어요. 원래 위치에서 다시 실행해 주세요.");
                    Application.Run(new OfficeWindow());
                }
                catch (Exception error)
                {
                    MessageBox.Show("에이메스를 실행하지 못했어요.\n" + error.Message, "에이메스", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
            }
        }

        internal static bool IsLocal(string address)
        {
            Uri uri;
            return Uri.TryCreate(address, UriKind.Absolute, out uri) && uri.Scheme == "http" && uri.Host == "127.0.0.1" && uri.Port == 8765 && String.IsNullOrEmpty(uri.UserInfo);
        }

        internal static void External(string address)
        {
            Uri uri;
            if (!Uri.TryCreate(address, UriKind.Absolute, out uri) || (uri.Scheme != "http" && uri.Scheme != "https") || !String.IsNullOrEmpty(uri.UserInfo)) return;
            try { Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true }); }
            catch { MessageBox.Show("링크를 열지 못했어요. 기본 브라우저 설정을 확인해 주세요.", "에이메스"); }
        }

        // 0 = absent; 1 = identified Office; 2 = another service uses the port.
        internal static int ServerState(bool lightweight = false)
        {
            try
            {
                var request = (HttpWebRequest)WebRequest.Create(lightweight ? Home + "api/work/reload-status" : Home);
                request.Proxy = null; request.Timeout = 1500; request.ReadWriteTimeout = 1500;
                request.AllowAutoRedirect = false;
                using (var response = (HttpWebResponse)request.GetResponse())
                using (var reader = new StreamReader(response.GetResponseStream()))
                {
                    char[] head = new char[8192];
                    int size = reader.Read(head, 0, head.Length);
                    string text = new String(head, 0, size);
                    return response.StatusCode == HttpStatusCode.OK && text.Contains(lightweight ? "\"pid\"" : "<title>AI OFFICE</title>") ? 1 : 2;
                }
            }
            catch (WebException error)
            {
                if (error.Response != null) { error.Response.Close(); return 2; }
                return 0;
            }
        }

        internal static void StartServer()
        {
            string setting = Path.Combine(Output, "python-path.txt");
            string python = File.ReadAllText(setting, Encoding.UTF8).Trim();
            if (!File.Exists(python)) throw new InvalidOperationException("설치 때 사용한 Python을 찾지 못했어요. 실행기를 다시 빌드해 주세요.");
            var start = new ProcessStartInfo(python, "\"" + Path.Combine(Root, "desktop-app", "server_bootstrap.py") + "\"");
            start.WorkingDirectory = Root; start.UseShellExecute = false; start.CreateNoWindow = true;
            start.EnvironmentVariables["AI_OFFICE_NO_BROWSER"] = "1";
            start.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            // Server owns its log file instead of pipes to this window, so it survives window close.
            Process.Start(start).Dispose();
        }
    }

    internal sealed class OfficeWindow : Form
    {
        WebView2 view = new WebView2();
        readonly Label status = new Label();
        readonly Panel recoveryBar = new Panel();
        readonly Button recoverButton = new Button();
        readonly System.Windows.Forms.Timer reconnect = new System.Windows.Forms.Timer();
        bool polling, ready, pageFailed, browserFailed, recoveringPage;
        int networkFailures;
        const string UiRecoveryRevision = "2026-10-29-explicit-page-recovery-v1";
        CoreWebView2Environment environment;
        readonly NativeMessengerHost messengers;

        internal OfficeWindow()
        {
            Text = "에이메스 · AI OFFICE";
            MinimumSize = new Size(1040, 720); Size = new Size(1500, 960);
            StartPosition = FormStartPosition.CenterScreen; WindowState = FormWindowState.Maximized;
            BackColor = Color.FromArgb(14, 16, 17);
            string icon = Path.Combine(Program.Output, "aemeath.ico");
            if (File.Exists(icon)) Icon = new Icon(icon);
            view.Dock = DockStyle.Fill; view.DefaultBackgroundColor = BackColor;
            recoveryBar.Dock=DockStyle.Top;recoveryBar.Height=44;
            status.Dock = DockStyle.Fill; status.Height = 44;
            status.BackColor = Color.FromArgb(29, 34, 28); status.ForeColor = Color.FromArgb(205, 255, 105);
            status.Font = new Font("Malgun Gothic", 10); status.TextAlign = ContentAlignment.MiddleCenter;
            status.Text = "에이메스를 준비하고 있어요…";
            recoverButton.Dock=DockStyle.Right;recoverButton.Width=110;recoverButton.Text="화면 복구";recoverButton.Visible=false;
            recoverButton.Click+=async delegate { await RecoverPage(); };
            recoveryBar.Controls.Add(status);recoveryBar.Controls.Add(recoverButton);
            Controls.Add(view); Controls.Add(recoveryBar); recoveryBar.BringToFront();
            messengers = new NativeMessengerHost(this);
            HandleCreated += delegate { MessengerReceipt("ready",null,null,null); };
            Shown += async delegate { await Initialize(); };
            reconnect.Interval = 2500; reconnect.Tick += async delegate { await CheckRecovery(); };
            FormClosed += delegate { messengers.Dispose(); reconnect.Stop(); reconnect.Dispose(); };
        }

        protected override void WndProc(ref Message message)
        {
            if (message.Msg == Program.ActivateMessage)
            {
                if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
                Show(); Activate();
            }
            base.WndProc(ref message);
        }

        async Task Initialize()
        {
            try
            {
                int state = await Task.Run(() => Program.ServerState());
                if (IsDisposed) return;
                if (state == 2) throw new InvalidOperationException("오피스 포트를 다른 프로그램이 사용 중이에요. 기존 프로그램을 확인해 주세요.");
                if (state == 0)
                {
                    // A launch mutex also covers a second copy installed at a different source root.
                    using (var startLock = new Mutex(false, "Local\\AI.Office.Server.8765"))
                    {
                        bool locked = false;
                        try
                        {
                            try { locked = startLock.WaitOne(0); } catch (AbandonedMutexException) { locked = true; }
                            if (locked && await Task.Run(() => Program.ServerState()) == 0) Program.StartServer();
                            var limit = DateTime.UtcNow.AddSeconds(90);
                            do
                            {
                                await Task.Delay(700);
                                if (IsDisposed) return;
                                state = await Task.Run(() => Program.ServerState());
                                if (state == 2) throw new InvalidOperationException("오피스 서버를 확인하지 못했어요. 시작 로그를 확인해 주세요.");
                            } while (state != 1 && DateTime.UtcNow < limit);
                        }
                        finally { if (locked) startLock.ReleaseMutex(); }
                    }
                    if (state != 1) throw new InvalidOperationException("서버 준비 시간이 길어지고 있어요. 잠시 후 아이콘으로 다시 실행해 주세요.");
                }
                if (IsDisposed) return;
                string profile = Path.Combine(Program.Output, "..", "user-data");
                environment = await CoreWebView2Environment.CreateAsync(null, Path.GetFullPath(profile));
                await view.EnsureCoreWebView2Async(environment);
                if (IsDisposed) return;
                ConfigureWebView(view, environment);
                // Only this local Office root gets the fixed messenger bridge;
                // previews and external pages do not receive native capability.
                view.CoreWebView2.Settings.IsWebMessageEnabled = true;
                await view.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(
                    "if(location.origin==='http://127.0.0.1:8765'&&(location.pathname==='/'||location.pathname==='/index.html'))Object.defineProperty(window,'officeNativeMessengerHost',{value:3});");
                view.CoreWebView2.WebMessageReceived += NativeMessengerMessage;
                string seed = Path.Combine(Program.Output, "..", "other-requests-seed.json");
                if (File.Exists(seed))
                {
                    // Only the exact first-run Office request list is migrated; no browser profile/cookies.
                    string encoded = Convert.ToBase64String(File.ReadAllBytes(seed));
                    await view.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(
                        "(()=>{if(location.origin!=='http://127.0.0.1:8765')return;try{" +
                        "const key='aemeath.otherRequests.v1';if(localStorage.getItem(key)!==null)return;" +
                        "const data=decodeURIComponent(escape(atob('" + encoded + "')));" +
                        "const rows=JSON.parse(data);if(Array.isArray(rows)&&rows.length<=100)localStorage.setItem(key,data);" +
                        "}catch(_){}})()");
                }
                view.CoreWebView2.NavigationCompleted += delegate(object sender, CoreWebView2NavigationCompletedEventArgs e)
                {
                    if(e.IsSuccess){pageFailed=false;browserFailed=false;recoveringPage=false;ShowRecoveryState();}
                    else if(e.WebErrorStatus!=CoreWebView2WebErrorStatus.OperationCanceled){
                        pageFailed=true;recoveringPage=false;status.Text="화면 연결에 실패했어요. 작업을 보관한 뒤 화면 복구를 눌러주세요.";
                        recoverButton.Visible=true;recoverButton.Enabled=true;recoveryBar.Visible=true;
                    }
                };
                view.CoreWebView2.ProcessFailed += delegate(object sender,CoreWebView2ProcessFailedEventArgs e) {
                    string kind=e.ProcessFailedKind.ToString();
                    if(kind!="BrowserProcessExited"&&kind!="RenderProcessExited")return;
                    pageFailed=true;browserFailed=kind=="BrowserProcessExited";recoveringPage=false;ShowRecoveryState();
                };
                ready = true; view.CoreWebView2.Navigate(Program.Home); reconnect.Start();
            }
            catch (Exception error)
            {
                if (IsDisposed) return;
                status.Text = "에이메스 실행을 마치지 못했어요. 창을 닫고 다시 실행해 주세요.";
                MessageBox.Show(this, error.Message, "에이메스", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        }

        async Task CheckRecovery()
        {
            if (polling || !ready || IsDisposed) return;
            polling = true;
            try
            {
                int state = await Task.Run(() => Program.ServerState(true));
                if (IsDisposed) return;
                if (state != 1)
                {
                    networkFailures++;
                    if(networkFailures>=2)ShowRecoveryState();
                }
                else
                {
                    networkFailures=0;ShowRecoveryState();
                }
            }
            catch { networkFailures++;if(networkFailures>=2&&!IsDisposed)ShowRecoveryState(); }
            finally { polling = false; }
        }

        void ShowRecoveryState()
        {
            recoverButton.Visible=pageFailed;
            recoverButton.Enabled=pageFailed&&networkFailures==0&&!recoveringPage;
            recoveryBar.Visible=pageFailed||networkFailures>=2;
            status.Text=pageFailed?"화면 프로세스에 문제가 생겼어요. 작업을 보관한 뒤 화면 복구를 눌러주세요.":
                networkFailures>=2?"서비스 연결을 기다리고 있어요. 현재 화면과 입력은 유지합니다.":"";
        }

        bool NativeCanReplaceView()
        {
            foreach(Control child in Controls)if(child!=view&&child!=recoveryBar&&child.Visible)return false;
            foreach(Form child in OwnedForms)if(child.Visible)return false;
            return true;
        }

        async Task RecoverPage()
        {
            if(!pageFailed||recoveringPage||networkFailures!=0)return;
            if(!NativeCanReplaceView()){status.Text="열린 메신저·미리보기 창을 정리한 뒤 화면 복구를 눌러주세요.";return;}
            try {
                string safe=await view.CoreWebView2.ExecuteScriptAsync("window.officeUpdates?window.officeUpdates.prepareRecovery():true");
                if(safe=="false"){status.Text="진행 중인 입력·첨부·작업창을 정리한 뒤 복구해 주세요.";return;}
            } catch { /* A failed renderer cannot supply a draft; use its prior local snapshot. */ }
            recoveringPage=true;recoverButton.Enabled=false;
            try {
                if(browserFailed){
                    ready=false;var old=view;Controls.Remove(old);old.Dispose();
                    view=new WebView2();view.Dock=DockStyle.Fill;view.DefaultBackgroundColor=BackColor;Controls.Add(view);recoveryBar.BringToFront();
                    await Initialize();
                } else view.CoreWebView2.Reload();
            } catch {recoveringPage=false;ShowRecoveryState();status.Text="화면 복구를 마치지 못했어요. 현재 작업은 유지됩니다.";}
        }

        string messengerClientBuild;
        async void NativeMessengerMessage(object sender, CoreWebView2WebMessageReceivedEventArgs e)
        {
            Uri address;
            if (!Program.IsLocal(e.Source) || !Uri.TryCreate(e.Source, UriKind.Absolute, out address)
                || (address.AbsolutePath != "/" && address.AbsolutePath != "/index.html")
                || e.WebMessageAsJson.Length > 65536) return;
            string ident = null;
            var json = new JavaScriptSerializer { MaxJsonLength = 65536 };
            try
            {
                var data = json.Deserialize<Dictionary<string, object>>(e.WebMessageAsJson);
                if(data!=null&&data.Count==2&&data.ContainsKey("type")&&data["type"] as string=="office-page-update"&&data.ContainsKey("id")){
                    string updateId=data["id"] as string;
                    if(updateId!=null&&System.Text.RegularExpressions.Regex.IsMatch(updateId,"^[a-f0-9]{32}$"))
                        view.CoreWebView2.PostWebMessageAsJson(json.Serialize(new {type="office-page-update-result",id=updateId,ok=NativeCanReplaceView(),error="열린 메신저·미리보기 창을 정리한 뒤 적용해 주세요."}));
                    return;
                }
                if(data!=null&&data.Count==3&&data.ContainsKey("type")&&data["type"] as string=="office-native-messenger-client") {
                    object build,request;
                    if(data.TryGetValue("build",out build)&&build is string&&(string)build=="20261026-entry-v3"
                        &&data.TryGetValue("id",out request)&&request is string&&System.Text.RegularExpressions.Regex.IsMatch((string)request,"^[a-f0-9]{32}$")) {
                        messengerClientBuild=(string)build;MessengerReceipt("client_ready",(string)request,null,null);
                        view.CoreWebView2.PostWebMessageAsJson(json.Serialize(new {type="office-native-messenger-status",bridge=3,pid=Process.GetCurrentProcess().Id,frontend_build=messengerClientBuild,ui_recovery_revision=UiRecoveryRevision}));
                    }
                    return;
                }
                if (data == null || (data.Count != 3 && data.Count != 4) || (data.Count==4&&!data.ContainsKey("notification")) || !data.ContainsKey("type") || !data.ContainsKey("id") || !data.ContainsKey("source")
                    || !(data["type"] is string) || (string)data["type"] != "office-native-messenger") return;
                ident = data["id"] as string;
                string selected = data["source"] as string;
                if (ident == null || !System.Text.RegularExpressions.Regex.IsMatch(ident, "^[a-f0-9]{32}$") || !NativeMessengerHost.Known(selected)) return;
                Dictionary<string,object> notification=null;
                if(data.ContainsKey("notification")&&data["notification"]!=null) {
                    notification=data["notification"] as Dictionary<string,object>;
                    if(notification==null||notification.Count!=5||selected=="messengers")throw new InvalidOperationException("선택한 알림 정보를 확인해 주세요.");
                    foreach(var field in new[]{"id","title","body","status"}) {
                        object value;
                        if(!notification.TryGetValue(field,out value)||!(value is string)||((string)value).Length>(field=="body"?5000:field=="status"?500:200))throw new InvalidOperationException("알림 표시 범위를 확인해 주세요.");
                    }
                    if(!notification.ContainsKey("room")||!(notification["room"] is bool)
                        ||!System.Text.RegularExpressions.Regex.IsMatch((string)notification["id"],"^[a-f0-9]{32,64}$"))throw new InvalidOperationException("선택한 알림 ID를 확인해 주세요.");
                }
                MessengerReceipt("received",ident,selected,notification==null?null:(string)notification["id"]);
                await messengers.Open(selected,notification);
                MessengerReceipt(messengers.NativeReady?"native_ready":"record_only",ident,selected,notification==null?null:(string)notification["id"]);
                if (!IsDisposed) view.CoreWebView2.PostWebMessageAsJson(json.Serialize(new { type="office-native-messenger", id=ident, ok=true, source=selected, surface="embedded-native", native_ready=messengers.NativeReady, record_shown=false, notification_id=notification==null?null:notification["id"], conversation_selected=selected=="between"&&messengers.NativeReady }));
            }
            catch (Exception error)
            {
                MessengerReceipt("failed",ident,null,null);
                if (ident != null && !IsDisposed) view.CoreWebView2.PostWebMessageAsJson(json.Serialize(new { type="office-native-messenger", id=ident, ok=false, error=error.Message }));
            }
        }

        void MessengerReceipt(string phase,string requestId,string source,string notificationId)
        {
            // Operational metadata only; never persist message text or tokens.
            try {
                string folder=Path.Combine(Program.Root,"local_backups","desktop-app");Directory.CreateDirectory(folder);
                var data=new { bridge=3,frontend_build=messengerClientBuild,pid=Process.GetCurrentProcess().Id,executable=Application.ExecutablePath,phase=phase,request_id=requestId,source=source,notification_id=notificationId,at_utc=DateTime.UtcNow.ToString("o") };
                File.WriteAllText(Path.Combine(folder,"native-messenger-bridge.json"),new JavaScriptSerializer().Serialize(data),Encoding.UTF8);
            } catch { }
        }

        internal static void ConfigureWebView(WebView2 target, CoreWebView2Environment environment)
        {
            var core = target.CoreWebView2;
            core.Settings.AreHostObjectsAllowed = false;
            core.Settings.IsWebMessageEnabled = false;
            core.NavigationStarting += delegate(object sender, CoreWebView2NavigationStartingEventArgs e)
            {
                if (e.Uri == "about:blank" || Program.IsLocal(e.Uri)) return;
                e.Cancel = true; Program.External(e.Uri);
            };
            core.NewWindowRequested += async delegate(object sender, CoreWebView2NewWindowRequestedEventArgs e)
            {
                e.Handled = true;
                string address = e.Uri;
                if (!Program.IsLocal(address)) { Program.External(address); return; }
                var popup = new Form { Text = "에이메스 · 미리보기", Width = 1150, Height = 850, StartPosition = FormStartPosition.CenterParent };
                popup.Icon = target.FindForm().Icon;
                var preview = new WebView2 { Dock = DockStyle.Fill, DefaultBackgroundColor = Color.FromArgb(20, 20, 20) };
                popup.Controls.Add(preview); popup.Show(target.FindForm());
                try { await preview.EnsureCoreWebView2Async(environment); ConfigureWebView(preview, environment); preview.CoreWebView2.Navigate(address); }
                catch { popup.Close(); MessageBox.Show("미리보기를 열지 못했어요.", "에이메스"); }
            };
            core.DownloadStarting += delegate(object sender, CoreWebView2DownloadStartingEventArgs e)
            {
                e.Handled = true;
                using (var chooser = new SaveFileDialog { FileName = Path.GetFileName(e.ResultFilePath), Title = "에이메스 · 파일 저장", OverwritePrompt = true })
                {
                    if (chooser.ShowDialog(target.FindForm()) == DialogResult.OK) e.ResultFilePath = chooser.FileName;
                    else e.Cancel = true;
                }
            };
        }
    }
}

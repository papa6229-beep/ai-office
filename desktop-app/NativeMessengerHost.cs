using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace AemeathDesktop
{
    // Keeps original top-level GPU/transparent surfaces intact. The client is
    // owned by Office and positioned over its reserved content rectangle; it is
    // never converted into a cross-process WS_CHILD window (SetParent breaks
    // Between's transparent Chromium surface). No injected keyboard/mouse input.
    internal sealed class NativeMessengerHost : IDisposable
    {
        readonly Form office;
        readonly Panel shade = new PopupCard(), frame = new Panel(), surface = new Panel();
        readonly Label title = new Label(), notice = new Label();
        readonly PictureBox appIcon = new PictureBox();
        readonly ContextMenuStrip sources = new ContextMenuStrip();
        readonly ToolTip hints = new ToolTip();
        readonly Button back = new Button();
        readonly TableLayoutPanel hanbiroRecovery = new TableLayoutPanel();
        readonly Button hanbiroLaunch = new Button();
        readonly SemaphoreSlim openGate = new SemaphoreSlim(1,1);
        internal const string Revision="20261112-hanbiro-minimized-roster-v10";
        string hanbiroSelection="none";
        bool hanbiroRendererVisible;
        IntPtr hanbiroPopup;
        string lastSource="between", notificationId;
        internal bool NativeReady { get; private set; }
        readonly System.Windows.Forms.Timer watch = new System.Windows.Forms.Timer();
        List<SavedWindow> windows = new List<SavedWindow>();
        readonly Dictionary<string,ClientSession> clients = new Dictionary<string,ClientSession>();
        readonly Dictionary<string,Image> icons = new Dictionary<string,Image>();
        readonly Dictionary<string,Size> popupSizes = new Dictionary<string,Size>();
        string switchMode="initial";
        long switchElapsed;
        readonly HashSet<int> allowedPids = new HashSet<int>();
        string source;
        bool opening, disposed, waking, closeAfterWake, closeOfficeAfterWake, positioned, fitting;
        int generation;
        int closeVersion;
        DateTime pidScan;
        Rectangle fittedBounds;
        bool suspended;
        readonly Dictionary<string,Size> minimumContent = new Dictionary<string,Size>();
        Point dragStart;
        readonly object receiptLock = new object();

        internal NativeMessengerHost(Form owner)
        {
            office=owner;
            // A compact in-Office popup. No full-window shade: the rest of the
            // WebView stays visible and usable while a conversation is open.
            shade.Dock=DockStyle.None; shade.Padding=new Padding(3); shade.BackColor=Color.FromArgb(32,34,37); shade.Visible=false;
            frame.Dock=DockStyle.Fill; frame.BackColor=shade.BackColor; frame.Padding=new Padding(8,0,8,22);
            frame.Resize+=delegate { ClipRounded(frame,11); };
            var header=new Panel { Dock=DockStyle.Top, Height=56, Padding=new Padding(4,9,4,9), Cursor=Cursors.SizeAll };
            title.AutoSize=false; title.Dock=DockStyle.Fill; title.ForeColor=Color.FromArgb(242,240,232);
            title.Font=new Font("Malgun Gothic",11.5f,FontStyle.Bold); title.TextAlign=ContentAlignment.MiddleLeft;
            appIcon.Dock=DockStyle.Left;appIcon.Width=40;appIcon.SizeMode=PictureBoxSizeMode.CenterImage;
            var close=new ChromeButton("close","대화 닫기") { Dock=DockStyle.Right, Width=34 };
            close.Click+=delegate { Close(); };
            var choose=new ChromeButton("menu","메신저 전환") { Dock=DockStyle.Right, Width=34 };
            choose.Click+=delegate { sources.Show(choose,new Point(choose.Width-sources.PreferredSize.Width,choose.Height+4)); };
            back=new ChromeButton("back","이전 대화") { Dock=DockStyle.Right, Width=34, Visible=false };
            back.Click+=async delegate { try { if(source=="hanbiro")await Open("hanbiro");else Back(); } catch(Exception e){ShowNotice(e.Message);} };
            header.Controls.Add(title);header.Controls.Add(appIcon);header.Controls.Add(choose);header.Controls.Add(back);header.Controls.Add(close);
            hints.SetToolTip(close,"닫기");hints.SetToolTip(choose,"메신저 전환");hints.SetToolTip(back,"이전 대화");
            BindDrag(header); BindDrag(title);BindDrag(appIcon);
            sources.ShowImageMargin=false;sources.ShowCheckMargin=false;sources.Font=new Font("Malgun Gothic",9.5f);
            sources.Padding=new Padding(6);sources.DropShadowEnabled=false;sources.Renderer=new SourceMenuRenderer();
            sources.SizeChanged+=delegate { ClipRounded(sources,10); };
            sources.BackColor=Color.FromArgb(40,43,47);sources.ForeColor=Color.FromArgb(242,240,232);
            foreach(string name in new[]{"between","hanbiro","nateon","kakao"}) {
                string selected=name;
                var item=new ToolStripMenuItem(Label(name)) { AutoSize=false, Width=216, Height=44, Image=AppImage(name) };
                item.Click+=async delegate { try{await Open(selected);}catch(Exception e){ShowNotice(e.Message);} };
                sources.Items.Add(item);
            }
            notice.Dock=DockStyle.Bottom; notice.Height=38; notice.Padding=new Padding(8,5,6,3);notice.Visible=false;
            notice.ForeColor=Color.FromArgb(239,181,181); notice.Font=new Font("Malgun Gothic",8.5f);
            surface.Dock=DockStyle.Fill; surface.BackColor=Color.FromArgb(22,24,24);
            surface.Resize+=delegate { Fit(); };
            hanbiroRecovery.Dock=DockStyle.Fill;hanbiroRecovery.Visible=false;
            hanbiroRecovery.ColumnCount=1;hanbiroRecovery.RowCount=1;
            hanbiroLaunch.Anchor=AnchorStyles.None;hanbiroLaunch.AutoSize=true;
            hanbiroLaunch.Padding=new Padding(14,8,14,8);
            hanbiroLaunch.FlatStyle=FlatStyle.Flat;
            hanbiroLaunch.BackColor=Color.FromArgb(44,48,51);hanbiroLaunch.ForeColor=title.ForeColor;
            hanbiroLaunch.Click+=async delegate { await RecoverHanbiro(); };
            hanbiroRecovery.Controls.Add(hanbiroLaunch,0,0);
            surface.Controls.Add(hanbiroRecovery);
            frame.Controls.Add(surface);frame.Controls.Add(notice);frame.Controls.Add(header);
            shade.Controls.Add(frame); office.Controls.Add(shade);
            var grip=new ResizeGrip { Size=new Size(18,18), Anchor=AnchorStyles.Right|AnchorStyles.Bottom };
            frame.Controls.Add(grip);grip.BringToFront();
            frame.Resize+=delegate { grip.Location=new Point(frame.Width-22,frame.Height-21); };
            hints.SetToolTip(grip,"끌어서 크기 조절");
            BindResize(grip);
            shade.Resize+=delegate { Fit(); };
            office.LocationChanged+=delegate { Fit(); };
            office.SizeChanged+=delegate { Layout(); };
            office.VisibleChanged+=delegate { Fit(); };
            office.HandleCreated+=delegate { SaveReceipt("ready"); };
            office.FormClosing+=delegate(object sender,FormClosingEventArgs e) { if(waking)closeOfficeAfterWake=true;if(!Close())e.Cancel=true; };
            watch.Interval=250; watch.Tick+=delegate { Observe(); };
        }

        void ShowNotice(string text)
        {
            notice.Text=text;notice.Visible=true;shade.Visible=true;shade.BringToFront();Fit();
        }
        void SetHeader(string selected)
        {
            title.Text="통합메신저 · "+Label(selected);notice.Visible=false;
            hanbiroRecovery.Visible=false;
            hanbiroLaunch.Text="한비로톡 실행";
            back.Text=selected=="hanbiro"?"대화 목록":"";
            back.Width=selected=="hanbiro"?112:34;
            back.AccessibleName=selected=="hanbiro"?"대화 목록으로 돌아가기":"이전 대화";
            hints.SetToolTip(back,back.AccessibleName);
            back.Invalidate();
            foreach(ToolStripMenuItem item in sources.Items)item.Checked=item.Text==Label(selected);
            appIcon.Image=AppImage(selected);
        }
        static string HanbiroShortcut()
        {
            return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs),"HanbiroTalk2.lnk");
        }
        bool NativeHanbiro
        {
            get { ClientSession client; return source=="hanbiro"&&clients.TryGetValue(source,out client)&&client.NativeVisible; }
        }
        async Task RecoverHanbiro()
        {
            if(opening||source!="hanbiro"||!hanbiroRecovery.Visible)return;
            hanbiroLaunch.Enabled=false;
            try {
                ScanPids();
                if(allowedPids.Count>0) {
                    await Open("hanbiro");
                    return;
                }
                string app=Executable("hanbiro");
                string shortcut=HanbiroShortcut();
                if(File.Exists(app)&&FileVersionInfo.GetVersionInfo(app).ProductVersion=="2.1.283.0")shortcut=app;
                if(!File.Exists(shortcut))throw new FileNotFoundException("시작 메뉴에서 한비로톡을 실행한 뒤 대화 목록을 눌러 주세요.");
                // Use the user's existing Start menu entry only after a click.
                using(var process=Process.Start(new ProcessStartInfo(shortcut){UseShellExecute=true,
                    WorkingDirectory=shortcut==app?Environment.GetFolderPath(Environment.SpecialFolder.UserProfile):Path.GetDirectoryName(shortcut)})) { }
                hanbiroLaunch.Text="한비로 대화 목록 연결";
                ShowNotice("한비로톡 창이 뜨면 여기서 대화 목록 연결을 눌러 주세요.");
            } catch(Exception error) {
                if(source=="hanbiro"&&shade.Visible)ShowNotice(error is System.ComponentModel.Win32Exception?
                    "한비로톡을 실행하지 못했어요. 시작 메뉴에서 직접 열어 주세요.":error.Message);
            } finally { hanbiroLaunch.Enabled=true; }
        }
        Image AppImage(string selected)
        {
            Image image;if(icons.TryGetValue(selected,out image))return image;
            try { string exe=Executable(selected);if(File.Exists(exe))using(var icon=Icon.ExtractAssociatedIcon(exe))using(var sized=new Icon(icon,28,28))image=sized.ToBitmap(); }
            catch { image=null; }
            icons[selected]=image;return image;
        }
        internal static bool Known(string value) { return value=="between"||value=="hanbiro"||value=="nateon"||value=="kakao"||value=="messengers"; }
        static string Label(string value) { return value=="between"?"비트윈":value=="hanbiro"?"한비로톡":value=="kakao"?"카카오톡":"네이트온"; }
        void BindDrag(Control control)
        {
            control.MouseDown+=delegate(object sender,MouseEventArgs e) {
                if(e.Button!=MouseButtons.Left)return;
                var point=office.PointToClient(Control.MousePosition);
                dragStart=new Point(point.X-shade.Left,point.Y-shade.Top); control.Capture=true;
            };
            control.MouseMove+=delegate(object sender,MouseEventArgs e) {
                if(!control.Capture||e.Button!=MouseButtons.Left)return;
                var point=office.PointToClient(Control.MousePosition);
                shade.Location=Clamp(new Point(point.X-dragStart.X,point.Y-dragStart.Y));positioned=true;Fit();
            };
            control.MouseUp+=delegate { control.Capture=false; };
        }
        Point Clamp(Point point)
        {
            return new Point(Math.Max(8,Math.Min(point.X,office.ClientSize.Width-shade.Width-8)),
                Math.Max(8,Math.Min(point.Y,office.ClientSize.Height-shade.Height-8)));
        }
        void BindResize(Control control)
        {
            Point start=Point.Empty;Size initial=Size.Empty;
            control.MouseDown+=delegate(object sender,MouseEventArgs e) {
                if(e.Button!=MouseButtons.Left)return;
                start=Control.MousePosition;initial=shade.Size;control.Capture=true;
            };
            control.MouseMove+=delegate(object sender,MouseEventArgs e) {
                if(!control.Capture||e.Button!=MouseButtons.Left||source==null)return;
                Size minimum;minimumContent.TryGetValue(source,out minimum);
                var point=Control.MousePosition;
                int maxWidth=Math.Max(1,office.ClientSize.Width-shade.Left-8),maxHeight=Math.Max(1,office.ClientSize.Height-shade.Top-8);
                int width=Math.Min(maxWidth,Math.Max(Math.Max(340,minimum.Width+22),initial.Width+point.X-start.X));
                int height=Math.Min(maxHeight,Math.Max(Math.Max(360,minimum.Height+84),initial.Height+point.Y-start.Y));
                shade.Size=new Size(width,height);popupSizes[source]=shade.Size;Fit();
            };
            control.MouseUp+=delegate { control.Capture=false;if(source!=null){popupSizes[source]=shade.Size;SaveReceipt("attached");} };
        }

        async Task RevealBetween(int request)
        {
            if(source!="between"||windows.Count==0)return;
            var client=clients[source];if(client.Revealed)return;
            var saved=windows[windows.Count-1];var active=Win.GetForegroundWindow();
            // Only the user's already-active Office/client interaction can
            // request the installed client's normal show handler. Background
            // work never starts/focuses a messenger. Attach/Fit happen first.
            if(active!=office.Handle&&active!=saved.Handle)return;
            string exe=Win.ProcessPath(saved.Pid);
            if(!AllowedPath("between",exe)||Path.GetFileName(Path.GetDirectoryName(exe))!="1.4.0.0")return;
            waking=true;
            try {
                // Verified installed 1.4.0: second-instance => existing u.show()
                // after100ms, not a new BrowserWindow. This synchronizes the
                // app's own hidden/visible state instead of Win32 paint alone.
                using(var process=Process.Start(new ProcessStartInfo(exe) {
                    UseShellExecute=false,WindowStyle=ProcessWindowStyle.Hidden,WorkingDirectory=Path.GetDirectoryName(exe)
                })) { }
                await Task.Delay(500);
            } finally { waking=false; }
            if(request!=generation||closeAfterWake)throw new OperationCanceledException("메신저 창을 닫았어요.");
            client.Revealed=true;
            fittedBounds=Rectangle.Empty; Fit();
        }

        async Task RevealHanbiro(int request)
        {
            if(source!="hanbiro"||windows.Count==0)return;
            var saved=windows[0];var active=Win.GetForegroundWindow();
            // This is an explicit native popup selection, never a timer or a
            // background task. The helper rechecks ownership and foreground.
            if(active!=office.Handle&&active!=saved.Handle)
                throw new InvalidOperationException("다른 화면을 사용 중이라 표시를 시작하지 않았어요. 한비로를 다시 선택해 주세요.");
            long officeHandle=office.Handle.ToInt64(),appHandle=saved.Handle.ToInt64();
            waking=true;
            Dictionary<string,object> result;
            try {
                result=await Task.Run(()=>{
                    string python=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),".cache","codex-runtimes","codex-primary-runtime","dependencies","python","python.exe");
                    var start=new ProcessStartInfo(python,"\""+Path.Combine(Program.Root,"messenger_native_window.py")+"\"") {
                        UseShellExecute=false,CreateNoWindow=true,WorkingDirectory=Program.Root,
                        RedirectStandardInput=true,RedirectStandardOutput=true,RedirectStandardError=true
                    };
                    start.EnvironmentVariables["PYTHONIOENCODING"]="utf-8";
                    using(var process=Process.Start(start)) {
                        var output=process.StandardOutput.ReadToEndAsync();var error=process.StandardError.ReadToEndAsync();
                        process.StandardInput.Write(new System.Web.Script.Serialization.JavaScriptSerializer().Serialize(new{action="present",office=officeHandle,handle=appHandle}));
                        process.StandardInput.Close();
                        if(!process.WaitForExit(8000)){process.Kill();throw new InvalidOperationException("한비로 내부 표시 확인 시간이 초과됐어요.");}
                        Task.WaitAll(output,error);
                        if(process.ExitCode!=0)throw new InvalidOperationException("한비로 내부 표시 연결을 확인하지 못했어요.");
                        return new System.Web.Script.Serialization.JavaScriptSerializer().Deserialize<Dictionary<string,object>>(output.Result);
                    }
                });
            } finally { waking=false; }
            if(request!=generation||disposed||closeAfterWake)throw new OperationCanceledException("메신저 창을 닫았어요.");
            if(result==null||!result.ContainsKey("ok")||!(result["ok"] is bool)||!(bool)result["ok"])
                throw new InvalidOperationException(result!=null&&result.ContainsKey("error")?Convert.ToString(result["error"]):"한비로 내부 화면이 아직 숨김 상태예요.");
                hanbiroRendererVisible=result.ContainsKey("renderer_visible")&&result["renderer_visible"] is bool&&(bool)result["renderer_visible"];
            fittedBounds=Rectangle.Empty;Fit();
        }

        internal async Task Open(string selected)
        {
            await Open(selected,null);
        }
        internal async Task Open(string selected,Dictionary<string,object> item)
        {
            // Acknowledge only the selection that actually finished opening.
            // The former queued path returned success before attaching it.
            int version=closeVersion;
            await openGate.WaitAsync();
            try {
                if(version!=closeVersion)throw new OperationCanceledException("메신저 창을 닫았어요.");
                await OpenCore(selected,item);
            }
            finally { openGate.Release(); }
        }
        async Task OpenCore(string selected,Dictionary<string,object> item)
        {
            if(disposed||!Known(selected))throw new InvalidOperationException("연결할 메신저를 확인해 주세요.");
            if(selected=="messengers")selected=lastSource;
            opening=true;
            var elapsed=Stopwatch.StartNew();
            try {
                watch.Stop();
                if(source!=null)popupSizes[source]=shade.Size;
                if(source!=selected) {
                    if(NativeHanbiro) {
                        if(!RestoreHanbiroAfterFailure())throw new InvalidOperationException("한비로 원본 창 복원을 확인하지 못했어요.");
                    } else {
                        foreach(var prior in windows)if(Win.IsWindow(prior.Handle))Win.ShowWindow(prior.Handle,0);
                        ClientSession previous;
                        if(source!=null&&clients.TryGetValue(source,out previous))
                            foreach(var popup in previous.Floating)if(Win.IsWindow(popup.Handle))Win.ShowWindow(popup.Handle,0);
                    }
                }
                source=selected;
                lastSource=selected;NativeReady=false;hanbiroRendererVisible=false;
                ClientSession client;
                if(!clients.TryGetValue(source,out client)){client=new ClientSession();clients.Add(source,client);}
                windows=client.Windows;
                notificationId=item==null?null:(string)item["id"];
                windows.RemoveAll(saved=>!SameWindow(saved));
                if(windows.Count==0)client.Revealed=false;
                switchMode=windows.Count>0?"warm":"initial";
                int request=++generation;
                SetHeader(source);
                shade.Visible=true; shade.BringToFront(); Layout();
                ScanPids();
                fittedBounds=Rectangle.Empty;
                if(source=="hanbiro") {
                    if(allowedPids.Count==0) {
                        hanbiroSelection="client_not_running";
                        hanbiroRecovery.Visible=File.Exists(HanbiroShortcut());
                        throw new InvalidOperationException(hanbiroRecovery.Visible?
                            "한비로톡 PC 앱이 꺼져 있어요. 한비로톡 실행을 눌러 주세요.":
                            "한비로톡 PC 앱이 꺼져 있어요. 시작 메뉴에서 실행한 뒤 대화 목록을 눌러 주세요.");
                    }
                    await SelectHanbiro(client,item,request);
                    Fit();if(!client.NativeVisible)await RevealHanbiro(request);await EnsurePresented(request);
                    if(hanbiroPopup!=IntPtr.Zero)PresentHanbiroPopup(hanbiroPopup);
                    watch.Start();
                    switchElapsed=elapsed.ElapsedMilliseconds;SaveReceipt("attached");return;
                }
                if(windows.Count>0) {
                    Fit();await RevealBetween(request);await EnsurePresented(request);watch.Start();
                    switchElapsed=elapsed.ElapsedMilliseconds;SaveReceipt("attached");return;
                }
                var candidates=Candidates(false);
                if(candidates.Count==0) {
                    // A client's own startup/second-instance handler may call
                    // focus() even when launched hidden. Never do that here.
                    throw new InvalidOperationException(Label(source)+" PC 원본 창이 실행되어 있지 않아요. 화면 전환을 막기 위해 자동 실행하지 않았어요.");
                }
                await Task.Yield();
                if(request!=generation||disposed)throw new OperationCanceledException("메신저 연결을 취소했어요.");
                if(candidates.Count==0)throw new InvalidOperationException(Label(source)+" 원본 창을 연결하지 못했어요. 외부 창으로 대신 열지 않았어요.");
                // Prefer the ordinary main window; owned conversation windows
                // are captured only while the source is explicitly open here.
                candidates.Sort((a,b)=>Rank(b).CompareTo(Rank(a)));
                Attach(candidates[0]);await RevealBetween(request);await EnsurePresented(request);watch.Start();
                switchElapsed=elapsed.ElapsedMilliseconds;
                SaveReceipt("attached");
            } catch(Exception error) {
                if(!disposed&&!(error is OperationCanceledException)) {
                    watch.Stop();
                    bool restored=true;
                    if(source=="hanbiro")restored=RestoreHanbiroAfterFailure();
                    else foreach(var saved in windows)if(Win.IsWindow(saved.Handle))Win.ShowWindow(saved.Handle,0);
                    NativeReady=false;
                    ShowNotice(restored?error.Message:"한비로 원본 창을 되돌리지 못했어요. 통합메신저 닫기를 다시 눌러 주세요.");
                    SaveReceipt(!restored?"restore_pending":item!=null?"record_only":"connection_failed");
                    if(item!=null)return;
                    throw;
                }
                Close(); throw;
            } finally {
                opening=false;
                if(closeAfterWake){closeAfterWake=false;Close();}
                if(closeOfficeAfterWake){closeOfficeAfterWake=false;office.BeginInvoke(new Action(()=>office.Close()));}
            }
        }
        async Task SelectHanbiro(ClientSession client,Dictionary<string,object> item,int request)
        {
            IntPtr main=client.Main;uint pid;
            Win.GetWindowThreadProcessId(main,out pid);
            if(windows.Count==0||main==IntPtr.Zero||!Win.IsWindow(main)||!allowedPids.Contains((int)pid)||Win.Title(main)!=client.MainTitle) {
                var result=await Task.Run(()=>{
                    // Match messenger_attachments._runtime: its cache reader uses the
                    // already installed bundled Python, not the backend Python.
                    string python=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),".cache","codex-runtimes","codex-primary-runtime","dependencies","python","python.exe");
                    if(!File.Exists(python))throw new InvalidOperationException("한비로 캐시 어댑터 실행 환경을 찾지 못했어요.");
                    var start=new ProcessStartInfo(python,"\""+Path.Combine(Program.Root,"messenger_window_target.py")+"\"") {
                        UseShellExecute=false,CreateNoWindow=true,WorkingDirectory=Program.Root,
                        RedirectStandardInput=true,RedirectStandardOutput=true,RedirectStandardError=true
                    };
                    start.EnvironmentVariables["PYTHONIOENCODING"]="utf-8";
                    using(var process=Process.Start(start)) {
                        var output=process.StandardOutput.ReadToEndAsync();var error=process.StandardError.ReadToEndAsync();
                        process.StandardInput.Write(new System.Web.Script.Serialization.JavaScriptSerializer().Serialize(new{pids=new List<int>(allowedPids),allowNativeVisible=true}));
                        process.StandardInput.Close();
                        if(!process.WaitForExit(6000)){process.Kill();throw new InvalidOperationException("한비로 대화 목록 확인 시간이 초과됐어요.");}
                        Task.WaitAll(output,error);
                        if(process.ExitCode!=0)throw new InvalidOperationException("한비로 대화 목록 정보를 읽지 못했어요.");
                        return new System.Web.Script.Serialization.JavaScriptSerializer().Deserialize<Dictionary<string,object>>(output.Result);
                    }
                });
                if(request!=generation||disposed)throw new OperationCanceledException("메신저 연결을 취소했어요.");
                if(result==null||!result.ContainsKey("ok")||!(result["ok"] is bool)||!(bool)result["ok"])
                    throw new InvalidOperationException(result!=null&&result.ContainsKey("error")?Convert.ToString(result["error"]):"한비로 대화 목록창을 확인하지 못했어요.");
                main=new IntPtr(Convert.ToInt64(result["handle"]));Win.GetWindowThreadProcessId(main,out pid);
                if(!allowedPids.Contains((int)pid)||!AllowedPath("hanbiro",Win.ProcessPath((int)pid))||Win.Title(main)!=(string)result["title"])
                    throw new InvalidOperationException("한비로 목록창이 바뀌어 연결을 보류했어요.");
                object nativeVisible;
                client.NativeVisible=result.TryGetValue("native_visible",out nativeVisible)&&object.Equals(nativeVisible,true);
                client.Main=main;client.MainTitle=(string)result["title"];
            }
            // Keep the verified main/list window at the bottom of the stack.
            // Old chat windows keep their native draft/state, but never become
            // the implicit destination of an unrelated notification.
            while(windows.Count>0&&(windows.Count>1||windows[0].Handle!=main)) {
                var prior=windows[windows.Count-1];
                if(!Restore(prior))throw new InvalidOperationException("앞선 한비로 창 복원을 확인하지 못했어요.");
                windows.RemoveAt(windows.Count-1);
            }
            if(windows.Count==0) {
                if(client.NativeVisible&&!Win.IsWindowVisible(main))
                    throw new InvalidOperationException("한비로톡 PC 대화 목록을 먼저 열어 주세요.");
                Attach(main);
            }
            hanbiroSelection="list";hanbiroPopup=IntPtr.Zero;
            if(item!=null) {
                string target=((string)item["title"]).Trim();
                var matches=Candidates(false).FindAll(handle=>String.Equals(Win.Title(handle).Trim(),target,StringComparison.Ordinal));
                if(!String.IsNullOrWhiteSpace(target)&&matches.Count==1) {
                    hanbiroPopup=matches[0];hanbiroSelection="floating_existing_exact_title";
                } else {
                    notice.Text=target+" 알림 · 해당 대화창이 확인되지 않아 대화 목록을 표시합니다. 목록에서 상대를 선택해 주세요.";
                    notice.Visible=true;
                }
            }
        }
        bool TrackHanbiroPopup(IntPtr handle)
        {
            ClientSession client;
            if(source!="hanbiro"||!clients.TryGetValue("hanbiro",out client)||handle==client.Main||!Win.IsWindow(handle))return false;
            if(client.Floating.Exists(saved=>saved.Handle==handle&&SameWindow(saved)))return true;
            uint pid;Win.GetWindowThreadProcessId(handle,out pid);
            if(!allowedPids.Contains((int)pid)||!AllowedPath("hanbiro",Win.ProcessPath((int)pid)))return false;
            var savedWindow=new SavedWindow { Handle=handle,Pid=(int)pid,Style=Win.GetWindowLong(handle,-16),ExStyle=Win.GetWindowLong(handle,-20),Owner=Win.GetWindowLongPtr(handle,-8) };
            savedWindow.Placement.Length=Marshal.SizeOf(typeof(Win.Placement));
            if(!Win.GetWindowPlacement(handle,ref savedWindow.Placement))return false;
            client.Floating.Add(savedWindow);
            // Keep the native caption, size, IME and close behavior. Ownership
            // associates this modeless popup with Office without replacing
            // or hiding the main Hanbiro roster window.
            Win.SetWindowLongPtr(handle,-8,office.Handle);
            if(Win.GetWindowLongPtr(handle,-8)!=office.Handle) {
                if(Restore(savedWindow))client.Floating.Remove(savedWindow);
                return false;
            }
            return true;
        }
        void PresentHanbiroPopup(IntPtr handle)
        {
            if(!TrackHanbiroPopup(handle)){hanbiroSelection="list";return;}
            Win.ShowWindow(handle,4);
            Win.SetWindowPos(handle,IntPtr.Zero,0,0,0,0,0x0213);
            Win.RedrawWindow(handle,IntPtr.Zero,IntPtr.Zero,0x0481);
        }
        async Task EnsurePresented(int request)
        {
            var elapsed=Stopwatch.StartNew();
            do {
                if(request!=generation||disposed||!shade.Visible)throw new OperationCanceledException("메신저 연결을 취소했어요.");
                shade.PerformLayout();frame.PerformLayout();Fit();PresentAfterUserSelection();
                if(windows.Count>0) {
                    var handle=windows[windows.Count-1].Handle;
                    Win.Rect actual;Win.GetWindowRect(handle,out actual);
                    if(!suspended&&Win.IsWindowVisible(handle)&&!Win.IsIconic(handle)
                        &&actual.Left==fittedBounds.Left&&actual.Top==fittedBounds.Top
                        &&actual.Right==fittedBounds.Right&&actual.Bottom==fittedBounds.Bottom&&AboveOffice(handle)
                        &&(source!="hanbiro"||NativeHanbiro||hanbiroRendererVisible)) { NativeReady=true;return; }
                }
                await Task.Delay(30);
            } while(elapsed.ElapsedMilliseconds<900);
            throw new InvalidOperationException("원본 메신저의 표시 준비를 확인하지 못했어요. 오피스 창 크기와 앱 상태를 확인해 주세요.");
        }
        bool SameWindow(SavedWindow saved)
        {
            if(!Win.IsWindow(saved.Handle))return false;
            uint pid;Win.GetWindowThreadProcessId(saved.Handle,out pid);
            return pid==saved.Pid&&AllowedPath(source,Win.ProcessPath(saved.Pid));
        }
        void PresentAfterUserSelection()
        {
            if(windows.Count==0||suspended||!shade.Visible)return;
            var handle=windows[windows.Count-1].Handle;var foreground=Win.GetForegroundWindow();
            uint foregroundPid;Win.GetWindowThreadProcessId(foreground,out foregroundPid);
            bool officeInteraction=foregroundPid==(uint)Process.GetCurrentProcess().Id;
            if(!officeInteraction)foreach(var client in clients.Values)
                if((client.Windows.Exists(saved=>saved.Handle==foreground)||client.Floating.Exists(saved=>saved.Handle==foreground))&&Win.GetWindowLongPtr(foreground,-8)==office.Handle){officeInteraction=true;break;}
            if(!officeInteraction&&foreground!=handle)return;
            // Changing the owner does not itself normalize an existing hidden
            // client's z-order. Fit intentionally preserves z-order on timer
            // ticks; only the user's explicit selection presents it above
            // Office, without activation or a global topmost window.
            Win.SetWindowPos(handle,IntPtr.Zero,0,0,0,0,0x0213);
            // NOMOVE|NOSIZE|NOACTIVATE|NOOWNERZORDER, HWND_TOP (not TOPMOST).
            Win.RedrawWindow(handle,IntPtr.Zero,IntPtr.Zero,0x0481);
        }

        long Rank(IntPtr handle)
        {
            Win.Rect rect; Win.GetWindowRect(handle,out rect);
            // Rank the normal rectangle as Candidates does. A minimized main
            // window can be160x28 at -32000, below a hidden400x600 helper.
            if(Win.IsIconic(handle)) {
                var placement=new Win.Placement { Length=Marshal.SizeOf(typeof(Win.Placement)) };
                if(Win.GetWindowPlacement(handle,ref placement))rect=placement.Normal;
            }
            long rank=Math.Max(0,rect.Right-rect.Left)*(long)Math.Max(0,rect.Bottom-rect.Top);
            if(Win.IsWindowVisible(handle)&&!Win.IsIconic(handle))rank+=10000000000L;
            if(Win.GetWindowLongPtr(handle,-8)==IntPtr.Zero)rank+=100000000;
            string text=Win.Title(handle);
            if(source=="between"&&text=="Between")rank+=1000000000;
            if(source=="nateon"&&text=="NateOn"&&Win.GetWindowLongPtr(handle,-8)==IntPtr.Zero)rank+=20000000000L;
            // Kakao's main window can be minimized while an unrelated chat is
            // visible. Always start with the installed app's main screen.
            if(source=="kakao"&&(text=="카카오톡"||text=="KakaoTalk")&&Win.GetWindowLongPtr(handle,-8)==IntPtr.Zero)rank+=20000000000L;
            return rank;
        }
        static string ProcessName(string value) { return value=="between"?"between":value=="hanbiro"?"HanbiroTalk2":value=="kakao"?"KakaoTalk":"NateOnMain"; }
        static bool AllowedPath(string value,string path)
        {
            if(String.IsNullOrEmpty(path))return false;
            path=Path.GetFullPath(path);
            if(value!="between")return String.Equals(path,Executable(value),StringComparison.OrdinalIgnoreCase);
            string root=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"Between");
            string dir=Path.GetDirectoryName(path); Version version;
            return String.Equals(Path.GetFileName(path),"between.exe",StringComparison.OrdinalIgnoreCase)
                &&String.Equals(Path.GetDirectoryName(dir),root,StringComparison.OrdinalIgnoreCase)
                &&Version.TryParse(Path.GetFileName(dir),out version);
        }
        static string Executable(string value)
        {
            string local=Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            if(value=="hanbiro")return Path.Combine(local,"Programs","hanbiro-talk","HanbiroTalk2.exe");
            if(value=="nateon")return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86),"SK Communications","NATEON","BIN","NateOnMain.exe");
            if(value=="kakao")return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86),"Kakao","KakaoTalk","KakaoTalk.exe");
            string root=Path.Combine(local,"Between"),best=null; Version latest=null;
            if(Directory.Exists(root))foreach(string dir in Directory.GetDirectories(root)) {
                Version version; string exe=Path.Combine(dir,"between.exe");
                if(Version.TryParse(Path.GetFileName(dir),out version)&&File.Exists(exe)&&(latest==null||version>latest)){best=exe;latest=version;}
            }
            return best;
        }
        void ScanPids()
        {
            allowedPids.Clear(); pidScan=DateTime.UtcNow;
            foreach(var process in Process.GetProcessesByName(ProcessName(source)))using(process) {
                if(AllowedPath(source,Win.ProcessPath(process.Id)))allowedPids.Add(process.Id);
            }
        }
        List<IntPtr> Candidates(bool visibleOnly)
        {
            var result=new List<IntPtr>();
            Win.EnumWindows(delegate(IntPtr handle,IntPtr unused) {
                uint pid; Win.GetWindowThreadProcessId(handle,out pid);
                if(!allowedPids.Contains((int)pid)||!Win.IsWindow(handle)||(visibleOnly&&!Win.IsWindowVisible(handle)))return true;
                if(windows.Exists(item=>item.Handle==handle))return true;
                string text=Win.Title(handle);
                if((Win.GetWindowLong(handle,-16)&0x40000000)!=0||String.IsNullOrWhiteSpace(text))return true;
                if(source=="between"&&text!="Between")return true;
                var className=new StringBuilder(128); Win.GetClassName(handle,className,className.Capacity);
                if(className.ToString()=="#32770")return true; // File/permission dialogs keep their own modal semantics.
                if(source=="kakao"&&className.ToString()!="EVA_Window_Dblclk")return true;
                Win.Rect rect; Win.GetWindowRect(handle,out rect);
                if(Win.IsIconic(handle)){
                    var placement=new Win.Placement { Length=Marshal.SizeOf(typeof(Win.Placement)) };
                    if(Win.GetWindowPlacement(handle,ref placement))rect=placement.Normal;
                }
                if(rect.Right-rect.Left<200||rect.Bottom-rect.Top<140)return true;
                result.Add(handle);return true;
            },IntPtr.Zero);
            return result;
        }
        void Attach(IntPtr handle)
        {
            uint pid; Win.GetWindowThreadProcessId(handle,out pid);
            if(!allowedPids.Contains((int)pid)||!AllowedPath(source,Win.ProcessPath((int)pid)))throw new InvalidOperationException("원본 앱의 실행 경로가 바뀌었어요.");
            var saved=new SavedWindow { Handle=handle, Pid=(int)pid, Style=Win.GetWindowLong(handle,-16), ExStyle=Win.GetWindowLong(handle,-20), Owner=Win.GetWindowLongPtr(handle,-8) };
            saved.Placement.Length=Marshal.SizeOf(typeof(Win.Placement));
            if(!Win.GetWindowPlacement(handle,ref saved.Placement))throw new InvalidOperationException("원본 창의 복원 정보를 확보하지 못했어요.");
            windows.Add(saved); SaveReceipt("attaching");
            if(!NativeHanbiro)Win.ShowWindow(handle,0);
            // GWLP_HWNDPARENT sets an owner for a top-level window. Unlike
            // SetParent this preserves Chromium's native compositor and IME.
            Win.SetWindowLongPtr(handle,-8,office.Handle);
            if(Win.GetWindowLongPtr(handle,-8)!=office.Handle){if(Restore(saved,NativeHanbiro))windows.Remove(saved);throw new InvalidOperationException("원본 창을 에이메스에 연결하지 못했어요.");}
            int attachStyle=saved.Style&~0x00c40000&~0x21000000;
            if(source=="hanbiro"||source=="between") {
                // ShowWindow(HIDE) has already cleared WS_VISIBLE. Do not put
                // the saved visible bit back, or clear WS_MINIMIZE by hand:
                // Fit would then skip ShowWindow and Chromium can stay blank.
                // Preserve the actual hidden/state bits until the normal
                // non-activating show/restore below, changing only the frame.
                attachStyle=Win.GetWindowLong(handle,-16)&~0x00c40000;
            }
            Win.SetWindowLong(handle,-16,attachStyle);
            Win.SetWindowLong(handle,-20,(saved.ExStyle&~0x00040008)|0x00000080);
            foreach(var prior in windows)if(prior!=saved)Win.ShowWindow(prior.Handle,0);
            fittedBounds=Rectangle.Empty; Fit();
        }
        void Layout()
        {
            if(source==null)return;
            Size minimum;minimumContent.TryGetValue(source,out minimum);
            Size preferred;if(!popupSizes.TryGetValue(source,out preferred))preferred=new Size(source=="hanbiro"?820:520,686);
            int width=Math.Max(1,Math.Min(office.ClientSize.Width-32,Math.Max(preferred.Width,minimum.Width+22)));
            int height=Math.Max(1,Math.Min(office.ClientSize.Height-40,Math.Max(preferred.Height,minimum.Height+84)));
            shade.Size=new Size(width,height);
            shade.Location=Clamp(positioned?shade.Location:new Point((office.ClientSize.Width-width)/2,(office.ClientSize.Height-height)/2));
            positioned=true;
            Fit();
        }
        void Fit()
        {
            if(fitting)return;
            fitting=true;
            try { FitCore(); } finally { fitting=false; }
        }
        void FitCore()
        {
            var foreground=Win.GetForegroundWindow();uint foregroundPid;Win.GetWindowThreadProcessId(foreground,out foregroundPid);
            ((PopupCard)shade).Active=foreground==office.Handle||allowedPids.Contains((int)foregroundPid);
            back.Visible=source=="hanbiro"||windows.Count>1;
            if(windows.Count==0)return;
            IntPtr handle=windows[windows.Count-1].Handle;
            if(!Win.IsWindow(handle))return;
            if(!shade.Visible||!office.Visible||office.WindowState==FormWindowState.Minimized) {
                SuspendWindow(handle);return;
            }
            // Restore only when actually hidden/minimized, then apply the final
            // bounds. Repeated SW_SHOWNOACTIVATE after sizing can restore a
            // client's previous size and let it spill beyond the popup.
            for(int pass=0;pass<3;pass++) {
                var bounds=surface.RectangleToScreen(surface.ClientRectangle);
                bounds=Rectangle.Intersect(bounds,office.RectangleToScreen(office.ClientRectangle));
                if(bounds.Width<1||bounds.Height<1){SuspendWindow(handle);return;}
                Win.Rect actual;Win.GetWindowRect(handle,out actual);
                bool reveal=!Win.IsWindowVisible(handle)||Win.IsIconic(handle);
                if(bounds!=fittedBounds||reveal||actual.Left!=bounds.Left||actual.Top!=bounds.Top||actual.Right!=bounds.Right||actual.Bottom!=bounds.Bottom) {
                    Win.SetWindowPos(handle,IntPtr.Zero,bounds.X,bounds.Y,bounds.Width,bounds.Height,0x0234);
                    // NOACTIVATE|NOZORDER|FRAMECHANGED|NOOWNERZORDER; no SHOW.
                    if(reveal){Win.ShowWindow(handle,4);Win.SetWindowPos(handle,IntPtr.Zero,bounds.X,bounds.Y,bounds.Width,bounds.Height,0x0234);}
                    Win.GetWindowRect(handle,out actual);
                }
                int actualWidth=actual.Right-actual.Left,actualHeight=actual.Bottom-actual.Top;
                if(actualWidth>bounds.Width||actualHeight>bounds.Height) {
                    int width=Math.Max(shade.Width,actualWidth+shade.Width-surface.Width);
                    int height=Math.Max(shade.Height,actualHeight+shade.Height-surface.Height);
                    if(width>office.ClientSize.Width-16||height>office.ClientSize.Height-16) {
                        SuspendWindow(handle);
                        notice.Text="원본 메신저의 최소 크기보다 오피스 창이 작아요. 오피스 창을 넓혀주세요.";notice.Visible=true;return;
                    }
                    minimumContent[source]=new Size(actualWidth,actualHeight);
                    shade.Size=new Size(width,height);shade.Location=Clamp(shade.Location);
                    popupSizes[source]=shade.Size;
                    shade.PerformLayout();frame.PerformLayout();continue;
                }
                fittedBounds=bounds;suspended=false;return;
            }
            SuspendWindow(handle);notice.Text="원본 메신저 크기를 내부 영역에 맞추지 못했어요.";notice.Visible=true;
        }
        void SuspendWindow(IntPtr handle)
        {
            suspended=true;
            if(!NativeHanbiro){Win.ShowWindow(handle,0);return;}
            NativeReady=false;
            RestoreHanbiroAfterFailure();
        }
        void Observe()
        {
            if(opening||disposed||!shade.Visible||source==null)return;
            try {
                if(office.WindowState==FormWindowState.Minimized||!office.Visible){Fit();return;}
                while(windows.Count>0&&(!Win.IsWindow(windows[windows.Count-1].Handle)||(!suspended&&!Win.IsWindowVisible(windows[windows.Count-1].Handle)))) {
                    var old=windows[windows.Count-1]; if(!Restore(old)){ShowNotice("원본 창 복원을 확인하고 있어요.");return;} windows.RemoveAt(windows.Count-1);
                    if(windows.Count>0)Win.ShowWindow(windows[windows.Count-1].Handle,4);
                }
                if(windows.Count==0){Close();return;}
                Fit();
                if(suspended)return;
                if((DateTime.UtcNow-pidScan).TotalSeconds>2)ScanPids();
                var extra=Candidates(true);
                var active=Win.GetForegroundWindow();
                if(source=="hanbiro") {
                    var popups=clients[source].Floating;
                    bool changed=popups.RemoveAll(saved=>!SameWindow(saved))>0;
                    if(extra.Contains(active)&&!popups.Exists(saved=>saved.Handle==active))changed=TrackHanbiroPopup(active)||changed;
                    if(changed)SaveReceipt("attached");
                    return;
                }
                if(extra.Contains(active)){
                    Attach(active);
                    // Newly opened NateOn chats need the same no-activation
                    // presentation pass as the initial app connection.
                    if(source=="nateon"||source=="hanbiro"){
                        PresentAfterUserSelection();
                        Win.RedrawWindow(active,IntPtr.Zero,IntPtr.Zero,0x0481);
                    }
                    SaveReceipt("attached");
                }
            } catch(Exception e) { ShowNotice("원본 창 연결 확인: "+e.Message); }
        }
        void Back()
        {
            if(windows.Count<2)return;
            do {
                var last=windows[windows.Count-1]; if(!Restore(last)){ShowNotice("원본 창 복원을 확인하지 못했어요.");return;} windows.RemoveAt(windows.Count-1);
            } while(source=="hanbiro"&&windows.Count>1);
            fittedBounds=Rectangle.Empty; Fit(); PresentAfterUserSelection();
            if(windows.Count>0)Win.RedrawWindow(windows[windows.Count-1].Handle,IntPtr.Zero,IntPtr.Zero,0x0481);
            SaveReceipt("attached");
        }
        bool RestoreHanbiroAfterFailure()
        {
            ClientSession client;
            if(!clients.TryGetValue("hanbiro",out client))return true;
            for(int i=client.Floating.Count-1;i>=0;i--)if(Restore(client.Floating[i],true))client.Floating.RemoveAt(i);
            for(int i=windows.Count-1;i>=0;i--)if(Restore(windows[i],true))windows.RemoveAt(i);
            return windows.Count==0&&client.Floating.Count==0;
        }
        static bool Restore(SavedWindow saved,bool restoreVisibility=false)
        {
            if(!Win.IsWindow(saved.Handle))return true;
            uint pid; Win.GetWindowThreadProcessId(saved.Handle,out pid);if(pid!=saved.Pid)return true;
            bool wasVisible=restoreVisibility&&(saved.Style&0x10000000)!=0;
            if(!wasVisible)Win.ShowWindow(saved.Handle,0);
            Win.SetWindowLong(saved.Handle,-16,saved.Style);
            Win.SetWindowLong(saved.Handle,-20,saved.ExStyle);
            var owner=Win.IsWindow(saved.Owner)?saved.Owner:IntPtr.Zero;
            Win.SetWindowLongPtr(saved.Handle,-8,owner);
            if(Win.GetWindowLongPtr(saved.Handle,-8)!=owner)return false;
            var placement=saved.Placement;
            placement.Show=wasVisible?((saved.Style&0x20000000)!=0?7:4):0;
            if(!Win.SetWindowPlacement(saved.Handle,ref placement)&&wasVisible)return false;
            if(wasVisible) {
                Win.SetWindowPos(saved.Handle,IntPtr.Zero,0,0,0,0,0x0277);
                return Win.IsWindowVisible(saved.Handle);
            }
            // Keep the client logged in in the background; closing this Office
            // surface must not terminate a messenger or activate it elsewhere.
            Win.ShowWindow(saved.Handle,0);
            return true;
        }
        internal bool Close()
        {
            // Keep ownership until the already-requested client show callback
            // has run, so closing during that500ms cannot reopen it externally.
            closeVersion++;NativeReady=false;
            if(waking){closeAfterWake=true;generation++;return false;}
            generation++;
            watch.Stop();
            if(source!=null)popupSizes[source]=shade.Size;
            string pending=null;
            foreach(var pair in clients) {
                var floating=pair.Value.Floating;
                for(int i=floating.Count-1;i>=0;i--)if(Restore(floating[i],pair.Value.NativeVisible))floating.RemoveAt(i);
                var saved=pair.Value.Windows;
                for(int i=saved.Count-1;i>=0;i--)if(Restore(saved[i],pair.Value.NativeVisible))saved.RemoveAt(i);
                if(saved.Count>0||floating.Count>0)pending=pair.Key;
            }
            if(pending!=null){source=pending;windows=clients[source].Windows;ScanPids();SetHeader(source);ShowNotice("원본 창 복원이 끝나지 않아 화면을 닫지 않았어요. 닫기를 다시 눌러 주세요.");SaveReceipt("restore_pending");return false;}
            clients.Clear();windows=new List<SavedWindow>();
            shade.Visible=false; source=null;
            SaveReceipt("closed");
            return true;
        }
        void SaveReceipt(string state)
        {
            try {
                var rows=new List<object>();var popupRows=new List<object>();
                ClientSession current;
                if(source!=null&&clients.TryGetValue(source,out current))foreach(var popup in current.Floating)
                    popupRows.Add(new{handle=popup.Handle.ToInt64(),pid=popup.Pid,visible=Win.IsWindowVisible(popup.Handle),owner=Win.GetWindowLongPtr(popup.Handle,-8).ToInt64()});
                foreach(var item in windows)rows.Add(new{handle=item.Handle.ToInt64(),pid=item.Pid,style=item.Style,ex_style=item.ExStyle,owner=item.Owner.ToInt64(),parent=Win.GetParent(item.Handle).ToInt64(),visible=Win.IsWindowVisible(item.Handle),above_office=AboveOffice(item.Handle)});
                string json=new System.Web.Script.Serialization.JavaScriptSerializer().Serialize(new{state=state,source=source,native_revision=Revision,hanbiro_selection=hanbiroSelection,floating_windows=popupRows,mode="owned-native-surface",layout="compact-popup",switch_mode=switchMode,switch_elapsed_ms=switchElapsed,cached_clients=clients.Count,native_ready=NativeReady,renderer_document_visible=source=="hanbiro"?(bool?)hanbiroRendererVisible:null,notification_id=notificationId,popup=new{x=shade.Left,y=shade.Top,width=shade.Width,height=shade.Height},rendering_verified=false,office=office.Handle.ToInt64(),surface=surface.IsHandleCreated?surface.Handle.ToInt64():0,windows=rows,at_utc=DateTime.UtcNow.ToString("o")});
                string dir=Path.Combine(Program.Root,"local_backups","desktop-app");Directory.CreateDirectory(dir);
                lock(receiptLock)File.WriteAllText(Path.Combine(dir,"native-messenger-state.json"),json,Encoding.UTF8);
            } catch { /* UI remains usable if an operational receipt cannot be saved. */ }
        }
        bool AboveOffice(IntPtr handle)
        {
            // Read only: GW_HWNDPREV walks windows preceding this source.
            var prior=Win.GetWindow(handle,3);
            for(int i=0;i<4096&&prior!=IntPtr.Zero;i++,prior=Win.GetWindow(prior,3))if(prior==office.Handle)return false;
            return Win.IsWindow(handle)&&Win.IsWindow(office.Handle);
        }
        public void Dispose() { if(disposed||!Close())return;disposed=true;watch.Dispose();sources.Dispose();hints.Dispose();appIcon.Image=null;foreach(var icon in icons.Values)if(icon!=null)icon.Dispose();shade.Dispose(); }
        sealed class ClientSession { internal readonly List<SavedWindow> Windows=new List<SavedWindow>();internal readonly List<SavedWindow> Floating=new List<SavedWindow>();internal bool Revealed,NativeVisible;internal IntPtr Main;internal string MainTitle; }
        sealed class SavedWindow { internal IntPtr Handle,Owner; internal int Pid,Style,ExStyle; internal Win.Placement Placement; }

        static GraphicsPath Round(RectangleF bounds,float radius)
        {
            var path=new GraphicsPath();float d=radius*2;
            path.AddArc(bounds.Left,bounds.Top,d,d,180,90);path.AddArc(bounds.Right-d,bounds.Top,d,d,270,90);
            path.AddArc(bounds.Right-d,bounds.Bottom-d,d,d,0,90);path.AddArc(bounds.Left,bounds.Bottom-d,d,d,90,90);path.CloseFigure();return path;
        }
        static void ClipRounded(Control control,float radius)
        {
            if(control.Width<radius*2||control.Height<radius*2)return;
            using(var path=Round(new RectangleF(0,0,control.Width,control.Height),radius)){var old=control.Region;control.Region=new Region(path);if(old!=null)old.Dispose();}
        }
        sealed class PopupCard : Panel
        {
            bool active;
            internal bool Active { get{return active;}set{if(active==value)return;active=value;Invalidate();} }
            internal PopupCard(){SetStyle(ControlStyles.UserPaint|ControlStyles.AllPaintingInWmPaint|ControlStyles.OptimizedDoubleBuffer,true);}
            protected override void OnResize(EventArgs e){base.OnResize(e);if(Width<30||Height<30)return;using(var path=Round(new RectangleF(0,0,Width,Height),14)){var old=Region;Region=new Region(path);if(old!=null)old.Dispose();}Invalidate();}
            protected override void OnPaint(PaintEventArgs e){base.OnPaint(e);e.Graphics.SmoothingMode=SmoothingMode.AntiAlias;using(var path=Round(new RectangleF(1.25f,1.25f,Width-2.5f,Height-2.5f),12.75f))using(var pen=new Pen(active?Color.FromArgb(204,255,105):Color.FromArgb(101,112,91),active?2f:1.25f))e.Graphics.DrawPath(pen,path);}
        }
        sealed class ResizeGrip : Control
        {
            internal ResizeGrip(){Cursor=Cursors.SizeNWSE;AccessibleName="메신저 크기 조절";SetStyle(ControlStyles.UserPaint|ControlStyles.AllPaintingInWmPaint|ControlStyles.OptimizedDoubleBuffer,true);}
            protected override void OnPaint(PaintEventArgs e){e.Graphics.SmoothingMode=SmoothingMode.AntiAlias;using(var pen=new Pen(Color.FromArgb(156,176,124),1.5f)){pen.StartCap=LineCap.Round;pen.EndCap=LineCap.Round;e.Graphics.DrawLine(pen,5,13,13,5);e.Graphics.DrawLine(pen,10,14,14,10);}}
        }
        sealed class SourceMenuRenderer : ToolStripRenderer
        {
            protected override void OnRenderToolStripBackground(ToolStripRenderEventArgs e){e.Graphics.Clear(Color.FromArgb(32,34,37));}
            protected override void OnRenderToolStripBorder(ToolStripRenderEventArgs e){e.Graphics.SmoothingMode=SmoothingMode.AntiAlias;using(var path=Round(new RectangleF(.75f,.75f,e.ToolStrip.Width-1.5f,e.ToolStrip.Height-1.5f),9.25f))using(var pen=new Pen(Color.FromArgb(89,103,72),1))e.Graphics.DrawPath(pen,path);}
            protected override void OnRenderMenuItemBackground(ToolStripItemRenderEventArgs e){
                var g=e.Graphics;g.SmoothingMode=SmoothingMode.AntiAlias;
                var item=e.Item as ToolStripMenuItem;
                if(e.Item.Selected||(item!=null&&item.Checked))using(var path=Round(new RectangleF(1,2,e.Item.Width-2,e.Item.Height-4),7))using(var brush=new SolidBrush(e.Item.Selected?Color.FromArgb(55,65,43):Color.FromArgb(41,47,36)))g.FillPath(brush,path);
                if(e.Item.Image!=null)g.DrawImage(e.Item.Image,new Rectangle(11,11,22,22));
                TextRenderer.DrawText(g,e.Item.Text,e.Item.Font,new Rectangle(45,0,e.Item.Width-72,e.Item.Height),Color.FromArgb(242,240,232),TextFormatFlags.Left|TextFormatFlags.VerticalCenter|TextFormatFlags.SingleLine);
                if(item!=null&&item.Checked)using(var brush=new SolidBrush(Color.FromArgb(204,255,105)))g.FillEllipse(brush,e.Item.Width-21,19,6,6);
            }
            protected override void OnRenderItemText(ToolStripItemTextRenderEventArgs e){}
            protected override void OnRenderItemImage(ToolStripItemImageRenderEventArgs e){}
            protected override void OnRenderItemCheck(ToolStripItemImageRenderEventArgs e){}
            protected override void OnRenderImageMargin(ToolStripRenderEventArgs e){}
        }
        sealed class ChromeButton : Button
        {
            readonly string glyph;bool hover;
            internal ChromeButton(string kind,string name){glyph=kind;AccessibleName=name;Text="";TabStop=true;FlatStyle=FlatStyle.Flat;FlatAppearance.BorderSize=0;BackColor=Color.FromArgb(32,34,37);ForeColor=Color.FromArgb(210,214,216);Cursor=Cursors.Hand;SetStyle(ControlStyles.UserPaint|ControlStyles.AllPaintingInWmPaint|ControlStyles.OptimizedDoubleBuffer,true);}
            protected override void OnMouseEnter(EventArgs e){hover=true;Invalidate();base.OnMouseEnter(e);}
            protected override void OnMouseLeave(EventArgs e){hover=false;Invalidate();base.OnMouseLeave(e);}
            protected override void OnPaint(PaintEventArgs e){
                var g=e.Graphics;g.Clear(BackColor);g.SmoothingMode=SmoothingMode.AntiAlias;
                float x=Width/2f,y=Height/2f;
                bool labeled=glyph=="back"&&!String.IsNullOrEmpty(Text);
                if(labeled)x=16;
                if(hover)using(var path=Round(new RectangleF(x-15,y-15,30,30),9))using(var brush=new SolidBrush(Color.FromArgb(53,57,62)))g.FillPath(brush,path);
                using(var p=new Pen(hover?Color.FromArgb(204,255,105):ForeColor,1.7f)){
                    p.StartCap=LineCap.Round;p.EndCap=LineCap.Round;
                    if(glyph=="close"){g.DrawLine(p,x-4,y-4,x+4,y+4);g.DrawLine(p,x+4,y-4,x-4,y+4);}
                    else if(glyph=="menu"){g.DrawLine(p,x-4,y-2,x,y+2);g.DrawLine(p,x,y+2,x+4,y-2);}
                    else{g.DrawLine(p,x+2,y-5,x-3,y);g.DrawLine(p,x-3,y,x+2,y+5);}
                }
                if(labeled)TextRenderer.DrawText(g,Text,Font,new Rectangle(29,0,Width-34,Height),ForeColor,TextFormatFlags.Left|TextFormatFlags.VerticalCenter|TextFormatFlags.SingleLine);
                if(Focused&&ShowFocusCues)ControlPaint.DrawFocusRectangle(g,new Rectangle(3,3,Width-6,Height-6),ForeColor,BackColor);
            }
        }

        internal static class Win
        {
            internal delegate bool EnumCallback(IntPtr handle,IntPtr data);
            [StructLayout(LayoutKind.Sequential)]internal struct Rect{internal int Left,Top,Right,Bottom;}
            [StructLayout(LayoutKind.Sequential)]internal struct Point{internal int X,Y;}
            [StructLayout(LayoutKind.Sequential)]internal struct Placement{internal int Length,Flags,Show;internal Point Min,Max;internal Rect Normal;}
            [DllImport("user32.dll")]internal static extern bool EnumWindows(EnumCallback callback,IntPtr data);
            [DllImport("user32.dll")]internal static extern bool IsWindow(IntPtr h);
            [DllImport("user32.dll")]internal static extern bool IsWindowVisible(IntPtr h);
            [DllImport("user32.dll")]internal static extern bool IsIconic(IntPtr h);
            [DllImport("user32.dll")]internal static extern IntPtr GetForegroundWindow();
            [DllImport("user32.dll")]internal static extern IntPtr GetParent(IntPtr h);
            [DllImport("user32.dll")]internal static extern IntPtr GetWindow(IntPtr h,uint command);
            [DllImport("user32.dll")]internal static extern bool RedrawWindow(IntPtr h,IntPtr rect,IntPtr region,uint flags);
            [DllImport("user32.dll",EntryPoint="GetWindowLongW")]internal static extern int GetWindowLong(IntPtr h,int index);
            [DllImport("user32.dll",EntryPoint="SetWindowLongW")]internal static extern int SetWindowLong(IntPtr h,int index,int value);
            [DllImport("user32.dll",EntryPoint="GetWindowLongPtrW")]internal static extern IntPtr GetWindowLongPtr(IntPtr h,int index);
            [DllImport("user32.dll",EntryPoint="SetWindowLongPtrW")]internal static extern IntPtr SetWindowLongPtr(IntPtr h,int index,IntPtr value);
            [DllImport("user32.dll")]internal static extern bool GetWindowRect(IntPtr h,out Rect rect);
            [DllImport("user32.dll")]internal static extern bool GetWindowPlacement(IntPtr h,ref Placement placement);
            [DllImport("user32.dll")]internal static extern bool SetWindowPlacement(IntPtr h,ref Placement placement);
            [DllImport("user32.dll")]internal static extern bool SetWindowPos(IntPtr h,IntPtr after,int x,int y,int width,int height,uint flags);
            [DllImport("user32.dll")]internal static extern bool ShowWindow(IntPtr h,int command);
            [DllImport("user32.dll")]internal static extern uint GetWindowThreadProcessId(IntPtr h,out uint pid);
            [DllImport("user32.dll",CharSet=CharSet.Unicode)]internal static extern int GetWindowText(IntPtr h,StringBuilder text,int count);
            [DllImport("user32.dll",CharSet=CharSet.Unicode)]internal static extern int GetClassName(IntPtr h,StringBuilder text,int count);
            [DllImport("kernel32.dll")]static extern IntPtr OpenProcess(uint access,bool inherit,int pid);
            [DllImport("kernel32.dll",CharSet=CharSet.Unicode)]static extern bool QueryFullProcessImageName(IntPtr h,uint flags,StringBuilder text,ref int size);
            [DllImport("kernel32.dll")]static extern bool CloseHandle(IntPtr h);
            internal static string Title(IntPtr h){var text=new StringBuilder(512);GetWindowText(h,text,text.Capacity);return text.ToString();}
            internal static string ProcessPath(int pid){IntPtr h=OpenProcess(0x1000,false,pid);if(h==IntPtr.Zero)return null;try{var b=new StringBuilder(32768);int n=b.Capacity;return QueryFullProcessImageName(h,0,b,ref n)?b.ToString():null;}finally{CloseHandle(h);}}
        }
    }
}

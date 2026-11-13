using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using Forms = System.Windows.Forms;

public sealed class LoloSettings {
    public string skin = "pajamas";
    public double size = 180, opacity = 1, left = -10000, top = -10000;
    public bool motion = true, topmost = true, taskbar = true;
}

public sealed class LoloWindow : Window {
    readonly string assets, directory, statusUrl;
    readonly EventWaitHandle showEvent, stopEvent;
    readonly JavaScriptSerializer json = new JavaScriptSerializer();
    readonly Image sprite = new Image();
    readonly TranslateTransform movement = new TranslateTransform();
    readonly DispatcherTimer timer = new DispatcherTimer();
    readonly Forms.NotifyIcon tray = new Forms.NotifyIcon();
    LoloSettings prefs = new LoloSettings();
    BitmapImage sheet;
    string loadedSkin;
    Task<string> request;
    string pose = "idle";
    DateTime nextPoll = DateTime.MinValue;
    DateTime greetingUntil = DateTime.UtcNow.AddSeconds(3);
    bool wasBusy;
    Window settingsWindow;

    public LoloWindow(string assets, string directory, int port, EventWaitHandle showEvent, EventWaitHandle stopEvent) {
        this.assets = assets; this.directory = directory;
        this.showEvent = showEvent; this.stopEvent = stopEvent;
        statusUrl = "http://127.0.0.1:" + port + "/api/companion/status";
        Title = "小 lolo · 桌面陪伴";
        WindowStyle = WindowStyle.None; ResizeMode = ResizeMode.NoResize;
        AllowsTransparency = true; Background = Brushes.Transparent;
        ShowActivated = false;
        try { prefs = json.Deserialize<LoloSettings>(File.ReadAllText(Path.Combine(directory, "settings.json"), Encoding.UTF8)) ?? prefs; } catch {}
        Normalize(prefs); ApplyRequest();
        ShowInTaskbar = prefs.taskbar; Width = prefs.size; Height = prefs.size;
        Content = sprite; sprite.Stretch = Stretch.Uniform; sprite.RenderTransform = movement;
        sprite.Cursor = Cursors.Hand;
        sprite.ToolTip = "拖动小 lolo 换位置 · 右键换装和设置 · 双击打开聊天";
        MouseLeftButtonDown += delegate(object sender, MouseButtonEventArgs e) {
            if (e.ClickCount == 2) { OpenChat(); return; }
            try { DragMove(); } catch (InvalidOperationException) {}
            prefs.left = Left; prefs.top = Top; Save();
        };
        MouseRightButtonUp += delegate(object sender, MouseButtonEventArgs e) {
            ContextMenu menu = MakeMenu(); menu.PlacementTarget = this; menu.IsOpen = true; e.Handled = true;
        };
        KeyDown += delegate(object sender, KeyEventArgs e) {
            double step = Keyboard.Modifiers == ModifierKeys.Shift ? 32 : 12;
            if (e.Key == Key.Left) Left -= step;
            else if (e.Key == Key.Right) Left += step;
            else if (e.Key == Key.Up) Top -= step;
            else if (e.Key == Key.Down) Top += step;
            else if (e.Key == Key.Home) ResetPosition();
            else return;
            ClampPosition(); prefs.left = Left; prefs.top = Top; Save(); e.Handled = true;
        };
        tray.Icon = System.Drawing.SystemIcons.Application;
        tray.Text = "lolo · 双击找回桌面小人";
        Forms.ContextMenuStrip trayMenu = new Forms.ContextMenuStrip();
        trayMenu.Items.Add("显示小 lolo", null, delegate { Restore(); });
        trayMenu.Items.Add("换装与大小", null, delegate { ShowSettings(); });
        trayMenu.Items.Add("回到 Lodestar", null, delegate { OpenChat(); });
        trayMenu.Items.Add(new Forms.ToolStripSeparator());
        trayMenu.Items.Add("退出小 lolo", null, delegate { Close(); });
        tray.ContextMenuStrip = trayMenu;
        tray.DoubleClick += delegate { Restore(); };
        tray.Visible = true;
        Closed += delegate { timer.Stop(); tray.Visible = false; tray.Dispose(); if (settingsWindow != null) settingsWindow.Close(); };
        Loaded += delegate {
            if (prefs.left != -10000) { Left = prefs.left; Top = prefs.top; }
            Apply(); if (prefs.left == -10000) ResetPosition();
        };
        timer.Interval = TimeSpan.FromMilliseconds(250);
        timer.Tick += Tick; timer.Start();
    }

    public static double Bound(double value, double min, double max, double fallback) {
        return Double.IsNaN(value) || Double.IsInfinity(value) ? fallback : Math.Max(min, Math.Min(max, value));
    }
    public static void Normalize(LoloSettings p) {
        if (p.skin != "maid" && p.skin != "pajamas") p.skin = "pajamas";
        p.size = Bound(p.size, 64, 320, 180); p.opacity = Bound(p.opacity, .4, 1, 1);
        p.left = Bound(p.left, -100000, 100000, -10000); p.top = Bound(p.top, -100000, 100000, -10000);
    }
    void ApplyRequest() {
        try {
            Dictionary<string, object> value = json.Deserialize<Dictionary<string, object>>(File.ReadAllText(Path.Combine(directory, "request.json"), Encoding.UTF8));
            if (value.ContainsKey("skin")) prefs.skin = Convert.ToString(value["skin"]);
            if (value.ContainsKey("size")) prefs.size = Convert.ToDouble(value["size"]);
            if (value.ContainsKey("motion") && value["motion"] is bool) prefs.motion = (bool)value["motion"];
            Normalize(prefs);
            File.Delete(Path.Combine(directory, "request.json"));
        } catch {}
    }
    void Save() {
        try {
            string target = Path.Combine(directory, "settings.json"), temporary = target + ".tmp";
            File.WriteAllText(temporary, json.Serialize(prefs), Encoding.UTF8);
            if (File.Exists(target)) File.Replace(temporary, target, null); else File.Move(temporary, target);
        } catch {}
    }
    void Apply() {
        Normalize(prefs); Width = prefs.size; Height = prefs.size;
        Opacity = prefs.opacity; Topmost = prefs.topmost; ShowInTaskbar = prefs.taskbar;
        string path = Path.Combine(assets, prefs.skin == "maid" ? "lolo-companion.png" : "lolo-pajamas.png");
        if (sheet == null || loadedSkin != prefs.skin) {
            sheet = new BitmapImage(); sheet.BeginInit(); sheet.CacheOption = BitmapCacheOption.OnLoad;
            sheet.UriSource = new Uri(path); sheet.EndInit(); sheet.Freeze(); loadedSkin = prefs.skin;
        }
        SetPose(pose, true); Animate(); ClampPosition();
        if (prefs.left != -10000) { prefs.left = Left; prefs.top = Top; }
        Save();
    }
    void Animate() {
        movement.BeginAnimation(TranslateTransform.YProperty, null); movement.Y = 0;
        if (!prefs.motion || !SystemParameters.ClientAreaAnimation) return;
        DoubleAnimation animation = new DoubleAnimation(0, -2, TimeSpan.FromSeconds(2.6));
        animation.AutoReverse = true; animation.RepeatBehavior = RepeatBehavior.Forever;
        movement.BeginAnimation(TranslateTransform.YProperty, animation);
    }
    void SetPose(string next, bool force) {
        if (!force && next == pose) return;
        pose = next;
        int index = next == "thinking" ? 1 : next == "reading" || next == "writing" ? 2 : next == "happy" ? 3 : 0;
        int width = sheet.PixelWidth / 2, height = sheet.PixelHeight / 2;
        CroppedBitmap frame = new CroppedBitmap(sheet, new Int32Rect(index % 2 * width, index / 2 * height, width, height));
        frame.Freeze(); sprite.Source = frame;
    }
    void ClampPosition() {
        Left = Bound(Left, SystemParameters.VirtualScreenLeft, SystemParameters.VirtualScreenLeft + SystemParameters.VirtualScreenWidth - Width, SystemParameters.WorkArea.Right - Width - 25);
        Top = Bound(Top, SystemParameters.VirtualScreenTop, SystemParameters.VirtualScreenTop + SystemParameters.VirtualScreenHeight - Height, SystemParameters.WorkArea.Bottom - Height - 25);
    }
    void ResetPosition() {
        Left = SystemParameters.WorkArea.Right - Width - 25;
        Top = SystemParameters.WorkArea.Bottom - Height - 25;
        prefs.left = Left; prefs.top = Top; Save();
    }
    void Restore() { Show(); ClampPosition(); }
    void OpenChat() { Process.Start(new ProcessStartInfo(statusUrl.Replace("/api/companion/status", "/")) { UseShellExecute = true }); }
    MenuItem Item(string label, Action action) {
        MenuItem item = new MenuItem { Header = label }; item.Click += delegate { action(); }; return item;
    }
    ContextMenu MakeMenu() {
        ContextMenu menu = new ContextMenu();
        menu.Items.Add(Item("回到 Lodestar", OpenChat));
        MenuItem costumes = new MenuItem { Header = "今天穿什么" };
        MenuItem pajamas = Item("紫色睡衣 · 萌萌睡帽", delegate { prefs.skin = "pajamas"; Apply(); }); pajamas.IsChecked = prefs.skin == "pajamas";
        MenuItem maid = Item("浅紫女仆装", delegate { prefs.skin = "maid"; Apply(); }); maid.IsChecked = prefs.skin == "maid";
        costumes.Items.Add(pajamas); costumes.Items.Add(maid); menu.Items.Add(costumes);
        menu.Items.Add(Item("大小、透明度与动画…", ShowSettings));
        MenuItem pin = Item("始终在最上面", delegate { prefs.topmost = !prefs.topmost; Apply(); }); pin.IsChecked = prefs.topmost; menu.Items.Add(pin);
        menu.Items.Add(Item("回到屏幕角落", ResetPosition));
        menu.Items.Add(new Separator());
        menu.Items.Add(Item("先藏到托盘", delegate { Hide(); }));
        menu.Items.Add(Item("退出小 lolo", Close));
        return menu;
    }
    void ShowSettings() {
        if (settingsWindow != null) { settingsWindow.Activate(); return; }
        Window dialog = new Window { Title = "小 lolo 的桌面设置", Width = 360, Height = 415, ResizeMode = ResizeMode.NoResize,
            WindowStartupLocation = WindowStartupLocation.CenterScreen, Background = Brushes.White, Topmost = true };
        settingsWindow = dialog;
        StackPanel panel = new StackPanel { Margin = new Thickness(23) };
        dialog.Content = panel;
        panel.Children.Add(new TextBlock { Text = "在这里陪你～", FontSize = 21, FontWeight = FontWeights.SemiBold, Foreground = new SolidColorBrush(Color.FromRgb(112,86,149)), Margin = new Thickness(0,0,0,18) });
        TextBlock sizeLabel = new TextBlock { Text = "大小 · " + (int)prefs.size + " px" }; panel.Children.Add(sizeLabel);
        Slider size = new Slider { Minimum = 64, Maximum = 320, Value = prefs.size, TickFrequency = 4, IsSnapToTickEnabled = true, Margin = new Thickness(0,8,0,18) };
        System.Windows.Automation.AutomationProperties.SetName(size, "小 lolo 大小");
        size.ValueChanged += delegate { prefs.size = size.Value; sizeLabel.Text = "大小 · " + (int)size.Value + " px"; Apply(); };
        panel.Children.Add(size);
        TextBlock opacityLabel = new TextBlock { Text = "透明度 · " + (int)(prefs.opacity * 100) + "%" }; panel.Children.Add(opacityLabel);
        Slider opacity = new Slider { Minimum = 40, Maximum = 100, Value = prefs.opacity * 100, Margin = new Thickness(0,8,0,18) };
        System.Windows.Automation.AutomationProperties.SetName(opacity, "小 lolo 透明度");
        opacity.ValueChanged += delegate { prefs.opacity = opacity.Value / 100; opacityLabel.Text = "透明度 · " + (int)opacity.Value + "%"; Opacity = prefs.opacity; Save(); };
        panel.Children.Add(opacity);
        CheckBox motion = new CheckBox { Content = "轻轻晃动", IsChecked = prefs.motion, Margin = new Thickness(0,0,0,12) };
        motion.Click += delegate { prefs.motion = motion.IsChecked == true; Animate(); Save(); }; panel.Children.Add(motion);
        CheckBox pinned = new CheckBox { Content = "始终在其他窗口上方", IsChecked = prefs.topmost, Margin = new Thickness(0,0,0,18) };
        pinned.Click += delegate { prefs.topmost = pinned.IsChecked == true; Topmost = prefs.topmost; Save(); }; panel.Children.Add(pinned);
        CheckBox taskbar = new CheckBox { Content = "在任务栏显示入口", IsChecked = prefs.taskbar, Margin = new Thickness(0,0,0,15) };
        taskbar.Click += delegate { prefs.taskbar = taskbar.IsChecked == true; ShowInTaskbar = prefs.taskbar; Save(); }; panel.Children.Add(taskbar);
        panel.Children.Add(new TextBlock { Text = "拖动小人换位置，右键换装，双击回到聊天。", TextWrapping = TextWrapping.Wrap, FontSize = 11, Foreground = Brushes.Gray, Margin = new Thickness(0,0,0,15) });
        Button done = new Button { Content = "好啦", Height = 31, Width = 80, HorizontalAlignment = HorizontalAlignment.Right }; done.Click += delegate { dialog.Close(); }; panel.Children.Add(done);
        dialog.Closed += delegate { settingsWindow = null; };
        dialog.Show();
    }
    void Tick(object sender, EventArgs e) {
        if (stopEvent.WaitOne(0)) { Close(); return; }
        if (showEvent.WaitOne(0)) { ApplyRequest(); Apply(); Restore(); }
        if (request != null && request.IsCompleted) {
            try {
                string body = request.Result;
                Dictionary<string, object> state = json.Deserialize<Dictionary<string, object>>(body);
                string next = state.ContainsKey("pose") ? Convert.ToString(state["pose"]) : "idle";
                string runStatus = state.ContainsKey("status") ? Convert.ToString(state["status"]) : "idle";
                bool busy = next == "thinking" || next == "reading" || next == "writing";
                if (runStatus == "completed" && wasBusy) greetingUntil = DateTime.UtcNow.AddSeconds(4.5);
                if (runStatus == "cancelled" || runStatus == "error" || runStatus == "interrupted") greetingUntil = DateTime.MinValue;
                wasBusy = busy;
                SetPose(!busy && DateTime.UtcNow < greetingUntil ? "happy" : next, false);
            } catch { wasBusy = false; SetPose(DateTime.UtcNow < greetingUntil ? "happy" : "idle", false); }
            request = null;
        }
        if (request == null && DateTime.UtcNow >= nextPoll) {
            nextPoll = DateTime.UtcNow.AddSeconds(1.5);
            request = Task.Run(delegate {
                HttpWebRequest call = (HttpWebRequest)WebRequest.Create(statusUrl);
                call.Proxy = null; call.Timeout = 1100; call.ReadWriteTimeout = 1100;
                using (WebResponse response = call.GetResponse()) using (StreamReader reader = new StreamReader(response.GetResponseStream())) return reader.ReadToEnd();
            });
        }
    }
}

public static class Program {
    [STAThread]
    public static int Main(string[] args) {
        if (args.Length == 1 && args[0] == "--self-test") {
            LoloSettings test = new LoloSettings { skin = "../../oops", size = Double.NaN, opacity = 2 };
            LoloWindow.Normalize(test);
            return test.skin == "pajamas" && test.size == 180 && test.opacity == 1 ? 0 : 2;
        }
        if (args.Length != 4) return 2;
        string assets = Path.GetFullPath(args[0]), directory = Path.GetFullPath(args[1]);
        int port; if (!Int32.TryParse(args[2], out port) || port < 1 || port > 65535) return 2;
        string suffix;
        using (SHA256 hash = SHA256.Create()) suffix = BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(directory.ToLowerInvariant()))).Replace("-", "").Substring(0,20);
        bool created;
        using (Mutex mutex = new Mutex(true, "Local\\LodestarLolo_" + suffix, out created)) {
            if (!created || args[3] == "stop") {
                try { using (EventWaitHandle signal = EventWaitHandle.OpenExisting("Local\\LodestarLolo_" + (args[3] == "stop" ? "Stop_" : "Show_") + suffix)) signal.Set(); } catch (WaitHandleCannotBeOpenedException) {}
                return 0;
            }
            try {
                using (EventWaitHandle show = new EventWaitHandle(false, EventResetMode.AutoReset, "Local\\LodestarLolo_Show_" + suffix))
                using (EventWaitHandle stop = new EventWaitHandle(false, EventResetMode.AutoReset, "Local\\LodestarLolo_Stop_" + suffix)) {
                    Application app = new Application();
                    app.ShutdownMode = ShutdownMode.OnMainWindowClose;
                    LoloWindow window = new LoloWindow(assets, directory, port, show, stop);
                    app.Run(window);
                }
            } catch (Exception error) {
                try { File.WriteAllText(Path.Combine(directory, "desktop-error.log"), error.GetType().Name + ": " + error.Message); } catch {}
                return 1;
            }
            finally { mutex.ReleaseMutex(); }
        }
        return 0;
    }
}

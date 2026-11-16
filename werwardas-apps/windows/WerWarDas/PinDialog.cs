using System.Windows;
using System.Windows.Controls;

namespace WerWarDas;

/// <summary>Kleine Abfrage der PIN (für Einstellungen und Beenden).</summary>
public static class PinDialog
{
    public static bool Ask(Settings settings, string action)
    {
        if (!settings.HasPin) return true;

        var box = new PasswordBox { Margin = new Thickness(0, 8, 0, 8), Padding = new Thickness(3) };
        var ok = new Button { Content = "OK", Padding = new Thickness(18, 4, 18, 4), IsDefault = true };
        var panel = new StackPanel { Margin = new Thickness(16) };
        panel.Children.Add(new TextBlock { Text = $"PIN eingeben, um {action}:" });
        panel.Children.Add(box);
        panel.Children.Add(ok);
        ok.HorizontalAlignment = HorizontalAlignment.Right;

        var window = new Window
        {
            Title = "WerWarDas",
            Content = panel,
            Width = 300,
            SizeToContent = SizeToContent.Height,
            ResizeMode = ResizeMode.NoResize,
            WindowStartupLocation = WindowStartupLocation.CenterScreen,
            Topmost = true,
        };
        ok.Click += (_, _) => window.DialogResult = settings.CheckPin(box.Password);
        window.Loaded += (_, _) => box.Focus();
        return window.ShowDialog() == true;
    }
}

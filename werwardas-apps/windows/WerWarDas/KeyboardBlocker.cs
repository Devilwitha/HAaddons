using System;
using System.Diagnostics;
using System.Runtime.InteropServices;

namespace WerWarDas;

/// <summary>
/// Blockiert während der Sperre Tastenkombinationen zum Wegwechseln
/// (Windows-Taste, Alt+Tab, Alt+Esc, Strg+Esc, Alt+F4).
/// Strg+Alt+Entf kann von keiner normalen App blockiert werden.
/// </summary>
public sealed class KeyboardBlocker
{
    private const int WH_KEYBOARD_LL = 13;
    private const int VK_TAB = 0x09;
    private const int VK_ESCAPE = 0x1B;
    private const int VK_LWIN = 0x5B;
    private const int VK_RWIN = 0x5C;
    private const int VK_F4 = 0x73;
    private const int VK_CONTROL = 0x11;
    private const int LLKHF_ALTDOWN = 0x20;

    private delegate IntPtr LowLevelKeyboardProc(int nCode, IntPtr wParam, IntPtr lParam);

    // Feld, damit der Delegate nicht vom GC eingesammelt wird.
    private readonly LowLevelKeyboardProc _proc;
    private IntPtr _hook = IntPtr.Zero;

    public KeyboardBlocker()
    {
        _proc = HookCallback;
    }

    public void Enable()
    {
        if (_hook != IntPtr.Zero) return;
        using var module = Process.GetCurrentProcess().MainModule;
        _hook = SetWindowsHookEx(WH_KEYBOARD_LL, _proc, GetModuleHandle(module?.ModuleName), 0);
        if (_hook == IntPtr.Zero) Log.Write($"Tastatur-Hook fehlgeschlagen: {Marshal.GetLastWin32Error()}");
    }

    public void Disable()
    {
        if (_hook == IntPtr.Zero) return;
        UnhookWindowsHookEx(_hook);
        _hook = IntPtr.Zero;
    }

    private IntPtr HookCallback(int nCode, IntPtr wParam, IntPtr lParam)
    {
        if (nCode >= 0)
        {
            var info = Marshal.PtrToStructure<KBDLLHOOKSTRUCT>(lParam);
            var alt = (info.flags & LLKHF_ALTDOWN) != 0;
            var ctrl = (GetAsyncKeyState(VK_CONTROL) & 0x8000) != 0;
            var block = info.vkCode switch
            {
                VK_LWIN or VK_RWIN => true,
                VK_TAB => alt,
                VK_ESCAPE => alt || ctrl,
                VK_F4 => alt,
                _ => false,
            };
            if (block) return (IntPtr)1;
        }
        return CallNextHookEx(_hook, nCode, wParam, lParam);
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct KBDLLHOOKSTRUCT
    {
        public int vkCode;
        public int scanCode;
        public int flags;
        public int time;
        public IntPtr dwExtraInfo;
    }

    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr SetWindowsHookEx(int idHook, LowLevelKeyboardProc lpfn, IntPtr hMod, uint dwThreadId);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool UnhookWindowsHookEx(IntPtr hhk);

    [DllImport("user32.dll")]
    private static extern IntPtr CallNextHookEx(IntPtr hhk, int nCode, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern short GetAsyncKeyState(int vKey);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr GetModuleHandle(string? lpModuleName);
}

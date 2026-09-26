// Stub di lancio: sta nella root di distribuzione e avvia app\WOTSCLauncher-nuitka.exe.
// Solo WinAPI, CRT statico (/MT): nessuna dipendenza extra.
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <stdio.h>

#define TARGET_REL L"\\app\\WOTSCLauncher-nuitka.exe"

int WINAPI wWinMain(HINSTANCE hInst, HINSTANCE hPrev, LPWSTR lpCmd, int nShow) {
    (void)hInst; (void)hPrev; (void)lpCmd; (void)nShow;

    wchar_t selfDir[MAX_PATH];
    if (GetModuleFileNameW(NULL, selfDir, MAX_PATH) == 0 || GetLastError() == ERROR_INSUFFICIENT_BUFFER)
        return 1;
    wchar_t *sep = wcsrchr(selfDir, L'\\');
    if (sep == NULL)
        return 1;
    *sep = L'\0';

    wchar_t target[MAX_PATH];
    _snwprintf_s(target, _countof(target), _TRUNCATE, L"%s%s", selfDir, TARGET_REL);
    if (GetFileAttributesW(target) == INVALID_FILE_ATTRIBUTES) {
        MessageBoxW(NULL,
            L"File mancante:\napp\\WOTSCLauncher-nuitka.exe\n\nNon spostare questo exe fuori dalla sua cartella.",
            L"WOTSC Launcher", MB_OK | MB_ICONERROR);
        return 1;
    }

    // Inoltra gli argomenti (salta argv[0]) quotandoli.
    int argc = 0;
    LPWSTR *argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    wchar_t args[8192] = L"";
    if (argv != NULL) {
        for (int i = 1; i < argc; i++) {
            wcscat_s(args, _countof(args), L" \"");
            wcscat_s(args, _countof(args), argv[i]);
            wcscat_s(args, _countof(args), L"\"");
        }
        LocalFree(argv);
    }

    wchar_t cmdline[8192 + MAX_PATH];
    _snwprintf_s(cmdline, _countof(cmdline), _TRUNCATE, L"\"%s\"%s", target, args);

    wchar_t workdir[MAX_PATH];
    _snwprintf_s(workdir, _countof(workdir), _TRUNCATE, L"%s\\app", selfDir);

    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    ZeroMemory(&pi, sizeof(pi));
    if (!CreateProcessW(target, cmdline, NULL, NULL, FALSE, 0, NULL, workdir, &si, &pi)) {
        wchar_t msg[512];
        _snwprintf_s(msg, _countof(msg), _TRUNCATE,
            L"Impossibile avviare:\napp\\WOTSCLauncher-nuitka.exe\n\nErrore %lu.", GetLastError());
        MessageBoxW(NULL, msg, L"WOTSC Launcher", MB_OK | MB_ICONERROR);
        return 1;
    }
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return 0;
}

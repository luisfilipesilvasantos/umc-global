#include <windows.h>
#include <iostream>
#include <string>
#include <vector>

std::wstring GetTargetCommandLine() {
    std::wstring cmdLine = GetCommandLineW();
    bool inQuote = false;
    size_t i = 0;
    while (i < cmdLine.length()) {
        if (cmdLine[i] == L'"') {
            inQuote = !inQuote;
        } else if (cmdLine[i] == L' ' && !inQuote) {
            break;
        }
        i++;
    }
    while (i < cmdLine.length() && cmdLine[i] == L' ') {
        i++;
    }
    if (i < cmdLine.length()) {
        return cmdLine.substr(i);
    }
    return L"";
}

int wmain(int argc, wchar_t* argv[]) {
    if (argc < 2) {
        std::wcerr << L"Usage: umc_loader.exe <target_exe> [arguments...]\n";
        std::wcerr << L"Example: umc_loader.exe python main.py\n";
        return -1;
    }

    std::wstring targetCmd = GetTargetCommandLine();
    if (targetCmd.empty()) {
        std::wcerr << L"[LOADER ERROR] Target command line is empty.\n";
        return -1;
    }

    wchar_t loaderPath[MAX_PATH];
    GetModuleFileNameW(NULL, loaderPath, MAX_PATH);
    std::wstring dllPath = loaderPath;
    size_t pos = dllPath.find_last_of(L"\\/");
    if (pos != std::wstring::npos) {
        dllPath = dllPath.substr(0, pos + 1) + L"umc_hook.dll";
    } else {
        dllPath = L"umc_hook.dll";
    }

    DWORD attribs = GetFileAttributesW(dllPath.c_str());
    if (attribs == INVALID_FILE_ATTRIBUTES || (attribs & FILE_ATTRIBUTE_DIRECTORY)) {
        std::wcerr << L"[LOADER ERROR] Hook DLL not found at: " << dllPath << L"\n";
        return -1;
    }

    std::wcout << L"[LOADER INFO] Spawning target process: " << targetCmd << L"\n";
    std::wcout << L"[LOADER INFO] Injected DLL: " << dllPath << L"\n";

    STARTUPINFOW si = {0};
    si.cb = sizeof(si);
    PROCESS_INFORMATION pi = {0};

    std::vector<wchar_t> cmdBuffer(targetCmd.begin(), targetCmd.end());
    cmdBuffer.push_back(L'\0');
    BOOL success = CreateProcessW(
        nullptr,
        cmdBuffer.data(),
        nullptr,
        nullptr,
        FALSE,
        CREATE_SUSPENDED,
        nullptr,
        nullptr,
        &si,
        &pi
    );

    if (!success) {
        std::wcerr << L"[LOADER ERROR] CreateProcessW failed. Win32 Error: " << GetLastError() << L"\n";
        return -1;
    }

    std::wcout << L"[LOADER INFO] Child process spawned. PID = " << pi.dwProcessId << L"\n";

    size_t pathSize = (dllPath.length() + 1) * sizeof(wchar_t);
    void* remoteMem = VirtualAllocEx(pi.hProcess, nullptr, pathSize, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!remoteMem) {
        std::wcerr << L"[LOADER ERROR] VirtualAllocEx failed in child process. Error: " << GetLastError() << L"\n";
        TerminateProcess(pi.hProcess, -1);
        CloseHandle(pi.hProcess);
        CloseHandle(pi.hThread);
        return -1;
    }

    if (!WriteProcessMemory(pi.hProcess, remoteMem, dllPath.c_str(), pathSize, nullptr)) {
        std::wcerr << L"[LOADER ERROR] WriteProcessMemory failed in child. Error: " << GetLastError() << L"\n";
        VirtualFreeEx(pi.hProcess, remoteMem, 0, MEM_RELEASE);
        TerminateProcess(pi.hProcess, -1);
        CloseHandle(pi.hProcess);
        CloseHandle(pi.hThread);
        return -1;
    }

    HMODULE hKernel32 = GetModuleHandleW(L"kernel32.dll");
    LPTHREAD_START_ROUTINE pLoadLibraryW = (LPTHREAD_START_ROUTINE)GetProcAddress(hKernel32, "LoadLibraryW");

    HANDLE hThread = CreateRemoteThread(pi.hProcess, nullptr, 0, pLoadLibraryW, remoteMem, 0, nullptr);
    if (!hThread) {
        std::wcerr << L"[LOADER ERROR] CreateRemoteThread failed. Error: " << GetLastError() << L"\n";
        VirtualFreeEx(pi.hProcess, remoteMem, 0, MEM_RELEASE);
        TerminateProcess(pi.hProcess, -1);
        CloseHandle(pi.hProcess);
        CloseHandle(pi.hThread);
        return -1;
    }

    WaitForSingleObject(hThread, INFINITE);
    DWORD exitCode = 0;
    GetExitCodeThread(hThread, &exitCode);
    CloseHandle(hThread);
    VirtualFreeEx(pi.hProcess, remoteMem, 0, MEM_RELEASE);

    if (exitCode == 0) {
        std::wcerr << L"[LOADER ERROR] LoadLibraryW failed inside child process (returned NULL handle).\n";
        TerminateProcess(pi.hProcess, -1);
        CloseHandle(pi.hProcess);
        CloseHandle(pi.hThread);
        return -1;
    }

    std::wcout << L"[LOADER INFO] Hook DLL injected successfully. Resuming target process thread...\n";

    ResumeThread(pi.hThread);
    WaitForSingleObject(pi.hProcess, INFINITE);

    DWORD procExit = 0;
    GetExitCodeProcess(pi.hProcess, &procExit);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);

    std::wcout << L"[LOADER INFO] Target process terminated. Exit code: " << procExit << L"\n";
    return static_cast<int>(procExit);
}

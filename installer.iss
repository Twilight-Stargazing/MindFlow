#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{7D2E9B64-3C1A-4F58-9A0B-6E5D1C8F2A47}
AppName=MindFlow
AppVersion={#AppVersion}
AppPublisher=MindFlow
DefaultDirName={autopf}\MindFlow
DefaultGroupName=MindFlow
UninstallDisplayIcon={app}\MindFlow.exe
OutputDir=dist
OutputBaseFilename=MindFlow-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
; Tắt tự mở lại của Windows để tránh app bật 2 lần: mở lại do mục [Run] có /RELAUNCH=1 lo
RestartApplications=no
; Cùng AppId + cùng thư mục cài => bản mới luôn cài ĐÈ bản cũ. KHÔNG BAO GIỜ đổi AppId.
UsePreviousAppDir=yes

#if FileExists(CompilerPath + "Languages\Vietnamese.isl")
[Languages]
Name: "vi"; MessagesFile: "compiler:Languages\Vietnamese.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "Tạo biểu tượng ngoài màn hình"; Flags: unchecked
Name: "assoc"; Description: "Mở file .mmap bằng MindFlow (có thể giành quyền của app khác dùng đuôi .mmap)"; Flags: unchecked

[Files]
; Cài đè bản cũ cùng AppId. Dữ liệu người dùng nằm ở %APPDATA%\MindFlow nên được giữ nguyên.
Source: "dist\MindFlow.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\MindFlow"; Filename: "{app}\MindFlow.exe"
Name: "{autodesktop}\MindFlow"; Filename: "{app}\MindFlow.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Classes\.mmap"; ValueType: string; ValueData: "MindFlow.mmap"; Flags: uninsdeletevalue; Tasks: assoc
Root: HKCU; Subkey: "Software\Classes\MindFlow.mmap"; ValueType: string; ValueData: "Sơ đồ MindFlow"; Flags: uninsdeletekey; Tasks: assoc
Root: HKCU; Subkey: "Software\Classes\MindFlow.mmap\shell\open\command"; ValueType: string; ValueData: """{app}\MindFlow.exe"" ""%1"""; Tasks: assoc

[UninstallDelete]
; Shortcut do nút "Tạo shortcut" trong app tạo ra, bộ cài không biết nên phải xoá khi gỡ
Type: files; Name: "{autodesktop}\MindFlow.lnk"

[Run]
Filename: "{app}\MindFlow.exe"; Description: "Mở MindFlow"; Flags: nowait postinstall skipifsilent
; Khi app tự cập nhật (chạy bộ cài với /SILENT /RELAUNCH=1) thì tự mở lại app sau khi cài xong
Filename: "{app}\MindFlow.exe"; Flags: nowait; Check: NeedRelaunch

[Code]
function NeedRelaunch: Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;

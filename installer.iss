#define MyAppName "贾维斯"
#define MyAppVersion "1.2.0"
#define MyAppExeName "Jarvis.exe"

[Setup]
AppId={{8E6ED6E6-593D-4D66-9C2A-91D38F43A6B2}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Jarvis
DefaultDirName={localappdata}\Programs\Jarvis
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
OutputDir=installer
OutputBaseFilename=贾维斯安装包
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoVersion=1.2.0.0
VersionInfoDescription={#MyAppName} Windows 安装包
CloseApplications=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Files]
Source: "dist\Jarvis\*"; DestDir: "{app}"; Excludes: "*.log,*.env,_internal\*.log,_internal\.env"; Flags: ignoreversion recursesubdirs createallsubdirs
; WebView2 引导安装器只在需要时解压到临时目录，不落到安装目录。
Source: "installer\redist\MicrosoftEdgeWebView2Setup.exe"; DestDir: "{tmp}"; Flags: dontcopy

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载{#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动{#MyAppName}"; Flags: postinstall nowait skipifsilent unchecked

[Code]
const
  WebView2ClientGuid = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WebView2DownloadPage = 'https://developer.microsoft.com/microsoft-edge/webview2/';

function GetWebView2Version: String;
var
  Version: String;
begin
  Result := '';
  { 64 位系统和 32 位系统的 EdgeUpdate 注册表位置不同，都要查。 }
  if RegQueryStringValue(HKLM,
    'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\' + WebView2ClientGuid,
    'pv', Version) then
    Result := Version
  else if RegQueryStringValue(HKLM,
    'SOFTWARE\Microsoft\EdgeUpdate\Clients\' + WebView2ClientGuid,
    'pv', Version) then
    Result := Version
  else if RegQueryStringValue(HKCU,
    'Software\Microsoft\EdgeUpdate\Clients\' + WebView2ClientGuid,
    'pv', Version) then
    Result := Version;
end;

function IsWebView2Installed: Boolean;
begin
  Result := GetWebView2Version <> '';
end;

function RunWebView2Installer: Boolean;
var
  InstallerPath: String;
  ResultCode: Integer;
begin
  Result := False;
  ExtractTemporaryFile('MicrosoftEdgeWebView2Setup.exe');
  InstallerPath := ExpandConstant('{tmp}\MicrosoftEdgeWebView2Setup.exe');
  if not FileExists(InstallerPath) then
  begin
    Log('WebView2: 引导安装器解压失败');
    Exit;
  end;

  Log('WebView2: 开始静默安装引导程序');
  if not Exec(InstallerPath, '/silent /install', '',
    SW_SHOW, ewWaitUntilTerminated, ResultCode) then
  begin
    Log('WebView2: 无法启动引导安装器');
    Exit;
  end;

  Log('WebView2: 引导安装器退出码=' + IntToStr(ResultCode));
  if ResultCode <> 0 then
    Exit;

  Result := IsWebView2Installed;
end;

procedure ShowWebView2ManualHint;
begin
  MsgBox('自动安装没有成功。请打开下面的网页，下载并安装 Microsoft Edge WebView2 Runtime，然后再启动贾维斯：' + #13#10 + #13#10 +
    WebView2DownloadPage + #13#10 + #13#10 +
    '如果对方电脑不能联网，请把 WebView2 离线安装包一起发给对方。',
    mbInformation, MB_OK);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Answer: Integer;
begin
  Result := '';
  NeedsRestart := False;

  if IsWebView2Installed then
  begin
    Log('WebView2: 已检测到 Runtime，跳过安装');
    Exit;
  end;

  Log('WebView2: 未检测到 Runtime');

  { 静默安装时不能弹窗；直接尝试安装，失败则明确中止。 }
  if WizardSilent then
  begin
    if not RunWebView2Installer then
      Result := '未安装 WebView2 Runtime，静默安装已中止。';
    Exit;
  end;

  Answer := MsgBox('检测到这台电脑没有安装 Microsoft Edge WebView2 Runtime。' + #13#10 + #13#10 +
    '贾维斯的全息界面依赖它；现在自动安装吗？' + #13#10 +
    '已安装的电脑会自动跳过这一步。',
    mbConfirmation, MB_YESNO);

  if Answer = IDYES then
  begin
    if not RunWebView2Installer then
    begin
      ShowWebView2ManualHint;
      if MsgBox('WebView2 没有安装成功。是否仍然继续安装贾维斯？' + #13#10 +
        '继续安装后，界面可能白屏或无法打开。',
        mbConfirmation, MB_YESNO) = IDNO then
        Result := '安装已取消：WebView2 Runtime 未安装。';
    end;
  end
  else
  begin
    if MsgBox('不安装 WebView2 Runtime，贾维斯界面可能白屏或无法打开。' + #13#10 +
      '仍然继续安装吗？', mbConfirmation, MB_YESNO) = IDNO then
      Result := '安装已取消：未安装 WebView2 Runtime。';
  end;
end;


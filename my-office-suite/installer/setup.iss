; =============================================================================================
;  MyOffice Setup - Inno Setup 6 script                       build:  iscc installer\setup.iss
;
;  Produces one Output\MyOffice_Setup.exe for Windows 10 / 11 (x64) that
;    * carries everything MyOffice needs - the program, the Tesseract OCR engine with Urdu, Hindi and
;      English language data, and Ghostscript - so it installs offline in seconds: no downloads and no
;      other installers.  The helper programs are staged by tools\fetch_windows_deps.py (only the files
;      that are really used, debug symbols stripped) and packed with LZMA2 like everything else.  The
;      Visual C++ runtime is part of the program folder ("local deployment"), so no VC++ setup either,
;    * LibreOffice for the legacy formats (.doc, .xls, .ppt, OpenDocument) is optional (375 MB, already
;      compressed): if the official LibreOffice_<version>_Win_x86-64.msi lies next to MyOffice_Setup.exe it is
;      installed from there, offline and SHA-256 checked (task "legacypacklocal", ticked); otherwise it can be
;      downloaded (task "legacypack", unticked) - MyOffice also offers it later, when such a file is opened,
;    * installs MyOffice, desktop + Start-menu shortcuts, file associations and the language
;      chosen during setup, and offers to pin MyOffice to the taskbar (Windows asks the user to
;      confirm when MyOffice starts - Windows does not allow installers to pin silently),
;    * uninstalls completely: program folder (with the bundled helper programs), the settings/data of
;      every user profile, file associations, shortcuts, taskbar pin, temp files and firewall rule -
;      optionally also LibreOffice, if MyOffice installed it.  User documents are never touched.
;
;  Expects the PyInstaller one-folder build in ..\dist\MyOffice (see build_windows.bat) and the
;  helper programs in deps\ (python tools\fetch_windows_deps.py --out installer\deps).
;  Optional defines:  /DMyAppVersion=1.2.3  /DBuildDir=..\dist\MyOffice  /DDepsDir=C:\abs\path\deps
;                     /DLzmaThreads=1  /DSignToolName=mysign   (with a [SignTool] configured in ISCC /S)
;  Setup command line:  /MERGETASKS="legacypack"  also download and install LibreOffice (works silently)
;                       /MERGETASKS="!legacypacklocal"  do not install the LibreOffice MSI lying next to setup
;                       /NOPREREQS                never download anything
;                       /MYOFFICELANG=ur_PK       en_US | ur_PK | hi_IN
; =============================================================================================
#ifndef MyAppVersion
  #define MyAppVersion "1.6.3"
#endif
#ifndef BuildDir
  #define BuildDir "..\dist\MyOffice"
#endif
#ifndef DepsDir
  #define DepsDir AddBackslash(SourcePath) + "deps"
#endif
#ifndef LzmaThreads
  #define LzmaThreads "4"
#endif
#if !FileExists(AddBackslash(DepsDir) + "tesseract\tesseract.exe") || !FileExists(AddBackslash(DepsDir) + "gs\gswin64c.exe") || !FileExists(AddBackslash(DepsDir) + "tessdata\urd.traineddata")
  #error The bundled helper programs are missing. Run: python tools\fetch_windows_deps.py --out installer\deps
#endif
#define MyAppName         "MyOffice"
#define MyAppPublisher    "MyOffice Project"
#define MyAppURL          "https://github.com/myoffice/myoffice"
#define MyAppExeName      "MyOffice.exe"
#define MyAppUserModelID  "MyOffice.Suite"
#define MyAppMutex        "MyOffice.Suite.Running"
#define FirewallRule      "MyOffice Collaboration"
#define TessVersion       "5.5.3"
#define GSVersion         "10.08.0"

; ---- optional download: LibreOffice (Legacy Formats Pack) - same pin as app/services/legacy_pack.py --
#define LOVersion     "26.8.1"
#define LOBuild       "26.8.1.1"
#define LOUrl         "https://download.documentfoundation.org/libreoffice/stable/" + LOVersion + "/win/x86_64/LibreOffice_" + LOVersion + "_Win_x86-64.msi"
#define LOUrl2        "https://downloadarchive.documentfoundation.org/libreoffice/old/" + LOBuild + "/win/x86_64/LibreOffice_" + LOBuild + "_Win_x86-64.msi"
#define LOSha256      "c6298b10cfa1748bcbedad94c62ce771e240e5021bfd6d7a967cd571b04b5312"
#define LOSizeMB      "375"
#define LONeedMB      "1650"
#define LOProperties  "ALLUSERS=1 CREATEDESKTOPLINK=0 REGISTER_NO_MSO_TYPES=1 ISCHECKFORPRODUCTUPDATES=0 QUICKSTART=0"

; ---- file associations (must match app/services/register.py 1-to-1) ---------------------------
#dim AExt[32]
#dim AProg[32]
#dim AName[32]
#dim AIcon[32]
#dim ATask[32]
#define AExt[0]  ".docx"
#define AProg[0] "MyOffice.Writer.docx"
#define AName[0] "MyOffice Writer Document"
#define AIcon[0] "writer"
#define ATask[0] "associate"
#define AExt[1]  ".docm"
#define AProg[1] "MyOffice.Writer.docm"
#define AName[1] "MyOffice Macro-Enabled Document"
#define AIcon[1] "writer"
#define ATask[1] "associate"
#define AExt[2]  ".dotx"
#define AProg[2] "MyOffice.Writer.dotx"
#define AName[2] "MyOffice Writer Template"
#define AIcon[2] "writer"
#define ATask[2] "associate"
#define AExt[3]  ".xlsx"
#define AProg[3] "MyOffice.Sheet.xlsx"
#define AName[3] "MyOffice Spreadsheet"
#define AIcon[3] "sheet"
#define ATask[3] "associate"
#define AExt[4]  ".xlsm"
#define AProg[4] "MyOffice.Sheet.xlsm"
#define AName[4] "MyOffice Macro-Enabled Spreadsheet"
#define AIcon[4] "sheet"
#define ATask[4] "associate"
#define AExt[5]  ".xltx"
#define AProg[5] "MyOffice.Sheet.xltx"
#define AName[5] "MyOffice Spreadsheet Template"
#define AIcon[5] "sheet"
#define ATask[5] "associate"
#define AExt[6]  ".pptx"
#define AProg[6] "MyOffice.Slide.pptx"
#define AName[6] "MyOffice Presentation"
#define AIcon[6] "slide"
#define ATask[6] "associate"
#define AExt[7]  ".ppsx"
#define AProg[7] "MyOffice.Slide.ppsx"
#define AName[7] "MyOffice Slide Show"
#define AIcon[7] "slide"
#define ATask[7] "associate"
#define AExt[8]  ".potx"
#define AProg[8] "MyOffice.Slide.potx"
#define AName[8] "MyOffice Presentation Template"
#define AIcon[8] "slide"
#define ATask[8] "associate"
#define AExt[9]  ".pdf"
#define AProg[9] "MyOffice.PDF"
#define AName[9] "PDF Document"
#define AIcon[9] "pdf"
#define ATask[9] "associate"
#define AExt[10] ".movault"
#define AProg[10] "MyOffice.Vault"
#define AName[10] "MyOffice Hardware-Encrypted Vault"
#define AIcon[10] "vault"
#define ATask[10] "associate"
#define AExt[11] ".png"
#define AProg[11] "MyOffice.Photos.png"
#define AName[11] "MyOffice PNG Image"
#define AIcon[11] "photos"
#define ATask[11] "associatephotos"
#define AExt[12] ".jpg"
#define AProg[12] "MyOffice.Photos.jpg"
#define AName[12] "MyOffice JPEG Image"
#define AIcon[12] "photos"
#define ATask[12] "associatephotos"
#define AExt[13] ".jpeg"
#define AProg[13] "MyOffice.Photos.jpeg"
#define AName[13] "MyOffice JPEG Image"
#define AIcon[13] "photos"
#define ATask[13] "associatephotos"
#define AExt[14] ".webp"
#define AProg[14] "MyOffice.Photos.webp"
#define AName[14] "MyOffice WebP Image"
#define AIcon[14] "photos"
#define ATask[14] "associatephotos"
#define AExt[15] ".bmp"
#define AProg[15] "MyOffice.Photos.bmp"
#define AName[15] "MyOffice Bitmap Image"
#define AIcon[15] "photos"
#define ATask[15] "associatephotos"
#define AExt[16] ".gif"
#define AProg[16] "MyOffice.Photos.gif"
#define AName[16] "MyOffice GIF Image"
#define AIcon[16] "photos"
#define ATask[16] "associatephotos"
#define AExt[17] ".tif"
#define AProg[17] "MyOffice.Photos.tif"
#define AName[17] "MyOffice TIFF Image"
#define AIcon[17] "photos"
#define ATask[17] "associatephotos"
#define AExt[18] ".tiff"
#define AProg[18] "MyOffice.Photos.tiff"
#define AName[18] "MyOffice TIFF Image"
#define AIcon[18] "photos"
#define ATask[18] "associatephotos"
#define AExt[19] ".jfif"
#define AProg[19] "MyOffice.Photos.jfif"
#define AName[19] "MyOffice JPEG Image"
#define AIcon[19] "photos"
#define ATask[19] "associatephotos"
#define AExt[20] ".doc"
#define AProg[20] "MyOffice.Writer.doc"
#define AName[20] "MyOffice Writer 97-2003 Document"
#define AIcon[20] "writer"
#define ATask[20] "associatelegacy"
#define AExt[21] ".rtf"
#define AProg[21] "MyOffice.Writer.rtf"
#define AName[21] "Rich Text Document"
#define AIcon[21] "writer"
#define ATask[21] "associatelegacy"
#define AExt[22] ".wpt"
#define AProg[22] "MyOffice.Writer.wpt"
#define AName[22] "MyOffice Writer Template"
#define AIcon[22] "writer"
#define ATask[22] "associatelegacy"
#define AExt[23] ".odt"
#define AProg[23] "MyOffice.Writer.odt"
#define AName[23] "OpenDocument Text Document"
#define AIcon[23] "writer"
#define ATask[23] "associatelegacy"
#define AExt[24] ".xls"
#define AProg[24] "MyOffice.Sheet.xls"
#define AName[24] "MyOffice 97-2003 Spreadsheet"
#define AIcon[24] "sheet"
#define ATask[24] "associatelegacy"
#define AExt[25] ".csv"
#define AProg[25] "MyOffice.Sheet.csv"
#define AName[25] "CSV File"
#define AIcon[25] "sheet"
#define ATask[25] "associatelegacy"
#define AExt[26] ".tsv"
#define AProg[26] "MyOffice.Sheet.tsv"
#define AName[26] "TSV Spreadsheet"
#define AIcon[26] "sheet"
#define ATask[26] "associatelegacy"
#define AExt[27] ".ett"
#define AProg[27] "MyOffice.Sheet.ett"
#define AName[27] "MyOffice Spreadsheet Template"
#define AIcon[27] "sheet"
#define ATask[27] "associatelegacy"
#define AExt[28] ".ods"
#define AProg[28] "MyOffice.Sheet.ods"
#define AName[28] "OpenDocument Spreadsheet"
#define AIcon[28] "sheet"
#define ATask[28] "associatelegacy"
#define AExt[29] ".ppt"
#define AProg[29] "MyOffice.Slide.ppt"
#define AName[29] "MyOffice 97-2003 Presentation"
#define AIcon[29] "slide"
#define ATask[29] "associatelegacy"
#define AExt[30] ".dpt"
#define AProg[30] "MyOffice.Slide.dpt"
#define AName[30] "MyOffice Presentation Template"
#define AIcon[30] "slide"
#define ATask[30] "associatelegacy"
#define AExt[31] ".odp"
#define AProg[31] "MyOffice.Slide.odp"
#define AName[31] "OpenDocument Presentation"
#define AIcon[31] "slide"
#define ATask[31] "associatelegacy"
#define I

[Setup]
AppId={{6F1C2E7A-3B8D-4E59-9A41-2D7C5B9E8F10}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
AppCopyright=Copyright (C) 2026 {#MyAppPublisher}
AppComments=Writer, Spreadsheets, Presentation and PDF tools with Urdu and Hindi support
VersionInfoVersion={#MyAppVersion}
VersionInfoProductName={#MyAppName}
VersionInfoDescription={#MyAppName} Setup
VersionInfoCompany={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
DisableWelcomePage=no
DisableDirPage=no
LicenseFile=..\LICENSE
OutputDir=Output
OutputBaseFilename=MyOffice_Setup
SetupIconFile=..\resources\icons\app\myoffice.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
SetupMutex=MyOfficeSetup,Global\MyOfficeSetup
#if Ver >= EncodeVer(6,6,0)
WizardStyle=modern dynamic windows11
#else
WizardStyle=modern
#endif
WizardSizePercent=110
WizardImageFile=art\wizard-100.png,art\wizard-125.png,art\wizard-150.png,art\wizard-175.png,art\wizard-200.png,art\wizard-225.png,art\wizard-250.png
WizardSmallImageFile=art\small-100.png,art\small-125.png,art\small-150.png,art\small-175.png,art\small-200.png,art\small-225.png,art\small-250.png
#if Ver >= EncodeVer(6,6,0)
WizardImageFileDynamicDark=art\wizard-dark-100.png,art\wizard-dark-125.png,art\wizard-dark-150.png,art\wizard-dark-175.png,art\wizard-dark-200.png,art\wizard-dark-225.png,art\wizard-dark-250.png
WizardSmallImageFileDynamicDark=art\small-100.png,art\small-125.png,art\small-150.png,art\small-175.png,art\small-200.png,art\small-225.png,art\small-250.png
#endif
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
LZMANumBlockThreads={#LzmaThreads}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
; admin rights: Program Files, machine-wide file associations, the firewall rule and the optional LibreOffice MSI
PrivilegesRequired=admin
ChangesAssociations=yes
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
ShowTasksTreeLines=yes
; Hard limit from the specification: the installer must stay below 150 MB
DiskSpanning=no
#ifdef SignToolName
SignTool={#SignToolName}
SignedUninstaller=yes
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
WelcomeLabel2=This will install [name/ver] on your computer.%n%nMyOffice is a free office suite: Writer, Spreadsheets, Presentation and a complete PDF toolbox with OCR, with Urdu and Hindi support.%n%nEverything MyOffice needs is included in this setup - no internet connection is required.%n%nIt is recommended that you close all other applications before continuing.
ConfirmUninstall=Are you sure you want to completely remove %1?%n%nThe settings, add-ins, macros, backups and recovery files of every user of this computer will be deleted as well. Your own documents are not affected.
BeveledLabel={#MyAppName} {#MyAppVersion}

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "taskbarpin";  Description: "Pin MyOffice to the &taskbar (Windows asks you to confirm when MyOffice starts)"; GroupDescription: "Shortcuts:"
Name: "associate";   Description: "Open Office, PDF & Hardware Vault files (.docx, .xlsx, .pptx, .pdf, .movault) with MyOffice"; GroupDescription: "File types:"
Name: "associatephotos"; Description: "Open image files (.png, .jpg, .jpeg, .webp, .bmp, .gif, .tiff) with MyOffice Photos"; GroupDescription: "File types:"
Name: "associatelegacy"; Description: "Also legacy & OpenDocument formats (.doc, .xls, .ppt, .odt, .ods, .odp, .rtf, .csv, .wpt, .ett, .dpt)"; GroupDescription: "File types:"
Name: "firewall";    Description: "Allow real-time LAN collaboration & Team Chat through Windows Firewall (Wi-Fi & Ethernet)"; GroupDescription: "Network:"
Name: "legacypacklocal"; Description: "Install LibreOffice {#LOVersion} for old .doc, .xls and .ppt files from the installer file next to this setup (no internet needed)"; GroupDescription: "Old file formats:"; Check: (not LibreOfficeInstalled) and LocalLibreOfficeAvailable
Name: "legacypack";  Description: "Download LibreOffice {#LOVersion} for old .doc, .xls and .ppt files ({#LOSizeMB} MB - MyOffice can also add it later, when you open such a file)"; GroupDescription: "Optional download:"; Flags: unchecked; Check: (not LibreOfficeInstalled) and (not LocalLibreOfficeAvailable)

[InstallDelete]
; a new version must not run with stale modules or helper files of the previous one
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\tesseract"
Type: filesandordirs; Name: "{app}\gs"
Type: filesandordirs; Name: "{app}\tessdata"
Type: filesandordirs; Name: "{app}\licenses"

[Files]
Source: "{#BuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; helper programs carried inside the setup (see tools\fetch_windows_deps.py and licenses\THIRD_PARTY_PROGRAMS.txt)
Source: "{#DepsDir}\tesseract\*"; DestDir: "{app}\tesseract"; Flags: ignoreversion
Source: "{#DepsDir}\tessdata\*"; DestDir: "{app}\tessdata"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#DepsDir}\gs\*"; DestDir: "{app}\gs"; Flags: ignoreversion
Source: "{#DepsDir}\licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; the AppUserModelID equals the one MyOffice.exe sets, so taskbar grouping and pinning use this entry
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; AppUserModelID: "{#MyAppUserModelID}"; Comment: "Writer, Spreadsheets, Presentation and PDF tools"
Name: "{autoprograms}\{#MyAppName} Apps\MyOffice Writer"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--new writer"; IconFilename: "{app}\_internal\resources\icons\app\writer.ico"; Comment: "New document"
Name: "{autoprograms}\{#MyAppName} Apps\MyOffice Spreadsheets"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--new sheet"; IconFilename: "{app}\_internal\resources\icons\app\sheet.ico"; Comment: "New spreadsheet"
Name: "{autoprograms}\{#MyAppName} Apps\MyOffice Presentation"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--new slide"; IconFilename: "{app}\_internal\resources\icons\app\slide.ico"; Comment: "New presentation"
Name: "{autoprograms}\{#MyAppName} Apps\MyOffice PDF"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--new pdf"; IconFilename: "{app}\_internal\resources\icons\app\pdf.ico"; Comment: "Open a PDF"
Name: "{autoprograms}\{#MyAppName} Apps\MyOffice Photos"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--new photos"; IconFilename: "{app}\_internal\resources\icons\app\photos.ico"; Comment: "Open MyOffice Photos"
Name: "{autoprograms}\{#MyAppName} Apps\Uninstall MyOffice"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; AppUserModelID: "{#MyAppUserModelID}"; Comment: "Writer, Spreadsheets, Presentation and PDF tools"; Tasks: desktopicon

[Registry]
; ---- application registration: Default apps, Open with, Win+R "MyOffice" ----
Root: HKA; Subkey: "Software\MyOffice"; ValueType: string; ValueName: "InstallDir"; ValueData: "{app}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\MyOffice"; ValueType: string; ValueName: "Version"; ValueData: "{#MyAppVersion}"
Root: HKA; Subkey: "Software\MyOffice"; ValueType: string; ValueName: "Language"; ValueData: "{code:GetLanguageCode}"
Root: HKA; Subkey: "Software\MyOffice"; ValueType: string; ValueName: "PinToTaskbar"; ValueData: "1"; Tasks: taskbarpin
Root: HKA; Subkey: "Software\MyOffice"; ValueType: none; ValueName: "PinToTaskbar"; Flags: deletevalue; Tasks: not taskbarpin
Root: HKA; Subkey: "Software\MyOffice\Capabilities"; ValueType: string; ValueName: "ApplicationName"; ValueData: "{#MyAppName}"
Root: HKA; Subkey: "Software\MyOffice\Capabilities"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "Writer, Spreadsheets, Presentation and PDF tools"
Root: HKA; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "{#MyAppName}"; ValueData: "Software\MyOffice\Capabilities"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExeName}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#MyAppName}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExeName}\shell\open\command"; ValueType: string; ValueData: """{app}\{#MyAppExeName}"" ""%1"""
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\{#MyAppExeName}"; ValueType: string; ValueData: "{app}\{#MyAppExeName}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\{#MyAppExeName}"; ValueType: string; ValueName: "Path"; ValueData: "{app}"
; ---- myoffice:// protocol for 1-click Live Collaboration room links ----
Root: HKA; Subkey: "Software\Classes\myoffice"; ValueType: string; ValueData: "URL:MyOffice Live Collaboration Protocol"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\myoffice"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\myoffice\DefaultIcon"; ValueType: string; ValueData: "{app}\_internal\resources\icons\app\myoffice.ico"
Root: HKA; Subkey: "Software\Classes\myoffice\shell\open\command"; ValueType: string; ValueData: """{app}\{#MyAppExeName}"" ""%1"""
; ---- ProgIDs and extensions (generated from the table at the top) ----
#sub EmitAssociation
Root: HKCU; Subkey: "Software\Classes\{#AProg[I]}\DefaultIcon"; Flags: deletekey
Root: HKA; Subkey: "Software\Classes\{#AProg[I]}"; ValueType: string; ValueData: "{#AName[I]}"; Flags: uninsdeletekey; Tasks: {#ATask[I]}
Root: HKA; Subkey: "Software\Classes\{#AProg[I]}"; ValueType: string; ValueName: "FriendlyTypeName"; ValueData: "{#AName[I]}"; Tasks: {#ATask[I]}
Root: HKA; Subkey: "Software\Classes\{#AProg[I]}\DefaultIcon"; ValueType: string; ValueData: "{app}\_internal\resources\icons\app\{#AIcon[I]}.ico"; Tasks: {#ATask[I]}
Root: HKA; Subkey: "Software\Classes\{#AProg[I]}\shell\open\command"; ValueType: string; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: {#ATask[I]}
Root: HKA; Subkey: "Software\Classes\{#AExt[I]}"; Flags: uninsdeletekeyifempty; Tasks: {#ATask[I]}
Root: HKA; Subkey: "Software\Classes\{#AExt[I]}\OpenWithProgids"; ValueType: string; ValueName: "{#AProg[I]}"; ValueData: ""; Flags: uninsdeletevalue uninsdeletekeyifempty; Tasks: {#ATask[I]}
Root: HKA; Subkey: "Software\MyOffice\Capabilities\FileAssociations"; ValueType: string; ValueName: "{#AExt[I]}"; ValueData: "{#AProg[I]}"; Tasks: {#ATask[I]}
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExeName}\SupportedTypes"; ValueType: string; ValueName: "{#AExt[I]}"; ValueData: ""; Tasks: {#ATask[I]}
#endsub
#for {I = 0; I < DimOf(AExt); I++} EmitAssociation
; default handler only where no other program has claimed the extension yet (removed on uninstall
; only if it still points to MyOffice - see RemoveOwnDefaultHandlers)
Root: HKA; Subkey: "Software\Classes\.docx"; ValueType: string; ValueData: "MyOffice.Writer.docx"; Flags: createvalueifdoesntexist; Tasks: associate
Root: HKA; Subkey: "Software\Classes\.docm"; ValueType: string; ValueData: "MyOffice.Writer.docm"; Flags: createvalueifdoesntexist; Tasks: associate
Root: HKA; Subkey: "Software\Classes\.dotx"; ValueType: string; ValueData: "MyOffice.Writer.dotx"; Flags: createvalueifdoesntexist; Tasks: associate
Root: HKA; Subkey: "Software\Classes\.xlsx"; ValueType: string; ValueData: "MyOffice.Sheet.xlsx"; Flags: createvalueifdoesntexist; Tasks: associate
Root: HKA; Subkey: "Software\Classes\.xlsm"; ValueType: string; ValueData: "MyOffice.Sheet.xlsm"; Flags: createvalueifdoesntexist; Tasks: associate
Root: HKA; Subkey: "Software\Classes\.xltx"; ValueType: string; ValueData: "MyOffice.Sheet.xltx"; Flags: createvalueifdoesntexist; Tasks: associate
Root: HKA; Subkey: "Software\Classes\.pptx"; ValueType: string; ValueData: "MyOffice.Slide.pptx"; Flags: createvalueifdoesntexist; Tasks: associate
Root: HKA; Subkey: "Software\Classes\.ppsx"; ValueType: string; ValueData: "MyOffice.Slide.ppsx"; Flags: createvalueifdoesntexist; Tasks: associate
Root: HKA; Subkey: "Software\Classes\.potx"; ValueType: string; ValueData: "MyOffice.Slide.potx"; Flags: createvalueifdoesntexist; Tasks: associate
Root: HKA; Subkey: "Software\Classes\.pdf";  ValueType: string; ValueData: "MyOffice.PDF"; Flags: createvalueifdoesntexist; Tasks: associate
Root: HKA; Subkey: "Software\Classes\.movault"; ValueType: string; ValueData: "MyOffice.Vault"; Flags: createvalueifdoesntexist; Tasks: associate
Root: HKA; Subkey: "Software\Classes\.png";  ValueType: string; ValueData: "MyOffice.Photos.png"; Flags: createvalueifdoesntexist; Tasks: associatephotos
Root: HKA; Subkey: "Software\Classes\.jpg";  ValueType: string; ValueData: "MyOffice.Photos.jpg"; Flags: createvalueifdoesntexist; Tasks: associatephotos
Root: HKA; Subkey: "Software\Classes\.jpeg"; ValueType: string; ValueData: "MyOffice.Photos.jpeg"; Flags: createvalueifdoesntexist; Tasks: associatephotos
Root: HKA; Subkey: "Software\Classes\.webp"; ValueType: string; ValueData: "MyOffice.Photos.webp"; Flags: createvalueifdoesntexist; Tasks: associatephotos
Root: HKA; Subkey: "Software\Classes\.bmp";  ValueType: string; ValueData: "MyOffice.Photos.bmp"; Flags: createvalueifdoesntexist; Tasks: associatephotos
Root: HKA; Subkey: "Software\Classes\.gif";  ValueType: string; ValueData: "MyOffice.Photos.gif"; Flags: createvalueifdoesntexist; Tasks: associatephotos
Root: HKA; Subkey: "Software\Classes\.tif";  ValueType: string; ValueData: "MyOffice.Photos.tif"; Flags: createvalueifdoesntexist; Tasks: associatephotos
Root: HKA; Subkey: "Software\Classes\.tiff"; ValueType: string; ValueData: "MyOffice.Photos.tiff"; Flags: createvalueifdoesntexist; Tasks: associatephotos
Root: HKA; Subkey: "Software\Classes\.jfif"; ValueType: string; ValueData: "MyOffice.Photos.jfif"; Flags: createvalueifdoesntexist; Tasks: associatephotos

[Run]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""{#FirewallRule}"""; Flags: runhidden; Tasks: firewall
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""{#FirewallRule}"" dir=in action=allow program=""{app}\{#MyAppExeName}"" enable=yes profile=any"; Flags: runhidden; Tasks: firewall; StatusMsg: "Allowing real-time collaboration through Windows Firewall..."
Filename: "{sys}\ie4uinit.exe"; Parameters: "-show"; Flags: runhidden; Check: FileExists(ExpandConstant('{sys}\ie4uinit.exe'))
Filename: "{app}\{#MyAppExeName}"; Description: "Launch MyOffice"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""{#FirewallRule}"""; Flags: runhidden; RunOnceId: "DelFirewallRule"

[UninstallDelete]
; everything left in the program folder (files written later, e.g. by a newer OCR language download)
Type: filesandordirs; Name: "{app}"

[Code]
const
  MUTEXES = '{#MyAppMutex},Global\{#MyAppMutex}';
  { remembered so the uninstaller can offer to remove what MyOffice added: by this setup (HKLM) ... }
  HELPERS_KEY = 'Software\MyOffice\InstalledByMyOfficeSetup';
  { ... or by MyOffice itself, Tools > Legacy Formats Pack (per user, see app/services/legacy_pack.py) }
  USER_HELPERS_KEY = 'Software\MyOffice\InstalledByMyOffice';
  UNINSTALL_ROOT = 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall';
  LO_FILE = 'LibreOffice.msi';

var
  LangPage: TInputOptionWizardPage;
  DownloadPage: TDownloadWizardPage;
  LOChecked, LOPresent, LODownloaded: Boolean;
  LocalChecked: Boolean;
  LocalMsi: String;
  RestartNeeded: Boolean;
  InstallNotes: String;
  UninstallFromApp: Boolean;
  RemoveHelpers: Boolean;
  HelperKeys: String;

{ ------------------------------------------------------------------------------ helpers }
function CmdLineParamExists(const Name: String): Boolean;
var
  K: Integer;
begin
  Result := False;
  for K := 1 to ParamCount do
    if CompareText(ParamStr(K), Name) = 0 then
      Result := True;
end;

function RegStr(const Key, Name: String; var Value: String): Boolean;
begin
  Result := RegQueryStringValue(HKLM64, Key, Name, Value);
  if not Result then
    Result := RegQueryStringValue(HKLM32, Key, Name, Value);
end;

function ExpandEnv(const S: String): String;
begin
  Result := S;
  StringChangeEx(Result, '%SystemDrive%', GetEnv('SystemDrive'), True);
  StringChangeEx(Result, '%SystemRoot%', GetEnv('SystemRoot'), True);
  StringChangeEx(Result, '%USERPROFILE%', GetEnv('USERPROFILE'), True);
end;

{ ------------------------------------------------------------------------------ LibreOffice (optional) }
function DetectLibreOffice(var Found: String): Boolean;
var
  S: String;
begin
  Result := False;
  if RegStr('SOFTWARE\LibreOffice\UNO\InstallPath', '', S) then
    if FileExists(AddBackslash(S) + 'soffice.exe') then
    begin
      Found := AddBackslash(S) + 'soffice.exe';
      Result := True;
      Exit;
    end;
  S := ExpandConstant('{commonpf64}\LibreOffice\program\soffice.exe');
  if not FileExists(S) then
    S := ExpandConstant('{commonpf32}\LibreOffice\program\soffice.exe');
  if FileExists(S) then
  begin
    Found := S;
    Result := True;
  end;
end;

{ Check function of the "legacypack" task: the task is not offered when LibreOffice is already there }
function LibreOfficeInstalled: Boolean;
var
  F: String;
begin
  if not LOChecked then
  begin
    LOPresent := DetectLibreOffice(F);
    LOChecked := True;
    if LOPresent then
      Log('LibreOffice already installed: ' + F);
  end;
  Result := LOPresent;
end;

{ the official MSI copied next to MyOffice_Setup.exe (USB stick, network share): installed without internet }
function LocalLibreOfficeMsi: String;
var
  S: String;
begin
  if not LocalChecked then
  begin
    LocalChecked := True;
    LocalMsi := '';
    S := ExpandConstant('{src}\LibreOffice_{#LOVersion}_Win_x86-64.msi');
    if not FileExists(S) then
      S := ExpandConstant('{src}\LibreOffice_{#LOBuild}_Win_x86-64.msi');
    if FileExists(S) then
    begin
      LocalMsi := S;
      Log('LibreOffice installer found next to setup: ' + S);
    end;
  end;
  Result := LocalMsi;
end;

function LocalLibreOfficeAvailable: Boolean;
begin
  Result := LocalLibreOfficeMsi <> '';
end;

function WantLocalLibreOffice: Boolean;
begin
  Result := WizardIsTaskSelected('legacypacklocal') and not LibreOfficeInstalled and LocalLibreOfficeAvailable;
end;

function WantLibreOffice: Boolean;
begin
  Result := WizardIsTaskSelected('legacypack') and not LibreOfficeInstalled and not CmdLineParamExists('/NOPREREQS');
end;

{ ------------------------------------------------------------------------------ language page }
function DefaultLanguageIndex: Integer;
var
  Prev: String;
begin
  Prev := ExpandConstant('{param:MYOFFICELANG|}');
  if Prev = '' then
    Prev := GetPreviousData('MyOfficeLanguage', '');
  if Prev = 'ur_PK' then
    Result := 1
  else if Prev = 'hi_IN' then
    Result := 2
  else if Prev = 'en_US' then
    Result := 0
  else if (GetUILanguage and $3FF) = $20 then
    Result := 1
  else if (GetUILanguage and $3FF) = $39 then
    Result := 2
  else
    Result := 0;
end;

function GetLanguageCode(Param: String): String;
begin
  case LangPage.SelectedValueIndex of
    1: Result := 'ur_PK';
    2: Result := 'hi_IN';
  else
    Result := 'en_US';
  end;
end;

function LanguageName: String;
begin
  case LangPage.SelectedValueIndex of
    1: Result := 'Urdu';
    2: Result := 'Hindi';
  else
    Result := 'English';
  end;
end;

procedure RegisterPreviousData(PreviousDataKey: Integer);
begin
  SetPreviousData(PreviousDataKey, 'MyOfficeLanguage', GetLanguageCode(''));
end;

{ ------------------------------------------------------------------------------ wizard }
procedure InitializeWizard;
begin
  LangPage := CreateInputOptionPage(wpSelectTasks, 'MyOffice language',
    'Choose the language of MyOffice''s menus and dialogs.',
    'You can change it at any time inside MyOffice: MyOffice menu > Language.', True, False);
  LangPage.Add('English');
  LangPage.Add('Urdu   ' + #$0627#$0631#$062F#$0648);
  LangPage.Add('Hindi   ' + #$0939#$093F#$0928#$094D#$0926#$0940);
  LangPage.SelectedValueIndex := DefaultLanguageIndex;

  DownloadPage := CreateDownloadPage('Downloading LibreOffice',
    'Setup is downloading LibreOffice {#LOVersion} for the legacy formats (.doc, .xls, .ppt). Please wait.', nil);
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  while CheckForMutexes(MUTEXES) do
    if SuppressibleMsgBox('MyOffice is running.' + #13#10#13#10 +
       'Please save your work and close MyOffice, then click OK to continue.',
       mbError, MB_OKCANCEL, IDCANCEL) = IDCANCEL then
    begin
      Result := False;
      Exit;
    end;
end;

{ inserts a paragraph before "Click Finish to exit Setup." on the last page }
procedure AddFinishedNote(const Note: String);
var
  S, Click: String;
  P: Integer;
begin
  S := WizardForm.FinishedLabel.Caption;
  Click := SetupMessage(msgClickFinish);
  P := Pos(Click, S);
  if P > 0 then
    S := Copy(S, 1, P - 1) + Note + #13#10#13#10 + Copy(S, P, Length(S))
  else
    S := S + #13#10#13#10 + Note;
  WizardForm.FinishedLabel.Caption := S;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
  begin
    if WizardIsTaskSelected('taskbarpin') then
      AddFinishedNote('Taskbar: when MyOffice starts, click "Pin to taskbar" on its Home page - Windows will ask you to confirm.');
    if InstallNotes <> '' then
      AddFinishedNote(InstallNotes);
    WizardForm.FinishedLabel.AdjustHeight;
    WizardForm.RunList.Top := WizardForm.FinishedLabel.Top + WizardForm.FinishedLabel.Height + ScaleY(12);
  end;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo,
  MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := MemoDirInfo + NewLine + NewLine;
  if MemoTasksInfo <> '' then
    Result := Result + MemoTasksInfo + NewLine + NewLine;
  Result := Result + 'MyOffice language:' + NewLine + Space + LanguageName + NewLine + NewLine;
  Result := Result + 'Included in this setup (nothing to download):' + NewLine +
    Space + 'MyOffice {#MyAppVersion} - Writer, Spreadsheets, Presentation, PDF toolbox' + NewLine +
    Space + 'Tesseract OCR {#TessVersion} with Urdu, Hindi and English language data' + NewLine +
    Space + 'Ghostscript {#GSVersion} for strong PDF compression' + NewLine;
  if WantLocalLibreOffice then
    Result := Result + NewLine + 'Install from the file next to this setup (no download):' + NewLine + Space +
      'LibreOffice {#LOVersion}  (' + ExtractFileName(LocalLibreOfficeMsi) + ')' + NewLine
  else if WantLibreOffice then
    Result := Result + NewLine + 'Download and install:' + NewLine + Space + 'LibreOffice {#LOVersion}  ({#LOSizeMB} MB)' + NewLine;
end;

{ ------------------------------------------------------------------------------ LibreOffice download }
function TryDownload(const Url: String): String;
begin
  Result := '';
  DownloadPage.Clear;
  DownloadPage.Add(Url, LO_FILE, '{#LOSha256}');
  try
    DownloadPage.Download;
  except
    Result := GetExceptionMessage;
  end;
end;

{ returns False when the user wants to go back or cancelled }
function DownloadLibreOffice: Boolean;
var
  Err: String;
  Choice: Integer;
  Labels: TArrayOfString;
begin
  Result := True;
  repeat
    DownloadPage.Show;
    try
      Err := TryDownload('{#LOUrl}');
      if (Err <> '') and not DownloadPage.AbortedByUser then
      begin
        Log('LibreOffice: primary download failed (' + Err + '), trying the archive mirror.');
        Err := TryDownload('{#LOUrl2}');
      end;
    finally
      DownloadPage.Hide;
    end;
    if DownloadPage.AbortedByUser then
    begin
      Result := False;
      Exit;
    end;
    if Err = '' then
    begin
      LODownloaded := True;
      Log('LibreOffice downloaded.');
      Exit;
    end;
    Log('LibreOffice download failed: ' + Err);
    SetArrayLength(Labels, 3);
    Labels[0] := 'Try again';
    Labels[1] := 'Continue without it';
    Labels[2] := 'Back';
    Choice := SuppressibleTaskDialogMsgBox('LibreOffice could not be downloaded',
      'Please check your internet connection.' + #13#10#13#10 + Err + #13#10#13#10 +
      'You can also continue: MyOffice is installed completely and offers LibreOffice again when you open a .doc, .xls or .ppt file.',
      mbError, MB_YESNOCANCEL, Labels, 0, IDNO);
    if Choice = IDNO then
    begin
      InstallNotes := 'LibreOffice was not downloaded. MyOffice offers it again when you open a .doc, .xls or .ppt file.';
      Exit;
    end
    else if Choice <> IDYES then
    begin
      Result := False;
      Exit;
    end;
  until False;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpReady) and WantLibreOffice and not LODownloaded then
    Result := DownloadLibreOffice;
end;

{ ------------------------------------------------------------------------------ installation }
{ a local MSI must be exactly the pinned release: same SHA-256 as the download }
function LocalMsiIsGenuine(const Msi: String): Boolean;
var
  Hash: String;
begin
  WizardForm.StatusLabel.Caption := 'Checking the LibreOffice installer file...';
  WizardForm.FilenameLabel.Caption := Msi;
  Hash := '';
  try
    Hash := GetSHA256OfFile(Msi);
  except
    Log('Could not read ' + Msi + ': ' + GetExceptionMessage);
  end;
  Result := CompareText(Hash, '{#LOSha256}') = 0;
  if not Result then
    Log('SHA-256 of ' + Msi + ' is "' + Hash + '", expected {#LOSha256}');
end;

{ LibreOffice takes ~1.4 GB; a full disk would otherwise end in Windows Installer error 1603 }
function EnoughSpaceForLibreOffice: Boolean;
var
  Free, Total: Int64;
begin
  Result := True;
  if GetSpaceOnDisk64(ExpandConstant('{commonpf64}'), Free, Total) then
  begin
    Result := Free div 1048576 >= {#LONeedMB};
    if not Result then
      Log(Format('Only %d MB free for LibreOffice, %s MB needed', [Free div 1048576, '{#LONeedMB}']));
  end;
end;

procedure InstallLibreOffice(const Msi: String; const IsLocal: Boolean);
var
  Code: Integer;
  Ok: Boolean;
  Tmp, F, Err: String;
begin
  Tmp := ExpandConstant('{tmp}\');
  WizardForm.ProgressGauge.Style := npbstMarquee;
  try
    if IsLocal and not LocalMsiIsGenuine(Msi) then
    begin
      InstallNotes := 'LibreOffice was not installed: the file next to setup is damaged or not LibreOffice {#LOVersion}. MyOffice offers LibreOffice again when you open a .doc, .xls or .ppt file.';
      Exit;
    end;
    if not EnoughSpaceForLibreOffice then
    begin
      InstallNotes := 'LibreOffice was not installed: it needs about 1.7 GB of free disk space. MyOffice offers it again when you open a .doc, .xls or .ppt file.';
      Exit;
    end;
    WizardForm.StatusLabel.Caption := 'Installing LibreOffice {#LOVersion}...';
    WizardForm.FilenameLabel.Caption := 'This can take a few minutes - please wait.';
    Code := -1;
    Ok := Exec(ExpandConstant('{sys}\msiexec.exe'), '/i "' + Msi + '" /qn /norestart {#LOProperties} ' +
      '/L*v "' + Tmp + 'LibreOffice-install.log"', '', SW_HIDE, ewWaitUntilTerminated, Code);
    Ok := Ok and ((Code = 0) or (Code = 3010) or (Code = 1641));
  finally
    WizardForm.ProgressGauge.Style := npbstNormal;
  end;
  if (Code = 3010) or (Code = 1641) then
    RestartNeeded := True;
  F := '';
  { trust the exit code only if LibreOffice can now actually be found }
  if Ok then
    Ok := DetectLibreOffice(F);
  if Ok then
  begin
    Log('LibreOffice installed (' + F + ').');
    RegWriteStringValue(HKLM64, HELPERS_KEY, 'libreoffice', '{#LOVersion}');
  end
  else
  begin
    if Code = 1618 then
      Err := 'another installation was running'
    else
      Err := 'error ' + IntToStr(Code);
    InstallNotes := 'LibreOffice could not be installed (' + Err + '). MyOffice offers it again when you open a .doc, .xls or .ppt file.';
    Log('LibreOffice could not be installed: ' + Err);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if LODownloaded then
      InstallLibreOffice(ExpandConstant('{tmp}\') + LO_FILE, False)
    else if WantLocalLibreOffice then
      InstallLibreOffice(LocalLibreOfficeMsi, True);
  end;
end;

function NeedRestart(): Boolean;
begin
  Result := RestartNeeded;
end;

{ ============================================================================== uninstall }
procedure DeleteAppShortcuts(const Folder: String);
var
  D: String;
begin
  if Folder = '' then
    Exit;
  D := AddBackslash(Folder);
  { names used by Tools > Create Desktop Shortcuts (app/services/shortcuts.py) }
  DeleteFile(D + 'MyOffice.lnk');
  DeleteFile(D + 'MyOffice Writer.lnk');
  DeleteFile(D + 'MyOffice Spreadsheets.lnk');
  DeleteFile(D + 'MyOffice Presentation.lnk');
  DeleteFile(D + 'MyOffice PDF.lnk');
end;

procedure DeleteTempEntries(const TempDir: String);
var
  FindRec: TFindRec;
  Base: String;
begin
  Base := AddBackslash(TempDir);
  if FindFirst(Base + 'myoffice_*', FindRec) then
  begin
    try
      repeat
        if FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY <> 0 then
          DelTree(Base + FindRec.Name, True, True, True)
        else
          DeleteFile(Base + FindRec.Name);
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

procedure CleanProfile(const Profile, Desktop: String);
var
  P: String;
begin
  if (Profile = '') or not DirExists(Profile) then
    Exit;
  P := AddBackslash(Profile);
  Log('Removing MyOffice data from ' + Profile);
  DelTree(P + 'AppData\Roaming\MyOffice', True, True, True);
  DelTree(P + 'AppData\Local\MyOffice', True, True, True);
  DeleteFile(P + 'AppData\Roaming\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\MyOffice.lnk');
  DelTree(P + 'AppData\Roaming\Microsoft\Windows\Start Menu\Programs\MyOffice', True, True, True);
  DeleteAppShortcuts(P + 'Desktop');
  DeleteAppShortcuts(Desktop);
  DeleteTempEntries(P + 'AppData\Local\Temp');
end;

procedure DeleteProgIdValue(const Root: Integer; const Key, ProgId: String);
begin
  if RegValueExists(Root, Key, ProgId) then
    RegDeleteValue(Root, Key, ProgId);
end;

{ per-user registrations made from inside MyOffice (Tools > Register File Types) }
procedure CleanUserHive(const Root: Integer; const P: String);
begin
  RegDeleteKeyIncludingSubkeys(Root, P + 'Software\MyOffice');
  RegDeleteKeyIncludingSubkeys(Root, P + 'Software\Classes\myoffice');
  RegDeleteKeyIncludingSubkeys(Root, P + 'Software\Classes\Applications\{#MyAppExeName}');
  if RegValueExists(Root, P + 'Software\RegisteredApplications', '{#MyAppName}') then
    RegDeleteValue(Root, P + 'Software\RegisteredApplications', '{#MyAppName}');
#sub EmitHiveCleanup
  RegDeleteKeyIncludingSubkeys(Root, P + 'Software\Classes\{#AProg[I]}');
  DeleteProgIdValue(Root, P + 'Software\Classes\{#AExt[I]}\OpenWithProgids', '{#AProg[I]}');
  DeleteProgIdValue(Root, P + 'Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\{#AExt[I]}\OpenWithProgids', '{#AProg[I]}');
#endsub
#for {I = 0; I < DimOf(AExt); I++} EmitHiveCleanup
end;

procedure RemoveOwnDefaultHandlers;
var
  V: String;
begin
#sub EmitDefaultCleanup
  if RegQueryStringValue(HKLM64, 'Software\Classes\{#AExt[I]}', '', V) then
    if V = '{#AProg[I]}' then
      RegDeleteValue(HKLM64, 'Software\Classes\{#AExt[I]}', '');
#endsub
#for {I = 0; I < DimOf(AExt); I++} EmitDefaultCleanup
end;

procedure CleanAllUsers;
var
  Sids: TArrayOfString;
  K: Integer;
  Path, Desktop, ProfileKey: String;
begin
  ProfileKey := 'SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList';
  { data folders, pins, shortcuts and temp files of every user profile on this computer }
  if RegGetSubkeyNames(HKLM64, ProfileKey, Sids) then
    for K := 0 to GetArrayLength(Sids) - 1 do
      if Pos('S-1-5-21-', Sids[K]) = 1 then
        if RegQueryStringValue(HKLM64, ProfileKey + '\' + Sids[K], 'ProfileImagePath', Path) then
        begin
          Desktop := '';
          RegQueryStringValue(HKEY_USERS, Sids[K] + '\Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders', 'Desktop', Desktop);
          CleanProfile(ExpandEnv(Path), Desktop);
        end;
  Desktop := '';
  RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders', 'Desktop', Desktop);
  CleanProfile(GetEnv('USERPROFILE'), Desktop);
  DelTree(ExpandConstant('{commonappdata}\MyOffice'), True, True, True);
  { per-user registry entries in every loaded user hive, plus this account }
  if RegGetSubkeyNames(HKEY_USERS, '', Sids) then
    for K := 0 to GetArrayLength(Sids) - 1 do
      if (Pos('S-1-5-21-', Sids[K]) = 1) and (Pos('_Classes', Sids[K]) = 0) then
        CleanUserHive(HKEY_USERS, Sids[K] + '\');
  CleanUserHive(HKCU, '');
  RemoveOwnDefaultHandlers;
end;

{ ---- helper programs that MyOffice Setup installed ---- }
function HelperTitle(const Key: String): String;
begin
  if Key = 'libreoffice' then
    Result := 'LibreOffice'
  else if Key = 'tesseract' then
    Result := 'Tesseract OCR'
  else if Key = 'ghostscript' then
    Result := 'Ghostscript'
  else
    Result := Key;
end;

function MatchesHelper(const Key, DisplayName: String): Boolean;
begin
  if Key = 'libreoffice' then
    Result := (Pos('LibreOffice', DisplayName) = 1) and (Pos('Help Pack', DisplayName) = 0)
  else if Key = 'tesseract' then
    Result := Pos('Tesseract-OCR', DisplayName) = 1
  else if Key = 'ghostscript' then
    Result := (Pos('GPL Ghostscript', DisplayName) = 1) or (Pos('Ghostscript', DisplayName) = 1)
  else
    Result := False;
end;

function FindUninstallEntry(const Key: String; var RootKey: Integer; var SubKey: String): Boolean;
var
  R, K: Integer;
  Names: TArrayOfString;
  DisplayName: String;
begin
  Result := False;
  for R := 0 to 1 do
  begin
    if R = 0 then
      RootKey := HKLM64
    else
      RootKey := HKLM32;
    if (not Result) and RegGetSubkeyNames(RootKey, UNINSTALL_ROOT, Names) then
      for K := 0 to GetArrayLength(Names) - 1 do
        if not Result then
          if RegQueryStringValue(RootKey, UNINSTALL_ROOT + '\' + Names[K], 'DisplayName', DisplayName) then
            if MatchesHelper(Key, DisplayName) then
            begin
              SubKey := UNINSTALL_ROOT + '\' + Names[K];
              Result := True;
            end;
    if Result then
      Exit;
  end;
end;

procedure SplitCommand(const Cmd: String; var Exe, Params: String);
var
  P: Integer;
  S: String;
begin
  S := Trim(Cmd);
  Exe := S;
  Params := '';
  if Copy(S, 1, 1) = '"' then
  begin
    Delete(S, 1, 1);
    P := Pos('"', S);
    if P > 0 then
    begin
      Exe := Copy(S, 1, P - 1);
      Params := Trim(Copy(S, P + 1, Length(S)));
    end;
  end
  else
  begin
    P := Pos('.exe', Lowercase(S));
    if P > 0 then
    begin
      Exe := Copy(S, 1, P + 3);
      Params := Trim(Copy(S, P + 4, Length(S)));
    end;
  end;
end;

{ MyOffice itself records LibreOffice (Tools > Legacy Formats Pack) per user: look in this account and in
  every loaded user hive - the uninstaller may run elevated under another account }
function UserMarkerExists(const Name: String): Boolean;
var
  Sids: TArrayOfString;
  K: Integer;
begin
  Result := RegValueExists(HKCU, USER_HELPERS_KEY, Name);
  if (not Result) and RegGetSubkeyNames(HKEY_USERS, '', Sids) then
    for K := 0 to GetArrayLength(Sids) - 1 do
      if (not Result) and (Pos('S-1-5-21-', Sids[K]) = 1) and (Pos('_Classes', Sids[K]) = 0) then
        Result := RegValueExists(HKEY_USERS, Sids[K] + '\' + USER_HELPERS_KEY, Name);
end;

procedure AddHelperCandidate(const Name: String; var List: String);
var
  RootKey: Integer;
  SubKey: String;
begin
  if Pos(Name + ',', HelperKeys) > 0 then
    Exit;
  if FindUninstallEntry(Name, RootKey, SubKey) then
  begin
    HelperKeys := HelperKeys + Name + ',';
    List := List + #13#10 + '      - ' + HelperTitle(Name);
  end;
end;

procedure AskAboutHelpers;
var
  Names: TArrayOfString;
  K: Integer;
  List: String;
begin
  RemoveHelpers := False;
  HelperKeys := '';
  List := '';
  { this setup: LibreOffice; MyOffice 1.0 setups also installed Tesseract and Ghostscript system-wide }
  if RegGetValueNames(HKLM64, HELPERS_KEY, Names) then
    for K := 0 to GetArrayLength(Names) - 1 do
      AddHelperCandidate(Names[K], List);
  if UserMarkerExists('libreoffice') then
    AddHelperCandidate('libreoffice', List);
  if HelperKeys = '' then
    Exit;
  RemoveHelpers := SuppressibleMsgBox('MyOffice also installed these free programs:' + #13#10 + List + #13#10#13#10 +
    'Other programs on this computer may use them as well.' + #13#10 + 'Do you want to remove them too?',
    mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES;
end;

procedure SetUninstallStatus(const Msg: String);
begin
  Log(Msg);
  try
    UninstallProgressForm.StatusLabel.Caption := Msg;
  except
    Log('(status label not available)');
  end;
end;

{ msiexec removes the files it installed; caches the program wrote later (LibreOffice's Python __pycache__
  folders, for example) stay behind - remove the folder once the program itself is gone }
procedure RemoveLeftoverDir(const Dir, ProgramFile: String);
begin
  if DirExists(Dir) and not FileExists(AddBackslash(Dir) + ProgramFile) then
  begin
    Log('Removing what is left of ' + Dir);
    DelTree(Dir, True, True, True);
  end;
end;

procedure RemoveHelper(const Key: String);
var
  RootKey, Code: Integer;
  SubKey, Cmd, Exe, Params, Dir: String;
  IsMsi: Cardinal;
begin
  if not FindUninstallEntry(Key, RootKey, SubKey) then
    Exit;
  Log('Removing ' + HelperTitle(Key) + ' (' + SubKey + ')');
  SetUninstallStatus('Removing ' + HelperTitle(Key) + '... This can take a minute.');
  IsMsi := 0;
  if not RegQueryDWordValue(RootKey, SubKey, 'WindowsInstaller', IsMsi) then
    IsMsi := 0;
  if IsMsi = 1 then
  begin
    Exec(ExpandConstant('{sys}\msiexec.exe'), '/x ' + ExtractFileName(SubKey) + ' /qn /norestart', '',
      SW_HIDE, ewWaitUntilTerminated, Code);
    if Key = 'libreoffice' then
      RemoveLeftoverDir(ExpandConstant('{commonpf64}\LibreOffice'), 'program\soffice.exe');
  end
  else if RegQueryStringValue(RootKey, SubKey, 'UninstallString', Cmd) then
  begin
    SplitCommand(Cmd, Exe, Params);
    Dir := RemoveBackslashUnlessRoot(ExtractFilePath(Exe));
    { NSIS: "_?=<folder>" runs the uninstaller in place, so Exec waits until it has finished;
      the uninstaller cannot delete itself then, so it and the empty folder are removed here }
    if Exec(Exe, Trim(Params + ' /S _?=' + Dir), '', SW_HIDE, ewWaitUntilTerminated, Code) then
    begin
      DeleteFile(Exe);
      RemoveDir(Dir);
      if Key = 'ghostscript' then
        RemoveDir(ExtractFileDir(Dir));
      if RegKeyExists(RootKey, SubKey) and not FileExists(Exe) then
        RegDeleteKeyIncludingSubkeys(RootKey, SubKey);
    end;
  end;
  Log(HelperTitle(Key) + ' removal finished (code ' + IntToStr(Code) + ').');
end;

function InitializeUninstall(): Boolean;
var
  Waited: Integer;
begin
  Result := True;
  UninstallFromApp := CmdLineParamExists('/FROMAPP');
  { started from MyOffice's own menu: give MyOffice a moment to close }
  Waited := 0;
  if UninstallFromApp then
    while CheckForMutexes(MUTEXES) and (Waited < 60) do
    begin
      Sleep(500);
      Waited := Waited + 1;
    end;
  while CheckForMutexes(MUTEXES) do
    if SuppressibleMsgBox('MyOffice is still running.' + #13#10#13#10 +
       'Please close all MyOffice windows, then click OK to continue.', mbError, MB_OKCANCEL, IDCANCEL) = IDCANCEL then
    begin
      Result := False;
      Exit;
    end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  case CurUninstallStep of
    usUninstall:
      begin
        AskAboutHelpers;
        SetUninstallStatus('Removing MyOffice settings and data of all users...');
        CleanAllUsers;
        { helper programs are independent of MyOffice's files: removed while the progress window is up }
        if RemoveHelpers then
        begin
          if Pos('libreoffice,', HelperKeys) > 0 then
            RemoveHelper('libreoffice');
          if Pos('tesseract,', HelperKeys) > 0 then
            RemoveHelper('tesseract');
          if Pos('ghostscript,', HelperKeys) > 0 then
            RemoveHelper('ghostscript');
        end;
      end;
    usPostUninstall:
      DelTree(ExpandConstant('{app}'), True, True, True);
    usDone:
      if UninstallFromApp then
        SuppressibleMsgBox('MyOffice was removed from your computer.', mbInformation, MB_OK, IDOK);
  end;
end;

$ErrorActionPreference = 'Stop'

$packagePath = Join-Path $PSScriptRoot '..\msi\Package.wxs'
[xml]$package = Get-Content $packagePath -Raw
$ns = [System.Xml.XmlNamespaceManager]::new($package.NameTable)
$ns.AddNamespace('w', 'http://wixtoolset.org/schemas/v4/wxs')

function Require-Node([string]$xpath, [string]$message) {
    $node = $package.SelectSingleNode($xpath, $ns)
    if (-not $node) { throw $message }
    $node
}

$label = Require-Node "/w:Wix/w:Package/w:Property[@Id='WIXUI_EXITDIALOGOPTIONALCHECKBOXTEXT' and @Value='Launch PracticeGraph']" 'missing launch checkbox label'
$default = Require-Node "/w:Wix/w:Package/w:Property[@Id='WIXUI_EXITDIALOGOPTIONALCHECKBOX' and @Value='1']" 'launch checkbox is not selected by default'
$action = Require-Node "/w:Wix/w:Package/w:CustomAction[@Id='LaunchPracticeGraph' and @FileRef='ShellExeFile' and @ExeCommand='tray' and @Execute='immediate' and @Impersonate='yes' and @Return='asyncNoWait']" 'launch action does not start the installed shell in the user context'
$publish = Require-Node "/w:Wix/w:Package/w:UI/w:Publish[@Dialog='ExitDialog' and @Control='Finish' and @Event='DoAction' and @Value='LaunchPracticeGraph' and @Condition='WIXUI_EXITDIALOGOPTIONALCHECKBOX = 1 AND NOT Installed']" 'finish button does not conditionally launch PracticeGraph through the UI sequence'
if ($package.SelectSingleNode("/w:Wix/w:Package/w:InstallExecuteSequence/w:Custom[@Action='LaunchPracticeGraph']", $ns)) {
    throw 'launch action must not run during a quiet installation'
}

Write-Host 'launch-after-install authoring is valid'

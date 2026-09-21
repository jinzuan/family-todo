# 构建《待会儿办》安卓壳（宿主 Windows 上执行）。
#
# 背景（重要）：0.5.0/0.5.1 的 APK 曾在一个未同步的副本（D:\family-todo-build）里构建，
# 结果 UpdateChecker.kt / ApiClient.latestApp 根本没进 dex，App 里没有任何自更新代码，
# 所以无论服务端 latest.json 升到多少 versionCode 都不会弹窗。
# 本脚本在 assemble 之前先校验关键源码存在且已接线，避免再次用旧版源码打包。
Set-Location 'D:\family-todo-build\android'
$env:JAVA_HOME = "C:\Program Files\Eclipse Adoptium\jdk-17.0.17.10-hotspot"
$env:ANDROID_HOME = "C:\Users\35256\AppData\Local\Android\Sdk"
$env:Path = "$env:JAVA_HOME\bin;$env:Path"

$updateChecker = 'app\src\main\java\com\familytodo\app\UpdateChecker.kt'
$mainActivity = 'app\src\main\java\com\familytodo\app\MainActivity.kt'
if (-not (Test-Path $updateChecker)) {
    throw "构建源码缺少 $updateChecker —— 当前目录不是最新单源，请先 git pull / 同步后再构建。"
}
if (-not (Select-String -Path $mainActivity -Pattern 'UpdateChecker.checkOnLaunch' -Quiet)) {
    throw "MainActivity.kt 未接入 UpdateChecker.checkOnLaunch —— 源码不是最新，请先同步后再构建。"
}

& cmd /c 'gradlew.bat assembleDebug --no-daemon'
'BUILD_DONE exit=' + $LASTEXITCODE

# 《待会儿办》安卓原生壳 + 桌面小组件（M3）

这是「接收端」的安卓原生壳：**WebView 承载 M2 网页界面**，桌面放一个**小组件，点一下直接把待办勾掉**。

壳很薄、基本不更新；界面与业务逻辑都在服务器网页里，改网页即更新。

---

## 一、环境要求

- **Android Studio**：Hedgehog（2023.1.1）或更新版本（自带 JDK 17）
- **JDK**：17
- **Gradle**：8.2+（用 Android Studio 打开工程时由 IDE 自动下载，无需手装）
- **Android SDK**：compileSdk / targetSdk **34**，minSdk **26**（Android 8.0）

> 本工程刻意**不含 Gradle Wrapper 二进制**（在无 SDK 的服务器上无法生成）。
> 用 Android Studio 打开会自动用 IDE 自带 Gradle；若要命令行构建，先执行一次
> `gradle wrapper --gradle-version 8.2` 生成 wrapper，或用系统安装的 `gradle` 命令。

---

## 二、导入与运行

1. Android Studio → **Open**，选择本 `android/` 目录（不是仓库根目录）。
2. 等待 Gradle Sync 完成（首次会下载依赖）。
3. 连接手机或启动模拟器，点 **Run ▶**。

启动后：

- 若本机**未加入家庭**，会自动打开「设置 / 加入家庭」页；
- 填入服务器地址与家庭令牌 → 点「加入家庭」；
- 加入成功后返回，WebView 会自动带上令牌进入 M2 网页；桌面小组件也共用同一令牌。

### 线上 vs 本地

默认地址用的是线上部署的服务器域名：

| 场景 | 网页地址 | 接口地址 |
|---|---|---|
| 生产（默认） | `https://YOUR_SERVER.example/` | `https://YOUR_SERVER.example` |
| 真机（同一局域网） | `http://192.168.x.x:5173/` | `http://192.168.x.x:8000` |
| 模拟器 + 本机服务器 | `http://10.0.2.2:5173/` | `http://10.0.2.2:8000` |

> 网页（5173，`src/serve.py`）与接口（8000，`server/`）通常分开。网站在 5173 上时会自动把接口指向同主机的 8000；但本 App 仍会显式写入 `ft_api`，所以按上表填对即可。

---

## 三、修改服务器地址

两种方式：

1. **App 内（推荐）**：左下角齿轮 → 设置页改「网页地址 / 接口地址」→ 保存。
2. **改默认值**：编辑 `app/src/main/java/com/familytodo/app/AppConfig.kt`
   ```kotlin
   const val DEFAULT_WEB_URL = "https://YOUR_SERVER.example/"
   const val DEFAULT_API_BASE = "https://YOUR_SERVER.example"
   ```

> 生产环境请用 HTTPS 域名。为方便局域网联调，Manifest 里开了 `usesCleartextTraffic="true"`；正式发布可关掉并改用 HTTPS。

---

## 四、构建 APK

**Android Studio**：
`Build` → `Build Bundle(s) / APK(s)` → `Build APK(s)`，产物在
`app/build/outputs/apk/debug/app-debug.apk`。

**命令行**（先确保有 Gradle）：
```bash
cd android
gradle wrapper --gradle-version 8.2   # 仅首次，生成 ./gradlew
./gradlew assembleDebug               # 产物同上
./gradlew assembleRelease             # 需要签名配置
```

正式发布请在 `app/build.gradle` 里配置 `signingConfigs`（本工程未内置密钥，避免泄漏）。

---

## 五、添加桌面小组件

1. 先把 App 装到手机上，并**至少打开一次**（确保令牌已保存）。
2. 回到桌面 → 长按空白处 → 「小组件 / Widgets」→ 找到《待会儿办》→ 拖到桌面。
3. 小组件会显示当前未完成待办：
   - **点某一条 = 直接完成**（后台发 `POST /api/todos/{id}/complete`）；
   - 点右上角**刷新图标**手动刷新；
   - 点标题栏打开 App。

> 刷新机制：WorkManager 每 15 分钟自动拉一次（Android 对周期任务的最小间隔限制），
> 加上手动刷新与操作后即时刷新。MVP 不做实时推送（推送属 M4）。

---

## 六、目录结构

```
android/
├── settings.gradle / build.gradle / gradle.properties   # Gradle 配置
└── app/
    ├── build.gradle
    └── src/main/
        ├── AndroidManifest.xml            # 权限、Activity、Widget/Service 声明
        ├── java/com/familytodo/app/
        │   ├── App.kt                     # 启动时安排小组件定时刷新
        │   ├── AppConfig.kt               # 默认地址等常量
        │   ├── TokenStore.kt              # 令牌/地址存 SharedPreferences（网页+小组件共用）
        │   ├── ApiClient.kt               # HttpURLConnection + org.json，零第三方网络库
        │   ├── Todo.kt                    # 待办实体
        │   ├── MainActivity.kt            # WebView 壳 + 注入令牌
        │   ├── SettingsActivity.kt        # 设置 / join
        │   ├── KeepAliveService.kt        # 前台服务占位（M4）
        │   └── widget/
        │       ├── TodoWidgetProvider.kt      # 小组件入口
        │       ├── TodoWidgetService.kt       # 列表数据源（RemoteViewsFactory）
        │       ├── CompleteActionReceiver.kt  # 点即勾广播
        │       └── WidgetRefreshWorker.kt     # WorkManager 定时刷新
        └── res/                            # 布局、图标、字符串、widget 配置
```

---

## 七、关键实现说明（避坑）

- **令牌共享**：`join` 得到的设备令牌存 `SharedPreferences`（`TokenStore`）。
  WebView 在页面加载完成后，用 `evaluateJavascript` 把令牌写入网页 `localStorage`
  （键名 `ft_api/ft_token/ft_device/ft_family/ft_role/ft_device_name`，与 `src/app.js` 的 `KEYS` 对齐），
  写入后 `reload()` 一次，网页即自动登录。注入用 `injected` 标志防死循环。
- **WebView 配置**：开启 JavaScript 与 DOM Storage（`domStorageEnabled`，令牌需要）；
  WebSocket 是 WebView 原生能力，加载网页后自动可用，无需额外开关。
- **点即勾**：小组件列表项点击走 `PendingIntent` 模板 + `setOnClickFillInIntent`
  把待办 id 传给 `CompleteActionReceiver`；接收器用 `goAsync()` 在后台线程发 `/complete`，
  完成后通知刷新。**Widget 不做三选一弹窗**——若父项还有未处理子项，服务端返回
  `needs_decision`，此处忽略，交由 App 内网页处理（符合任务约定）。
- **刷新**：`updatePeriodMillis="0"`，改由 WorkManager 周期任务（最短 15 分钟）+ 手动/操作后立即刷新。
- **最少依赖**：只用 AppCompat + WebKit + WorkManager；HTTP 与 JSON 用系统自带
  `HttpURLConnection` / `org.json`，避免 OkHttp/Gson 等额外依赖。
- **图标**：用矢量图 + 自适应图标（API 26+），不含二进制 PNG，源码即可构建。
- **零实名**：代码与文案不含真实人名/账号；注释为中文。

---

## 八、已知边界

- 小组件对「还有未处理子项的父待办」点击不会完成（服务端要求三选一），这是有意为之；
  请到 App 内操作。
- WebView 里若自行退出登录（网页的「退出」按钮），原生令牌不会同步清除；
  需要退出请用 App 设置页的「退出本机设备」。
- 首次构建需联网下载依赖。

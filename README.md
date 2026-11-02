Hermes watch
每 5 分钟用 curl 检查 Nous Research Hermes Mobile 的公开链接状态（GitHub Actions 定时任务，尽力而为，GitHub 繁忙时可能延迟）。
查什么
落地页 https://hermes-agent.nousresearch.com/mobile：每次重新提取安卓 Firebase 链接和 iOS TestFlight 邀请码，不沿用旧码。
TestFlight 链接：只看 HTTP 状态。404 = 失效/已移除；200 = 页面可访问，不能直接认定有名额，仍需在手机上用 TestFlight 打开核实。
安卓 Firebase 链接：未登录的 curl 只能看到跳转，永远记为“未验证”，不能判断名额满不满。Firebase 的结论只靠 30 分钟浏览器复查。
GitHub issue #147269：有新动态只记为“需浏览器核实”，不是有名额的证明。
只有和上一次 state.json 相比发生变化时，才提交状态并（配好 Telegram secrets 后）发一条 Telegram 通知。一直是 404 就不重复报警。
为什么是 Public 仓库
公开仓库的 Actions 分钟数不限量；私有仓库免费版每月只有 2000 分钟，每 5 分钟跑一次几天就用完。这个仓库只存公开链接和状态，不含任何账号、密码、token。
Telegram 通知（可选，不配也能跑）
仓库 Settings → Secrets and variables → Actions → New repository secret，加两个：
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
值直接在 GitHub 页面里填，不要发给任何人。没配这两个 secret 时，检查和状态提交照常跑，只是不发 Telegram。
注意事项
定时任务只在默认分支（main）上生效；仓库 60 天没有任何提交活动时，GitHub 会自动停掉定时任务，届时推一个空提交即可恢复。
第一次运行请用 Actions 页的 Run workflow 手动跑一次，确认 state.json 已生成且日志里是当前邀请码和 404 状态。
本仓库不做报名、不点接受、不登录任何账号。名额放开后的接受和安装，仍需本人在手机上完成。

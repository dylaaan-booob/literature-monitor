# Literature Monitor Browser Companion

这是独立于 Python package 的 Chrome Manifest V3 扩展。扩展提供安全 handoff：识别应用的 loopback 页面、绑定真实 Chrome tab、完成一次 claim，并发送已认证的 `tab_ready`。同时支持 publisher-first 导航、可见 XMU resolver 选择和任务下载证据。`tab_ready` 只表示 companion 已连接，不表示 PDF acquisition 已推进或完成。

## 在日常 Chrome 中安装

1. 使用你平时登录和阅读论文的普通 Chrome profile（Chrome 102 或更新版本）。
2. 打开 `chrome://extensions`，启用 **Developer mode**。
3. 点击 **Load unpacked**，选择本仓库根目录的 **browser_companion** 文件夹。
4. 建议将扩展固定到工具栏；点击图标打开当前任务 popup，使用明确的连接重试、下载、fallback 或 resolver choice 按钮。

文件无需构建，也无需 Node/npm。Literature Monitor Web 应用与 Zotero Desktop 分别运行；扩展不会启动任何一个程序。Zotero 写授权仍由应用的 Settings 管理。

## 当前握手与恢复

Add PDF 的 bounded preflight 成功后，应用会在普通 Chrome 打开带一次性 fragment 的 handoff URL。扩展只识别 HTTP `localhost` 或 `127.0.0.1` 下的 `/browser-handoff/<canonical UUID>`；query 必须为空。不要复制或分享该 URL，也不要向 URL query 中添加 token。

claim 成功后，content script 用 `history.replaceState` 清除 fragment，不导航、不改写页面正文。worker 使用 Chrome 的可信 sender tab ID 生成绑定，不接收页面指定的 tab 身份。初始能力不写入任何存储；后续能力只进入 `chrome.storage.session`，并限制为 `TRUSTED_CONTEXTS`，不返回 content script。

每次 claim 仅自动尝试一次 `tab_ready`。如果工具栏显示 **!**，在同一个已 claim 的 handoff tab 中打开扩展 popup，点击 **Retry companion connection**，显式重试一次 ready 通知。每个网络请求最多等待 10 秒；没有轮询、队列或自动重试。成功通知后 **!** 消失。其他 tab、其他 task 或其他 loopback origin 不能使用已有授权。

**X** 表示服务器已拒绝旧 authority，或 session 存储失败。关闭 Chrome、禁用/重新加载扩展或浏览器重启会丢失 session authority。worker 的普通 suspension/revival 会从 session 恢复同一绑定；缺失状态时不会从已清除 fragment 的 URL 重建能力，也不会重新 claim。此时需要应用先取消/失效旧 task，再建立新 task；不要重新使用已消耗的初始 URL。

应用侧的 terminal/cancel invalidation 会使旧 handoff 失效，但不会直接清除扩展的 session 状态。下一个绑定不同的新 handoff 会先向已验证的旧 origin 发起无能力值的 GET `/browser-handoff/<旧 task UUID>`：200 表示旧任务仍有效，拒绝新 claim；仅 404 才清除旧 session 状态，然后以新任务自己的初始能力和真实 sender tab 完成 fresh claim。旧 tab 已关闭或停留在其他页面都不影响此检查。旧服务器不可用、超时、redirect 或其他响应均 fail closed，保留旧状态，不发送新 claim，也不清除新页面 fragment；可稍后重新加载该新 handoff 页面重试。

## 权限与阶段边界

扩展申请 `storage`、`downloads`、`webNavigation` 和 `tabs`。`downloads` 用于启动和观察下载；`webNavigation` 只处理已 claim tab 的主 frame 导航；`tabs` 用于读取该 tab 的当前 URL，并按当前来源 origin 查询竞争 tab，保守排除无法唯一归属的下载。Chrome DownloadItem 没有 tab ID，因此只靠 URL、文件名或 MIME 不足以证明归属。查询不读取浏览历史或其他 tab 的页面内容。

host permissions 限于两个 HTTP loopback host、`https://resolver.ebsco.com/*` 与 `https://research.ebsco.com/*`。Chrome 的 host permission 不按 path 或 port 限定，因此 loopback 请求仍在 worker 中校验 origin，并只使用固定 claim/events 路径及规范旧任务 UUID 的 handoff 状态路径。content script 分别限于 handoff、FTF `/c/45yels/result` / `/redirect` 和 Research `/c/<context>/search/details/<record>`；均再次验证完整 URL，Research 只有当前已批准 XMU task/tab 才能观察记录。没有通用 publisher content script 或 `<all_urls>`。

扩展不读取、复制或导出 cookie，不存储机构密码，不接收 Zotero API key、Paper Markdown 或 workflow 写权限。它不调用、控制或集成 Zotero Connector；Connector 保持独立。所有 JavaScript 均随本目录打包，无远程代码、遥测或 analytics。

## 当前源码与已发布基线

当前源码对应 SPEC §37 下的 **v0.5.2 released** 应用，Python package metadata 为 **0.5.2**，companion manifest 独立版本保持 **0.1.0**；**v0.5.2 是最新已发布版本**。A0–A5 实现及独立阶段审查、最终集成审计和发布准备审查已完成；准备提交、annotated tag、main/tag 推送及 GitHub Release 已完成（§24.18）。独立实现审计的实际范围见 SPEC §37.10（审查环境无 Node，未独立执行 JS runtime/content suites）。单独的最终 clean-export full validation 按用户明确选择跳过，不计为已完成；不声明新增 v0.5.2 live companion/Zotero 验证。Web Add PDF 通过 installed normal Chrome 打开 handoff，不使用 dedicated profile、headless 或 Playwright，没有 production Playwright fallback。Python wheel 不会安装此独立扩展；请保留包含 browser_companion/ 的源码目录。

## DOI-bound 当前任务流程

任务只冻结 task ID、Paper UUID、normalized DOI、verified Zotero parent key 和 Server-ID。Python `navigation_plan(frozen_task)` 的 START plan 只有 `task_id`、`doi`、`direct_url`，从 canonical DOI URL `https://doi.org/<normalized-doi>` 开始，DOI 按 URL 规则编码。`xmu_fallback_url` 仅在 publisher 已明确 exhausted 时启用；login/CAPTCHA/MFA/Cloudflare 等 human wait 不构成 exhausted。

worker 通过已认证 browser event 接收可选固定命令回复：START 只能携带上述 DOI plan，PUBLISHER_EXHAUSTED 只在当前 DOI task 的 publisher 明确 exhausted 后启用，CHOOSE 只接受当前 choice ID，DOWNLOAD_CURRENT 只使用任务当前 URL。回复不包含 capability、Zotero credential、Paper 或可覆盖的 tab；执行前再次核对当前 session authority，旧 task 的响应不能重定向新 task。没有命令轮询、历史队列或任意页面控制接口。

在任务 tab 打开扩展 popup：**Publisher path exhausted — try XMU** 表示用户确认 direct publisher 路径已不可获得当前 DOI 的 PDF，先经应用验证才执行 fallback；login 等待本身不表示 exhausted。多个 resolver choices 保持顺序，并经应用确认当前 ID 后导航同一 tab。**Arm next user download** 显式 arm；**Download current URL with Chrome** 经应用返回固定命令；**Check current download** 只显式复查当前已知候选 ID，不搜索最新文件；**Login or verification needed** 将当前任务呈现为 human wait。其他 tab 或任意页面消息不能使用这些操作。

XMU adapter 只识别 exact `https://resolver.ebsco.com/c/45yels/result` 或 `/redirect` 的 OPID `45yels`、customer `s1215021`、group `main`、profile `ftf` 与匹配 DOI。result 页候选 anchor 必须位于可见语义 list/table/group 结果结构中；redirect 页使用下述 exact provider 句子。单独的 Full Text / SmartLink(s) anchor 不采用。result 页根据可见 exact Full Text / SmartLink(s) 文本或可见语义分组标题识别 choice；redirect 页只额外接受可见的 `Find this article in full text from EBSCOhost SmartLinks`（或 Full Text）provider 句子及其对应链接，仍经当前 resolver choice authority 才进入该路线。非 eligible 分组标题会拒绝该分组，排除 SearchEngines、Other、DocumentDelivery、research/tool/help 链接；href 本身不是类别证据。只取一次最长 15 秒的可见结果快照，不请求或观察私有 API。多个 eligible choices 保留页面顺序，通过 `resolver_choices` 发送 bounded ID/category/label 等待显式选择。超过六项或页面仍 busy 时拒绝采用。human wait 保持当前任务/tab；完成后可重新加载同一 resolver 页面继续观察。

下载优化只针对任务当前已观察 URL，保留 Chrome 返回的 extension download ID；不指定目标文件名，不覆盖已有文件。用户可先在所属 publisher/PDF-viewer tab 的扩展 popup 点击 **Arm next user download**（显示 **D**）；扩展核实真实 clicked tab 和当前 URL 后，在当前任务 session 保存该落地页 URL 与 arm 时间。对于普通 HTTP 下载，在 10 秒内点击落地页的 PDF 链接，下载 URL 可以不同，但 referrer 必须与已 arm 的当前落地页完全一致，所属 tab 必须仍停留在该页面；没有显式 arm 时，referrer 单独不能建立归属。原有 exact 当前导航 URL 的下载归属路径继续保留。来源/referrer/PDF target origins 的竞争 tab、其他 initiating extension、时间、URL 安全性及候选唯一性均须通过检查，且下载 complete，才发送 `download_candidate`。arm 在采用候选、拒绝归属、歧义、超时、导航或任务变更时清除；候选保留冻结的 arm 证据供 completion 复核，不能复用来采用另一个候选。没有可安全归属的 API 证据、其他 tab 在同一来源、并发候选或无法恢复的事件均 fail closed；文件继续属于用户。扩展从不删除文件或 Chrome 下载历史。它不声称每一种 PDF viewer 下载都可归属。

显式 arm 还支持同源 `blob:https://.../<UUID>` 下载：blob 的内嵌 HTTPS origin 必须等于 armed record/page origin，referrer 可以为空或精确 armed page。已批准 XMU、FullText/SmartLinks 的 Research 同记录路线还允许同记录 SPA referrer；额外只允许精确 `https://research.ebsco.com/` 根 referrer，须同时通过显式 Arm、task/same-record/category、时间窗、实际 claimed/current tab、竞争 tab、extension 来源和唯一候选检查。没有通用 same-origin trust，任意同源路径、不同 origin 根或普通 HTTP 下载的根 referrer 均不获此权限。完成归属与记录/PDF 动作证据复核后，worker 将已验证的非空 Research SPA/根 referrer 规范化为精确任务记录 navigation URL；根 referrer 本身不建立记录身份。`blob:http`、opaque/null、跨 origin、未 arm 或矛盾 referrer 均拒绝。blob token 不进入 browser event、日志或 provenance；传输使用 `transport_kind=blob`、`download_origin`，`url`/`final_url` 为 null，保留真实 HTTP navigation、下载 id 和 Chrome 本地路径。

Research/EBSCO record adapter 使用 exact Research record context 与 `div[data-auto="record-html-metadata"] > article[lang]` 内的可见 DOI。DOI 只读取 `li#DOI` 的直接文本，忽略扩展注入的 descendant link。content→worker 的 `recordEvidence` 记录 payload 只有 DOI；文献类型、期刊出版信息、年份和卷期不作为下载接纳条件，preprint/AAM/online-first 字样也不作为拒绝条件。

用户通过受信任的可见 Download entry 打开 dialog、选择 PDF 后，应在同一 task tab **Arm next user download**，并在 10 秒用户操作窗口内点击最终 Download；最终受信任的 PDF Download 点击才建立同一 record/arm 的动作证据。验证后的点击另起最多 120 秒的 provider preparation 窗口，允许远端生成 PDF 后 Chrome 才报告下载开始；必须继续满足同一 task、claimed tab、record、DOI、已批准 XMU category、navigation epoch、transport/referrer/origin、无竞争 tab 和单一候选。单独 Arm 不获得延长窗口，普通 publisher 下载仍限 10 秒。协议以 `attribution=ebsco_pdf_action`、`arm_time` 和 `action_time` 明确区分这条 blob 下载路径，`navigation_time` 保留实际导航时间；Python 独立核验任务/记录 DOI、下载归属和时间边界。可访问 modal 暂时隐藏背景时，仅保留先前可见且 DOM 未变的记录观察。新 arm 或 document navigation 清除旧 evidence；content event 与 Chrome onCreated 交错时，各自的 session 状态不会覆盖对方。

`/viewer/pdf/...` 不是据此可推断的 raw PDF endpoint。此路线使用用户可见 PDF Download 产生的 blob；没有私有下载 API。Python 要求 `provider_record_url` 等于该下载的 navigation context，并核验同一记录的 DOI/PDF 动作归属。

`navigation_state` 只报告 scheme/host，不发送 signed query；`publisher_state`、`human_action_needed`、`resolver_choices`、`browser_path_failure` 和 `download_candidate` 都走 capability/tab 认证和 4096-byte payload 上限。下载路径、来源/referrer/final URL 是短暂 transport evidence，不记录日志、写 Paper 或存入 local/sync。session 只保存当前任务的导航/选择/候选，未保存历史或重放队列。

Python `download_evidence` 只接受已认证、与冻结 task/verified tab 匹配的 complete 候选。`stage_download` 对绝对本地源路径逐层 no-follow 打开，拒绝 symlink、特殊对象、路径替换、字节变化、空文件、未完成或超过 **128 MiB** 的文件；复制时实时限额，复核父路径与源 descriptor identity，并验证私有 staged 字节以 `%PDF` 开头。目标目录在 project/workspace/config/browser profile/download roots 之外，目录 0700、文件 0600；后续 validation/cleanup 逐层 no-follow 打开并绑定目录 descriptor，只通过固定 leaf 名相对访问，同时复核目录路径绑定；目录被重命名、替换或改为 symlink 时拒绝验证/清理。cleanup 只删除身份匹配的应用 staged copy，不改动用户源文件。

通过 `download_evidence` 认证后，私有 `stage_download` 产生绑定 task ID 的 `StagedPdf`。observed DOI 存在时必须匹配冻结 DOI；否则拒绝，缺少 observed DOI 本身不拒绝其他归属有效的 direct download。完成 staged `%PDF` 验证后，经 authorization 与最终 Zotero identity/PDF 检查直接进入 commit；staging 本身不授权 Zotero mutation。

应用只保留一个 process-local 当前任务。preflight、staging 与最终 Zotero commit 使用会结束的 bounded workers；浏览器、机构登录和 Zotero authorization 等待没有 lifetime worker。缺少初始 Zotero 授权时保留同一 StagedPdf 私有副本，打开 Web **Settings → Advanced & Diagnostics → Zotero integration** 授权后点击 **Continue after Zotero authorization**。这不会重新读取 Paper 或重新获取 PDF。

Web **Cancel acquisition** 仅在 mutation gate 前可用。Cancel 与 gate 在同一锁内竞争：Cancel 成功后不会有 Zotero content mutation；gate 先进入时 Cancel 返回 too late。gate 后重复 frozen key/DOI/Server-ID 和实际 PDF 检查；新出现 PDF 会抑制上传。terminal 失效 handoff、释放 slot、清理应用 staged copy，用户下载保持不变。Chrome 打开失败时用 Web **Open in Chrome again** 重开同一有效 handoff；URL 不进入 HTML。当前行为以 SPEC §37 为准；保留的 writer retry/partial-write 边界见 SPEC §§36.6–36.12。

## 历史 v0.5.1 live 验证

SPEC §36.14 记录的历史 v0.5.1 限定 live audit 已经过真实普通 Chrome、publisher→XMU、SmartLinks/Research、blob 下载归属、private staging、当时的 PUBLISHED qualification 与 Settings 授权。v0.5.1 已发布，实际 Zotero 文件注册已验证：parent `ZHIST6EG` 下的 child `LJ6UV83V` 有非空 regular PDF 文件，字节与用户 Chrome 下载一致。早期无实际文件的 partial child 保留，但不计作 PDF 成功。另一次机构路线自然未出现 human-verification challenge，记录为 `HUMAN_VERIFICATION_NOT_PRESENT`，该分支未 live-exercised，没有制造或绕过验证。历史 v0.5.1 证据不建立 v0.5.2 live 验证；当前自动回归也不证明真实浏览器/Zotero 写入。后续真实写入或重新注册需要明确用户授权。

参考：[Chrome storage.session](https://developer.chrome.com/docs/extensions/reference/api/storage)、[downloads](https://developer.chrome.com/docs/extensions/reference/api/downloads)、[webNavigation](https://developer.chrome.com/docs/extensions/reference/api/webNavigation)、[tabs](https://developer.chrome.com/docs/extensions/reference/api/tabs)。

实现回归使用模拟 Chrome API 和合成 DOM，不构成真实 Chrome/XMU 验证。更新应用/扩展需重启应用并刷新扩展，再由用户启动全新任务；旧进程任务不能恢复为新授权或作为修复后的 live 证据。

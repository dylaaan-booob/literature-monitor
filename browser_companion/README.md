# Literature Monitor Browser Companion

这是独立于 Python package 的 Chrome Manifest V3 扩展。扩展提供安全 handoff：识别应用的 loopback 页面、绑定真实 Chrome tab、完成一次 claim、清除 fragment，再通过独立 activation 发送已认证的 `tab_ready`。同时支持 publisher-first 导航、可见 XMU resolver 选择和任务下载证据。`tab_ready` 只表示 companion 已连接，不表示 PDF acquisition 已推进或完成。

## 在日常 Chrome 中安装

1. 使用你平时登录和阅读论文的普通 Chrome profile（Chrome 102 或更新版本）。
2. 打开 `chrome://extensions`，启用 **Developer mode**。
3. 点击 **Load unpacked**，选择本仓库根目录的 **browser_companion** 文件夹。
4. 建议将扩展固定到工具栏；点击图标打开当前任务 popup，使用明确的连接重试、下载、fallback 或 resolver choice 按钮。

文件无需构建，也无需 Node/npm。Literature Monitor Web 应用与 Zotero Desktop 分别运行；扩展不会启动任何一个程序。Zotero 写授权仍由应用的 Settings 管理。

## 当前握手与恢复

Add PDF 的 bounded preflight 成功后，应用会在普通 Chrome 打开带一次性 fragment 的 handoff URL。扩展只识别 HTTP `localhost` 或 `127.0.0.1` 下的 `/browser-handoff/<canonical UUID>`；query 必须为空。不要复制或分享该 URL，也不要向 URL query 中添加 token。

握手顺序为 `claim → fragment scrub → activation → tab_ready → START`。claim 成功后，content script 先用 `history.replaceState` 清除 fragment，不导航、不改写页面正文，再发送独立 `activate_handoff` 消息。activation 只接受已清除 fragment 的 canonical handoff 页面，不包含初始能力、handoff URL 或 tab 身份。worker 使用 Chrome 的可信 sender tab ID 生成绑定，不接收页面指定的 tab 身份。初始能力不写入任何存储；后续能力只进入 `chrome.storage.session`，并限制为 `TRUSTED_CONTEXTS`，不返回 content script 或 popup。

worker 不再在 fresh claim continuation 中自动发送 `tab_ready`，并在释放 claim 锁后才回复 content script；session authority 保存成功后才能 activation/START。若 START 回复丢失或工具栏显示 **!**，在同一个已 claim 且已清除 fragment 的 handoff tab 中打开 popup，点击 **Retry companion connection**，重放相同 activation 语义，不重新 claim。重复 activation 可再次取得同一 frozen START；same-plan START 成功恢复而不重新导航或重置已有 acquisition context。popup 是唯一人工操作入口，production 没有 `chrome.action.onClicked` listener。每个网络请求最多等待 10 秒；没有轮询、通用队列或定时重试。成功通知后 **!** 消失。其他 tab、其他 task 或其他 loopback origin 不能使用已有授权。

**X** 表示服务器已拒绝旧 authority，或 session 存储失败。服务器已消费 claim 但 session 保存失败时，fragment 仍会清除，activation 失败，不会发送有效 `tab_ready` 或执行 START。关闭 Chrome、禁用/重新加载扩展或浏览器重启会丢失 session authority。worker 的普通 suspension/revival 会从 session 恢复同一绑定，包括 claim 完成后、activation 前的重启；clean handoff 页面重新执行或 popup 重试均可继续，无需第二次 claim。如果 claim acknowledgement 丢失、页面仍带原 fragment，重新加载同一 owner 页面会使用已保存 authority 返回成功，清除 fragment 后再 activation，不重发 claim POST。缺失状态时不会从已清除 fragment 的 URL 重建能力，也不会重新 claim；需要应用先取消/失效旧 task，再建立新 task，不要重新使用已消耗的初始 URL。应用重启不会从扩展状态恢复旧 acquisition。

应用侧的 terminal/cancel invalidation 会使旧 handoff 失效，但不会直接清除扩展的 session 状态。下一个绑定不同的新 handoff 会先向已验证的旧 origin 发起无能力值的 GET `/browser-handoff/<旧 task UUID>`：200 表示旧任务仍有效，拒绝新 claim；仅 404 才清除旧 session 状态，然后以新任务自己的初始能力和真实 sender tab 完成 fresh claim。旧 tab 已关闭或停留在其他页面都不影响此检查。旧服务器不可用、超时、redirect 或其他响应均 fail closed，保留旧状态，不发送新 claim，也不清除新页面 fragment；可稍后重新加载该新 handoff 页面重试。

## 权限与阶段边界

扩展申请 `storage`、`downloads`、`webNavigation` 和 `tabs`。`downloads` 用于启动和观察下载；`webNavigation` 只处理已 claim tab 的主 frame 导航；`tabs` 用于核实 claimed tab 的当前 URL 和 popup 操作所在 tab。Chrome DownloadItem 没有 tab ID，因此仍需当前 committed navigation、epoch、时间和精确来源证据；其他普通同-origin tab 的存在不拒绝归属，也不建立歧义。不查询竞争 origins 或浏览历史。

host permissions 限于两个 HTTP loopback host、`https://resolver.ebsco.com/*` 与 `https://research.ebsco.com/*`。Chrome 的 host permission 不按 path 或 port 限定，因此 loopback 请求仍在 worker 中校验 origin，并只使用固定 claim/events 路径及规范旧任务 UUID 的 handoff 状态路径。content script 分别限于 handoff、FTF `/c/45yels/result` / `/redirect` 和 Research `/c/<context>/search/details/<record>`；均再次验证完整 URL，Research 只有当前已批准 XMU task/tab 才能观察记录。没有通用 publisher content script 或 `<all_urls>`。

扩展不读取、复制或导出 cookie，不存储机构密码，不接收 Zotero API key、Paper Markdown 或 workflow 写权限。它不调用、控制或集成 Zotero Connector；Connector 保持独立。所有 JavaScript 均随本目录打包，无远程代码、遥测或 analytics。

## 当前源码与已发布基线

**v0.5.3 已发布，是最新 released/completed baseline**；当前 browser/acquisition 生命周期以 SPEC §38 为准，未变 DOI-first 行为仍以 §37 为准。实现、A0–A5 独立阶段复审、A6 automated integration/acceptance、最终 code/behavior audit、closed-claim storage-fault、fresh-handoff activation 修复和实现 documentation closeout 均已完成独立审查；实现提交为 `21547260157f121a1efd2a5e8f930fad5f26959f`。Release preparation 独立审阅已通过，准备提交/release HEAD 为 `4a2595e56bfcfb7d845216c5161e978628d3a71a`。Final exact-release-HEAD clean-export validation、offline wheel/sdist build 和 installed-wheel smoke 已通过；annotated tag `v0.5.3`、main/tag 推送和 [GitHub Release v0.5.3](https://github.com/dylaaan-booob/literature-monitor/releases/tag/v0.5.3) 已完成，两个 final assets 的 authoritative SHA-256 已核验（§24.19）。Python package metadata 和 Provider identity 均为 **0.5.3**；companion manifest 独立版本仍为 **0.1.0**，权限与内容未变。Scoped normal-Chrome validation 保持 SPEC §38.13 的 partial live boundary，fresh 自动 handoff 修复已 live 验证；实际 v0.5.3 target-PDF staging/Zotero registration 仍未验证。Post-release documentation closeout 是 release tag 之后的单独文档与静态状态断言变更，不进入 tag target。历史 v0.5.2：v0.5.2 A0–A5 实现及独立阶段审查、最终集成审计和发布准备审查已完成；准备提交、annotated tag、main/tag 推送及 GitHub Release 已完成（§24.18）。独立实现审计的实际范围见 SPEC §37.10（审查环境无 Node，未独立执行 JS runtime/content suites）。单独的最终 clean-export full validation 按用户明确选择跳过，不计为已完成；不声明新增 v0.5.2 live companion/Zotero 验证。Web Add PDF 通过 installed normal Chrome 打开 handoff，不使用 dedicated profile、headless 或 Playwright，没有 production Playwright fallback。Python wheel 不会安装此独立扩展；请保留包含 browser_companion/ 的源码目录。

## DOI-bound 当前任务流程

任务只冻结 task ID、Paper UUID、normalized DOI、verified Zotero parent key 和 Server-ID。Python `navigation_plan(frozen_task)` 的 START plan 只有 `task_id`、`doi`、`direct_url`，从 canonical DOI URL `https://doi.org/<normalized-doi>` 开始，DOI 按 URL 规则编码。`xmu_fallback_url` 仅在 publisher 已明确 exhausted 时启用；login/CAPTCHA/MFA/Cloudflare 等 human wait 不构成 exhausted。

v0.5.3 的机构认证由用户在同一 claimed normal-Chrome task tab 手动完成，login/CAPTCHA/MFA 不会被绕过；登录等待不授权 XMU fallback。Proactive/automatic institutional authentication 属于未来版本范围，当前没有自动 CARSI、机构选择、publisher login adapter、credential automation 或 cookie/session access。

合法 `tab_ready` 只绑定 claimed tab 并返回幂等 START，应用继续保持 **HANDOFF**。START 先在 `chrome.storage.session` 建立完整 task context 和 pending direct navigation，再调用 `chrome.tabs.update()`；fallback 和 resolver choice 同样先保存 pending，route/category 变化在 commit 时应用。保存失败不会调用导航；`tabs.update()` 成功返回也不代表 document 已提交。

只有 claimed tab 主 frame 的 `webNavigation.onCommitted` 建立 current committed navigation，记录实际 browser URL，允许它与命令 target 不同的正常重定向，清除 pending，并发送只含 scheme/host 和 route 的已认证 `navigation_state`。应用严格验证后才进入 **BROWSER_ACTION**。

`onBeforeNavigate` 仅保存最多 8 个进行中的起始证据（URL、Chrome timestamp，以及可用的 process ID），pending 指令绑定首个 target start 的 id/时间；后续 provisional start 不直接取消 pending。该事件不建立 committed state、推进 epoch 或发送 navigation_state。

commit 只有能关联到 pending target/start 的 browser sequence 才应用 route/category；同一有效 processId 的 repeated provisional starts 与 redirect qualifier 可以保留 transition。不同 user sequence 的 commit 可 supersede 指令，但不继承其 transition；未结束的 losing sequence 只保留有上限的瞬时证据，防止它的迟到事件被下一条指令收养。只有唯一匹配 pending 初始 target/序列的 error 才能报告 `navigation_failed`；无关或歧义 error 不终止任务，`ERR_ABORTED` 只记录 provisional 中止。证据超过上限或归属歧义时不应用指令 transition。

A2 复审验收采用歧义流程 fail closed：重复 starts 缺少有效 processId，或 command provisional load 中止后由另一个 process commit 时，现有事件不能证明是 command redirect 还是 user supersession。`ERR_ABORTED` 与 redirect qualifier 的组合不能单独授权跨序列继承；这些流程仍记录 actual committed URL/epoch，但不应用 pending route/category。自动化正例验证具备可关联序列证据的 repeated-before redirect；未知或跨 process 的歧义例验证 transition 不继承、错误不终止任务。此边界保持现有 permissions 与 §38。

首次 commit 前，publisher 操作、generic arm、当前 URL 下载和 resolver/Research 内容授权均 fail closed。每次 commit（包括同 URL reload）推进 epoch，清除旧 arm、record/provider-action expectation 和 resolver choices。持久化的 Chrome event timestamp 拒绝迟到或重复 commit，不与扩展的 `Date.now()` 混用排序。序列证据保存在 `chrome.storage.session`，普通 worker restart 后继续使用，不建立 history 或 local storage；[Chrome API](https://developer.chrome.com/docs/extensions/reference/api/webNavigation) 的 before/error processId 为 `-1` 时按未知处理，不能把 `-1` 当成共享序列 ID。

普通 MV3 worker suspension/revival 可从 session pending 及其导航关联证据继续接受随后到达的 commit，无需重新 claim、activation 或 START。当前 committed epoch 的报告进度也保留在 session：其他事件 POST 占用网络锁时只保留最新未报告状态，在锁释放或 worker 恢复时补报；旧回复不能把更新 epoch 标记为已报告。没有通用事件队列、轮询或定时重试。即时 commit 即使先于 `tabs.update()` Promise resolve 到达也不会被后续旧 snapshot 覆盖。导航调用被拒绝或收到真实 terminal navigation error 时清除对应 pending，保留已 committed URL/epoch 和 route/category，沿固定 `navigation_failed` 路径收敛；非终局 `ERR_ABORTED` 不作为导航失败。session 只保存当前 committed 导航、可选 pending 与最多 8 个尚未结束的序列证据，没有 durable 导航历史。

worker 通过已认证 browser event 接收可选固定命令回复：START 只能携带上述 DOI plan，PUBLISHER_EXHAUSTED 只在当前 DOI task 的 publisher 明确 exhausted 后启用，CHOOSE 只接受当前 choice ID，DOWNLOAD_CURRENT 只使用任务当前 URL。回复不包含 capability、Zotero credential、Paper 或可覆盖的 tab；执行前再次核对当前 session authority，旧 task 的响应不能重定向新 task。没有命令轮询、历史队列或任意页面控制接口。

在任务 tab 打开扩展 popup：**Publisher path exhausted — try XMU** 表示用户确认 direct publisher 路径已不可获得当前 DOI 的 PDF，先经应用验证才执行 fallback；login 等待本身不表示 exhausted。多个 resolver choices 保持顺序，并经应用确认当前 ID 后导航同一 tab。**Capture next PDF download** 建立 generic arm；**Download current URL with Chrome** 经应用返回固定命令；**Retry companion connection** 重试同一 handoff 的 activation。popup 已删除手动复查下载、手动声明登录等待和旧版本选择措辞。下载自动按 exact ID 观察；用户直接在同一任务 tab 完成 login/CAPTCHA/MFA，真正来自 resolver/content lifecycle 的 `human_action_needed` 仍受支持。其他 tab 或任意页面消息不能使用这些操作。

XMU adapter 只识别 exact `https://resolver.ebsco.com/c/45yels/result` 或 `/redirect` 的 OPID `45yels`、customer `s1215021`、group `main`、profile `ftf` 与匹配 DOI。result 页候选 anchor 必须位于可见语义 list/table/group 结果结构中；redirect 页使用下述 exact provider 句子。单独的 Full Text / SmartLink(s) anchor 不采用。result 页根据可见 exact Full Text / SmartLink(s) 文本或可见语义分组标题识别 choice；redirect 页只额外接受可见的 `Find this article in full text from EBSCOhost SmartLinks`（或 Full Text）provider 句子及其对应链接，仍经当前 resolver choice authority 才进入该路线。非 eligible 分组标题会拒绝该分组，排除 SearchEngines、Other、DocumentDelivery、research/tool/help 链接；href 本身不是类别证据。只取一次最长 15 秒的可见结果快照，不请求或观察私有 API。多个 eligible choices 保留页面顺序，通过 `resolver_choices` 发送 bounded ID/category/label 等待显式选择。超过六项或页面仍 busy 时拒绝采用。human wait 保持当前任务/tab；完成后可重新加载同一 resolver 页面继续观察。

下载优化只针对任务当前 committed URL，先保留 extension-owned reservation，再绑定 `chrome.downloads.download()` 返回的 exact ID；保持 `saveAs: true`、`conflictAction: uniquify`，不指定目标文件路径。普通 unrelated onCreated/onChanged 不干扰此候选。

用户可在所属 publisher/PDF-viewer tab 的扩展 popup 点击 **Capture next PDF download**（显示 **D**）。arm 绑定当前 task、claimed tab、committed URL、navigation epoch、唯一 arm 标识和时间。普通 HTTP 下载须在 10 秒内开始，referrer 与 armed landing page 精确一致，真实 claimed tab 仍在该页面；下载 URL 可以不同。没有显式 arm 时，referrer 单独不能建立归属；原有 exact 当前导航 URL 的有限时间归属路径保留。同-origin 普通 tab 本身不拒绝 otherwise attributable 下载，其他 extension、错误 referrer、无关同源下载或跨源 blob 不消费仍有效 arm。arm 仅在超时、epoch 变化、实际 compatible candidate claim、真实歧义或 authority 终止时结束。

session 最多保留 8 个尚可能 compatible 的 exact download observations，只含 ID、epoch、arm 标识/时间和 provider expectation ID，不保存下载 URL、token 或 capability。`onCreated` metadata 不足时仅观察 ID，不建立 candidate；已知 incompatible 则忽略。容量满时保留已有 ID，拒绝新增不完整 ID，overflow 本身不构成歧义。`onChanged` 只对已观察或冻结的 exact ID 执行 `chrome.downloads.search({id})`：仍不完整则保留，已证明 incompatible 或已消失则移除 observation，兼容则尝试冻结候选。未知 ID 不触发搜索，没有 latest/recent/history 扫描、跨 ID 文件名猜测或下载队列。

一个 compatible ID 冻结完整 ownership proof 后消费 live arm，并清除 observations；in-progress candidate 后续只追踪该 ID 的完成或不可用状态。unrelated 下载和 callback overlap 不改写它。第二个不同 actual ID 也满足冻结的 attribution proof 时，才设置 ambiguous，清除候选、arm 和 observations，发送固定 `ambiguous_download_ownership`；不选择 winner，也不复活 arm。candidate complete 才发送现有已认证 `download_candidate`，interrupted、missing 或 exists=false 沿固定 `download_unavailable` 终止。普通 worker restart 保留有效 observations；epoch 变化、authority 退役/替换清理旧 evidence。文件继续属于用户，扩展从不删除文件或 Chrome 下载历史，也不声称每一种 PDF viewer 下载都可归属。

三种 download outcome（complete candidate、真实 ambiguity、candidate unavailable）会先冻结为 session 中唯一的待确认 outcome，保留现有 bounded event payload 或固定 reason，不含 capability。`downloadReported` 只在有效 authenticated response 后设置；网络 mutex 被占用时保留 outcome，当前 POST 释放 mutex 后补送，普通 worker restart 也会恢复补送。等待期间候选、arm、observations 已停止变化，不会重新 arm 或让 unrelated 下载替换 outcome。传输/回复失败不算确认，不自行循环重试；后续其他 POST 释放 mutex 或 worker restart 可以重送同一冻结 outcome。403 清理旧 authority/evidence，替换 task 不继承旧 outcome。应用在首次接纳候选后立即离开 browser stages，同一 frozen payload 的重送不能再启动 staging；terminal task 的 registry authority 已失效，重送被拒绝。这是一个当前任务的 outcome，不是通用事件队列或历史。

当前 v0.5.3 scoped live 证据来自 normal Chrome 154.0.8037.93 和 unpacked companion 0.1.0：修复后的 fresh claim → scrub → automatic activation 无需 Retry，同一 claimed tab 的 committed publisher navigation / `BROWSER_ACTION`、popup 和 pre-freeze close / slot release 均通过；更早自然停滞的 handoff 已验证 popup Retry recovery。两个授权 DOI 均到达正确 publisher，TEST_FORCED_XMU 均到达真实 resolver，但没有 eligible FullText/SmartLinks candidate 或 EBSCO Research 路线；这不证明 genuine publisher exhaustion 或 full-text retrieval 成功。

当前路径未提供 target PDF，实际 v0.5.3 target DownloadItem、private staging、Zotero registration、EBSCO final PDF action 和 post-freeze close 均未 live-exercised。exact-ID ownership/reconciliation、provider-action、post-freeze close 与 staging/writer safety 只有自动测试证据。真正 challenge 未出现：`HUMAN_VERIFICATION_NOT_PRESENT`，manual continuation 分支未 live-exercised。完整分类见 SPEC §38.13；历史 staging/Zotero 成功不计为 v0.5.3 证据。

普通 generic arm 还支持同源 `blob:https://.../<UUID>` 下载：blob 的内嵌 HTTPS origin 必须等于 armed page origin，referrer 可以为空或精确 armed page，仍限 10 秒。已批准 XMU、FullText/SmartLinks 的 Research 同记录路线改由下述 trusted PDF action 独立授权，允许空 referrer、同记录 SPA referrer 或精确 `https://research.ebsco.com/` 根 referrer；须同时通过 task/same-record/category、provider 时间窗、实际 claimed/current tab、extension 来源和唯一候选检查。任意同源路径、不同 origin 根或普通 HTTP 下载的根 referrer 均不获此权限。完成归属与记录/PDF 动作证据复核后，worker 将已验证的非空 Research SPA/根 referrer 规范化为精确任务记录 navigation URL；根 referrer 本身不建立记录身份。`blob:http`、opaque/null、跨 origin、缺少对应 authority 或矛盾 referrer 均拒绝。blob token 不进入 browser event、日志或 provenance；传输使用 `transport_kind=blob`、`download_origin`，`url`/`final_url` 为 null，保留真实 HTTP navigation、下载 id 和 Chrome 本地路径。

Research/EBSCO record adapter 使用 exact Research record context 与 `div[data-auto="record-html-metadata"] > article[lang]` 内的可见 DOI。DOI 只读取 `li#DOI` 的直接文本，忽略扩展注入的 descendant link。content→worker 的 `recordEvidence` 记录 payload 只有 DOI；文献类型、期刊出版信息、年份和卷期不作为下载接纳条件，preprint/AAM/online-first 字样也不作为拒绝条件。

用户通过受信任的可见 Download entry 打开 dialog、选择唯一可见且启用的 PDF 选项，再可信点击最终 Download，即建立 provider-action expectation，无需 generic arm。content script 保留稳定的记录 DOM 和 exact DOI 观察；错误、隐藏、禁用、非 PDF、多选、未受信任或记录已变的动作均拒绝。content message 必须在点击后 10 秒内送达，这是独立的消息新鲜度检查。worker 原子冻结 task、claimed tab、origin、record URL、DOI、实际 committed navigation time/epoch、首次 `actionTime` 和 expectation ID；重复点击不能刷新它，已有 generic arm 会清除，也不参与 provider authority。

provider preparation 的 120 秒窗口只按首次 `action_time` 计算：`0 <= start_time - action_time <= 120000`，允许远端生成 PDF 后才开始下载。普通 worker suspension/revival 从 session 恢复同一 expectation；任何新 document commit（包括同 URL reload）均使旧 expectation 失效。provider candidate 使用 `ownership=provider_action`、`attribution=ebsco_pdf_action` 和 `action_time`，不含 `arm_time`；`navigation_time` 始终为实际 committed navigation 时间。Python 独立核验任务/记录 DOI、route/category、ownership 和时间边界。可访问 modal 暂时隐藏背景时，仅保留先前可见且 DOM 未变的记录观察。

action 与 `onCreated` 交错时，action 前不会建立 provider candidate；只可在现有 8-ID 上限内保留当前 task/record 下尚可能匹配的 exact observations。action 建立后仅复查这些 ID，必要时 `chrome.downloads.search({id})`，不扫描 arbitrary IDs。已知错误 origin/referrer、其他 extension 或早于 action 开始的下载不会被追认。第二个实际 compatible ID 仍按 A3 规则收敛为 ambiguity；provider outcome 复用同一个 bounded delivery、busy/restart recovery 和应用幂等接纳路径。

`/viewer/pdf/...` 不是据此可推断的 raw PDF endpoint。此路线使用用户可见 PDF Download 产生的 blob；没有私有下载 API。Python 要求 `provider_record_url` 等于该下载的 navigation context，并核验同一记录的 DOI/PDF 动作归属。

`navigation_state` 只报告 scheme/host，不发送 signed query；`publisher_state`、`human_action_needed`、`resolver_choices`、`browser_path_failure` 和 `download_candidate` 都走 capability/tab 认证和 4096-byte payload 上限。下载路径、来源/referrer/final URL 是短暂 transport evidence，不记录日志、写 Paper 或存入 local/sync。session 只保存当前任务的导航/选择/provider-action expectation/候选、有上限的 exact-ID observations 和至多一个待确认 download outcome，未保存历史或通用重放队列。

Python `download_evidence` 只接受已认证、与冻结 task/verified tab 匹配的 complete 候选。`stage_download` 对绝对本地源路径逐层 no-follow 打开，拒绝 symlink、特殊对象、路径替换、字节变化、空文件、未完成或超过 **128 MiB** 的文件；复制时实时限额，复核父路径与源 descriptor identity，并验证私有 staged 字节以 `%PDF` 开头。目标目录在 project/workspace/config/browser profile/download roots 之外，目录 0700、文件 0600；后续 validation/cleanup 逐层 no-follow 打开并绑定目录 descriptor，只通过固定 leaf 名相对访问，同时复核目录路径绑定；目录被重命名、替换或改为 symlink 时拒绝验证/清理。cleanup 只删除身份匹配的应用 staged copy，不改动用户源文件。

通过 `download_evidence` 认证后，私有 `stage_download` 产生绑定 task ID 的 `StagedPdf`。observed DOI 存在时必须匹配冻结 DOI；否则拒绝，缺少 observed DOI 本身不拒绝其他归属有效的 direct download。完成 staged `%PDF` 验证后，经 authorization 与最终 Zotero identity/PDF 检查直接进入 commit；staging 本身不授权 Zotero mutation。

claimed task tab 是唯一 browser authority，不迁移到其他同-origin tab，也不自动寻找或打开替代 tab。`tabs.onRemoved` 与 candidate claim 共用 session mutation 序列：关闭先提交、尚未冻结 compatible exact ID 时，清除 pending/navigation association、arm、provider/record evidence 和 observations，冻结单一 `browser_path_failure` / `task_tab_closed` outcome。network mutex busy 时保留，释放后或普通 worker restart 时补送；仅 authenticated ACK 或确认 403/revocation 才退役旧 authority，替换 task 不继承它。claim 已成功但 activation 尚未到达时关闭，同样终止应用任务并释放 single-acquisition slot。

compatible exact ID 先冻结时，关闭 tab 保留该候选的 task/tab/navigation/arm-or-provider proof 和待确认 download outcome；只继续同一 ID 的完成或不可用处理，不接纳新 ID、arm、provider action、resolver 或 navigation。completion 继续核验 transport/referrer/origin、开始时间、provider DOI、文件 metadata 和完成状态，只在已确认关闭后使用 frozen proof，不要求 vanished tab 仍存在。extension-owned 路径也必须先安全冻结返回的 exact ID；仅有未返回 ID 的 reservation 不获得此例外。扩展不取消或删除用户下载，也不删除 Chrome 历史。

应用在发出 browser authority/打开 Chrome 时建立约 **30 分钟（1800 秒）**的 process-local inactivity lease，显式重试打开时重新建立。当前 task/tab 的 bounded browser event 通过 registry envelope authentication 后刷新 lease，包括 business payload 随后校验失败的事件；错误 task/tab/capability、malformed envelope、status GET、其他 Web 请求和 UI polling 均不刷新。每个 active attempt 至多一个可取消的 daemon one-shot Timer，共用 registry/coordinator RLock，并核对 attempt/generation；没有 periodic runner、Chrome alarm、service-worker keepalive 或 durable lease/history。extension reload/disable、Chrome crash 或终止事件无法送达时，browser stages 的 lease 到期释放 slot、失效 registry authority，返回可重新发起的 `NO_VALID_PDF` / `CHECK_BROWSER`。旧 timer 或晚到事件不能复活任务。

接纳 `download_candidate` 时先进入 VALIDATING_PDF 并取消 browser lease；staging、Zotero authorization wait 和 ATTACHING 不受 browser inactivity 到期影响。mutation gate 后 Cancel 仍 TOO_LATE，tab close 不隐含 rollback，partial-write/mutation uncertainty 语义保留。应用 restart 建立全新 process-local coordinator/registry，不从 Chrome tabs、session、Downloads 或 staging orphans 重建 acquisition；旧 authority status GET 返回 404。

应用只保留一个 process-local 当前任务。preflight、staging 与最终 Zotero commit 使用会结束的 bounded workers；浏览器、机构登录和 Zotero authorization 等待没有 lifetime worker。缺少初始 Zotero 授权时保留同一 StagedPdf 私有副本，打开 Web **Settings → Advanced & Diagnostics → Zotero integration** 授权后点击 **Continue after Zotero authorization**。这不会重新读取 Paper 或重新获取 PDF。

Web **Cancel acquisition** 仅在 mutation gate 前可用。Cancel 与 gate 在同一锁内竞争：Cancel 成功后不会有 Zotero content mutation；gate 先进入时 Cancel 返回 too late。gate 后重复 frozen key/DOI/Server-ID 和实际 PDF 检查；新出现 PDF 会抑制上传。terminal 失效 handoff、释放 slot、清理应用 staged copy，用户下载保持不变。Chrome 打开失败时用 Web **Open in Chrome again** 重开同一有效 handoff；URL 不进入 HTML。当前 v0.5.3 browser/acquisition lifecycle 以 SPEC §38 为准；未变 DOI-first identity/Provider/Paper/single-manifestation/Zotero parent 行为仍以 §37 为准；未变 staging、authorization、writer、cancellation 和 partial-write safety 仍以 §36 为准。

## 历史 v0.5.1 live 验证

SPEC §36.14 记录的历史 v0.5.1 限定 live audit 已经过真实普通 Chrome、publisher→XMU、SmartLinks/Research、blob 下载归属、private staging、当时的 PUBLISHED qualification 与 Settings 授权。v0.5.1 已发布，实际 Zotero 文件注册已验证：parent `ZHIST6EG` 下的 child `LJ6UV83V` 有非空 regular PDF 文件，字节与用户 Chrome 下载一致。早期无实际文件的 partial child 保留，但不计作 PDF 成功。另一次机构路线自然未出现 human-verification challenge，记录为 `HUMAN_VERIFICATION_NOT_PRESENT`，该分支未 live-exercised，没有制造或绕过验证。历史 v0.5.1 证据不建立 v0.5.2 live 验证；当前自动回归也不证明真实浏览器/Zotero 写入。后续真实写入或重新注册需要明确用户授权。

参考：[Chrome storage.session](https://developer.chrome.com/docs/extensions/reference/api/storage)、[downloads](https://developer.chrome.com/docs/extensions/reference/api/downloads)、[webNavigation](https://developer.chrome.com/docs/extensions/reference/api/webNavigation)、[tabs](https://developer.chrome.com/docs/extensions/reference/api/tabs)。

实现回归使用模拟 Chrome API 和合成 DOM，不构成真实 Chrome/XMU 验证。更新应用/扩展需重启应用并刷新扩展，再由用户启动全新任务；旧进程任务不能恢复为新授权或作为修复后的 live 证据。

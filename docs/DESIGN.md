# 设计决策 · meta-coding 静态产品页

文件：`site/index.html`　配套说明：`site/README.md`

## 1. 一个决定：纯白，不选深色

这个项目处理的是编码规则、论文原文、逐字段差异和修改建议。它更像一份可核对的 codebook / 校样，而不是监控面板。

因此选择**纯白极简**，不选深色底：

- 白纸让大规模文字、表格、页码证据读起来最接近研究工作本身；
- 深色底容易把界面推向“仪表盘”，削弱“可回原文核对”的感觉；
- 红色只作为校样笔迹一样的强调色，出现在 critical、差异行和关键动作上；
- 不用渐变、发光、阴影和大圆角。质感来自字体层级、细分割线和留白，不来自装饰。

一句话概括：**白纸、墨字、一支暗红校样笔。**

## 2. 色彩：三种色相，灰阶只是墨的深浅

| Token | 值 | 角色 |
|---|---|---|
| `--paper` | `#FFFFFF` | 页面底色 |
| `--ink` | `#101110` | 标题、主按钮、强分割线 |
| `--body` | `#2E2E2A` | 正文 |
| `--muted` | `#62625B` | 次级正文、表格值 |
| `--faint` | `#77776F` | 元信息、标签、脚注 |
| `--rule` | `#E4E3DE` | 常规细线 |
| `--rule-strong` | `#C8C7C0` | 输入框、次要边框 |
| `--wash` | `#F7F7F4` | 应用框架、表头底、空态 |
| `--wash-2` | `#FBFBF9` | 侧栏、证据行 |
| `--accent` | `#A63D40` | 唯一强调色：critical、差异、关键动作 |
| `--accent-deep` | `#8C3134` | 强调色悬停深阶 |
| `--accent-wash` | `#FBF4F3` | 差异行底色 |

**色相只有三种：白、墨、暗红。** 中间灰阶是墨的明度变化，不构成第二强调色。`critical` 与差异高亮使用同一支暗红，不额外引入绿色或橙色状态色。

实测对比度（WCAG 2.x）：

| 前景 / 背景 | 比值 |
|---|---:|
| `--ink` / `--paper` | 18.92 |
| `--body` / `--paper` | 13.63 |
| `--muted` / `--paper` | 6.14 |
| `--faint` / `--paper` | 4.51 |
| `--accent` / `--paper` | 6.24 |
| `--paper` / `--accent` | 6.24 |
| `--accent` / `--accent-wash` | 5.75 |

所有正文与元信息达到 AA 小字号阈值；细线不承载语义，只做分隔。

## 3. 字体：系统衬线 + 系统无衬线 + 系统等宽

不拉字体文件，不请求 CDN。

```css
--serif: "Songti SC", "STSong", "Noto Serif CJK SC",
         "Source Han Serif SC", "SimSun", Georgia, serif;
--sans:  -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
         "Hiragino Sans GB", "Microsoft YaHei", "Helvetica Neue", Arial, sans-serif;
--mono:  ui-monospace, "SFMono-Regular", "SF Mono", Menlo, Consolas,
         "Liberation Mono", monospace;
```

- **大标题用衬线**。中文宋体的横细竖粗、字面留白，天然带“文稿 / 出版”的气质；比默认无衬线大标题更不像模板。
- **正文用系统无衬线**，17px / 1.6。中文在屏幕上更稳，长文不疲劳。
- **等宽只做元信息**：字段名、哈希、页码、表头、状态标签。它负责“机器可读”的质感，不进入正文。
- 字号阶梯：9–10px（kicker / 表头）、13px（小注）、16–17px（正文）、19px（lead）、20–24px（小节标题）、26px（应用屏标题）、32–48px（章节标题）、48–78px（Hero）、38–64px（页脚宣言）。
- 大标题负字距（`-.03em` 到 `-.05em`），正文不压缩字距。中文标题行高 1.06–1.18，西文数字保持 tabular。

## 4. 8px 节奏与 720px 阅读宽度

间距 token 全部取 8 的倍数：

```text
8 · 16 · 24 · 32 · 40 · 48 · 64 · 80 · 96 · 128
```

- 页面 shell：最大 `1160px`，左右 gutter `clamp(20px, 4vw, 48px)`。
- 章节采用“元信息栏 + 正文”的不对称网格：`160px` 标签栏 + `48px` 间隔 + 最大 `856px` 内容。
- 所有长段正文限制在 `720px`，保证中文每行不过长。
- 桌面章节上下 `128px`，平板 `96px`，手机 `80px`；细线 `1px` 贯穿。
- 应用演示允许突破 720 到 `1064px`，因为它是“产品界面”，不是阅读正文。

关于“8px 基线”：**布局节奏严格取 8 的倍数**（间距、容器高度、按钮高度、行块间距）。正文行高按需求保留 `17px × 1.6 = 27.2px`，不为了凑整而压成 1.41 或 1.88；字体规格优先，块级节奏仍落在 8px 网格上。这是排版与网格之间明确的取舍，而不是混用两套间距。

## 5. 版式骨架

### Hero

- 两列：左标题 / 定义 / 双按钮，右 280px 的 `REPO READOUT` 数据栏。
- 标题 `把判断，摊开给人看。` 用 48–78px 宋体，两行断句；左侧一条 1px 竖线与章节编号形成“文稿页”结构。
- 右侧数据栏展示真实仓库状态：规则包、28 字段、21 篇摄取、0 次真实编码。**把“还没跑”放在首屏**，是可信度的一部分。
- 底部四格 mono 特征条，用细线分隔，不用图标。

### 章节

- 统一结构：左侧 sticky 编号 + 英文 kicker，右侧大标题 + 正文。
- 章节之间只靠 `1px` 上边线和 128px 留白分隔，不换背景、不加卡片。
- 问题区用 01 / 02 编号 + 细线行；工作流用 6 行 step ledger；状态区用三列表格；试用区用表单 + 输出台。
- 只出现两种“面”：纸面和应用框。没有第三层卡片。

### 页脚

- 反相墨色块，作为“版权页 / colophon”。大字 `规则是输入。证据是输出。`
- 页脚里保留强制的“演示数据，非真实研究结果”声明和许可行。
- 反相块是整页唯一的深色大面积；它结束页面，不参与正文阅读。

## 6. 为什么界面全部用 HTML / CSS 画

三个界面（规则包、结果对照、修改意见）没有一张截图，全部是真实 DOM：

- **清晰度**：任意 DPI、任意缩放都锐利，不会出现截图模糊。
- **响应式**：同一套表格在手机上变成横向滚动；状态表在手机上重排为单列。截图做不到。
- **可访问**：表格是 `<table>`，有 caption、表头、状态文字；截图对屏幕阅读器是空白。
- **可维护**：改字段名、改数值、改文案就是改 HTML，不需要重新导出图片。
- **一致性**：界面颜色、细线、字号直接继承页面 token，不会出现“截图和页面两个世界”。
- **不撒谎**：界面中的演示数值有明确标注；没有用“产品截图”冒充真实运行结果。

应用框本身也只用了 1px 边框、背景 wash、侧栏和表格，没有阴影或圆角。顶栏的 `127.0.0.1:8765`、`G2 off`、`$0.00`、`4 / 5 steps` 都是产品状态的一部分，不是装饰。

## 7. 交互：原生 JS，一个 IIFE

没有框架、没有构建、没有事件委托库。全部行为在 `index.html` 末尾的单个 `<script>` 中。

1. **三屏切换**：`role="tablist"` + `role="tab"` + `aria-selected` + `aria-controls`；支持左右/上下方向键、Home/End；roving tabindex。
2. **差异筛选**：全部 / 仅差异 / critical；用 `aria-pressed`，切换时同步隐藏证据行、更新「显示 n / 588 格」，并隐藏空的 tbody。
3. **证据展开**：带 `≠` 的行可点击 / Enter / Space；一次只展开一条，`aria-expanded` 同步；展开内容为 A 原文、B 原文、裁决理由与 `match_type`。
4. **修改意见**：采纳 / 驳回可切换，状态文案与顶部计数实时更新；不是只在页面上变色，但静态页不会真的写回文件——文案明确说写回是本地控制台的行为。
5. **当前章节高亮**：`IntersectionObserver` 更新导航的 `aria-current="location"`；无动画，只换下划线。
6. **试用模块**：原生 `fetch`、`AbortController`（60 秒）、JSON 解析与格式化、错误分支；无第三方库。

所有交互都有键盘路径；`prefers-reduced-motion` 下只保留颜色变化，不做位移动画。

## 8. 线上试用：设计成“单点”，不设计成“流水线”

试用区放在页面最后（05），标题就叫「先试一次提取，再决定要不要跑全流程」。这是刻意的叙事顺序：先看完整产品，再给一个受限的真实调用。

- **Provider 只列 DeepSeek**。按当前实测，DeepSeek 的 OPTIONS 预检回显 Origin，浏览器可直连；Anthropic 没有 CORS 头，Google 未确认，不列入下拉。页面把原因写在右侧 CORS 注记里。
- **只做一次提取**：一段方法文字 → 一个 JSON。提示词里明确“只提取原文写出的值、每字段带 verbatim quote、不推断、不编页码”。
- **不是完整流程**：文案反复说明不包含 PDF 解析、双模型独立编码、逐字段比对、裁决、manifest；状态表新增一行「浏览器单次提取（本页试用）· 可试用 · 仅 DeepSeek / 无 PDF / 比对 / 裁决」，与 Hero 的 `G2 pipeline calls — off` 呼应。
- **密钥只在内存**：`type=password`；不发往本站服务器；不写 `localStorage` / `sessionStorage` / cookie；不放进 URL；刷新即失；「清空 key 与结果」立即清空。输入控件不设 `name`，即使脚本失效触发表单提交，key 也不会进入查询串。
- **请求路径可审计**：`POST https://api.deepseek.com/v1/chat/completions`，`Authorization: Bearer <key>`，`credentials: 'omit'`，`cache: 'no-store'`。key 不出现在请求 body 里。
- **CSP 兜底**：`connect-src https://api.deepseek.com`，即使页面被注入脚本，也不能把 key fetch 到其他域；`object-src 'none'`、`base-uri 'none'`。
- **错误可读且分类**：401/403 = 密钥无效；402 = 额度不足；429 = 限流；`TypeError` = CORS / 网络拦截（离线单独提示）；60 秒 = 超时；非 JSON = 保留原文并提示。API 的错误消息会显示，但不会回显 key。
- **输出台**：深墨色代码块，mono 字体，`JSON.stringify(parsed, null, 2)`；顶部状态显示 `HTTP 200 · deepseek-chat · n tokens · x.x s`。这是整页唯一接近“终端”的表面，用来强调它是真实返回，不是演示表。

安全边界写在页面上，不藏在 README 里：右侧三块注记分别讲 key path、scope、CORS；输出下方再提醒浏览器扩展仍可能读取页面内容。

## 9. 响应式

断点：`1020px` / `820px` / `520px`。

- **Hero**：两列变单列，右栏数据块落到标题与按钮之后；标题用 `clamp(40px, 11.4vw, 54px)`，避免中文大标题溢出。
- **章节**：标签栏从左侧 sticky 变为顶部横排，编号与 kicker 并排；正文恢复单列。
- **工作流**：4 列 ledger 变为 2 列（编号 + 内容），artifact 名落到描述下方。
- **应用框**：侧栏变成横向可滚动 tab bar；统计 4 列变 2×2；表格保持横向滚动，并在上方显示「横向滑动表格…」提示。
- **状态表**：在 820px 以下从三列表格重排为**单列行**：能力 + 说明在左，状态在右，不再需要横向滚动。这是唯一一个改为堆叠的表格，因为状态是本页的核心结论。
- **试用**：provider / key 从并排变上下；按钮全宽；输出代码块全宽。
- **页脚**：大字缩小，链接变 2×2，声明换行。

页面级横向溢出被 `html, body { overflow-x: clip }` 阻断；数据表只在 `.table-scroll` 内部滚动。390px 实测 `scrollWidth == clientWidth == 390`。

## 10. 可访问性

- 语义化 `header / nav / main / section / article / footer`；跳过链接。
- 三屏是真正的 ARIA tabs；筛选是 `aria-pressed`；证据行是 `aria-expanded`；错误区 `role="alert"`；试用状态 `aria-live="polite"`。
- 所有表格有 `<caption class="sr-only">`；表头 `<th scope="col">`。
- `:focus-visible` 用 2px 暗红 outline，不用 `outline: none` 裸奔。
- 颜色对比度见第 2 节；状态不只靠颜色，另有文字（已实现 / 待 API key / 可试用）。
- 支持 `prefers-reduced-motion`；无自动播放、无动画循环。
- 移动端主内容可读宽度不受 sticky 标签影响；锚点有 `scroll-margin-top`。

## 11. 明确不用的东西

- 没有渐变、没有 box-shadow、没有 text-shadow。
- 没有圆角卡片；按钮、输入框、应用框、状态标签都是直角。
- 没有 emoji；状态用 CSS 方块，差异用 `≠`，方向用 `→`。
- 没有外部字体、CDN、图标库、图片文件、SVG 插图。
- 没有第二强调色；`critical` 与链接共用同一支暗红。
- 没有“三个等宽卡片”的模板栅格；所有分栏都由内容决定（ledger / 表格 / 双栏）。
- 没有为了装饰而存在的动画。

## 12. 取舍与已知限制

1. **系统衬线因平台而异**。macOS 的 Songti SC 最接近设计稿；Windows 会落到 SimSun，标题气质会变重。没有用 webfont 换取一致性，是为了守住“零外部请求”。
2. **数据表在手机上横向滚动**。这是有意的：把 6 列表格硬压成卡片会损失 A/B/建议值的并置关系。已在表格上方给出横向滑动提示，且页面本身不横向溢出。
3. **静态演示数据会与代码漂移**。页面中的 28 字段、页码、数值是形状示例；真实状态以仓库 README 与 `docs/` 为准。
4. **浏览器无法精确区分 CORS 与网络失败**。`fetch` 都表现为 `TypeError`；页面按 `navigator.onLine` 区分离线，其余提示“可能是 CORS / 网络 / 扩展拦截”，不假装知道具体原因。
5. **试用不是研究工具**。它只演示单次提取，不产生可引用的研究结果；输出可能错，必须回原文核对。

## 13. 验证记录

- `python3 -m http.server` 访问首页：HTTP 200。
- Chrome headless：1440×1000 与 390×844 截图检查；390px 下 `scrollWidth == clientWidth == 390`。
- 交互：三屏切换、critical 筛选（3 行）、证据展开、采纳/驳回计数、空 key 禁用、key 输入后启用、成功 JSON 格式化、401/402/CORS/非 JSON 四类错误、刷新后 key 与输出清空。
- 存储：刷新后 `localStorage.length == 0`、`sessionStorage.length == 0`。
- 请求：端点、方法、`mode=cors`、`credentials=omit`、`response_format=json_object`、提示词含规则与段落、key 不在 body 中。
- 资源：无 `<script src>`、无外部 `<link rel=stylesheet>`、无 `<img>`、无 CSS `url()`；仅 GitHub 链接、OG URL、CSP 中的 DeepSeek 端点和用户主动触发的 API 请求。
- 控制台：无 JS 异常、无 CSP 报错。
- CORS 复测（2026-11-20，Origin `https://asyazhao.github.io`）：`OPTIONS` 返回 200 并回显 Origin；带无效 key 的 `POST` 返回 401，且 401 响应同样带 `access-control-allow-origin`，因此页面可以读到并显示「密钥无效」。

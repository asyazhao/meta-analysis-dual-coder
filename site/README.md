# meta-coding · 静态产品页

这是 `site/` 目录下的独立静态产品页，用于 GitHub Pages。它不是本机 FastAPI 控制台的替代品。

页面把项目讲清楚：规则文档如何变成规则包，A / B 两个模型如何独立编码，差异如何逐字段比对，第三方裁决如何给出带原文页码的修改建议。三个界面演示全部由 HTML / CSS 绘制，没有截图文件。

## 文件

| 文件 | 作用 |
|---|---|
| `index.html` | 单页站点。CSS 与原生 JS 全部内联；无构建链、无 CDN、无外部字体、无图片文件。 |
| `.nojekyll` | 让 GitHub Pages 跳过 Jekyll 处理；`site/` 下没有需要 Jekyll 的模板。 |
| `README.md` | 本说明。 |

## 本地预览

```bash
cd site
python3 -m http.server 8080
```

打开 `http://127.0.0.1:8080/`。自测：

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/
# 200
```

## 在 GitHub Pages 启用

仓库：<https://github.com/asyazhao/meta-analysis-dual-coder>

1. 进入仓库 **Settings → Pages**。
2. **Build and deployment → Source** 选择 **Deploy from a branch**。
3. Branch 选 **main**，文件夹选 **`/site`**。
4. 保存，等待部署完成。

预期地址：

```text
https://asyazhao.github.io/meta-analysis-dual-coder/
```

页面是相对路径、自包含的单文件，放在项目站点的任意子路径也能打开。GitHub 仓库链接使用绝对地址。

## 与本地控制台的区别

本机控制台在 `src/meta_coding/web/`，是真正接密钥、PDF、规则包与运行产物的 FastAPI 应用，只监听 `127.0.0.1`。这个静态页只负责展示和一次可选的单点试用。

| | 本机控制台 `/src/meta_coding/web/` | 静态页 `site/` |
|---|---|---|
| 运行方式 | FastAPI，本机 127.0.0.1 | 纯静态 HTML，无服务端 |
| 密钥 | 写入 `.env`，页面只回显「已设置」 | 可选试用：只在浏览器内存中，不落盘 |
| PDF / 规则包 / 运行 | 可上传、解析、运行、写产物 | 不接收文件，不运行流水线 |
| 结果 | 真实运行产物与差异表 | 界面中的数值、页码、引文均为构造样例 |
| 修改意见 | 采纳/驳回写入 decisions 与 provenance | 仅切换演示状态，不写任何地方 |
| 门禁 | G2 真实调用、G3 导出由服务端拒绝 | 不适用；试用区不是 G2 |

**不要把静态页的演示数据当成研究结果。** 页面底部与演示区都标注了「演示数据，非真实研究结果」。

## 线上试用模块（可选）

页面底部第 05 节允许用户自带 DeepSeek key，从一段论文方法文字中做**一次**结构化提取。

### 它做什么

- 一个 provider 下拉：当前只列 **DeepSeek**，因为按当前实测它回显 CORS Origin，浏览器可直连。
- `POST https://api.deepseek.com/v1/chat/completions`，模型 `deepseek-chat`，要求返回 JSON 对象。
- 把返回的 JSON 格式化显示在页面下方；失败时显示可读错误。
- 请求超时 60 秒；发送中按钮禁用。

### 它不做什么

- 不解析 PDF、不读取页码、不切分表格。
- 不做双模型独立编码、逐字段比对、第三方裁决、manifest 或导出。
- 不把结果写回仓库或任何服务器。
- Anthropic 没有 CORS 头，浏览器直连会被拦；Google 未确认，因此都不列入下拉。

### 密钥与请求路径

- 输入框 `type=password`。
- 只在当前页面的 JavaScript 内存中使用；不写 `localStorage`、`sessionStorage`、cookie，也不放进 URL。
- 输入控件不设 `name`，即使 JavaScript 失效触发表单提交，key 也不会进入查询串。
- 刷新页面即失；「清空 key 与结果」会立即清空输入与输出。
- 直接发往 `api.deepseek.com`，没有中转服务器，没有请求日志。
- 页面用 CSP 的 `connect-src https://api.deepseek.com` 限制可连接的端点，降低脚本把 key 发往别处的风险。
- 浏览器扩展仍可能读取页面内容；请使用可信环境，也不要把 key 贴在截图或聊天里。
- 用 `file://` 直接打开时 Origin 为 `null`，CORS 可能失败；请用上面的 `http.server` 或 GitHub Pages 测试试用。

### 错误提示

| 情况 | 页面提示 |
|---|---|
| HTTP 401 / 403 | 密钥无效或没有权限 |
| HTTP 402 | 账户额度不足 |
| HTTP 429 | 请求被限流 |
| `fetch` 抛 `TypeError` | 请求被浏览器拦下（可能是 CORS），并区分离线 |
| 60 秒无响应 | 请求超时 |
| 返回非 JSON | 保留原始文本，提示不是合法 JSON |

DeepSeek 的 CORS 能力按当前实测标注；服务端策略可能变化。若预检失败，页面会明确提示去开发者工具的 Network 面板检查 CORS，而不是把失败伪装成成功。

复测记录（2026-11-20，Origin 使用 GitHub Pages 地址）：

- `OPTIONS /v1/chat/completions` → `200`，`access-control-allow-origin: https://asyazhao.github.io`，允许 `authorization,content-type`。
- 带一个无效 key 的 `POST` → `401`，响应同样带 `access-control-allow-origin`，所以浏览器能读到错误 JSON，页面会显示「密钥无效」而不是伪装成 CORS 失败。

## 数据与许可

- 页面里的 `scale_mean 3.54 / 3.45 / 3.54`、`Table 2, p.7`、论文编号、引文和理由都是构造样例，用于展示界面形状。
- 试用区输出由模型实时生成，可能有错，不是研究结果；请对照原文核对。
- 代码 MIT；文档与报告 CC BY 4.0。

## 修改方式

直接编辑 `site/index.html`。CSS 在 `<style>` 中，行为在文件末尾的单个 `<script>` 中，没有构建步骤。若改动试用逻辑，至少复测：

1. key 为空时按钮禁用；
2. 填入 key 后可发送；
3. 成功返回 JSON 能格式化显示；
4. 401 / 402 / CORS / 非 JSON 四类错误提示可读；
5. 刷新后 key 输入与输出为空，`localStorage` / `sessionStorage` 仍为空；
6. 页面无横向溢出，表格在自己的滚动容器内滚动。

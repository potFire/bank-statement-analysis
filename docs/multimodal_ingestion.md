# 多模态银行流水识别与人工复核

## 输入架构

Excel → StatementImporter（字段映射） → 标准流水 v1.1

所有 PDF（文字型、表格线、扫描件） → PyMuPDF 按页渲染 PNG → 视觉 LLM 逐页结构化识别 → BankAdapter → 标准流水 v1.1

PNG / JPG / WebP / BMP / TIFF → 视觉 LLM → BankAdapter → 标准流水 v1.1

PDF 的文本层仅作为文档属性存在，当前识别主链路不调用 find_tables/get_text，不绕过视觉大模型。

识别后统一经过 source_file、source_sheet、source_page、source_row 溯源；PDF 和图片默认进入人工审核，不直接作为信贷审核依据。

## 运行示例

安装 Python 3.11 或项目支持的环境并执行 pip install -r requirements.txt。

所有 PDF 均依赖具备图像理解能力的 OpenAI-compatible chat/completions 模型；文字型 PDF 也必须配置模型：

    python main.py scan.pdf photo.jpg --company "样例企业" --output output \
      --llm-url http://127.0.0.1:11434/v1/chat/completions \
      --llm-model your-local-vision-model

LLM 服务必须支持 image_url（base64 PNG）形式的视觉请求，且返回指定 JSON 表格。任何 PDF 页识别请求失败都会使整份文件导入中止，避免漏页继续分析；模型是否漏行或误识别仍需人工核查。没有模型时，所有 PDF 与图片明确报错。

## 人工复核

1. 首次识别生成 output/recognition_review.xlsx 和 output/recognition_manifest.json，停止后续授信指标计算。
2. 审核人员对照原 PDF 页码/图片，检查并修正交易日期、账号、收支金额、余额和摘要；不能删除识别行或改动 source_file/source_sheet/source_page/source_row。
3. 将每条交易的“审核状态”改为“已确认”，保存工作簿，再执行：

       python main.py --reviewed-xlsx output/recognition_review.xlsx \
         --company "样例企业" --output output

4. 系统验证字段结构、记录数量和来源坐标摘要，成功后再计算通用指标、风险规则并生成 Word/Markdown/JSON 报告。

该审核标记为操作人自我声明，当前不包含人员身份认证、数字签名、双人复核及审计后台。

## 安全与可靠性

- 本地模型地址可使用 localhost、127.0.0.1、::1。默认拒绝远程 URL；远端仅在 --allow-remote-llm 明确启用且 URL 为 HTTPS 时访问。外部 API 的数据出境、合规和脱敏由接入方评估。
- API Key 使用 BANK_LLM_API_KEY 环境变量；不可放入 Git 仓库。
- 逐页解析和渲染，不把整个 PDF 一次性转换成图片。默认单文件最多 200 页，图片单帧最多 3000 万像素，处理前缩放长边至不超过 3200 像素。
- PDF 每页以约 200 DPI 渲染 PNG 后提交给视觉 LLM，最大边长缩放到 3200 像素。模型默认最多生成 8192 tokens，最多允许 40 列、2000 交易行／页；页数多或单页密集时需要人工检查是否截断、漏笔，不能盲目接受不完整结果。
- 识别结构评分由必需字段覆盖、日期格式及金额格式组成；不代表 OCR 准确率、原始流水真实性或交易真实性。
- 任何未确认的 PDF/图片记录均不会默认进入分析。测试时 --allow-unreviewed 仅输出显式标注的草稿报告，且识别出现日期或金额无法解析时会拒绝生成，防止遗漏交易。
- 银行流水、人工复核工作簿、模型响应日志应存放在受控机器，output 目录已被 .gitignore 忽略。
- PDF 均走视觉 LLM。对于连续多页、没有重复表头、图片模糊或单页包含多张表格的流水，应重点人工检查漏行、跨页账户和金额方向。
- 本实现未实现通用 OCR 引擎、自训练模型、自动学习/发布银行模板、借贷自动审批或外部交叉验证。

## 代码入口

- document_ingest.py：识别文件类型、将全部 PDF 页面渲染为图片、调用视觉模型。
- vision_llm.py：OpenAI-compatible 请求、输出 JSON 校验、数据传输安全边界。
- bank_adapters.py：将候选表格规范化到标准字段。
- review_workflow.py：写出/读取人工确认表、核验来源记录完整性。
- data_processor.py：验证资金方向、金额、日期和余额关系。
- metrics_calculator.py / risk_rules.py：与输入格式无关的通用信贷分析。

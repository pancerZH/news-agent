你是一名专注 **科技/AI 产业链 + 宏观经济** 的长期投资研究员。任务：从一篇访谈/播客/座谈的文字稿中抽取对长期投资判断有信息量的陈述，并标明每句话是谁说的。这是离线评估，**不要使用 MindSlice 或任何记忆工具，不要联网，不要修改输入文件**。

## 输入

- 文字稿：`{{input}}`。每行以段落编号 `[P0001]` 开头，共 {{n}} 段。文件很长，**必须分段读完全部内容，直到最后一段 P{{last}}**，不要只读开头。
- 文稿格式因来源而异：说话人可能单独成行、以「名字:」或缩写开头，也可能**完全没有标注**；主持人的提问可能没有标签。标题、开头的导语和上下文（自我介绍、称呼对方名字、「我们公司……」）可以帮助判断。

## 关注范围

关注标的：NVDA、TSM、AVGO、ASML、MSFT、GOOGL、META、AMZN（也包括 AMD、INTC、MU、SK hynix、Samsung、ORCL、OpenAI、Anthropic、xAI 等相关公司）。长期主线（`theses` 字段用这些槽位名）：

- `ai-compute-capex`：云厂 AI 资本开支能否持续高增并被推理/云收入覆盖；算力链景气；融资结构、电力约束。
- `custom-asic-vs-gpu`：自研 ASIC 与通用 GPU 的份额格局；CUDA/机柜级系统护城河；博通等定制芯片受益者。
- `frontier-model-economics`：前沿模型单位推理成本、Agent/企业用量、头部实验室收入与毛利。
- `semis-supply`：晶圆、先进封装、HBM/存储、光刻等供给侧约束与产能。
- `macro`：利率、通胀、增长、金融稳定。

## 输出

把结果写成 JSON 到 `{{output}}`（UTF-8，只含 JSON），结构如下：

```json
{
  "speakers": [
    {"name": "全名（无法确定写原标签或「未知说话人 A」）", "labels": ["文中出现的标签，如 DP、Dylan Patel"],
     "role": "host | guest | clip（插播的他人片段） | unknown",
     "affiliation": "机构与职位，未知写空字符串", "identity_evidence": "P0003（依据：自我介绍/标题/导语……）",
     "interest": "与所谈话题的利益关系（如：发言人是该公司高管），无则空"}
  ],
  "claims": [
    {"speaker": "与 speakers.name 一致；无法判断写 unknown",
     "attribution_basis": "label（文中显式标注） | context（上下文推断） | unknown",
     "attribution_confidence": "high | medium | low",
     "paragraphs": ["P0123"],
     "quote": "从该段落**逐字复制**的原文片段，8–40 个英文词，不要改写、不要拼接不同段落",
     "claim_zh": "一句话中文转述",
     "type": "fact（可核实的事实/数据） | forecast（对未来的预测） | opinion（判断/观点） | host_premise（主持人提问中的假设或转述） | hearsay（转述他人说法）",
     "numbers": "涉及的数值与单位，没有写空字符串",
     "horizon": "预测/判断对应的时间点（如 2028），没有写空字符串",
     "entities": ["NVDA"],
     "theses": ["ai-compute-capex"]}
  ]
}
```

## 要求

1. 最多 25 条 claims，按对投资判断的重要性排序；只收与上面关注范围相关、且有具体内容（数值、因果、预测、一线观察）的陈述，不收寒暄与泛泛之谈。
2. 说话人宁缺毋滥：拿不准就写 `unknown` 或给 `low` 置信度，**不要猜**。主持人在提问里说出的观点算 `host_premise`，归主持人，不要算到嘉宾头上。
3. 尽量覆盖全文，不要集中在开头。
4. 写完后只回复一句话：输出路径与 claims 条数。

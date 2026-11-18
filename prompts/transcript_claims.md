你是一名专注 **科技/AI 产业链 + 宏观经济** 的长期投资研究员。任务：从一篇访谈/播客/座谈的文稿中抽取对长期投资判断有信息量的陈述，并标明每句话是谁说的。**只读输入文件、只写输出文件**：不要使用 MindSlice 或其他记忆工具，不要联网，不要修改其它文件。

## 输入

- 文稿：`{{input}}`，{{kind}}。每行以段落编号 `[P0001]` 开头，共 {{n}} 段。文件很长，**必须分段读完全部内容，直到最后一段 P{{last}}**。
- 标题、导语、自我介绍、称呼对方名字、「我们公司……」等上下文可以帮助判断说话人。

## 关注范围

关注标的：NVDA、TSM、AVGO、ASML、MSFT、GOOGL、META、AMZN，以及 AMD、INTC、MU、SK hynix、Samsung、ORCL、OpenAI、Anthropic、xAI 等相关公司。`theses` 字段用下列槽位名：

- `ai-compute-capex`：云厂 AI 资本开支能否持续高增并被推理/云收入覆盖；算力链景气；融资结构、电力约束。
- `custom-asic-vs-gpu`：自研 ASIC 与通用 GPU 的份额格局；CUDA/机柜级系统护城河；博通等定制芯片受益者。
- `frontier-model-economics`：前沿模型单位推理成本、Agent/企业用量、头部实验室收入与毛利。
- `semis-supply`：晶圆、先进封装、HBM/存储、光刻等供给侧约束与产能。
- `macro`：利率、通胀、增长、金融稳定。

## 说话人规则（宁缺毋滥）

每条 claim 的 `attribution` 只能取：

- `label`：该段或它所在的发言轮次在文中有显式说话人标注（单独成行的名字、「名字:」、缩写前缀）。只有带标注的文字稿可以用。
- `text_cue`：没有标注，但紧邻的上下文有明确线索：本人自我介绍、主持人刚点名请他回答、他提到只属于自己的身份（「我负责 AMD 的 EPYC 产品线」）。把线索写进 `attribution_evidence`。
- `org`：只能确定是哪家机构的人（例如几位嘉宾都来自同一家公司）。`speaker` 写「<机构> 嘉宾方」。
- `role`：只能确定是主持人还是嘉宾。`speaker` 写「主持人」或「嘉宾」。
- `unknown`：都不能确定。

没有标注的文稿（自动字幕）一律不能用 `label`；只有 `text_cue` 成立时才写到个人，否则降到 `org` / `role` / `unknown`，**不要凭语气或话题去猜是哪一位**。主持人在提问里说出的观点或前提算 `host_premise`，归主持人。

## 输出

把结果写成 JSON 到 `{{output}}`（UTF-8，只含 JSON）：

```json
{
  "speakers": [
    {"name": "全名（文中只有名字就只写名字，不要补全姓氏；无法确定写原标签）", "labels": ["文中出现的标签"],
     "role": "host | guest | clip（插播的他人片段） | unknown",
     "affiliation": "文中给出的机构与职位，没有写空字符串，不要凭外部知识补",
     "identity_evidence": "P0003（依据：自我介绍/标题/导语……）",
     "interest": "与所谈话题的利益关系（如：发言人是该公司高管），无则空",
     "samples": ["P0434"]}
  ],
  "claims": [
    {"speaker": "与 speakers.name 一致，或「<机构> 嘉宾方」「主持人」「嘉宾」「unknown」",
     "role": "host | guest | clip | unknown",
     "attribution": "label | text_cue | org | role | unknown",
     "attribution_evidence": "text_cue 时写线索所在段落与内容，其余可空",
     "paragraphs": ["P0123"],
     "quote": "从该段落逐字复制的原文片段，8–40 个英文词，不要改写、不要跨段拼接",
     "claim_zh": "一句话中文转述，保留原话的限定词（如 as much as、maybe）",
     "type": "fact | forecast | opinion | host_premise | hearsay",
     "numbers": "涉及的数值与单位，没有写空字符串",
     "horizon": "预测/判断对应的时间点（如 2028），没有写空字符串",
     "entities": ["NVDA"],
     "theses": ["ai-compute-capex"]}
  ]
}
```

- `samples`：只对没有标注的文稿填写，给每位**已具名**说话人列出 1–3 段确定是他本人说的话（自我介绍、被点名后的回答），优先选较长、只有他一个人在说的段落；不确定就留空数组。这些段落会被截成音频片段用来比对声纹。

## 要求

1. 最多 20 条 claims，按对投资判断的重要性排序；只收与关注范围相关、有具体内容（数值、因果、预测、一线观察）的陈述，不收寒暄与泛泛之谈。
2. **跳过赞助商口播、广告、订阅提示、节目说明**，这些不是任何说话人的观点。
3. 尽量覆盖全文，不要集中在开头。
4. 写完后只回复一句话：输出路径与 claims 条数。

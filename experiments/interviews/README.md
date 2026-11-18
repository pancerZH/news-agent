# 访谈音视频实验

用同一期节目的不同来源回答一个问题：直接处理音轨，每小时能比已有文字多得到几条可核验的研究陈述？演讲、播客、访谈视频均可使用本地媒体文件的第一条音轨。每次只评一个固定窗口。

## 转录

从仓库根目录运行。实验依赖装在独立环境，本地音频、转录和评分表放在 Git 忽略的 `data/` 中。计时从加载转录库开始，包含解码输入文件、加载模型和处理整段采样，使用已下载的模型目录可排除下载耗时。长视频可先准备音轨，因为解码器会读取整个输入文件。

```sh
D=data/interview-eval
mkdir -p "$D"
uv venv "$D/venv"
uv pip install --python "$D/venv/bin/python" -r experiments/interviews/requirements.txt
"$D/venv/bin/python" experiments/interviews/transcribe.py "$D/opening.mp3" \
  --model large-v3-turbo --language en --device cuda --start 0 --seconds 300 > "$D/asr.json"
```

GPU 运行需要 CUDA 运行库。可用 `--device cpu --model base.en` 跑英文小模型。输出保留源文件时间轴、实际解码时长和逐段文本；设备、精度及耗时随结果记录。独立裁出的文件以该文件的开头为零点。

## 评估

1. 转录前选定音频文件和时间窗。在 `plan.json` 固定节目 URL、媒体文件名、起止范围和少量具名关键点。用同一期的署名逐字稿或独立回听选点。可按开头、中段和听众问答分别抽样，每个窗口独立计分。
2. 冻结文字基线。分别保存采集器实际取得的标题和摘要、节目简介、署名逐字稿及选定报道。记录 URL、版本或存档。只能取得部分文本时标为 `unchecked`。公布后的回顾性对照要注明资料取得时间。
3. 从 ASR 提取候选项，再对照文字判定。一次只评一个可证伪的命题，合并重复表述。保存原问题、回答要点、带起止时间的回答片段、说话人、身份依据和性质（如主持人假设、嘉宾估计或预测）。姓名依据可来自署名逐字稿或自我介绍。出现匿名说话人标签时保留不确定性。
4. 对每条候选项确认实体、方向、数值、单位和时间范围。只有可由证据支持的表述才能记为 `supported`。先前文字已覆盖同一命题记为 `present`，检查后确实缺失记为 `absent`。相对全部文字新增的陈述还需回到原始媒体核验并填 `media_checked: true`。涉及只有图表显示的数值时，保留视频帧时间及坐标轴、单位和图例。
5. 人工审核时记录实际复核总分钟数和实际播放的回听分钟数（包括重复播放）。无人计时时两项均填 `null`。另列待回听区间，评分器合并重叠时间，只估算一遍原速播放的长度。
6. 将支持的候选与预选关键点匹配，按不同关键点计召回。漏项保留在分母。审阅者看过参考答案时注明单人非盲评。新增数按实际音频时长折算，短片结果只描述本次样本。

评分输入格式如下。示意只列一个基线；实际运行时每条通过的陈述须逐一填写所有基线。可在 `window` 外另存问题的时间区间。

```json
{"episode":"episode-url","media":"opening.mp3","start":0,"seconds":300,
 "anchors":{"capacity":"预先选定的产能估计，含单位及年份"}}
```

```json
{"episode":"episode-url","model":"large-v3-turbo","baselines":{"transcript":"署名稿来源和版本"},
 "claims":[{"id":"capacity-estimate","anchor":"capacity","window":[60,75],
            "speaker":"嘉宾姓名","identity_basis":"署名稿段落","question":"主持人的原问题要点",
            "answer":"嘉宾给出的产能估计","kind":"嘉宾估计","evidence":"逐字稿或媒体定位",
            "verdict":"supported","coverage":{"transcript":"present"},"media_checked":false}],
 "replay_windows":[[60,75]],"human_review_minutes":null,"human_replay_minutes":null}
```

```sh
python3 experiments/interviews/score.py "$D/asr.json" "$D/plan.json" "$D/review.json"
```

`absent_by_baseline` 是相对各文字来源缺失的受支持陈述数。`confirmed_new_vs_all` 要求所有基线均已检查且缺失，同时已核验原始媒体。`pending_novelty` 保留至少一个基线待查或尚未回听的可能新增项。`missed_anchors` 列出未由受支持候选恢复的预选要点。

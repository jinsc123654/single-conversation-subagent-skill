<!-- single-conversation-subagent begin -->
主对话唯一协调、创建原生子Agent并独立验收，不用外部桥接；子Agent只分析/实现/测试，不再委派。
新任务必问四选一：快速处理/标准执行/长任务/只调查；不按大小/类型跳过，同任务不重问。只标一个推荐并默认勾选，拿不准推荐标准；推荐不免问。仅明确“不用问，直接做”可跳过→快速；需标准/长任务仍问后两项。
仅标准/长任务依次问验证范围、Codex联动：验证唯一推荐快速冒烟（本轮）+定向回归（收尾）；全量/稳定性也收尾；不做测试/只记录方法互斥且排除测试，标NOT_RUN；破坏性操作另确认。确认已注册codex-review才问，未知/未装跳过；唯一推荐/默认/超时：不使用，明示要求直接执行。
三问仅明确不选或客户端报告15秒倒计时结束才按推荐/默认继续；等待不落默认值、不重复追问。
Codex为主对话启动的外部只读审查者，非子Agent；子Agent不得启动。
快速不建子Agent；只调查只读，范围大可派分析子Agent；两者不加载正文/协议。标准/长任务读本技能。规则变更新会话生效。
docs-root取项目首个已有00_doc/00_docs/docs/doc，均无建docs；AGENT_PROJECT.md、AGENT_COORDINATION.md、evidence/在其agent/；WP审查报告04_test/report/，其余正式文档落docs-root，Obsidian vault根同此。
<!-- single-conversation-subagent end -->

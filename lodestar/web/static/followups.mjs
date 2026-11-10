const FRESH = /(?:最新|最近|近期|今天|本周|本月|新模型|发布|上线|新闻|动态|\b(?:latest|recent|today|this week|new model|release|news|current)\b)/i;

// These are optional next-turn choices, not assertions or hidden commands.
// They avoid another model call and remain visible/editable as normal prompts.
export function suggestedPrompts({ answer, userPrompt, sourceCount = 0, error = false }) {
  if (error || typeof answer !== "string" || answer.trim().length < 120) return [];
  if (sourceCount || FRESH.test(userPrompt || "")) return [
    { title: "核对关键依据", detail: "区分它支持与不能证明的内容", prompt: "请选出上面最关键的两条来源，分别说明它们支持什么、还不能证明什么。" },
    { title: "继续落到下一步", detail: "把结论变成最小行动", prompt: "基于上面的内容，给我一个最小的下一步：要验证什么、看什么证据、什么结果会改变判断？" },
  ];
  return [
    { title: "用例子说明", detail: "换成一个具体场景", prompt: "请用一个贴近实际的例子解释上面最关键的机制或判断。" },
    { title: "比较两条路线", detail: "把取舍讲清楚", prompt: "请给出两条可选的下一步路线，并说明各自的收益、代价和适用条件。" },
  ];
}

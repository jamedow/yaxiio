/**
 * Commander 智能路由器
 * ====================
 * 意图识别 + 路由策略。
 *
 * 支持模式:
 *   - keyword: 关键词匹配 (快速, 离线)
 *   - semantic: LLM 语义分析 (精确, 在线)
 */

// ═══════════════════════════════════════════════════════════
// 类型
// ═══════════════════════════════════════════════════════════

export interface RoutingDecision {
  action: "self" | "dispatch" | "handle";
  target: string;
  task: string;
  confidence: number;
  method: "keyword" | "semantic" | "fallback";
}

export interface IntentKeywordMap {
  [intent: string]: {
    patterns: RegExp[];
    target: string;
    priority: number;   // 1=低, 5=高
    examples: string[];
  };
}

// ═══════════════════════════════════════════════════════════
// 意图关键词矩阵
// ═══════════════════════════════════════════════════════════

const INTENT_MAP: IntentKeywordMap = {
  // 翻译相关
  translation: {
    patterns: [
      /翻译|translate|翻成|转为.*语|translate|lang|language/i,
      /俄语|英语|法语|西班牙语|阿拉伯语|russian|english|french|spanish|arabic/i,
      /localization|本地化|多语言|multilingual/i,
    ],
    target: "翻译官",
    priority: 4,
    examples: ["翻译这份产品规格书", "translate this to Russian", "把这个页面翻译成阿拉伯语"],
  },

  // 商务/报价
  business: {
    patterns: [
      /报价|报价单|quotation|询价|inquiry|询盘|发盘/i,
      /价格|价格表|pricing|price|cost|费用/i,
      /客户|客户经理|customer|client|lead/i,
      /订单|order|contract|合同|签约/i,
      /50MW|100MW|光伏|solar|支架|bracket|tracker/i,
      /沙特|中东|非洲|东南亚|巴西|智利|项目/i,
    ],
    target: "商务经理",
    priority: 3,
    examples: ["给沙特50MW光伏项目报价", "分析这个客户询盘", "生成热镀锌支架报价单"],
  },

  // 技术/售前
  presales: {
    patterns: [
      /规格|specification|spec|技术参数|datasheet/i,
      /方案|solution|选型|material selection/i,
      /标准|standard|ISO|ASTM|GB|DIN|JIS/i,
      /技术咨询|technical|engineering|工程师/i,
      /对比|compare|versus|vs|哪种好/i,
    ],
    target: "售前经理",
    priority: 3,
    examples: ["对比热镀锌和达克罗防腐方案", "推荐适合高风压地区的光伏支架", "这个项目用什么标准"],
  },

  // 开发/代码
  development: {
    patterns: [
      /build|开发|修复|代码|code|component|bug|fix/i,
      /部署|deploy|docker|kubernetes|nginx|服务器|server/i,
      /API|api|REST|GraphQL|SQL|数据库|database|mongo|redis/i,
      /前端|frontend|Vue|Nuxt|React|TypeScript|组件|样式|CSS/i,
      /后端|backend|Spring|Java|Controller|Service|Node/i,
      /测试|test|jest|vitest|pytest|单元测试/i,
      /SEO|seo|sitemap|hreflang|schema|排名|keyword/i,
    ],
    target: "dev-agent",
    priority: 3,
    examples: ["修复登录页面的bug", "部署到生产环境", "优化MySQL查询性能"],
  },

  // 内容/CMS
  content: {
    patterns: [
      /文章|article|blog|博客|白皮书|whitepaper/i,
      /发布|publish|草稿|draft|编辑|审核|审批/i,
      /封面|cover|banner|图片|图片处理|image/i,
      /CMS|cms|内容管理/i,
      /论坛|forum|社区|community/i,
    ],
    target: "CMS工程师",
    priority: 2,
    examples: ["发布这篇白皮书", "生成文章封面图", "审核待发布的内容"],
  },

  // 审计/质检
  audit: {
    patterns: [
      /审计|audit|review|检查|抽检|质检|核查|验证/i,
      /质量|quality|QM|术语|一致性|参数真实性/i,
      /DQF|MQM|质量框架/i,
    ],
    target: "审计官",
    priority: 2,
    examples: ["审计俄语产品页面质量", "抽检多语言术语一致性", "检查参数真实性"],
  },

  // 产品搜索
  productSearch: {
    patterns: [
      /产品|product|搜索|search|查找|找.*产品|有哪些|有没有|支持.*规格/i,
      /规格|品类|分类|category|行业|industry/i,
      /库存|inventory|stock|供货/i,
    ],
    target: "产品搜索Agent",
    priority: 2,
    examples: ["搜索适合沙漠环境的光伏支架", "有没有ISO 1461认证的地桩", "查一下C型钢规格"],
  },

  // 设计/UI
  design: {
    patterns: [
      /设计|design|UI|UX|界面|布局|layout|配色|color|字体|font/i,
      /组件|component|样式|style|主题|theme|响应式|responsive/i,
      /动画|animation|交互|interaction|hover|click/i,
      /可访问性|accessibility|a11y|WCAG/i,
    ],
    target: "UI设计师",
    priority: 2,
    examples: ["设计产品详情页布局", "调整主色调", "优化移动端响应式"],
  },

  // 基础设施
  infrastructure: {
    patterns: [
      /部署|deploy|CI|CD|流水线|pipeline|构建|build|打包/i,
      /监控|monitor|报警|alert|日志|log|性能|performance/i,
      /安全|security|防火墙|firewall|漏洞|vulnerability|依赖/i,
      /服务器|server|VPS|云|cloud|docker|k8s|kubernetes/i,
    ],
    target: "架构运维工程师",
    priority: 2,
    examples: ["部署应用到生产环境", "检查Core Web Vitals指标", "升级依赖包"],
  },
};

// ═══════════════════════════════════════════════════════════
// 快捷命令 — 直接路由，跳过意图识别
// ═══════════════════════════════════════════════════════════

const QUICK_COMMANDS: Record<string, { action: "dispatch"; target: string }> = {
  "/translate": { action: "dispatch", target: "翻译官" },
  "/报价": { action: "dispatch", target: "商务经理" },
  "/quote": { action: "dispatch", target: "商务经理" },
  "/tech": { action: "dispatch", target: "售前经理" },
  "/audit": { action: "dispatch", target: "审计官" },
  "/build": { action: "dispatch", target: "dev-agent" },
  "/deploy": { action: "dispatch", target: "dev-agent" },
  "/design": { action: "dispatch", target: "UI设计师" },
  "/seo": { action: "dispatch", target: "SEO工程师" },
  "/product": { action: "dispatch", target: "产品搜索Agent" },
  "/infra": { action: "dispatch", target: "架构运维工程师" },
  "/cms": { action: "dispatch", target: "CMS工程师" },
};

// ═══════════════════════════════════════════════════════════
// 意图识别核心
// ═══════════════════════════════════════════════════════════

export function recognizeIntent(
  userInput: string,
  options: { useLLM?: boolean; llmClient?: any } = {}
): RoutingDecision {
  // ── 阶段 0: 快捷命令 ──
  for (const [cmd, route] of Object.entries(QUICK_COMMANDS)) {
    if (userInput.startsWith(cmd)) {
      // 提取命令后的任务描述
      const task = userInput.slice(cmd.length).trim() || userInput;
      return {
        action: "dispatch",
        target: route.target,
        task,
        confidence: 1.0,
        method: "keyword",
      };
    }
  }

  // ── 阶段 1: 关键词匹配 ──
  let bestMatch: { target: string; priority: number; score: number } | null = null;

  for (const [, intent] of Object.entries(INTENT_MAP)) {
    let score = 0;
    for (const pattern of intent.patterns) {
      if (pattern.test(userInput)) {
        score += 1;
      }
    }
    if (score > 0) {
      const weightedScore = score * intent.priority;
      if (!bestMatch || weightedScore > bestMatch.score) {
        bestMatch = {
          target: intent.target,
          priority: intent.priority,
          score: weightedScore,
        };
      }
    }
  }

  if (bestMatch && bestMatch.priority >= 1) {
    // 置信度基于匹配数 / 最大可能匹配数
    const confidence = Math.min(
      bestMatch.score / (5 * bestMatch.priority), // max possible: all patterns matched
      0.98
    );
    return {
      action: "dispatch",
      target: bestMatch.target,
      task: userInput,
      confidence: Math.max(confidence, 0.5),
      method: "keyword",
    };
  }

  // ── 阶段 2: 启发式规则 ──

  // 包含"?"或疑问词 → 可能是简单问答
  if (
    /^(what|who|when|where|why|how|什么是|如何|怎么|为什么|谁|什么|哪)/i.test(
      userInput
    ) &&
    userInput.length < 60
  ) {
    return {
      action: "self",
      target: "",
      task: userInput,
      confidence: 0.7,
      method: "keyword",
    };
  }

  // 极短输入（< 15 chars）→ 简单对话
  if (userInput.length < 15) {
    return {
      action: "self",
      target: "",
      task: userInput,
      confidence: 0.6,
      method: "fallback",
    };
  }

  // ── 阶段 3: 语义分析 (如果启用) ──
  if (options.useLLM && options.llmClient) {
    // TODO: 调用 LLM 做语义意图分析
    // 发送给 Commander 内部的 LLM Router
  }

  // ── 阶段 4: Fallback ──
  // 找不到匹配 → 不干预，让当前 Agent 自行处理
  return {
    action: "self",
    target: "",
    task: userInput,
    confidence: 0.3,
    method: "fallback",
  };
}

/**
 * 检查输入是否应被 Commander 干预
 */
export function shouldIntercept(text: string): boolean {
  // 永远不拦截 Commander 自身命令
  if (text.startsWith("/commander")) return false;
  // 不拦截空输入
  if (!text.trim()) return false;

  const decision = recognizeIntent(text);
  return decision.action === "dispatch" || decision.action === "handle";
}

/**
 * 获取所有可用的意图目标
 */
export function getAvailableTargets(): string[] {
  const targets = new Set<string>();
  for (const [, intent] of Object.entries(INTENT_MAP)) {
    targets.add(intent.target);
  }
  return Array.from(targets);
}

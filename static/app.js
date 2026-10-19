/* The UI renders server-returned facts, checks and traces without inventing progress. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = { result: null, input: null, running: false, examples: [], catalog: null, elapsedTimer: null, toastTimer: null };
  const presets = {
    campus: { product: "matebook14-ultra", audience: "大学生", scene: "开学季", platform: "小红书", goal: "帮助新生理解适合自己的使用体验，引导收藏选购要点", brief: "围绕 MateBook 14 酷睿 Ultra 写一篇自然、有具体使用场景的小红书图文，避免堆参数。围绕便携、学习效率与日常使用组织内容，给出有出处的配图建议。配置以知识库检索结果为准，不编造优惠价格。" },
    business: { product: "matebook14-ultra", audience: "职场人士", scene: "职场办公", platform: "公众号", goal: "解释真实办公场景中的产品价值，帮助读者判断适用性", brief: "围绕 MateBook 14 酷睿 Ultra 撰写一篇公众号场景文章。以一天的移动办公需求展开，表达克制、结构清楚。将具体功能和知识库中的产品事实对应起来，选择与实际型号匹配的图片，不承诺未经核实的续航或性能。" },
    store: { product: "matebook14-ultra", audience: "到店顾客", scene: "门店到访", platform: "门店朋友圈", goal: "用简短、亲切的内容引导到店体验与咨询", brief: "围绕 MateBook 14 酷睿 Ultra 写一段适合门店导购朋友圈发布的图文，正文尽量控制在 180 字以内。强调可以现场体验的具体功能，不编造门店地址、活动期限、现货情况、赠品或价格；活动信息需要时请标明待门店确认。" }
  };

  function textOf(value) {
    if (value === null || value === undefined) return "";
    if (Array.isArray(value)) return value.map(textOf).filter(Boolean).join("；");
    if (typeof value === "object") return JSON.stringify(value, null, 2);
    return String(value);
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = textOf(text);
    return node;
  }

  function empty(container, message) {
    container.replaceChildren(element("p", "empty-state", message));
  }

  function safeUrl(value) {
    if (typeof value !== "string" || !value.trim()) return null;
    try {
      const url = new URL(value, location.origin);
      return ["https:", "http:"].includes(url.protocol) ? url.href : null;
    } catch (_) { return null; }
  }

  function link(label, url, className) {
    const clean = safeUrl(url);
    if (!clean) return element("span", className, label);
    const node = element("a", className, label);
    node.href = clean;
    node.target = "_blank";
    node.rel = "noopener noreferrer";
    return node;
  }

  function toast(message) {
    clearTimeout(state.toastTimer);
    $("toast").textContent = message;
    $("toast").hidden = false;
    state.toastTimer = setTimeout(() => { $("toast").hidden = true; }, 3500);
  }

  function showNotice(title, message, questions, isError = false) {
    const target = $("notice");
    target.className = isError ? "notice error" : "notice";
    target.replaceChildren(element("h3", "", title));
    if (message) target.append(element("p", "", message));
    if (Array.isArray(questions) && questions.length) {
      const list = element("ul");
      questions.forEach((question) => list.append(element("li", "", question)));
      target.append(list);
    }
    target.hidden = false;
  }

  async function request(path, options = {}, timeoutMs = 30000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(path, { ...options, signal: controller.signal, headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers } });
      let data;
      try { data = await response.json(); } catch (_) { throw new Error("服务未返回有效的 JSON 结果，请稍后重试。"); }
      if (!response.ok) {
        const detail = data.message || data.detail || data.error || `请求未完成（HTTP ${response.status}）`;
        throw new Error(textOf(detail));
      }
      return data;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("本次等待已超时。任务仍可能在后台执行，请稍后检查运行结果或重试。");
      throw error;
    } finally { clearTimeout(timer); }
  }

  function modeLabel(mode) {
    const names = { live: "实时检索与生成", live_llm: "模型生成 · ima 产品资料", online: "实时检索与生成", llm: "模型生成", ima_live: "ima 实时检索", live_ima: "ima 实时检索", local: "本地资料模式", local_fallback: "本地资料备用模式", offline: "离线资料模式", demo: "演示模式", fallback: "备用模式", hybrid: "混合检索模式", rules: "规则生成模式", rule_based: "规则生成模式", verified: "已核验资料模式", clarification: "等待信息确认", unconfigured: "生成服务待配置" };
    return names[mode] || textOf(mode);
  }

  async function loadStatus() {
    $("refresh-status").disabled = true;
    $("connection-dot").className = "status-dot loading";
    try {
      const result = await request("/api/status");
      const connected = result.ima_connected === true;
      const cached = result.source_mode === "verified_cache";
      $("connection-dot").className = connected || cached ? "status-dot online" : "status-dot warning";
      const labels = [cached ? "产品资料可用" : connected ? "ima 知识库已连接" : "资料连接待恢复"];
      if (result.llm_configured === false) labels.push("生成模型待配置");
      $("connection-label").textContent = labels.join(" · ");
      $("connection-label").title = textOf(result.message);
      // Source links are shown beside the adopted facts and images.
      if (result.github_url && safeUrl(result.github_url)?.startsWith("https://")) {
        $("github-link").href = safeUrl(result.github_url);
        $("github-link").target = "_blank";
        $("github-link").rel = "noopener noreferrer";
        $("github-link").classList.remove("disabled-link");
        $("github-link").removeAttribute("aria-disabled");
        $("github-link").title = "查看项目源代码";
      }
    } catch (error) {
      $("connection-dot").className = "status-dot warning";
      $("connection-label").textContent = "暂时无法读取服务状态";
      $("connection-label").title = error.message;
      // The status label above already states the connection issue.
    } finally { $("refresh-status").disabled = false; }
  }

  async function loadCatalog() {
    try {
      const catalog = await request("/api/catalog");
      state.catalog = catalog;
      if (Array.isArray(catalog.products)) {
        const selection = $("product").value;
        $("product").replaceChildren(new Option("请选择完整产品型号", "auto"));
        catalog.products.forEach((product) => {
          const name = typeof product === "string" ? product : product.name;
          const id = typeof product === "string" ? product : product.id || name;
          if (name && id !== "auto") {
            const unavailable = typeof product === "object" && product.available === false;
            const singleImage = !unavailable && typeof product === "object" && product.image_count === 1;
            const option = new Option(name + (unavailable ? "（来源待核对）" : singleImage ? "（1张图：朋友圈/海报）" : ""), id);
            option.disabled = unavailable;
            $("product").add(option);
          }
        });
        if ([...$("product").options].some((option) => option.value === selection)) $("product").value = selection;
      }
      $("quick-product").replaceChildren(new Option("从这句话里识别", "auto"));
      [...$("product").options].filter(o=>o.value!=="auto").forEach(o=>{const option=new Option(o.textContent,o.value);option.disabled=o.disabled;$("quick-product").add(option);});
      if (Array.isArray(catalog.platforms) && catalog.platforms.length) {
        const selection = $("platform").value;
        $("platform").replaceChildren();
        catalog.platforms.forEach((platform) => {
          const name = typeof platform === "string" ? platform : platform.name || platform.label;
          if (name) $("platform").add(new Option(name, name));
        });
        if ([...$("platform").options].some((option) => option.value === selection)) $("platform").value = selection;
      }
      // Keep all eight contest scenes available; custom input is independent.
      const requiredScenes = ["开学季", "职场办公", "内容创作", "家庭换新", "企业采购", "门店到访", "新品首发", "节日营销", "生态体验"];
      const selection = $("scene").value;
      $("scene").replaceChildren(new Option("请选择场景", ""));
      requiredScenes.forEach((name) => $("scene").add(new Option(name, name)));
      $("scene").add(new Option("自定义", "custom"));
      if ([...$("scene").options].some((option) => option.value === selection)) $("scene").value = selection;

    } catch (_) { /* The form remains usable with its explicitly labelled defaults. */ }
  }

  function syncCustom(key) {
    const custom = $(key + "-custom");
    custom.hidden = $(key).value !== "custom";
    custom.required = !custom.hidden;
  }

  function initBenefits() {
    const target = $("quote-benefits");
    ["门店优惠", "平台权益", "换新 / 专属权益"].forEach((name, index) => {
      const row = element("div", "benefit-editor");
      row.innerHTML = `<div class="benefit-label"><span>0${index + 1}</span><label><input type="checkbox" id="benefit-${index}-stackable">可叠加</label></div><div class="benefit-grid"><label class="field"><span>权益名称</span><input id="benefit-${index}-label" maxlength="100" placeholder="${name}"></label><label class="field"><span>计算方式</span><select id="benefit-${index}-kind"><option value="fixed">减免金额（元）</option><option value="percent">优惠比例（%）</option></select></label><label class="field"><span>优惠数值</span><input id="benefit-${index}-amount" type="number" min="0" step="0.01" placeholder="例：100 / 5"></label><label class="field benefit-condition"><span>适用条件</span><input id="benefit-${index}-conditions" maxlength="300" placeholder="领取资格、适用门店或其他条件"></label><label class="field"><span>权益来源链接</span><input id="benefit-${index}-source" type="url" maxlength="1000" placeholder="填写本项权益的 HTTPS 来源"></label><label class="field"><span>确认日期</span><input id="benefit-${index}-date" type="date"></label><label class="field"><span>有效期至</span><input id="benefit-${index}-expires" type="date"></label><label class="field"><span>互斥组</span><input id="benefit-${index}-exclusive" maxlength="80" placeholder="互斥权益填写相同名称"></label></div>`;
      target.append(row);
    });
  }

  function readInput() {
    const data = {};
    ["product", "audience", "scene", "platform", "goal", "brief"].forEach((key) => { data[key] = ["audience", "scene"].includes(key) && $(key).value === "custom" ? $(key + "-custom").value.trim() : $(key).value.trim(); });
    data.customer_context = {};
    ["occupation", "budget", "devices", "concerns", "purchase_stage"].forEach((key) => { const value = $("customer-" + key).value.trim(); if (value) data.customer_context[key] = value; });
    const quote = { base_price: $("quote-base-price").value, sku: $("quote-sku").value.trim(), source_url: $("quote-source-url").value.trim(), verified_at: $("quote-verified-at").value, benefits: [] };
    for (let index = 0; index < 3; index++) {
      const value = (key) => $("benefit-" + index + "-" + key).value.trim();
      if (!value("amount") && !value("label")) continue;
      quote.benefits.push({ id: "benefit" + index, label: value("label") || ["门店优惠", "平台权益", "换新权益"][index], kind: value("kind"), amount: value("amount"), source_url: value("source"), verified_at: value("date"), valid_until: value("expires"), conditions: value("conditions"), stackable: $("benefit-" + index + "-stackable").checked, exclusive_group: value("exclusive") });
    }
    if (quote.base_price || quote.sku || quote.source_url || quote.benefits.length) data.quote_inputs = quote;
    data.store_profile = readStoreProfile();
    data.content_nonce = String(Date.now());
    return data;
  }

  function fillInput(input) {
    ["product", "audience", "scene", "platform", "goal", "brief"].forEach((key) => {
      if (input[key] === undefined) return;
      const node = $(key);
      if (["audience", "scene"].includes(key)) {
        const matched = [...node.options].find((item) => item.value === input[key] && item.value !== "custom");
        node.value = matched ? matched.value : "custom";
        $(key + "-custom").value = matched ? "" : textOf(input[key]);
        syncCustom(key);
      } else if (node.tagName === "SELECT") {
        let option = [...node.options].find((item) => item.value === input[key] || item.text === input[key]);
        if (!option) { option = new Option(textOf(input[key]), textOf(input[key])); node.add(option); }
        node.value = option.value;
      } else node.value = textOf(input[key]);
    });
    const customer = input.customer_context || {};
    ["occupation", "budget", "devices", "concerns", "purchase_stage"].forEach((key) => { $("customer-" + key).value = textOf(customer[key]); });
    const quote = input.quote_inputs || {};
    [["quote-base-price", "base_price"], ["quote-sku", "sku"], ["quote-source-url", "source_url"], ["quote-verified-at", "verified_at"]].forEach(([id, key]) => { $(id).value = textOf(quote[key]); });
    for (let index = 0; index < 3; index++) {
      const benefit = (quote.benefits || [])[index] || {};
      [["label", "label"], ["kind", "kind"], ["amount", "amount"], ["source", "source_url"], ["date", "verified_at"], ["expires", "valid_until"], ["conditions", "conditions"], ["exclusive", "exclusive_group"]].forEach(([id, key]) => { $("benefit-" + index + "-" + id).value = textOf(benefit[key] || (key === "kind" ? "fixed" : "")); });
      $("benefit-" + index + "-stackable").checked = benefit.stackable === true;
    }
  }

  function setRunning(running) {
    state.running = running;
    $("generate-button").disabled = running;
    $("regenerate-button").disabled = running;
    $("generate-button").firstElementChild.textContent = running ? "正在生成与核验" : "按详细设置生成";
    $("quick-generate").disabled = running;
    $("quick-generate").firstElementChild.textContent = running ? "正在生成与核验" : "帮我准备图文";
    $("generate-variants").disabled = running || !["ok","success"].includes(state.result?.status);
    $("edit-button").disabled = running || !state.result?.run_id || !["ok","success"].includes(state.result?.status);
    document.querySelectorAll("[data-quick-preset], [data-channel]").forEach(b => {b.disabled=running;});
    document.querySelectorAll(".preset-button").forEach((button) => { button.disabled = running; });
    if (running) {
      $("run-progress").hidden = false;
      $("progress-spinner").className = "progress-spinner";
      $("progress-heading").textContent = "正在处理营销任务";
      $("progress-description").textContent = "正在匹配资料、组织图文与行动方案。";
      $("elapsed").textContent = "0 秒";
      const started = Date.now();
      clearInterval(state.elapsedTimer);
      state.elapsedTimer = setInterval(() => { $("elapsed").textContent = `${Math.floor((Date.now() - started) / 1000)} 秒`; }, 1000);
    } else clearInterval(state.elapsedTimer);
  }

  async function generate(event) {
    event.preventDefault();
    if (state.running) return;
    return performGeneration(readInput());
  }

  async function performGeneration(input) {
    state.input = { ...input };
    $("notice").hidden = true;
    $("result").hidden = true;
    setRunning(true);
    try {
      const result = await request("/api/generate", { method: "POST", body: JSON.stringify(input) }, 300000);
      $("progress-spinner").className = "progress-spinner finished";
      $("progress-heading").textContent = ["ok", "success"].includes(result.status) ? "本次任务已返回结果" : "本次任务已完成处理";
      $("progress-description").textContent = "图文内容与行动方案已准备完成。";
      $("run-progress").hidden = true;
      if (Number.isFinite(Number(result.elapsed_ms))) $("elapsed").textContent = `${(Number(result.elapsed_ms) / 1000).toFixed(1)} 秒`;
      if (result.status === "needs_clarification") showNotice("还需要确认几件事", result.message || "请将以下信息补充到任务中，再开始生成。", result.questions);
      else if (result.status === "blocked") showNotice("本次任务未形成可发布成稿", result.message || "请根据核验建议调整任务。", result.questions, true);
      else if (result.status === "error") showNotice("本次任务未成功完成", result.message || "请稍后重试。", result.questions, true);
      renderResult(result, input);
      $("result").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
      $("run-progress").hidden = true;
      showNotice("这次请求没有完成", error.message, [], true);
    } finally { setRunning(false); }
  }

  function renderStrategy(strategy, input) {
    const target = $("strategy-content");
    const grid = element("dl", "strategy-grid");
    const fields = [["目标人群", strategy.audience || input.audience], ["使用场景", strategy.scene || input.scene], ["发布平台", strategy.platform || input.platform], ["内容目标", strategy.goal || input.goal], ["切入角度", strategy.angle, true], ["内容结构", strategy.structure, true], ["表达语气", strategy.tone], ["行动引导", strategy.cta]];
    fields.forEach(([label, value, wide]) => {
      if (!value) return;
      const pair = element("div", wide ? "wide" : "");
      pair.append(element("dt", "", label), element("dd", "", value));
      grid.append(pair);
    });
    if (grid.children.length) target.replaceChildren(grid);
    else empty(target, "本次响应没有提供场景策略。");
  }

  function readable(value) {
    if (!value || typeof value !== "object") return inlineText(value);
    if (Array.isArray(value)) return value.map(readable).filter(Boolean).join("；");
    return Object.values(value).map(readable).filter(Boolean).join(" · ");
  }

  function growthBlock(title, subtitle) {
    const block = element("section", "growth-block");
    const heading = element("div", "growth-block-heading");
    heading.append(element("h4", "", title));
    if (subtitle) heading.append(element("p", "", subtitle));
    block.append(heading);
    return block;
  }

  function renderGrowth(plan) {
    const target = $("growth-content");
    target.replaceChildren();
    const available = plan && typeof plan === "object" && Object.keys(plan).length;
    $("growth-panel").hidden = !available;
    $("growth-nav").hidden = !available;
    if (!available) return;
    const insights = plan.insights || plan.customer_insight || [];
    if (Array.isArray(insights) && insights.length) {
      const block = growthBlock("客户洞察", "从已提供的信息出发，找出这次沟通的重点。");
      const grid = element("div", "insight-grid");
      insights.forEach((insight) => {
        const card = element("article", "insight-card");
        const provided = insight.status !== "needs_confirmation";
        card.append(element("h5", "", (insight.dimension || insight.label || "需求重点") + (provided ? "" : " · 待补充")), element("p", "insight-conclusion", readable(provided ? insight.conclusion || insight.insight : insight.action || insight.conclusion)));
        if (provided && insight.evidence) card.append(element("p", "insight-evidence", `依据：${readable(insight.evidence)}`));
        grid.append(card);
      });
      block.append(grid); target.append(block);
    }
    if (plan.content_strategy) {
      const block = growthBlock("沟通策略");
      const strategy = plan.content_strategy;
      if (typeof strategy === "object" && !Array.isArray(strategy)) {
        const labels = { angle: "切入点", hook: "开场", message: "核心表达", proof: "证明方式", cta: "下一步", tone: "沟通语气", opening: "开场", core_message: "核心表达", platform: "平台", structure: "内容结构", pain_point: "客户关切", value: "产品价值" };
        const grid = element("dl", "strategy-grid growth-strategy");
        Object.entries(strategy).forEach(([key, value]) => {
          if (!value) return;
          const pair = element("div"); pair.append(element("dt", "", labels[key] || "表达建议"), element("dd", "", readable(value))); grid.append(pair);
        });
        block.append(grid);
      } else block.append(element("p", "growth-prose", readable(strategy)));
      target.append(block);
    }
    if (plan.quote) {
      const quote = plan.quote;
      const block = growthBlock("四档报价", "按录入价格与权益试算，同一配置逐档比较。");
      if (quote.sku || quote.source_url) {
        const source = element("p", "quote-source");
        if (quote.sku) source.append(document.createTextNode(inlineText(quote.sku) + " · "));
        if (quote.source_url) source.append(link("价格来源 ↗", quote.source_url));
        block.append(source);
      }
      if (Array.isArray(quote.tiers) && quote.tiers.length) {
        const grid = element("div", "quote-grid");
        quote.tiers.forEach((tier, index) => {
          const card = element("article", "quote-card");
          card.append(element("span", "quote-number", `0${index + 1}`), element("h5", "", tier.name || `方案 ${index + 1}`));
          const hasPrice = tier.price !== null && tier.price !== undefined && tier.price !== "" && Number.isFinite(Number(tier.price));
          card.append(element("p", `quote-price${hasPrice ? "" : " quote-price-pending"}`, hasPrice ? `¥ ${Number(tier.price).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}` : "待补充价格"));
          if (hasPrice) card.append(element("span", "quote-calculated-label", "按填报条件试算"));
          if (tier.expression) card.append(element("p", "quote-expression", inlineText(tier.expression)));
          if (tier.benefits && readable(tier.benefits)) card.append(element("p", "quote-benefits", readable(tier.benefits)));
          if (tier.conditions && readable(tier.conditions)) card.append(element("p", "quote-conditions", readable(tier.conditions)));
          grid.append(card);
        });
        block.append(grid);
      }
      if (quote.best_option && quote.best_option.price !== null) {
        const best = element("p", "quote-source");
        best.append(element("strong", "", `当前可计算方案最低：${quote.best_option.name} · ¥ ${Number(quote.best_option.price).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`), document.createTextNode("；" + textOf(quote.best_option.scope)));
        block.append(best);
      }
      if (quote.missing && readable(quote.missing)) {
        const pending = element("div", "quote-missing");
        pending.append(element("strong", "", "报价前需确认"), element("p", "", readable(quote.missing))); block.append(pending);
      }
      target.append(block);
    }
    const demo = plan.demo_script || plan.demo_180s || [];
    if (Array.isArray(demo) && demo.length) {
      const block = growthBlock("180 秒产品演示", "让客户带着自己的任务，现场看见、亲手验证。");
      const wrapper = element("div", "table-wrap demo-table");
      wrapper.append(makeTable(["时间", "现场动作", "导购话术", "体验确认"], demo.map((step) => ({ cells: [step.time || `${step.seconds || ""} 秒`, readable(step.action), readable(step.words || step.script), readable(step.check)] }))));
      block.append(wrapper); target.append(block);
    }
    if (Array.isArray(plan.followups) && plan.followups.length) {
      const block = growthBlock("后续跟进", "按客户进度，安排下一次有用的沟通。");
      const list = element("div", "followup-list");
      plan.followups.forEach((step) => {
        const row = element("article", "followup-row");
        const label = /^\d+$/.test(textOf(step.day)) ? `D+${step.day}` : step.day;
        const body = element("div");
        body.append(element("h5", "", readable(step.action)), element("p", "", readable(step.message)));
        row.append(element("span", "followup-day", label || "跟进"), body); list.append(row);
      });
      block.append(list); target.append(block);
    }
  }

  function imageAllowed(image) {
    return Boolean(safeUrl(image.url)) && !/rejected|blocked|mismatch|unverified|unsafe|failed|不匹配|禁止|未核验|驳回/i.test(textOf(image.status));
  }

  function createFigure(image) {
    const figure = element("figure", "article-figure");
    const photo = element("img");
    photo.src = safeUrl(image.url);
    photo.alt = textOf(image.title || image.caption || "产品素材图");
    photo.loading = "lazy";
    photo.decoding = "async";
    photo.referrerPolicy = "no-referrer";
    photo.addEventListener("error", () => {
      photo.replaceWith(element("div", "image-unavailable", "此图片暂时无法加载。请通过下方来源链接查看原素材。"));
    }, { once: true });
    const caption = element("figcaption");
    const captionParts = [image.caption || image.title, image.model ? `适用型号：${textOf(image.model)}` : "", image.source_title ? `来源：${textOf(image.source_title)}` : ""].filter(Boolean);
    caption.append(document.createTextNode(captionParts.join(" · ")));
    if (safeUrl(image.source_url)) caption.append(link("查看来源 ↗", image.source_url, "image-source-link"));
    figure.append(photo, caption);
    return figure;
  }

  function imageSection(position) {
    const match = textOf(position).match(/section[_\s-]*(\d+)|第?([一二三四五六七八九十\d]+)[段节]|段落\s*(\d+)/i);
    if (!match) return null;
    const value = match[1] || match[2] || match[3];
    const chinese = { 一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10 };
    return chinese[value] || Number(value) || null;
  }

  function renderArticle(article, images, input) {
    const target = $("article-content");
    target.replaceChildren();
    $("article-platform").textContent = input.platform || "";
    if (!article || (!article.title && !article.lead && !article.sections?.length && !article.text)) {
      empty(target, "本次运行没有返回成稿，请查看资料依据与核验结果。");
      return;
    }
    if (article.title) target.append(element("h3", "article-title", article.title));
    if (article.lead) target.append(element("p", "article-lead", article.lead));
    const selectedImages = images.filter(imageAllowed);
    const used = new Set();
    selectedImages.forEach((image, index) => {
      if (/cover|hero|first|封面|首图|开头|文首|正文前|标题.{0,3}[下后]|导语.{0,3}[下后]/i.test(textOf(image.position))) {
        target.append(createFigure(image));
        used.add(index);
      }
    });
    const sections = Array.isArray(article.sections) ? article.sections : article.text ? [{ text: article.text }] : [];
    sections.forEach((section, index) => {
      const group = element("section", "article-section");
      if (typeof section === "string") group.append(element("p", "article-paragraph", section));
      else {
        if (section.heading) group.append(element("h4", "", section.heading));
        if (section.text) group.append(element("p", "article-paragraph", section.text));
        if (Array.isArray(section.fact_ids) && section.fact_ids.length) {
          const references = element("div", "fact-references");
          references.append(element("span", "", "依据"));
          section.fact_ids.forEach((id) => {
            const reference = element("a", "", id);
            reference.href = `#fact-${encodeURIComponent(textOf(id))}`;
            references.append(reference);
          });
          group.append(references);
        }
      }
      target.append(group);
      selectedImages.forEach((image, imageIndex) => {
        if (used.has(imageIndex)) return;
        const position = textOf(image.position);
        const sectionNumber = imageSection(position);
        const headingMatch = typeof section === "object" && section.heading && textOf(section.heading).length > 3 && position.includes(textOf(section.heading));
        if (sectionNumber === index + 1 || headingMatch) {
          target.append(createFigure(image));
          used.add(imageIndex);
        }
      });
    });
    selectedImages.forEach((image, index) => { if (!used.has(index)) target.append(createFigure(image)); });
    if (article.cta) target.append(element("p", "article-cta", article.cta));
    if (Array.isArray(article.tags) && article.tags.length) {
      const tags = element("div", "article-tags");
      article.tags.forEach((tag) => tags.append(element("span", "", textOf(tag).startsWith("#") ? tag : `#${textOf(tag)}`)));
      target.append(tags);
    }
    if (!selectedImages.length) target.append(element("p", "image-brief", "本次结果未返回可用于成稿的已核验图片。请查看下方图片来源。"));
  }

  function makeTable(headers, rows) {
    const table = element("table");
    const head = element("thead");
    const header = element("tr");
    headers.forEach((label) => { const th = element("th", "", label); th.scope = "col"; header.append(th); });
    head.append(header);
    const body = element("tbody");
    rows.forEach((row) => {
      const tr = element("tr");
      if (row.id) tr.id = row.id;
      row.cells.forEach((cell) => { const td = element("td"); td.append(cell instanceof Node ? cell : document.createTextNode(textOf(cell))); tr.append(td); });
      body.append(tr);
    });
    table.append(head, body);
    return table;
  }

  function inlineText(value) {
    return textOf(value).replace(/\\[nr]/g, " ").replace(/\s+/g, " ").replace(/(^|\s)#{1,6}\s*/g, "$1").trim();
  }

  function renderFacts(facts) {
    const target = $("facts-content");
    if (!facts.length) return empty(target, "本次响应没有返回可引用的产品事实。");
    const rows = facts.map((fact, index) => {
      const id = fact.id || `F${index + 1}`;
      const source = element("div", "fact-source");
      source.append(link("查看来源 ↗", fact.source_url));
      const locator = inlineText(fact.locator);
      if (locator && locator !== fact.source_title && locator !== "产品事实.md") source.append(element("span", "source-secondary", locator));
      if (fact.quote) {
        const excerpt = element("details", "fact-excerpt");
        excerpt.append(element("summary", "", "原文摘录"), element("p", "", inlineText(fact.quote)));
        source.append(excerpt);
      }
      return { id: `fact-${id}`, cells: [element("span", "fact-id", id), inlineText(fact.text || fact.claim), source] };
    });
    target.replaceChildren(makeTable(["依据", "采用的产品事实", "原文与来源"], rows));
  }

  function renderImages(images) {
    const target = $("images-content");
    if (!images.length) return empty(target, "本次响应没有返回图片素材。");
    const rows = images.map((image, index) => {
      const source = element("div");
      source.append(link(image.source_title || image.title || "来源未标注", image.source_url || image.url));
      if (image.model) source.append(element("p", "source-secondary", `适用型号：${textOf(image.model)}`));
      const explanation = element("div");
      explanation.append(element("p", "", image.caption || image.title || "未提供说明"));
      if (image.status) explanation.append(element("p", "source-secondary", `素材状态：${statusInfo(image.status).label}`));
      return { cells: [image.id || `I${index + 1}`, source, image.position || "未指定", explanation] };
    });
    target.replaceChildren(makeTable(["编号", "图片来源", "放置位置", "说明"], rows));
  }

  function statusInfo(status) {
    const raw = textOf(status).toLowerCase();
    if (/^(pass|passed|ok|success|succeeded|verified|approved|matched|selected|complete|completed|done|通过|已通过|已核验|完成)$/.test(raw)) return { style: "pass", label: "通过" };
    if (/^(fail|failed|error|blocked|rejected|mismatch|unsafe|不通过|失败|拦截|已拦截|不匹配)$/.test(raw)) return { style: "fail", label: "需处理" };
    if (/^(warn|warning|review|needs_review|needs_confirmation|uncertain|unverified|pending|partial|missing|待核验|待确认|需确认|需复核|待复核|待补充|需人工复核|警告|未知)$/.test(raw)) return { style: "warn", label: "待确认" };
    if (/^(skip|skipped|not_applicable|不适用|跳过)$/.test(raw)) return { style: "neutral", label: "未执行" };
    return { style: "neutral", label: textOf(status) || "未标注" };
  }

  function checkType(type) {
    const names = { factual: "事实核验", fact: "事实核验", facts: "事实核验", model: "型号归属", model_match: "型号归属", image: "图片匹配", images: "图片匹配", compliance: "营销合规", platform: "平台适配", freshness: "时效信息", source: "资料来源", price: "价格与优惠", absolute_claim: "绝对化措辞", specification: "产品参数", relevance: "场景相关性" };
    return names[type] || textOf(type) || "核验项";
  }

  function renderChecks(checks, target) {
    if (!checks.length) return empty(target, "本次响应未提供逐项核验结果。");
    const tally = { pass: 0, warn: 0, fail: 0, neutral: 0 };
    checks.forEach((check) => { tally[statusInfo(check.status).style] += 1; });
    const summary = element("div", "check-summary");
    [["项通过", tally.pass], ["项待确认", tally.warn], ["项需处理", tally.fail]].forEach(([label, count]) => {
      if (!count) return;
      const item = element("span"); item.append(element("strong", "", count), document.createTextNode(label)); summary.append(item);
    });
    const needsAttention = element("div", "check-list");
    const passedList = element("div", "check-list");
    checks.forEach((check) => {
      const status = statusInfo(check.status);
      const row = element("div", "check-row");
      const detail = element("div");
      detail.append(element("p", "check-claim", inlineText(check.claim || check.message || "核验完成")));
      if (check.evidence) detail.append(element("p", "check-evidence", `依据：${inlineText(check.evidence)}`));
      if (status.style !== "pass" && check.action && !/^(无需|无须|无需修改|无需处理|无|none|no action)/i.test(inlineText(check.action))) detail.append(element("p", "check-action", `建议：${inlineText(check.action)}`));
      row.append(element("div", "check-type", checkType(check.type)), element("span", `check-badge ${status.style}`, status.label), detail);
      (status.style === "pass" ? passedList : needsAttention).append(row);
    });
    target.replaceChildren(summary);
    if (needsAttention.children.length) target.append(needsAttention);
    if (passedList.children.length) {
      const passed = element("details", "passed-checks");
      passed.append(element("summary", "", `查看 ${tally.pass} 项已通过核验`), passedList);
      target.append(passed);
    }
  }


  function formatDate(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return textOf(value);
    return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date);
  }

  function renderResult(result, input = {}, reviewNote = "") {
    state.result = result;
    state.input = input;
    $("editor-panel").hidden = true;
    renderPersonalization(result,input);
    $("result").hidden = false;
    $("result-heading").textContent = result.status === "blocked" ? "本次内容需要修订" : result.status === "needs_clarification" ? "补充信息后继续任务" : result.status === "error" ? "本次运行未成功完成" : "本店的这一篇，准备好了";
    $("article-heading").textContent = result.status === "blocked" ? "待修订内容" : result.mode === "verified_edit" ? "图文成稿 · 编辑后已核验" : result.publication_revision ? "图文成稿 · 发布整理版" : "图文成稿";
    $("result-meta").textContent = [input.scene, input.platform, result.elapsed_ms ? `${result.mode === "verified_edit" ? "编辑核验 " : "生成 "}${(result.elapsed_ms/1000).toFixed(1)} 秒` : "", formatDate(result.generated_at)].filter(Boolean).join(" · ");
    const banner = $("result-status");
    const fallbackMessages = { blocked: "本次内容未通过核验，请根据逐项核验结果修订后再使用。", needs_clarification: "当前信息不足，请补充任务要求后重新生成。", error: "本次任务未成功完成，请稍后重试。" };
    if (fallbackMessages[result.status]) {
      banner.textContent = result.message || fallbackMessages[result.status];
      banner.className = `result-status ${["ok", "success"].includes(result.status) ? "success" : ["error", "blocked"].includes(result.status) ? "error" : ""}`;
      banner.hidden = false;
    } else banner.hidden = true;
    const facts = Array.isArray(result.facts) ? result.facts : [];
    const images = Array.isArray(result.images) ? result.images : [];
    const checks = Array.isArray(result.checks) ? result.checks : [];
    $("review-context").textContent = result.run_id ? "将对照本次方案采用的产品资料。" : "生成后可自动对照本次产品资料。";
    renderStrategy(result.strategy || {}, input);
    renderGrowth(result.growth_plan);
    renderArticle(result.article, images, input);
    renderFacts(facts);
    renderImages(images);
    renderChecks(checks, $("checks-content"));
    $("copy-button").disabled = !result.article || ["blocked", "error", "needs_clarification"].includes(result.status);
    $("copy-button").textContent = result.status === "blocked" ? "成稿未通过核验" : "复制成稿";
    $("export-button").textContent = ["blocked", "error", "needs_clarification"].includes(result.status) ? "导出核验记录 ↓" : "下载图文包 ↓";
    $("edit-button").disabled = !result.run_id || ["blocked","error","needs_clarification"].includes(result.status);
    $("generate-variants").disabled = state.running || !["ok","success"].includes(result.status);
  }

  function articleText(result, includeImageMarkdown = false) {
    const article = result?.article;
    if (!article) return "";
    const pieces = [];
    if (article.title) pieces.push(includeImageMarkdown ? `# ${textOf(article.title)}` : textOf(article.title));
    if (article.lead) pieces.push(textOf(article.lead));
    (Array.isArray(article.sections) ? article.sections : []).forEach((section) => {
      if (typeof section === "string") pieces.push(section);
      else {
        if (section.heading) pieces.push(includeImageMarkdown ? `## ${textOf(section.heading)}` : textOf(section.heading));
        if (section.text) pieces.push(textOf(section.text));
      }
    });
    if (article.text) pieces.push(textOf(article.text));
    if (article.cta) pieces.push(textOf(article.cta));
    if (article.tags?.length) pieces.push(article.tags.map((tag) => textOf(tag).startsWith("#") ? textOf(tag) : `#${textOf(tag)}`).join(" "));
    if (includeImageMarkdown) (result.images || []).filter(imageAllowed).forEach((image) => { pieces.push(`![${textOf(image.caption || image.title).replace(/[\[\]]/g, "")}](${safeUrl(image.url)})\n\n图片来源：${textOf(image.source_title)}${safeUrl(image.source_url) ? ` ${safeUrl(image.source_url)}` : ""}`); });
    return pieces.join("\n\n");
  }

  async function copyArticle() {
    if (["blocked", "error", "needs_clarification"].includes(state.result?.status)) return toast("当前结果尚未形成可交付成稿，请先处理核验或补充信息。");
    const content = articleText(state.result);
    if (!content) return toast("本次结果没有可复制的成稿。");
    try {
      if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(content);
      else {
        const area = element("textarea");
        area.value = content;
        area.style.position = "fixed";
        area.style.left = "-9999px";
        document.body.append(area);
        area.select();
        const copied = document.execCommand("copy");
        area.remove();
        if (!copied) throw new Error("copy failed");
      }
      toast("成稿已复制；资料与核验记录可通过导出保存。");
    } catch (_) { toast("浏览器未允许复制，请使用导出按钮保存内容。"); }
  }

  function exportResult() {
    const result = state.result;
    if (!result) return;
    const reportOnly = ["blocked", "error", "needs_clarification"].includes(result.status);
    const lines = reportOnly ? ["# 知华·有据 · 任务核验记录", `处理状态：${textOf(result.status)}\n${textOf(result.message)}`, "此记录不包含未通过核验的成稿正文。"] : [result.markdown || articleText(result, true)];
    if (!result.markdown || reportOnly) {
      lines.push("---", "## 场景策略", textOf(result.strategy), "## 资料依据");
      (result.facts || []).forEach((fact) => lines.push(`- [${textOf(fact.id)}] ${textOf(fact.text)}\n  来源：${textOf(fact.source_title)}；${textOf(fact.locator)}${safeUrl(fact.source_url) ? `；${safeUrl(fact.source_url)}` : ""}`));
      lines.push("## 逐项核验");
      (result.checks || []).forEach((check) => lines.push(`- ${checkType(check.type)} / ${statusInfo(check.status).label}：${textOf(check.claim)}\n  依据：${textOf(check.evidence)}\n  处理建议：${textOf(check.action)}`));
      lines.push("## 运行信息", `运行编号：${textOf(result.run_id)}\n生成时间：${textOf(result.generated_at)}\n运行模式：${modeLabel(result.mode)}`, "## 实际运行记录");
      (result.trace || []).forEach((trace) => lines.push(`- ${textOf(trace.step)} / ${textOf(trace.status)}：${textOf(trace.detail)}`));
    }
    const blob = new Blob([lines.join("\n\n")], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const download = element("a");
    download.href = url;
    download.download = `知华有据_${reportOnly ? "核验记录_" : ""}${textOf(result.run_id || "图文内容").replace(/[^\w\u4e00-\u9fff-]/g, "_")}.md`;
    document.body.append(download);
    download.click();
    download.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  }

  async function checkCopy(event) {
    event.preventDefault();
    const button = $("review-button");
    if (button.disabled) return;
    const text = $("review-text").value.trim();
    if (!text) return;
    button.disabled = true;
    button.firstChild.textContent = "正在核验 ";
    const target = $("review-result");
    const runId = state.result?.run_id || "";
    target.replaceChildren(element("p", "review-message", runId ? "正在对照当前运行的资料依据并核验文案，请稍候。" : "正在检查营销措辞。当前未绑定运行记录，产品事实仍需资料确认。"));
    try {
      const response = await request("/api/check", { method: "POST", body: JSON.stringify({ text, product: $("product").value, run_id: runId }) }, 180000);
      const content = element("div");
      const checks = Array.isArray(response.checks) ? response.checks : [];
      if (!checks.length && ["ok", "success"].includes(response.status)) empty(content, "本次规则核验未发现需要处理的项目。检查范围与资料对照情况以上方返回说明为准。");
      else renderChecks(checks, content);
      target.replaceChildren();
      if (response.status === "blocked") target.append(element("p", "review-warning", "这段文案存在需要处理的核验项，请修改后再使用。"));
      if (response.message) target.append(element("p", "review-message", response.message));
      target.append(content);
    } catch (error) { target.replaceChildren(element("p", "review-message", `本次核验未完成：${error.message}`)); }
    finally { button.disabled = false; button.firstChild.textContent = "核验这段文案 "; }
  }

  async function loadExamples() {
    const target = $("examples-content");
    try {
      const response = await request("/api/examples");
      state.examples = Array.isArray(response.examples) ? response.examples : [];
      if (!state.examples.length) return empty(target, "目前还没有已保存的运行案例。可以先生成一份营销方案。");
      target.replaceChildren();
      state.examples.forEach((example, index) => {
        const row = element("article", "example-row");
        const body = element("div", "example-body");
        const input = example.publication_input || example.input || {};
        const result = example.publication_result || example.result;
        body.append(element("h2", "", example.label || result?.article?.title || `运行案例 ${index + 1}`));
        body.append(element("p", "example-description", [input.audience, input.scene].filter(Boolean).join(" · ")));
        const details = [input.platform, result?.images?.length ? `${result.images.length} 张配图` : ""].filter(Boolean);
        body.append(element("p", "example-detail", details.join(" / ")));
        const actions = element("div", "example-actions");
        const view = element("button", "secondary-button", example.publication_result ? "查看整理成稿 ↗" : "查看实际结果 ↗");
        view.type = "button";
        view.disabled = !result;
        view.addEventListener("click", () => {
          if (state.running) return toast("当前任务正在运行，请等待结果后再打开案例。");
          fillInput(input);fillStoreProfile(input.store_profile||{});$("quick-brief").value=input.brief||"";$("quick-product").value=$("product").value;setChannel(input.platform||"门店朋友圈");
          showView("workbench");
          $("notice").hidden = true;
          $("run-progress").hidden = true;
          renderResult(result, input, example.report_note || "");
          requestAnimationFrame(() => $("result").scrollIntoView({ behavior: "smooth", block: "start" }));
        });
        const load = element("button", "text-button", "用这个任务试一遍");
        load.type = "button";
        load.addEventListener("click", () => {
          if (state.running) return toast("当前任务正在运行，请等待结果后再载入新任务。");
          fillInput(input);fillStoreProfile(input.store_profile||{});$("quick-brief").value=input.brief||"";$("quick-product").value=$("product").value;setChannel(input.platform||"门店朋友圈");
          showView("workbench");
          $("task-form").scrollIntoView({ behavior: "smooth", block: "start" });
          toast("已载入案例任务，可以修改后点击生成。");
        });
        actions.append(view, load);
        row.append(element("div", "example-number", String(index + 1).padStart(2, "0")), body, actions);
        target.append(row);
      });
    } catch (error) { empty(target, `暂时无法读取案例：${error.message}`); }
  }

  const storeFields = {store_name:"store-name",store_type:"store-type",local_context:"store-context",voice:"store-voice",customer_question:"store-question",experience_notes:"store-notes"};
  const storeScenarios = {
    campus:{store_name:"校园门店",store_type:"校园店",local_context:"大学周边，课堂笔记、图书馆学习、小组汇报",voice:"轻松随和",customer_question:"上课、图书馆来回背，选电脑先看什么？",experience_notes:""},
    business:{store_name:"商务区门店",store_type:"商务区店",local_context:"写字楼周边，通勤、会议、移动办公",voice:"干练直接",customer_question:"白天开会和下班路上，怎么把工作接起来？",experience_notes:""},
    community:{store_name:"社区门店",store_type:"社区店",local_context:"住宅社区周边，家庭换机、设备共享、资料整理",voice:"耐心讲解",customer_question:"家里旧电脑想换掉，先把哪些问题问清楚？",experience_notes:""}
  };
  const quickPresets = {
    campus:{product:"matebook14-ultra",platform:"小红书",profile:"campus",brief:"给准备选电脑的大学生写一篇 MateBook 14 酷睿 Ultra 小红书。从这个选购问题写起：上课、图书馆来回背，选电脑先看什么？从小组汇报的一天切入，语气轻松自然。"},
    business:{product:"matebook14-ultra",platform:"公众号",profile:"business",brief:"给通勤的上班族写一篇 MateBook 14 酷睿 Ultra 公众号文章。顾客关心会议、来回通勤和日常文档。从出门到回办公室的具体任务写起，表达干练。"},
    community:{product:"matebook-d16",platform:"门店朋友圈",profile:"community",brief:"给想换旧电脑的家庭用户写一条 MateBook D 16 2024 朋友圈。从这个换机问题写起：旧文件和常用应用怎么办？从换机前需要理清的三件事切入，邀请带上常用任务到店试试。"},
    ecosystem:{product:"matebook-pro",platform:"门店朋友圈",profile:"business",brief:"为 MateBook Pro 写一条面向职场人士的生态体验朋友圈：在手机备忘录里记下想法，回到电脑继续整理。根据官方支持资料说明接续所需的系统、账号和应用条件，邀请带上自己的设备到店体验。"}
  };

  function readStoreProfile(){
    const result={};
    Object.entries(storeFields).forEach(([key,id])=>{const value=$(id).value.trim();if(value)result[key]=value;});
    return result;
  }
  function fillStoreProfile(profile={}){
    Object.entries(storeFields).forEach(([key,id])=>{const node=$(id),value=profile[key]||({store_type:"综合门店",voice:"自然亲切"}[key]||"");node.value=value;});
    updateStoreSummary();
  }
  function updateStoreSummary(){const p=readStoreProfile();$("store-summary-label").textContent=p.store_name||p.store_type||"我的门店";}
  function setChannel(value,touched=true){
    state.quickChannel=value;if(touched)state.channelTouched=true;
    document.querySelectorAll("[data-channel]").forEach(b=>{const selected=b.dataset.channel===value;b.classList.toggle("selected",selected);b.setAttribute("aria-pressed",String(selected));});
    if([...$("platform").options].some(o=>o.value===value))$("platform").value=value;
  }
  function chooseQuickPreset(key){
    const item=quickPresets[key];if(!item)return;
    $("quick-brief").value=item.brief;
    if([...$("quick-product").options].some(o=>o.value===item.product))$("quick-product").value=item.product;
    else $("quick-product").value="auto";
    $("product").value=$("quick-product").value;
    setChannel(item.platform);fillStoreProfile(storeScenarios[item.profile]);
    state.pendingIntake=null;$("intake-panel").hidden=true;
    document.querySelectorAll("[data-quick-preset]").forEach(b=>b.classList.toggle("selected",b.dataset.quickPreset===key));
    $("quick-brief").focus();
  }
  function renderIntake(response){
    state.pendingIntake=response.payload;
    $("intake-panel").hidden=false;
    const summary=$("intake-summary");summary.replaceChildren(element("h3","",response.status==="ready"?"这次就这样写":"再点一下，把需求补齐"));
    const line=element("div","intake-summary-line");
    const input=response.payload||{};
    [input.audience,input.scene,input.platform].filter(Boolean).forEach(t=>line.append(element("span","",t)));
    if(line.children.length)summary.append(line);
    const questions=$("intake-questions");questions.replaceChildren();
    const fields=new Set();
    (response.questions||[]).forEach(question=>{
      fields.add(question.field);
      const box=element("div","intake-question"),options=element("div","intake-options");
      box.append(element("h4","",question.label||"请选择"));
      (question.options||[]).forEach(option=>{
        const value=typeof option==="string"?option:option.value,label=typeof option==="string"?option:option.label;
        const b=element("button","",label);b.type="button";
        b.addEventListener("click",()=>{state.pendingIntake[question.field]=value;options.querySelectorAll("button").forEach(n=>n.classList.toggle("selected",n===b));fields.delete(question.field);$("intake-continue").disabled=fields.size>0;});
        options.append(b);
      });box.append(options);questions.append(box);
    });
    $("intake-continue").hidden=response.status==="ready";
    $("intake-continue").disabled=fields.size>0;
  }
  async function startQuick(usePending=false){
    if(state.running||state.intaking)return;
    const brief=$("quick-brief").value.trim();
    if(!brief){$("quick-brief").focus();return toast("先写一句需求，也可以点选示例任务。");}
    state.intaking=true;$("quick-generate").disabled=true;$("quick-generate").firstElementChild.textContent="正在理解需求";$("notice").hidden=true;
    try{
      const payload=usePending&&state.pendingIntake?{...state.pendingIntake,store_profile:readStoreProfile()}:{brief,product:$("quick-product").value||"auto",platform:state.channelTouched?state.quickChannel:"",store_profile:readStoreProfile()};
      const response=await request("/api/intake",{method:"POST",body:JSON.stringify(payload)},45000);
      renderIntake(response);
      if(response.status==="ready"){
        fillInput(response.payload);fillStoreProfile(response.payload.store_profile||readStoreProfile());setChannel(response.payload.platform,false);
        $("quick-product").value=$("product").value;
        state.intaking=false;
        await performGeneration({...response.payload,customer_context:readInput().customer_context});
      }else $("intake-panel").scrollIntoView({behavior:"smooth",block:"nearest"});
    }catch(error){showNotice("暂时未能准备任务",error.message,[],true);}
    finally{state.intaking=false;$("quick-generate").disabled=state.running;$("quick-generate").firstElementChild.textContent=state.running?"正在生成与核验":"帮我准备图文";}
  }
  function renderPersonalization(result,input){
    const box=$("personalization-summary"),p=result.personalization||{},profile=p.store_profile||input.store_profile||{};
    box.replaceChildren();
    if(!profile.store_name&&!profile.store_type&&!p.creative_brief){box.hidden=true;return;}
    box.hidden=false;
    box.append(element("strong","",[profile.store_name||profile.store_type,profile.voice,input.platform].filter(Boolean).join(" · ")));
    const material=[profile.customer_question?`从顾客的问题写起：${profile.customer_question}`:"",profile.local_context?`场景：${profile.local_context}`:""].filter(Boolean).join("　");
    if(material)box.append(element("p","",material));
    const novelty=result.novelty;
    if(novelty&&novelty.message)box.append(element("p","",novelty.message));
    if(p.variant_id)box.append(element("p","variant-meta",`本店版本 ${p.variant_id}`));
  }
  function openEditor(){
    if(!state.result?.article)return;
    $("editor-panel").hidden=false;
    $("edit-title").value=state.result.article.title||"";
    const body={...state.result,article:{...state.result.article,title:""}};
    $("edit-text").value=articleText(body);
    $("edit-checks").replaceChildren();
    $("editor-panel").scrollIntoView({behavior:"smooth",block:"start"});
  }
  async function applyEdit(){
    if(!state.result?.run_id)return toast("请先生成一份当前运行的成稿。");
    const title=$("edit-title").value.trim(),text=$("edit-text").value.trim();
    if(!title||!text)return toast("请保留标题和正文。");
    const button=$("apply-edit");button.disabled=true;button.textContent="正在对照资料核验";
    const target=$("edit-checks");target.replaceChildren(element("p","review-message","正在检查修改后的产品事实与营销表述。"));
    try{
      const result=await request("/api/revise",{method:"POST",body:JSON.stringify({run_id:state.result.run_id,title,text})},180000);
      if(["ok","success"].includes(result.status)&&result.article&&result.run_id){
        renderResult(result,state.input);$("editor-panel").hidden=true;toast("修改已通过本次资料核验，复制与下载均使用新版本。");
        $("article-panel").scrollIntoView({behavior:"smooth",block:"start"});
      }else{
        target.replaceChildren(element("p","review-warning",result.message||"这次修改仍有需要处理的内容，原稿已保留。"));
        const details=element("div");renderChecks(result.checks||[],details);target.append(details);
      }
    }catch(error){target.replaceChildren(element("p","review-warning",error.message));}
    finally{button.disabled=false;button.textContent="核验并采用";}
  }
  function renderVariantCard(result,input,index){
    const card=element("article","variant-card"),p=input.store_profile||{};
    card.append(element("span","variant-store",`${String(index+1).padStart(2,"0")}　${p.store_name||p.store_type}`));
    if(!["ok","success"].includes(result.status)){
      card.append(element("h4","","这个版本需要调整"),element("p","variant-excerpt",result.message||"未形成完整成稿"));return card;
    }
    card.append(element("h4","",result.article?.title||"本店内容"));
    const body=articleText({...result,article:{...result.article,title:""}});
    card.append(element("p","variant-excerpt",body));
    card.append(element("p","variant-angle",`顾客问题：${p.customer_question||""}`));
    const view=element("button","","查看完整图文 ↗");view.type="button";
    view.addEventListener("click",()=>{renderResult(result,input);fillInput(input);fillStoreProfile(p);$("result").scrollIntoView({behavior:"smooth",block:"start"});});
    card.append(view);
    if(result.elapsed_ms)card.append(element("p","variant-time",`${result.mode === "verified_edit" ? "编辑核验" : "本次生成"} ${(result.elapsed_ms/1000).toFixed(1)} 秒`));
    return card;
  }
  async function generateVariants(){
    if(state.running||state.batching)return;
    const base=state.input||readInput();
    if(!base.product||base.product==="auto")return toast("先确定产品并生成一篇图文，再比较门店版本。");
    state.batching=true;state.variants=[];setRunning(true);$("variants-content").replaceChildren();$("variants-status").textContent="正在为三家门店分别组织内容…";
    const definitions=[{key:"campus",audience:"大学生",scene:"开学季",goal:"帮助学生理清选购要点，引导带着课堂任务到店体验"},{key:"business",audience:"职场人士",scene:"职场办公",goal:"围绕通勤与会议中的实际任务，引导到店体验"},{key:"community",audience:"家庭用户",scene:"家庭换新",goal:"围绕家庭换机的问题，邀请带着常用任务到店试用"}];
    let successes=0;
    try{
      for(let i=0;i<definitions.length;i++){
        const item=definitions[i],profile={...storeScenarios[item.key]};
        $("variants-status").textContent=`${i+1} / 3　正在生成${profile.store_name}版本，产品与发布平台保持一致。`;
        const input={product:base.product,audience:item.audience,scene:item.scene,platform:base.platform||"门店朋友圈",goal:item.goal,brief:`围绕同一产品，为${profile.store_type}写一篇自然的${base.platform||"门店朋友圈"}内容。从顾客的问题“${profile.customer_question}”切入，使用${profile.voice}的语气，以适合本地客群的体验行动结尾。`,store_profile:profile,content_angle:profile.customer_question,content_nonce:`${Date.now()}-${i}`};
        try{
          const result=await request("/api/generate",{method:"POST",body:JSON.stringify(input)},300000);
          state.variants=state.variants||[];state.variants.push({input,result});
          if(["ok","success"].includes(result.status))successes++;
          $("variants-content").append(renderVariantCard(result,input,i));
        }catch(error){$("variants-content").append(renderVariantCard({status:"error",message:error.message},input,i));}
      }
      $("variants-status").textContent=`已形成 ${successes} / 3 份门店图文。比较开头、顾客问题、场景和行动引导。`;
    }finally{state.batching=false;setRunning(false);$("run-progress").hidden=true;}
  }
  async function downloadPackage(){
    const r=state.result;if(!r)return;
    if(!r.run_id||!["ok","success"].includes(r.status))return exportResult();
    const button=$("export-button");button.disabled=true;
    try{
      const response=await fetch(`/api/runs/${encodeURIComponent(r.run_id)}/export?format=zip`);
      if(!response.ok){let message="图文包暂时未能生成。";try{const data=await response.json();message=data.detail||data.message||message;}catch(_){}throw new Error(message);}
      const url=URL.createObjectURL(await response.blob()),a=element("a");a.href=url;a.download=`知华有据_${r.run_id}_图文包.zip`;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);toast("图文包已准备，包含正文、图片与来源。");
    }catch(error){toast(error.message+" 可重新生成后再下载。");}
    finally{button.disabled=false;}
  }
  async function loadKnowledge(){
    try{
      const data=await request('/static/knowledge.json');
      $("knowledge-status").textContent=`${data.products.length} 款完整机型 · 资料按型号分开核对 · 生态功能以对应系统与设备条件为准`;
      const list=$("knowledge-products");list.replaceChildren();
      data.products.forEach(p=>{
        const card=element("article","knowledge-product");card.append(element("h3","",p.name),element("p","",`官方资料核对：${formatDate(p.verified_at)}　·　${p.images} 张已匹配图片`),link("查看华为官方规格 ↗",p.url));list.append(card);
      });
      const eco=$("ecosystem-content");eco.replaceChildren();
      (data.ecosystem||[]).forEach(item=>{
        const card=element("article","ecosystem-card");card.append(element("h3","",item.title),element("p","ecosystem-why",item.description),element("p","ecosystem-condition",item.conditions));
        card.append(link("查看华为官方支持说明 ↗",item.url));
        const button=element("button","secondary-button","用这个场景创作 ↗");button.type="button";
        button.addEventListener("click",()=>{
          showView("workbench");fillStoreProfile(storeScenarios.business);setChannel("门店朋友圈");
          const option=[...$("quick-product").options].find(o=>o.textContent.includes(item.product.replace(/^HUAWEI /,'')));
          $("quick-product").value=option?option.value:"auto";
          $("quick-brief").value=`为${item.product}写一条面向职场人士的朋友圈，重点介绍${item.title}的生态体验。请根据华为官方资料保留设备与系统条件，从顾客的具体任务出发，邀请带着自己的手机或平板到店体验。`;
          $("intake-panel").hidden=true;$("task-form").scrollIntoView({behavior:"smooth",block:"start"});
        });card.append(button);eco.append(card);
      });
    }catch(error){$("knowledge-status").textContent=`资料目录暂时不可用：${error.message}`;}
  }

  function initQuickWorkbench(){
    state.quickChannel="小红书";state.channelTouched=false;
    try{const saved=localStorage.getItem('zhihua-store-profile-v2');if(saved)fillStoreProfile(JSON.parse(saved));}catch(_){}
    updateStoreSummary();
    $("open-store").addEventListener("click",()=>{$("store-panel").open=!$("store-panel").open;if($("store-panel").open)$("store-panel").scrollIntoView({behavior:"smooth",block:"nearest"});});
    $("save-store").addEventListener("click",()=>{try{localStorage.setItem('zhihua-store-profile-v2',JSON.stringify(readStoreProfile()));updateStoreSummary();toast("本店设置已保存，下次打开会自动带入。");}catch(_){toast("当前浏览器不允许保存，设置仍可用于本次任务。");}});
    Object.values(storeFields).forEach(id=>$(id).addEventListener('change',updateStoreSummary));
    document.querySelectorAll("[data-channel]").forEach(b=>b.addEventListener("click",()=>setChannel(b.dataset.channel)));
    document.querySelectorAll("[data-quick-preset]").forEach(b=>b.addEventListener("click",()=>chooseQuickPreset(b.dataset.quickPreset)));
    $("quick-generate").addEventListener("click",()=>startQuick(false));$("intake-continue").addEventListener("click",()=>startQuick(true));
    $("quick-brief").addEventListener("input",()=>{state.pendingIntake=null;$("intake-panel").hidden=true;});
    $("quick-brief").addEventListener("keydown",e=>{if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){e.preventDefault();startQuick(false);}});
    $("quick-product").addEventListener("change",()=>{$("product").value=$("quick-product").value;state.pendingIntake=null;});
    $("product").addEventListener("change",()=>{$("quick-product").value=$("product").value;});
    $("edit-button").addEventListener("click",openEditor);$("cancel-edit").addEventListener("click",()=>{$("editor-panel").hidden=true;});$("apply-edit").addEventListener("click",applyEdit);
    $("generate-variants").addEventListener("click",generateVariants);
    loadKnowledge();
  }


  function showView(view, updateHash = true) {
    if (!["workbench", "examples", "guide", "knowledge"].includes(view)) return;
    document.querySelectorAll(".view").forEach((node) => { node.hidden = node.id !== `view-${view}`; });
    document.querySelectorAll("[data-view]").forEach((node) => {
      const active = node.dataset.view === view;
      node.classList.toggle("active", active);
      if (active) node.setAttribute("aria-current", "page");
      else node.removeAttribute("aria-current");
    });
    if (updateHash) history.replaceState(null, "", `#${view}`);
    if (view === "examples") loadExamples();
    if (view === "knowledge") loadKnowledge();
  }

  document.querySelectorAll("[data-view]").forEach((node) => node.addEventListener("click", (event) => { event.preventDefault(); showView(node.dataset.view); window.scrollTo({ top: 0, behavior: "smooth" }); }));
  document.querySelector(".brand").addEventListener("click", (event) => { event.preventDefault(); showView("workbench"); window.scrollTo({ top: 0, behavior: "smooth" }); });
  document.querySelectorAll("[data-preset]").forEach((button) => button.addEventListener("click", () => {
    fillInput(presets[button.dataset.preset]);
    document.querySelectorAll("[data-preset]").forEach((node) => node.classList.toggle("selected", node === button));
    toast("任务已载入，可调整后点击生成。");
  }));
  initBenefits();
  ["audience", "scene"].forEach((key) => $(key).addEventListener("change", () => syncCustom(key)));
  $("task-form").addEventListener("submit", generate);
  $("regenerate-button").addEventListener("click", () => { if(state.input&&!state.running)performGeneration({...state.input,content_nonce:String(Date.now()),store_profile:readStoreProfile()}); });
  $("copy-button").addEventListener("click", copyArticle);
  $("export-button").addEventListener("click", downloadPackage);
  $("refresh-status").addEventListener("click", loadStatus);
  $("review-form").addEventListener("submit", checkCopy);
  window.addEventListener("hashchange", () => showView(location.hash.slice(1), false));
  initQuickWorkbench();
  showView(["examples", "guide", "knowledge"].includes(location.hash.slice(1)) ? location.hash.slice(1) : "workbench", false);
  Promise.allSettled([loadStatus(), loadCatalog()]);
})();

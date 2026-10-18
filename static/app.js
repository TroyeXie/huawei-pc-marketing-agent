/* The UI renders server-returned facts, checks and traces without inventing progress. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = { result: null, input: null, running: false, examples: [], catalog: null, elapsedTimer: null, toastTimer: null };
  const presets = {
    campus: { product: "matebook14-ultra", audience: "准备购机的大一新生", scene: "开学季：上课记笔记、查资料、做课程汇报，偶尔在宿舍追剧", platform: "小红书", goal: "帮助新生理解适合自己的使用体验，引导收藏选购要点", brief: "围绕 MateBook 14 酷睿 Ultra 写一篇自然、有具体使用场景的小红书图文，避免堆参数。围绕便携、学习效率与日常使用组织内容，给出有出处的配图建议。配置以知识库检索结果为准，不编造优惠价格。" },
    business: { product: "matebook14-ultra", audience: "经常出差、需要移动办公的职场人士", scene: "商务办公：高铁途中处理文档，在客户现场展示方案，整理会议资料", platform: "公众号", goal: "解释真实办公场景中的产品价值，帮助读者判断适用性", brief: "围绕 MateBook 14 酷睿 Ultra 撰写一篇公众号场景文章。以一天的移动办公需求展开，表达克制、结构清楚。将具体功能和知识库中的产品事实对应起来，选择与实际型号匹配的图片，不承诺未经核实的续航或性能。" },
    store: { product: "matebook14-ultra", audience: "有换机计划、正在比较选择的本地顾客", scene: "门店到访：导购通过朋友圈介绍产品，邀请顾客带着使用需求到店体验", platform: "门店朋友圈", goal: "用简短、亲切的内容引导到店体验与咨询", brief: "围绕 MateBook 14 酷睿 Ultra 写一段适合门店导购朋友圈发布的图文，正文尽量控制在 180 字以内。强调可以现场体验的具体功能，不编造门店地址、活动期限、现货情况、赠品或价格；活动信息需要时请标明待门店确认。" }
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
    const names = { live: "实时检索与生成", live_llm: "真实模型生成 · ima 资料", online: "实时检索与生成", llm: "模型生成", ima_live: "ima 实时检索", live_ima: "ima 实时检索", local: "本地资料模式", local_fallback: "本地资料备用模式", offline: "离线资料模式", demo: "演示模式", fallback: "备用模式", hybrid: "混合检索模式", rules: "规则生成模式", rule_based: "规则生成模式", verified: "已核验资料模式", clarification: "等待信息确认", unconfigured: "生成服务待配置" };
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
      const labels = [cached ? "ima 已核验资料缓存" : connected ? "ima 知识库已连接" : "ima 连接未就绪"];
      if (result.llm_configured === false) labels.push("生成模型待配置");
      $("connection-label").textContent = labels.join(" · ");
      $("connection-label").title = textOf(result.message);
      const base = result.knowledge_base;
      const baseName = typeof base === "object" && base ? base.name || base.title : base;
      $("knowledge-label").textContent = cached ? `资料来源：${textOf(baseName)} · 2026-10-18 已核验缓存；新内容由模型现场生成。` : baseName ? `资料来源：${textOf(baseName)}` : result.message || "产品事实与图片来自 ima 知识库";
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
      $("knowledge-label").textContent = "请刷新状态或稍后重试";
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
      if (Array.isArray(catalog.platforms) && catalog.platforms.length) {
        const selection = $("platform").value;
        $("platform").replaceChildren();
        catalog.platforms.forEach((platform) => {
          const name = typeof platform === "string" ? platform : platform.name || platform.label;
          if (name) $("platform").add(new Option(name, name));
        });
        if ([...$("platform").options].some((option) => option.value === selection)) $("platform").value = selection;
      }
      if (Array.isArray(catalog.scenes)) {
        $("scene-options").replaceChildren();
        catalog.scenes.forEach((scene) => {
          const name = typeof scene === "string" ? scene : scene.name || scene.label;
          if (name) $("scene-options").append(new Option(name, name));
        });
      }
    } catch (_) { /* The form remains usable with its explicitly labelled defaults. */ }
  }

  function readInput() {
    const data = {};
    ["product", "audience", "scene", "platform", "goal", "brief"].forEach((key) => { data[key] = $(key).value.trim(); });
    return data;
  }

  function fillInput(input) {
    ["product", "audience", "scene", "platform", "goal", "brief"].forEach((key) => {
      if (input[key] === undefined) return;
      const node = $(key);
      if (node.tagName === "SELECT") {
        let option = [...node.options].find((item) => item.value === input[key] || item.text === input[key]);
        if (!option) {
          option = new Option(textOf(input[key]), textOf(input[key]));
          node.add(option);
        }
        node.value = option.value;
      } else node.value = textOf(input[key]);
    });
  }

  function setRunning(running) {
    state.running = running;
    $("generate-button").disabled = running;
    $("regenerate-button").disabled = running;
    $("generate-button").firstElementChild.textContent = running ? "正在生成与核验" : "生成图文内容";
    document.querySelectorAll(".preset-button").forEach((button) => { button.disabled = running; });
    if (running) {
      $("run-progress").hidden = false;
      $("progress-spinner").className = "progress-spinner";
      $("progress-heading").textContent = "正在处理营销任务";
      $("progress-description").textContent = "检索与核验需要一些时间；完成后会显示本次真实执行记录。";
      $("elapsed").textContent = "0 秒";
      const started = Date.now();
      clearInterval(state.elapsedTimer);
      state.elapsedTimer = setInterval(() => { $("elapsed").textContent = `${Math.floor((Date.now() - started) / 1000)} 秒`; }, 1000);
    } else clearInterval(state.elapsedTimer);
  }

  async function generate(event) {
    event.preventDefault();
    if (state.running) return;
    const input = readInput();
    state.input = { ...input };
    $("notice").hidden = true;
    $("result").hidden = true;
    setRunning(true);
    try {
      const result = await request("/api/generate", { method: "POST", body: JSON.stringify(input) }, 300000);
      $("progress-spinner").className = "progress-spinner finished";
      $("progress-heading").textContent = ["ok", "success"].includes(result.status) ? "本次任务已返回结果" : "本次任务已完成处理";
      $("progress-description").textContent = result.message || "请查看图文内容、资料依据与逐项核验结果。";
      if (Number.isFinite(Number(result.elapsed_ms))) $("elapsed").textContent = `${(Number(result.elapsed_ms) / 1000).toFixed(1)} 秒`;
      if (result.status === "needs_clarification") showNotice("还需要确认几件事", result.message || "请将以下信息补充到任务中，再开始生成。", result.questions);
      else if (result.status === "blocked") showNotice("本次任务未形成可发布成稿", result.message || "请根据核验建议调整任务。", result.questions, true);
      else if (result.status === "error") showNotice("本次任务未成功完成", result.message || "请查看返回记录后重试。", result.questions, true);
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
    if (!selectedImages.length) target.append(element("p", "image-brief", "本次结果未返回可用于成稿的已核验图片。图片检索与处理情况请查看下方资料依据和运行记录。"));
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

  function renderFacts(facts) {
    const target = $("facts-content");
    if (!facts.length) return empty(target, "本次响应没有返回可引用的产品事实。");
    const rows = facts.map((fact, index) => {
      const id = fact.id || `F${index + 1}`;
      const source = element("div");
      source.append(link(fact.source_title || "来源未标注", fact.source_url));
      if (fact.locator) source.append(element("p", "source-secondary", fact.locator));
      return { id: `fact-${id}`, cells: [element("span", "fact-id", id), fact.text || fact.claim || "", source] };
    });
    target.replaceChildren(makeTable(["编号", "采用的产品事实", "来源与定位"], rows));
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
    [["核验项", checks.length], ["通过", tally.pass], ["待确认", tally.warn], ["需处理", tally.fail]].forEach(([label, count]) => {
      const item = element("span");
      item.append(element("strong", "", count), document.createTextNode(label));
      summary.append(item);
    });
    const list = element("div", "check-list");
    checks.forEach((check) => {
      const status = statusInfo(check.status);
      const row = element("div", "check-row");
      const detail = element("div");
      detail.append(element("p", "check-claim", check.claim || check.message || "未提供待核验内容"));
      if (check.evidence) detail.append(element("p", "check-evidence", `依据：${textOf(check.evidence)}`));
      if (check.action) detail.append(element("p", "check-action", `处理建议：${textOf(check.action)}`));
      row.append(element("div", "check-type", checkType(check.type)), element("span", `check-badge ${status.style}`, status.label), detail);
      list.append(row);
    });
    target.replaceChildren(summary, list);
  }

  function renderTrace(trace) {
    $("trace-count").textContent = trace.length ? `${trace.length} 个实际记录` : "";
    const target = $("trace-content");
    if (!trace.length) return empty(target, "本次响应没有附带执行记录。");
    const list = element("ol", "trace-list");
    trace.forEach((item) => {
      const row = element("li");
      const stage = element("span", "", item.step || item.name || "执行步骤");
      if (Number.isFinite(Number(item.ms))) stage.append(element("small", "trace-ms", `${Number(item.ms)} ms`));
      const status = statusInfo(item.status);
      row.append(stage, element("span", `check-badge ${status.style}`, status.label), element("span", "trace-description", item.detail || item.message || ""));
      list.append(row);
    });
    target.replaceChildren(list);
  }

  function formatDate(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return textOf(value);
    return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date);
  }

  function renderResult(result, input = {}) {
    state.result = result;
    state.input = input;
    $("result").hidden = false;
    $("result-heading").textContent = result.status === "blocked" ? "本次内容需要修订" : result.status === "needs_clarification" ? "补充信息后继续任务" : result.status === "error" ? "本次运行未成功完成" : "为这次任务准备的内容";
    $("article-heading").textContent = result.status === "blocked" ? "待修订内容" : "图文成稿";
    const metadata = [result.run_id ? `运行 ${textOf(result.run_id)}` : "", formatDate(result.generated_at), modeLabel(result.mode), result.material_provenance?.note].filter(Boolean);
    $("result-meta").textContent = metadata.join(" · ");
    const banner = $("result-status");
    const fallbackMessages = { blocked: "本次内容未通过核验，请根据逐项核验结果修订后再使用。", needs_clarification: "当前信息不足，请补充任务要求后重新生成。", error: "本次任务未成功完成，请查看实际运行记录。" };
    if (result.message || fallbackMessages[result.status]) {
      banner.textContent = result.message || fallbackMessages[result.status];
      banner.className = `result-status ${["ok", "success"].includes(result.status) ? "success" : ["error", "blocked"].includes(result.status) ? "error" : ""}`;
      banner.hidden = false;
    } else banner.hidden = true;
    const facts = Array.isArray(result.facts) ? result.facts : [];
    const images = Array.isArray(result.images) ? result.images : [];
    const checks = Array.isArray(result.checks) ? result.checks : [];
    $("review-context").textContent = result.run_id ? `已绑定当前运行 ${textOf(result.run_id).slice(0, 12)}：核验时将请求对照该运行的产品事实；实际对照情况会在返回结果中说明。` : "尚未绑定运行记录：可检查营销措辞，产品事实仍需资料确认。";
    renderStrategy(result.strategy || {}, input);
    renderArticle(result.article, images, input);
    renderFacts(facts);
    renderImages(images);
    renderChecks(checks, $("checks-content"));
    renderTrace(Array.isArray(result.trace) ? result.trace : []);
    $("raw-result").textContent = JSON.stringify(result, null, 2);
    $("copy-button").disabled = !result.article || ["blocked", "error", "needs_clarification"].includes(result.status);
    $("copy-button").textContent = result.status === "blocked" ? "成稿未通过核验" : "复制成稿";
    $("export-button").textContent = ["blocked", "error", "needs_clarification"].includes(result.status) ? "导出核验记录 ↓" : "导出 Markdown ↓";
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
      if (!state.examples.length) return empty(target, "目前还没有已保存的实际运行案例。你可以先在工作台生成一份图文，查看完整结果。");
      target.replaceChildren();
      state.examples.forEach((example, index) => {
        const row = element("article", "example-row");
        const body = element("div", "example-body");
        const input = example.input || {};
        body.append(element("h2", "", example.label || example.result?.article?.title || `运行案例 ${index + 1}`));
        body.append(element("p", "example-description", [input.audience, input.scene].filter(Boolean).join(" · ")));
        const details = [input.platform, modeLabel(example.result?.mode), example.result?.generated_at ? formatDate(example.result.generated_at) : "", example.result?.material_provenance?.note].filter(Boolean);
        body.append(element("p", "example-detail", details.join(" / ")));
        const actions = element("div", "example-actions");
        const view = element("button", "secondary-button", "查看实际结果 ↗");
        view.type = "button";
        view.disabled = !example.result;
        view.addEventListener("click", () => {
          if (state.running) return toast("当前任务正在运行，请等待结果后再打开案例。");
          fillInput(input);
          showView("workbench");
          $("notice").hidden = true;
          $("run-progress").hidden = true;
          renderResult(example.result, input);
          requestAnimationFrame(() => $("result").scrollIntoView({ behavior: "smooth", block: "start" }));
        });
        const load = element("button", "text-button", "用这个任务试一遍");
        load.type = "button";
        load.addEventListener("click", () => {
          if (state.running) return toast("当前任务正在运行，请等待结果后再载入新任务。");
          fillInput(input);
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

  function showView(view, updateHash = true) {
    if (!["workbench", "examples", "guide"].includes(view)) return;
    document.querySelectorAll(".view").forEach((node) => { node.hidden = node.id !== `view-${view}`; });
    document.querySelectorAll("[data-view]").forEach((node) => {
      const active = node.dataset.view === view;
      node.classList.toggle("active", active);
      if (active) node.setAttribute("aria-current", "page");
      else node.removeAttribute("aria-current");
    });
    if (updateHash) history.replaceState(null, "", `#${view}`);
    if (view === "examples") loadExamples();
  }

  document.querySelectorAll("[data-view]").forEach((node) => node.addEventListener("click", (event) => { event.preventDefault(); showView(node.dataset.view); window.scrollTo({ top: 0, behavior: "smooth" }); }));
  document.querySelector(".brand").addEventListener("click", (event) => { event.preventDefault(); showView("workbench"); window.scrollTo({ top: 0, behavior: "smooth" }); });
  document.querySelectorAll("[data-preset]").forEach((button) => button.addEventListener("click", () => {
    fillInput(presets[button.dataset.preset]);
    document.querySelectorAll("[data-preset]").forEach((node) => node.classList.toggle("selected", node === button));
    toast("任务已载入，可调整后点击生成。");
  }));
  $("task-form").addEventListener("submit", generate);
  $("regenerate-button").addEventListener("click", () => $("task-form").requestSubmit());
  $("copy-button").addEventListener("click", copyArticle);
  $("export-button").addEventListener("click", exportResult);
  $("refresh-status").addEventListener("click", loadStatus);
  $("review-form").addEventListener("submit", checkCopy);
  window.addEventListener("hashchange", () => showView(location.hash.slice(1), false));
  showView(["examples", "guide"].includes(location.hash.slice(1)) ? location.hash.slice(1) : "workbench", false);
  Promise.allSettled([loadStatus(), loadCatalog()]);
})();

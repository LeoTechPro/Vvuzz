(() => {
  "use strict";

  const body = document.body;
  const base = body.dataset.basePath || "";
  const state = {
    applicantId: "",
    year: 0,
    results: [],
    selected: [],
  };
  const storageKey = "vvuzz-public-v1";

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function formatNumber(value) {
    return new Intl.NumberFormat("ru-RU").format(Number(value || 0));
  }

  function loadLocalState() {
    try {
      const value = JSON.parse(localStorage.getItem(storageKey) || "{}");
      state.selected = Array.isArray(value.selected) ? value.selected.slice(0, 5) : [];
    } catch {
      state.selected = [];
    }
  }

  function saveLocalState() {
    const existing = JSON.parse(localStorage.getItem(storageKey) || "{}");
    localStorage.setItem(storageKey, JSON.stringify({
      ...existing,
      selected: state.selected.slice(0, 5),
    }));
  }

  function saveHistory(response) {
    const existing = JSON.parse(localStorage.getItem(storageKey) || "{}");
    const history = Array.isArray(existing.history) ? existing.history : [];
    history.unshift({
      savedAt: new Date().toISOString(),
      year: response.campaign_year,
      applicantId: state.applicantId,
      positions: response.results.map((item) => ({
        competitionId: item.competition_id,
        position: item.position,
        effective: item.effective_competitors,
      })),
    });
    localStorage.setItem(storageKey, JSON.stringify({
      ...existing,
      selected: state.selected.slice(0, 5),
      history: history.slice(0, 20),
    }));
  }

  function setStatus(message, loading = false) {
    const target = document.querySelector("#lookup-status");
    if (!target) return;
    target.textContent = message;
    target.classList.toggle("is-loading", loading);
  }

  function metric(label, value) {
    const wrapper = el("div");
    wrapper.append(el("dt", "", label), el("dd", "", String(value ?? "—")));
    return wrapper;
  }

  function comparisonButton(item) {
    const button = el("button", "secondary-action");
    const selected = state.selected.some((saved) => saved.competition_id === item.competition_id);
    button.textContent = selected ? "Убрать из сравнения" : "Добавить к сравнению";
    button.type = "button";
    button.addEventListener("click", () => {
      if (selected) {
        state.selected = state.selected.filter((saved) => saved.competition_id !== item.competition_id);
      } else if (state.selected.length < 5) {
        state.selected.push(item);
      } else {
        setStatus("Можно сравнить не более пяти конкурсов.");
        return;
      }
      saveLocalState();
      renderResults({found: true, results: state.results, campaign_year: state.year, warning: "Оценка не гарантирует поступление."});
      updateCompareTray();
    });
    return button;
  }

  function renderCompetition(item) {
    const article = el("article", "competition");
    const header = el("header");
    header.append(el("span", "risk-label", item.risk_label), el("span", "", `Позиция ${item.position} · мест ${item.places ?? "—"}`));
    article.append(header, el("h3", "", item.university), el("p", "", item.program));

    const meta = el("div", "competition-meta");
    [item.quota_type, item.study_form, item.priority ? `приоритет ${item.priority}` : ""].filter(Boolean).forEach((text) => meta.append(el("span", "", text)));
    article.append(meta);

    const metrics = el("dl", "metric-row");
    metrics.append(
      metric("Эффективно выше", item.effective_competitors),
      metric("Согласие здесь", item.consent_here),
      metric("В другом месте", item.consent_elsewhere),
      metric("Без согласия", item.no_consent),
    );
    article.append(metrics);

    const bar = el("progress", "position-bar");
    const denominator = Math.max(Number(item.places || 0), Number(item.effective_competitors || 0), 1);
    bar.max = denominator;
    bar.value = Math.min(denominator, Number(item.effective_competitors || 0));
    bar.textContent = `${item.effective_competitors} из ${denominator}`;
    bar.setAttribute("aria-label", `Мест ${item.places ?? 0}, эффективных конкурентов выше ${item.effective_competitors}`);
    article.append(bar);

    const details = el("details");
    details.append(el("summary", "", "Как рассчитано"));
    details.append(el("p", "", item.formula));
    details.append(el("p", "", `Расчётная позиция: ${item.projected_position}. Процент: ${item.chance_percent ?? "н/д"}%.`));
    article.append(details);

    const actions = el("div", "competition-actions");
    actions.append(comparisonButton(item));
    article.append(actions);
    return article;
  }

  function renderResults(response) {
    const target = document.querySelector("#lookup-results");
    if (!target) return;
    target.replaceChildren();
    if (!response.found) {
      const summary = el("div", "result-summary");
      const copy = el("div");
      copy.append(el("span", "eyebrow", `Кампания ${response.campaign_year}`), el("h2", "", "ID не найден в этом снимке"));
      summary.append(copy, el("p", "", "Проверьте год и написание ID. Мы не показываем, какие другие ID есть в базе."));
      target.append(summary);
      return;
    }
    const summary = el("div", "result-summary");
    const copy = el("div");
    copy.append(el("span", "eyebrow", `Кампания ${response.campaign_year}`), el("h2", "", `${response.results.length} конкурсов`));
    summary.append(copy, el("p", "", response.warning));
    target.append(summary);

    const labels = {
      stable: "Устойчивое положение",
      borderline: "Пограничная ситуация",
      risk: "Высокий риск",
    };
    Object.entries(labels).forEach(([risk, title]) => {
      const items = response.results.filter((item) => item.risk === risk);
      if (!items.length) return;
      const section = el("section", `result-group result-group--${risk}`);
      const heading = el("h2", "", title);
      heading.append(el("span", "", String(items.length)));
      section.append(heading);
      items.forEach((item) => section.append(renderCompetition(item)));
      target.append(section);
    });
  }

  function updateCompareTray() {
    const tray = document.querySelector("#compare-tray");
    if (!tray) return;
    tray.hidden = state.selected.length === 0;
    document.querySelector("#compare-count").textContent = `${state.selected.length} из 5`;
  }

  function renderComparison() {
    const target = document.querySelector("#lookup-results");
    if (!target || !state.selected.length) return;
    target.replaceChildren();
    const panel = el("section", "comparison-panel");
    const heading = el("div", "result-summary");
    const copy = el("div");
    copy.append(el("span", "eyebrow", "Сравнение хранится в этом браузере"), el("h2", "", "Какой конкурс выбрать"));
    heading.append(copy, el("p", "", "Выберите основной вариант. Сценарий показывает запас по текущему снимку и не меняет данные конкурса."));
    panel.append(heading);
    state.selected.forEach((item) => {
      const row = el("article", "comparison-item");
      row.append(el("h3", "", item.university), el("p", "", item.program));
      const margin = Number(item.places || 0) - Number(item.effective_competitors || 0);
      row.append(el("strong", "", `Запас: ${margin >= 0 ? "+" : ""}${margin.toFixed(1)} места`));
      const choose = el("button", "secondary-action", "Если выбрать этот конкурс");
      choose.type = "button";
      choose.addEventListener("click", () => {
        panel.querySelectorAll(".comparison-item").forEach((node) => node.classList.remove("is-choice"));
        row.classList.add("is-choice");
        setStatus(`Основной сценарий: ${item.program}. Проверьте приоритет и официальный срок согласия.`);
      });
      row.append(el("div", "competition-actions")).append(choose);
      panel.append(row);
    });
    const back = el("button", "text-action", "← Вернуться к результатам");
    back.type = "button";
    back.addEventListener("click", () => renderResults({found: true, results: state.results, campaign_year: state.year, warning: "Оценка не гарантирует поступление."}));
    panel.append(back);
    target.append(panel);
  }

  async function submitLookup(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    state.applicantId = String(data.get("applicant_id") || "");
    state.year = Number(data.get("year"));
    setStatus("Проверяем позиции по последнему снимку…", true);
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      const response = await fetch(`${base}/api/public/lookup`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({year: state.year, applicant_id: state.applicantId}),
        cache: "no-store",
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "Не удалось выполнить поиск");
      state.results = result.results;
      renderResults(result);
      if (result.found) saveHistory(result);
      setStatus(result.freshness_at ? `Снимок: ${result.freshness_at}` : "Показан последний доступный снимок.");
      updateCompareTray();
    } catch (error) {
      setStatus(error.message || "Сервис временно недоступен.");
    } finally {
      button.disabled = false;
    }
  }

  async function loadArchive(year) {
    const target = document.querySelector("#archive-overview");
    if (!target) return;
    target.replaceChildren(el("p", "", "Загружаем обзор кампании…"));
    try {
      const response = await fetch(`${base}/api/public/archive/${year}/overview`, {cache: "no-store"});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Архив недоступен");
      target.replaceChildren();
      target.append(el("span", "eyebrow", data.status === "active" ? "Текущий снимок" : "Финальный снимок"), el("h2", "", data.name));
      const metrics = el("div", "archive-metrics");
      [["Конкурсов", data.competition_count], ["Вузов", data.institution_count], ["Участников", data.applicant_count]].forEach(([label, value]) => {
        const item = el("div");
        item.append(el("strong", "", formatNumber(value)), el("span", "", label));
        metrics.append(item);
      });
      target.append(metrics);
      const columns = el("div", "archive-columns");
      [["Вузы", data.institutions], ["Программы", data.programs]].forEach(([title, rows]) => {
        const section = el("section");
        section.append(el("h3", "", title));
        const list = el("ol");
        (rows || []).slice(0, 12).forEach((row) => list.append(el("li", "", `${row.name} — ${formatNumber(row.competitions)}`)));
        section.append(list);
        columns.append(section);
      });
      target.append(columns, el("p", "freshness", `Обновлено: ${data.freshness_at || "дата не указана"}`));
    } catch (error) {
      target.replaceChildren(el("p", "form-error", error.message));
    }
  }

  loadLocalState();
  document.querySelector("#lookup-form")?.addEventListener("submit", submitLookup);
  document.querySelector("#open-compare")?.addEventListener("click", renderComparison);
  document.querySelector("#clear-history")?.addEventListener("click", () => {
    localStorage.removeItem(storageKey);
    state.selected = [];
    updateCompareTray();
    setStatus("Локальная история и сравнение удалены с этого устройства.");
  });
  updateCompareTray();

  const yearButtons = [...document.querySelectorAll(".year-button")];
  yearButtons.forEach((button) => button.addEventListener("click", () => {
    yearButtons.forEach((node) => node.removeAttribute("aria-current"));
    button.setAttribute("aria-current", "true");
    loadArchive(button.dataset.year);
  }));
  if (yearButtons.length) loadArchive(yearButtons[0].dataset.year);
})();

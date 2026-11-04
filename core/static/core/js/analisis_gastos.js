(() => {
  "use strict";

  const source = document.getElementById("spending-analysis-data");
  if (!source) return;
  const data = JSON.parse(source.textContent);
  const isIncome = document.querySelector("[data-analysis-kind]").dataset.analysisKind === "income";
  const noun = isIncome ? "ingreso" : "gasto";
  const plural = isIncome ? "ingresos" : "gastos";
  const months = data.months;
  const currentIndex = months.findIndex(month => month.current);
  if (currentIndex < 0) return;

  const one = selector => document.querySelector(selector);
  const all = selector => [...document.querySelectorAll(selector)];
  const setText = (selector, value) => { one(selector).textContent = value; };
  const money = new Intl.NumberFormat("es-EC", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const percent = new Intl.NumberFormat("es-EC", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const sum = values => values.reduce((total, value) => total + Number(value || 0), 0);
  const palette = ["#159a8c", "#8b68ca", "#f2a43a", "#4b83c5"];
  const monthButtons = all("[data-spending-month]");
  let selected = monthButtons.filter(button => button.getAttribute("aria-pressed") === "true").map(button => Number(button.value));
  let categoryName = "";
  let mode = "cumulative";
  let comparison = "full_month";
  let activePanel = "daily";
  const charts = {};

  const dailyByCategory = (category, index) => category.daily[index];
  const daily = index => {
    if (!categoryName) return data.totalDaily[index];
    return dailyByCategory(data.categories.find(category => category.name === categoryName), index);
  };
  const cutoff = index => index === currentIndex ? data.currentDay
    : comparison === "same_day" ? Math.min(data.currentDay, months[index].days) : months[index].days;
  const total = (values, index) => sum(values.slice(0, cutoff(index)));
  const cumulative = (values, index) => {
    let running = 0;
    return values.map((value, day) => {
      if (day >= cutoff(index)) return null;
      running += Number(value || 0);
      return mode === "cumulative" ? running : Number(value || 0);
    });
  };
  const previous = () => selected.filter(index => index !== currentIndex);
  const average = values => values.length ? sum(values) / values.length : null;

  const chartOptions = (horizontal = false) => ({
    indexAxis: horizontal ? "y" : "x",
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 250 },
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: { display: horizontal },
      tooltip: { callbacks: { label: context => `${context.dataset.label}: ${money.format(context.parsed[horizontal ? "x" : "y"] || 0)}` } },
    },
    scales: {
      x: { grid: { color: "#e8edf1" }, ticks: horizontal ? { callback: value => money.format(value) } : {} },
      y: { beginAtZero: !horizontal, grid: { color: "#e8edf1" }, ticks: horizontal ? { autoSkip: false } : { callback: value => money.format(value) } },
    },
  });

  if (typeof Chart !== "undefined") {
    charts.daily = new Chart(one("#spending-daily-chart"), {
      type: "line", data: { labels: [], datasets: [] },
      options: { ...chartOptions(), plugins: { ...chartOptions().plugins, legend: { display: false } }, scales: { ...chartOptions().scales, x: { title: { display: true, text: "Día del mes" }, grid: { color: "#e8edf1" } } } },
    });
    charts.monthly = new Chart(one("#spending-monthly-chart"), {
      type: "bar", data: { labels: [], datasets: [] }, options: chartOptions(),
    });
    charts.category = new Chart(one("#spending-category-chart"), {
      type: "bar", data: { labels: [], datasets: [] }, options: chartOptions(true),
    });
  } else {
    all(".spending-main-chart .chart-box, .spending-secondary-panel .chart-box").forEach(box => {
      box.textContent = "No se pudo cargar la gráfica. Recarga la página para intentarlo de nuevo.";
    });
  }

  const renderStats = () => {
    const current = total(daily(currentIndex), currentIndex);
    const earlier = previous();
    const mean = average(earlier.map(index => total(daily(index), index)));
    setText("[data-stat-current]", money.format(current));
    setText("[data-stat-current-label]", `${isIncome ? "Ingresado" : "Gastado"} este mes${categoryName ? " · categoría" : ""}`);
    setText("[data-stat-average]", mean === null ? "Sin datos" : money.format(mean));
    setText("[data-stat-average-label]", `Promedio anterior (${earlier.length})`);
    one(".spending-stat:nth-child(2) div > span").textContent = comparison === "same_day" ? "Mismo corte de los meses elegidos" : "Meses anteriores completos";

    const variationCard = one("[data-stat-variation-card]");
    variationCard.classList.remove("is-higher", "is-lower");
    let variation = "Sin datos";
    let detail = "Selecciona meses anteriores";
    if (mean !== null) {
      if (mean === 0) {
        variation = current ? `Nuevo ${noun}` : `Sin ${noun}`;
      } else {
        const change = (current / mean - 1) * 100;
        variation = `${change > 0 ? "+" : ""}${percent.format(change)}%`;
        variationCard.classList.add(change > 0 ? "is-higher" : "is-lower");
      }
      detail = comparison === "same_day" ? `Hasta el día ${data.currentDay}` : "Frente a meses completos";
    }
    setText("[data-stat-variation]", variation);
    setText("[data-stat-variation-detail]", detail);

    const fullCurrentDays = months[currentIndex].days;
    const priorRatios = earlier.map(index => {
      const values = daily(index);
      const atCutoff = sum(values.slice(0, Math.min(data.currentDay, values.length)));
      return atCutoff > 0 ? sum(values) / atCutoff : null;
    }).filter(ratio => ratio !== null);
    const estimated = data.currentDay >= fullCurrentDays ? current
      : current * (average(priorRatios) ?? fullCurrentDays / data.currentDay);
    setText("[data-stat-projection]", money.format(estimated));
    setText("[data-stat-projection-detail]", data.currentDay >= fullCurrentDays ? "Mes cerrado" : priorRatios.length ? "Según meses anteriores" : "Según ritmo diario actual");
  };

  const renderDaily = () => {
    const maxDays = Math.max(...selected.map(index => months[index].days));
    const colors = new Map(selected.map((index, position) => [index, index === currentIndex ? "#159a8c" : palette[(position + 1) % palette.length]]));
    const chart = charts.daily;
    if (chart) {
      chart.data.labels = Array.from({ length: maxDays }, (_, day) => String(day + 1));
      chart.data.datasets = selected.map(index => ({
        label: months[index].label + (index === currentIndex ? " · actual" : ""),
        data: cumulative(daily(index), index),
        borderColor: colors.get(index), backgroundColor: colors.get(index),
        borderWidth: index === currentIndex ? 3 : 2, pointRadius: 2,
        pointHoverRadius: 5, tension: 0.25, spanGaps: false,
      }));
      chart.update();
    }
    setText("[data-daily-description]", `${isIncome ? "Ingreso" : "Gasto"} ${mode === "cumulative" ? "acumulado" : "diario"} de los meses elegidos${categoryName ? ` · ${categoryName}` : ""}.`);
    setText("[data-current-cutoff]", `Actual hasta el día ${data.currentDay}`);
    const legend = one("[data-spending-legend]");
    legend.replaceChildren();
    selected.forEach(index => {
      const label = document.createElement("span");
      const dot = document.createElement("i");
      dot.className = "spending-color-dot";
      dot.style.backgroundColor = colors.get(index);
      label.append(dot, document.createTextNode(months[index].label + (index === currentIndex ? " · actual" : "")));
      legend.append(label);
    });

    setText("[data-comparison-description]", comparison === "same_day" ? `${isIncome ? "Ingresos" : "Gastos"} hasta el día ${data.currentDay} (o último día del mes).` : "Meses anteriores completos; actual hasta hoy.");
    setText("[data-comparison-relative]", "Vs. actual");
    const rows = one("[data-comparison-rows]");
    rows.replaceChildren();
    const current = total(daily(currentIndex), currentIndex);
    selected.forEach(index => {
      const row = document.createElement("tr");
      const label = document.createElement("td");
      const dot = document.createElement("i");
      dot.className = "spending-color-dot";
      dot.style.backgroundColor = colors.get(index);
      label.append(dot, document.createTextNode(months[index].label));
      const amount = document.createElement("td");
      amount.className = "text-end";
      const value = total(daily(index), index);
      amount.textContent = money.format(value);
      const relative = document.createElement("td");
      relative.className = "text-end";
      relative.textContent = index === currentIndex ? "—" : current ? `${percent.format((value / current - 1) * 100)}%` : value ? `Nuevo ${noun}` : "—";
      row.append(label, amount, relative);
      rows.append(row);
    });

    const top = one("[data-top-categories]");
    top.replaceChildren();
    setText("[data-top-description]", `Mes actual hasta el día ${data.currentDay}.`);
    const categoryTotals = data.categories.map(category => ({
      ...category, amount: total(dailyByCategory(category, currentIndex), currentIndex),
    })).filter(category => category.amount > 0 && (!categoryName || category.name === categoryName))
      .sort((a, b) => b.amount - a.amount).slice(0, 5);
    const maximum = categoryTotals[0]?.amount || 1;
    if (!categoryTotals.length) top.textContent = `Sin ${plural} confirmados en este mes.`;
    categoryTotals.forEach(category => {
      const row = document.createElement("div");
      row.className = "spending-category-row";
      const name = document.createElement("span");
      name.textContent = category.name;
      const track = document.createElement("span");
      track.className = "spending-category-track";
      const bar = document.createElement("span");
      bar.style.width = `${category.amount / maximum * 100}%`;
      bar.style.backgroundColor = category.color;
      track.append(bar);
      const amount = document.createElement("strong");
      amount.textContent = money.format(category.amount);
      row.append(name, track, amount);
      top.append(row);
    });
  };

  const renderMonthly = () => {
    const chart = charts.monthly;
    if (!chart) return;
    chart.data.labels = selected.map(index => months[index].label);
    chart.data.datasets = [{
      label: comparison === "same_day" ? `${isIncome ? "Ingreso" : "Gasto"} hasta el día ${data.currentDay}` : `${isIncome ? "Ingreso" : "Gasto"} registrado`,
      data: selected.map(index => total(daily(index), index)),
      backgroundColor: selected.map(index => index === currentIndex ? "#159a8c" : "#8b68ca"),
      borderRadius: 7,
    }];
    chart.update();
    setText("[data-monthly-description]", comparison === "same_day"
      ? `Importes hasta el día ${data.currentDay} (o último día del mes). El mes actual llega hasta hoy.`
      : "Importes completos de los meses anteriores. El mes actual llega hasta hoy.");
  };

  const renderCategory = () => {
    const chart = charts.category;
    if (!chart) return;
    const earlier = previous();
    const categories = data.categories.filter(category => !categoryName || category.name === categoryName)
      .map(category => ({
        name: category.name,
        current: total(dailyByCategory(category, currentIndex), currentIndex),
        prior: average(earlier.map(index => total(dailyByCategory(category, index), index))) || 0,
      })).filter(category => category.current || category.prior)
      .sort((a, b) => Math.max(b.current, b.prior) - Math.max(a.current, a.prior)).slice(0, 12);
    chart.data.labels = categories.map(category => category.name);
    chart.data.datasets = [
      { label: "Mes actual", data: categories.map(category => category.current), backgroundColor: "#159a8c", borderRadius: 5 },
      { label: "Promedio anterior", data: categories.map(category => category.prior), backgroundColor: "#8b68ca", borderRadius: 5 },
    ];
    chart.update();
    setText("[data-category-description]", earlier.length
      ? `Mes actual frente al promedio de ${earlier.length} mes${earlier.length === 1 ? "" : "es"} anterior${earlier.length === 1 ? "" : "es"}${comparison === "same_day" ? `, hasta el día ${data.currentDay}` : `, ${earlier.length === 1 ? "completo" : "completos"}`}.`
      : "Selecciona al menos un mes anterior para comparar categorías.");
  };

  const render = () => {
    setText("[data-months-count]", `Meses a comparar (${selected.length}/4)`);
    setText("[data-months-summary]", selected.map(index => months[index].label).join(", "));
    setText("[data-comparison-help]", comparison === "same_day"
      ? `Solo días 1–${data.currentDay} de cada mes. Los movimientos posteriores aparecen al elegir «Mes completo registrado».`
      : `Meses anteriores completos; el actual llega hasta hoy. ${isIncome ? "Incluye todos los ingresos confirmados." : "Incluye compras a crédito confirmadas."}`);
    renderStats();
    renderDaily();
    renderMonthly();
    renderCategory();
  };

  monthButtons.forEach(button => button.addEventListener("click", () => {
    const wasSelected = button.getAttribute("aria-pressed") === "true";
    const proposed = wasSelected ? selected.filter(index => index !== Number(button.value))
      : [...selected, Number(button.value)].sort((a, b) => a - b);
    if (proposed.length < 1 || proposed.length > 4) {
      one("[data-month-warning]").hidden = false;
      return;
    }
    one("[data-month-warning]").hidden = true;
    button.setAttribute("aria-pressed", String(!wasSelected));
    selected = proposed;
    render();
  }));
  const monthPicker = one("#spending-months");
  document.addEventListener("click", event => {
    if (!monthPicker.contains(event.target)) monthPicker.open = false;
  });
  monthPicker.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      monthPicker.open = false;
      monthPicker.querySelector("summary").focus();
    }
  });
  one("#spending-category").addEventListener("change", event => { categoryName = event.target.value; render(); });
  one("#spending-compare").addEventListener("change", event => { comparison = event.target.value; render(); });
  all("[data-spending-mode]").forEach(button => button.addEventListener("click", () => {
    mode = button.dataset.spendingMode;
    all("[data-spending-mode]").forEach(item => {
      item.classList.toggle("is-active", item === button);
      item.setAttribute("aria-pressed", String(item === button));
    });
    renderDaily();
  }));
  all("[data-spending-tab]").forEach(button => button.addEventListener("click", () => {
    activePanel = button.dataset.spendingTab;
    all("[data-spending-tab]").forEach(item => item.setAttribute("aria-selected", String(item === button)));
    all("[data-spending-panel]").forEach(panel => { panel.hidden = panel.dataset.spendingPanel !== activePanel; });
    requestAnimationFrame(() => charts[activePanel]?.resize());
  }));
  render();
})();

const COLORS = {
  luminosity: "#d99017",
  temperature: "#d84a4a",
  humidity: "#2878b8",
  grid: "#dce5e1",
  text: "#17201d",
  muted: "#66726e",
};

const LABELS = {
  luminosity: "Luminosidade",
  temperature: "Temperatura",
  humidity: "Umidade",
};

const UNITS = {
  luminosity: "%",
  temperature: "C",
  humidity: "%",
};

const state = {
  timer: null,
  data: null,
  loading: false,
};

const elements = {
  canvas: document.querySelector("#sensorChart"),
  status: document.querySelector("#connectionStatus"),
  legend: document.querySelector("#chartLegend"),
  alertList: document.querySelector("#alertList"),
  alertCount: document.querySelector("#alertCount"),
  refreshButton: document.querySelector("#refreshButton"),
  liveToggle: document.querySelector("#liveToggle"),
  intervalInput: document.querySelector("#intervalInput"),
  lastNInput: document.querySelector("#lastNInput"),
  commandMessage: document.querySelector("#commandMessage"),
  latest: {
    luminosity: document.querySelector("#latestLuminosity"),
    temperature: document.querySelector("#latestTemperature"),
    humidity: document.querySelector("#latestHumidity"),
  },
  times: {
    luminosity: document.querySelector("#timeLuminosity"),
    temperature: document.querySelector("#timeTemperature"),
    humidity: document.querySelector("#timeHumidity"),
  },
};

function formatValue(value, unit) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "--";
  }

  const digits = unit === "C" ? 1 : 0;
  return `${Number(value).toFixed(digits)} ${unit}`;
}

function formatTime(value) {
  if (!value) {
    return "--";
  }

  return new Intl.DateTimeFormat("pt-BR", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

function setStatus(status, text) {
  elements.status.classList.remove("online", "offline");
  if (status) {
    elements.status.classList.add(status);
  }
  elements.status.querySelector("span:last-child").textContent = text;
}

function setCanvasSize(canvas) {
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.floor(rect.width * ratio);
  canvas.height = Math.floor(rect.height * ratio);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { ctx, width: rect.width, height: rect.height };
}

function getAllPoints(series) {
  return Object.values(series || {})
    .flat()
    .filter((point) => point.recvTime && point.value !== null)
    .map((point) => ({
      x: new Date(point.recvTime).getTime(),
      y: Number(point.value),
    }));
}

function niceRange(min, max) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) {
    return [0, 100];
  }

  if (min === max) {
    return [Math.max(0, min - 5), max + 5];
  }

  const padding = (max - min) * 0.14;
  return [Math.max(0, min - padding), max + padding];
}

function drawEmpty(ctx, width, height, message) {
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = COLORS.muted;
  ctx.font = "700 15px system-ui";
  ctx.textAlign = "center";
  ctx.fillText(message, width / 2, height / 2);
}

function drawChart(payload) {
  const { ctx, width, height } = setCanvasSize(elements.canvas);
  const points = getAllPoints(payload?.series);

  if (!points.length) {
    drawEmpty(ctx, width, height, "Nenhum dado historico encontrado");
    return;
  }

  const margin = {
    top: 28,
    right: 28,
    bottom: 58,
    left: 54,
  };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const minX = Math.min(...points.map((point) => point.x));
  const maxX = Math.max(...points.map((point) => point.x));
  const [minY, maxY] = niceRange(
    Math.min(...points.map((point) => point.y), 0),
    Math.max(...points.map((point) => point.y), 100),
  );

  const xScale = (value) => {
    if (minX === maxX) {
      return margin.left + plotWidth / 2;
    }
    return margin.left + ((value - minX) / (maxX - minX)) * plotWidth;
  };
  const yScale = (value) =>
    margin.top + plotHeight - ((value - minY) / (maxY - minY)) * plotHeight;

  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, width, height);

  ctx.strokeStyle = COLORS.grid;
  ctx.lineWidth = 1;
  ctx.fillStyle = COLORS.muted;
  ctx.font = "12px system-ui";
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";

  for (let i = 0; i <= 5; i += 1) {
    const value = minY + ((maxY - minY) / 5) * i;
    const y = yScale(value);
    ctx.beginPath();
    ctx.moveTo(margin.left, y);
    ctx.lineTo(width - margin.right, y);
    ctx.stroke();
    ctx.fillText(value.toFixed(0), margin.left - 10, y);
  }

  ctx.strokeStyle = "#aebbb6";
  ctx.beginPath();
  ctx.moveTo(margin.left, margin.top);
  ctx.lineTo(margin.left, height - margin.bottom);
  ctx.lineTo(width - margin.right, height - margin.bottom);
  ctx.stroke();

  drawLimitLine(ctx, yScale(payload.limits.luminosityMax), "Luz 30%", COLORS.luminosity);
  drawLimitLine(ctx, yScale(payload.limits.tempMin), "Temp 12 C", COLORS.temperature);
  drawLimitLine(ctx, yScale(payload.limits.tempMax), "Temp 18 C", COLORS.temperature);
  drawLimitLine(ctx, yScale(payload.limits.humidityMin), "Umid 60%", COLORS.humidity);
  drawLimitLine(ctx, yScale(payload.limits.humidityMax), "Umid 80%", COLORS.humidity);

  for (const [attribute, values] of Object.entries(payload.series)) {
    const cleanValues = values.filter((point) => point.recvTime && point.value !== null);

    if (!cleanValues.length) {
      continue;
    }

    ctx.strokeStyle = COLORS[attribute];
    ctx.fillStyle = COLORS[attribute];
    ctx.lineWidth = 3;
    ctx.beginPath();

    cleanValues.forEach((point, index) => {
      const x = xScale(new Date(point.recvTime).getTime());
      const y = yScale(Number(point.value));

      if (index === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
    });

    ctx.stroke();

    cleanValues.forEach((point) => {
      const x = xScale(new Date(point.recvTime).getTime());
      const y = yScale(Number(point.value));
      ctx.beginPath();
      ctx.arc(x, y, 4, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 1.5;
      ctx.stroke();
    });
  }

  ctx.fillStyle = COLORS.muted;
  ctx.font = "12px system-ui";
  ctx.textAlign = "center";
  ctx.textBaseline = "top";

  for (let i = 0; i <= 4; i += 1) {
    const time = minX + ((maxX - minX) / 4) * i;
    const x = xScale(time);
    ctx.fillText(formatTime(new Date(time).toISOString()), x, height - margin.bottom + 16);
  }
}

function drawLimitLine(ctx, y, label, color) {
  if (!Number.isFinite(y)) {
    return;
  }

  ctx.save();
  ctx.strokeStyle = color;
  ctx.globalAlpha = 0.45;
  ctx.setLineDash([7, 7]);
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(54, y);
  ctx.lineTo(ctx.canvas.width / (window.devicePixelRatio || 1) - 28, y);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.globalAlpha = 1;
  ctx.fillStyle = color;
  ctx.font = "700 11px system-ui";
  ctx.textAlign = "left";
  ctx.fillText(label, 62, Math.max(12, y - 14));
  ctx.restore();
}

function renderLegend() {
  elements.legend.innerHTML = Object.entries(LABELS)
    .map(
      ([attribute, label]) =>
        `<span><i style="background:${COLORS[attribute]}"></i>${label}</span>`,
    )
    .join("");
}

function renderLatest(latest) {
  for (const attribute of Object.keys(LABELS)) {
    const item = latest?.[attribute];
    elements.latest[attribute].textContent = formatValue(item?.value, UNITS[attribute]);
    elements.times[attribute].textContent = formatTime(item?.recvTime);
  }
}

function renderAlerts(alerts) {
  const lastAlerts = [...(alerts || [])].slice(-8).reverse();
  elements.alertCount.textContent = String(alerts?.length || 0);

  if (!lastAlerts.length) {
    elements.alertList.innerHTML =
      '<div class="empty-alert">Ambiente dentro dos limites definidos.</div>';
    return;
  }

  elements.alertList.innerHTML = lastAlerts
    .map(
      (alert) => `
        <article class="alert-item">
          <strong>${alert.alerts}</strong>
          <p>${formatTime(alert.recvTime)}</p>
          <p>Luz: ${formatValue(alert.luminosity, "%")} · Temp: ${formatValue(
            alert.temperature,
            "C",
          )} · Umid: ${formatValue(alert.humidity, "%")}</p>
        </article>
      `,
    )
    .join("");
}

async function loadData() {
  if (state.loading) {
    return;
  }

  state.loading = true;
  setStatus("", "Atualizando");

  try {
    const lastN = elements.lastNInput.value || 50;
    const response = await fetch(`/api/history?lastN=${encodeURIComponent(lastN)}`);
    const payload = await response.json();

    if (!response.ok) {
      throw new Error(payload.detail || payload.error || "Falha ao carregar dados");
    }

    state.data = payload;
    renderLatest(payload.latest);
    renderAlerts(payload.alerts);
    drawChart(payload);
    if (payload.remoteControl?.error) {
      elements.commandMessage.textContent = `Controle remoto: ${payload.remoteControl.error}`;
    }
    setStatus("online", "Online");
  } catch (error) {
    setStatus("offline", "Offline");
    elements.alertList.innerHTML = `<div class="empty-alert">${error.message}</div>`;
    drawEmpty(
      elements.canvas.getContext("2d"),
      elements.canvas.clientWidth,
      elements.canvas.clientHeight,
      "Erro ao buscar dados",
    );
  } finally {
    state.loading = false;
  }
}

function scheduleLiveUpdates() {
  window.clearInterval(state.timer);

  if (!elements.liveToggle.checked) {
    return;
  }

  state.timer = window.setInterval(loadData, Number(elements.intervalInput.value));
}

async function sendCommand(command, value = "") {
  elements.commandMessage.textContent = "Enviando comando...";

  try {
    const response = await fetch("/api/command", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ command, value }),
    });
    const payload = await response.json();

    if (!response.ok) {
      throw new Error(payload.detail || payload.error || "Falha ao enviar comando");
    }

    elements.commandMessage.textContent = `Comando ${command} enviado.`;
  } catch (error) {
    elements.commandMessage.textContent = error.message;
  }
}

renderLegend();
loadData();
scheduleLiveUpdates();

elements.refreshButton.addEventListener("click", loadData);
elements.liveToggle.addEventListener("change", scheduleLiveUpdates);
elements.intervalInput.addEventListener("change", scheduleLiveUpdates);
elements.lastNInput.addEventListener("change", loadData);
window.addEventListener("resize", () => {
  if (state.data) {
    drawChart(state.data);
  }
});

document.querySelectorAll("[data-command]").forEach((button) => {
  button.addEventListener("click", () => sendCommand(button.dataset.command, button.dataset.value || ""));
});

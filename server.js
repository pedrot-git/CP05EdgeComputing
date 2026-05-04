const http = require("http");
const fs = require("fs");
const path = require("path");

const PORT = Number(process.env.PORT || 3000);

const CONFIG = {
  sthUrl: process.env.STH_URL || "http://34.95.131.219:8666",
  orionUrl: process.env.ORION_URL || "http://34.95.131.219:1026",
  service: process.env.FIWARE_SERVICE || "smart",
  servicePath: process.env.FIWARE_SERVICE_PATH || "/",
  entityType: process.env.ENTITY_TYPE || "WineCellar",
  entityId: process.env.ENTITY_ID || "urn:ngsi-ld:WineCellar:001",
};

const ATTRIBUTES = {
  luminosity: "Luminosidade (%)",
  temperature: "Temperatura (C)",
  humidity: "Umidade (%)",
};

const LIMITS = {
  tempMin: 12,
  tempMax: 18,
  humidityMin: 60,
  humidityMax: 80,
  luminosityMax: 30,
};

const MIME_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
};

function sendJson(res, status, payload) {
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
  });
  res.end(JSON.stringify(payload));
}

function sendStatic(req, res) {
  const pathname = new URL(req.url, `http://${req.headers.host}`).pathname;
  const safePath = pathname === "/" ? "/index.html" : pathname;
  const filePath = path.join(__dirname, "public", path.normalize(safePath));

  if (!filePath.startsWith(path.join(__dirname, "public"))) {
    res.writeHead(403);
    res.end("Forbidden");
    return;
  }

  fs.readFile(filePath, (error, content) => {
    if (error) {
      res.writeHead(404);
      res.end("Not found");
      return;
    }

    const ext = path.extname(filePath);
    res.writeHead(200, {
      "Content-Type": MIME_TYPES[ext] || "application/octet-stream",
    });
    res.end(content);
  });
}

function parseNumeric(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

async function fetchAttribute(attribute, lastN) {
  const url = new URL(
    `/STH/v1/contextEntities/type/${CONFIG.entityType}/id/${CONFIG.entityId}/attributes/${attribute}`,
    CONFIG.sthUrl,
  );
  url.searchParams.set("lastN", String(lastN));

  const response = await fetch(url, {
    headers: {
      "fiware-service": CONFIG.service,
      "fiware-servicepath": CONFIG.servicePath,
    },
    signal: AbortSignal.timeout(15000),
  });

  const text = await response.text();

  if (!response.ok) {
    throw new Error(`STH retornou HTTP ${response.status}: ${text.slice(0, 240)}`);
  }

  const data = JSON.parse(text);
  const values =
    data.contextResponses?.[0]?.contextElement?.attributes?.[0]?.values || [];

  return values
    .map((item) => ({
      recvTime: item.recvTime,
      value: parseNumeric(item.attrValue),
    }))
    .filter((item) => item.recvTime && item.value !== null)
    .sort((a, b) => new Date(a.recvTime) - new Date(b.recvTime));
}

function mergeSeries(series) {
  const rowsByTime = new Map();

  for (const [attribute, values] of Object.entries(series)) {
    for (const item of values) {
      const time = item.recvTime;
      const row = rowsByTime.get(time) || { recvTime: time };
      row[attribute] = item.value;
      rowsByTime.set(time, row);
    }
  }

  return [...rowsByTime.values()].sort(
    (a, b) => new Date(a.recvTime) - new Date(b.recvTime),
  );
}

function getLatest(series) {
  return Object.fromEntries(
    Object.entries(series).map(([attribute, values]) => {
      const latest = values.at(-1);
      return [
        attribute,
        {
          label: ATTRIBUTES[attribute],
          value: latest?.value ?? null,
          recvTime: latest?.recvTime ?? null,
        },
      ];
    }),
  );
}

function evaluateAlerts(rows) {
  return rows
    .map((row) => {
      const problems = [];

      if (
        row.temperature !== undefined &&
        !(LIMITS.tempMin <= row.temperature && row.temperature <= LIMITS.tempMax)
      ) {
        problems.push("temperatura fora do limite");
      }

      if (
        row.humidity !== undefined &&
        !(LIMITS.humidityMin <= row.humidity && row.humidity <= LIMITS.humidityMax)
      ) {
        problems.push("umidade fora do limite");
      }

      if (
        row.luminosity !== undefined &&
        row.luminosity > LIMITS.luminosityMax
      ) {
        problems.push("luminosidade alta");
      }

      return problems.length
        ? {
            recvTime: row.recvTime,
            luminosity: row.luminosity ?? null,
            temperature: row.temperature ?? null,
            humidity: row.humidity ?? null,
            alerts: problems.join(", "),
          }
        : null;
    })
    .filter(Boolean);
}

async function handleHistory(req, res) {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const lastN = Math.min(Math.max(Number(url.searchParams.get("lastN")) || 50, 1), 1000);

  try {
    const entries = await Promise.all(
      Object.keys(ATTRIBUTES).map(async (attribute) => [
        attribute,
        await fetchAttribute(attribute, lastN),
      ]),
    );
    const series = Object.fromEntries(entries);
    const rows = mergeSeries(series);

    sendJson(res, 200, {
      labels: ATTRIBUTES,
      limits: LIMITS,
      series,
      rows,
      latest: getLatest(series),
      alerts: evaluateAlerts(rows),
      fetchedAt: new Date().toISOString(),
    });
  } catch (error) {
    sendJson(res, 502, {
      error: "Nao foi possivel buscar o historico no STH-Comet.",
      detail: error.message,
    });
  }
}

async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) {
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
}

async function handleCommand(req, res) {
  const validCommands = new Set(["alert_on", "alert_off", "silence_buzzer"]);

  try {
    const body = await readBody(req);
    const command = body.command;

    if (!validCommands.has(command)) {
      sendJson(res, 400, {
        error: "Comando invalido.",
        validCommands: [...validCommands],
      });
      return;
    }

    const response = await fetch(
      `${CONFIG.orionUrl}/v2/entities/${CONFIG.entityId}/attrs`,
      {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "fiware-service": CONFIG.service,
          "fiware-servicepath": CONFIG.servicePath,
        },
        body: JSON.stringify({
          [command]: {
            type: "command",
            value: "",
          },
        }),
        signal: AbortSignal.timeout(15000),
      },
    );

    const text = await response.text();

    if (!response.ok) {
      sendJson(res, response.status, {
        error: `Orion retornou HTTP ${response.status}.`,
        detail: text.slice(0, 500),
      });
      return;
    }

    sendJson(res, 200, {
      ok: true,
      command,
    });
  } catch (error) {
    sendJson(res, 500, {
      error: "Nao foi possivel enviar o comando.",
      detail: error.message,
    });
  }
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);

  if (req.method === "GET" && url.pathname === "/api/history") {
    handleHistory(req, res);
    return;
  }

  if (req.method === "POST" && url.pathname === "/api/command") {
    handleCommand(req, res);
    return;
  }

  if (req.method === "GET") {
    sendStatic(req, res);
    return;
  }

  sendJson(res, 405, { error: "Metodo nao permitido." });
});

server.listen(PORT, () => {
  console.log(`Dashboard rodando em http://localhost:${PORT}`);
});

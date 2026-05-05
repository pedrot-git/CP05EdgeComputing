from datetime import datetime
import html
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from flask import Flask, jsonify, request, send_from_directory


VM_IP = os.environ.get("VM_IP", "34.39.150.225")
STH_URL = os.environ.get("STH_URL", f"http://{VM_IP}:8666").rstrip("/")
ORION_URL = os.environ.get("ORION_URL", f"http://{VM_IP}:1026").rstrip("/")
FIWARE_SERVICE = os.environ.get("FIWARE_SERVICE", "smart")
FIWARE_SERVICE_PATH = os.environ.get("FIWARE_SERVICE_PATH", "/")
ENTITY_TYPE = os.environ.get("ENTITY_TYPE", "WineCellar")
ENTITY_ID = os.environ.get("ENTITY_ID", "urn:ngsi-ld:WineCellar:001")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "5000"))
AUTO_REMOTE_CONTROL = os.environ.get("AUTO_REMOTE_CONTROL", "1") == "1"

app = Flask(__name__, static_folder="public", static_url_path="/public")

TEMP_MIN = 12.0
TEMP_MAX = 18.0
UMIDADE_MIN = 60.0
UMIDADE_MAX = 80.0
LUMINOSIDADE_MAX = 30.0

ATRIBUTOS = {
    "luminosity": "Luminosidade",
    "temperature": "Temperatura",
    "humidity": "Umidade",
}

UNIDADES = {
    "luminosity": "%",
    "temperature": "C",
    "humidity": "%",
}

CORES = {
    "luminosity": "#d99017",
    "temperature": "#d84a4a",
    "humidity": "#2878b8",
}

ULTIMO_COMANDO_REMOTO = None


def numero(valor):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def parse_tempo(valor):
    if not valor:
        return None

    texto = str(valor).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(texto)
    except ValueError:
        return None


def formatar_tempo(valor):
    dt = parse_tempo(valor)
    if not dt:
        return "--"
    return dt.strftime("%d/%m %H:%M:%S")


def formatar_valor(valor, unidade):
    if valor is None:
        return "--"
    casas = 1 if unidade == "C" else 0
    return f"{valor:.{casas}f} {unidade}"


def cabecalhos_fiware(extra=None):
    headers = {
        "fiware-service": FIWARE_SERVICE,
        "fiware-servicepath": FIWARE_SERVICE_PATH,
    }
    if extra:
        headers.update(extra)
    return headers


def obter_historico_atributo(atributo, last_n=50):
    entity_type = quote(ENTITY_TYPE, safe="")
    entity_id = quote(ENTITY_ID, safe=":")
    attr = quote(atributo, safe="")
    url = (
        f"{STH_URL}/STH/v1/contextEntities/type/{entity_type}"
        f"/id/{entity_id}/attributes/{attr}?lastN={last_n}"
    )

    request = Request(url, headers=cabecalhos_fiware())

    with urlopen(request, timeout=15) as response:
        data = json.loads(response.read().decode("utf-8"))

    atributos = (
        data.get("contextResponses", [{}])[0]
        .get("contextElement", {})
        .get("attributes", [])
    )

    if not atributos:
        return []

    registros = []
    for item in atributos[0].get("values", []):
        valor = numero(item.get("attrValue"))
        recv_time = item.get("recvTime")
        if valor is not None and recv_time:
            registros.append({"recvTime": recv_time, "value": valor})

    return sorted(registros, key=lambda item: parse_tempo(item["recvTime"]) or datetime.min)


def obter_historicos(last_n=50):
    return {
        atributo: obter_historico_atributo(atributo, last_n)
        for atributo in ATRIBUTOS
    }


def juntar_historicos(historicos):
    linhas_por_tempo = {}

    for atributo, registros in historicos.items():
        for item in registros:
            linha = linhas_por_tempo.setdefault(item["recvTime"], {"recvTime": item["recvTime"]})
            linha[atributo] = item["value"]

    return sorted(
        linhas_por_tempo.values(),
        key=lambda linha: parse_tempo(linha["recvTime"]) or datetime.min,
    )


def obter_ultimas_leituras(historicos):
    ultimas = {}

    for atributo, registros in historicos.items():
        ultima = registros[-1] if registros else None
        ultimas[atributo] = {
            "valor": ultima["value"] if ultima else None,
            "recvTime": ultima["recvTime"] if ultima else None,
        }

    return ultimas


def avaliar_alertas(linhas):
    alertas = []

    for linha in linhas:
        problemas = []
        temperatura = linha.get("temperature")
        umidade = linha.get("humidity")
        luminosidade = linha.get("luminosity")

        if temperatura is not None and not (TEMP_MIN <= temperatura <= TEMP_MAX):
            problemas.append("temperatura fora do limite")

        if umidade is not None and not (UMIDADE_MIN <= umidade <= UMIDADE_MAX):
            problemas.append("umidade fora do limite")

        if luminosidade is not None and luminosidade > LUMINOSIDADE_MAX:
            problemas.append("luminosidade alta")

        if problemas:
            alertas.append(
                {
                    "recvTime": linha.get("recvTime"),
                    "luminosity": luminosidade,
                    "temperature": temperatura,
                    "humidity": umidade,
                    "alertas": ", ".join(problemas),
                }
            )

    return alertas


def enviar_comando_remoto(comando, valor=""):
    comandos_validos = {"buzzer"}
    if comando not in comandos_validos:
        raise ValueError("Comando invalido. Use buzzer com temperature, humidity, luminosity ou none.")

    url = f"{ORION_URL}/v2/entities/{quote(ENTITY_ID, safe=':')}/attrs"
    payload = json.dumps({comando: {"type": "command", "value": valor}}).encode("utf-8")
    request = Request(
        url,
        data=payload,
        method="PATCH",
        headers=cabecalhos_fiware({"Content-Type": "application/json"}),
    )

    try:
        with urlopen(request, timeout=15) as response:
            return response.status
    except HTTPError as exc:
        detalhe = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Orion retornou HTTP {exc.code}: {detalhe}") from exc


def escala(valor, minimo, maximo, inicio, fim):
    if maximo == minimo:
        return (inicio + fim) / 2
    return inicio + ((valor - minimo) / (maximo - minimo)) * (fim - inicio)


def gerar_grafico_svg(historicos):
    pontos = []
    for registros in historicos.values():
        for item in registros:
            dt = parse_tempo(item["recvTime"])
            if dt:
                pontos.append((dt.timestamp(), item["value"]))

    if not pontos:
        return '<div class="empty-chart">Nenhum dado historico encontrado no STH-Comet.</div>'

    largura = 1100
    altura = 450
    margem_esq = 66
    margem_dir = 28
    margem_top = 28
    margem_base = 64
    graf_largura = largura - margem_esq - margem_dir
    graf_altura = altura - margem_top - margem_base

    min_x = min(ponto[0] for ponto in pontos)
    max_x = max(ponto[0] for ponto in pontos)
    min_y = min(0, *(ponto[1] for ponto in pontos))
    max_y = max(100, *(ponto[1] for ponto in pontos))
    padding = max((max_y - min_y) * 0.12, 5)
    min_y = max(0, min_y - padding)
    max_y = max_y + padding

    def x(valor):
        return escala(valor, min_x, max_x, margem_esq, margem_esq + graf_largura)

    def y(valor):
        return escala(valor, min_y, max_y, margem_top + graf_altura, margem_top)

    partes = [
        f'<svg viewBox="0 0 {largura} {altura}" role="img" aria-label="Grafico dos sensores">',
        '<rect width="1100" height="450" fill="#ffffff"/>',
    ]

    for indice in range(6):
        valor = min_y + ((max_y - min_y) / 5) * indice
        y_pos = y(valor)
        partes.append(
            f'<line x1="{margem_esq}" y1="{y_pos:.2f}" x2="{largura - margem_dir}" '
            'y2="{:.2f}" stroke="#dce5e1" stroke-width="1"/>'.format(y_pos)
        )
        partes.append(
            f'<text x="{margem_esq - 12}" y="{y_pos + 4:.2f}" text-anchor="end" '
            f'fill="#66726e" font-size="12">{valor:.0f}</text>'
        )

    partes.append(
        f'<path d="M {margem_esq} {margem_top} V {altura - margem_base} '
        f'H {largura - margem_dir}" fill="none" stroke="#aebbb6" stroke-width="1.3"/>'
    )

    limites = [
        (LUMINOSIDADE_MAX, "Luz 30%", CORES["luminosity"]),
        (TEMP_MIN, "Temp 12 C", CORES["temperature"]),
        (TEMP_MAX, "Temp 18 C", CORES["temperature"]),
        (UMIDADE_MIN, "Umid 60%", CORES["humidity"]),
        (UMIDADE_MAX, "Umid 80%", CORES["humidity"]),
    ]

    for valor, rotulo, cor in limites:
        y_pos = y(valor)
        partes.append(
            f'<line x1="{margem_esq}" y1="{y_pos:.2f}" x2="{largura - margem_dir}" '
            f'y2="{y_pos:.2f}" stroke="{cor}" stroke-width="1.4" '
            'stroke-dasharray="7 7" opacity="0.45"/>'
        )
        partes.append(
            f'<text x="{margem_esq + 8}" y="{max(14, y_pos - 8):.2f}" '
            f'fill="{cor}" font-size="12" font-weight="700">{html.escape(rotulo)}</text>'
        )

    for atributo, registros in historicos.items():
        coordenadas = []
        for item in registros:
            dt = parse_tempo(item["recvTime"])
            if dt:
                coordenadas.append((x(dt.timestamp()), y(item["value"])))

        if not coordenadas:
            continue

        pontos_linha = " ".join(f"{px:.2f},{py:.2f}" for px, py in coordenadas)
        cor = CORES[atributo]
        partes.append(
            f'<polyline points="{pontos_linha}" fill="none" stroke="{cor}" '
            'stroke-width="3.2" stroke-linejoin="round" stroke-linecap="round"/>'
        )
        for px, py in coordenadas:
            partes.append(
                f'<circle cx="{px:.2f}" cy="{py:.2f}" r="4.2" fill="{cor}" '
                'stroke="#ffffff" stroke-width="1.6"/>'
            )

    for indice in range(5):
        tempo = min_x + ((max_x - min_x) / 4) * indice
        x_pos = x(tempo)
        rotulo = datetime.fromtimestamp(tempo).strftime("%d/%m %H:%M")
        partes.append(
            f'<text x="{x_pos:.2f}" y="{altura - 30}" text-anchor="middle" '
            f'fill="#66726e" font-size="12">{html.escape(rotulo)}</text>'
        )

    partes.append("</svg>")
    return "".join(partes)


def html_cartao(classe, letra, titulo, item, unidade):
    return f"""
    <article class="sensor-card {classe}">
      <div class="sensor-icon">{letra}</div>
      <div>
        <p>{html.escape(titulo)}</p>
        <strong>{html.escape(formatar_valor(item["valor"], unidade))}</strong>
        <small>{html.escape(formatar_tempo(item["recvTime"]))}</small>
      </div>
    </article>
    """


def renderizar_pagina(last_n, intervalo, auto, mensagem=""):
    erro = ""
    historicos = {atributo: [] for atributo in ATRIBUTOS}

    try:
        historicos = obter_historicos(last_n)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        erro = f"Nao foi possivel buscar dados do STH-Comet: {exc}"

    linhas = juntar_historicos(historicos)
    ultimas = obter_ultimas_leituras(historicos)
    alertas = avaliar_alertas(linhas)
    grafico = gerar_grafico_svg(historicos)
    status_classe = "online" if not erro else "offline"
    status_texto = "Online" if not erro else "Offline"
    query = urlencode({"last_n": last_n, "intervalo": intervalo, "auto": "1" if auto else "0"})
    meta_refresh = (
        f'<meta http-equiv="refresh" content="{intervalo}; url=/?{query}" />'
        if auto
        else ""
    )

    cards = "\n".join(
        [
            html_cartao("sensor-light", "L", "Luminosidade", ultimas["luminosity"], "%"),
            html_cartao("sensor-temp", "T", "Temperatura", ultimas["temperature"], "C"),
            html_cartao("sensor-humidity", "U", "Umidade", ultimas["humidity"], "%"),
        ]
    )

    alerta_html = renderizar_alertas(alertas)
    mensagem_html = f'<p class="message">{html.escape(mensagem)}</p>' if mensagem else ""
    erro_html = f'<div class="error">{html.escape(erro)}</div>' if erro else ""
    checked = "checked" if auto else ""

    return f"""<!doctype html>
<html lang="pt-BR">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    {meta_refresh}
    <title>Dashboard ESP32 Vinheria</title>
    <style>{CSS}</style>
  </head>
  <body>
    <main class="shell">
      <section class="topbar">
        <div>
          <p class="eyebrow">Aplicacao Python + FIWARE STH-Comet</p>
          <h1>Dashboard ESP32 da vinheria</h1>
        </div>
        <div class="status-pill {status_classe}">
          <span class="dot"></span>
          <span>{status_texto}</span>
        </div>
      </section>

      {erro_html}
      {mensagem_html}

      <section class="sensor-grid">
        {cards}
      </section>

      <section class="workspace">
        <section class="chart-panel">
          <div class="panel-head">
            <div>
              <p class="eyebrow">Historico</p>
              <h2>Luminosidade, temperatura e umidade</h2>
            </div>
            <div class="legend">
              <span><i style="background:{CORES["luminosity"]}"></i>Luminosidade</span>
              <span><i style="background:{CORES["temperature"]}"></i>Temperatura</span>
              <span><i style="background:{CORES["humidity"]}"></i>Umidade</span>
            </div>
          </div>
          <div class="chart-wrap">{grafico}</div>
        </section>

        <aside class="side-panel">
          <section class="controls">
            <div class="panel-head compact">
              <div>
                <p class="eyebrow">Ao vivo</p>
                <h2>Atualizacao</h2>
              </div>
              <a class="icon-button" href="/?{query}" title="Atualizar agora">↻</a>
            </div>

            <form method="get" action="/">
              <label>
                <span>Ultimos registros</span>
                <input name="last_n" type="number" min="1" max="1000" value="{last_n}" />
              </label>

              <label>
                <span>Intervalo em segundos</span>
                <select name="intervalo">
                  {option(intervalo, 5)}
                  {option(intervalo, 10)}
                  {option(intervalo, 30)}
                  {option(intervalo, 60)}
                </select>
              </label>

              <label class="toggle">
                <input name="auto" value="1" type="checkbox" {checked} />
                <span>Atualizar automaticamente</span>
              </label>

              <button class="primary-button" type="submit">Aplicar</button>
            </form>

            <form class="command-row" method="post" action="/command">
              <input type="hidden" name="last_n" value="{last_n}" />
              <input type="hidden" name="intervalo" value="{intervalo}" />
              <input type="hidden" name="auto" value="{"1" if auto else "0"}" />
              <button name="command" value="temperature" type="submit">Temp</button>
              <button name="command" value="humidity" type="submit">Umid</button>
              <button name="command" value="luminosity" type="submit">Luz</button>
              <button name="command" value="none" type="submit">Silenciar</button>
            </form>
          </section>

          <section class="alerts">
            <div class="panel-head compact">
              <div>
                <p class="eyebrow">Triggers</p>
                <h2>Alertas</h2>
              </div>
              <strong>{len(alertas)}</strong>
            </div>
            <div class="alert-list">{alerta_html}</div>
          </section>
        </aside>
      </section>
    </main>
  </body>
</html>"""


def option(atual, valor):
    selected = "selected" if atual == valor else ""
    return f'<option value="{valor}" {selected}>{valor} s</option>'


def renderizar_alertas(alertas):
    if not alertas:
        return '<div class="empty-alert">Ambiente dentro dos limites definidos.</div>'

    itens = []
    for alerta in reversed(alertas[-8:]):
        itens.append(
            f"""
            <article class="alert-item">
              <strong>{html.escape(alerta["alertas"])}</strong>
              <p>{html.escape(formatar_tempo(alerta["recvTime"]))}</p>
              <p>
                Luz: {html.escape(formatar_valor(alerta["luminosity"], "%"))} ·
                Temp: {html.escape(formatar_valor(alerta["temperature"], "C"))} ·
                Umid: {html.escape(formatar_valor(alerta["humidity"], "%"))}
              </p>
            </article>
            """
        )
    return "".join(itens)


def montar_payload_dashboard(last_n):
    global ULTIMO_COMANDO_REMOTO

    historicos = obter_historicos(last_n)
    linhas = juntar_historicos(historicos)
    ultimas = obter_ultimas_leituras(historicos)
    alertas = avaliar_alertas(linhas)
    alerta_atual = avaliar_alerta_atual(ultimas)
    controle_remoto = sincronizar_alerta_remoto(alerta_atual)

    return {
        "series": historicos,
        "latest": {
            atributo: {
                "value": item["valor"],
                "recvTime": item["recvTime"],
            }
            for atributo, item in ultimas.items()
        },
        "alerts": [
            {
                "recvTime": alerta["recvTime"],
                "luminosity": alerta["luminosity"],
                "temperature": alerta["temperature"],
                "humidity": alerta["humidity"],
                "alerts": alerta["alertas"],
            }
            for alerta in alertas
        ],
        "currentAlert": alerta_atual,
        "remoteControl": controle_remoto,
        "limits": {
            "luminosityMax": LUMINOSIDADE_MAX,
            "tempMin": TEMP_MIN,
            "tempMax": TEMP_MAX,
            "humidityMin": UMIDADE_MIN,
            "humidityMax": UMIDADE_MAX,
        },
    }


def avaliar_alerta_atual(ultimas):
    temperatura = ultimas["temperature"]["valor"]
    umidade = ultimas["humidity"]["valor"]
    luminosidade = ultimas["luminosity"]["valor"]
    problemas = []

    if temperatura is not None and not (TEMP_MIN <= temperatura <= TEMP_MAX):
        problemas.append("temperatura fora do limite")
        trigger = "temperature"
        buzzer_pattern = "long"
    elif umidade is not None and not (UMIDADE_MIN <= umidade <= UMIDADE_MAX):
        problemas.append("umidade fora do limite")
        trigger = "humidity"
        buzzer_pattern = "double"
    elif luminosidade is not None and luminosidade > LUMINOSIDADE_MAX:
        problemas.append("luminosidade alta")
        trigger = "luminosity"
        buzzer_pattern = "triple"
    else:
        trigger = "none"
        buzzer_pattern = "none"

    return {
        "active": trigger != "none",
        "trigger": trigger,
        "buzzerPattern": buzzer_pattern,
        "reason": ", ".join(problemas) if problemas else "parametros dentro dos limites",
    }


def sincronizar_alerta_remoto(alerta_atual):
    global ULTIMO_COMANDO_REMOTO

    alvo = alerta_atual["trigger"]
    status = {
        "enabled": AUTO_REMOTE_CONTROL,
        "target": alvo,
        "sent": False,
        "status": None,
        "error": None,
    }

    if not AUTO_REMOTE_CONTROL:
        return status

    try:
        if alvo == "none":
            http_status = enviar_comando_remoto("buzzer", "none")
        else:
            http_status = enviar_comando_remoto("buzzer", alvo)

        reenviado = alvo == ULTIMO_COMANDO_REMOTO
        ULTIMO_COMANDO_REMOTO = alvo
        status.update({"sent": True, "status": http_status, "resent": reenviado})
    except Exception as exc:
        status["error"] = str(exc)

    return status


def normalizar_parametros(query):
    def obter(nome, padrao):
        valor = query.get(nome, padrao)
        if isinstance(valor, list):
            return valor[0] if valor else padrao
        return valor

    try:
        last_n = int(obter("last_n", "50") or 50)
    except ValueError:
        last_n = 50

    try:
        intervalo = int(obter("intervalo", "5") or 5)
    except ValueError:
        intervalo = 5

    auto = obter("auto", "0") == "1"
    return min(max(last_n, 1), 1000), min(max(intervalo, 2), 3600), auto


@app.get("/")
def dashboard():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/app.js")
def frontend_js():
    return send_from_directory(app.static_folder, "app.js")


@app.get("/styles.css")
def frontend_css():
    return send_from_directory(app.static_folder, "styles.css")


@app.get("/api/history")
def api_history():
    last_n, _, _ = normalizar_parametros(
        {"last_n": request.args.get("lastN", request.args.get("last_n", "50"))}
    )

    try:
        return jsonify(montar_payload_dashboard(last_n))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return (
            jsonify(
                {
                    "error": "Nao foi possivel buscar dados do STH-Comet",
                    "detail": str(exc),
                }
            ),
            502,
        )


@app.post("/command")
def command():
    last_n, intervalo, auto = normalizar_parametros(request.form)
    valor = request.form.get("command", "")

    try:
        status = enviar_comando_remoto("buzzer", valor)
        mensagem = f"Comando buzzer|{valor} enviado com sucesso. HTTP {status}."
    except Exception as exc:
        mensagem = f"Erro ao enviar comando buzzer|{valor}: {exc}"

    return renderizar_pagina(last_n, intervalo, auto, mensagem=mensagem)


@app.post("/api/command")
def api_command():
    data = request.get_json(silent=True) or {}
    comando = data.get("command", "")
    valor = data.get("value", "")

    try:
        status = enviar_comando_remoto(comando, valor)
        return jsonify({"ok": True, "status": status})
    except Exception as exc:
        return jsonify({"error": "Erro ao enviar comando", "detail": str(exc)}), 400


CSS = """
:root {
  color-scheme: light;
  --bg: #f5f7f6;
  --surface: #ffffff;
  --text: #17201d;
  --muted: #66726e;
  --line: #dce5e1;
  --wine: #8f2747;
  --gold: #d99017;
  --red: #d84a4a;
  --blue: #2878b8;
  --green: #167760;
  --shadow: 0 16px 40px rgba(26, 38, 34, 0.08);
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background: linear-gradient(180deg, rgba(143, 39, 71, 0.08), transparent 360px), var(--bg);
  color: var(--text);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

button, input, select { font: inherit; }

.shell {
  width: min(1440px, calc(100% - 32px));
  margin: 0 auto;
  padding: 28px 0 34px;
}

.topbar, .sensor-card, .controls, .alerts, .chart-panel {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.92);
  box-shadow: var(--shadow);
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  min-height: 112px;
  padding: 24px;
}

.eyebrow {
  margin: 0 0 6px;
  color: var(--wine);
  font-size: 0.72rem;
  font-weight: 800;
  letter-spacing: 0;
  text-transform: uppercase;
}

h1, h2, p { margin-top: 0; }

h1 {
  max-width: 760px;
  margin-bottom: 0;
  font-size: clamp(2rem, 4vw, 4.25rem);
  line-height: 0.98;
  letter-spacing: 0;
}

h2 {
  margin-bottom: 0;
  font-size: 1rem;
  line-height: 1.2;
  letter-spacing: 0;
}

.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 132px;
  justify-content: center;
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 9px 13px;
  color: var(--muted);
  background: var(--surface);
  font-weight: 700;
  white-space: nowrap;
}

.dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--red);
}

.status-pill.online .dot { background: var(--green); }

.error, .message {
  margin: 16px 0 0;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 12px 14px;
  background: #fff;
  color: var(--muted);
}

.sensor-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  margin: 16px 0;
}

.sensor-card {
  display: grid;
  grid-template-columns: 54px minmax(0, 1fr);
  gap: 14px;
  align-items: center;
  min-height: 128px;
  padding: 20px;
}

.sensor-icon {
  display: grid;
  width: 54px;
  height: 54px;
  place-items: center;
  border-radius: 8px;
  color: #fff;
  font-weight: 900;
}

.sensor-light .sensor-icon { background: var(--gold); }
.sensor-temp .sensor-icon { background: var(--red); }
.sensor-humidity .sensor-icon { background: var(--blue); }

.sensor-card p {
  margin-bottom: 6px;
  color: var(--muted);
  font-size: 0.92rem;
}

.sensor-card strong {
  display: block;
  min-height: 42px;
  font-size: 2.1rem;
  line-height: 1;
}

.sensor-card small {
  display: block;
  min-height: 17px;
  margin-top: 8px;
  color: var(--muted);
}

.workspace {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 16px;
}

.chart-panel, .controls, .alerts { padding: 18px; }

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.legend {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.legend span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 30px;
  padding: 6px 9px;
  border: 1px solid var(--line);
  border-radius: 999px;
  color: var(--muted);
  background: var(--surface);
  font-size: 0.82rem;
  font-weight: 700;
}

.legend i {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}

.chart-wrap {
  min-height: 520px;
  margin-top: 16px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fff;
  overflow: hidden;
}

.chart-wrap svg {
  display: block;
  width: 100%;
  height: auto;
}

.empty-chart, .empty-alert {
  min-height: 180px;
  display: grid;
  place-items: center;
  color: var(--muted);
  text-align: center;
}

.side-panel {
  display: grid;
  align-content: start;
  gap: 16px;
}

.controls label {
  display: grid;
  gap: 7px;
  margin-top: 16px;
  color: var(--muted);
  font-size: 0.86rem;
  font-weight: 700;
}

input, select {
  width: 100%;
  height: 42px;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 0 12px;
  color: var(--text);
  background: var(--surface);
  outline: none;
}

.toggle {
  grid-template-columns: auto 1fr;
  align-items: center;
}

.toggle input {
  width: 18px;
  height: 18px;
}

.primary-button, .icon-button, .command-row button {
  border: 1px solid var(--line);
  border-radius: 8px;
  color: var(--text);
  background: var(--surface);
  cursor: pointer;
  text-decoration: none;
}

.primary-button {
  width: 100%;
  min-height: 42px;
  margin-top: 16px;
  font-weight: 800;
}

.icon-button {
  display: grid;
  width: 42px;
  height: 42px;
  place-items: center;
  font-size: 1.25rem;
  line-height: 1;
}

.command-row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin-top: 16px;
}

.command-row input { display: none; }

.command-row button {
  min-height: 40px;
  padding: 8px;
  font-size: 0.78rem;
  font-weight: 800;
}

.primary-button:hover, .command-row button:hover, .icon-button:hover {
  border-color: rgba(143, 39, 71, 0.35);
  background: #fff7fa;
}

.alerts strong {
  display: grid;
  min-width: 36px;
  height: 36px;
  place-items: center;
  border-radius: 8px;
  color: #fff;
  background: var(--wine);
}

.alert-list {
  display: grid;
  gap: 10px;
  max-height: 392px;
  margin-top: 14px;
  overflow: auto;
  padding-right: 4px;
}

.alert-item {
  border: 1px solid var(--line);
  border-left: 5px solid var(--wine);
  border-radius: 8px;
  padding: 12px;
  background: #fff;
}

.alert-item strong {
  display: block;
  min-width: 0;
  height: auto;
  margin-bottom: 6px;
  color: var(--text);
  background: transparent;
  font-size: 0.92rem;
}

.alert-item p {
  margin: 0;
  color: var(--muted);
  font-size: 0.82rem;
  line-height: 1.45;
}

@media (max-width: 980px) {
  .workspace, .sensor-grid { grid-template-columns: 1fr; }
}

@media (max-width: 640px) {
  .shell {
    width: min(100% - 20px, 1440px);
    padding-top: 10px;
  }

  .topbar, .panel-head {
    align-items: flex-start;
    flex-direction: column;
  }

  h1 { font-size: 2.3rem; }

  .chart-wrap { min-height: 300px; }
}
"""


def main():
    print(f"Dashboard Flask rodando localmente em http://localhost:{PORT}")
    print(f"Se estiver rodando na VM, acesse http://{VM_IP}:{PORT}")
    print(f"STH-Comet: {STH_URL}")
    print(f"Orion: {ORION_URL}")
    app.run(host=HOST, port=PORT)


if __name__ == "__main__":
    main()

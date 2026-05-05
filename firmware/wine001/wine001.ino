#include <WiFi.h>
#include <PubSubClient.h>
#include <DHTesp.h>

/*
==========================================================
CP-05 - Edge Computing / IoT Full + Dashboard
Projeto: Monitoramento ambiental de vinheria

Hardware:
- ESP32
- LDR no GPIO 34
- DHT22 no GPIO 15
- LED azul externo no GPIO 19
- Buzzer no GPIO 18

Decisao de arquitetura:
- O ESP32 publica apenas as leituras e obedece comandos.
- O dashboard Python calcula os triggers usando os dados historicos
  do STH-Comet e aciona remotamente LED/buzzer via FIWARE.

Comandos aceitos pelo ESP32:
- wine001@buzzer|temperature
- wine001@buzzer|humidity
- wine001@buzzer|luminosity
- wine001@buzzer|none
==========================================================
*/

const int PINO_DHT = 15;
const int PINO_LDR = 34;
const int PINO_LED_AZUL = 19;
const int PINO_BUZZER = 18;

const unsigned long INTERVALO_LEITURA_LDR_MS = 1000;
const unsigned long INTERVALO_LEITURA_DHT_MS = 2000;
const unsigned long INTERVALO_PUBLICACAO_MS = 2000;
const unsigned long INTERVALO_LOG_MS = 2000;
const unsigned long INTERVALO_PISCA_LED_MS = 350;
const unsigned long INTERVALO_PADRAO_BUZZER_MS = 2500;

const char* WIFI_SSID = "Wokwi-GUEST";
const char* WIFI_PASSWORD = "";

const char* MQTT_BROKER = "34.39.150.225";
const int MQTT_PORT = 1883;
const char* MQTT_CLIENT_ID = "wine001_client";

const char* DEVICE_PREFIX = "wine001";
const char* TOPICO_SUBSCRIBE = "/TEF/wine001/cmd";
const char* TOPICO_PUBLISH_ATTRS = "/TEF/wine001/attrs";

const char* TOPICO_PUBLISH_LUMINOSIDADE = "/TEF/wine001/attrs/l";
const char* TOPICO_PUBLISH_TEMPERATURA = "/TEF/wine001/attrs/t";
const char* TOPICO_PUBLISH_UMIDADE = "/TEF/wine001/attrs/h";
const char* TOPICO_PUBLISH_ENVIRONMENT = "/TEF/wine001/attrs/e";
const char* TOPICO_PUBLISH_ALERT_STATUS = "/TEF/wine001/attrs/as";
const char* TOPICO_PUBLISH_ALERT_TYPE = "/TEF/wine001/attrs/at";
const char* TOPICO_PUBLISH_TRIGGER = "/TEF/wine001/attrs/tv";
const char* TOPICO_PUBLISH_BLUE_LED = "/TEF/wine001/attrs/bl";
const char* TOPICO_PUBLISH_BUZZER = "/TEF/wine001/attrs/bs";
const char* TOPICO_PUBLISH_REASON = "/TEF/wine001/attrs/r";

WiFiClient espClient;
PubSubClient MQTT(espClient);
DHTesp dhtSensor;

float temperaturaAtual = NAN;
float umidadeAtual = NAN;
int luminosidadeAtual = 0;

bool alertaRemotoAtivo = false;
bool estadoLedAzul = false;
bool buzzerLigado = false;

String environmentStatus = "normal";
String alertStatus = "normal";
String alertType = "none";
String triggerViolated = "none";
String decisionReason = "aguardando comando do dashboard";
String buzzerPattern = "none";

unsigned long ultimoLdrMs = 0;
unsigned long ultimoDhtMs = 0;
unsigned long ultimaPublicacaoMs = 0;
unsigned long ultimoLogMs = 0;
unsigned long ultimoPiscaLedMs = 0;
unsigned long ultimoPadraoBuzzerMs = 0;

void iniciarSerial();
void iniciarSaidas();
void iniciarSensores();
void conectarWiFi();
void conectarMQTT();
void verificarConexoes();
void lerLuminosidade();
void lerDHT();
void aplicarEstadoRemoto();
void atualizarLedAzul();
void atualizarBuzzer();
void tocarBip(int duracaoMs);
void publicarDadosFiware();
void imprimirResumoSerial();
void callbackMQTT(char* topic, byte* payload, unsigned int length);
void processarComando(String mensagem);
void ativarAlertaRemoto(String tipo);
void desativarAlertaRemoto();

void setup() {
  iniciarSaidas();
  iniciarSerial();
  iniciarSensores();

  conectarWiFi();
  MQTT.setServer(MQTT_BROKER, MQTT_PORT);
  MQTT.setCallback(callbackMQTT);
  conectarMQTT();

  delay(1000);

  lerLuminosidade();
  ultimoDhtMs = millis() - INTERVALO_LEITURA_DHT_MS;
  lerDHT();
  aplicarEstadoRemoto();
  publicarDadosFiware();
  imprimirResumoSerial();
}

void loop() {
  verificarConexoes();
  MQTT.loop();

  unsigned long agora = millis();

  if (agora - ultimoLdrMs >= INTERVALO_LEITURA_LDR_MS) {
    ultimoLdrMs = agora;
    lerLuminosidade();
  }

  if (agora - ultimoDhtMs >= INTERVALO_LEITURA_DHT_MS) {
    lerDHT();
  }

  aplicarEstadoRemoto();
  atualizarLedAzul();
  atualizarBuzzer();

  if (agora - ultimaPublicacaoMs >= INTERVALO_PUBLICACAO_MS) {
    ultimaPublicacaoMs = agora;
    publicarDadosFiware();
  }

  if (agora - ultimoLogMs >= INTERVALO_LOG_MS) {
    ultimoLogMs = agora;
    imprimirResumoSerial();
  }
}

void iniciarSerial() {
  Serial.begin(115200);
  Serial.println();
  Serial.println("===== INICIANDO WINE001 - MONITORAMENTO DE VINHERIA =====");
}

void iniciarSaidas() {
  pinMode(PINO_LED_AZUL, OUTPUT);
  pinMode(PINO_BUZZER, OUTPUT);
  digitalWrite(PINO_LED_AZUL, LOW);
  noTone(PINO_BUZZER);
}

void iniciarSensores() {
  dhtSensor.setup(PINO_DHT, DHTesp::DHT22);
}

void conectarWiFi() {
  if (WiFi.status() == WL_CONNECTED) {
    return;
  }

  Serial.println("------ CONEXAO WI-FI ------");
  Serial.print("Conectando-se na rede: ");
  Serial.println(WIFI_SSID);

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  delay(100);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  unsigned long inicio = millis();
  const unsigned long TIMEOUT_WIFI_MS = 20000;

  while (WiFi.status() != WL_CONNECTED && millis() - inicio < TIMEOUT_WIFI_MS) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("Wi-Fi conectado com sucesso.");
    Serial.print("IP obtido: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("Falha ao conectar no Wi-Fi do Wokwi.");
    Serial.print("Status Wi-Fi: ");
    Serial.println(WiFi.status());
  }
}

void conectarMQTT() {
  while (!MQTT.connected()) {
    Serial.print("* Tentando conexao com broker MQTT: ");
    Serial.println(MQTT_BROKER);

    if (MQTT.connect(MQTT_CLIENT_ID)) {
      Serial.println("Conectado ao broker MQTT com sucesso.");
      MQTT.subscribe(TOPICO_SUBSCRIBE);
      Serial.print("Inscrito em: ");
      Serial.println(TOPICO_SUBSCRIBE);
    } else {
      Serial.println("Falha na conexao MQTT. Nova tentativa em 2s.");
      delay(2000);
    }
  }
}

void verificarConexoes() {
  conectarWiFi();

  if (!MQTT.connected()) {
    conectarMQTT();
  }
}

void lerLuminosidade() {
  int valorBrutoLdr = analogRead(PINO_LDR);
  luminosidadeAtual = map(valorBrutoLdr, 0, 4095, 0, 100);
  luminosidadeAtual = constrain(luminosidadeAtual, 0, 100);
}

void lerDHT() {
  ultimoDhtMs = millis();

  TempAndHumidity dados = dhtSensor.getTempAndHumidity();

  if (isnan(dados.temperature) || isnan(dados.humidity)) {
    Serial.println("Falha ao ler o DHT22.");
    return;
  }

  temperaturaAtual = dados.temperature;
  umidadeAtual = dados.humidity;
}

void aplicarEstadoRemoto() {
  if (!alertaRemotoAtivo) {
    environmentStatus = "normal";
    alertStatus = "normal";
    alertType = "none";
    triggerViolated = "none";
    decisionReason = "parametros normalizados pelo dashboard";
    buzzerPattern = "none";
    return;
  }

  environmentStatus = "alerta";
  alertStatus = "active";
  triggerViolated = alertType;

  if (alertType == "temperature") {
    decisionReason = "dashboard detectou temperatura fora do limite";
    buzzerPattern = "long";
  } else if (alertType == "humidity") {
    decisionReason = "dashboard detectou umidade fora do limite";
    buzzerPattern = "double";
  } else if (alertType == "luminosity") {
    decisionReason = "dashboard detectou luminosidade acima do limite";
    buzzerPattern = "triple";
  } else {
    decisionReason = "alerta ativado remotamente pelo dashboard";
    buzzerPattern = "triple";
  }
}

void atualizarLedAzul() {
  if (alertStatus != "active") {
    estadoLedAzul = false;
    digitalWrite(PINO_LED_AZUL, LOW);
    return;
  }

  unsigned long agora = millis();

  if (agora - ultimoPiscaLedMs >= INTERVALO_PISCA_LED_MS) {
    ultimoPiscaLedMs = agora;
    estadoLedAzul = !estadoLedAzul;
    digitalWrite(PINO_LED_AZUL, estadoLedAzul ? HIGH : LOW);
  }
}

void atualizarBuzzer() {
  if (alertStatus != "active" || buzzerPattern == "none") {
    buzzerLigado = false;
    noTone(PINO_BUZZER);
    return;
  }

  unsigned long agora = millis();

  if (agora - ultimoPadraoBuzzerMs < INTERVALO_PADRAO_BUZZER_MS) {
    return;
  }

  ultimoPadraoBuzzerMs = agora;

  if (buzzerPattern == "long") {
    tocarBip(900);
  } else if (buzzerPattern == "double") {
    tocarBip(150);
    delay(120);
    tocarBip(150);
  } else if (buzzerPattern == "triple") {
    tocarBip(120);
    delay(100);
    tocarBip(120);
    delay(100);
    tocarBip(120);
  }
}

void tocarBip(int duracaoMs) {
  buzzerLigado = true;
  tone(PINO_BUZZER, 2000);
  delay(duracaoMs);
  noTone(PINO_BUZZER);
  buzzerLigado = false;
}

void ativarAlertaRemoto(String tipo) {
  alertaRemotoAtivo = true;
  alertType = tipo;
  aplicarEstadoRemoto();
}

void desativarAlertaRemoto() {
  alertaRemotoAtivo = false;
  alertType = "none";
  aplicarEstadoRemoto();
}

void publicarDadosFiware() {
  String payload = "l|" + String(luminosidadeAtual);

  if (!isnan(temperaturaAtual)) {
    payload += "|t|" + String(temperaturaAtual, 1);
  }

  if (!isnan(umidadeAtual)) {
    payload += "|h|" + String(umidadeAtual, 1);
  }

  payload += "|e|" + environmentStatus;
  payload += "|as|" + alertStatus;
  payload += "|at|" + alertType;
  payload += "|tv|" + triggerViolated;
  payload += "|bl|" + String(estadoLedAzul ? "on" : "off");
  payload += "|bs|" + String(buzzerLigado ? "on" : "off");
  payload += "|r|" + decisionReason;

  MQTT.publish(TOPICO_PUBLISH_ATTRS, payload.c_str());
  MQTT.publish(TOPICO_PUBLISH_LUMINOSIDADE, String(luminosidadeAtual).c_str());

  if (!isnan(temperaturaAtual)) {
    MQTT.publish(TOPICO_PUBLISH_TEMPERATURA, String(temperaturaAtual, 1).c_str());
  }

  if (!isnan(umidadeAtual)) {
    MQTT.publish(TOPICO_PUBLISH_UMIDADE, String(umidadeAtual, 1).c_str());
  }

  MQTT.publish(TOPICO_PUBLISH_ENVIRONMENT, environmentStatus.c_str());
  MQTT.publish(TOPICO_PUBLISH_ALERT_STATUS, alertStatus.c_str());
  MQTT.publish(TOPICO_PUBLISH_ALERT_TYPE, alertType.c_str());
  MQTT.publish(TOPICO_PUBLISH_TRIGGER, triggerViolated.c_str());
  MQTT.publish(TOPICO_PUBLISH_BLUE_LED, estadoLedAzul ? "on" : "off");
  MQTT.publish(TOPICO_PUBLISH_BUZZER, buzzerLigado ? "on" : "off");
  MQTT.publish(TOPICO_PUBLISH_REASON, decisionReason.c_str());
}

void imprimirResumoSerial() {
  Serial.println("----------- RESUMO VINHERIA -----------");
  Serial.print("Luminosidade: ");
  Serial.print(luminosidadeAtual);
  Serial.println("%");

  Serial.print("Temperatura: ");
  if (isnan(temperaturaAtual)) {
    Serial.println("sem leitura valida");
  } else {
    Serial.print(temperaturaAtual, 1);
    Serial.println(" C");
  }

  Serial.print("Umidade: ");
  if (isnan(umidadeAtual)) {
    Serial.println("sem leitura valida");
  } else {
    Serial.print(umidadeAtual, 1);
    Serial.println("%");
  }

  Serial.print("Status ambiental: ");
  Serial.println(environmentStatus);
  Serial.print("Alerta: ");
  Serial.println(alertStatus);
  Serial.print("Tipo: ");
  Serial.println(alertType);
  Serial.print("Trigger violado: ");
  Serial.println(triggerViolated);
  Serial.print("LED azul: ");
  Serial.println(estadoLedAzul ? "on" : "off");
  Serial.print("Buzzer: ");
  Serial.println(buzzerLigado ? "on" : "off");
  Serial.print("Motivo: ");
  Serial.println(decisionReason);
  Serial.println("---------------------------------------");
}

void callbackMQTT(char* topic, byte* payload, unsigned int length) {
  String mensagem;

  for (unsigned int i = 0; i < length; i++) {
    mensagem += (char)payload[i];
  }

  Serial.print("Mensagem recebida no topico ");
  Serial.print(topic);
  Serial.print(": ");
  Serial.println(mensagem);

  processarComando(mensagem);
}

void processarComando(String mensagem) {
  String prefixoBuzzer = String(DEVICE_PREFIX) + "@buzzer|";

  if (mensagem.startsWith(prefixoBuzzer)) {
    String tipo = mensagem.substring(prefixoBuzzer.length());

    if (tipo == "none") {
      desativarAlertaRemoto();
    } else {
      ativarAlertaRemoto(tipo);
    }

    Serial.print("Comando remoto BUZZER executado: ");
    Serial.println(tipo);
    return;
  }

  Serial.println("Comando nao reconhecido.");
}
